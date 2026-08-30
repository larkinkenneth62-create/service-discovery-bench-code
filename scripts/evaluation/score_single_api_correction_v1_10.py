from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CORRECTION_CONTRACT = "RANKING_AND_SELECTED_SET_V1_10"
TASKS = (
    "single_service_discovery",
    "single_api_recommendation",
    "multi_service_discovery",
    "multi_api_recommendation",
    "composable_service_discovery",
    "composable_api_recommendation",
)


def _load_v15() -> Any:
    path = Path(__file__).with_name("score_native_machine_selection_v1_5.py")
    spec = importlib.util.spec_from_file_location("sdb_selection_scoring_v1_5", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen V1.5 scorer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V15 = _load_v15()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def unique(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value or value in result:
            raise ValueError(f"invalid or duplicate {label} {field}: {value!r}")
        result[value] = row
    return result


def mean(rows: list[dict[str, Any]], field: str) -> float:
    return sum(float(row[field]) for row in rows) / len(rows) if rows else 0.0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(field for row in rows for field in row)) if rows else ["n"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def load_prediction(status: dict[str, Any], artifact_root: Path) -> dict[str, Any] | None:
    if status["status"] == "parse_failure":
        return None
    if status["status"] != "succeeded":
        raise ValueError(f"unresolved status for {status.get('request_id')}: {status.get('status')}")
    relative = status.get("parsed_prediction_path")
    if not isinstance(relative, str):
        raise ValueError(f"successful row lacks prediction path: {status.get('request_id')}")
    root = artifact_root.resolve()
    path = (artifact_root / relative).resolve()
    if path != root and root not in path.parents:
        raise ValueError("parsed prediction path escapes artifact root")
    return json.loads(path.read_text(encoding="utf-8"))


def score_correction(
    manifest_rows: list[dict[str, Any]], status_rows: list[dict[str, Any]], artifact_root: Path
) -> list[dict[str, Any]]:
    manifests = unique(manifest_rows, "benchmark_task_id", "manifest")
    statuses = unique(status_rows, "request_id", "status")
    if set(manifests) != set(statuses):
        raise ValueError("correction manifest/status IDs do not match exactly")
    if len(manifests) != 3043:
        raise ValueError(f"correction row count mismatch: {len(manifests)} != 3043")
    scored: list[dict[str, Any]] = []
    for request_id in sorted(manifests):
        source = manifests[request_id]
        status = statuses[request_id]
        if source.get("task_type") != "single_api_recommendation":
            raise ValueError(f"non-Single-API correction row: {request_id}")
        if status.get("output_contract") != CORRECTION_CONTRACT:
            raise ValueError(f"wrong correction contract: {request_id}")
        gold = V15._gold_sets(source)
        prediction = load_prediction(status, artifact_root)
        failed = status["status"] == "parse_failure"
        ranking = V15.score_ranking(
            None if prediction is None else prediction["ranked_candidate_ids"], gold, failed
        )
        selected = V15.score_selected_set(
            None if prediction is None else prediction["selected_candidate_ids"], gold, failed
        )
        scored.append({
            "request_id": request_id,
            "task_type": "single_api_recommendation",
            "prediction_target": "api",
            "source": "V1.10_TARGETED_CORRECTION",
            "candidate_count": int(status["candidate_count"]),
            "gold_count": min(len(option) for option in gold),
            "status": status["status"],
            "parse_status": status.get("parse_status"),
            "exact_task_success": selected["exact_set_match"],
            "hit_at_1": ranking["hit_at_1"],
            "mrr_at_5": ranking["mrr_at_5"],
            "recall_at_5": ranking["recall_at_5"],
            "ndcg_at_5": ranking["ndcg_at_5"],
            "exact_set_match": selected["exact_set_match"],
            "precision": selected["precision"],
            "recall": selected["recall"],
            "f1": selected["f1"],
            "completeness": selected["completeness"],
            "jaccard": selected["jaccard"],
            "under_selection": selected["under_selection"],
            "over_selection": selected["over_selection"],
            "cardinality_error": selected["cardinality_error"],
            "parse_failure": selected["parse_failure"],
        })
    return scored


def retained_old_rows(old_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    retained: list[dict[str, Any]] = []
    for row in old_rows:
        if row["task_type"] == "single_api_recommendation":
            continue
        metrics = row["metrics"]
        exact = metrics["hit_at_1"] if row["task_type"] == "single_service_discovery" else metrics["exact_set_match"]
        retained.append({
            "request_id": row["request_id"],
            "task_type": row["task_type"],
            "prediction_target": row["prediction_target"],
            "source": "V1.9_RETAINED_UNCHANGED",
            "candidate_count": row["candidate_count"],
            "gold_count": row["gold_count"],
            "status": row["status"],
            "parse_status": row["parse_status"],
            "exact_task_success": exact,
            "hit_at_1": metrics.get("hit_at_1"),
            "mrr_at_5": metrics.get("mrr_at_5"),
            "recall_at_5": metrics.get("recall_at_5"),
            "ndcg_at_5": metrics.get("ndcg_at_5"),
            "exact_set_match": metrics.get("exact_set_match"),
            "precision": metrics.get("precision"),
            "recall": metrics.get("recall"),
            "f1": metrics.get("f1"),
            "completeness": metrics.get("completeness"),
            "jaccard": metrics.get("jaccard"),
            "under_selection": metrics.get("under_selection"),
            "over_selection": metrics.get("over_selection"),
            "cardinality_error": metrics.get("cardinality_error"),
            "parse_failure": metrics["parse_failure"],
        })
    if len(retained) != 1755:
        raise ValueError(f"retained V1.9 row count mismatch: {len(retained)} != 1755")
    return retained


def aggregate(corrected: list[dict[str, Any]], old_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in corrected:
        by_task[row["task_type"]].append(row)
    if tuple(sorted(by_task)) != tuple(sorted(TASKS)):
        raise ValueError("corrected six-task coverage mismatch")
    task_rows: list[dict[str, Any]] = []
    for task in TASKS:
        rows = by_task[task]
        task_rows.append({
            "task_type": task,
            "n": len(rows),
            "source": rows[0]["source"],
            "exact_task_success": mean(rows, "exact_task_success"),
            "parse_failure": mean(rows, "parse_failure"),
        })
    single_api = by_task["single_api_recommendation"]
    ranking_fields = ("hit_at_1", "mrr_at_5", "recall_at_5", "ndcg_at_5", "parse_failure")
    set_fields = ("exact_set_match", "precision", "recall", "f1", "completeness", "jaccard", "under_selection", "over_selection", "cardinality_error", "parse_failure")
    single_api_ranking = {"n": len(single_api), **{field: mean(single_api, field) for field in ranking_fields}}
    single_api_set = {"n": len(single_api), **{field: mean(single_api, field) for field in set_fields}}
    set_tasks = [task for task in TASKS if task != "single_service_discovery"]
    set_macro = {
        "task_count": len(set_tasks),
        **{
            field: sum(mean(by_task[task], field) for task in set_tasks) / len(set_tasks)
            for field in set_fields
        },
    }
    old_single_api = [row for row in old_rows if row["task_type"] == "single_api_recommendation"]
    old_task_success = [sum(float(r["metrics"]["task_success"]) for r in old_rows if r["task_type"] == task) / len([r for r in old_rows if r["task_type"] == task]) for task in TASKS]
    old_micro = sum(float(row["metrics"]["task_success"]) for row in old_rows) / len(old_rows)
    corrected_macro = sum(row["exact_task_success"] for row in task_rows) / len(task_rows)
    corrected_micro = mean(corrected, "exact_task_success")
    comparison = [{
        "old_contract": "V1.9_HISTORICAL_MIXED_CONTRACT_DIAGNOSTIC",
        "new_contract": "V1.10_CORRECTED_EXACT_TASK_CONTRACT",
        "old_single_api_hit_at_1": sum(float(r["metrics"]["hit_at_1"]) for r in old_single_api) / len(old_single_api),
        "new_single_api_hit_at_1": single_api_ranking["hit_at_1"],
        "new_single_api_exact_set_match": single_api_set["exact_set_match"],
        "old_micro_mixed_contract": old_micro,
        "new_micro_exact_task_success": corrected_micro,
        "old_macro_6_mixed_contract": sum(old_task_success) / len(old_task_success),
        "new_macro_6_exact_task_success": corrected_macro,
        "old_single_api_parse_failure": sum(float(r["metrics"]["parse_failure"]) for r in old_single_api) / len(old_single_api),
        "new_single_api_parse_failure": single_api_set["parse_failure"],
    }]
    return {
        "task_rows": task_rows,
        "single_api_ranking": single_api_ranking,
        "single_api_set": single_api_set,
        "set_selection_macro": set_macro,
        "macro_6_exact_task_success": corrected_macro,
        "micro_exact_task_success": corrected_micro,
        "comparison": comparison,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Single API Correction V1.10 and corrected Native six-task results")
    parser.add_argument("--old-per-request", type=Path, required=True)
    parser.add_argument("--correction-manifest", type=Path, required=True)
    parser.add_argument("--correction-status", type=Path, required=True)
    parser.add_argument("--correction-artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    old_rows = json.loads(args.old_per_request.read_text(encoding="utf-8"))
    correction = score_correction(
        read_jsonl(args.correction_manifest), read_jsonl(args.correction_status), args.correction_artifact_root
    )
    retained = retained_old_rows(old_rows)
    corrected = sorted(retained + correction, key=lambda row: row["request_id"])
    if len(corrected) != 4798 or len({row["request_id"] for row in corrected}) != 4798:
        raise ValueError("corrected Native row identity/count mismatch")
    tables = aggregate(corrected, old_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "CORRECTED_PER_REQUEST_SCORES.json").write_text(json.dumps(corrected, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    write_csv(args.output_dir / "CORRECTED_SIX_TASK_RESULTS.csv", tables["task_rows"])
    write_csv(args.output_dir / "CORRECTED_SINGLE_API_RANKING.csv", [tables["single_api_ranking"]])
    write_csv(args.output_dir / "CORRECTED_SINGLE_API_SET_SELECTION.csv", [tables["single_api_set"]])
    write_csv(args.output_dir / "CORRECTED_SET_SELECTION_MACRO.csv", [tables["set_selection_macro"]])
    write_csv(args.output_dir / "OLD_MIXED_CONTRACT_VS_CORRECTED_EXACT_CONTRACT.csv", tables["comparison"])
    summary = {
        "status": "PASS",
        "rows": len(corrected),
        "correction_rows": len(correction),
        "retained_v1_9_rows": len(retained),
        "old_rows_reused_as_correction_predictions": 0,
        "status_counts": dict(Counter(row["status"] for row in correction)),
        "single_api_ranking": tables["single_api_ranking"],
        "single_api_set": tables["single_api_set"],
        "set_selection_macro": tables["set_selection_macro"],
        "macro_6_exact_task_success": tables["macro_6_exact_task_success"],
        "micro_exact_task_success": tables["micro_exact_task_success"],
        "historical_label": "V1.9_HISTORICAL_MIXED_CONTRACT_DIAGNOSTIC",
        "dataset_changed": False,
        "retriever_changed": False,
        "benchmark_route_changed": False,
        "test_gold_cardinality_used": False,
    }
    (args.output_dir / "CORRECTED_SCORE_SUMMARY.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
