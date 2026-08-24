from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Iterable


HUMAN_REVIEW_ALLOWED_VALUES = {
    "semantic_alignment_check": {"aligned", "misaligned", "uncertain"},
    "gold_validity_check": {"true", "false", "uncertain"},
    "candidate_validity_check": {"true", "false", "uncertain"},
    "service_catalog_check": {"pass", "fail", "uncertain"},
    "task_type_check": {"pass", "fail", "uncertain"},
    "leakage_check": {"no_blocking_leak", "blocking_leak", "uncertain"},
    "dependency_check": {"true", "false", "uncertain", "not_applicable_parallel_multi"},
    "final_decision": {"keep", "remove", "uncertain"},
    "severity": {"none", "minor", "major", "critical"},
}


def human_review_validation_errors(row: dict[str, str], composable: bool) -> list[str]:
    """Validate the human-entered portion of one completed review row.

    These rules deliberately reject only structural or logically incompatible
    values. They never infer or replace a human decision.
    """

    errors = []
    for field, allowed in HUMAN_REVIEW_ALLOWED_VALUES.items():
        value = (row.get(field) or "").strip()
        if value not in allowed:
            errors.append(f"{field}: invalid value {value!r}")
    decision = (row.get("final_decision") or "").strip()
    severity = (row.get("severity") or "").strip()
    dependency = (row.get("dependency_check") or "").strip()
    if decision == "remove" and not (row.get("error_type") or "").strip():
        errors.append("error_type: required when final_decision=remove")
    if decision == "uncertain" and not (row.get("notes") or "").strip():
        errors.append("notes: required when final_decision=uncertain")
    if decision == "keep" and severity in {"major", "critical"}:
        errors.append("severity: keep cannot carry a major or critical issue")
    if composable and dependency == "not_applicable_parallel_multi":
        errors.append("dependency_check: composable review cannot use ordinary-task N/A")
    if not composable and dependency != "not_applicable_parallel_multi":
        errors.append("dependency_check: ordinary review must use not_applicable_parallel_multi")
    reviewed_at = (row.get("reviewed_at") or "").strip()
    try:
        datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        errors.append("reviewed_at: must be a valid ISO-8601 timestamp")
    return errors


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def cohens_kappa(left: Iterable[str], right: Iterable[str]) -> float:
    left_values, right_values = list(left), list(right)
    if len(left_values) != len(right_values):
        raise ValueError("label sequences must have equal length")
    if not left_values:
        return 0.0
    observed = sum(a == b for a, b in zip(left_values, right_values)) / len(left_values)
    left_counts, right_counts = Counter(left_values), Counter(right_values)
    labels = set(left_counts) | set(right_counts)
    expected = sum((left_counts[label] / len(left_values)) * (right_counts[label] / len(right_values)) for label in labels)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def raw_agreement(left: Iterable[str], right: Iterable[str]) -> float:
    left_values, right_values = list(left), list(right)
    if len(left_values) != len(right_values):
        raise ValueError("label sequences must have equal length")
    return sum(a == b for a, b in zip(left_values, right_values)) / len(left_values) if left_values else 0.0
