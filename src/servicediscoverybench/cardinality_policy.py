"""Dev-frozen selected-set cardinality policies.

Formal set metrics must never read the number of Gold items from the test row.
This module fits a deterministic Top-K selection policy on the *dev* split and
applies it unchanged to test.  Test-Gold cardinality remains available only as
an explicitly labelled oracle diagnostic.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
import json
from statistics import median
from typing import Mapping, Sequence

from .joint_split_optimizer_v3 import candidate_bucket


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value or "").strip()
    if not text:
        return []
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("expected JSON list")
    return [str(item) for item in parsed]


def _json_list_of_lists(value: object) -> list[list[str]]:
    if isinstance(value, list):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("expected JSON list")
    result: list[list[str]] = []
    for item in parsed:
        if isinstance(item, list):
            values = [str(value) for value in item]
            if values:
                result.append(values)
    return result


def acceptable_gold_sets(row: Mapping[str, object]) -> list[list[str]]:
    target = str(row.get("prediction_target") or "").strip()
    if target == "service":
        alternatives = _json_list_of_lists(row.get("acceptable_gold_service_sets_json"))
        reference = _json_list(row.get("gold_services_json"))
    elif target == "api":
        alternatives = _json_list_of_lists(row.get("acceptable_gold_api_sets_json"))
        reference = _json_list(row.get("gold_apis_json"))
    else:
        raise ValueError(f"unknown prediction_target {target!r}")
    return alternatives or ([reference] if reference else [])


def _best_set_f1(predicted: Sequence[str], alternatives: Sequence[Sequence[str]]) -> float:
    predicted_set = set(predicted)
    best = 0.0
    for gold in alternatives:
        gold_set = set(gold)
        if not gold_set:
            continue
        overlap = len(predicted_set & gold_set)
        precision = overlap / len(predicted_set) if predicted_set else 0.0
        recall = overlap / len(gold_set)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        best = max(best, f1)
    return best


@dataclass(frozen=True)
class CardinalityRule:
    task_type: str
    candidate_count_bucket: str
    selected_k: int
    dev_rows: int
    dev_mean_f1: float
    fallback_level: str


@dataclass
class CardinalityPolicy:
    policy_name: str
    rules: dict[tuple[str, str], CardinalityRule]
    task_fallback: dict[str, int]
    global_fallback: int
    max_k: int
    fit_split: str = "dev"
    uses_test_gold: bool = False

    def predict_k(self, row: Mapping[str, object], candidate_count: int) -> int:
        task = str(row.get("task_type") or "").strip()
        if task.startswith("single_"):
            return 1
        bucket = candidate_bucket(candidate_count)
        rule = self.rules.get((task, bucket))
        if rule:
            k = rule.selected_k
        elif task in self.task_fallback:
            k = self.task_fallback[task]
        else:
            k = self.global_fallback
        return max(1, min(int(k), int(candidate_count)))

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_name": self.policy_name,
            "fit_split": self.fit_split,
            "uses_test_gold": self.uses_test_gold,
            "max_k": self.max_k,
            "global_fallback": self.global_fallback,
            "task_fallback": dict(self.task_fallback),
            "rules": [asdict(rule) for rule in sorted(self.rules.values(), key=lambda r: (r.task_type, r.candidate_count_bucket))],
        }


def _mode_smallest(values: Sequence[int]) -> int:
    if not values:
        return 1
    counts = Counter(values)
    maximum = max(counts.values())
    return min(value for value, count in counts.items() if count == maximum)


def fit_dev_topk_policy(
    dev_rows: Sequence[Mapping[str, object]],
    rankings: Mapping[str, Sequence[str]],
    *,
    policy_name: str,
    max_k: int = 20,
    minimum_stratum_rows: int = 5,
) -> CardinalityPolicy:
    """Fit Top-K by maximizing mean set-F1 on dev only.

    A rule is fitted for each ``task_type × candidate_count_bucket`` stratum
    with at least ``minimum_stratum_rows`` rows.  Sparse strata fall back to a
    task-level modal Gold cardinality learned on dev; the final fallback is the
    global dev modal cardinality.  Ties in F1 choose the smaller K.
    """

    gold_cardinality_by_task: dict[str, list[int]] = defaultdict(list)
    all_cardinalities: list[int] = []
    strata: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in dev_rows:
        task_id = str(row.get("benchmark_task_id") or "").strip()
        if task_id not in rankings:
            continue
        alternatives = acceptable_gold_sets(row)
        if not alternatives:
            continue
        cardinality = max(len(gold) for gold in alternatives)
        task = str(row.get("task_type") or "").strip()
        gold_cardinality_by_task[task].append(cardinality)
        all_cardinalities.append(cardinality)
        candidate_count = len(rankings[task_id])
        strata[(task, candidate_bucket(candidate_count))].append(row)

    task_fallback = {task: _mode_smallest(values) for task, values in gold_cardinality_by_task.items()}
    global_fallback = _mode_smallest(all_cardinalities)
    rules: dict[tuple[str, str], CardinalityRule] = {}

    for (task, bucket), rows in strata.items():
        if task.startswith("single_"):
            rules[(task, bucket)] = CardinalityRule(task, bucket, 1, len(rows), 1.0, "single_task_fixed_1")
            continue
        if len(rows) < minimum_stratum_rows:
            rules[(task, bucket)] = CardinalityRule(
                task,
                bucket,
                task_fallback.get(task, global_fallback),
                len(rows),
                float("nan"),
                "task_modal_gold_cardinality_from_dev",
            )
            continue
        best_k = 1
        best_f1 = -1.0
        maximum_candidate_count = max(len(rankings[str(row.get("benchmark_task_id"))]) for row in rows)
        for k in range(1, min(max_k, maximum_candidate_count) + 1):
            values: list[float] = []
            for row in rows:
                task_id = str(row.get("benchmark_task_id"))
                ranking = list(rankings[task_id])
                values.append(_best_set_f1(ranking[: min(k, len(ranking))], acceptable_gold_sets(row)))
            mean_f1 = sum(values) / len(values)
            if mean_f1 > best_f1 + 1e-12 or (abs(mean_f1 - best_f1) <= 1e-12 and k < best_k):
                best_k = k
                best_f1 = mean_f1
        rules[(task, bucket)] = CardinalityRule(task, bucket, best_k, len(rows), best_f1, "dev_f1_optimized_topk")

    return CardinalityPolicy(
        policy_name=policy_name,
        rules=rules,
        task_fallback=task_fallback,
        global_fallback=global_fallback,
        max_k=max_k,
        fit_split="dev",
        uses_test_gold=False,
    )


def selected_set_from_policy(row: Mapping[str, object], ranking: Sequence[str], policy: CardinalityPolicy) -> list[str]:
    k = policy.predict_k(row, len(ranking))
    return list(dict.fromkeys(ranking))[:k]


def oracle_selected_set(row: Mapping[str, object], ranking: Sequence[str]) -> list[str]:
    """Test-Gold cardinality diagnostic. Never label this as a formal result."""

    alternatives = acceptable_gold_sets(row)
    cardinality = max((len(gold) for gold in alternatives), default=1)
    return list(dict.fromkeys(ranking))[:cardinality]
