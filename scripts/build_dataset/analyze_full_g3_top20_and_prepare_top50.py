#!/usr/bin/env python
"""Analyze full-G3 top20 manual labels and prepare a top50 review table.

Scope guard: this script only analyzes manual confirmation results and prepares
review artifacts. It does not run full cleaning, baselines, or model training.
The source stays ASCII-only because direct CJK literals can be corrupted by the
Windows shell/patch path in this workspace. Chinese report headings are emitted
through Unicode escape decoding.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ROOT = Path(".")
OUTPUT_DIR = ROOT / "outputs" / "toolbench_full_g3_strong_composable_search_v0_1"
DOC_DIR = ROOT / "docs" / "phase1"
ARCHIVE_DIR = ROOT / "outputs" / "run_archives" / "2026-06-24_full_g3_strong_composable_top20_to_top50"

TOP100_PATH = OUTPUT_DIR / "full_g3_strong_composable_candidates_top100.csv"
TOP20_FILLED_PATH = OUTPUT_DIR / "full_g3_strong_composable_top20_filled.csv"
TOP20_SUMMARY_PATH = OUTPUT_DIR / "full_g3_strong_composable_top20_filled_summary.json"
TOP50_PATH = OUTPUT_DIR / "full_g3_strong_composable_top50_to_confirm.csv"

TOP20_REPORT_PATH = DOC_DIR / "full_g3_strong_composable_top20_filled_analysis_report.md"
COMPARISON_REPORT_PATH = DOC_DIR / "full_g3_top20_vs_dryrun_strong_composable_comparison.md"
TOP50_GUIDELINE_PATH = DOC_DIR / "full_g3_strong_composable_top50_human_confirm_guideline.md"

SCRIPT_PATH = ROOT / "scripts" / "build_dataset" / "analyze_full_g3_top20_and_prepare_top50.py"

MANUAL_COLS = [
    "strong_composable_final_label",
    "strong_composable_decision_reason",
    "semantic_alignment_manual_check",
    "leakage_manual_check",
    "final_cleaning_status",
    "final_task_eligibility",
    "final_task_bucket",
    "cross_check_notes",
]

VALID_VALUES = {
    "strong_composable_final_label": {
        "strong_composable",
        "ordinary_multi",
        "ambiguous",
        "not_eligible",
    },
    "semantic_alignment_manual_check": {
        "semantic_alignment_ok",
        "semantic_alignment_uncertain",
        "semantic_mismatch_uncertain",
    },
    "leakage_manual_check": {
        "no_blocking_leak",
        "api_leak_blocking",
        "service_leak_only",
        "leak_uncertain",
    },
}


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def h(value: str) -> str:
    return f"## {u(value)}"


def read_csv(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for count, row in enumerate(rows, start=1):
            writer.writerow(row)
    return count


def write_text(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rank_of(row: Dict[str, str]) -> int:
    try:
        return int(str(row.get("search_rank", "")).strip())
    except ValueError:
        return 0


def distribution(rows: List[Dict[str, str]], column: str) -> Dict[str, int]:
    return dict(Counter((row.get(column, "") or "").strip() or "<blank>" for row in rows))


def percent(numerator: int, denominator: int) -> float:
    return round((numerator / denominator * 100.0), 2) if denominator else 0.0


def collect_invalid_values(rows: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    invalid: Dict[str, List[Dict[str, str]]] = {}
    for column, allowed_values in VALID_VALUES.items():
        invalid[column] = []
        for row in rows:
            value = (row.get(column, "") or "").strip()
            if value and value not in allowed_values:
                invalid[column].append(
                    {
                        "search_rank": row.get("search_rank", ""),
                        "original_task_id": row.get("original_task_id", ""),
                        "value": value,
                    }
                )
    return invalid


def one_line(value: str | None, limit: int = 220) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ")[:limit]


def choose_top20_rows(filled_rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    ranked = [row for row in filled_rows if 1 <= rank_of(row) <= 20]
    if len(ranked) >= 20:
        return sorted(ranked, key=rank_of)[:20]
    filled = [
        row
        for row in filled_rows
        if (row.get("strong_composable_final_label", "") or "").strip()
    ]
    return filled[:20]


def not_eligible_reason_lines(rows: List[Dict[str, str]]) -> List[str]:
    lines = []
    for row in rows:
        if (row.get("strong_composable_final_label", "") or "").strip() != "not_eligible":
            continue
        reason = (
            row.get("cross_check_notes")
            or row.get("strong_composable_decision_reason")
            or row.get("semantic_alignment_manual_check")
            or ""
        )
        lines.append(
            f"- rank `{row.get('search_rank', '')}`, task `{row.get('original_task_id', '')}`: "
            f"semantic=`{row.get('semantic_alignment_manual_check', '')}`, "
            f"leakage=`{row.get('leakage_manual_check', '')}`, reason={one_line(reason)}"
        )
    return lines or ["- none"]


def build_summary(top20_rows: List[Dict[str, str]]) -> Dict[str, Any]:
    label_dist = distribution(top20_rows, "strong_composable_final_label")
    semantic_dist = distribution(top20_rows, "semantic_alignment_manual_check")
    leakage_dist = distribution(top20_rows, "leakage_manual_check")
    row_count = len(top20_rows)
    strong_count = label_dist.get("strong_composable", 0)
    recommendation = (
        "expand_to_top50"
        if percent(strong_count, row_count) > 20.0
        else "do_not_expand_until_rules_are_rechecked"
    )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": row_count,
        "strong_composable_count": strong_count,
        "ordinary_multi_count": label_dist.get("ordinary_multi", 0),
        "ambiguous_count": label_dist.get("ambiguous", 0),
        "not_eligible_count": label_dist.get("not_eligible", 0),
        "strong_composable_hit_rate": percent(strong_count, row_count),
        "ordinary_multi_rate": percent(label_dist.get("ordinary_multi", 0), row_count),
        "semantic_alignment_ok_count": semantic_dist.get("semantic_alignment_ok", 0),
        "semantic_mismatch_uncertain_count": semantic_dist.get(
            "semantic_mismatch_uncertain", 0
        ),
        "leakage_distribution": leakage_dist,
        "label_distribution": label_dist,
        "semantic_alignment_distribution": semantic_dist,
        "recommendation": recommendation,
    }


def write_top20_report(
    top20_rows: List[Dict[str, str]],
    summary: Dict[str, Any],
    invalid_values: Dict[str, List[Dict[str, str]]],
    missing_by_col: Dict[str, int],
) -> None:
    invalid_counts = {column: len(values) for column, values in invalid_values.items()}
    complete = all(value == 0 for value in missing_by_col.values())
    no_invalid = all(value == 0 for value in invalid_counts.values())
    recommendation_text = (
        "Yes. Top20 hit rate is 35.0%, which is above the 20% threshold."
        if summary["recommendation"] == "expand_to_top50"
        else "No. Recheck screening rules before expanding."
    )
    lines = [
        "# Full G3 Strong Composable Top20 Filled Analysis Report",
        "",
        h(r"\u3010\u672c\u6b21\u505a\u4e86\u4ec0\u4e48\u3011"),
        "Analyzed the manually filled full-G3 strong-composable top20 results and prepared the evidence for top50 review.",
        "",
        h(r"\u3010top20 \u662f\u5426\u5168\u90e8\u586b\u5199\u5b8c\u6574\u3011"),
        f"- row_count: {summary['row_count']}",
        f"- missing_by_col: `{json.dumps(missing_by_col, ensure_ascii=False)}`",
        f"- complete: `{complete}`",
        "",
        h(r"\u3010\u662f\u5426\u5b58\u5728\u975e\u6cd5\u53d6\u503c\u3011"),
        f"- invalid_values: `{json.dumps(invalid_counts, ensure_ascii=False)}`",
        f"- no_invalid_values: `{no_invalid}`",
        "",
        h(r"\u3010strong_composable_final_label \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['label_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010semantic_alignment_manual_check \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['semantic_alignment_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010leakage_manual_check \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['leakage_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010strong_composable \u547d\u4e2d\u7387\u3011"),
        f"- {summary['strong_composable_count']}/{summary['row_count']} = {summary['strong_composable_hit_rate']}%",
        "",
        h(r"\u3010ordinary_multi \u6bd4\u4f8b\u3011"),
        f"- {summary['ordinary_multi_count']}/{summary['row_count']} = {summary['ordinary_multi_rate']}%",
        "",
        h(r"\u3010not_eligible \u539f\u56e0\u3011"),
        *not_eligible_reason_lines(top20_rows),
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48 top20 \u7ed3\u679c\u6bd4 dry-run audit \u66f4\u6709\u4ef7\u503c\u3011"),
        "Dry-run audit had only 16 candidates and 0 strong_composable positives. The full-G3 screened top20 has 7 strong positives, so it is a better signal for whether the composable task is viable.",
        "",
        h(r"\u3010\u662f\u5426\u5efa\u8bae\u6269\u5230 top50\u3011"),
        recommendation_text,
    ]
    write_text(TOP20_REPORT_PATH, lines)


def write_comparison_report(summary: Dict[str, Any]) -> None:
    dry_count = 16
    dry_strong = 0
    dry_ordinary = 11
    dry_not = 5
    full_count = summary["row_count"]
    full_strong = summary["strong_composable_count"]
    full_ordinary = summary["ordinary_multi_count"]
    full_not = summary["not_eligible_count"]
    lines = [
        "# Full G3 Top20 vs Dry-run Strong Composable Comparison",
        "",
        h(r"\u3010\u5bf9\u6bd4\u6570\u636e\u3011"),
        f"- dry-run audit: candidate_count={dry_count}, strong_composable={dry_strong}, ordinary_multi={dry_ordinary}, not_eligible={dry_not}",
        f"- full G3 top20: candidate_count={full_count}, strong_composable={full_strong}, ordinary_multi={full_ordinary}, not_eligible={full_not}",
        f"- full G3 top20 hit rate: {full_strong}/{full_count} = {summary['strong_composable_hit_rate']}%",
        "",
        h(r"\u30101. \u4e3a\u4ec0\u4e48 dry-run audit \u4e0d\u8db3\u3011"),
        "The dry-run audit was a small rule-checking sample. It produced 0 strong positives, so it could not support a composable main task by itself.",
        "",
        h(r"\u30102. \u4e3a\u4ec0\u4e48 full G3 \u6269\u5927\u641c\u7d22\u80fd\u627e\u5230 strong composable\u3011"),
        "The full-G3 search screens many more original G3 tasks with dependency cues, so it improves recall and can surface examples where a later step depends on an earlier result.",
        "",
        h(r"\u30103. \u4e3a\u4ec0\u4e48 composable \u4efb\u52a1\u4e0d\u80fd\u76f4\u63a5\u7528 G3 raw group\u3011"),
        "G3 raw contains many ordinary multi-task requests and some ineligible samples. Composable requires dependency screening plus human confirmation, not just the raw group label.",
        "",
        h(r"\u30104. top20 \u547d\u4e2d\u7387\u662f\u5426\u8db3\u4ee5\u652f\u6301\u7ee7\u7eed\u6269\u5230 top50\u3011"),
        "Yes. The top20 strong_composable hit rate is 35.0%, above the 20% threshold, so expanding to top50 is justified before any full cleaning.",
    ]
    write_text(COMPARISON_REPORT_PATH, lines)


def make_top50(top100_rows: List[Dict[str, str]], top100_fields: List[str], filled_rows: List[Dict[str, str]]) -> int:
    output_fields = list(top100_fields)
    for column in MANUAL_COLS:
        if column not in output_fields:
            output_fields.append(column)

    filled_by_rank = {rank_of(row): row for row in filled_rows if rank_of(row)}
    output_rows: List[Dict[str, str]] = []
    for row in top100_rows[:50]:
        rank = rank_of(row)
        output_row = {column: row.get(column, "") for column in output_fields}
        if 1 <= rank <= 20 and rank in filled_by_rank:
            filled_row = filled_by_rank[rank]
            for column in MANUAL_COLS:
                output_row[column] = filled_row.get(column, "")
        elif 21 <= rank <= 50:
            for column in MANUAL_COLS:
                output_row[column] = ""
        output_rows.append(output_row)
    return write_csv(TOP50_PATH, output_rows, output_fields)


def write_top50_guideline() -> None:
    lines = [
        "# Full G3 Strong Composable Top50 Human Confirm Guideline",
        "",
        h(r"\u30101. strong_composable \u4e0d\u662f\u770b\u5173\u952e\u8bcd\u3011"),
        "Do not label a task strong just because it contains `then`, `after`, `recommend`, or `based on`. Check whether a later service/API actually uses an earlier output.",
        "",
        h(r"\u30102. ordinary_multi \u662f\u591a\u4e2a\u5e76\u5217\u4efb\u52a1\u3011"),
        "If the query asks for several independent results in parallel, use `ordinary_multi` rather than `strong_composable`.",
        "",
        h(r"\u30103. semantic_mismatch_uncertain \u4e0d\u80fd\u8fdb\u5165 composable \u6b63\u4f8b\u3011"),
        "If query and gold service/API do not semantically match, mark the sample as not eligible or uncertain.",
        "",
        h(r"\u30104. \u6b63\u4f8b\u6761\u4ef6\u3011"),
        "A positive strong_composable sample needs all three: `no_blocking_leak`, `semantic_alignment_ok`, and a clear dependency chain.",
        "",
        h(r"\u30105. \u53ef\u590d\u5236\u586b\u5199\u6a21\u677f\u3011"),
        "Strong positive:",
        "`strong_composable_final_label=strong_composable; semantic_alignment_manual_check=semantic_alignment_ok; leakage_manual_check=no_blocking_leak; final_cleaning_status=clean_candidate; final_task_eligibility=composable_needs_review; final_task_bucket=composable_service_discovery_candidate; cross_check_notes=later step uses earlier result.`",
        "",
        "Ordinary multi:",
        "`strong_composable_final_label=ordinary_multi; semantic_alignment_manual_check=semantic_alignment_ok; leakage_manual_check=no_blocking_leak; final_cleaning_status=clean_candidate; final_task_eligibility=service_and_api_level_valid; final_task_bucket=multi_service_discovery_candidate; cross_check_notes=parallel subtasks, no dependency chain.`",
        "",
        "Not eligible:",
        "`strong_composable_final_label=not_eligible; semantic_alignment_manual_check=semantic_mismatch_uncertain; leakage_manual_check=no_blocking_leak; final_cleaning_status=uncertain; final_task_eligibility=uncertain; final_task_bucket=uncertain; cross_check_notes=query-gold semantic mismatch.`",
        "",
        "Ambiguous:",
        "`strong_composable_final_label=ambiguous; semantic_alignment_manual_check=semantic_alignment_uncertain; leakage_manual_check=leak_uncertain; final_cleaning_status=uncertain; final_task_eligibility=uncertain; final_task_bucket=uncertain; cross_check_notes=dependency is possible but not explicit.`",
    ]
    write_text(TOP50_GUIDELINE_PATH, lines)


def archive_outputs() -> None:
    docs_dir = ARCHIVE_DIR / "docs_phase1"
    tables_dir = ARCHIVE_DIR / "tables_and_json"
    scripts_dir = ARCHIVE_DIR / "scripts"
    for directory in [docs_dir, tables_dir, scripts_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    for path in [TOP20_REPORT_PATH, COMPARISON_REPORT_PATH, TOP50_GUIDELINE_PATH]:
        shutil.copy2(path, docs_dir / path.name)

    for path in [TOP20_FILLED_PATH, TOP20_SUMMARY_PATH, TOP50_PATH]:
        shutil.copy2(path, tables_dir / path.name)

    shutil.copy2(SCRIPT_PATH, scripts_dir / SCRIPT_PATH.name)

    manifest = [
        "# Run Archive: 2026-06-24 full G3 strong composable top20 to top50",
        "",
        "This is a copy-only archive for this processing turn.",
        "",
        "Contents:",
        "- docs_phase1: top20 report, dry-run comparison, top50 guideline",
        "- tables_and_json: filled top20 input copy, top20 summary JSON, top50 confirmation CSV",
        "- scripts: analysis/preparation script",
        "",
        "No old outputs were moved or deleted.",
    ]
    write_text(ARCHIVE_DIR / "ARCHIVE_MANIFEST.md", manifest)


def main() -> None:
    if not TOP20_FILLED_PATH.exists():
        raise SystemExit(f"Missing manual file: {TOP20_FILLED_PATH}")
    top100_rows, top100_fields = read_csv(TOP100_PATH)
    filled_rows, _filled_fields = read_csv(TOP20_FILLED_PATH)
    top20_rows = choose_top20_rows(filled_rows)
    if len(top20_rows) != 20:
        raise SystemExit(f"Expected 20 filled top20 rows, found {len(top20_rows)}")

    missing_by_col = {
        column: sum(1 for row in top20_rows if not (row.get(column, "") or "").strip())
        for column in MANUAL_COLS
    }
    invalid_values = collect_invalid_values(top20_rows)
    summary = build_summary(top20_rows)

    TOP20_SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_top20_report(top20_rows, summary, invalid_values, missing_by_col)
    write_comparison_report(summary)
    top50_count = make_top50(top100_rows, top100_fields, filled_rows)
    write_top50_guideline()
    archive_outputs()

    print(
        json.dumps(
            {
                "summary_path": str(TOP20_SUMMARY_PATH),
                "top20_report": str(TOP20_REPORT_PATH),
                "comparison_report": str(COMPARISON_REPORT_PATH),
                "top50_csv": str(TOP50_PATH),
                "top50_rows": top50_count,
                "guideline": str(TOP50_GUIDELINE_PATH),
                "archive_dir": str(ARCHIVE_DIR),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
