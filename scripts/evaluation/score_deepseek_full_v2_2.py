from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


PROVIDER = "deepseek"
REVISION = "DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2"
IMPLEMENTATION_REVISION = "DEEPSEEK_V4_FLASH_V2_2_R2_GATE_ACCOUNTING"
MODEL_IDS = {"deepseek-v4-flash", "DeepSeek-V4-Flash-0731"}
COMBINED = "RANKING_AND_SELECTED_SET_V1_10"
TASKS = (
    "single_service_discovery", "single_api_recommendation",
    "multi_service_discovery", "multi_api_recommendation",
    "composable_service_discovery", "composable_api_recommendation",
)
SET_TASKS = TASKS[1:]
EXPECTED_ROWS = {"native": 4798, "machine": 197}
SET_FIELDS = ("exact_set_match", "precision", "recall", "f1", "completeness", "jaccard", "under_selection", "over_selection", "cardinality_error", "parse_failure")
RANKING_FIELDS = ("hit_at_1", "mrr_at_5", "recall_at_5", "ndcg_at_5", "parse_failure")
PROVENANCE_FIELDS = (
    "provider", "experiment_revision", "implementation_revision", "requested_model",
    "model_version_mapping", "runtime_freeze_sha256", "budget_freeze_sha256",
    "runner_sha256", "parser_sha256", "endpoint_sha256", "git_commit_sha",
)
METADATA_DIMENSIONS = ("source_dataset", "core_expansion", "evidence_tier", "pairing_id")


def _load_common() -> Any:
    path = Path(__file__).with_name("score_native_machine_selection_v1_5.py")
    spec = importlib.util.spec_from_file_location("sdb_frozen_selection_metrics_v1_5_for_deepseek", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen metrics: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V15 = _load_common()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"non-object row at {path}:{line_number}")
        result.append(value)
    return result


def unique(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value or value in result:
            raise ValueError(f"invalid or duplicate {label} ID: {value!r}")
        result[value] = row
    return result


def _candidate_ids(row: dict[str, Any]) -> list[str]:
    visible = row.get("model_visible_input", row)
    documents = visible.get("candidate_documents") if isinstance(visible, dict) else None
    values = row.get("candidate_ids") or ([item.get("candidate_id") for item in documents] if isinstance(documents, list) else None)
    if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
        raise ValueError("BLOCKED_CANDIDATE_COUNT_MISMATCH: manifest candidate IDs are invalid")
    return values


def _expected_contract(track: str, task: Any) -> str:
    if track == "machine" or task == "single_service_discovery":
        return "TOP5_RANKING_V1"
    if task == "single_api_recommendation":
        return COMBINED
    if isinstance(task, str) and task.startswith(("multi_", "composable_")):
        return "SELECTED_SET_V1"
    raise ValueError(f"unregistered task in DeepSeek manifest: {task!r}")


def _singleton(statuses: dict[str, dict[str, Any]], field: str) -> Any:
    values = {stable_json(row.get(field)) for row in statuses.values()}
    if len(values) != 1:
        raise ValueError(f"BLOCKED_MIXED_PROVENANCE: {field}")
    value = next(iter(statuses.values())).get(field)
    if value is None or value == "":
        raise ValueError(f"BLOCKED_MISSING_PROVENANCE: {field}")
    return value


def validate_scope(track: str, manifests: dict[str, dict[str, Any]], statuses: dict[str, dict[str, Any]], manifest_sha256: str | None = None) -> dict[str, Any]:
    if track not in EXPECTED_ROWS:
        raise ValueError(f"unsupported track: {track}")
    expected = EXPECTED_ROWS[track]
    if len(manifests) != expected or len(statuses) != expected or set(manifests) != set(statuses):
        raise ValueError(f"{track} manifest/status identity or count mismatch; expected {expected}")
    if any(row.get("provider") != PROVIDER or row.get("experiment_revision") != REVISION for row in statuses.values()):
        raise ValueError("status rows are not independent DeepSeek V2.2 results")
    allowed = {"succeeded", "parse_failure"}
    observed_statuses = {row.get("status") for row in statuses.values()}
    if not observed_statuses <= allowed:
        raise ValueError(f"BLOCKED_SCORING_STATUS: {sorted(str(value) for value in observed_statuses - allowed)}")
    provenance = {field: _singleton(statuses, field) for field in PROVENANCE_FIELDS}
    if provenance["implementation_revision"] != IMPLEMENTATION_REVISION:
        raise ValueError("BLOCKED_IMPLEMENTATION_REVISION_MISMATCH")
    if provenance["git_commit_sha"] == "UNKNOWN":
        raise ValueError("BLOCKED_GIT_COMMIT_UNKNOWN")
    if manifest_sha256 is not None:
        source_hashes = {row.get("source_manifest_sha256") for row in statuses.values()}
        if source_hashes != {manifest_sha256}:
            raise ValueError("BLOCKED_SOURCE_MANIFEST_HASH_MISMATCH")
    if track == "native" and set(row.get("task_type") for row in manifests.values()) != set(TASKS):
        raise ValueError("Native scoring requires all six tasks")
    for request_id, source in manifests.items():
        status = statuses[request_id]
        task = source.get("task_type")
        if status.get("track") != track or status.get("task_type") != task or status.get("output_contract") != _expected_contract(track, task):
            raise ValueError(f"DeepSeek track/task/contract mismatch: {request_id}")
        if status.get("source_row_sha256") != sha256_text(stable_json(source)):
            raise ValueError(f"BLOCKED_SOURCE_ROW_HASH_MISMATCH: {request_id}")
        if status.get("candidate_count") != len(_candidate_ids(source)):
            raise ValueError(f"BLOCKED_CANDIDATE_COUNT_MISMATCH: {request_id}")
        if status.get("response_model") not in MODEL_IDS:
            raise ValueError(f"BLOCKED_RESPONSE_MODEL_MISMATCH: {request_id}")
    return provenance


def load_prediction(status: dict[str, Any], artifact_root: Path) -> dict[str, Any] | None:
    if status["status"] == "parse_failure":
        return None
    relative = status.get("parsed_prediction_path")
    if not isinstance(relative, str):
        raise ValueError(f"successful row lacks parsed prediction path: {status.get('request_id')}")
    root = artifact_root.resolve()
    path = (artifact_root / relative).resolve()
    if path != root and root not in path.parents:
        raise ValueError("parsed prediction path escapes artifact root")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("parsed prediction must be an object")
    return value


def load_metadata_field_map(path: Path | None) -> dict[str, str | None]:
    if path is None:
        return {field: None for field in METADATA_DIMENSIONS}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != set(METADATA_DIMENSIONS):
        raise ValueError("metadata field map must define the four frozen dimensions")
    if any(item is not None and (not isinstance(item, str) or not item) for item in value.values()):
        raise ValueError("metadata field-map values must be field names or null")
    return value


def score_rows(manifest_rows: list[dict[str, Any]], status_rows: list[dict[str, Any]], artifact_root: Path, track: str, *, enforce_scope: bool = True, manifest_sha256: str | None = None, metadata_field_map: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
    normalized = [{**row, "_id": row.get("benchmark_task_id", row.get("request_id"))} for row in manifest_rows]
    manifests = unique(normalized, "_id", "manifest")
    statuses = unique(status_rows, "request_id", "status")
    original_by_id = {row.get("benchmark_task_id", row.get("request_id")): row for row in manifest_rows}
    if enforce_scope:
        validate_scope(track, original_by_id, statuses, manifest_sha256)
    elif set(manifests) != set(statuses):
        raise ValueError("manifest/status IDs differ")
    field_map = metadata_field_map or {field: None for field in METADATA_DIMENSIONS}
    scored: list[dict[str, Any]] = []
    for request_id in sorted(manifests):
        source = original_by_id[request_id]
        status = statuses[request_id]
        gold = V15._gold_sets(source)
        prediction = load_prediction(status, artifact_root)
        failed = status["status"] == "parse_failure"
        contract = status.get("output_contract")
        ranking = None
        selected = None
        if contract == "TOP5_RANKING_V1":
            ranking = V15.score_ranking(None if prediction is None else prediction["ranked_candidate_ids"], gold, failed)
            exact = ranking["hit_at_1"]
        elif contract == "SELECTED_SET_V1":
            selected = V15.score_selected_set(None if prediction is None else prediction["selected_candidate_ids"], gold, failed)
            exact = selected["exact_set_match"]
        elif contract == COMBINED:
            ranking = V15.score_ranking(None if prediction is None else prediction["ranked_candidate_ids"], gold, failed)
            selected = V15.score_selected_set(None if prediction is None else prediction["selected_candidate_ids"], gold, failed)
            exact = selected["exact_set_match"]
        else:
            raise ValueError(f"unknown output contract for {request_id}: {contract}")
        task_type = source["task_type"]
        metadata = {name: source.get(actual) if actual else None for name, actual in field_map.items()}
        scored.append({
            "request_id": request_id, "track": track, "task_type": task_type,
            "task_family": task_type.split("_", 1)[0], "prediction_target": source["prediction_target"],
            "provider": PROVIDER, "experiment_revision": REVISION,
            "implementation_revision": IMPLEMENTATION_REVISION, "contract": contract,
            "status": status["status"], "parse_status": status.get("parse_status"),
            "candidate_count": int(status["candidate_count"]), "gold_count": min(len(option) for option in gold),
            "exact_task_success": exact, "ranking_metrics": ranking, "set_metrics": selected,
            "parse_failure": float(failed), **metadata,
        })
    return scored


def mean(rows: list[dict[str, Any]], getter: Callable[[dict[str, Any]], float]) -> float | None:
    return sum(float(getter(row)) for row in rows) / len(rows) if rows else None


def _common_group(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row.get(field)].append(row)
    result = []
    for key, values in sorted(grouped.items(), key=lambda item: str(item[0])):
        result.append({field: key, "n": len(values), "exact_task_success": mean(values, lambda row: row["exact_task_success"]), "parse_failure": mean(values, lambda row: row["parse_failure"]), "raw_success_count": sum(row["exact_task_success"] == 1.0 for row in values), "raw_parse_failure_count": sum(row["parse_failure"] == 1.0 for row in values)})
    return result


def _metric_groups(rows: list[dict[str, Any]], field: str, metric_key: str, metrics: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row[metric_key] is not None:
            grouped[row.get(field)].append(row)
    result = []
    for key, values in sorted(grouped.items(), key=lambda item: str(item[0])):
        result.append({field: key, "n": len(values), **{metric: mean(values, lambda row, name=metric: row[metric_key][name]) for metric in metrics}})
    return result


def _not_available(dimension: str) -> list[dict[str, Any]]:
    return [{"dimension": dimension, "finding": "NOT_AVAILABLE", "n": "N/A"}]


def aggregate(scored: list[dict[str, Any]], track: str) -> dict[str, Any]:
    by_task_common = _common_group(scored, "task_type")
    by_task_ranking = _metric_groups(scored, "task_type", "ranking_metrics", RANKING_FIELDS)
    by_task_set = _metric_groups(scored, "task_type", "set_metrics", SET_FIELDS)
    task_lookup = {row["task_type"]: row for row in by_task_common}
    macro_tasks = [task_lookup[task] for task in TASKS if task in task_lookup]
    macro_6 = [{"task_count": len(macro_tasks), "exact_task_success": mean(macro_tasks, lambda row: row["exact_task_success"]), "parse_failure": mean(macro_tasks, lambda row: row["parse_failure"]), "aggregation": "TASK_EQUAL"}]
    micro = [{"n": len(scored), "exact_task_success": mean(scored, lambda row: row["exact_task_success"]), "parse_failure": mean(scored, lambda row: row["parse_failure"]), "aggregation": "ROW_WEIGHTED"}]
    ranking_rows = [row for row in scored if row["ranking_metrics"] is not None]
    set_rows = [row for row in scored if row["set_metrics"] is not None]
    set_by_task = {row["task_type"]: row for row in by_task_set}
    set_task_rows = [set_by_task[task] for task in SET_TASKS if task in set_by_task]
    set_macro = [{"task_count": len(set_task_rows), "aggregation": "TASK_EQUAL", **{metric: mean(set_task_rows, lambda row, name=metric: row[name]) for metric in SET_FIELDS}}] if set_task_rows else _not_available("set_selection")
    set_micro = [{"n": len(set_rows), "aggregation": "ROW_WEIGHTED", **{metric: mean(set_rows, lambda row, name=metric: row["set_metrics"][name]) for metric in SET_FIELDS}}] if set_rows else _not_available("set_selection")
    single_service = [row for row in scored if row["task_type"] == "single_service_discovery" and row["ranking_metrics"] is not None]
    single_api_rank = [row for row in scored if row["task_type"] == "single_api_recommendation" and row["ranking_metrics"] is not None]
    single_api_set = [row for row in scored if row["task_type"] == "single_api_recommendation" and row["set_metrics"] is not None]
    ranking_summary = [{"n": len(ranking_rows), **{metric: mean(ranking_rows, lambda row, name=metric: row["ranking_metrics"][name]) for metric in RANKING_FIELDS}}] if ranking_rows else _not_available("ranking")
    tables: dict[str, list[dict[str, Any]]] = {
        "BY_TASK_COMMON.csv": by_task_common,
        "BY_TASK_RANKING.csv": by_task_ranking or _not_available("task_ranking"),
        "BY_TASK_SET_SELECTION.csv": by_task_set or _not_available("task_set_selection"),
        "MACRO_6_EXACT_TASK_SUCCESS.csv": macro_6,
        "MICRO_EXACT_TASK_SUCCESS.csv": micro,
        "SINGLE_SERVICE_RANKING.csv": _metric_groups(single_service, "task_type", "ranking_metrics", RANKING_FIELDS) or _not_available("single_service_ranking"),
        "SINGLE_API_RANKING.csv": _metric_groups(single_api_rank, "task_type", "ranking_metrics", RANKING_FIELDS) or _not_available("single_api_ranking"),
        "SINGLE_API_SET_SELECTION.csv": _metric_groups(single_api_set, "task_type", "set_metrics", SET_FIELDS) or _not_available("single_api_set_selection"),
        "SET_SELECTION_MACRO_TASK_EQUAL.csv": set_macro,
        "SET_SELECTION_MICRO_ROW_WEIGHTED.csv": set_micro,
        "SERVICE_VS_API.csv": _common_group(scored, "prediction_target"),
        "SINGLE_MULTI_COMPOSABLE.csv": _common_group(scored, "task_family"),
        "BY_CANDIDATE_COUNT.csv": _common_group(scored, "candidate_count"),
        "BY_GOLD_COUNT.csv": _common_group(scored, "gold_count"),
        "BY_PARSE_STATUS.csv": _common_group(scored, "parse_status"),
    }
    for dimension, filename in (("source_dataset", "BY_SOURCE_DATASET.csv"), ("core_expansion", "BY_CORE_EXPANSION.csv"), ("evidence_tier", "BY_EVIDENCE_TIER.csv")):
        tables[filename] = _not_available(dimension) if all(row.get(dimension) is None for row in scored) else _common_group(scored, dimension)
    return {
        "track": track, "rows": len(scored), "macro_6_exact_task_success": macro_6[0],
        "micro_exact_task_success": micro[0], "ranking_micro_row_weighted": ranking_summary[0],
        "set_selection_macro_task_equal": set_macro[0], "set_selection_micro_row_weighted": set_micro[0],
        "tables": tables,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(field for row in rows for field in row)) if rows else ["finding"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score one independent DeepSeek V4 Flash V2.2 full track")
    parser.add_argument("--track", choices=("native", "machine"), required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--request-status", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--metadata-field-map", type=Path)
    args = parser.parse_args()
    manifest_rows = read_jsonl(args.manifest)
    status_rows = read_jsonl(args.request_status)
    field_map = load_metadata_field_map(args.metadata_field_map)
    scored = score_rows(manifest_rows, status_rows, args.artifact_root, args.track, manifest_sha256=sha256_file(args.manifest), metadata_field_map=field_map)
    provenance = {field: status_rows[0][field] for field in PROVENANCE_FIELDS}
    aggregated = aggregate(scored, args.track)
    summary = {"status": "PASS", "provider": PROVIDER, "experiment_revision": REVISION, "implementation_revision": IMPLEMENTATION_REVISION, "manifest_sha256": sha256_file(args.manifest), "old_qwen_rows_reused": 0, **provenance, **{key: value for key, value in aggregated.items() if key != "tables"}}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "PER_REQUEST_SCORES.json").write_text(json.dumps(scored, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "SCORE_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    for filename, rows in aggregated["tables"].items():
        write_csv(args.output_dir / filename, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
