from __future__ import annotations

import importlib.util
import json
import sys
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


P = load("sdb_deepseek_r3_provenance_test", ROOT / "scripts/evaluation/build_deepseek_v2_2_r3_provenance_binding.py")


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    return path


def make_track(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str = "succeeded"):
    monkeypatch.setitem(P.EXPECTED_ROWS, "machine", 1)
    root = tmp_path / "machine"
    manifest_row = {"request_id": "m1", "task_type": "single_service_discovery", "prediction_target": "service", "candidate_ids": ["a", "b"], "acceptable_gold_sets": [["a"]]}
    manifest = write_jsonl(tmp_path / "manifest.jsonl", [manifest_row])
    raw = write_json(root / "artifacts/x/raw_response_attempt_1.json", {"choices": []})
    response_attempt = write_json(root / "artifacts/x/response_attempt_1.json", {"status": 200})
    prediction = write_json(root / "artifacts/x/parsed_prediction.json", {"ranked_candidate_ids": ["a", "b"]})
    row = {
        "request_id": "m1", "provider": P.PROVIDER, "experiment_revision": P.EXPERIMENT_REVISION,
        "implementation_revision": P.IMPLEMENTATION_REVISION, "transport_protocol": P.TRANSPORT_PROTOCOL,
        "track": "machine", "requested_model": "DeepSeek-V4-Flash",
        "requested_model_version_mapping": "DeepSeek-V4-Flash-0731", "response_model": "DeepSeek-V4-Flash",
        "http_status": 200, "response_complete_received": True, "terminal_event_received": None,
        "done_received": None, "sse_event_count": 0, "response_object_count": 1, "finish_reason": "stop",
        "status": status, "parse_status": "valid" if status == "succeeded" else "invalid",
        "parsed_prediction_path": "artifacts/x/parsed_prediction.json" if status == "succeeded" else None,
        "source_manifest_sha256": P.sha256_file(manifest),
        "source_row_sha256": P.sha256_bytes(P.stable_json(manifest_row).encode()),
        "runner_sha256": "1" * 64, "parser_sha256": "2" * 64, "runtime_freeze_sha256": "3" * 64,
        "budget_freeze_sha256": "4" * 64, "endpoint_sha256": "5" * 64, "git_commit_sha": "UNKNOWN",
        "attempt_count": 1, "retry_count": 0, "raw_response_path": "artifacts/x/raw_response_attempt_1.json",
        "raw_response_sha256": P.sha256_file(raw),
    }
    write_jsonl(root / "REQUEST_STATUS.jsonl", [row])
    ledger = [
        {"event": "attempt_started", "request_id": "m1", "attempt": 1},
        {"event": "attempt_finished", "request_id": "m1", "attempt": 1, "will_retry": False,
         "raw_response_path": row["raw_response_path"], "raw_response_sha256": row["raw_response_sha256"],
         "response_attempt_path": "artifacts/x/response_attempt_1.json", "response_attempt_sha256": P.sha256_file(response_attempt)},
    ]
    write_jsonl(root / "ATTEMPT_LEDGER.jsonl", ledger)
    write_json(root / "RUN_SUMMARY.json", {
        "status": "COMPLETE_ALL_PARSED" if status == "succeeded" else "COMPLETE_WITH_MODEL_FAILURES",
        "provider": P.PROVIDER, "experiment_revision": P.EXPERIMENT_REVISION,
        "implementation_revision": P.IMPLEMENTATION_REVISION, "transport_protocol": P.TRANSPORT_PROTOCOL,
        "mode": "formal", "track": "machine", "requested_rows": 1, "terminal_rows": 1,
        "status_counts": {status: 1, "infra_error": 0, "api_error": 0},
    })
    return root, manifest, row


@pytest.mark.parametrize("status", ["succeeded", "parse_failure"])
def test_track_and_ledger_validation_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str):
    root, manifest, _ = make_track(tmp_path, monkeypatch, status)
    result = P.validate_run_track(track="machine", run_root=root, manifest=manifest)
    ledger = P.validate_attempt_ledger(run_root=root, statuses_by_id=result["status_by_id"])
    assert result["rows"] == 1 and ledger["attempt_started_count"] == ledger["attempt_finished_count"] == 1


@pytest.mark.parametrize("field,bad", [
    ("implementation_revision", "R2"), ("transport_protocol", "sse"), ("provider", "qwen"),
    ("response_complete_received", False), ("terminal_event_received", True), ("done_received", True),
    ("sse_event_count", 1), ("response_object_count", 2), ("finish_reason", "length"),
    ("http_status", 500), ("response_model", "other"), ("requested_model", "other"),
])
def test_invalid_r3_row_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, bad):
    root, manifest, _ = make_track(tmp_path, monkeypatch)
    rows = P.read_jsonl(root / "REQUEST_STATUS.jsonl"); rows[0][field] = bad; write_jsonl(root / "REQUEST_STATUS.jsonl", rows)
    with pytest.raises(ValueError, match="BLOCKED_RESULT_INTEGRITY"):
        P.validate_run_track(track="machine", run_root=root, manifest=manifest)


def test_duplicate_id_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, manifest, row = make_track(tmp_path, monkeypatch)
    monkeypatch.setitem(P.EXPECTED_ROWS, "machine", 2)
    summary_path = root / "RUN_SUMMARY.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["requested_rows"] = 2
    summary["terminal_rows"] = 2
    summary["status_counts"] = {"succeeded": 2}
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    write_jsonl(root / "REQUEST_STATUS.jsonl", [row, row])
    write_jsonl(manifest, [json.loads(manifest.read_text().splitlines()[0])] * 2)
    with pytest.raises(ValueError, match="DUPLICATE"):
        P.validate_run_track(track="machine", run_root=root, manifest=manifest)


@pytest.mark.parametrize("mutation", ["missing_finish", "raw_hash", "attempt_count", "retry_count"])
def test_ledger_integrity_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str):
    root, manifest, _ = make_track(tmp_path, monkeypatch)
    result = P.validate_run_track(track="machine", run_root=root, manifest=manifest)
    if mutation == "missing_finish":
        write_jsonl(root / "ATTEMPT_LEDGER.jsonl", [P.read_jsonl(root / "ATTEMPT_LEDGER.jsonl")[0]])
    elif mutation == "raw_hash":
        rows = P.read_jsonl(root / "ATTEMPT_LEDGER.jsonl"); rows[-1]["raw_response_sha256"] = "0" * 64; write_jsonl(root / "ATTEMPT_LEDGER.jsonl", rows)
    else:
        result["status_by_id"]["m1"][mutation] = 9
    with pytest.raises(ValueError, match="BLOCKED_RESULT_INTEGRITY"):
        P.validate_attempt_ledger(run_root=root, statuses_by_id=result["status_by_id"])


def test_commit_binding_unknown_and_exact_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    blobs = {P.RUNNER_REL: b"runner", P.PARSER_REL: b"parser", P.RUNTIME_REL: b"runtime", P.SIZE_UTIL_REL: b"size", P.BUDGET_BUILDER_REL: b"builder"}
    monkeypatch.setattr(P, "git_blob_bytes", lambda repo, commit, rel: blobs[rel])
    runtime = tmp_path / "runtime"; runtime.write_bytes(blobs[P.RUNTIME_REL])
    budget = tmp_path / "budget"; budget.write_bytes(b"budget")
    base = {"runner_sha256": P.sha256_bytes(b"runner"), "parser_sha256": P.sha256_bytes(b"parser"), "runtime_freeze_sha256": P.sha256_bytes(b"runtime"), "budget_freeze_sha256": P.sha256_bytes(b"budget")}
    for git_value in ("UNKNOWN", P.INFERENCE_PUBLIC_COMMIT):
        result = P.validate_commit_binding(repo_root=tmp_path, statuses=[{**base, "git_commit_sha": git_value}], runtime_freeze=runtime, budget_freeze=budget)
        assert result["runner_hash_match"] is True


@pytest.mark.parametrize("field", ["runner_sha256", "parser_sha256", "runtime_freeze_sha256", "budget_freeze_sha256"])
def test_commit_hash_mismatch_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str):
    blobs = {P.RUNNER_REL: b"runner", P.PARSER_REL: b"parser", P.RUNTIME_REL: b"runtime", P.SIZE_UTIL_REL: b"size", P.BUDGET_BUILDER_REL: b"builder"}
    monkeypatch.setattr(P, "git_blob_bytes", lambda repo, commit, rel: blobs[rel])
    runtime = tmp_path / "runtime"; runtime.write_bytes(b"runtime")
    budget = tmp_path / "budget"; budget.write_bytes(b"budget")
    row = {"runner_sha256": P.sha256_bytes(b"runner"), "parser_sha256": P.sha256_bytes(b"parser"), "runtime_freeze_sha256": P.sha256_bytes(b"runtime"), "budget_freeze_sha256": P.sha256_bytes(b"budget"), "git_commit_sha": "UNKNOWN"}
    row[field] = "0" * 64
    with pytest.raises(ValueError, match="BLOCKED_INFERENCE_PROVENANCE_BINDING"):
        P.validate_commit_binding(repo_root=tmp_path, statuses=[row], runtime_freeze=runtime, budget_freeze=budget)


@pytest.mark.parametrize("values", [["other"], ["UNKNOWN", P.INFERENCE_PUBLIC_COMMIT]])
def test_invalid_git_values_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values):
    blobs = {P.RUNNER_REL: b"runner", P.PARSER_REL: b"parser", P.RUNTIME_REL: b"runtime", P.SIZE_UTIL_REL: b"size", P.BUDGET_BUILDER_REL: b"builder"}
    monkeypatch.setattr(P, "git_blob_bytes", lambda repo, commit, rel: blobs[rel])
    runtime = tmp_path / "runtime"; runtime.write_bytes(b"runtime")
    budget = tmp_path / "budget"; budget.write_bytes(b"budget")
    base = {"runner_sha256": P.sha256_bytes(b"runner"), "parser_sha256": P.sha256_bytes(b"parser"), "runtime_freeze_sha256": P.sha256_bytes(b"runtime"), "budget_freeze_sha256": P.sha256_bytes(b"budget")}
    with pytest.raises(ValueError, match="git commit"):
        P.validate_commit_binding(repo_root=tmp_path, statuses=[{**base, "git_commit_sha": value} for value in values], runtime_freeze=runtime, budget_freeze=budget)
