from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


B = load("sdb_deepseek_r2_bundle_test", ROOT / "scripts/release/build_deepseek_v4_flash_v2_2_bundle.py")


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    return path


def make_bundle_inputs(tmp_path: Path, provider: str = "deepseek"):
    q0 = write_json(tmp_path / "q0" / "Q0_REPORT.json", {
        "status": "PASS", "provider": "deepseek", "experiment_revision": B.REVISION,
        "implementation_revision": B.IMPLEMENTATION_REVISION,
        "runtime_freeze_sha256": "1" * 64, "budget_freeze_sha256": "2" * 64,
    })
    q0_hash = B.sha256_file(q0)
    smoke_root, machine_root, native_root = (tmp_path / "smoke", tmp_path / "machine", tmp_path / "native")
    common = {"provider": "deepseek", "experiment_revision": B.REVISION, "implementation_revision": B.IMPLEMENTATION_REVISION, "status": "COMPLETE_ALL_PARSED", "runtime_freeze_sha256": "1" * 64, "budget_freeze_sha256": "2" * 64}
    smoke_summary = write_json(smoke_root / "RUN_SUMMARY.json", {**common, "mode": "smoke", "track": "smoke", "requested_rows": 60, "terminal_rows": 60, "gate_passed": True, "status_counts": {"succeeded": 60}, "prerequisite_q0_report_sha256": q0_hash})
    smoke_hash = B.sha256_file(smoke_summary)
    machine_summary = write_json(machine_root / "RUN_SUMMARY.json", {**common, "mode": "formal", "track": "machine", "requested_rows": 197, "terminal_rows": 197, "status_counts": {"succeeded": 197}, "prerequisite_q0_report_sha256": q0_hash, "prerequisite_smoke_summary_sha256": smoke_hash})
    machine_hash = B.sha256_file(machine_summary)
    write_json(native_root / "RUN_SUMMARY.json", {**common, "mode": "formal", "track": "native", "requested_rows": 4798, "terminal_rows": 4798, "status_counts": {"succeeded": 4798}, "prerequisite_q0_report_sha256": q0_hash, "prerequisite_smoke_summary_sha256": smoke_hash, "prerequisite_machine_summary_sha256": machine_hash})
    def status_rows(track: str, count: int):
        return [{"request_id": f"{track}-{index}", "provider": provider, "experiment_revision": B.REVISION, "implementation_revision": B.IMPLEMENTATION_REVISION, "status": "succeeded", "task_type": "single_service_discovery", "output_contract": "TOP5_RANKING_V1", "finish_reason": "stop", "response_model": "deepseek-v4-flash", "system_fingerprint": "fp", "latency_seconds": 1.0, "attempts": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}} for index in range(count)]
    write_jsonl(smoke_root / "REQUEST_STATUS.jsonl", status_rows("smoke", 60))
    write_jsonl(machine_root / "REQUEST_STATUS.jsonl", status_rows("machine", 197))
    write_jsonl(native_root / "REQUEST_STATUS.jsonl", status_rows("native", 4798))
    for root in (smoke_root, machine_root, native_root):
        write_jsonl(root / "ATTEMPT_LEDGER.jsonl", [])
    machine_score, native_score = tmp_path / "machine-score", tmp_path / "native-score"
    score_summary = {"provider": "deepseek", "experiment_revision": B.REVISION, "implementation_revision": B.IMPLEMENTATION_REVISION, "old_qwen_rows_reused": 0}
    write_json(machine_score / "PER_REQUEST_SCORES.json", [{"request_id": f"machine-{index}"} for index in range(197)])
    write_json(machine_score / "SCORE_SUMMARY.json", score_summary)
    write_json(native_score / "PER_REQUEST_SCORES.json", [{"request_id": f"native-{index}"} for index in range(4798)])
    write_json(native_score / "SCORE_SUMMARY.json", score_summary)
    return q0, smoke_root, machine_root, native_root, machine_score, native_score


def test_bundle_blocks_machine_not_197(tmp_path: Path):
    inputs = make_bundle_inputs(tmp_path)
    summary_path = inputs[2] / "RUN_SUMMARY.json"
    value = json.loads(summary_path.read_text(encoding="utf-8")); value["terminal_rows"] = 196; write_json(summary_path, value)
    with pytest.raises(ValueError, match="machine"):
        B.validate_inputs(*inputs)


def test_bundle_blocks_native_not_4798(tmp_path: Path):
    inputs = make_bundle_inputs(tmp_path)
    summary_path = inputs[3] / "RUN_SUMMARY.json"
    value = json.loads(summary_path.read_text(encoding="utf-8")); value["requested_rows"] = 4797; write_json(summary_path, value)
    with pytest.raises(ValueError, match="native"):
        B.validate_inputs(*inputs)


def test_bundle_blocks_prerequisite_hash_chain_error(tmp_path: Path):
    inputs = make_bundle_inputs(tmp_path)
    summary_path = inputs[3] / "RUN_SUMMARY.json"
    value = json.loads(summary_path.read_text(encoding="utf-8")); value["prerequisite_machine_summary_sha256"] = "0" * 64; write_json(summary_path, value)
    with pytest.raises(ValueError, match="HASH_CHAIN"):
        B.validate_inputs(*inputs)


def test_bundle_blocks_qwen_status_rows(tmp_path: Path):
    inputs = make_bundle_inputs(tmp_path, provider="qwen")
    with pytest.raises(ValueError, match="FOREIGN"):
        B.validate_inputs(*inputs)


def test_synthetic_bundle_has_crc_sidecar_and_internal_checksums(tmp_path: Path):
    inputs = make_bundle_inputs(tmp_path)
    output = tmp_path / "result"
    zip_path = B.build_bundle(*inputs, output)
    sidecar = output / f"{B.ZIP_NAME}.sha256"
    assert zip_path.is_file() and sidecar.is_file()
    assert sidecar.read_text(encoding="utf-8").split()[0] == B.sha256_file(zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert any(name.endswith("SHA256SUMS.txt") for name in names)
        assert any(name.endswith("VALIDATION_SUMMARY.json") for name in names)
