from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
import math
import random
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from servicediscoverybench.baselines import bm25_ranking, local_embedding_ranking, random_ranking
from servicediscoverybench.cardinality_policy import acceptable_gold_sets, oracle_selected_set
from servicediscoverybench.joint_split_optimizer_v3 import TASKS, candidate_bucket
from servicediscoverybench.metrics import evaluate_acceptable_gold_sets

from .common import json_list, text, write_csv, write_json


def candidate_ids(row: Mapping[str, object]) -> list[str]:
    field = "candidate_services_json" if text(row.get("prediction_target")) == "service" else "candidate_apis_json"
    ids = json_list(row.get(field))
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"invalid Native candidates for {text(row.get('benchmark_task_id'))}")
    return ids


def document_text(document: Mapping[str, object]) -> str:
    return " ".join(text(document.get(field)) for field in (
        "canonical_name", "description", "provider_or_host", "endpoint_summary", "api_schema_summary"
    )).strip()


def reference_metrics(ranking: Sequence[str], alternatives: Sequence[Sequence[str]], ks=(1, 3, 5, 10)) -> dict[str, float]:
    alternatives = [list(dict.fromkeys(values)) for values in alternatives if values]
    if not alternatives:
        raise ValueError("empty reference Gold")
    per_alt: list[dict[str, float]] = []
    for gold in alternatives:
        gold_set = set(gold)
        first = next((index for index, item in enumerate(dict.fromkeys(ranking), 1) if item in gold_set), None)
        row: dict[str, float] = {"mrr": 0.0 if first is None else 1.0 / first}
        for k in ks:
            top = list(dict.fromkeys(ranking))[:k]
            hits = len(set(top) & gold_set)
            row[f"recall@{k}"] = hits / len(gold_set)
            row[f"hit@{k}"] = float(hits > 0)
            row[f"completeness@{k}"] = float(gold_set.issubset(set(top)))
            dcg = sum((1.0 if item in gold_set else 0.0) / math.log2(index + 2) for index, item in enumerate(top))
            ideal = sum(1.0 / math.log2(index + 2) for index in range(min(k, len(gold_set))))
            row[f"ndcg@{k}"] = dcg / ideal if ideal else 0.0
        per_alt.append(row)
    return {key: max(row[key] for row in per_alt) for key in per_alt[0]}


def mean_numeric(rows: Sequence[Mapping[str, object]]) -> dict[str, float]:
    if not rows:
        return {}
    keys = sorted(set.intersection(*(set(key for key, value in row.items() if isinstance(value, (int, float)) and not isinstance(value, bool)) for row in rows)))
    return {key: sum(float(row[key]) for row in rows) / len(rows) for key in keys}


def bootstrap_ci(rows: Sequence[Mapping[str, object]], keys: Sequence[str], *, seed: int = 20260806, repetitions: int = 500) -> dict[str, dict[str, float]]:
    if not rows:
        return {}
    rng = random.Random(seed)
    n = len(rows)
    samples = {key: [] for key in keys}
    for _ in range(repetitions):
        indices = [rng.randrange(n) for _ in range(n)]
        for key in keys:
            samples[key].append(sum(float(rows[index][key]) for index in indices) / n)
    result: dict[str, dict[str, float]] = {}
    for key, values in samples.items():
        values.sort()
        result[key] = {
            "mean": sum(float(row[key]) for row in rows) / n,
            "bootstrap95_low": values[int(0.025 * (repetitions - 1))],
            "bootstrap95_high": values[int(0.975 * (repetitions - 1))],
            "repetitions": repetitions,
        }
    return result


def analytical_random_mrr(n: int, m: int) -> float:
    if n <= 0 or m <= 0 or m > n:
        raise ValueError(f"invalid random MRR cardinalities n={n}, m={m}")
    denominator = math.comb(n, m)
    return sum((math.comb(n - rank, m - 1) / denominator) / rank for rank in range(1, n - m + 2))


@dataclass(frozen=True)
class CardinalityRule:
    task_type: str
    candidate_count_bucket: str
    selected_k: int
    dev_rows: int
    dev_mean_f1: float | None
    rule: str


@dataclass
class CardinalityPolicy:
    rules: dict[tuple[str, str], CardinalityRule]
    task_fallback: dict[str, int]
    global_fallback: int
    fit_split: str = "dev"
    uses_test_gold: bool = False

    def predict_k(self, row: Mapping[str, object], count: int) -> int:
        task = text(row.get("task_type"))
        if task == "single_service_discovery":
            return 1
        rule = self.rules.get((task, candidate_bucket(count)))
        value = rule.selected_k if rule else self.task_fallback.get(task, self.global_fallback)
        return max(1, min(int(value), count))

    def to_dict(self) -> dict[str, object]:
        return {
            "fit_split": self.fit_split, "uses_test_gold": self.uses_test_gold,
            "single_service_fixed_k": 1, "single_api_fixed_to_one": False,
            "task_fallback": self.task_fallback, "global_fallback": self.global_fallback,
            "rules": [asdict(rule) for rule in sorted(self.rules.values(), key=lambda value: (value.task_type, value.candidate_count_bucket))],
        }


def _mode_smallest(values: Sequence[int]) -> int:
    counts = Counter(values)
    maximum = max(counts.values()) if counts else 0
    return min((value for value, count in counts.items() if count == maximum), default=1)


def fit_cardinality(dev_rows: Sequence[Mapping[str, object]], rankings: Mapping[str, Sequence[str]], output: Path) -> CardinalityPolicy:
    strata: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    task_cards: dict[str, list[int]] = defaultdict(list)
    all_cards: list[int] = []
    candidate_rows: list[dict[str, object]] = []
    for row in dev_rows:
        task_id = text(row.get("benchmark_task_id"))
        ranking = list(rankings[task_id])
        alternatives = acceptable_gold_sets(row)
        cardinality = max(len(values) for values in alternatives)
        task = text(row.get("task_type"))
        task_cards[task].append(cardinality)
        all_cards.append(cardinality)
        strata[(task, candidate_bucket(len(ranking)))].append(row)
    task_fallback = {task: _mode_smallest(values) for task, values in task_cards.items()}
    global_fallback = _mode_smallest(all_cards)
    rules: dict[tuple[str, str], CardinalityRule] = {}
    for (task, bucket), rows in sorted(strata.items()):
        maximum = min(20, max(len(rankings[text(row.get("benchmark_task_id"))]) for row in rows))
        if task == "single_service_discovery":
            rules[(task, bucket)] = CardinalityRule(task, bucket, 1, len(rows), None, "SINGLE_SERVICE_FIXED_K_1")
            candidate_rows.append({"task_type": task, "candidate_count_bucket": bucket, "selected_k": 1, "dev_rows": len(rows), "dev_mean_f1": "", "selected": True})
            continue
        best_k, best_f1 = 1, -1.0
        for k in range(1, maximum + 1):
            scores = []
            for row in rows:
                ranking = rankings[text(row.get("benchmark_task_id"))]
                metrics = evaluate_acceptable_gold_sets(ranking, acceptable_gold_sets(row), ks=(1, 3, 5, 10), predicted_set=ranking[:k])
                scores.append(metrics["multi_label_f1"])
            mean_f1 = sum(scores) / len(scores)
            if mean_f1 > best_f1 + 1e-12 or (abs(mean_f1 - best_f1) <= 1e-12 and k < best_k):
                best_k, best_f1 = k, mean_f1
            candidate_rows.append({"task_type": task, "candidate_count_bucket": bucket, "selected_k": k, "dev_rows": len(rows), "dev_mean_f1": mean_f1, "selected": False})
        rules[(task, bucket)] = CardinalityRule(task, bucket, best_k, len(rows), best_f1, "DEV_F1_OPTIMIZED")
        for record in candidate_rows:
            if record["task_type"] == task and record["candidate_count_bucket"] == bucket and record["selected_k"] == best_k:
                record["selected"] = True
    policy = CardinalityPolicy(rules, task_fallback, global_fallback)
    write_csv(output / "02_CARDINALITY_CANDIDATES.csv", candidate_rows)
    write_json(output / "02_CARDINALITY_POLICY.json", policy.to_dict())
    (output / "02_CARDINALITY_POLICY_REPORT.md").write_text(
        "# Dev-frozen cardinality policy\n\n"
        "Policy fitting used only Candidate A Dev. `single_service_discovery` is fixed at k=1; `single_api_recommendation` is not fixed to one. "
        "All other strata select the smallest k attaining the best Dev set-F1. Test Gold cardinality appears only in ORACLE_CARDINALITY_DIAGNOSTIC fields.\n",
        encoding="utf-8",
    )
    return policy


def _rankings(rows: Sequence[Mapping[str, object]], catalog: Mapping[str, Mapping[str, object]], method: str, seed: int = 0) -> dict[str, list[str]]:
    documents = {candidate_id: document_text(document) for candidate_id, document in catalog.items()}
    result: dict[str, list[str]] = {}
    for row in rows:
        task_id = text(row.get("benchmark_task_id"))
        ids = candidate_ids(row)
        missing = [candidate_id for candidate_id in ids if candidate_id not in catalog]
        if missing:
            raise ValueError(f"{task_id}: missing Native catalog documents {missing[:5]}")
        if method == "bm25":
            result[task_id] = bm25_ranking(text(row.get("query_text")), ids, documents)
        elif method == "local_hashing":
            result[task_id] = local_embedding_ranking(text(row.get("query_text")), ids, documents)
        elif method == "random":
            result[task_id] = random_ranking(ids, seed=seed, task_id=task_id)
        else:
            raise ValueError(method)
    return result


def _evaluate_rows(rows: Sequence[Mapping[str, object]], rankings: Mapping[str, Sequence[str]], policy: CardinalityPolicy) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    metrics_rows: list[dict[str, object]] = []
    predictions: list[dict[str, object]] = []
    for row in rows:
        task_id = text(row.get("benchmark_task_id"))
        ranking = list(rankings[task_id])
        selected_k = policy.predict_k(row, len(ranking))
        selected = ranking[:selected_k]
        oracle = oracle_selected_set(row, ranking)
        metrics = evaluate_acceptable_gold_sets(ranking, acceptable_gold_sets(row), ks=(1, 3, 5, 10), predicted_set=selected)
        meta = {
            "benchmark_task_id": task_id, "task_type": text(row.get("task_type")),
            "source_dataset": text(row.get("source_dataset")), "prediction_target": text(row.get("prediction_target")),
            "candidate_count_bucket": candidate_bucket(len(ranking)), **metrics,
        }
        metrics_rows.append(meta)
        predictions.append({
            "benchmark_task_id": task_id, "ranking_json": json.dumps(ranking, ensure_ascii=False),
            "formal_selected_set_json": json.dumps(selected, ensure_ascii=False), "formal_selected_k": selected_k,
            "ORACLE_CARDINALITY_DIAGNOSTIC": len(oracle),
        })
    return metrics_rows, predictions


def _group_summaries(rows: Sequence[Mapping[str, object]], field: str) -> list[dict[str, object]]:
    groups: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        groups[text(row.get(field))].append(row)
    return [{field: key, "n": len(values), **mean_numeric(values)} for key, values in sorted(groups.items())]


def run_native_baselines(rows: Sequence[Mapping[str, object]], catalog: Mapping[str, Mapping[str, object]], cardinality_dir: Path, output: Path) -> dict[str, Any]:
    dev_rows = [row for row in rows if text(row.get("split")) == "dev"]
    test_rows = [row for row in rows if text(row.get("split")) == "test"]
    output.mkdir(parents=True, exist_ok=True)
    dev_bm25 = _rankings(dev_rows, catalog, "bm25")
    policy = fit_cardinality(dev_rows, dev_bm25, cardinality_dir)
    result: dict[str, Any] = {"status": "COMPLETED", "dev_rows": len(dev_rows), "test_rows": len(test_rows), "methods": {}, "per_query": {}}
    for method in ("bm25", "local_hashing"):
        rankings = _rankings(test_rows, catalog, method)
        metrics_rows, predictions = _evaluate_rows(test_rows, rankings, policy)
        write_csv(output / f"{method}_PREDICTIONS.csv", predictions)
        write_csv(output / f"{method}_METRICS_BY_QUERY.csv", metrics_rows)
        for label, field in (("TASK", "task_type"), ("SOURCE", "source_dataset"), ("TARGET", "prediction_target"), ("CANDIDATE_BUCKET", "candidate_count_bucket")):
            write_csv(output / f"{method}_RESULTS_BY_{label}.csv", _group_summaries(metrics_rows, field))
        by_task = _group_summaries(metrics_rows, "task_type")
        macro_keys = [key for key in mean_numeric(metrics_rows) if key in by_task[0]]
        six_task_macro = {key: sum(float(row[key]) for row in by_task) / len(by_task) for key in macro_keys}
        summary = {
            "overall_weighted_micro": mean_numeric(metrics_rows), "six_task_macro": six_task_macro,
            "bootstrap_95_ci": bootstrap_ci(metrics_rows, ("mrr", "recall@1", "recall@3", "recall@5", "ndcg@5", "multi_label_f1")),
            "uses_test_gold_for_formal_cardinality": False,
        }
        write_json(output / f"{method}_SUMMARY.json", summary)
        result["methods"][method] = summary
        result["per_query"][method] = {row["benchmark_task_id"]: row for row in metrics_rows}

    random_seed_rows: list[dict[str, object]] = []
    random_per_query: dict[str, list[dict[str, object]]] = defaultdict(list)
    analytical = []
    for row in test_rows:
        alternatives = acceptable_gold_sets(row)
        relevant = len(set(item for values in alternatives for item in values))
        analytical.append(analytical_random_mrr(len(candidate_ids(row)), relevant))
    for seed in range(20):
        rankings = _rankings(test_rows, catalog, "random", seed)
        metrics_rows, _ = _evaluate_rows(test_rows, rankings, policy)
        random_seed_rows.append({"seed": seed, **mean_numeric(metrics_rows)})
        for metric in metrics_rows:
            random_per_query[str(metric["benchmark_task_id"])].append(metric)
    write_csv(output / "random_20_SEED_RESULTS.csv", random_seed_rows)
    random_query_means = [
        {"benchmark_task_id": task_id, **mean_numeric(values)} for task_id, values in sorted(random_per_query.items())
    ]
    observed = sum(float(row["mrr"]) for row in random_seed_rows) / len(random_seed_rows)
    expected = sum(analytical) / len(analytical)
    random_summary = {
        "seed_count": 20, "overall_seed_mean": mean_numeric(random_seed_rows),
        "analytical_expected_mrr": expected, "observed_mrr": observed,
        "observed_analytical_mrr_abs_diff": abs(observed - expected),
        "bootstrap_95_ci": bootstrap_ci(random_query_means, ("mrr", "recall@1", "recall@3", "recall@5", "ndcg@5", "multi_label_f1")),
    }
    if random_summary["observed_analytical_mrr_abs_diff"] > 0.01:
        raise RuntimeError(f"Random analytical MRR alignment failed: {random_summary}")
    write_json(output / "random_SUMMARY.json", random_summary)
    write_json(output / "DENSE_CROSS_ENCODER_STATUS.json", {"status": "BLOCKED_MODEL_ARTIFACT_UNAVAILABLE", "fabricated_scores": False})
    result["methods"]["random"] = random_summary
    result["per_query"]["random"] = {row["benchmark_task_id"]: row for row in random_query_means}
    serializable = {key: value for key, value in result.items() if key != "per_query"}
    write_json(output / "BASELINE_COMPARISON.json", serializable)
    return result
