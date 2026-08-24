from __future__ import annotations

import argparse
import csv
import heapq
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from final_qa_v1_5_common import (
    OUTPUT_DIR,
    QA_HUMAN_FIELDS,
    QA_OUTPUT_FIELDS,
    V1_4_DEDUP_TRACE,
    V1_4_QA_FRAME,
    V1_4_TASK_TRACE,
    ensure_dir,
    now_text,
    stable_score,
    table_lines,
    write_csv,
    write_md,
)


TARGETS = {
    "clean_candidate_high_conf": 100,
    "removed_api_leak": 20,
    "removed_choice_space_invalid": 15,
    "removed_semantic_mismatch": 10,
    "removed_capability_mismatch": 5,
    "uncertain_semcap": 35,
    "uncertain_weak_api_leak": 10,
    "uncertain_ambiguous_service_leak": 5,
    "service_leak_only": 30,
    "duplicate_clean_candidate": 30,
}

POOL_TO_BUCKET = {
    "clean_candidate_high_conf": "clean_candidate",
    "removed_api_leak": "removed",
    "removed_choice_space_invalid": "removed",
    "removed_semantic_mismatch": "removed",
    "removed_capability_mismatch": "removed",
    "uncertain_semcap": "uncertain",
    "uncertain_weak_api_leak": "uncertain",
    "uncertain_ambiguous_service_leak": "uncertain",
    "service_leak_only": "service_leak_only",
    "duplicate_clean_candidate": "duplicate_clean_candidate",
}

REMOVED_POOLS = [
    "removed_api_leak",
    "removed_choice_space_invalid",
    "removed_semantic_mismatch",
    "removed_capability_mismatch",
]

UNCERTAIN_POOLS = [
    "uncertain_semcap",
    "uncertain_weak_api_leak",
    "uncertain_ambiguous_service_leak",
]


def push_candidate(store: dict[str, list[tuple[int, int, dict[str, str]]]], pool: str, row: dict[str, str], counter: int) -> None:
    target = TARGETS.get(pool, 0)
    if target <= 0:
        return
    cap = max(target * 30, target + 100)
    score = stable_score(pool, row.get("task_id", ""), row.get("query_text", ""))
    item = (-score, counter, dict(row))
    heap = store.setdefault(pool, [])
    if len(heap) < cap:
        heapq.heappush(heap, item)
    elif score < -heap[0][0]:
        heapq.heapreplace(heap, item)


def sorted_candidates(store: dict[str, list[tuple[int, int, dict[str, str]]]], pool: str) -> list[dict[str, str]]:
    items = store.get(pool, [])
    return [row for _neg_score, _counter, row in sorted(items, key=lambda item: (-item[0], item[1]))]


def make_qa_row(row: dict[str, str], item_index: int, qa_bucket: str, qa_subbucket: str) -> dict[str, Any]:
    out = {field: row.get(field, "") for field in QA_OUTPUT_FIELDS}
    out.update(
        {
            "qa_item_id": f"FQA-1.5-{item_index:03d}",
            "qa_bucket": qa_bucket,
            "qa_subbucket": qa_subbucket,
            "qa_group_id": f"QA-GROUP-{row.get('dedup_group_id', '')}" if qa_bucket == "duplicate_clean_candidate" else "",
            "is_representative_candidate": row.get("is_dedup_representative", ""),
        }
    )
    for field in QA_HUMAN_FIELDS:
        out[field] = ""
    return out


def select_rows(
    candidates: list[dict[str, str]],
    target: int,
    selected_task_ids: set[str],
    item_rows: list[dict[str, Any]],
    qa_bucket: str,
    qa_subbucket: str | None = None,
) -> int:
    added = 0
    for row in candidates:
        task_id = row.get("task_id", "")
        if not task_id or task_id in selected_task_ids:
            continue
        item_rows.append(make_qa_row(row, len(item_rows) + 1, qa_bucket, qa_subbucket or row.get("final_qa_pool", "")))
        selected_task_ids.add(task_id)
        added += 1
        if added >= target:
            break
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description="Build fixed-size final QA review set v1.5.")
    parser.add_argument("--qa-frame", type=Path, default=V1_4_QA_FRAME)
    parser.add_argument("--task-trace", type=Path, default=V1_4_TASK_TRACE)
    parser.add_argument("--dedup-trace", type=Path, default=V1_4_DEDUP_TRACE)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5.csv")
    parser.add_argument("--report", type=Path, default=OUTPUT_DIR / "final_qa_sampling_report_v1_5.md")
    args = parser.parse_args()

    for path in [args.qa_frame, args.task_trace, args.dedup_trace]:
        if not path.exists():
            raise FileNotFoundError(f"Missing required input: {path}")
    ensure_dir(args.output.parent)

    store: dict[str, list[tuple[int, int, dict[str, str]]]] = {}
    duplicate_best_by_group: dict[str, tuple[int, dict[str, str]]] = {}
    pool_counts = Counter()
    feature_counts = Counter()
    total_rows = 0

    with args.qa_frame.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for counter, row in enumerate(reader):
            total_rows += 1
            pool = row.get("final_qa_pool", "")
            pool_counts[pool] += 1
            if row.get("source_group"):
                feature_counts[f"source_group::{row.get('source_group')}"] += 1
            if row.get("prediction_level"):
                feature_counts[f"prediction_level::{row.get('prediction_level')}"] += 1
            if row.get("candidate_service_count") == "1":
                feature_counts["candidate_service_count::1"] += 1
            if row.get("query_mentions_any_gold_api") == "1":
                feature_counts["query_mentions_any_gold_api::1"] += 1
            if row.get("query_mentions_any_gold_service") == "1":
                feature_counts["query_mentions_any_gold_service::1"] += 1
            if pool == "duplicate_clean_candidate":
                group_id = row.get("dedup_group_id", "")
                if group_id:
                    score = stable_score("duplicate", group_id, row.get("task_id", ""))
                    old = duplicate_best_by_group.get(group_id)
                    if old is None or score < old[0]:
                        duplicate_best_by_group[group_id] = (score, dict(row))
            elif pool in TARGETS:
                push_candidate(store, pool, row, counter)

    selected_task_ids: set[str] = set()
    item_rows: list[dict[str, Any]] = []
    selection_counts = Counter()
    backfill_notes: list[str] = []

    added = select_rows(
        sorted_candidates(store, "clean_candidate_high_conf"),
        TARGETS["clean_candidate_high_conf"],
        selected_task_ids,
        item_rows,
        "clean_candidate",
        "clean_candidate_high_conf",
    )
    selection_counts["clean_candidate_high_conf"] = added

    removed_selected = 0
    for pool in REMOVED_POOLS:
        added = select_rows(
            sorted_candidates(store, pool),
            TARGETS[pool],
            selected_task_ids,
            item_rows,
            "removed",
            pool,
        )
        selection_counts[pool] += added
        removed_selected += added
        if added < TARGETS[pool]:
            backfill_notes.append(f"{pool}: target {TARGETS[pool]}, selected {added}; deficit backfilled from other removed buckets when possible.")
    removed_deficit = sum(TARGETS[pool] for pool in REMOVED_POOLS) - removed_selected
    if removed_deficit > 0:
        backfill_pool = []
        for pool in REMOVED_POOLS:
            backfill_pool.extend(sorted_candidates(store, pool))
        backfill_pool.sort(key=lambda row: stable_score("removed_backfill", row.get("task_id", "")))
        added = select_rows(backfill_pool, removed_deficit, selected_task_ids, item_rows, "removed")
        selection_counts["removed_backfill"] += added

    uncertain_selected = 0
    for pool in UNCERTAIN_POOLS:
        added = select_rows(
            sorted_candidates(store, pool),
            TARGETS[pool],
            selected_task_ids,
            item_rows,
            "uncertain",
            pool,
        )
        selection_counts[pool] += added
        uncertain_selected += added
        if added < TARGETS[pool]:
            backfill_notes.append(f"{pool}: target {TARGETS[pool]}, selected {added}; deficit backfilled from other uncertain buckets when possible.")
    uncertain_deficit = sum(TARGETS[pool] for pool in UNCERTAIN_POOLS) - uncertain_selected
    if uncertain_deficit > 0:
        backfill_pool = []
        for pool in UNCERTAIN_POOLS:
            backfill_pool.extend(sorted_candidates(store, pool))
        backfill_pool.sort(key=lambda row: stable_score("uncertain_backfill", row.get("task_id", "")))
        added = select_rows(backfill_pool, uncertain_deficit, selected_task_ids, item_rows, "uncertain")
        selection_counts["uncertain_backfill"] += added

    added = select_rows(
        sorted_candidates(store, "service_leak_only"),
        TARGETS["service_leak_only"],
        selected_task_ids,
        item_rows,
        "service_leak_only",
        "service_leak_only",
    )
    selection_counts["service_leak_only"] = added

    duplicate_candidates = [
        row for _score, row in sorted(duplicate_best_by_group.values(), key=lambda item: item[0])
    ]
    added = select_rows(
        duplicate_candidates,
        TARGETS["duplicate_clean_candidate"],
        selected_task_ids,
        item_rows,
        "duplicate_clean_candidate",
        "duplicate_clean_candidate",
    )
    selection_counts["duplicate_clean_candidate"] = added

    write_csv(args.output, item_rows, QA_OUTPUT_FIELDS)

    qa_bucket_counts = Counter(row.get("qa_bucket", "") for row in item_rows)
    qa_subbucket_counts = Counter(row.get("qa_subbucket", "") for row in item_rows)
    source_group_counts = Counter(row.get("source_group", "") for row in item_rows)
    prediction_counts = Counter(row.get("prediction_level", "") for row in item_rows)
    task_type_counts = Counter(row.get("task_type", "") for row in item_rows)
    selected_feature_counts = Counter()
    selected_feature_counts["candidate_service_count=1"] = sum(1 for row in item_rows if row.get("candidate_service_count") == "1")
    selected_feature_counts["candidate_service_count>1"] = sum(1 for row in item_rows if int(row.get("candidate_service_count") or 0) > 1)
    selected_feature_counts["candidate_api_count>gold_api_count"] = sum(
        1 for row in item_rows if int(row.get("candidate_api_count") or 0) > int(row.get("gold_api_count") or 0)
    )
    selected_feature_counts["query_mentions_any_gold_api=1"] = sum(
        1 for row in item_rows if row.get("query_mentions_any_gold_api") == "1"
    )
    selected_feature_counts["query_mentions_any_gold_service=1"] = sum(
        1 for row in item_rows if row.get("query_mentions_any_gold_service") == "1"
    )
    coverage_notes = []
    if "api" not in prediction_counts:
        coverage_notes.append(
            "- API-level coverage is unavailable in this v1.5 QA set because the v1.4 final QA sampling frame contains only `prediction_level=service` rows."
        )
    if selection_counts.get("removed_capability_mismatch", 0) == 0:
        coverage_notes.append(
            "- `removed_capability_mismatch` has zero available rows in v1.4, so its requested 5 slots were backfilled from other removed buckets."
        )
    report_lines = [
        "# Final QA Sampling Report v1.5",
        "",
        f"Generated time: {now_text()}",
        f"Input QA frame: `{args.qa_frame}`",
        f"Input task trace: `{args.task_trace}`",
        f"Input dedup trace: `{args.dedup_trace}`",
        f"Output review set: `{args.output}`",
        f"Input sampling frame rows: {total_rows}",
        f"Final QA review item count: {len(item_rows)}",
        "",
        "This is a fixed-size release-quality QA audit set. It is not a final clean dataset.",
        "",
        "## Requested Targets",
        "",
        *table_lines(TARGETS),
        "",
        "## Selected Counts",
        "",
        *table_lines(selection_counts),
        "",
        "## QA Bucket Distribution",
        "",
        *table_lines(qa_bucket_counts),
        "",
        "## QA Subbucket Distribution",
        "",
        *table_lines(qa_subbucket_counts),
        "",
        "## Source Group Coverage",
        "",
        *table_lines(source_group_counts),
        "",
        "## Prediction Level Coverage",
        "",
        *table_lines(prediction_counts),
        "",
        "## Task Type Coverage",
        "",
        *table_lines(task_type_counts),
        "",
        "## Selected Feature Coverage",
        "",
        *table_lines(selected_feature_counts),
        "",
        "## Coverage Notes",
        "",
        *(coverage_notes or ["- Requested coverage dimensions were represented where the v1.4 sampling frame contained available rows."]),
        "",
        "## Backfill Notes",
        "",
        *(backfill_notes or ["- No backfill was needed except naturally empty subbuckets were recorded as zero where applicable."]),
        "",
        "## Raw QA Pool Availability",
        "",
        *table_lines(pool_counts),
    ]
    write_md(args.report, report_lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
