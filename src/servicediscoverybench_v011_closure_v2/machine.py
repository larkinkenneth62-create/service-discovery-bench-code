from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from servicediscoverybench.baselines import bm25_ranking, local_embedding_ranking, random_ranking
from servicediscoverybench.machine_challenge_v12 import (
    build_machine_challenge, select_task_balanced_queries, stream_evidence_for_queries,
)

from .common import text, write_csv, write_json
from .evaluation import analytical_random_mrr, bootstrap_ci, document_text, mean_numeric, reference_metrics


def build_and_evaluate_machine(
    test_rows: Sequence[Mapping[str, object]],
    catalog: Mapping[str, Mapping[str, object]],
    evidence_path: Path,
    native_per_query: Mapping[str, Mapping[str, Mapping[str, object]]],
    output: Path,
) -> tuple[list[dict[str, object]], dict[str, Any]]:
    output.mkdir(parents=True, exist_ok=True)
    main_rows, reserve_rows, selection = select_task_balanced_queries(test_rows)
    query_ids = {text(row.get("benchmark_task_id")) for row in main_rows + reserve_rows}
    evidence = stream_evidence_for_queries(evidence_path, query_ids)
    challenge = build_machine_challenge(main_rows, reserve_rows, evidence, catalog)
    write_csv(output / "TASKS.csv", challenge.tasks)
    write_csv(output / "CANDIDATES.csv", challenge.candidates)
    write_csv(output / "ATTRITION_LEDGER.csv", challenge.attrition)
    write_csv(output / "CANDIDATE_SOURCE_DISTRIBUTION.csv", challenge.source_distribution)
    write_json(output / "QUERY_SELECTION_STATUS.json", selection)
    write_json(output / "STATUS.json", challenge.status)
    if not challenge.status.get("machine_challenge_ready"):
        raise RuntimeError(f"Machine Challenge not ready: {challenge.status}")

    baseline_dir = output / "BASELINES"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, Any] = {}
    matched: list[dict[str, object]] = []
    gold_rank_rows: list[dict[str, object]] = []
    for method in ("bm25", "local_hashing"):
        metrics_rows: list[dict[str, object]] = []
        predictions: list[dict[str, object]] = []
        for row in challenge.tasks:
            task_id = text(row.get("benchmark_task_id"))
            docs_list = json.loads(text(row.get("candidate_documents_json")) or "[]")
            docs = {text(doc.get("candidate_id")): doc for doc in docs_list}
            ids = [text(doc.get("candidate_id")) for doc in docs_list]
            doc_text = {candidate_id: document_text(doc) for candidate_id, doc in docs.items()}
            query = text(row.get("query_text"))
            ranking = bm25_ranking(query, ids, doc_text) if method == "bm25" else local_embedding_ranking(query, ids, doc_text)
            gold_sets = json.loads(text(row.get("reference_gold_sets_json")) or "[]")
            metrics = reference_metrics(ranking, gold_sets, ks=(1, 3, 5, 10))
            metric_row = {
                "benchmark_task_id": task_id, "task_type": text(row.get("task_type")),
                "source_dataset": text(row.get("source_dataset")), **metrics,
            }
            metrics_rows.append(metric_row)
            predictions.append({"benchmark_task_id": task_id, "ranking_json": json.dumps(ranking, ensure_ascii=False)})
            gold_union = set(item for values in gold_sets for item in values)
            ranks = [ranking.index(item) + 1 for item in gold_union if item in ranking]
            gold_rank_rows.append({"method": method, "benchmark_task_id": task_id, "minimum_gold_rank": min(ranks) if ranks else "NOT_RETRIEVED", "gold_count": len(gold_union)})
            native = native_per_query.get(method, {}).get(task_id)
            if native:
                matched.append({
                    "method": method, "benchmark_task_id": task_id, "task_type": text(row.get("task_type")),
                    "native_mrr": native.get("mrr"), "challenge_mrr": metrics["mrr"],
                    "mrr_delta": metrics["mrr"] - float(native.get("mrr", 0.0)),
                    "native_recall@5": native.get("recall@5"), "challenge_recall@5": metrics["recall@5"],
                    "recall@5_delta": metrics["recall@5"] - float(native.get("recall@5", 0.0)),
                })
        write_csv(baseline_dir / f"{method}_PREDICTIONS.csv", predictions)
        write_csv(baseline_dir / f"{method}_METRICS_BY_QUERY.csv", metrics_rows)
        by_task: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for metric in metrics_rows:
            by_task[text(metric.get("task_type"))].append(metric)
        task_rows = [{"task_type": task, "n": len(values), **mean_numeric(values)} for task, values in sorted(by_task.items())]
        write_csv(baseline_dir / f"{method}_RESULTS_BY_TASK.csv", task_rows)
        summaries[method] = {
            "overall": mean_numeric(metrics_rows),
            "six_task_macro": {key: sum(float(row[key]) for row in task_rows) / len(task_rows) for key in mean_numeric(metrics_rows)},
            "bootstrap_95_ci": bootstrap_ci(metrics_rows, ("mrr", "recall@1", "recall@3", "recall@5", "completeness@3", "completeness@5", "ndcg@5")),
        }

    random_seed_rows: list[dict[str, object]] = []
    random_query: dict[str, list[dict[str, object]]] = defaultdict(list)
    analytical: list[float] = []
    for row in challenge.tasks:
        gold_sets = json.loads(text(row.get("reference_gold_sets_json")) or "[]")
        relevant = len(set(item for values in gold_sets for item in values))
        analytical.append(analytical_random_mrr(int(row["candidate_count"]), relevant))
    for seed in range(20):
        metrics_rows = []
        for row in challenge.tasks:
            task_id = text(row.get("benchmark_task_id"))
            ids = json.loads(text(row.get("candidate_ids_json")) or "[]")
            ranking = random_ranking(ids, seed=seed, task_id=task_id)
            metrics = reference_metrics(ranking, json.loads(text(row.get("reference_gold_sets_json")) or "[]"), ks=(1, 3, 5, 10))
            metrics_rows.append(metrics)
            random_query[task_id].append(metrics)
        random_seed_rows.append({"seed": seed, **mean_numeric(metrics_rows)})
    write_csv(baseline_dir / "random_20_SEED_RESULTS.csv", random_seed_rows)
    observed = sum(float(row["mrr"]) for row in random_seed_rows) / 20
    expected = sum(analytical) / len(analytical)
    if abs(observed - expected) > 0.01:
        raise RuntimeError("Machine random analytical MRR alignment failed")
    random_query_means = [mean_numeric(values) for values in random_query.values()]
    summaries["random"] = {
        "seed_count": 20, "observed_mrr": observed, "analytical_expected_mrr": expected,
        "observed_analytical_mrr_abs_diff": abs(observed - expected),
        "bootstrap_95_ci": bootstrap_ci(random_query_means, ("mrr", "recall@1", "recall@3", "recall@5", "completeness@3", "completeness@5", "ndcg@5")),
    }
    write_csv(baseline_dir / "MATCHED_NATIVE_CHALLENGE_DELTA.csv", matched)
    write_csv(baseline_dir / "GOLD_RANK_DISTRIBUTION.csv", gold_rank_rows)
    report = {
        "status": "READY", "machine_challenge_rows": len(challenge.tasks),
        "unjudged_candidates_are_not_formal_negatives": True,
        "headline_precision_f1_esm_computed": False, "methods": summaries,
    }
    write_json(baseline_dir / "MACHINE_CHALLENGE_BASELINE_REPORT.json", report)
    return challenge.tasks, {**challenge.status, **report}
