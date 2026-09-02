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


B = load("sdb_deepseek_r3_bundle_test", ROOT / "scripts/release/build_deepseek_v4_flash_v2_2_r3_nonstream_bundle.py")


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    return path


def make_inputs(tmp_path: Path):
    q0 = write_json(tmp_path / "q0/Q0_REPORT.json", {
        "status": "PASS", "provider": B.PROVIDER, "experiment_revision": B.REVISION,
        "implementation_revision": B.IMPLEMENTATION_REVISION, "runtime_freeze_sha256": "1" * 64,
        "budget_freeze_sha256": "2" * 64,
    })
    q0_hash = B.sha256_file(q0)
    smoke, machine, native = tmp_path / "smoke", tmp_path / "machine", tmp_path / "native"
    common = {"status": "COMPLETE_ALL_PARSED", "provider": B.PROVIDER, "experiment_revision": B.REVISION, "implementation_revision": B.IMPLEMENTATION_REVISION, "transport_protocol": B.TRANSPORT_PROTOCOL, "runtime_freeze_sha256": "1" * 64, "budget_freeze_sha256": "2" * 64}
    smoke_summary = write_json(smoke / "RUN_SUMMARY.json", {**common, "mode": "smoke", "track": "smoke", "requested_rows": 60, "terminal_rows": 60, "gate_passed": True, "status_counts": {"succeeded": 60}, "prerequisite_q0_report_sha256": q0_hash})
    smoke_hash = B.sha256_file(smoke_summary)
    machine_summary = write_json(machine / "RUN_SUMMARY.json", {**common, "mode": "formal", "track": "machine", "requested_rows": 197, "terminal_rows": 197, "status_counts": {"succeeded": 197}, "prerequisite_q0_report_sha256": q0_hash, "prerequisite_smoke_summary_sha256": smoke_hash})
    machine_hash = B.sha256_file(machine_summary)
    native_summary = write_json(native / "RUN_SUMMARY.json", {**common, "mode": "formal", "track": "native", "requested_rows": 4798, "terminal_rows": 4798, "status_counts": {"succeeded": 4798}, "prerequisite_q0_report_sha256": q0_hash, "prerequisite_smoke_summary_sha256": smoke_hash, "prerequisite_machine_summary_sha256": machine_hash})
    def rows(track: str, count: int):
        return [{"request_id": f"{track}-{index}", "provider": B.PROVIDER, "experiment_revision": B.REVISION, "implementation_revision": B.IMPLEMENTATION_REVISION, "transport_protocol": B.TRANSPORT_PROTOCOL, "status": "succeeded"} for index in range(count)]
    for track, root, count in (("smoke", smoke, 60), ("machine", machine, 197), ("native", native, 4798)):
        write_jsonl(root / "REQUEST_STATUS.jsonl", rows(track, count)); write_jsonl(root / "ATTEMPT_LEDGER.jsonl", [])
    binding = write_json(tmp_path / "provenance/DEEPSEEK_V2_2_R3_RUN_PROVENANCE_BINDING.json", {
        "status": "PASS", "provider": B.PROVIDER, "experiment_revision": B.REVISION,
        "implementation_revision": B.IMPLEMENTATION_REVISION, "transport_protocol": B.TRANSPORT_PROTOCOL,
        "inference_public_commit": B.INFERENCE_PUBLIC_COMMIT, "source_snapshot_match": True,
        "original_result_files_modified": False, "inference_rerun": False,
        "tracks": {
            "machine": {"request_status_sha256": B.sha256_file(machine / "REQUEST_STATUS.jsonl"), "run_summary_sha256": B.sha256_file(machine_summary), "attempt_ledger_sha256": B.sha256_file(machine / "ATTEMPT_LEDGER.jsonl")},
            "native": {"request_status_sha256": B.sha256_file(native / "REQUEST_STATUS.jsonl"), "run_summary_sha256": B.sha256_file(native_summary), "attempt_ledger_sha256": B.sha256_file(native / "ATTEMPT_LEDGER.jsonl")},
        },
    })
    (binding.parent / "PROVENANCE_BINDING_REPORT.md").write_text("PASS\n", encoding="utf-8")
    binding_hash = B.sha256_file(binding)
    machine_score, native_score = tmp_path / "machine-score", tmp_path / "native-score"
    for track, root, count in (("machine", machine_score, 197), ("native", native_score, 4798)):
        write_json(root / "PER_REQUEST_SCORES.json", [{"request_id": f"{track}-{index}"} for index in range(count)])
        write_json(root / "SCORE_SUMMARY.json", {"status": "PASS", "provider": B.PROVIDER, "experiment_revision": B.REVISION, "implementation_revision": B.IMPLEMENTATION_REVISION, "transport_protocol": B.TRANSPORT_PROTOCOL, "rows": count, "inference_provenance_binding_sha256": binding_hash, "old_qwen_rows_reused": 0})
    return {"provenance_binding": binding, "q0_report": q0, "smoke_root": smoke, "machine_root": machine, "native_root": native, "machine_score_dir": machine_score, "native_score_dir": native_score}


def test_complete_r3_bundle_crc_checksums_and_sidecar(tmp_path: Path):
    inputs = make_inputs(tmp_path); output = tmp_path / "output"
    path = B.build_bundle(**inputs, output_dir=output)
    assert path.is_file() and (output / f"{B.ZIP_NAME}.sha256").is_file()
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert any(name.endswith("SHA256SUMS.txt") for name in names)
        assert any(name.endswith("PROVENANCE/DEEPSEEK_V2_2_R3_RUN_PROVENANCE_BINDING.json") for name in names)


def test_r2_summary_is_blocked(tmp_path: Path):
    inputs = make_inputs(tmp_path); path = inputs["native_root"] / "RUN_SUMMARY.json"
    value = json.loads(path.read_text()); value["implementation_revision"] = "R2"; write_json(path, value)
    with pytest.raises(ValueError, match="native"):
        B.validate_inputs(**inputs)


def test_binding_hash_mismatch_is_blocked(tmp_path: Path):
    inputs = make_inputs(tmp_path); summary = inputs["machine_score_dir"] / "SCORE_SUMMARY.json"
    value = json.loads(summary.read_text()); value["inference_provenance_binding_sha256"] = "0" * 64; write_json(summary, value)
    with pytest.raises(ValueError, match="SCORE_PROVENANCE"):
        B.validate_inputs(**inputs)


@pytest.mark.parametrize("track,count", [("machine", 196), ("native", 4797)])
def test_score_row_count_is_exact(tmp_path: Path, track: str, count: int):
    inputs = make_inputs(tmp_path); score_dir = inputs[f"{track}_score_dir"]
    write_json(score_dir / "PER_REQUEST_SCORES.json", [{"request_id": str(i)} for i in range(count)])
    with pytest.raises(ValueError, match="SCORE_ROW_COUNT"):
        B.validate_inputs(**inputs)
