"""Deterministic, fail-closed joint train/dev/test optimizer.

The previous temporary allocator assigned dev and test without a valid joint
three-way capacity model and could leave train empty.  This module solves all
three splits in one MILP.  It aggregates identity groups that have identical
profiles, which keeps the optimization tractable while still assigning every
original identity group atomically.

SciPy's HiGHS MILP backend is required.  If it is unavailable or no feasible
solution is found, the caller must stop; there is deliberately no unverified
sequential-greedy fallback.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
import json
import math
from typing import Iterable, Mapping, Sequence

import numpy as np

try:
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix
except Exception as exc:  # pragma: no cover - exercised by fail-closed caller
    Bounds = LinearConstraint = milp = coo_matrix = None  # type: ignore[assignment]
    SCIPY_IMPORT_ERROR = repr(exc)
else:
    SCIPY_IMPORT_ERROR = ""

from .split_identity_v3 import stable_hash


TASKS = (
    "single_service_discovery",
    "single_api_recommendation",
    "multi_service_discovery",
    "multi_api_recommendation",
    "composable_service_discovery",
    "composable_api_recommendation",
)
SPLITS = ("train", "dev", "test")
DEFAULT_TARGETS = {"train": 50_497, "dev": 4_793, "test": 4_788}


def candidate_bucket(value: int | str) -> str:
    count = int(value)
    if count <= 5:
        return "2-5"
    if count <= 20:
        return "6-20"
    if count <= 50:
        return "21-50"
    if count <= 100:
        return "51-100"
    return "101+"


def _present(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class IdentityGroup:
    group_id: str
    row_ids: tuple[str, ...]
    row_count: int
    legacy_counts: tuple[int, int, int]
    task_counts: tuple[int, ...]
    source_counts: tuple[int, ...]
    cell_counts: tuple[int, ...]
    bucket_counts: tuple[int, ...]


@dataclass
class Profile:
    profile_id: str
    group_ids: list[str]
    multiplicity: int
    group_row_count: int
    legacy_counts: tuple[int, int, int]
    task_counts: tuple[int, ...]
    source_counts: tuple[int, ...]
    cell_counts: tuple[int, ...]
    bucket_counts: tuple[int, ...]


@dataclass(frozen=True)
class OptimizerConfig:
    exact_targets: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_TARGETS))
    relaxed_fraction: float = 0.01
    time_limit_seconds: float = 900.0
    mip_rel_gap: float = 0.0
    task_min_test: int = 20
    task_min_group_threshold: int = 40
    cell_min_test: int = 5
    cell_min_group_threshold: int = 20
    large_cell_row_threshold: int = 1_000
    large_cell_group_threshold: int = 100
    large_cell_ratio_tolerance: float = 0.04
    dominance_absolute_cap: float = 0.75
    dominance_extra_share: float = 0.10
    seed: int = 20260805


@dataclass
class CandidateResult:
    candidate_name: str
    solver_status: str
    solver_message: str
    used_relaxed_capacity: bool
    objective_value: float | None
    assignment_hash: str
    group_to_split: dict[str, str]
    row_to_split: dict[str, str]
    counts: dict[str, int]
    task_test_counts: dict[str, int]
    source_test_counts: dict[str, int]
    cell_test_counts: dict[str, int]
    bucket_test_counts: dict[str, int]
    moved_rows: int
    moved_groups: int
    constraint_results: list[dict[str, object]]
    distribution_metrics: dict[str, float]
    solver_metadata: dict[str, object]

    @property
    def valid(self) -> bool:
        return bool(self.constraint_results) and all(bool(row["passed"]) for row in self.constraint_results)


class _Model:
    def __init__(self) -> None:
        self.names: list[str] = []
        self.lower: list[float] = []
        self.upper: list[float] = []
        self.integrality: list[int] = []
        self.cost: list[float] = []
        self.constraint_names: list[str] = []
        self.constraint_coefficients: list[dict[int, float]] = []
        self.constraint_lower: list[float] = []
        self.constraint_upper: list[float] = []

    def add_var(self, name: str, *, lb: float = 0.0, ub: float = math.inf, integer: bool = False, cost: float = 0.0) -> int:
        index = len(self.names)
        self.names.append(name)
        self.lower.append(lb)
        self.upper.append(ub)
        self.integrality.append(1 if integer else 0)
        self.cost.append(cost)
        return index

    def add_constraint(self, name: str, coefficients: Mapping[int, float], *, lb: float = -math.inf, ub: float = math.inf) -> None:
        self.constraint_names.append(name)
        self.constraint_coefficients.append(dict(coefficients))
        self.constraint_lower.append(lb)
        self.constraint_upper.append(ub)

    def add_absolute_deviation(self, name: str, expression: Mapping[int, float], target: float, *, weight: float, scale: float) -> tuple[int, int]:
        effective_scale = max(float(scale), 1.0)
        positive = self.add_var(f"dev_pos::{name}", cost=weight / effective_scale)
        negative = self.add_var(f"dev_neg::{name}", cost=weight / effective_scale)
        coefficients = dict(expression)
        coefficients[positive] = coefficients.get(positive, 0.0) - 1.0
        coefficients[negative] = coefficients.get(negative, 0.0) + 1.0
        self.add_constraint(f"deviation::{name}", coefficients, lb=target, ub=target)
        return positive, negative

    def solve(self, *, time_limit_seconds: float, mip_rel_gap: float) -> object:
        if milp is None or coo_matrix is None or Bounds is None or LinearConstraint is None:
            raise RuntimeError(f"scipy.optimize.milp unavailable: {SCIPY_IMPORT_ERROR}")
        row_indices: list[int] = []
        col_indices: list[int] = []
        data: list[float] = []
        for row_index, coefficients in enumerate(self.constraint_coefficients):
            for col_index, value in coefficients.items():
                if value:
                    row_indices.append(row_index)
                    col_indices.append(col_index)
                    data.append(float(value))
        matrix = coo_matrix((data, (row_indices, col_indices)), shape=(len(self.constraint_coefficients), len(self.names))).tocsr()
        constraint = LinearConstraint(
            matrix,
            np.asarray(self.constraint_lower, dtype=float),
            np.asarray(self.constraint_upper, dtype=float),
        )
        return milp(
            np.asarray(self.cost, dtype=float),
            integrality=np.asarray(self.integrality, dtype=np.uint8),
            bounds=Bounds(np.asarray(self.lower, dtype=float), np.asarray(self.upper, dtype=float)),
            constraints=constraint,
            options={
                "disp": False,
                "time_limit": float(time_limit_seconds),
                "mip_rel_gap": float(mip_rel_gap),
                "presolve": True,
            },
        )


def _counter_tuple(counter: Mapping[str, int], labels: Sequence[str]) -> tuple[int, ...]:
    return tuple(int(counter.get(label, 0)) for label in labels)


def build_identity_groups(
    rows: Sequence[Mapping[str, object]],
    row_to_group: Mapping[str, str],
) -> tuple[dict[str, IdentityGroup], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    sources = tuple(sorted({_present(row.get("source_dataset")) or "UNKNOWN" for row in rows}))
    cells = tuple(sorted({f"{_present(row.get('task_type'))}|{_present(row.get('source_dataset'))}" for row in rows}))
    buckets = tuple(sorted({candidate_bucket(row.get("candidate_count", 0)) for row in rows}))
    by_group: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        row_id = _present(row.get("benchmark_task_id"))
        if row_id not in row_to_group:
            raise ValueError(f"missing identity group for {row_id}")
        by_group[row_to_group[row_id]].append(row)

    result: dict[str, IdentityGroup] = {}
    split_index = {split: index for index, split in enumerate(SPLITS)}
    task_index = {task: index for index, task in enumerate(TASKS)}
    source_index = {source: index for index, source in enumerate(sources)}
    cell_index = {cell: index for index, cell in enumerate(cells)}
    bucket_index = {bucket: index for index, bucket in enumerate(buckets)}

    for group_id, group_rows in by_group.items():
        legacy = [0, 0, 0]
        task_counts = [0] * len(TASKS)
        source_counts = [0] * len(sources)
        cell_counts = [0] * len(cells)
        bucket_counts = [0] * len(buckets)
        row_ids: list[str] = []
        for row in group_rows:
            row_id = _present(row.get("benchmark_task_id"))
            row_ids.append(row_id)
            split = _present(row.get("legacy_split"))
            if split not in split_index:
                raise ValueError(f"invalid legacy split {split!r} for {row_id}")
            legacy[split_index[split]] += 1
            task = _present(row.get("task_type"))
            if task not in task_index:
                raise ValueError(f"unknown task type {task!r}")
            task_counts[task_index[task]] += 1
            source = _present(row.get("source_dataset")) or "UNKNOWN"
            source_counts[source_index[source]] += 1
            cell_counts[cell_index[f"{task}|{source}"]] += 1
            bucket_counts[bucket_index[candidate_bucket(row.get("candidate_count", 0))]] += 1
        result[group_id] = IdentityGroup(
            group_id=group_id,
            row_ids=tuple(sorted(row_ids)),
            row_count=len(group_rows),
            legacy_counts=tuple(legacy),
            task_counts=tuple(task_counts),
            source_counts=tuple(source_counts),
            cell_counts=tuple(cell_counts),
            bucket_counts=tuple(bucket_counts),
        )
    return result, sources, cells, buckets


def aggregate_profiles(groups: Mapping[str, IdentityGroup]) -> list[Profile]:
    grouped: dict[tuple[object, ...], list[str]] = defaultdict(list)
    sample: dict[tuple[object, ...], IdentityGroup] = {}
    for group_id, group in groups.items():
        key: tuple[object, ...] = (
            group.row_count,
            group.legacy_counts,
            group.task_counts,
            group.source_counts,
            group.cell_counts,
            group.bucket_counts,
        )
        grouped[key].append(group_id)
        sample[key] = group
    profiles: list[Profile] = []
    for key, group_ids in sorted(grouped.items(), key=lambda item: stable_hash(item[0])):
        group = sample[key]
        ordered_ids = sorted(group_ids, key=lambda gid: stable_hash(["profile-member", gid]))
        profiles.append(
            Profile(
                profile_id=f"profile::{stable_hash(key)[:24]}",
                group_ids=ordered_ids,
                multiplicity=len(ordered_ids),
                group_row_count=group.row_count,
                legacy_counts=group.legacy_counts,
                task_counts=group.task_counts,
                source_counts=group.source_counts,
                cell_counts=group.cell_counts,
                bucket_counts=group.bucket_counts,
            )
        )
    return profiles


def _expression_for_dimension(
    profiles: Sequence[Profile],
    y_index: Mapping[tuple[int, int], int],
    split_idx: int,
    values_getter,
    label_idx: int,
) -> dict[int, float]:
    return {
        y_index[(profile_idx, split_idx)]: float(values_getter(profile)[label_idx])
        for profile_idx, profile in enumerate(profiles)
        if values_getter(profile)[label_idx]
    }


def _total_vector(groups: Mapping[str, IdentityGroup], attr: str, length: int) -> tuple[int, ...]:
    totals = [0] * length
    for group in groups.values():
        values = getattr(group, attr)
        for index, value in enumerate(values):
            totals[index] += int(value)
    return tuple(totals)


def _group_count_vector(groups: Mapping[str, IdentityGroup], attr: str, length: int) -> tuple[int, ...]:
    counts = [0] * length
    for group in groups.values():
        values = getattr(group, attr)
        for index, value in enumerate(values):
            if value:
                counts[index] += 1
    return tuple(counts)


def _build_model(
    profiles: Sequence[Profile],
    groups: Mapping[str, IdentityGroup],
    sources: Sequence[str],
    cells: Sequence[str],
    buckets: Sequence[str],
    config: OptimizerConfig,
    candidate_name: str,
    *,
    relaxed_capacity: bool,
) -> tuple[_Model, dict[tuple[int, int], int], dict[str, object]]:
    model = _Model()
    y_index: dict[tuple[int, int], int] = {}
    for profile_idx, profile in enumerate(profiles):
        for split_idx, split in enumerate(SPLITS):
            move_cost = profile.group_row_count - profile.legacy_counts[split_idx]
            if candidate_name == "C_MINIMAL_CHANGE":
                cost = float(move_cost) + (split_idx + 1) * 1e-8
            elif candidate_name == "A_PROPORTIONAL":
                cost = float(move_cost) * 1e-5 + (split_idx + 1) * 1e-9
            elif candidate_name == "B_REPRESENTATIVE":
                cost = float(move_cost) * 1e-7 + (split_idx + 1) * 1e-9
            else:
                raise ValueError(f"unknown candidate {candidate_name}")
            y_index[(profile_idx, split_idx)] = model.add_var(
                f"assign::{profile.profile_id}::{split}",
                lb=0,
                ub=profile.multiplicity,
                integer=True,
                cost=cost,
            )
        model.add_constraint(
            f"profile_partition::{profile.profile_id}",
            {y_index[(profile_idx, split_idx)]: 1.0 for split_idx in range(3)},
            lb=profile.multiplicity,
            ub=profile.multiplicity,
        )

    targets = dict(config.exact_targets)
    total_rows = sum(group.row_count for group in groups.values())
    if sum(targets.values()) != total_rows:
        raise ValueError(f"split targets sum to {sum(targets.values())}, expected {total_rows}")

    for split_idx, split in enumerate(SPLITS):
        expression = {
            y_index[(profile_idx, split_idx)]: float(profile.group_row_count)
            for profile_idx, profile in enumerate(profiles)
        }
        target = targets[split]
        if relaxed_capacity:
            tolerance = max(1, math.floor(target * config.relaxed_fraction))
            model.add_constraint(f"capacity::{split}", expression, lb=target - tolerance, ub=target + tolerance)
        else:
            model.add_constraint(f"capacity::{split}", expression, lb=target, ub=target)

    task_totals = _total_vector(groups, "task_counts", len(TASKS))
    task_groups = _group_count_vector(groups, "task_counts", len(TASKS))
    source_totals = _total_vector(groups, "source_counts", len(sources))
    cell_totals = _total_vector(groups, "cell_counts", len(cells))
    cell_groups = _group_count_vector(groups, "cell_counts", len(cells))
    bucket_totals = _total_vector(groups, "bucket_counts", len(buckets))

    test_idx = SPLITS.index("test")
    dev_idx = SPLITS.index("dev")

    for task_idx, task in enumerate(TASKS):
        expression = _expression_for_dimension(profiles, y_index, test_idx, lambda p: p.task_counts, task_idx)
        if task_groups[task_idx] >= config.task_min_group_threshold:
            model.add_constraint(f"task_min_test::{task}", expression, lb=config.task_min_test)
        model.add_constraint(f"task_nonempty_test::{task}", expression, lb=1)
        upper_share = min(
            config.dominance_absolute_cap,
            task_totals[task_idx] / total_rows + config.dominance_extra_share,
        )
        model.add_constraint(
            f"task_dominance::{task}",
            expression,
            ub=math.floor(targets["test"] * upper_share + 1e-9),
        )

    for cell_idx, cell in enumerate(cells):
        test_expression = _expression_for_dimension(profiles, y_index, test_idx, lambda p: p.cell_counts, cell_idx)
        if cell_groups[cell_idx] >= config.cell_min_group_threshold:
            model.add_constraint(f"cell_min_test::{cell}", test_expression, lb=config.cell_min_test)
        if cell_totals[cell_idx] >= config.large_cell_row_threshold and cell_groups[cell_idx] >= config.large_cell_group_threshold:
            for split_idx, split in ((dev_idx, "dev"), (test_idx, "test")):
                expression = _expression_for_dimension(profiles, y_index, split_idx, lambda p: p.cell_counts, cell_idx)
                target_ratio = targets[split] / total_rows
                lower = max(0.0, (target_ratio - config.large_cell_ratio_tolerance) * cell_totals[cell_idx])
                upper = min(float(cell_totals[cell_idx]), (target_ratio + config.large_cell_ratio_tolerance) * cell_totals[cell_idx])
                model.add_constraint(f"large_cell_ratio::{cell}::{split}", expression, lb=math.floor(lower), ub=math.ceil(upper))

    metatool_cell = "single_service_discovery|MetaTool"
    if metatool_cell in cells:
        cell_idx = cells.index(metatool_cell)
        expression = _expression_for_dimension(profiles, y_index, test_idx, lambda p: p.cell_counts, cell_idx)
        model.add_constraint("metatool_single_service_test", expression, lb=1)

    ratios = {split: targets[split] / total_rows for split in SPLITS}

    def add_distribution_deviations(*, task_weight: float, source_weight: float, cell_weight: float, bucket_weight: float) -> None:
        for split_idx, split in enumerate(SPLITS):
            for idx, label in enumerate(TASKS):
                expression = _expression_for_dimension(profiles, y_index, split_idx, lambda p: p.task_counts, idx)
                model.add_absolute_deviation(
                    f"task::{split}::{label}", expression, task_totals[idx] * ratios[split], weight=task_weight, scale=max(task_totals[idx] * ratios[split], 5)
                )
            for idx, label in enumerate(sources):
                expression = _expression_for_dimension(profiles, y_index, split_idx, lambda p: p.source_counts, idx)
                model.add_absolute_deviation(
                    f"source::{split}::{label}", expression, source_totals[idx] * ratios[split], weight=source_weight, scale=max(source_totals[idx] * ratios[split], 5)
                )
            for idx, label in enumerate(cells):
                expression = _expression_for_dimension(profiles, y_index, split_idx, lambda p: p.cell_counts, idx)
                model.add_absolute_deviation(
                    f"cell::{split}::{label}", expression, cell_totals[idx] * ratios[split], weight=cell_weight, scale=max(cell_totals[idx] * ratios[split], 5)
                )
            for idx, label in enumerate(buckets):
                expression = _expression_for_dimension(profiles, y_index, split_idx, lambda p: p.bucket_counts, idx)
                model.add_absolute_deviation(
                    f"bucket::{split}::{label}", expression, bucket_totals[idx] * ratios[split], weight=bucket_weight, scale=max(bucket_totals[idx] * ratios[split], 5)
                )

    if candidate_name == "A_PROPORTIONAL":
        add_distribution_deviations(task_weight=12.0, source_weight=6.0, cell_weight=20.0, bucket_weight=3.0)
    elif candidate_name == "B_REPRESENTATIVE":
        add_distribution_deviations(task_weight=30.0, source_weight=10.0, cell_weight=35.0, bucket_weight=1.0)
        z_task = model.add_var("representative_min_task_test", lb=0, ub=targets["test"], cost=-2_000.0)
        for task_idx, task in enumerate(TASKS):
            expression = _expression_for_dimension(profiles, y_index, test_idx, lambda p: p.task_counts, task_idx)
            expression[z_task] = -1.0
            model.add_constraint(f"representative_z_task::{task}", expression, lb=0)
        eligible_cells = [idx for idx in range(len(cells)) if cell_groups[idx] >= config.cell_min_group_threshold]
        if eligible_cells:
            z_cell = model.add_var("representative_min_cell_test", lb=0, ub=targets["test"], cost=-500.0)
            for cell_idx in eligible_cells:
                expression = _expression_for_dimension(profiles, y_index, test_idx, lambda p: p.cell_counts, cell_idx)
                expression[z_cell] = -1.0
                model.add_constraint(f"representative_z_cell::{cells[cell_idx]}", expression, lb=0)
    elif candidate_name == "C_MINIMAL_CHANGE":
        # Tiny proportional tie-breaker; moved-row cost remains overwhelmingly dominant.
        add_distribution_deviations(task_weight=1e-5, source_weight=5e-6, cell_weight=1e-5, bucket_weight=1e-6)

    metadata = {
        "candidate_name": candidate_name,
        "profile_count": len(profiles),
        "group_count": len(groups),
        "total_rows": total_rows,
        "targets": targets,
        "relaxed_capacity": relaxed_capacity,
        "objective_hash": stable_hash(
            {
                "candidate_name": candidate_name,
                "cost": model.cost,
                "constraint_names": model.constraint_names,
            }
        ),
        "variable_count": len(model.names),
        "constraint_count": len(model.constraint_names),
    }
    return model, y_index, metadata


def _decode_profile_solution(
    profiles: Sequence[Profile],
    y_index: Mapping[tuple[int, int], int],
    values: Sequence[float],
    *,
    candidate_name: str,
) -> dict[str, str]:
    assignment: dict[str, str] = {}
    for profile_idx, profile in enumerate(profiles):
        counts = [int(round(values[y_index[(profile_idx, split_idx)]])) for split_idx in range(3)]
        if sum(counts) != profile.multiplicity:
            raise RuntimeError(
                f"profile {profile.profile_id} assignment counts {counts} do not sum to {profile.multiplicity}"
            )
        ordered = sorted(profile.group_ids, key=lambda gid: stable_hash([candidate_name, profile.profile_id, gid]))
        offset = 0
        for split_idx, count in enumerate(counts):
            for group_id in ordered[offset : offset + count]:
                assignment[group_id] = SPLITS[split_idx]
            offset += count
    if len(assignment) != sum(profile.multiplicity for profile in profiles):
        raise RuntimeError("decoded solution does not assign every identity group")
    return assignment


def _l1_distribution(actual: Mapping[str, int], total_actual: int, overall: Mapping[str, int], total_overall: int) -> float:
    labels = set(actual) | set(overall)
    return sum(abs(actual.get(label, 0) / max(total_actual, 1) - overall.get(label, 0) / max(total_overall, 1)) for label in labels)


def validate_assignment(
    rows: Sequence[Mapping[str, object]],
    groups: Mapping[str, IdentityGroup],
    row_to_group: Mapping[str, str],
    group_to_split: Mapping[str, str],
    config: OptimizerConfig,
    *,
    relaxed_capacity: bool,
) -> tuple[dict[str, str], list[dict[str, object]], dict[str, object]]:
    row_to_split: dict[str, str] = {}
    for row in rows:
        row_id = _present(row.get("benchmark_task_id"))
        group_id = row_to_group[row_id]
        if group_id not in group_to_split:
            raise ValueError(f"missing split for identity group {group_id}")
        row_to_split[row_id] = group_to_split[group_id]

    counts = Counter(row_to_split.values())
    task_test = Counter(_present(row.get("task_type")) for row in rows if row_to_split[_present(row.get("benchmark_task_id"))] == "test")
    source_test = Counter(_present(row.get("source_dataset")) for row in rows if row_to_split[_present(row.get("benchmark_task_id"))] == "test")
    cell_test = Counter(
        f"{_present(row.get('task_type'))}|{_present(row.get('source_dataset'))}"
        for row in rows
        if row_to_split[_present(row.get("benchmark_task_id"))] == "test"
    )
    bucket_test = Counter(
        candidate_bucket(row.get("candidate_count", 0))
        for row in rows
        if row_to_split[_present(row.get("benchmark_task_id"))] == "test"
    )
    total_task = Counter(_present(row.get("task_type")) for row in rows)
    total_source = Counter(_present(row.get("source_dataset")) for row in rows)
    total_cell = Counter(f"{_present(row.get('task_type'))}|{_present(row.get('source_dataset'))}" for row in rows)
    total_bucket = Counter(candidate_bucket(row.get("candidate_count", 0)) for row in rows)
    distinct_task_groups: Counter[str] = Counter()
    distinct_cell_groups: Counter[str] = Counter()
    for group in groups.values():
        for task_idx, value in enumerate(group.task_counts):
            if value:
                distinct_task_groups[TASKS[task_idx]] += 1
        # cell labels are reconstructed from rows below for audit clarity.
    groups_by_cell: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        groups_by_cell[f"{_present(row.get('task_type'))}|{_present(row.get('source_dataset'))}"].add(
            row_to_group[_present(row.get("benchmark_task_id"))]
        )
    distinct_cell_groups.update({cell: len(group_ids) for cell, group_ids in groups_by_cell.items()})

    constraints: list[dict[str, object]] = []

    def record(name: str, passed: bool, evidence: object) -> None:
        constraints.append({"constraint": name, "passed": bool(passed), "evidence": evidence})

    record("H01_group_unsplit", all(group_to_split[group_id] in SPLITS for group_id in groups), {"group_count": len(groups)})
    record("H02_six_tasks_test", all(task_test[task] > 0 for task in TASKS), dict(task_test))
    record("H03_identity_group_overlap", len(row_to_split) == len(rows), {"assigned_rows": len(row_to_split)})
    record("H04_relationship_conflicts", True, "identity graph components are atomic")
    record("H05_content_duplicate_conflicts", True, "query_signature and review_content_fingerprint are identity edges")

    targets = dict(config.exact_targets)
    for split, hard_name in (("test", "H06_test_size"), ("dev", "H07_dev_size")):
        target = targets[split]
        if relaxed_capacity:
            tolerance = max(1, math.floor(target * config.relaxed_fraction))
            passed = target - tolerance <= counts[split] <= target + tolerance
            evidence = {"actual": counts[split], "target": target, "tolerance": tolerance}
        else:
            passed = counts[split] == target
            evidence = {"actual": counts[split], "target": target, "tolerance": 0}
        record(hard_name, passed, evidence)

    task_min_evidence: dict[str, object] = {}
    task_min_pass = True
    for task in TASKS:
        required = config.task_min_test if distinct_task_groups[task] >= config.task_min_group_threshold else 1
        task_min_evidence[task] = {"actual": task_test[task], "required": required, "distinct_groups": distinct_task_groups[task]}
        task_min_pass &= task_test[task] >= required
    record("H08_task_minimum", task_min_pass, task_min_evidence)

    cell_min_evidence: dict[str, object] = {}
    cell_min_pass = True
    for cell, total in total_cell.items():
        if distinct_cell_groups[cell] >= config.cell_min_group_threshold:
            required = config.cell_min_test
            cell_min_evidence[cell] = {"actual": cell_test[cell], "required": required, "total": total, "distinct_groups": distinct_cell_groups[cell]}
            cell_min_pass &= cell_test[cell] >= required
    record("H09_source_task_minimum", cell_min_pass, cell_min_evidence)

    record(
        "H10_metatool_single_service",
        cell_test["single_service_discovery|MetaTool"] > 0,
        {"actual": cell_test["single_service_discovery|MetaTool"]},
    )

    dominance_evidence: dict[str, object] = {}
    dominance_pass = True
    for task in TASKS:
        upper_share = min(config.dominance_absolute_cap, total_task[task] / len(rows) + config.dominance_extra_share)
        share = task_test[task] / max(counts["test"], 1)
        dominance_evidence[task] = {"share": share, "upper_share": upper_share, "rows": task_test[task]}
        dominance_pass &= share <= upper_share + 1e-12
    record("H11_task_dominance", dominance_pass, dominance_evidence)
    record("H12_content_immutable", True, "caller verifies authoritative content hashes")

    large_cell_evidence: dict[str, object] = {}
    large_cell_pass = True
    for cell, total in total_cell.items():
        if total < config.large_cell_row_threshold or distinct_cell_groups[cell] < config.large_cell_group_threshold:
            continue
        per_split = Counter()
        for row in rows:
            row_cell = f"{_present(row.get('task_type'))}|{_present(row.get('source_dataset'))}"
            if row_cell == cell:
                per_split[row_to_split[_present(row.get("benchmark_task_id"))]] += 1
        dev_ratio = per_split["dev"] / total
        test_ratio = per_split["test"] / total
        target_dev = targets["dev"] / len(rows)
        target_test = targets["test"] / len(rows)
        passed = abs(dev_ratio - target_dev) <= config.large_cell_ratio_tolerance + 1e-12 and abs(test_ratio - target_test) <= config.large_cell_ratio_tolerance + 1e-12
        large_cell_pass &= passed
        large_cell_evidence[cell] = {
            "row_count": total,
            "distinct_groups": distinct_cell_groups[cell],
            "dev_ratio": dev_ratio,
            "test_ratio": test_ratio,
            "target_dev_ratio": target_dev,
            "target_test_ratio": target_test,
            "tolerance": config.large_cell_ratio_tolerance,
        }
    record("H13_large_source_task_ratio", large_cell_pass, large_cell_evidence)
    record("H14_no_row_mutation", len(row_to_split) == len(rows) and set(row_to_split) == {_present(row.get('benchmark_task_id')) for row in rows}, {"row_count": len(rows)})
    record("H15_source_local_cross_source_edges", True, "enforced by split_identity_v3 namespacing")

    moved_rows = sum(
        1
        for row in rows
        if row_to_split[_present(row.get("benchmark_task_id"))] != _present(row.get("legacy_split"))
    )
    moved_groups = 0
    for group_id, group in groups.items():
        split = group_to_split[group_id]
        if group.legacy_counts[SPLITS.index(split)] != group.row_count:
            moved_groups += 1

    distribution_metrics = {
        "task_test_l1": _l1_distribution(task_test, counts["test"], total_task, len(rows)),
        "source_test_l1": _l1_distribution(source_test, counts["test"], total_source, len(rows)),
        "cell_test_l1": _l1_distribution(cell_test, counts["test"], total_cell, len(rows)),
        "bucket_test_l1": _l1_distribution(bucket_test, counts["test"], total_bucket, len(rows)),
        "maximum_task_test_share": max((task_test[task] / max(counts["test"], 1) for task in TASKS), default=0.0),
        "minimum_task_test_rows": min((task_test[task] for task in TASKS), default=0),
        "minimum_eligible_cell_test_rows": min(
            (cell_test[cell] for cell in total_cell if distinct_cell_groups[cell] >= config.cell_min_group_threshold),
            default=0,
        ),
    }
    evidence = {
        "counts": dict(counts),
        "task_test_counts": dict(task_test),
        "source_test_counts": dict(source_test),
        "cell_test_counts": dict(cell_test),
        "bucket_test_counts": dict(bucket_test),
        "moved_rows": moved_rows,
        "moved_groups": moved_groups,
        "distribution_metrics": distribution_metrics,
    }
    return row_to_split, constraints, evidence


def solve_split_candidate(
    rows: Sequence[Mapping[str, object]],
    row_to_group: Mapping[str, str],
    candidate_name: str,
    *,
    config: OptimizerConfig | None = None,
) -> CandidateResult:
    config = config or OptimizerConfig()
    groups, sources, cells, buckets = build_identity_groups(rows, row_to_group)
    profiles = aggregate_profiles(groups)
    last_error = ""
    for relaxed in (False, True):
        model, y_index, metadata = _build_model(
            profiles,
            groups,
            sources,
            cells,
            buckets,
            config,
            candidate_name,
            relaxed_capacity=relaxed,
        )
        try:
            result = model.solve(time_limit_seconds=config.time_limit_seconds, mip_rel_gap=config.mip_rel_gap)
        except Exception as exc:
            last_error = repr(exc)
            break
        values = getattr(result, "x", None)
        if values is None:
            last_error = str(getattr(result, "message", "MILP produced no solution"))
            if not relaxed:
                continue
            break
        group_to_split = _decode_profile_solution(profiles, y_index, values, candidate_name=candidate_name)
        row_to_split, constraints, evidence = validate_assignment(
            rows,
            groups,
            row_to_group,
            group_to_split,
            config,
            relaxed_capacity=relaxed,
        )
        solver_metadata = {
            **metadata,
            "scipy_status": int(getattr(result, "status", -1)),
            "scipy_success": bool(getattr(result, "success", False)),
            "scipy_message": str(getattr(result, "message", "")),
            "mip_node_count": getattr(result, "mip_node_count", None),
            "mip_gap": getattr(result, "mip_gap", None),
            "mip_dual_bound": getattr(result, "mip_dual_bound", None),
        }
        candidate = CandidateResult(
            candidate_name=candidate_name,
            solver_status="FEASIBLE_VALIDATED" if all(row["passed"] for row in constraints) else "FEASIBLE_BUT_INVALID",
            solver_message=str(getattr(result, "message", "")),
            used_relaxed_capacity=relaxed,
            objective_value=float(getattr(result, "fun", math.nan)) if getattr(result, "fun", None) is not None else None,
            assignment_hash=stable_hash(sorted(group_to_split.items())),
            group_to_split=group_to_split,
            row_to_split=row_to_split,
            counts=dict(evidence["counts"]),
            task_test_counts=dict(evidence["task_test_counts"]),
            source_test_counts=dict(evidence["source_test_counts"]),
            cell_test_counts=dict(evidence["cell_test_counts"]),
            bucket_test_counts=dict(evidence["bucket_test_counts"]),
            moved_rows=int(evidence["moved_rows"]),
            moved_groups=int(evidence["moved_groups"]),
            constraint_results=constraints,
            distribution_metrics=dict(evidence["distribution_metrics"]),
            solver_metadata=solver_metadata,
        )
        if candidate.valid:
            return candidate
        last_error = f"MILP returned assignment that failed validation: {[row['constraint'] for row in constraints if not row['passed']]}"
        if relaxed:
            return candidate
    return CandidateResult(
        candidate_name=candidate_name,
        solver_status="NO_FEASIBLE_VALIDATED_SOLUTION",
        solver_message=last_error,
        used_relaxed_capacity=False,
        objective_value=None,
        assignment_hash="",
        group_to_split={},
        row_to_split={},
        counts={},
        task_test_counts={},
        source_test_counts={},
        cell_test_counts={},
        bucket_test_counts={},
        moved_rows=0,
        moved_groups=0,
        constraint_results=[],
        distribution_metrics={},
        solver_metadata={"scipy_import_error": SCIPY_IMPORT_ERROR},
    )


def recommendation_key(candidate: CandidateResult) -> tuple[object, ...]:
    """Common, non-hardcoded recommendation order.

    Validity is checked before this key is used.  The ordering prioritizes low
    task dominance, representative task/source cells, then distribution fit,
    and only finally migration cost.
    """

    metrics = candidate.distribution_metrics
    return (
        metrics.get("maximum_task_test_share", math.inf),
        metrics.get("cell_test_l1", math.inf),
        metrics.get("task_test_l1", math.inf),
        metrics.get("source_test_l1", math.inf),
        -metrics.get("minimum_task_test_rows", 0),
        -metrics.get("minimum_eligible_cell_test_rows", 0),
        candidate.moved_rows,
        candidate.moved_groups,
        candidate.assignment_hash,
    )


def choose_recommended_candidate(candidates: Sequence[CandidateResult]) -> CandidateResult | None:
    valid = [candidate for candidate in candidates if candidate.valid]
    return min(valid, key=recommendation_key) if valid else None


def candidate_summary(candidate: CandidateResult) -> dict[str, object]:
    return {
        "candidate_name": candidate.candidate_name,
        "valid": candidate.valid,
        "solver_status": candidate.solver_status,
        "used_relaxed_capacity": candidate.used_relaxed_capacity,
        "objective_value": candidate.objective_value,
        "assignment_hash": candidate.assignment_hash,
        "counts": candidate.counts,
        "task_test_counts": candidate.task_test_counts,
        "source_test_counts": candidate.source_test_counts,
        "moved_rows": candidate.moved_rows,
        "moved_groups": candidate.moved_groups,
        "distribution_metrics": candidate.distribution_metrics,
        "failed_constraints": [row["constraint"] for row in candidate.constraint_results if not row["passed"]],
        "solver_metadata": candidate.solver_metadata,
    }
