from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROVIDER = "deepseek"
REVISION = "DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2"
COMBINED = "RANKING_AND_SELECTED_SET_V1_10"
TASKS = (
    "single_service_discovery",
    "single_api_recommendation",
    "multi_service_discovery",
    "multi_api_recommendation",
    "composable_service_discovery",
    "composable_api_recommendation",
)
EXPECTED_ROWS = {"native": 4798, "machine": 197}
SET_FIELDS = ("exact_set_match", "precision", "recall", "f1", "completeness", "jaccard", "under_selection", "over_selection", "cardinality_error", "parse_failure")
RANKING_FIELDS = ("hit_at_1", "mrr_at_5", "recall_at_5", "ndcg_at_5", "parse_failure")


def _load_common() -> Any:
    path = Path(__file__).with_name("score_native_machine_selection_v1_5.py")
    spec = importlib.util.spec_from_file_location("sdb_frozen_selection_metrics_v1_5_for_deepseek", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen metrics: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V15 = _load_common()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def unique(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value or value in result:
            raise ValueError(f"invalid or duplicate {label} ID: {value!r}")
        result[value] = row
    return result


def validate_scope(track: str, manifests: dict[str, dict[str, Any]], statuses: dict[str, dict[str, Any]]) -> None:
    if track not in EXPECTED_ROWS:
        raise ValueError(f"unsupported track: {track}")
    expected = EXPECTED_ROWS[track]
    if len(manifests) != expected or len(statuses) != expected or set(manifests) != set(statuses):
        raise ValueError(f"{track} manifest/status identity or count mismatch; expected {expected}")
    if track == "native" and set(row.get("task_type") for row in manifests.values()) != set(TASKS):
        raise ValueError("Native scoring requires all six tasks")
    if any(row.get("provider") != PROVIDER or row.get("experiment_revision") != REVISION for row in statuses.values()):
        raise ValueError("status rows are not independent DeepSeek V2.2 results")
    for request_id, source in manifests.items():
        status = statuses[request_id]
        task = source.get("task_type")
        if track == "machine":
            expected_contract = "TOP5_RANKING_V1"
        elif task == "single_service_discovery":
            expected_contract = "TOP5_RANKING_V1"
        elif task == "single_api_recommendation":
            expected_contract = COMBINED
        elif isinstance(task, str) and task.startswith(("multi_", "composable_")):
            expected_contract = "SELECTED_SET_V1"
        else:
            raise ValueError(f"unregistered task in DeepSeek manifest: {request_id}")
        if status.get("track") != track or status.get("task_type") != task or status.get("output_contract") != expected_contract:
            raise ValueError(f"DeepSeek track/task/contract mismatch: {request_id}")
    unresolved = Counter(row.get("status") for row in statuses.values() if row.get("status") in {"infra_error", "api_error"})
    if unresolved:
        raise ValueError(f"unresolved infrastructure/API rows block scoring: {dict(unresolved)}")
    unknown = {row.get("status") for row in statuses.values()} - {"succeeded", "parse_failure"}
    if unknown:
        raise ValueError(f"unsupported status values: {sorted(str(value) for value in unknown)}")


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
    return json.loads(path.read_text(encoding="utf-8"))


def score_rows(manifest_rows: list[dict[str, Any]], status_rows: list[dict[str, Any]], artifact_root: Path, track: str, *, enforce_scope: bool = True) -> list[dict[str, Any]]:
    normalized = [{**row, "_id": row.get("benchmark_task_id", row.get("request_id"))} for row in manifest_rows]
    manifests = unique(normalized, "_id", "manifest")
    statuses = unique(status_rows, "request_id", "status")
    if enforce_scope:
        validate_scope(track, manifests, statuses)
    elif set(manifests) != set(statuses):
        raise ValueError("manifest/status IDs differ")
    scored: list[dict[str, Any]] = []
    for request_id in sorted(manifests):
        source = manifests[request_id]
        status = statuses[request_id]
        gold = V15._gold_sets(source)
        prediction = load_prediction(status, artifact_root)
        failed = status["status"] == "parse_failure"
        contract = status.get("output_contract")
        ranking = None
        selected = None
        if contract == V15.__dict__.get("TOP5_RANKING_V1", "TOP5_RANKING_V1"):
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
        scored.append({
            "request_id": request_id,
            "track": track,
            "task_type": source["task_type"],
            "prediction_target": source["prediction_target"],
            "provider": PROVIDER,
            "experiment_revision": REVISION,
            "contract": contract,
            "status": status["status"],
            "parse_status": status.get("parse_status"),
            "candidate_count": int(status["candidate_count"]),
            "gold_count": min(len(option) for option in gold),
            "exact_task_success": exact,
            "ranking_metrics": ranking,
            "set_metrics": selected,
            "parse_failure": float(failed),
        })
    return scored


def mean(rows: list[dict[str, Any]], getter) -> float:
    return sum(float(getter(row)) for row in rows) / len(rows) if rows else 0.0


def aggregate(scored: list[dict[str, Any]], track: str) -> dict[str, Any]:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        by_task[row["task_type"]].append(row)
    task_rows = [
        {"task_type": task, "n": len(rows), "exact_task_success": mean(rows, lambda row: row["exact_task_success"]), "parse_failure": mean(rows, lambda row: row["parse_failure"])}
        for task, rows in sorted(by_task.items())
    ]
    ranking_rows = [row for row in scored if row["ranking_metrics"] is not None]
    set_rows = [row for row in scored if row["set_metrics"] is not None]
    ranking = {"n": len(ranking_rows), **{field: mean(ranking_rows, lambda row, name=field: row["ranking_metrics"][name]) for field in RANKING_FIELDS}}
    selected = {"n": len(set_rows), **{field: mean(set_rows, lambda row, name=field: row["set_metrics"][name]) for field in SET_FIELDS}}
    single_api = by_task.get("single_api_recommendation", [])
    single_api_ranking = {"n": len(single_api), **{field: mean(single_api, lambda row, name=field: row["ranking_metrics"][name]) for field in RANKING_FIELDS}} if single_api else None
    single_api_set = {"n": len(single_api), **{field: mean(single_api, lambda row, name=field: row["set_metrics"][name]) for field in SET_FIELDS}} if single_api else None
    return {
        "track": track,
        "rows": len(scored),
        "task_rows": task_rows,
        "micro_exact_task_success": mean(scored, lambda row: row["exact_task_success"]),
        "macro_task_success": mean(task_rows, lambda row: row["exact_task_success"]),
        "parse_failure_rate": mean(scored, lambda row: row["parse_failure"]),
        "ranking": ranking,
        "set_selection": selected,
        "single_api_ranking": single_api_ranking,
        "single_api_set_selection": single_api_set,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(field for row in rows for field in row)) if rows else ["n"]
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
    args = parser.parse_args()
    scored = score_rows(read_jsonl(args.manifest), read_jsonl(args.request_status), args.artifact_root, args.track)
    summary = {"status": "PASS", "provider": PROVIDER, "experiment_revision": REVISION, "old_qwen_rows_reused": 0, **aggregate(scored, args.track)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "PER_REQUEST_SCORES.json").write_text(json.dumps(scored, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "SCORE_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    write_csv(args.output_dir / "BY_TASK.csv", summary["task_rows"])
    write_csv(args.output_dir / "RANKING.csv", [summary["ranking"]])
    write_csv(args.output_dir / "SET_SELECTION.csv", [summary["set_selection"]])
    if summary["single_api_ranking"] is not None:
        write_csv(args.output_dir / "SINGLE_API_RANKING.csv", [summary["single_api_ranking"]])
        write_csv(args.output_dir / "SINGLE_API_SET_SELECTION.csv", [summary["single_api_set_selection"]])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
