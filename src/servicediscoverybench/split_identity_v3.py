"""Fail-closed split identity v3 construction for ServiceDiscoveryBench.

This module fixes the two identity mistakes observed in earlier split runs:

1. ``legacy task_signature`` is a template/dedup fingerprint and MUST NOT be
   treated as a task-family identity edge.
2. source-local identifiers such as ``source_query_id`` and ``source_task_id``
   MUST be namespaced by ``source_dataset`` before they are linked.

The code deliberately uses conservative scope rules for relationship fields.
If a value cannot be proven globally unique, it is namespaced by source.  A
``parent_row_id`` is linked globally only when it exactly references a known
``benchmark_task_id``; otherwise it is treated as source-local.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Iterable, Mapping, Sequence


DIAGNOSTIC_ONLY_FIELDS = ("task_signature", "split_group_id")
GLOBAL_CONTENT_FIELDS = ("query_signature", "review_content_fingerprint")
SOURCE_LOCAL_FIELDS = (
    "source_query_id",
    "source_task_id",
    "paired_task_group_id",
    "underlying_task_id",
)
PARENT_FIELD = "parent_row_id"


def _text(value: object) -> str:
    return str(value or "").strip()


def stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
            next_value = self.parent[value]
            self.parent[value] = root
            value = next_value
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


@dataclass(frozen=True)
class FieldScopeDecision:
    field: str
    scope: str
    rationale: str
    raw_distinct_values: int
    raw_cross_source_collision_keys: int
    raw_cross_source_affected_rows: int
    relation_edges_before_namespacing: int
    cross_source_relation_edges_before_namespacing: int
    relation_edges_after_namespacing: int
    cross_source_relation_edges_after_namespacing: int


@dataclass(frozen=True)
class IdentityBuildResult:
    row_to_group: dict[str, str]
    relation_edges: list[dict[str, str]]
    scope_decisions: list[FieldScopeDecision]
    collision_summary: dict[str, int]
    group_sizes: dict[str, int]


def _pairwise_star_edges(rows: Sequence[Mapping[str, object]], token_getter) -> tuple[int, int]:
    """Return total and cross-source edge counts using a deterministic star.

    A star is used rather than all-pairs so the edge count is linear and
    reproducible.  It is sufficient to connect a shared identity value into a
    single component.
    """

    first: dict[str, tuple[str, str]] = {}
    total = 0
    cross = 0
    for row in rows:
        row_id = _text(row.get("benchmark_task_id"))
        source = _text(row.get("source_dataset"))
        token = token_getter(row)
        if not token:
            continue
        if token in first:
            total += 1
            if first[token][1] != source:
                cross += 1
        else:
            first[token] = (row_id, source)
    return total, cross


def _raw_collision_stats(rows: Sequence[Mapping[str, object]], field: str) -> tuple[int, int]:
    sources_by_value: dict[str, set[str]] = defaultdict(set)
    row_count_by_value: Counter[str] = Counter()
    for row in rows:
        value = _text(row.get(field))
        if not value:
            continue
        sources_by_value[value].add(_text(row.get("source_dataset")))
        row_count_by_value[value] += 1
    collision_values = {value for value, sources in sources_by_value.items() if len(sources) > 1}
    return len(collision_values), sum(row_count_by_value[value] for value in collision_values)


def _scope_for_field(field: str) -> tuple[str, str]:
    if field in SOURCE_LOCAL_FIELDS:
        return (
            "SOURCE_LOCAL_IDENTITY",
            "Identifier is created inside a source and is namespaced as source_dataset::raw_id.",
        )
    if field in GLOBAL_CONTENT_FIELDS:
        return (
            "GLOBAL_EXACT_CONTENT_DUPLICATE",
            "Cryptographic content/signature field; exact equality is treated as duplicate-family evidence.",
        )
    if field == PARENT_FIELD:
        return (
            "GLOBAL_ROW_REFERENCE_OR_SOURCE_LOCAL",
            "Exact benchmark_task_id references are global; unresolved raw parent IDs are source-local.",
        )
    if field in DIAGNOSTIC_ONLY_FIELDS:
        return (
            "DIAGNOSTIC_ONLY",
            "Historical/template field; never contributes a split identity edge.",
        )
    return ("INVALID_OR_UNRESOLVED", "No approved identity scope rule; fail closed and do not link.")


def _raw_token(row: Mapping[str, object], field: str) -> str:
    return _text(row.get(field))


def _scoped_token(row: Mapping[str, object], field: str, benchmark_ids: set[str]) -> str:
    value = _text(row.get(field))
    if not value:
        return ""
    source = _text(row.get("source_dataset"))
    scope, _ = _scope_for_field(field)
    if scope == "SOURCE_LOCAL_IDENTITY":
        return f"{field}::source::{source}::{value}"
    if scope == "GLOBAL_EXACT_CONTENT_DUPLICATE":
        return f"{field}::global::{value}"
    if field == PARENT_FIELD:
        if value in benchmark_ids:
            return f"{field}::row::{value}"
        return f"{field}::source::{source}::{value}"
    return ""


def audit_identity_field_scopes(
    rows: Sequence[Mapping[str, object]],
    *,
    fields: Sequence[str] = (
        "source_query_id",
        "source_task_id",
        "query_signature",
        "review_content_fingerprint",
        "paired_task_group_id",
        "underlying_task_id",
        "parent_row_id",
        "task_signature",
        "split_group_id",
    ),
) -> list[FieldScopeDecision]:
    benchmark_ids = {_text(row.get("benchmark_task_id")) for row in rows}
    decisions: list[FieldScopeDecision] = []
    for field in fields:
        raw_values = {_text(row.get(field)) for row in rows if _text(row.get(field))}
        collision_keys, affected_rows = _raw_collision_stats(rows, field)
        before_total, before_cross = _pairwise_star_edges(rows, lambda row, f=field: _raw_token(row, f))
        after_total, after_cross = _pairwise_star_edges(
            rows, lambda row, f=field: _scoped_token(row, f, benchmark_ids)
        )
        scope, rationale = _scope_for_field(field)
        decisions.append(
            FieldScopeDecision(
                field=field,
                scope=scope,
                rationale=rationale,
                raw_distinct_values=len(raw_values),
                raw_cross_source_collision_keys=collision_keys,
                raw_cross_source_affected_rows=affected_rows,
                relation_edges_before_namespacing=before_total,
                cross_source_relation_edges_before_namespacing=before_cross,
                relation_edges_after_namespacing=after_total,
                cross_source_relation_edges_after_namespacing=after_cross,
            )
        )
    return decisions


def build_identity_v3(
    rows: Sequence[Mapping[str, object]],
    *,
    id_field: str = "benchmark_task_id",
) -> IdentityBuildResult:
    row_ids = [_text(row.get(id_field)) for row in rows]
    if any(not row_id for row_id in row_ids):
        raise ValueError(f"every row must have non-empty {id_field}")
    if len(row_ids) != len(set(row_ids)):
        raise ValueError(f"duplicate {id_field}")
    benchmark_ids = set(row_ids)
    uf = UnionFind(row_ids)
    relation_edges: list[dict[str, str]] = []
    first_by_token: dict[str, tuple[str, str]] = {}

    link_fields = SOURCE_LOCAL_FIELDS + GLOBAL_CONTENT_FIELDS + (PARENT_FIELD,)
    for row in rows:
        row_id = _text(row.get(id_field))
        source = _text(row.get("source_dataset"))
        for field in link_fields:
            token = _scoped_token(row, field, benchmark_ids)
            if not token:
                continue
            if token in first_by_token:
                left_id, left_source = first_by_token[token]
                uf.union(left_id, row_id)
                relation_edges.append(
                    {
                        "relation_type": field,
                        "scoped_relation_value": token,
                        "left_task_id": left_id,
                        "right_task_id": row_id,
                        "left_source": left_source,
                        "right_source": source,
                        "cross_source": str(left_source != source).lower(),
                    }
                )
            else:
                first_by_token[token] = (row_id, source)

        # A direct parent row reference is linked even if the raw parent field
        # was not repeated elsewhere.
        parent_id = _text(row.get(PARENT_FIELD))
        if parent_id and parent_id in benchmark_ids:
            uf.union(row_id, parent_id)
            token = f"{PARENT_FIELD}::row::{parent_id}"
            relation_edges.append(
                {
                    "relation_type": "parent_row_direct_reference",
                    "scoped_relation_value": token,
                    "left_task_id": parent_id,
                    "right_task_id": row_id,
                    "left_source": "",
                    "right_source": source,
                    "cross_source": "false",
                }
            )

    members: dict[str, list[str]] = defaultdict(list)
    for row_id in row_ids:
        members[uf.find(row_id)].append(row_id)
    group_by_root = {
        root: f"split::identity-v3::{stable_hash(sorted(component))[:24]}"
        for root, component in members.items()
    }
    row_to_group = {row_id: group_by_root[uf.find(row_id)] for row_id in row_ids}
    group_sizes = Counter(row_to_group.values())

    decisions = audit_identity_field_scopes(rows)
    summary = {
        "row_count": len(rows),
        "group_count": len(group_sizes),
        "max_group_size": max(group_sizes.values(), default=0),
        "source_local_cross_source_edges_after_namespacing": sum(
            1
            for edge in relation_edges
            if edge["relation_type"] in SOURCE_LOCAL_FIELDS and edge["cross_source"] == "true"
        ),
        "raw_identifier_collision_key_count": sum(
            decision.raw_cross_source_collision_keys
            for decision in decisions
            if decision.field in ("source_query_id", "source_task_id")
        ),
        "raw_identifier_affected_row_count": sum(
            decision.raw_cross_source_affected_rows
            for decision in decisions
            if decision.field in ("source_query_id", "source_task_id")
        ),
        "relation_edge_count_before_namespacing": sum(
            decision.relation_edges_before_namespacing
            for decision in decisions
            if decision.field in ("source_query_id", "source_task_id")
        ),
        "cross_source_relation_edge_count_before_namespacing": sum(
            decision.cross_source_relation_edges_before_namespacing
            for decision in decisions
            if decision.field in ("source_query_id", "source_task_id")
        ),
        "relation_edge_count_after_namespacing": sum(
            decision.relation_edges_after_namespacing
            for decision in decisions
            if decision.field in ("source_query_id", "source_task_id")
        ),
    }
    if summary["source_local_cross_source_edges_after_namespacing"] != 0:
        raise RuntimeError("source-local identity created cross-source edges after namespacing")
    if sum(group_sizes.values()) != len(rows):
        raise RuntimeError("identity groups do not cover every row exactly once")

    return IdentityBuildResult(
        row_to_group=row_to_group,
        relation_edges=relation_edges,
        scope_decisions=decisions,
        collision_summary=summary,
        group_sizes=dict(group_sizes),
    )


def scope_decisions_as_rows(decisions: Sequence[FieldScopeDecision]) -> list[dict[str, object]]:
    return [asdict(decision) for decision in decisions]
