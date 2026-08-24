"""Task-balanced MachineChallenge-v1.2 construction.

The challenge is a machine-mined *diagnostic*, not a human-validated negative
benchmark.  Every added item is labelled ``UNJUDGED_MACHINE_CANDIDATE``.  The
builder selects queries before looking at future model outputs, uses a fixed
seed, enforces a six-task quota, and constructs each candidate set with the
OR-aware size rule:

    N_i = max(10, a_i + r_i)

where ``a_i`` is the number of distinct accepted/reference IDs displayed and
``r_i`` is the largest acceptable solution size.  With a single canonical
solution this reduces to ``max(10, 2*g_i)``.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, asdict
import hashlib
import json
import random
from typing import Iterable, Iterator, Mapping, Sequence

from .cardinality_policy import acceptable_gold_sets
from .joint_split_optimizer_v3 import TASKS
from .split_identity_v3 import stable_hash


DEFAULT_METHOD_ORDER = (
    "same_service_sibling",
    "sibling",
    "schema_overlap",
    "schema",
    "dense",
    "bm25",
    "local_hashing",
    "same_category",
    "category",
    "same_collection",
    "same_provider",
    "frozen_baseline_high_rank",
    "baseline_high_rank",
    "composable_subgoal_neighbor",
    "subgoal",
    "unknown",
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _candidate_ids(row: Mapping[str, object]) -> list[str]:
    target = _text(row.get("prediction_target"))
    field = "candidate_services_json" if target == "service" else "candidate_apis_json"
    value = row.get(field)
    parsed = value if isinstance(value, list) else json.loads(str(value or "[]"))
    return [str(item) for item in parsed]


def reference_gold_union(row: Mapping[str, object]) -> tuple[list[str], int]:
    alternatives = acceptable_gold_sets(row)
    union = list(dict.fromkeys(item for solution in alternatives for item in solution))
    largest_solution = max((len(solution) for solution in alternatives), default=0)
    if not union or largest_solution <= 0:
        raise ValueError(f"row {_text(row.get('benchmark_task_id'))} has no reference Gold")
    return union, largest_solution


def final_candidate_count(row: Mapping[str, object]) -> int:
    accepted_union, largest_solution = reference_gold_union(row)
    return max(10, len(accepted_union) + largest_solution)


def _source_round_robin(rows: Sequence[Mapping[str, object]], seed_key: str) -> list[Mapping[str, object]]:
    by_source: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_source[_text(row.get("source_dataset")) or "UNKNOWN"].append(row)
    queues: dict[str, deque[Mapping[str, object]]] = {}
    for source, values in by_source.items():
        queues[source] = deque(sorted(values, key=lambda row: stable_hash([seed_key, source, _text(row.get("benchmark_task_id"))])))
    ordered: list[Mapping[str, object]] = []
    sources = sorted(queues)
    while any(queues[source] for source in sources):
        for source in sources:
            if queues[source]:
                ordered.append(queues[source].popleft())
    return ordered


def select_task_balanced_queries(
    test_rows: Sequence[Mapping[str, object]],
    *,
    target_total: int = 197,
    minimum_per_task: int = 20,
    single_api_share_cap: float = 0.50,
    seed: int = 147949090,
    reserve_total: int | None = None,
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]], dict[str, object]]:
    by_task: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in test_rows:
        task = _text(row.get("task_type"))
        if task in TASKS:
            by_task[task].append(row)
    ordered_by_task = {
        task: _source_round_robin(values, f"machine-query::{seed}::{task}") for task, values in by_task.items()
    }

    selected: list[Mapping[str, object]] = []
    used: set[str] = set()
    counts = Counter()

    for task in TASKS:
        take = min(minimum_per_task, len(ordered_by_task.get(task, [])))
        for row in ordered_by_task.get(task, [])[:take]:
            task_id = _text(row.get("benchmark_task_id"))
            selected.append(row)
            used.add(task_id)
            counts[task] += 1

    cap_single_api = math_floor(target_total * single_api_share_cap)
    task_cycle = deque(TASKS)
    positions = {task: counts[task] for task in TASKS}
    safety = 0
    while len(selected) < target_total and safety < target_total * len(TASKS) * 20:
        safety += 1
        task = task_cycle[0]
        task_cycle.rotate(-1)
        if task == "single_api_recommendation" and counts[task] >= cap_single_api:
            continue
        values = ordered_by_task.get(task, [])
        pos = positions[task]
        while pos < len(values) and _text(values[pos].get("benchmark_task_id")) in used:
            pos += 1
        positions[task] = pos
        if pos >= len(values):
            if all(positions[t] >= len(ordered_by_task.get(t, [])) or (t == "single_api_recommendation" and counts[t] >= cap_single_api) for t in TASKS):
                break
            continue
        row = values[pos]
        positions[task] += 1
        task_id = _text(row.get("benchmark_task_id"))
        selected.append(row)
        used.add(task_id)
        counts[task] += 1

    remaining = [
        row
        for task in TASKS
        for row in ordered_by_task.get(task, [])
        if _text(row.get("benchmark_task_id")) not in used
    ]
    remaining.sort(key=lambda row: stable_hash(["machine-reserve", seed, _text(row.get("benchmark_task_id"))]))
    reserve_size = reserve_total if reserve_total is not None else target_total
    reserve = remaining[:reserve_size]

    status = {
        "target_total": target_total,
        "selected_total": len(selected),
        "reserve_total": len(reserve),
        "task_counts": dict(counts),
        "single_api_share": counts["single_api_recommendation"] / max(len(selected), 1),
        "six_tasks_present": all(counts[task] > 0 for task in TASKS),
        "minimum_per_task_satisfied_where_available": all(
            counts[task] >= min(minimum_per_task, len(ordered_by_task.get(task, []))) for task in TASKS
        ),
        "single_api_cap_satisfied": counts["single_api_recommendation"] <= cap_single_api,
        "seed": seed,
    }
    status["query_selection_valid"] = bool(
        len(selected) == target_total
        and status["six_tasks_present"]
        and status["minimum_per_task_satisfied_where_available"]
        and status["single_api_cap_satisfied"]
    )
    return selected, reserve, status


def math_floor(value: float) -> int:
    # Kept local to avoid a heavy import solely for one deterministic operation.
    return int(value // 1)


def _methods(record: Mapping[str, object]) -> list[str]:
    result: list[str] = []
    sources = record.get("retrieval_sources")
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except Exception:
            sources = [sources]
    if isinstance(sources, list):
        for item in sources:
            if isinstance(item, Mapping):
                method = _text(item.get("method") or item.get("miner") or item.get("source") or item.get("type"))
            else:
                method = _text(item)
            if method:
                result.append(method)
    for key in ("method", "miner", "candidate_source", "evidence_source"):
        method = _text(record.get(key))
        if method:
            result.append(method)
    return list(dict.fromkeys(result or ["unknown"]))


def _rank_score(record: Mapping[str, object]) -> tuple[float, float, str]:
    rank = math_inf()
    score = -math_inf()
    for key in ("rank", "retrieval_rank", "source_rank", "bm25_rank", "dense_rank"):
        try:
            if record.get(key) not in (None, ""):
                rank = min(rank, float(record[key]))
        except Exception:
            pass
    for key in ("score", "retrieval_score", "bm25_score", "dense_score", "similarity"):
        try:
            if record.get(key) not in (None, ""):
                score = max(score, float(record[key]))
        except Exception:
            pass
    candidate_id = _text(record.get("candidate_id"))
    return rank, -score, candidate_id


def math_inf() -> float:
    return float("inf")


def stream_evidence_for_queries(
    evidence_path,
    query_ids: set[str],
) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    with open(evidence_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            query_id = _text(record.get("query_id") or record.get("benchmark_task_id"))
            if query_id in query_ids:
                result[query_id].append(record)
    return result


def _normalized_method(method: str) -> str:
    value = method.casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "same_service": "same_service_sibling",
        "same_service_siblings": "same_service_sibling",
        "sibling_api": "same_service_sibling",
        "schema_similarity": "schema_overlap",
        "same_category_collection": "same_category",
        "character_hashing": "local_hashing",
        "hashing": "local_hashing",
        "baseline_error": "frozen_baseline_high_rank",
        "subgoal_neighbor": "composable_subgoal_neighbor",
    }
    return aliases.get(value, value or "unknown")


def select_machine_candidates(
    row: Mapping[str, object],
    evidence_records: Sequence[Mapping[str, object]],
    catalog_ids: set[str],
    *,
    method_order: Sequence[str] = DEFAULT_METHOD_ORDER,
    seed: int = 147949090,
) -> tuple[list[str], dict[str, object]]:
    accepted_ids, largest_solution = reference_gold_union(row)
    missing_gold = [candidate_id for candidate_id in accepted_ids if candidate_id not in catalog_ids]
    if missing_gold:
        return [], {"status": "GOLD_NOT_IN_CATALOG", "missing_gold": missing_gold}
    target_total = max(10, len(accepted_ids) + largest_solution)
    needed = target_total - len(accepted_ids)

    candidate_info: dict[str, dict[str, object]] = {}
    for record in evidence_records:
        candidate_id = _text(record.get("candidate_id"))
        if not candidate_id or candidate_id in accepted_ids or candidate_id not in catalog_ids:
            continue
        info = candidate_info.setdefault(
            candidate_id,
            {"candidate_id": candidate_id, "methods": set(), "records": [], "method_records": defaultdict(list)},
        )
        normalized_methods = [_normalized_method(method) for method in _methods(record)]
        info["methods"].update(normalized_methods)  # type: ignore[union-attr]
        info["records"].append(dict(record))  # type: ignore[union-attr]
        for method in normalized_methods:
            info["method_records"][method].append(dict(record))  # type: ignore[index,union-attr]

    queues: dict[str, deque[str]] = {}
    all_methods = list(
        dict.fromkeys(
            [_normalized_method(method) for method in method_order]
            + sorted({method for info in candidate_info.values() for method in info["methods"]})
        )
    )
    for method in all_methods:
        ids = [candidate_id for candidate_id, info in candidate_info.items() if method in info["methods"]]

        def method_rank(candidate_id: str) -> tuple[float, float, str]:
            info = candidate_info[candidate_id]
            records = info["method_records"].get(method) or info["records"]  # type: ignore[index,union-attr]
            return min((_rank_score(record) for record in records), default=(math_inf(), math_inf(), candidate_id))

        ids.sort(key=method_rank)
        queues[method] = deque(ids)

    chosen: list[str] = []
    chosen_set: set[str] = set()
    selected_source: dict[str, str] = {}
    while len(chosen) < needed and any(queues[method] for method in all_methods):
        progress = False
        for method in all_methods:
            queue = queues[method]
            while queue and queue[0] in chosen_set:
                queue.popleft()
            if not queue:
                continue
            candidate_id = queue.popleft()
            if candidate_id in chosen_set:
                continue
            chosen.append(candidate_id)
            chosen_set.add(candidate_id)
            selected_source[candidate_id] = method
            progress = True
            if len(chosen) >= needed:
                break
        if not progress:
            break

    if len(chosen) < needed:
        return [], {
            "status": "INSUFFICIENT_MACHINE_CANDIDATES",
            "target_total": target_total,
            "accepted_count": len(accepted_ids),
            "needed_unjudged": needed,
            "available_unjudged": len(candidate_info),
            "selected_unjudged": len(chosen),
        }

    final_ids = list(accepted_ids) + chosen
    rng_seed = int(hashlib.sha256(f"{seed}\0{_text(row.get('benchmark_task_id'))}".encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(rng_seed)
    rng.shuffle(final_ids)
    return final_ids, {
        "status": "READY",
        "target_total": target_total,
        "accepted_count": len(accepted_ids),
        "largest_solution_size": largest_solution,
        "selected_unjudged": len(chosen),
        "selected_source_by_candidate": selected_source,
        "candidate_order_hash": stable_hash(final_ids),
        "seed": seed,
    }


@dataclass
class MachineChallengeBuildResult:
    tasks: list[dict[str, object]]
    candidates: list[dict[str, object]]
    attrition: list[dict[str, object]]
    status: dict[str, object]
    source_distribution: list[dict[str, object]]


def build_machine_challenge(
    main_rows: Sequence[Mapping[str, object]],
    reserve_rows: Sequence[Mapping[str, object]],
    evidence_by_query: Mapping[str, Sequence[Mapping[str, object]]],
    catalog: Mapping[str, Mapping[str, object]],
    *,
    target_total: int = 197,
    minimum_per_task: int = 20,
    seed: int = 147949090,
) -> MachineChallengeBuildResult:
    catalog_ids = set(catalog)
    queue = list(main_rows) + list(reserve_rows)
    available_task_counts = Counter(_text(row.get("task_type")) for row in queue)
    tasks: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    attrition: list[dict[str, object]] = []
    source_counts = Counter()
    task_counts = Counter()
    used_reserve = 0

    for index, row in enumerate(queue):
        if len(tasks) >= target_total:
            break
        task_id = _text(row.get("benchmark_task_id"))
        final_ids, info = select_machine_candidates(
            row,
            evidence_by_query.get(task_id, []),
            catalog_ids,
            seed=seed,
        )
        if not final_ids:
            attrition.append(
                {
                    "benchmark_task_id": task_id,
                    "task_type": _text(row.get("task_type")),
                    "source_dataset": _text(row.get("source_dataset")),
                    "selection_stage": "MAIN" if index < len(main_rows) else "RESERVE",
                    "reason": info.get("status"),
                    "details_json": json.dumps(info, ensure_ascii=False, sort_keys=True),
                }
            )
            continue
        if index >= len(main_rows):
            used_reserve += 1
        accepted_ids, _ = reference_gold_union(row)
        accepted_set = set(accepted_ids)
        candidate_docs: list[dict[str, object]] = []
        for candidate_id in final_ids:
            document = dict(catalog[candidate_id])
            document.setdefault("candidate_id", candidate_id)
            candidate_docs.append(document)
            candidates.append(
                {
                    "benchmark_task_id": task_id,
                    "candidate_id": candidate_id,
                    "judgment": "REFERENCE_GOLD" if candidate_id in accepted_set else "UNJUDGED_MACHINE_CANDIDATE",
                    "candidate_source": "REFERENCE_GOLD" if candidate_id in accepted_set else info["selected_source_by_candidate"].get(candidate_id, "unknown"),
                    "candidate_order": final_ids.index(candidate_id),
                }
            )
            if candidate_id not in accepted_set:
                source_counts[info["selected_source_by_candidate"].get(candidate_id, "unknown")] += 1
        task_type = _text(row.get("task_type"))
        task_counts[task_type] += 1
        tasks.append(
            {
                "machine_challenge_id": f"machinechallenge-v1.2::{task_id}",
                "benchmark_task_id": task_id,
                "task_type": task_type,
                "source_dataset": _text(row.get("source_dataset")),
                "prediction_target": _text(row.get("prediction_target")),
                "query_text": _text(row.get("query_text")),
                "candidate_count": len(final_ids),
                "candidate_ids_json": json.dumps(final_ids, ensure_ascii=False),
                "candidate_documents_json": json.dumps(candidate_docs, ensure_ascii=False),
                "reference_gold_sets_json": json.dumps(acceptable_gold_sets(row), ensure_ascii=False),
                "candidate_order_hash": info["candidate_order_hash"],
                "machine_candidate_judgment_status": "UNJUDGED",
                "construction_info_json": json.dumps(info, ensure_ascii=False, sort_keys=True),
            }
        )

    selected_single_api = task_counts["single_api_recommendation"]
    minimum_evidence = {
        task: {
            "actual": task_counts[task],
            "required": min(minimum_per_task, available_task_counts[task]),
            "available": available_task_counts[task],
        }
        for task in TASKS
    }
    status = {
        "target_query_count": target_total,
        "actual_query_count": len(tasks),
        "task_counts": dict(task_counts),
        "six_tasks_present": all(task_counts[task] > 0 for task in TASKS),
        "minimum_per_task": minimum_per_task,
        "minimum_per_task_evidence": minimum_evidence,
        "minimum_per_task_satisfied_where_available": all(
            task_counts[task] >= min(minimum_per_task, available_task_counts[task]) for task in TASKS
        ),
        "single_api_share": selected_single_api / max(len(tasks), 1),
        "single_api_cap_satisfied": selected_single_api / max(len(tasks), 1) <= 0.50 + 1e-12,
        "reserve_queries_used": used_reserve,
        "attrition_count": len(attrition),
        "formal_hard_negative_count": 0,
        "human_validated_negative_count": 0,
        "candidate_judgment": "UNJUDGED_MACHINE_CANDIDATE",
        "seed": seed,
    }
    status["machine_challenge_ready"] = bool(
        len(tasks) == target_total
        and status["six_tasks_present"]
        and status["minimum_per_task_satisfied_where_available"]
        and status["single_api_cap_satisfied"]
    )
    source_distribution = [
        {"candidate_source": source, "candidate_count": count}
        for source, count in sorted(source_counts.items())
    ]
    return MachineChallengeBuildResult(tasks, candidates, attrition, status, source_distribution)
