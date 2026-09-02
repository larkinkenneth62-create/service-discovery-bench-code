from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = "deepseek"
REVISION = "DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2"
IMPLEMENTATION_REVISION = "DEEPSEEK_V4_FLASH_V2_2_R3_NONSTREAM_GATEWAY"
TRANSPORT_PROTOCOL = "openai_chat_completions_json_nonstream"
INFERENCE_PUBLIC_COMMIT = "3657a53b3ac3c98adc66ee3475111ba2115b83a3"
MODEL_IDS = {"DeepSeek-V4-Flash", "DeepSeek-V4-Flash-0731"}
EXPECTED_ROWS = {"native": 4798, "machine": 197}


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load("sdb_deepseek_v2_2_r2_frozen_metrics_for_r3", Path(__file__).with_name("score_deepseek_full_v2_2.py"))
CONTRACTS = _load(
    "sdb_deepseek_v2_2_contracts_for_r3_scoring",
    ROOT / "experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/output_contracts_v2_2.py",
)


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _single(rows: list[dict[str, Any]], field: str) -> Any:
    values = {stable_json(row.get(field)) for row in rows}
    if len(values) != 1:
        raise ValueError(f"BLOCKED_MIXED_PROVENANCE: {field}")
    return rows[0].get(field)


def validate_binding(binding: dict[str, Any], *, track: str, manifest: Path, request_status: Path, run_summary: Path) -> None:
    expected = {
        "status": "PASS",
        "provider": PROVIDER,
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "inference_public_commit": INFERENCE_PUBLIC_COMMIT,
        "effective_inference_git_commit": INFERENCE_PUBLIC_COMMIT,
        "source_snapshot_match": True,
        "inference_rerun": False,
        "original_result_files_modified": False,
    }
    if any(binding.get(field) != value for field, value in expected.items()):
        raise ValueError("BLOCKED_R3_PROVENANCE_BINDING")
    details = binding.get("tracks", {}).get(track)
    if not isinstance(details, dict):
        raise ValueError("BLOCKED_R3_PROVENANCE_BINDING: missing track")
    hashes = {
        "manifest_sha256": sha256_file(manifest),
        "request_status_sha256": sha256_file(request_status),
        "run_summary_sha256": sha256_file(run_summary),
    }
    if any(details.get(field) != value for field, value in hashes.items()):
        raise ValueError(f"BLOCKED_R3_PROVENANCE_BINDING: {track} hash")


def _candidate_ids(source: dict[str, Any]) -> list[str]:
    return BASE._candidate_ids(source)


def _strict_prediction(source: dict[str, Any], status: dict[str, Any], artifact_root: Path) -> None:
    if status["status"] == "parse_failure":
        if status.get("parse_status") != "invalid":
            raise ValueError("BLOCKED_FOREIGN_OR_MIXED_RESULT_ROWS")
        return
    if status.get("parse_status") != "valid" or not isinstance(status.get("parsed_prediction_path"), str):
        raise ValueError("BLOCKED_PARSED_PREDICTION")
    root = artifact_root.resolve()
    path = (artifact_root / status["parsed_prediction_path"]).resolve()
    if path != root and root not in path.parents:
        raise ValueError("BLOCKED_PARSED_PREDICTION_PATH_ESCAPE")
    prediction = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(prediction, dict):
        raise ValueError("BLOCKED_PARSED_PREDICTION")
    response = {"choices": [{"message": {"content": stable_json(prediction)}}]}
    candidates = _candidate_ids(source)
    contract = status.get("output_contract")
    if contract == CONTRACTS.TOP5_RANKING_V1:
        parsed = CONTRACTS.parse_topk_response(response, candidates, min(5, len(candidates)))
    elif contract == CONTRACTS.SELECTED_SET_V1:
        parsed = CONTRACTS.parse_selected_set_response(response, candidates)
    elif contract == CONTRACTS.RANKING_AND_SELECTED_SET_V1_10:
        parsed = CONTRACTS.parse_ranking_and_selected_set_response(response, candidates)
    else:
        raise ValueError("BLOCKED_FOREIGN_OR_MIXED_RESULT_ROWS")
    if not parsed.valid or parsed.data != prediction:
        raise ValueError(f"BLOCKED_PARSED_PREDICTION_REVALIDATION: {status.get('request_id')}")


def validate_r3_scope(track: str, manifest_rows: list[dict[str, Any]], status_rows: list[dict[str, Any]], artifact_root: Path, binding: dict[str, Any]) -> None:
    if track not in EXPECTED_ROWS or len(manifest_rows) != EXPECTED_ROWS[track] or len(status_rows) != EXPECTED_ROWS[track]:
        raise ValueError("BLOCKED_SCORING_ROW_COUNT")
    normalized = [{**row, "_id": row.get("benchmark_task_id", row.get("request_id"))} for row in manifest_rows]
    manifests = BASE.unique(normalized, "_id", "manifest")
    statuses = BASE.unique(status_rows, "request_id", "status")
    if set(manifests) != set(statuses):
        raise ValueError("BLOCKED_SCORING_IDENTITY")
    original_git_values = sorted({str(row.get("git_commit_sha")) for row in status_rows})
    if original_git_values not in (["UNKNOWN"], [INFERENCE_PUBLIC_COMMIT]):
        raise ValueError("BLOCKED_GIT_COMMIT_BINDING")
    if binding.get("original_git_commit_values") != original_git_values:
        raise ValueError("BLOCKED_GIT_COMMIT_BINDING")
    exact = {
        "provider": PROVIDER,
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "track": track,
        "response_complete_received": True,
        "terminal_event_received": None,
        "done_received": None,
        "sse_event_count": 0,
        "response_object_count": 1,
        "finish_reason": "stop",
    }
    if track == "native" and {row.get("task_type") for row in manifest_rows} != set(BASE.TASKS):
        raise ValueError("BLOCKED_NATIVE_TASK_COVERAGE")
    for request_id, status in statuses.items():
        source = manifests[request_id]
        if status.get("status") not in {"succeeded", "parse_failure"}:
            raise ValueError("BLOCKED_SCORING_STATUS")
        if any(status.get(field) != value for field, value in exact.items()):
            raise ValueError("BLOCKED_FOREIGN_OR_MIXED_RESULT_ROWS")
        if status.get("response_model") not in MODEL_IDS or status.get("http_status") != 200:
            raise ValueError("BLOCKED_FOREIGN_OR_MIXED_RESULT_ROWS")
        if status.get("output_contract") != BASE._expected_contract(track, source.get("task_type")):
            raise ValueError("BLOCKED_FOREIGN_OR_MIXED_RESULT_ROWS")
        original = dict(source)
        original.pop("_id", None)
        if status.get("source_row_sha256") != BASE.sha256_text(BASE.stable_json(original)):
            raise ValueError("BLOCKED_SOURCE_ROW_HASH_MISMATCH")
        if status.get("candidate_count") != len(_candidate_ids(source)):
            raise ValueError("BLOCKED_CANDIDATE_COUNT_MISMATCH")
        _strict_prediction(source, status, artifact_root)


def scoring_git_commit() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def score(*, track: str, manifest: Path, request_status: Path, run_summary: Path, artifact_root: Path, provenance_binding: Path, output_dir: Path, metadata_field_map: Path | None = None) -> dict[str, Any]:
    manifest_rows = BASE.read_jsonl(manifest)
    status_rows = BASE.read_jsonl(request_status)
    binding = read_json(provenance_binding)
    validate_binding(binding, track=track, manifest=manifest, request_status=request_status, run_summary=run_summary)
    validate_r3_scope(track, manifest_rows, status_rows, artifact_root, binding)
    field_map = BASE.load_metadata_field_map(metadata_field_map)
    scored = BASE.score_rows(manifest_rows, status_rows, artifact_root, track, enforce_scope=False, metadata_field_map=field_map)
    for row in scored:
        row["implementation_revision"] = IMPLEMENTATION_REVISION
        row["transport_protocol"] = TRANSPORT_PROTOCOL
    aggregated = BASE.aggregate(scored, track)
    if track == "machine":
        not_available = {"finding": "NOT_AVAILABLE", "reason": "Machine does not cover all six Native tasks"}
        aggregated["macro_6_exact_task_success"] = not_available
        aggregated["tables"]["MACRO_6_EXACT_TASK_SUCCESS.csv"] = [not_available]
    script_path = Path(__file__).resolve()
    binding_hash = sha256_file(provenance_binding)
    commit = scoring_git_commit()
    summary = {
        "status": "PASS",
        "provider": PROVIDER,
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "track": track,
        "rows": len(scored),
        "manifest_sha256": sha256_file(manifest),
        "request_status_sha256": sha256_file(request_status),
        "run_summary_sha256": sha256_file(run_summary),
        "inference_provenance_binding_sha256": binding_hash,
        "original_inference_git_commit_values": binding["original_git_commit_values"],
        "effective_inference_git_commit": INFERENCE_PUBLIC_COMMIT,
        "scoring_git_commit": commit,
        "scoring_script_sha256": sha256_file(script_path),
        "old_qwen_rows_reused": 0,
        "inference_rerun": False,
        "paid_api_calls": 0,
        **{key: value for key, value in aggregated.items() if key != "tables"},
    }
    provenance = {
        "status": "PASS",
        "track": track,
        "inference_public_commit": INFERENCE_PUBLIC_COMMIT,
        "scoring_git_commit": commit,
        "scoring_script_sha256": summary["scoring_script_sha256"],
        "inference_provenance_binding_sha256": binding_hash,
        "inputs": {
            "manifest_sha256": summary["manifest_sha256"],
            "request_status_sha256": summary["request_status_sha256"],
            "run_summary_sha256": summary["run_summary_sha256"],
        },
        "model_inference_calls": 0,
        "paid_api_calls": 0,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "PER_REQUEST_SCORES.json").write_text(json.dumps(scored, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output_dir / "SCORE_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output_dir / "SCORING_PROVENANCE.json").write_text(json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    for filename, rows in aggregated["tables"].items():
        BASE.write_csv(output_dir / filename, rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Score one provenance-bound DeepSeek V2.2 R3 non-stream track")
    parser.add_argument("--track", choices=("native", "machine"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--request-status", type=Path, required=True)
    parser.add_argument("--run-summary", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--provenance-binding", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--metadata-field-map", type=Path)
    args = parser.parse_args()
    result = score(**vars(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
