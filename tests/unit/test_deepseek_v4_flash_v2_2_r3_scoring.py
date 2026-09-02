from __future__ import annotations

import importlib.util
import json
import math
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


S = load("sdb_deepseek_r3_scorer_test", ROOT / "scripts/evaluation/score_deepseek_full_v2_2_r3_nonstream.py")


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_jsonl(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    return path


def binding_for(tmp_path: Path, track: str, manifest: Path, status: Path, summary: Path):
    return write_json(tmp_path / "binding.json", {
        "status": "PASS", "provider": S.PROVIDER, "experiment_revision": S.REVISION,
        "implementation_revision": S.IMPLEMENTATION_REVISION, "transport_protocol": S.TRANSPORT_PROTOCOL,
        "inference_public_commit": S.INFERENCE_PUBLIC_COMMIT, "effective_inference_git_commit": S.INFERENCE_PUBLIC_COMMIT,
        "source_snapshot_match": True, "inference_rerun": False, "original_result_files_modified": False,
        "original_git_commit_values": ["UNKNOWN"],
        "tracks": {track: {"manifest_sha256": S.sha256_file(manifest), "request_status_sha256": S.sha256_file(status), "run_summary_sha256": S.sha256_file(summary)}},
    })


def machine_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prediction=None, status_name="succeeded"):
    monkeypatch.setitem(S.EXPECTED_ROWS, "machine", 1)
    prediction = prediction or {"ranked_candidate_ids": ["a", "b"]}
    manifest_row = {"request_id": "m", "task_type": "single_service_discovery", "prediction_target": "service", "candidate_ids": ["a", "b"], "acceptable_gold_sets": [["a"]]}
    manifest = write_jsonl(tmp_path / "manifest.jsonl", [manifest_row])
    prediction_path = write_json(tmp_path / "run/artifacts/x/parsed_prediction.json", prediction)
    row = {
        "request_id": "m", "provider": S.PROVIDER, "experiment_revision": S.REVISION,
        "implementation_revision": S.IMPLEMENTATION_REVISION, "transport_protocol": S.TRANSPORT_PROTOCOL,
        "track": "machine", "task_type": "single_service_discovery", "response_complete_received": True,
        "terminal_event_received": None, "done_received": None, "sse_event_count": 0, "response_object_count": 1,
        "finish_reason": "stop", "response_model": "DeepSeek-V4-Flash", "http_status": 200,
        "status": status_name, "parse_status": "valid" if status_name == "succeeded" else "invalid",
        "parsed_prediction_path": str(prediction_path.relative_to(tmp_path / "run")).replace("\\", "/") if status_name == "succeeded" else None,
        "output_contract": "TOP5_RANKING_V1", "candidate_count": 2,
        "source_row_sha256": S.BASE.sha256_text(S.BASE.stable_json(manifest_row)), "git_commit_sha": "UNKNOWN",
    }
    status = write_jsonl(tmp_path / "run/REQUEST_STATUS.jsonl", [row])
    summary = write_json(tmp_path / "run/RUN_SUMMARY.json", {"status": "COMPLETE_ALL_PARSED"})
    binding = binding_for(tmp_path, "machine", manifest, status, summary)
    return manifest, status, summary, binding, tmp_path / "run"


@pytest.mark.parametrize("field,bad", [
    ("implementation_revision", "R2"), ("transport_protocol", "sse"),
    ("inference_public_commit", "0" * 40), ("source_snapshot_match", False),
    ("inference_rerun", True), ("original_result_files_modified", True),
])
def test_binding_identity_rejections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, bad):
    manifest, status, summary, binding_path, _ = machine_inputs(tmp_path, monkeypatch)
    binding = json.loads(binding_path.read_text()); binding[field] = bad
    with pytest.raises(ValueError, match="BLOCKED_R3_PROVENANCE_BINDING"):
        S.validate_binding(binding, track="machine", manifest=manifest, request_status=status, run_summary=summary)


def test_binding_is_result_specific(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest, status, summary, binding_path, _ = machine_inputs(tmp_path, monkeypatch)
    status.write_text(status.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        S.validate_binding(json.loads(binding_path.read_text()), track="machine", manifest=manifest, request_status=status, run_summary=summary)


@pytest.mark.parametrize("field,bad", [
    ("implementation_revision", "R2"), ("transport_protocol", "sse"), ("provider", "qwen"),
    ("response_complete_received", False), ("terminal_event_received", True), ("done_received", True),
    ("sse_event_count", 1), ("response_object_count", 2), ("finish_reason", "length"),
])
def test_foreign_or_mixed_rows_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, bad):
    manifest, status_path, _, binding_path, root = machine_inputs(tmp_path, monkeypatch)
    rows = S.BASE.read_jsonl(status_path); rows[0][field] = bad
    with pytest.raises(ValueError, match="FOREIGN"):
        S.validate_r3_scope("machine", S.BASE.read_jsonl(manifest), rows, root, json.loads(binding_path.read_text()))


@pytest.mark.parametrize("prediction", [
    {"ranked_candidate_ids": ["a"]}, {"ranked_candidate_ids": ["a", "a"]},
    {"ranked_candidate_ids": ["a", "z"]}, {"wrong": ["a", "b"]},
])
def test_prediction_is_revalidated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prediction):
    manifest, status_path, _, binding_path, root = machine_inputs(tmp_path, monkeypatch, prediction)
    with pytest.raises(ValueError, match="REVALIDATION"):
        S.validate_r3_scope("machine", S.BASE.read_jsonl(manifest), S.BASE.read_jsonl(status_path), root, json.loads(binding_path.read_text()))


def test_full_machine_score_writes_provenance_and_keeps_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest, status, summary, binding, root = machine_inputs(tmp_path, monkeypatch)
    before = status.read_bytes()
    output = tmp_path / "scores"
    result = S.score(track="machine", manifest=manifest, request_status=status, run_summary=summary, artifact_root=root, provenance_binding=binding, output_dir=output)
    assert result["rows"] == 1 and result["paid_api_calls"] == 0
    assert result["macro_6_exact_task_success"]["finding"] == "NOT_AVAILABLE"
    assert result["inference_provenance_binding_sha256"] == S.sha256_file(binding)
    assert status.read_bytes() == before and (output / "SCORING_PROVENANCE.json").is_file()


def test_parse_failure_scores_all_zero(tmp_path: Path):
    manifest = [{"request_id": "x", "task_type": "single_service_discovery", "prediction_target": "service", "acceptable_gold_sets": [["a"]]}]
    status = [{"request_id": "x", "status": "parse_failure", "parse_status": "invalid", "output_contract": "TOP5_RANKING_V1", "candidate_count": 2}]
    row = S.BASE.score_rows(manifest, status, tmp_path, "machine", enforce_scope=False)[0]
    assert row["exact_task_success"] == 0 and row["ranking_metrics"]["parse_failure"] == 1


def test_single_api_primary_success_is_set_esm(tmp_path: Path):
    write_json(tmp_path / "p.json", {"ranked_candidate_ids": ["a", "b"], "selected_candidate_ids": ["a"]})
    manifest = [{"request_id": "x", "task_type": "single_api_recommendation", "prediction_target": "api", "acceptable_gold_sets": [["a", "b"]]}]
    status = [{"request_id": "x", "status": "succeeded", "parse_status": "valid", "output_contract": S.BASE.COMBINED, "candidate_count": 2, "parsed_prediction_path": "p.json"}]
    row = S.BASE.score_rows(manifest, status, tmp_path, "native", enforce_scope=False)[0]
    assert row["ranking_metrics"]["hit_at_1"] == 1 and row["set_metrics"]["exact_set_match"] == 0 and row["exact_task_success"] == 0


def test_private_truth_crosswalk_uses_formal_hash_bridge():
    source = {"benchmark_task_id": "r1", "task_type": "single_service_discovery", "prediction_target": "service", "setting": "native", "model_request_hash": "m1", "candidate_order_hash": "c1", "candidate_ids": ["a", "b"]}
    formal = {"benchmark_task_id": "r1", "task_type": "single_service_discovery", "prediction_target": "service", "setting": "native", "model_request_hash": "m1", "candidate_order_hash": "c1", "candidate_count": 2, "frozen_input_hash": "f1"}
    truth = {"benchmark_task_id": "r1", "task_type": "single_service_discovery", "frozen_input_hash": "f1", "acceptable_solutions": [["a"]], "source_dataset": "synthetic"}
    merged = S.build_scoring_rows([source], [formal], [truth])
    assert merged[0]["acceptable_gold_sets"] == [["a"]]
    assert merged[0]["source_dataset"] == "synthetic"
    assert "acceptable_gold_sets" not in source


@pytest.mark.parametrize("field", ["model_request_hash", "candidate_order_hash", "frozen_input_hash"])
def test_private_truth_crosswalk_rejects_hash_mismatch(field: str):
    source = {"benchmark_task_id": "r1", "task_type": "single_service_discovery", "prediction_target": "service", "setting": "native", "model_request_hash": "m1", "candidate_order_hash": "c1", "candidate_ids": ["a"]}
    formal = {"benchmark_task_id": "r1", "task_type": "single_service_discovery", "prediction_target": "service", "setting": "native", "model_request_hash": "m1", "candidate_order_hash": "c1", "candidate_count": 1, "frozen_input_hash": "f1"}
    truth = {"benchmark_task_id": "r1", "task_type": "single_service_discovery", "frozen_input_hash": "f1", "acceptable_solutions": [["a"]]}
    if field == "frozen_input_hash":
        truth[field] = "wrong"
    else:
        formal[field] = "wrong"
    with pytest.raises(ValueError, match="TRUTH_CROSSWALK"):
        S.build_scoring_rows([source], [formal], [truth])


@pytest.mark.parametrize("prediction,gold,expected", [
    (["a", "b"], ["a", "b"], (1, 1, 1, 1, 1, 0, 0, 0)),
    (["a"], ["a", "b"], (0, 1, .5, 2/3, 0, 1, 0, 1)),
    (["a", "c"], ["a", "b"], (0, .5, .5, .5, 0, 1, 1, 0)),
    ([], ["a"], (0, 0, 0, 0, 0, 1, 0, 1)),
])
def test_set_formulas(prediction, gold, expected):
    result = S.BASE.V15.score_selected_set(prediction, [set(gold)], False)
    observed = (result["exact_set_match"], result["precision"], result["recall"], result["f1"], result["completeness"], result["under_selection"], result["over_selection"], result["cardinality_error"])
    assert observed == pytest.approx(expected)


def test_ranking_formulas_rank2_and_full_gold_denominator():
    result = S.BASE.V15.score_ranking(["x", "a", "b"], [{"a", "b", "c"}], False)
    assert result["hit_at_1"] == 0 and result["mrr_at_5"] == .5 and result["recall_at_5"] == pytest.approx(2/3)
    expected_dcg = 1 / math.log2(3) + 1 / math.log2(4)
    expected_idcg = 1 / math.log2(2) + 1 / math.log2(3) + 1 / math.log2(4)
    assert result["ndcg_at_5"] == pytest.approx(expected_dcg / expected_idcg)


def test_acceptable_gold_exact_match_wins():
    result = S.BASE.V15.score_selected_set(["c"], [{"a", "b"}, {"c"}], False)
    assert result["exact_set_match"] == result["f1"] == result["jaccard"] == 1
