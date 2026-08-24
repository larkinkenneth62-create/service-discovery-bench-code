#!/usr/bin/env python
"""Compare Round2 assistant draft with user-declared human final."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from round2_v0_5_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    ROUND2_DRAFT_PATH,
    duplicate_ids,
    ensure_dirs,
    fieldnames_union,
    find_round2_human_final,
    load_standardized,
    now_str,
    pct,
    rows_to_markdown_table,
    write_csv,
)


TRACE_CSV = OUTPUT_DIR / "round2_draft_vs_human_trace.csv"
CONFUSION_CSV = OUTPUT_DIR / "round2_draft_vs_human_confusion_matrix.csv"
REPORT_MD = DOCS_DIR / "round2_draft_vs_human_comparison_report_v0_5.md"


COMPARE_FIELDS = [
    ("manual_final_decision", "manual_final_decision_norm"),
    ("semantic_alignment_check", "semantic_alignment_bucket"),
    ("leakage_check", "leakage_bucket"),
    ("candidate_validity_check", "candidate_validity_bucket"),
    ("task_type_check", "task_type_check_bucket"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Round2 draft vs human final.")
    parser.add_argument("--round2-draft", type=Path, default=ROUND2_DRAFT_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dirs()
    human_path, human_resolution = find_round2_human_final(allow_overlay=True)
    if human_path is None:
        print("ERROR: Round2 human final is missing; cannot compare.")
        return 2

    _, draft_rows, _ = load_standardized(args.round2_draft)
    _, human_rows, _ = load_standardized(human_path)
    draft_dupes = duplicate_ids(draft_rows)
    human_dupes = duplicate_ids(human_rows)
    if draft_dupes or human_dupes:
        print(f"ERROR: duplicate sample IDs. draft={draft_dupes}; human={human_dupes}")
        return 1

    draft_by_id = {row["sample_id"]: row for row in draft_rows}
    human_by_id = {row["sample_id"]: row for row in human_rows}
    missing_in_human = sorted(set(draft_by_id) - set(human_by_id))
    missing_in_draft = sorted(set(human_by_id) - set(draft_by_id))
    if missing_in_human or missing_in_draft:
        print(
            "ERROR: cannot uniquely align draft and human final by sample_id. "
            f"missing_in_human={missing_in_human}; missing_in_draft={missing_in_draft}"
        )
        return 1

    trace_rows = []
    match_counts = {field: 0 for field, _ in COMPARE_FIELDS}
    confusion = Counter()
    for sample_id in sorted(draft_by_id):
        draft = draft_by_id[sample_id]
        human = human_by_id[sample_id]
        raw_human = human.get("_raw", {})
        row = {
            "sample_id": sample_id,
            "task_id": human.get("task_id", ""),
            "task_type": human.get("task_type", ""),
            "review_bucket": human.get("review_bucket_norm", ""),
            "query_text": human.get("query_text", ""),
            "human_final_source": raw_human.get("human_final_source", ""),
            "human_final_overlay_applied": raw_human.get("human_final_overlay_applied", ""),
            "user_feedback_category": raw_human.get("user_feedback_category", ""),
        }
        all_match = True
        for field, normalized_key in COMPARE_FIELDS:
            draft_norm = draft.get(normalized_key, "")
            human_norm = human.get(normalized_key, "")
            matched = draft_norm == human_norm
            all_match = all_match and matched
            if matched:
                match_counts[field] += 1
            row[f"draft_{field}"] = draft.get(field, "")
            row[f"human_{field}"] = human.get(field, "")
            row[f"draft_{field}_norm"] = draft_norm
            row[f"human_{field}_norm"] = human_norm
            row[f"{field}_match"] = "yes" if matched else "no"

        draft_decision = draft.get("manual_final_decision_norm", "")
        human_decision = human.get("manual_final_decision_norm", "")
        confusion[(draft_decision, human_decision)] += 1
        row["all_fields_match"] = "yes" if all_match else "no"
        row["draft_keep_human_remove_or_uncertain"] = (
            "yes"
            if draft_decision == "keep_for_cleaning_candidate"
            and human_decision in {"remove", "uncertain"}
            else "no"
        )
        row["draft_remove_or_uncertain_human_keep"] = (
            "yes"
            if draft_decision in {"remove", "uncertain"}
            and human_decision == "keep_for_cleaning_candidate"
            else "no"
        )
        row["draft_no_blocking_human_api_leak_blocking"] = (
            "yes"
            if draft.get("leakage_bucket") == "no_blocking"
            and human.get("leakage_bucket") == "api_leak_blocking"
            else "no"
        )
        row["draft_ok_human_mismatch_or_uncertain"] = (
            "yes"
            if draft.get("semantic_alignment_bucket") == "ok"
            and human.get("semantic_alignment_bucket") in {"mismatch", "uncertain"}
            else "no"
        )
        row["human_decision_reason"] = raw_human.get("manual_decision_reason", "")
        trace_rows.append(row)

    write_csv(TRACE_CSV, trace_rows, fieldnames_union(trace_rows))
    confusion_rows = [
        {
            "draft_manual_final_decision": draft_decision,
            "human_manual_final_decision": human_decision,
            "count": count,
        }
        for (draft_decision, human_decision), count in sorted(confusion.items())
    ]
    write_csv(CONFUSION_CSV, confusion_rows, fieldnames_union(confusion_rows))

    total = len(trace_rows)
    mismatch_rows = [row for row in trace_rows if row["all_fields_match"] == "no"]
    special_counts = {
        "draft_keep_human_remove_or_uncertain": sum(
            1 for row in trace_rows if row["draft_keep_human_remove_or_uncertain"] == "yes"
        ),
        "draft_remove_or_uncertain_human_keep": sum(
            1 for row in trace_rows if row["draft_remove_or_uncertain_human_keep"] == "yes"
        ),
        "draft_no_blocking_human_api_leak_blocking": sum(
            1 for row in trace_rows if row["draft_no_blocking_human_api_leak_blocking"] == "yes"
        ),
        "draft_ok_human_mismatch_or_uncertain": sum(
            1 for row in trace_rows if row["draft_ok_human_mismatch_or_uncertain"] == "yes"
        ),
    }

    lines = [
        "# Round2 Draft vs Human Final Comparison Report v0.5",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        f"- Round2 assistant draft: `{args.round2_draft}`",
        f"- Round2 human final: `{human_path}`",
        f"- human final resolution: `{human_resolution}`",
        "",
        "## 样本数量与对齐",
        "",
        f"- draft rows: `{len(draft_rows)}`",
        f"- human final rows: `{len(human_rows)}`",
        f"- aligned rows: `{total}`",
        "- alignment key: `sample_id` / `round2_review_id`",
        "- assistant draft 只是辅助判断，不能作为最终人工结果。",
        "",
        "## 一致率",
        "",
        "| field | match_count | total | agreement_rate |",
        "|---|---:|---:|---:|",
    ]
    for field, _ in COMPARE_FIELDS:
        lines.append(f"| `{field}` | {match_counts[field]} | {total} | {pct(match_counts[field], total)} |")

    lines.extend(
        [
            "",
            "## manual_final_decision Confusion Matrix",
            "",
            "| draft | human | count |",
            "|---|---|---:|",
        ]
    )
    for row in confusion_rows:
        lines.append(
            f"| `{row['draft_manual_final_decision']}` | `{row['human_manual_final_decision']}` | {row['count']} |"
        )

    lines.extend(
        [
            "",
            "## 特别标记统计",
            "",
            "| flag | count |",
            "|---|---:|",
        ]
    )
    for key, count in special_counts.items():
        lines.append(f"| `{key}` | {count} |")

    lines.extend(
        [
            "",
            "## 不一致样本示例",
            "",
        ]
    )
    example_cols = [
        "sample_id",
        "task_id",
        "task_type",
        "human_final_source",
        "user_feedback_category",
        "draft_manual_final_decision_norm",
        "human_manual_final_decision_norm",
        "draft_semantic_alignment_check_norm",
        "human_semantic_alignment_check_norm",
        "draft_leakage_check_norm",
        "human_leakage_check_norm",
        "human_decision_reason",
    ]
    lines.extend(rows_to_markdown_table(mismatch_rows, example_cols, max_rows=20))
    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            f"- trace CSV: `{TRACE_CSV}`",
            f"- confusion matrix CSV: `{CONFUSION_CSV}`",
            "",
            "## Scope",
            "",
            "- 没有执行 full cleaning。",
            "- 没有生成 split。",
            "- 没有运行 baseline。",
            "- 没有训练模型。",
        ]
    )
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"round2_draft_vs_human_trace={TRACE_CSV}")
    print(f"round2_draft_vs_human_confusion_matrix={CONFUSION_CSV}")
    print(f"round2_draft_vs_human_report={REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
