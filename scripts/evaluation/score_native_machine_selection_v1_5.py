from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


RANKING_METRICS = ("hit_at_1", "mrr_at_5", "recall_at_5", "ndcg_at_5", "parse_failure")
SET_METRICS = (
    "exact_set_match", "precision", "recall", "f1", "completeness", "jaccard",
    "under_selection", "over_selection", "cardinality_error", "parse_failure",
)


def _gold_sets(row: dict[str, Any]) -> list[set[str]]:
    value = row.get("acceptable_gold_sets")
    if value is None:
        value = row.get("gold_candidate_ids", row.get("gold_ids", row.get("gold")))
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [set(value)]
    if isinstance(value, list) and all(isinstance(item, list) for item in value):
        result = [set(item) for item in value if all(isinstance(candidate, str) for candidate in item)]
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
        metrics = {
            "hit_at_1": float(bool(ranking) and ranking[0] in gold),
            "mrr_at_5": 1.0 / min(positions) if positions else 0.0,
            "recall_at_5": len(set(ranking[:5]) & gold) / len(gold) if gold else 1.0,
            "ndcg_at_5": _dcg(ranking, gold) / ideal if ideal else 1.0,
            "parse_failure": 0.0,
        }
        if best is None or (metrics["ndcg_at_5"], metrics["recall_at_5"], metrics["mrr_at_5"]) > (best["ndcg_at_5"], best["recall_at_5"], best["mrr_at_5"]):
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
        metrics = {
            "exact_set_match": float(predicted == gold),
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
        if best is None or (metrics["f1"], metrics["jaccard"], -metrics["cardinality_error"]) > (best["f1"], best["jaccard"], -best["cardinality_error"]):
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


def aggregate(rows: list[dict[str, Any]], group_fields: list[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[field] for field in group_fields)].append(row)
    output: list[dict[str, Any]] = []
    for key, members in sorted(grouped.items(), key=lambda item: tuple(str(value) for value in item[0])):
        metric_names = sorted({name for member in members for name in member["metrics"]})
        summary = {field: value for field, value in zip(group_fields, key, strict=True)}
        summary["n"] = len(members)
        summary["raw_success_count"] = sum(member["status"] == "succeeded" for member in members)
        for name in metric_names:
            values = [member["metrics"].get(name, 0.0) for member in members]
            summary[name] = sum(values) / len(values)
        output.append(summary)
    return output


def score_rows(manifest_rows: list[dict[str, Any]], status_rows: list[dict[str, Any]], artifact_root: Path) -> list[dict[str, Any]]:
    statuses = {row["request_id"]: row for row in status_rows}
    scored: list[dict[str, Any]] = []
    for source in manifest_rows:
        request_id = source.get("benchmark_task_id", source.get("request_id"))
        status = statuses.get(request_id)
        if status is None:
            raise ValueError(f"missing terminal status for {request_id}")
        gold = _gold_sets(source)
        prediction = None
        if status.get("status") == "succeeded":
            prediction_path = status.get("parsed_prediction_path")
            if not isinstance(prediction_path, str):
                raise ValueError(f"successful row lacks parsed prediction path: {request_id}")
            prediction = json.loads((artifact_root / prediction_path).read_text(encoding="utf-8"))
        parse_failure = status.get("status") != "succeeded"
        contract = status.get("output_contract")
        if contract == "TOP5_RANKING_V1":
            metrics = score_ranking(None if prediction is None else prediction["ranked_candidate_ids"], gold, parse_failure)
        elif contract == "SELECTED_SET_V1":
            metrics = score_selected_set(None if prediction is None else prediction["selected_candidate_ids"], gold, parse_failure)
        else:
            raise ValueError(f"unknown output contract for {request_id}")
        task_type = source["task_type"]
        target = source["prediction_target"]
        candidate_count = int(status["candidate_count"])
        scored.append({
            "request_id": request_id,
            "task_type": task_type,
            "prediction_target": target,
            "task_family": task_type.split("_", 1)[0],
            "contract": contract,
            "status": status["status"],
            "candidate_count": candidate_count,
            "candidate_count_bucket": _bucket(candidate_count, (5, 10, 20, 50, 100, 200)),
            "gold_count": min(len(option) for option in gold),
            "gold_count_bucket": _bucket(min(len(option) for option in gold), (1, 2, 3, 5, 10)),
            "parse_status": status.get("parse_status"),
            "metrics": metrics,
        })
    return scored


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row}) if rows else ["n"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Native/Machine Qwen Selection V1.5")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--request-status", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    statuses = [json.loads(line) for line in args.request_status.read_text(encoding="utf-8").splitlines() if line.strip()]
    scored = score_rows(manifest, statuses, args.artifact_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "per_request_scores.json").write_text(json.dumps(scored, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tables = {
        "by_task": aggregate(scored, ["task_type"]),
        "micro_overall": aggregate(scored, []),
        "service_vs_api": aggregate(scored, ["prediction_target"]),
        "single_multi_composable": aggregate(scored, ["task_family"]),
        "by_candidate_count": aggregate(scored, ["candidate_count_bucket"]),
        "by_gold_count": aggregate(scored, ["gold_count_bucket"]),
        "by_parse_status": aggregate(scored, ["parse_status"]),
    }
    by_task = tables["by_task"]
    metric_names = sorted({name for row in by_task for name in row if name not in {"task_type", "n", "raw_success_count"}})
    macro = {"aggregation": "Macro-6", "n": sum(row["n"] for row in by_task), "task_count": len(by_task)}
    for name in metric_names:
        macro[name] = sum(row.get(name, 0.0) for row in by_task) / len(by_task) if by_task else 0.0
    tables["macro_6"] = [macro]
    for name, table in tables.items():
        _write_csv(args.output_dir / f"{name}.csv", table)
    summary = {"status": "PASS", "rows": len(scored), "tables": {name: len(table) for name, table in tables.items()}}
    (args.output_dir / "SCORE_SUMMARY.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
