#!/usr/bin/env python
"""Analyze Round2 80-row human final decisions for v0.5 validation."""

from __future__ import annotations

import argparse
import importlib.util
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

from round2_v0_5_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    ROUND2_DRAFT_PATH,
    candidate_validity_bucket,
    column_mapping,
    ensure_dirs,
    fieldnames_union,
    find_round2_human_final,
    leakage_bucket,
    load_standardized,
    now_str,
    pct,
    read_csv,
    row_to_standard,
    rows_to_markdown_table,
    semantic_bucket,
    task_family,
    task_type_check_bucket,
    value,
    write_csv,
)


SUMMARY_CSV = OUTPUT_DIR / "round2_human_final_summary.csv"
CROSSTAB_XLSX = OUTPUT_DIR / "round2_human_final_crosstab.xlsx"
CROSSTAB_FALLBACK_CSV = OUTPUT_DIR / "round2_human_final_crosstab_fallback.csv"
REPORT_MD = DOCS_DIR / "round2_manual80_analysis_report_v0_5.md"


def count_by(rows: List[dict], key: str) -> Counter:
    return Counter((row.get(key) or "<EMPTY>") for row in rows)


def add_distribution(summary: List[dict], metric: str, counts: Counter, total: int) -> None:
    for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        summary.append(
            {
                "metric": metric,
                "group": key,
                "value": key,
                "count": count,
                "rate": f"{(count / total * 100):.4f}" if total else "0.0000",
                "note": "",
            }
        )


def crosstab(rows: List[dict], group_key: str) -> List[dict]:
    table: Dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        group = row.get(group_key) or "<EMPTY>"
        decision = row.get("manual_final_decision_norm") or "<EMPTY>"
        table[group][decision] += 1
    out = []
    for group in sorted(table):
        counts = table[group]
        total = sum(counts.values())
        out.append(
            {
                "group_by": group_key,
                "group": group,
                "keep_for_cleaning_candidate": counts.get(
                    "keep_for_cleaning_candidate", 0
                ),
                "remove": counts.get("remove", 0),
                "uncertain": counts.get("uncertain", 0),
                "other": total
                - counts.get("keep_for_cleaning_candidate", 0)
                - counts.get("remove", 0)
                - counts.get("uncertain", 0),
                "total": total,
            }
        )
    return out


def rate_for(rows: List[dict], filter_key: str, filter_value: str, good_decisions: set[str]) -> dict:
    subset = [row for row in rows if row.get(filter_key) == filter_value]
    good = [row for row in subset if row.get("manual_final_decision_norm") in good_decisions]
    return {
        "metric": f"{filter_value}_rate",
        "group": filter_value,
        "value": ",".join(sorted(good_decisions)),
        "count": len(good),
        "denominator": len(subset),
        "rate": f"{(len(good) / len(subset) * 100):.4f}" if subset else "0.0000",
        "note": "",
    }


def keyword_counts(rows: List[dict]) -> tuple[Counter, str]:
    note_cols = ["human_notes", "manual_notes", "manual_decision_reason", "final_decision_reason"]
    actual = None
    raw_rows = [row["_raw"] for row in rows if "_raw" in row]
    if raw_rows:
        raw_cols = set(raw_rows[0].keys())
        for col in note_cols:
            if col in raw_cols:
                actual = col
                break
    if not actual:
        return Counter(), ""
    keywords = [
        "leak",
        "api",
        "service",
        "semantic",
        "mismatch",
        "uncertain",
        "container",
        "package",
        "tracking",
        "candidate",
        "gold",
        "cannot",
        "query",
        "restaurant",
        "zoo",
        "concert",
        "coordinate",
        "gas station",
        "single",
        "choice",
        "实现",
        "不能",
        "泄露",
        "语义",
        "包裹",
        "集装箱",
    ]
    counts = Counter()
    for raw in raw_rows:
        note = (raw.get(actual) or "").lower()
        for keyword in keywords:
            if keyword.lower() in note:
                counts[keyword] += 1
    return counts, actual


def representative_examples(rows: List[dict]) -> Dict[str, List[dict]]:
    raw_rows = []
    for row in rows:
        raw = dict(row.get("_raw", {}))
        raw["sample_id"] = row.get("sample_id", "")
        raw["manual_final_decision_norm"] = row.get("manual_final_decision_norm", "")
        raw["semantic_alignment_bucket"] = row.get("semantic_alignment_bucket", "")
        raw["leakage_bucket"] = row.get("leakage_bucket", "")
        raw["review_bucket_norm"] = row.get("review_bucket_norm", "")
        raw["task_family"] = row.get("task_family", "")
        raw_rows.append(raw)

    return {
        "high_confidence_but_remove_or_uncertain": [
            r
            for r in raw_rows
            if r.get("review_bucket_norm") == "high_confidence_candidate"
            and r.get("manual_final_decision_norm") in {"remove", "uncertain"}
        ][:10],
        "high_risk_but_keep": [
            r
            for r in raw_rows
            if r.get("review_bucket_norm") == "high_risk_review"
            and r.get("manual_final_decision_norm") == "keep_for_cleaning_candidate"
        ][:10],
        "no_obvious_leak_but_remove_or_uncertain": [
            r
            for r in raw_rows
            if (r.get("leak_status") or "").strip() == "no_obvious_leak"
            and r.get("manual_final_decision_norm") in {"remove", "uncertain"}
        ][:10],
        "service_leak_only_samples": [
            r for r in raw_rows if r.get("leakage_bucket") == "service_leak_only"
        ][:10],
        "semantic_mismatch_or_uncertain_samples": [
            r
            for r in raw_rows
            if r.get("semantic_alignment_bucket") in {"mismatch", "uncertain"}
        ][:10],
    }


def write_xlsx_or_fallback(crosstab_rows: List[dict]) -> tuple[str, str]:
    if importlib.util.find_spec("openpyxl") is None:
        write_csv(CROSSTAB_FALLBACK_CSV, crosstab_rows, fieldnames_union(crosstab_rows))
        return "csv_fallback_no_openpyxl", str(CROSSTAB_FALLBACK_CSV)

    from openpyxl import Workbook  # type: ignore

    wb = Workbook()
    ws = wb.active
    ws.title = "crosstab"
    fieldnames = fieldnames_union(crosstab_rows)
    ws.append(fieldnames)
    for row in crosstab_rows:
        ws.append([row.get(col, "") for col in fieldnames])
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 40)
    CROSSTAB_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(CROSSTAB_XLSX)
    return "xlsx_openpyxl", str(CROSSTAB_XLSX)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Round2 human final decisions.")
    parser.add_argument("--round2-draft", type=Path, default=ROUND2_DRAFT_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dirs()
    human_path, human_resolution = find_round2_human_final(allow_overlay=True)
    if human_path is None:
        print("ERROR: Round2 human final is missing; cannot analyze.")
        return 2

    _, rows, mapping = load_standardized(human_path)
    total = len(rows)
    if total == 0:
        print(f"ERROR: Round2 human final has zero rows: {human_path}")
        return 1

    summary: List[dict] = []
    summary.append(
        {
            "metric": "row_count",
            "group": "all",
            "value": "round2_human_final",
            "count": total,
            "rate": "100.0000",
            "note": str(human_path),
        }
    )
    add_distribution(
        summary,
        "manual_final_decision_distribution",
        count_by(rows, "manual_final_decision_norm"),
        total,
    )
    add_distribution(summary, "task_type_distribution", count_by(rows, "task_family"), total)
    add_distribution(
        summary,
        "review_bucket_distribution",
        count_by(rows, "review_bucket_norm"),
        total,
    )
    add_distribution(
        summary,
        "leakage_check_distribution",
        count_by(rows, "leakage_bucket"),
        total,
    )
    add_distribution(
        summary,
        "semantic_alignment_check_distribution",
        count_by(rows, "semantic_alignment_bucket"),
        total,
    )
    add_distribution(
        summary,
        "candidate_validity_check_distribution",
        count_by(rows, "candidate_validity_bucket"),
        total,
    )
    add_distribution(
        summary,
        "task_type_check_distribution",
        count_by(rows, "task_type_check_bucket"),
        total,
    )
    high_conf_keep_rate = rate_for(
        rows,
        "review_bucket_norm",
        "high_confidence_candidate",
        {"keep_for_cleaning_candidate"},
    )
    boundary_bad_rate = rate_for(
        rows, "review_bucket_norm", "boundary_review", {"remove", "uncertain"}
    )
    high_risk_bad_rate = rate_for(
        rows, "review_bucket_norm", "high_risk_review", {"remove", "uncertain"}
    )
    api_leak_remove_rate = rate_for(rows, "leakage_bucket", "api_leak_blocking", {"remove"})
    summary.extend(
        [
            high_conf_keep_rate,
            boundary_bad_rate,
            high_risk_bad_rate,
            api_leak_remove_rate,
        ]
    )

    service_leak_rows = [row for row in rows if row.get("leakage_bucket") == "service_leak_only"]
    service_leak_dist = count_by(service_leak_rows, "manual_final_decision_norm")
    for key, count in service_leak_dist.items():
        summary.append(
            {
                "metric": "service_leak_only_final_destination",
                "group": key,
                "value": key,
                "count": count,
                "denominator": len(service_leak_rows),
                "rate": f"{count / len(service_leak_rows) * 100:.4f}"
                if service_leak_rows
                else "0.0000",
                "note": "",
            }
        )

    note_counts, note_col = keyword_counts(rows)
    for key, count in note_counts.most_common(30):
        summary.append(
            {
                "metric": "human_note_keyword_count",
                "group": note_col,
                "value": key,
                "count": count,
                "rate": "",
                "note": "",
            }
        )

    write_csv(SUMMARY_CSV, summary, fieldnames_union(summary))

    crosstab_rows = []
    for key in [
        "task_family",
        "review_bucket_norm",
        "leakage_bucket",
        "semantic_alignment_bucket",
        "candidate_validity_bucket",
        "task_type_check_bucket",
    ]:
        crosstab_rows.extend(crosstab(rows, key))
    crosstab_mode, crosstab_path = write_xlsx_or_fallback(crosstab_rows)

    examples = representative_examples(rows)
    lines = [
        "# Round2 Manual80 Analysis Report v0.5",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        f"- Round2 human final: `{human_path}`",
        f"- Round2 assistant draft: `{args.round2_draft}`",
        f"- human final resolution: `{human_resolution}`",
        "",
        "## 样本数量",
        "",
        f"- Round2 total rows: `{total}`",
        "",
        "## manual_final_decision 分布",
        "",
        "| decision | count | rate |",
        "|---|---:|---:|",
    ]
    decision_counts = count_by(rows, "manual_final_decision_norm")
    for key in ["keep_for_cleaning_candidate", "remove", "uncertain"]:
        lines.append(f"| `{key}` | {decision_counts.get(key, 0)} | {pct(decision_counts.get(key, 0), total)} |")
    other = total - sum(decision_counts.get(key, 0) for key in ["keep_for_cleaning_candidate", "remove", "uncertain"])
    lines.append(f"| `other_abnormal_values` | {other} | {pct(other, total)} |")

    lines.extend(
        [
            "",
            "## 关键分组统计",
            "",
            f"- high_confidence_candidate 人工 keep 率：`{high_conf_keep_rate['rate']}%`",
            f"- boundary_review 人工 uncertain/remove 率：`{boundary_bad_rate['rate']}%`",
            f"- high_risk_review 人工 remove/uncertain 率：`{high_risk_bad_rate['rate']}%`",
            f"- api_leak_blocking 样本中人工 remove 率：`{api_leak_remove_rate['rate']}%`",
            "",
            "## 备注关键词",
            "",
        ]
    )
    if note_col:
        lines.append(f"使用备注列：`{note_col}`")
        lines.append("")
        lines.extend(["| keyword | count |", "|---|---:|"])
        for key, count in note_counts.most_common(20):
            lines.append(f"| `{key}` | {count} |")
    else:
        lines.append("没有发现 `human_notes` / `manual_notes` 或可映射备注列，不能做备注关键词分析。")

    lines.extend(
        [
            "",
            "## 代表性样例",
            "",
        ]
    )
    example_columns = [
        "sample_id",
        "task_id",
        "task_type",
        "review_bucket_norm",
        "leakage_bucket",
        "semantic_alignment_bucket",
        "manual_final_decision_norm",
        "query_text",
    ]
    for title, rows_subset in examples.items():
        lines.extend([f"### {title}", ""])
        lines.extend(rows_to_markdown_table(rows_subset, example_columns, max_rows=10))
        lines.append("")

    lines.extend(
        [
            "## 输出文件",
            "",
            f"- summary CSV: `{SUMMARY_CSV}`",
            f"- crosstab output: `{crosstab_path}`",
            f"- crosstab mode: `{crosstab_mode}`",
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
    print(f"round2_human_final_summary={SUMMARY_CSV}")
    print(f"round2_human_final_crosstab={crosstab_path}")
    print(f"round2_manual80_analysis_report={REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
