from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from final_qa_v1_5d_common import (
    OUTPUT_DIR,
    QA_HUMAN_FIELDS,
    QA_OUTPUT_FIELDS,
    V14B_CLEAN_BUCKET,
    V14B_DEDUP_TRACE,
    V14B_QA_FRAME,
    V14B_TASK_TRACE,
    V15C_FAILURE_PATCH,
    distribution,
    ensure_dir,
    infer_clean_subbucket,
    normalize_review_row,
    now_text,
    read_csv,
    stable_score,
    table_lines,
    write_csv,
    write_md,
)


CURRENT_TARGET = 100
PREVIOUS_TARGET = 32
MIN_DUPLICATE_IN_CURRENT = 20


def load_by_task(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows = read_csv(path)
    return {row.get("task_id", ""): row for row in rows if row.get("task_id")}


def enrich_with_dedup(row: dict[str, str], dedup_by_task: dict[str, dict[str, str]]) -> dict[str, str]:
    out = dict(row)
    dedup = dedup_by_task.get(out.get("task_id", ""), {})
    for field in ["dedup_group_id", "dedup_group_size", "is_dedup_representative", "dedup_reason"]:
        if dedup.get(field):
            out[field] = dedup.get(field, "")
    return out


def enrich_with_failure(row: dict[str, str], failure_by_task: dict[str, dict[str, str]]) -> dict[str, str]:
    out = dict(row)
    failure = failure_by_task.get(out.get("task_id", ""), {})
    if failure:
        out["previous_v1_5c_qa_severity"] = failure.get("qa_severity", "")
        out["previous_v1_5c_failure_type"] = failure.get("primary_failure_type") or failure.get("failure_types_all", "")
        reason_parts = [
            failure.get("qa_item_id", ""),
            failure.get("manual_failure_reason", ""),
            failure.get("recommended_policy_action", ""),
        ]
        out["previous_v1_5c_reason"] = " | ".join(part for part in reason_parts if part)
    return out


def scan_task_trace_for_ids(path: Path, task_ids: set[str]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    if not path.exists() or not task_ids:
        return found
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            task_id = row.get("task_id", "")
            if task_id in task_ids:
                found[task_id] = dict(row)
                if len(found) == len(task_ids):
                    break
    return found


def select_current_from_clean_bucket(clean_rows: list[dict[str, str]], selected_ids: set[str]) -> list[dict[str, str]]:
    targets = {
        "random_clean_candidate": 25,
        "generic_search_or_news_or_image_risk": 20,
        "travel_place_hotel_restaurant_risk": 15,
        "weather_or_forecast_risk": 10,
        "translation_or_language_risk": 10,
        "domain_availability_or_finance_rate_risk": 10,
        "G3_or_composable_raw_risk": 10,
    }
    pools: dict[str, list[dict[str, str]]] = {key: [] for key in targets}
    for row in clean_rows:
        task_id = row.get("task_id", "")
        if not task_id or task_id in selected_ids:
            continue
        subbucket = infer_clean_subbucket(row)
        row = dict(row)
        row["_inferred_subbucket"] = subbucket
        pools.setdefault(subbucket, []).append(row)
    for pool_rows in pools.values():
        pool_rows.sort(
            key=lambda row: (
                0 if row.get("dedup_group_id") else 1,
                stable_score(row.get("_inferred_subbucket", ""), row.get("task_id", ""), row.get("query_text", "")),
            )
        )
    selected: list[dict[str, str]] = []
    for subbucket, target in targets.items():
        for row in pools.get(subbucket, []):
            if row.get("task_id") in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row.get("task_id", ""))
            if sum(1 for item in selected if item.get("_inferred_subbucket") == subbucket) >= target:
                break
    if len(selected) < CURRENT_TARGET:
        remaining = []
        for row in clean_rows:
            task_id = row.get("task_id", "")
            if not task_id or task_id in selected_ids:
                continue
            row = dict(row)
            row["_inferred_subbucket"] = infer_clean_subbucket(row)
            remaining.append(row)
        remaining.sort(key=lambda row: stable_score("current_backfill", row.get("task_id", ""), row.get("query_text", "")))
        for row in remaining:
            selected.append(row)
            selected_ids.add(row.get("task_id", ""))
            if len(selected) >= CURRENT_TARGET:
                break
    return selected[:CURRENT_TARGET]


def force_duplicate_coverage(current_rows: list[dict[str, str]], clean_rows: list[dict[str, str]], selected_ids: set[str]) -> list[dict[str, str]]:
    dup_count = sum(1 for row in current_rows if row.get("dedup_group_id"))
    if dup_count >= MIN_DUPLICATE_IN_CURRENT:
        return current_rows
    replacement_pool = [
        dict(row, _inferred_subbucket=infer_clean_subbucket(row))
        for row in clean_rows
        if row.get("dedup_group_id") and row.get("task_id") not in selected_ids
    ]
    replacement_pool.sort(key=lambda row: stable_score("duplicate_backfill", row.get("dedup_group_id", ""), row.get("task_id", "")))
    if not replacement_pool:
        return current_rows
    removable_indexes = [
        idx for idx in range(len(current_rows) - 1, -1, -1)
        if not current_rows[idx].get("dedup_group_id")
    ]
    for repl, idx in zip(replacement_pool, removable_indexes):
        old_task = current_rows[idx].get("task_id", "")
        if old_task in selected_ids:
            selected_ids.remove(old_task)
        current_rows[idx] = repl
        selected_ids.add(repl.get("task_id", ""))
        dup_count += 1
        if dup_count >= MIN_DUPLICATE_IN_CURRENT:
            break
    return current_rows


def build_from_existing_frame(frame_rows: list[dict[str, str]], failure_by_task: dict[str, dict[str, str]], dedup_by_task: dict[str, dict[str, str]]) -> tuple[list[dict[str, str]], str]:
    previous_rows: list[dict[str, str]] = []
    current_rows: list[dict[str, str]] = []
    for row in frame_rows:
        enriched = enrich_with_failure(enrich_with_dedup(row, dedup_by_task), failure_by_task)
        if row.get("qa_frame_bucket") == "previous_failed_clean_candidate_regression":
            previous_rows.append(enriched)
        elif row.get("qa_frame_bucket") == "new_or_surviving_v14b_clean_candidate":
            enriched["_inferred_subbucket"] = infer_clean_subbucket(enriched)
            current_rows.append(enriched)
    previous_rows = sorted(previous_rows, key=lambda row: row.get("previous_qa_item_id", row.get("task_id", "")))[:PREVIOUS_TARGET]
    current_rows = sorted(current_rows, key=lambda row: stable_score("frame_current", row.get("task_id", "")))[:CURRENT_TARGET]
    selected_ids = {row.get("task_id", "") for row in previous_rows + current_rows if row.get("task_id")}
    if V14B_CLEAN_BUCKET.exists():
        clean_rows = [enrich_with_dedup(row, dedup_by_task) for row in read_csv(V14B_CLEAN_BUCKET)]
        current_rows = force_duplicate_coverage(current_rows, clean_rows, selected_ids)
    out: list[dict[str, str]] = []
    for row in previous_rows:
        out.append(normalize_review_row(row, len(out) + 1, "previous_failed_regression", "expected_moved_out_of_clean", "not_clean_candidate"))
    for row in current_rows[:CURRENT_TARGET]:
        out.append(normalize_review_row(row, len(out) + 1, "current_clean_candidate_audit", row.get("_inferred_subbucket") or infer_clean_subbucket(row), "clean_candidate_under_review"))
    return out, "existing_v1_4b_impacted_qa_frame"


def build_without_frame(failure_by_task: dict[str, dict[str, str]], dedup_by_task: dict[str, dict[str, str]]) -> tuple[list[dict[str, str]], str]:
    failure_ids = set(failure_by_task)
    task_rows = scan_task_trace_for_ids(V14B_TASK_TRACE, failure_ids)
    selected_ids: set[str] = set()
    out: list[dict[str, str]] = []
    for task_id in sorted(failure_ids):
        row = task_rows.get(task_id, failure_by_task.get(task_id, {}))
        row = enrich_with_failure(enrich_with_dedup(row, dedup_by_task), failure_by_task)
        out.append(normalize_review_row(row, len(out) + 1, "previous_failed_regression", "expected_moved_out_of_clean", "not_clean_candidate"))
        selected_ids.add(task_id)
    clean_rows = [enrich_with_dedup(row, dedup_by_task) for row in read_csv(V14B_CLEAN_BUCKET)]
    current_rows = select_current_from_clean_bucket(clean_rows, selected_ids)
    current_rows = force_duplicate_coverage(current_rows, clean_rows, selected_ids)
    for row in current_rows:
        out.append(normalize_review_row(row, len(out) + 1, "current_clean_candidate_audit", row.get("_inferred_subbucket") or infer_clean_subbucket(row), "clean_candidate_under_review"))
    return out, "rebuilt_from_task_trace_and_clean_bucket"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build v1.5d impacted clean-candidate QA review set.")
    parser.add_argument("--qa-frame", type=Path, default=V14B_QA_FRAME)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5d.csv")
    parser.add_argument("--report", type=Path, default=OUTPUT_DIR / "final_qa_sampling_report_v1_5d.md")
    args = parser.parse_args()
    ensure_dir(args.output.parent)

    if not V15C_FAILURE_PATCH.exists():
        raise FileNotFoundError(f"Missing v1.5c failure patch: {V15C_FAILURE_PATCH}")
    if not V14B_DEDUP_TRACE.exists():
        raise FileNotFoundError(f"Missing v1.4b dedup trace: {V14B_DEDUP_TRACE}")

    failure_by_task = load_by_task(V15C_FAILURE_PATCH)
    dedup_by_task = load_by_task(V14B_DEDUP_TRACE)
    if args.qa_frame.exists():
        review_rows, build_source = build_from_existing_frame(read_csv(args.qa_frame), failure_by_task, dedup_by_task)
    else:
        review_rows, build_source = build_without_frame(failure_by_task, dedup_by_task)

    # Enforce no repeated task_id while preserving order.
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for row in review_rows:
        task_id = row.get("task_id", "")
        if task_id and task_id in seen:
            continue
        if task_id:
            seen.add(task_id)
        for field in QA_HUMAN_FIELDS:
            row[field] = ""
        deduped.append(row)
    review_rows = deduped
    write_csv(args.output, review_rows, QA_OUTPUT_FIELDS)

    previous = [row for row in review_rows if row.get("qa_bucket") == "previous_failed_regression"]
    current = [row for row in review_rows if row.get("qa_bucket") == "current_clean_candidate_audit"]
    duplicates = [row for row in current if row.get("dedup_group_id")]
    previous_still_clean = [row for row in previous if row.get("v1_4b_dryrun_decision") == "dryrun_clean_candidate"]
    human_nonblank = [
        {"qa_item_id": row.get("qa_item_id", ""), "field": field}
        for row in review_rows
        for field in QA_HUMAN_FIELDS
        if row.get(field)
    ]

    lines = [
        "# Final QA Sampling Report v1.5d",
        "",
        f"Generated time: {now_text()}",
        f"Input QA frame: `{args.qa_frame}`",
        f"Build source: `{build_source}`",
        f"Output CSV: `{args.output}`",
        "",
        "This package is impacted clean-candidate QA only. It is not a final clean dataset, split, baseline, or training run.",
        "",
        "## Counts",
        "",
        f"- total review items: {len(review_rows)}",
        f"- previous_failed_regression: {len(previous)}",
        f"- current_clean_candidate_audit: {len(current)}",
        f"- duplicate samples inside current clean candidate audit: {len(duplicates)}",
        f"- previous failures still clean in v1.4b review set: {len(previous_still_clean)}",
        f"- nonblank human QA fields in output: {len(human_nonblank)}",
        "",
        "## QA Bucket Distribution",
        "",
        *table_lines(distribution(review_rows, "qa_bucket")),
        "",
        "## QA Subbucket Distribution",
        "",
        *table_lines(distribution(review_rows, "qa_subbucket")),
        "",
        "## Source Group Distribution",
        "",
        *table_lines(distribution(review_rows, "source_group")),
        "",
        "## Task Type Distribution",
        "",
        *table_lines(distribution(review_rows, "task_type")),
        "",
        "## Prediction Level Distribution",
        "",
        *table_lines(distribution(review_rows, "prediction_level")),
        "",
        "## Notes",
        "",
        "- The 32 previous failed samples are expected to be outside `dryrun_clean_candidate`.",
        "- The 100 current clean candidates are for human QA, not automatic approval.",
        "- Human QA fields are intentionally blank.",
        "- API-level final readiness is not certified by this package.",
    ]
    if previous_still_clean:
        lines.extend(["", "## Regression Not Fixed", "", *[f"- `{row.get('task_id')}`" for row in previous_still_clean]])
    write_md(args.report, lines)

    print(f"review_item_count={len(review_rows)}")
    print(f"previous_failed_regression_count={len(previous)}")
    print(f"current_clean_candidate_audit_count={len(current)}")
    print(f"duplicate_samples_in_current_count={len(duplicates)}")
    print(f"source_group_distribution={dict(Counter(row.get('source_group', '') for row in review_rows))}")
    print(f"task_type_distribution={dict(Counter(row.get('task_type', '') for row in review_rows))}")
    print(f"prediction_level_distribution={dict(Counter(row.get('prediction_level', '') for row in review_rows))}")
    print(f"all_32_previous_failures_not_clean={len(previous_still_clean) == 0 and len(previous) == 32}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
