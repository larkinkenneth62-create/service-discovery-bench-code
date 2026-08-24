from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from final_qa_v1_5e_common import (
    OUTPUT_DIR,
    QA_HUMAN_FIELDS,
    QA_OUTPUT_FIELDS,
    QUOTAS,
    V14B_DEDUP_TRACE,
    V14C_TASK_TRACE,
    distribution,
    ensure_dir,
    infer_risk_subbucket,
    normalize_review_row,
    now_text,
    read_csv,
    stable_score,
    table_lines,
    write_csv,
    write_md,
)


def load_dedup_by_task(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows = read_csv(path)
    return {row.get("task_id", ""): row for row in rows if row.get("task_id")}


def load_clean_pool(task_trace: Path, dedup_by_task: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    pool: list[dict[str, str]] = []
    with task_trace.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("dryrun_decision_v1_4c") != "dryrun_clean_candidate":
                continue
            out = dict(row)
            dedup = dedup_by_task.get(out.get("task_id", ""), {})
            for field in ["dedup_group_id", "dedup_group_size", "is_dedup_representative", "dedup_reason"]:
                if dedup.get(field):
                    out[field] = dedup[field]
            subbucket, hits = infer_risk_subbucket(out)
            out["_qa_subbucket"] = subbucket
            out["_risk_keywords"] = ";".join(hits)
            pool.append(out)
    return pool


def select_rows(pool: list[dict[str, str]]) -> tuple[list[dict[str, str]], Counter, list[str]]:
    selected: list[dict[str, str]] = []
    selected_ids: set[str] = set()
    selection_counts = Counter()
    notes: list[str] = []
    pools: dict[str, list[dict[str, str]]] = {key: [] for key in QUOTAS}
    for row in pool:
        pools.setdefault(row.get("_qa_subbucket", "random_clean_candidate"), []).append(row)
    for key, rows in pools.items():
        rows.sort(
            key=lambda row: (
                stable_score(key, row.get("task_id", ""), row.get("query_text", "")),
            )
        )
    # Select duplicate quota first so it cannot be displaced by broader topical pools.
    order = ["duplicate_or_near_duplicate_risk"] + [key for key in QUOTAS if key != "duplicate_or_near_duplicate_risk"]
    for subbucket in order:
        target = QUOTAS[subbucket]
        added = 0
        for row in pools.get(subbucket, []):
            task_id = row.get("task_id", "")
            if not task_id or task_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(task_id)
            added += 1
            if added >= target:
                break
        selection_counts[subbucket] = added
        if added < target:
            notes.append(f"{subbucket}: target {target}, selected {added}; deficit will be backfilled from remaining high-risk clean candidates.")
    if len(selected) < 100:
        remaining = [row for row in pool if row.get("task_id") and row.get("task_id") not in selected_ids]
        remaining.sort(key=lambda row: stable_score("v1_5e_backfill", row.get("_qa_subbucket", ""), row.get("task_id", "")))
        for row in remaining:
            selected.append(row)
            selected_ids.add(row.get("task_id", ""))
            selection_counts[f"backfill::{row.get('_qa_subbucket', '')}"] += 1
            if len(selected) >= 100:
                break
    return selected[:100], selection_counts, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Build v1.5e 100-row clean-candidate QA review set.")
    parser.add_argument("--task-trace", type=Path, default=V14C_TASK_TRACE)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5e.csv")
    parser.add_argument("--report", type=Path, default=OUTPUT_DIR / "final_qa_sampling_report_v1_5e.md")
    args = parser.parse_args()
    if not args.task_trace.exists():
        raise FileNotFoundError(f"Missing v1.4c task trace: {args.task_trace}")
    ensure_dir(args.output.parent)
    dedup_by_task = load_dedup_by_task(V14B_DEDUP_TRACE)
    pool = load_clean_pool(args.task_trace, dedup_by_task)
    selected, selection_counts, notes = select_rows(pool)
    if len(selected) != 100:
        raise RuntimeError(f"Expected exactly 100 v1.5e QA rows, got {len(selected)}")
    review_rows = []
    for i, row in enumerate(selected, start=1):
        review_rows.append(
            normalize_review_row(
                row,
                i,
                row.get("_qa_subbucket", "random_clean_candidate"),
                f"risk-aware stratified sample from v1.4c clean candidate pool; target_subbucket={row.get('_qa_subbucket', '')}",
                row.get("_risk_keywords", "").split(";") if row.get("_risk_keywords") else [],
            )
        )
    for row in review_rows:
        for field in QA_HUMAN_FIELDS:
            row[field] = ""
    write_csv(args.output, review_rows, QA_OUTPUT_FIELDS)
    duplicate_count = sum(1 for row in review_rows if row.get("dedup_group_id"))
    nonblank_human = sum(1 for row in review_rows for field in QA_HUMAN_FIELDS if row.get(field))
    lines = [
        "# Final QA Sampling Report v1.5e",
        "",
        f"Generated time: {now_text()}",
        f"Input task trace: `{args.task_trace}`",
        f"Output CSV: `{args.output}`",
        "",
        "This is a 100-row clean-candidate QA sample only. It is not final clean data, split, baseline, or training.",
        "",
        "## Counts",
        "",
        f"- clean candidate pool size: {len(pool)}",
        f"- review item count: {len(review_rows)}",
        f"- duplicate samples included: {duplicate_count}",
        f"- nonblank human QA fields: {nonblank_human}",
        "",
        "## Requested Quotas",
        "",
        *table_lines(Counter(QUOTAS)),
        "",
        "## Actual QA Subbucket Distribution",
        "",
        *table_lines(distribution(review_rows, "qa_subbucket")),
        "",
        "## Selection Counts",
        "",
        *table_lines(selection_counts),
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
        "## Backfill Notes",
        "",
        *([f"- {note}" for note in notes] if notes else ["- None"]),
        "",
        "Human QA fields are intentionally blank.",
    ]
    write_md(args.report, lines)
    print(f"v1.5e QA review item count: {len(review_rows)}")
    print(f"current_clean_candidate_audit count: {len(review_rows)}")
    print(f"source_group distribution: {distribution(review_rows, 'source_group')}")
    print(f"task_type distribution: {distribution(review_rows, 'task_type')}")
    print(f"prediction_level distribution: {distribution(review_rows, 'prediction_level')}")
    print(f"qa_subbucket distribution: {distribution(review_rows, 'qa_subbucket')}")
    print(f"duplicate samples included count: {duplicate_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
