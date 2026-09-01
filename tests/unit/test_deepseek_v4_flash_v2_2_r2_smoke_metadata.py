from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "experiments" / "llm_v0_2_deepseek_v4_flash_structured_selection_v2_2" / "code"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


R = load("sdb_deepseek_r2_smoke_runner_test", CODE / "run_deepseek_v4_flash_v2_2.py")


TASK_TARGET = {
    "single_service_discovery": "service", "single_api_recommendation": "api",
    "multi_service_discovery": "service", "multi_api_recommendation": "api",
    "composable_service_discovery": "service", "composable_api_recommendation": "api",
}


def smoke_items(tie: bool = False):
    result = []
    for task, target in TASK_TARGET.items():
        contract = R.CONTRACTS.contract_for("native", task)
        for index in range(10):
            request_id = f"{task}-{index:02d}"
            size = 100 if tie else 100 + index
            result.append(R.RequestItem(request_id, "smoke", task, target, ["a", "b", "c", "d", "e"], contract, {"max_tokens": 10}, "a" * 64, size, size - 10, 50, size + 20))
    return result


def run_smoke(tmp_path: Path, fail_ids: set[str] | None = None, tie: bool = False):
    fail_ids = fail_ids or set()
    runner = R.DeepSeekRunner(base_url="https://invalid.example", key="unused", output_dir=tmp_path / "deepseek-smoke", concurrency=2, provenance={})
    items = smoke_items(tie)
    def fake(item, worker):
        failed = item.request_id in fail_ids
        return {
            "provider": "deepseek", "experiment_revision": R.REVISION,
            "implementation_revision": R.IMPLEMENTATION_REVISION, "request_id": item.request_id,
            "task_type": item.task_type, "output_contract": item.contract,
            "candidate_count": len(item.candidate_ids), "worker_index": worker,
            "status": "parse_failure" if failed else "succeeded",
            "parse_status": "invalid" if failed else "valid", "finish_reason": "stop",
            "response_model": R.MODEL, "response_id": item.request_id,
            "response_created": 100, "system_fingerprint": "fp",
        }
    runner.run_one = fake
    return runner.run(items, "smoke")


def test_short_same_candidate_count_cannot_substitute_for_longest_request(tmp_path: Path):
    summary = run_smoke(tmp_path, {"single_service_discovery-09"})
    assert summary["status"] == "BLOCKED_DEEPSEEK_DEV_SMOKE"
    record = summary["smoke_gate_details"]["per_contract_max_request"][R.CONTRACTS.TOP5_RANKING_V1]
    assert record["request_id"] == "single_service_discovery-09" and record["passed"] is False


def test_exact_max_serialized_request_success_passes_subgate(tmp_path: Path):
    summary = run_smoke(tmp_path)
    assert summary["gate_passed"] is True
    assert all(row["passed"] for row in summary["smoke_gate_details"]["per_contract_max_request"].values())


def test_exact_max_legal_answer_failure_blocks_smoke(tmp_path: Path):
    summary = run_smoke(tmp_path, {"single_api_recommendation-09"})
    record = summary["smoke_gate_details"]["per_contract_max_legal_answer"][R.CONTRACTS.RANKING_AND_SELECTED_SET_V1_10]
    assert record["request_id"] == "single_api_recommendation-09"
    assert summary["gate_passed"] is False


def test_size_tie_is_broken_by_request_id(tmp_path: Path):
    summary = run_smoke(tmp_path, tie=True)
    record = summary["smoke_gate_details"]["per_contract_max_request"][R.CONTRACTS.TOP5_RANKING_V1]
    assert record["request_id"] == "single_service_discovery-00"


def diagnostic_summary(tmp_path: Path, fingerprints):
    items = [R.RequestItem(f"r-{index}", "machine", "single_service_discovery", "service", ["a"], R.CONTRACTS.TOP5_RANKING_V1, {"max_tokens": 1}, "a" * 64) for index in range(len(fingerprints))]
    runner = R.DeepSeekRunner(base_url="https://invalid.example", key="unused", output_dir=tmp_path / "deepseek-diagnostic", concurrency=1, provenance={})
    def fake(item, worker):
        index = int(item.request_id.split("-")[1])
        return {"provider": "deepseek", "experiment_revision": R.REVISION, "request_id": item.request_id, "task_type": item.task_type, "output_contract": item.contract, "candidate_count": 1, "status": "succeeded", "parse_status": "valid", "finish_reason": "stop", "response_model": R.MODEL, "response_id": item.request_id, "response_created": 10 + index, "system_fingerprint": fingerprints[index]}
    runner.run_one = fake
    return runner.run(items, "diagnostic")


def test_summary_records_single_fingerprint(tmp_path: Path):
    summary = diagnostic_summary(tmp_path, ["fp", "fp"])
    assert summary["backend_fingerprint_finding"] == "SINGLE_FINGERPRINT"
    assert summary["observed_system_fingerprints"] == {"fp": 2}


def test_summary_records_multiple_fingerprints(tmp_path: Path):
    summary = diagnostic_summary(tmp_path, ["fp-a", "fp-b"])
    assert summary["backend_fingerprint_finding"] == "MULTIPLE_FINGERPRINTS"


def test_summary_records_all_missing_fingerprint(tmp_path: Path):
    summary = diagnostic_summary(tmp_path, [None, None])
    assert summary["backend_fingerprint_finding"] == "ALL_MISSING"
    assert summary["missing_system_fingerprint_count"] == 2


def test_summary_records_mixed_present_and_missing_fingerprint(tmp_path: Path):
    summary = diagnostic_summary(tmp_path, ["fp", None])
    assert summary["backend_fingerprint_finding"] == "MIXED_PRESENT_AND_MISSING"


def test_summary_records_created_range_and_unique_ids(tmp_path: Path):
    summary = diagnostic_summary(tmp_path, ["fp", "fp", "fp"])
    assert summary["response_created_min"] == 10 and summary["response_created_max"] == 12
    assert summary["unique_response_id_count"] == 3
