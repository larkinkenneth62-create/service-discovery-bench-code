from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

RANKING_METRICS = ("task_success", "hit_at_1", "mrr_at_5", "recall_at_5", "ndcg_at_5", "parse_failure")
SET_METRICS = (
    "task_success", "exact_set_match", "precision", "recall", "f1", "completeness", "jaccard",
    "under_selection", "over_selection", "cardinality_error", "parse_failure",
)
COMMON_METRICS = ("task_success", "parse_failure")
RANKING_ONLY_METRICS = ("hit_at_1", "mrr_at_5", "recall_at_5", "ndcg_at_5")
SET_ONLY_METRICS = (
    "exact_set_match", "precision", "recall", "f1", "completeness", "jaccard",
    "under_selection", "over_selection", "cardinality_error",
)
ALLOWED_TERMINAL_STATUS = {"succeeded", "parse_failure"}
BLOCKING_STATUS = {"infra_error", "api_error"}


def _gold_sets(row: dict[str, Any]) -> list[set[str]]:
    value = row.get("acceptable_gold_sets")
    if value is None:
        value = row.get("gold_candidate_ids", row.get("gold_ids", row.get("gold")))
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return [set(value)]
    if isinstance(value, list) and value and all(isinstance(item, list) for item in value):
        result = [set(item) for item in value if item and all(isinstance(candidate, str) for candidate in item)]
        if result:
            return result
    raise ValueError(f"missing or invalid frozen Gold sets for {row.get('benchmark_task_id', row.get('request_id'))}")


def _dcg(ranking: list[str], gold: set[str]) -> float:
    return sum((1.0 / math.log2(index + 2)) for index, item in enumerate(ranking[:5]) if item in gold)


def score_ranking(ranking: list[str] | None, gold_options: list[set[str]], parse_failure: bool = False) -> dict[str, float]:
    if parse_failure or ranking is None:
        return {name: float(name == "parse_failure") for name in RANKING_METRICS}
    best: dict[str, float] | None = None
    for gold in gold_options:
        positions = [index + 1 for index, item in enumerate(ranking[:5]) if item in gold]
        ideal = sum(1.0 / math.log2(index + 2) for index in range(min(5, len(gold))))
        hit = float(bool(ranking) and ranking[0] in gold)
        metrics = {
            "task_success": hit,
            "hit_at_1": hit,
            "mrr_at_5": 1.0 / min(positions) if positions else 0.0,
            "recall_at_5": len(set(ranking[:5]) & gold) / len(gold) if gold else 1.0,
            "ndcg_at_5": _dcg(ranking, gold) / ideal if ideal else 1.0,
            "parse_failure": 0.0,
        }
        if best is None or (metrics["ndcg_at_5"], metrics["recall_at_5"], metrics["mrr_at_5"]) > (
            best["ndcg_at_5"], best["recall_at_5"], best["mrr_at_5"]
        ):
            best = metrics
    assert best is not None
    return best


def score_selected_set(selected: list[str] | None, gold_options: list[set[str]], parse_failure: bool = False) -> dict[str, float]:
    if parse_failure or selected is None:
        return {name: float(name == "parse_failure") for name in SET_METRICS}
    predicted = set(selected)
    best: dict[str, float] | None = None
    for gold in gold_options:
        intersection = predicted & gold
        union = predicted | gold
        precision = len(intersection) / len(predicted) if predicted else float(not gold)
        recall = len(intersection) / len(gold) if gold else float(not predicted)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        exact = float(predicted == gold)
        metrics = {
            "task_success": exact,
            "exact_set_match": exact,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "completeness": float(gold <= predicted),
            "jaccard": len(intersection) / len(union) if union else 1.0,
            "under_selection": float(bool(gold - predicted)),
            "over_selection": float(bool(predicted - gold)),
            "cardinality_error": float(abs(len(predicted) - len(gold))),
            "parse_failure": 0.0,
        }
        if best is None or (metrics["f1"], metrics["jaccard"], -metrics["cardinality_error"]) > (
            best["f1"], best["jaccard"], -best["cardinality_error"]
        ):
            best = metrics
    assert best is not None
    return best


def _bucket(value: int, breaks: tuple[int, ...]) -> str:
    lower = 0
    for upper in breaks:
        if value <= upper:
            return f"{lower}-{upper}"
        lower = upper + 1
    return f"{lower}+"


def _unique_by_id(rows: Iterable[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} row lacks a non-empty {field}")
        if value in result:
            raise ValueError(f"duplicate {label} ID: {value}")
        result[value] = row
    return result


def score_rows(manifest_rows: list[dict[str, Any]], status_rows: list[dict[str, Any]], artifact_root: Path) -> list[dict[str, Any]]:
    manifests = _unique_by_id(
        ({**row, "_resolved_request_id": row.get("benchmark_task_id", row.get("request_id"))} for row in manifest_rows),
        "_resolved_request_id",
        "manifest",
    )
    statuses = _unique_by_id(status_rows, "request_id", "status")
    if set(manifests) != set(statuses):
        missing = sorted(set(manifests) - set(statuses))[:10]
        extra = sorted(set(statuses) - set(manifests))[:10]
        raise ValueError(f"manifest/status task sets differ; missing={missing}, extra={extra}")
    blocking = Counter(row.get("status") for row in statuses.values() if row.get("status") in BLOCKING_STATUS)
    if blocking:
        raise ValueError(f"blocking infrastructure/API statuses must be resolved before scoring: {dict(blocking)}")
    unknown = sorted({str(row.get("status")) for row in statuses.values()} - ALLOWED_TERMINAL_STATUS)
    if unknown:
        raise ValueError(f"unsupported terminal statuses: {unknown}")

    scored: list[dict[str, Any]] = []
    for request_id in sorted(manifests):
        source = manifests[request_id]
        status = statuses[request_id]
        gold = _gold_sets(source)
        prediction = None
        if status["status"] == "succeeded":
            prediction_path = status.get("parsed_prediction_path")
            if not isinstance(prediction_path, str):
                raise ValueError(f"successful row lacks parsed prediction path: {request_id}")
            path = (artifact_root / prediction_path).resolve()
            root = artifact_root.resolve()
            if path != root and root not in path.parents:
                raise ValueError(f"parsed prediction path escapes artifact root: {request_id}")
            prediction = json.loads(path.read_text(encoding="utf-8"))
        parse_failure = status["status"] == "parse_failure"
        contract = status.get("output_contract")
        if contract == "TOP5_RANKING_V1":
            metrics = score_ranking(None if prediction is None else prediction["ranked_candidate_ids"], gold, parse_failure)
        elif contract == "SELECTED_SET_V1":
            metrics = score_selected_set(None if prediction is None else prediction["selected_candidate_ids"], gold, parse_failure)
        else:
            raise ValueError(f"unknown output contract for {request_id}: {contract}")
        task_type = source["task_type"]
        candidate_count = int(status["candidate_count"])
        minimum_gold_count = min(len(option) for option in gold)
        scored.append({
            "request_id": request_id,
            "task_type": task_type,
            "prediction_target": source["prediction_target"],
            "task_family": task_type.split("_", 1)[0],
            "contract": contract,
            "status": status["status"],
            "candidate_count": candidate_count,
            "candidate_count_bucket": _bucket(candidate_count, (5, 10, 20, 50, 100, 200)),
            "gold_count": minimum_gold_count,
            "gold_count_bucket": _bucket(minimum_gold_count, (1, 2, 3, 5, 10)),
            "parse_status": status.get("parse_status"),
            "metrics": metrics,
        })
    return scored


def _mean(members: list[dict[str, Any]], metric: str) -> float:
    return sum(float(member["metrics"][metric]) for member in members) / len(members)


def aggregate(rows: list[dict[str, Any]], group_fields: list[str], metrics: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[field] for field in group_fields)].append(row)
    output: list[dict[str, Any]] = []
    for key, members in sorted(grouped.items(), key=lambda item: tuple(str(value) for value in item[0])):
        summary = {field: value for field, value in zip(group_fields, key, strict=True)}
        summary["n"] = len(members)
        summary["raw_success_count"] = sum(member["status"] == "succeeded" for member in members)
        summary.update({metric: _mean(members, metric) for metric in metrics})
        output.append(summary)
    return output


def _macro_from_task_rows(task_rows: list[dict[str, Any]], metrics: tuple[str, ...], label: str) -> list[dict[str, Any]]:
    if not task_rows:
        return []
    result: dict[str, Any] = {"aggregation": label, "task_count": len(task_rows), "n": sum(int(row["n"]) for row in task_rows)}
    for metric in metrics:
        result[metric] = sum(float(row[metric]) for row in task_rows) / len(task_rows)
    return [result]


def build_tables(scored: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    ranking = [row for row in scored if row["contract"] == "TOP5_RANKING_V1"]
    selected = [row for row in scored if row["contract"] == "SELECTED_SET_V1"]
    by_task_common = aggregate(scored, ["task_type"], COMMON_METRICS)
    by_task_ranking = aggregate(ranking, ["task_type"], RANKING_METRICS) if ranking else []
    by_task_selected = aggregate(selected, ["task_type"], SET_METRICS) if selected else []
    return {
        "by_task_common": by_task_common,
        "by_task_ranking": by_task_ranking,
        "by_task_set_selection": by_task_selected,
        "micro_overall": aggregate(scored, [], COMMON_METRICS),
        "macro_6": _macro_from_task_rows(by_task_common, COMMON_METRICS, "Macro-6 Task Success"),
        "single_macro": _macro_from_task_rows(by_task_ranking, RANKING_METRICS, "Single/Machine Ranking Macro"),
        "set_selection_macro": _macro_from_task_rows(by_task_selected, SET_METRICS, "Multi/Composable Set-Selection Macro"),
        "service_vs_api": aggregate(scored, ["prediction_target"], COMMON_METRICS),
        "single_multi_composable": aggregate(scored, ["task_family"], COMMON_METRICS),
        "by_contract": aggregate(scored, ["contract"], COMMON_METRICS),
        "by_candidate_count": aggregate(scored, ["candidate_count_bucket"], COMMON_METRICS),
        "by_gold_count": aggregate(scored, ["gold_count_bucket"], COMMON_METRICS),
        "by_parse_status": aggregate(scored, ["parse_status"], COMMON_METRICS),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(field for row in rows for field in row)) if rows else ["n"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Native/Machine Qwen Selection V1.5 R2")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--request-status", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    statuses = [json.loads(line) for line in args.request_status.read_text(encoding="utf-8").splitlines() if line.strip()]
    scored = score_rows(manifest, statuses, args.artifact_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "per_request_scores.json").write_text(
        json.dumps(scored, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    tables = build_tables(scored)
    for name, table in tables.items():
        _write_csv(args.output_dir / f"{name}.csv", table)
    summary = {
        "status": "PASS",
        "rows": len(scored),
        "blocking_status_rows": 0,
        "task_id_exact_match": True,
        "tables": {name: len(table) for name, table in tables.items()},
        "aggregation_contract": {
            "macro_6": "task_success + parse_failure only",
            "single_macro": list(RANKING_METRICS),
            "set_selection_macro": list(SET_METRICS),
        },
    }
    (args.output_dir / "SCORE_SUMMARY.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
