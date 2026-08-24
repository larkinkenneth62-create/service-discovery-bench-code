"""Deterministic group-aware split construction and reverse leakage audits."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .signatures import stable_hash


LINK_FIELDS = (
    "split_group_id",
    "source_query_id",
    "query_signature",
    "task_signature",
    "paired_task_group_id",
    "underlying_task_id",
    "parent_row_id",
)

# v0.1 `task_signature` intentionally excludes the query so that it can be
# used to find review-template duplicates.  It is therefore not a task
# identity: using it as a connected-component key can join otherwise
# independent queries into one enormous split group.  New split revisions must
# use a full review-content fingerprint (which includes the query) instead.
# Keep the legacy fields above unchanged for reproducing the v0.1 release.
IDENTITY_LINK_FIELDS_V2 = (
    "source_query_id",
    "query_signature",
    "review_content_fingerprint",
    "paired_task_group_id",
    "underlying_task_id",
    "parent_row_id",
    "source_task_id",
)
AUDIT_FIELDS = (
    "split_group_id",
    "source_query_id",
    "task_signature",
    "query_signature",
    "paired_task_group_id",
    "parent_row_id",
    "underlying_task_id",
)


class UnionFind:
    def __init__(self, values: Iterable[str] = ()) -> None:
        self.parent: dict[str, str] = {}
        self.rank: dict[str, int] = {}
        for value in values:
            self.add(value)

    def add(self, value: str) -> None:
        if value not in self.parent:
            self.parent[value] = value
            self.rank[value] = 0

    def find(self, value: str) -> str:
        self.add(value)
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            parent = self.parent[value]
            self.parent[value] = root
            value = parent
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def _present(value: object) -> str:
    return str(value or "").strip()


def build_split_components(
    rows: Sequence[Mapping[str, object]],
    *,
    id_field: str = "benchmark_task_id",
    link_fields: Sequence[str] = LINK_FIELDS,
) -> dict[str, str]:
    """Return benchmark row ID -> stable connected-component split-group ID.

    Rows are linked transitively whenever any non-empty relationship field is
    shared. ``parent_row_id`` also links directly to a benchmark row when the
    referenced parent exists in the supplied frame.
    """

    ids = [_present(row.get(id_field)) for row in rows]
    if any(not value for value in ids):
        raise ValueError(f"every row must have non-empty {id_field}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {id_field}")
    uf = UnionFind(ids)
    by_value: dict[tuple[str, str], str] = {}
    id_set = set(ids)
    for row, row_id in zip(rows, ids):
        for field in link_fields:
            value = _present(row.get(field))
            if not value:
                continue
            key = (field, value)
            if key in by_value:
                uf.union(row_id, by_value[key])
            else:
                by_value[key] = row_id
        parent_id = _present(row.get("parent_row_id"))
        if parent_id in id_set:
            uf.union(row_id, parent_id)

    members: dict[str, list[str]] = defaultdict(list)
    for row_id in ids:
        members[uf.find(row_id)].append(row_id)
    group_id_by_root = {
        root: f"split::component::{stable_hash(sorted(component))[:24]}"
        for root, component in members.items()
    }
    return {row_id: group_id_by_root[uf.find(row_id)] for row_id in ids}


def build_split_components_v2(
    rows: Sequence[Mapping[str, object]],
    *,
    id_field: str = "benchmark_task_id",
    link_fields: Sequence[str] = IDENTITY_LINK_FIELDS_V2,
) -> dict[str, str]:
    """Build versioned split components from actual task identities.

    Unlike :func:`build_split_components`, this deliberately does not consume
    an inherited ``split_group_id`` or the legacy query-excluding
    ``task_signature``.  Both are historical metadata, not evidence that two
    task rows are the same underlying task.  The resulting mapping is suitable
    only for a new candidate split; callers must retain the old mapping.
    """
    return build_split_components(rows, id_field=id_field, link_fields=link_fields)


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


@dataclass(frozen=True)
class SplitAssignment:
    row_to_group: dict[str, str]
    group_to_split: dict[str, str]

    @property
    def row_to_split(self) -> dict[str, str]:
        return {row_id: self.group_to_split[group_id] for row_id, group_id in self.row_to_group.items()}


def assign_components(
    rows: Sequence[Mapping[str, object]],
    row_to_group: Mapping[str, str],
    *,
    ratios: Mapping[str, float] | None = None,
    seed: int = 20260719,
) -> SplitAssignment:
    """Assign whole components using deterministic greedy distribution balancing."""

    ratios = dict(ratios or {"train": 0.8, "dev": 0.1, "test": 0.1})
    if set(ratios) != {"train", "dev", "test"}:
        raise ValueError("ratios must contain exactly train/dev/test")
    if any(value <= 0 for value in ratios.values()) or abs(sum(ratios.values()) - 1.0) > 1e-9:
        raise ValueError("split ratios must be positive and sum to 1")

    groups: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        row_id = _present(row.get("benchmark_task_id"))
        if row_id not in row_to_group:
            raise ValueError(f"missing component for {row_id}")
        groups[row_to_group[row_id]].append(row)

    dimensions = ("task_type", "source_dataset", "candidate_count_bucket")
    global_counts: dict[str, Counter[str]] = {field: Counter() for field in dimensions}
    for row in rows:
        for field in dimensions:
            if field == "candidate_count_bucket":
                value = _present(row.get(field)) or candidate_bucket(row.get("candidate_count", 0))
            else:
                value = _present(row.get(field)) or "unknown"
            global_counts[field][value] += 1

    split_totals = Counter({name: 0 for name in ratios})
    split_counts: dict[str, dict[str, Counter[str]]] = {
        name: {field: Counter() for field in dimensions} for name in ratios
    }
    target_total = {name: len(rows) * ratio for name, ratio in ratios.items()}

    def group_profile(group_rows: Sequence[Mapping[str, object]]) -> dict[str, Counter[str]]:
        result = {field: Counter() for field in dimensions}
        for row in group_rows:
            for field in dimensions:
                if field == "candidate_count_bucket":
                    value = _present(row.get(field)) or candidate_bucket(row.get("candidate_count", 0))
                else:
                    value = _present(row.get(field)) or "unknown"
                result[field][value] += 1
        return result

    ordered = sorted(groups, key=lambda gid: (-len(groups[gid]), stable_hash([seed, gid])))
    group_to_split: dict[str, str] = {}
    for group_id in ordered:
        group_rows = groups[group_id]
        profile = group_profile(group_rows)

        def score(split_name: str) -> tuple[float, str]:
            # Evaluate the whole three-way state after the tentative placement.
            # Scoring only the candidate split makes the largest-ratio split look
            # best at every step and can starve dev/test completely.
            value = 0.0
            for evaluated_split in ratios:
                added = len(group_rows) if evaluated_split == split_name else 0
                after_total = split_totals[evaluated_split] + added
                total_scale = max(target_total[evaluated_split], 1.0)
                value += 4.0 * ((after_total - target_total[evaluated_split]) / total_scale) ** 2
                for field in dimensions:
                    for label, overall in global_counts[field].items():
                        target = overall * ratios[evaluated_split]
                        added_label = profile[field][label] if evaluated_split == split_name else 0
                        after = split_counts[evaluated_split][field][label] + added_label
                        value += ((after - target) / max(target, 1.0)) ** 2
            return value, stable_hash([seed, group_id, split_name])

        chosen = min(ratios, key=score)
        group_to_split[group_id] = chosen
        split_totals[chosen] += len(group_rows)
        for field in dimensions:
            split_counts[chosen][field].update(profile[field])

    return SplitAssignment(dict(row_to_group), group_to_split)


def reverse_leakage_audit(
    rows: Sequence[Mapping[str, object]],
    row_to_split: Mapping[str, str],
    *,
    fields: Sequence[str] = AUDIT_FIELDS,
) -> dict[str, list[dict[str, object]]]:
    """Report values that appear in more than one split for every hard audit key."""

    collisions: dict[str, list[dict[str, object]]] = {field: [] for field in fields}
    for field in fields:
        split_by_value: dict[str, set[str]] = defaultdict(set)
        rows_by_value: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            row_id = _present(row.get("benchmark_task_id"))
            split = row_to_split.get(row_id)
            if not split:
                raise ValueError(f"missing split assignment for {row_id}")
            value = _present(row.get(field))
            if not value:
                continue
            split_by_value[value].add(split)
            rows_by_value[value].append(row_id)
        for value in sorted(split_by_value):
            splits = sorted(split_by_value[value])
            if len(splits) > 1:
                collisions[field].append({"value": value, "splits": splits, "row_ids": sorted(rows_by_value[value])})
    return collisions


def audit_passed(collisions: Mapping[str, Sequence[object]]) -> bool:
    return not any(collisions.values())
