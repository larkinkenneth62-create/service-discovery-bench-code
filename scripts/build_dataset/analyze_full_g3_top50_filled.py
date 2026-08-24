#!/usr/bin/env python
"""Analyze full-G3 top50 manual labels and prepare top100 review artifacts.

Scope guard: analysis and review-table preparation only. This script does not
run full cleaning, baselines, or model training.
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
ARCHIVE_DIR = ROOT / "outputs" / "run_archives" / "2026-06-24_full_g3_strong_composable_top50_analysis"

TOP100_PATH = OUTPUT_DIR / "full_g3_strong_composable_candidates_top100.csv"
TOP50_FILLED_PATH = OUTPUT_DIR / "full_g3_strong_composable_top50_filled.csv"
USER_SUMMARY_CSV_PATH = OUTPUT_DIR / "full_g3_strong_composable_top50_filled_summary.csv"
SUMMARY_JSON_PATH = OUTPUT_DIR / "full_g3_strong_composable_top50_filled_analysis_summary.json"
COMPUTED_SUMMARY_CSV_PATH = OUTPUT_DIR / "full_g3_strong_composable_top50_filled_computed_summary.csv"
TOP100_TO_CONFIRM_PATH = OUTPUT_DIR / "full_g3_strong_composable_top100_to_confirm.csv"

TOP50_REPORT_PATH = DOC_DIR / "full_g3_strong_composable_top50_filled_analysis_report.md"
COMPARISON_REPORT_PATH = DOC_DIR / "full_g3_top50_vs_top20_vs_dryrun_strong_composable_comparison.md"
NEXT_STEP_REPORT_PATH = DOC_DIR / "full_g3_strong_composable_top50_next_step_recommendation.md"
TOP100_GUIDELINE_PATH = DOC_DIR / "full_g3_strong_composable_top100_human_confirm_guideline.md"

SCRIPT_PATH = ROOT / "scripts" / "build_dataset" / "analyze_full_g3_top50_filled.py"

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
    "final_cleaning_status": {
        "clean_candidate",
        "uncertain",
        "remove_api_leak",
        "service_leak_only",
        "invalid_candidate_or_gold",
    },
    "final_task_eligibility": {
        "composable_needs_review",
        "service_and_api_level_valid",
        "uncertain",
        "not_eligible",
    },
    "final_task_bucket": {
        "composable_service_discovery_candidate",
        "multi_service_discovery_candidate",
        "uncertain",
        "remove",
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


def choose_top50_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    ranked = [row for row in rows if 1 <= rank_of(row) <= 50]
    if len(ranked) >= 50:
        return sorted(ranked, key=rank_of)[:50]
    filled = [row for row in rows if (row.get("strong_composable_final_label", "") or "").strip()]
    return filled[:50]


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


def build_summary(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    label_dist = distribution(rows, "strong_composable_final_label")
    semantic_dist = distribution(rows, "semantic_alignment_manual_check")
    leakage_dist = distribution(rows, "leakage_manual_check")
    cleaning_dist = distribution(rows, "final_cleaning_status")
    eligibility_dist = distribution(rows, "final_task_eligibility")
    bucket_dist = distribution(rows, "final_task_bucket")
    row_count = len(rows)
    strong_count = label_dist.get("strong_composable", 0)
    recommendation = (
        "expand_to_top100_for_manual_confirmation"
        if percent(strong_count, row_count) > 20.0
        else "stop_composable_expansion_and_recheck_screening"
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
        "not_eligible_rate": percent(label_dist.get("not_eligible", 0), row_count),
        "semantic_alignment_ok_count": semantic_dist.get("semantic_alignment_ok", 0),
        "semantic_mismatch_uncertain_count": semantic_dist.get("semantic_mismatch_uncertain", 0),
        "leakage_distribution": leakage_dist,
        "label_distribution": label_dist,
        "semantic_alignment_distribution": semantic_dist,
        "final_cleaning_status_distribution": cleaning_dist,
        "final_task_eligibility_distribution": eligibility_dist,
        "final_task_bucket_distribution": bucket_dist,
        "recommendation": recommendation,
    }


def supplied_summary_rows() -> List[Dict[str, str]]:
    if not USER_SUMMARY_CSV_PATH.exists():
        return []
    rows, _fields = read_csv(USER_SUMMARY_CSV_PATH)
    return rows


def compare_supplied_summary(computed_summary_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    supplied = supplied_summary_rows()
    supplied_map = {
        (row.get("field", ""), row.get("label", "")): int(row.get("count", "0") or 0)
        for row in supplied
        if row.get("field") and row.get("label")
    }
    mismatches: List[Dict[str, Any]] = []
    for row in computed_summary_rows:
        key = (str(row["field"]), str(row["label"]))
        supplied_count = supplied_map.get(key)
        if supplied_count is None:
            mismatches.append(
                {
                    "field": key[0],
                    "label": key[1],
                    "computed_count": row["count"],
                    "supplied_count": None,
                }
            )
        elif supplied_count != int(row["count"]):
            mismatches.append(
                {
                    "field": key[0],
                    "label": key[1],
                    "computed_count": row["count"],
                    "supplied_count": supplied_count,
                }
            )
    return mismatches


def computed_summary_table(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    table: List[Dict[str, Any]] = []
    for field in [
        "strong_composable_final_label",
        "semantic_alignment_manual_check",
        "leakage_manual_check",
        "final_cleaning_status",
        "final_task_eligibility",
        "final_task_bucket",
    ]:
        for label, count in sorted(distribution(rows, field).items()):
            table.append({"field": field, "label": label, "count": count})
    return table


def reason_lines(rows: List[Dict[str, str]], target_label: str, max_items: int = 8) -> List[str]:
    selected = [
        row
        for row in rows
        if (row.get("strong_composable_final_label", "") or "").strip() == target_label
    ]
    lines = []
    for row in selected[:max_items]:
        reason = row.get("strong_composable_decision_reason") or row.get("cross_check_notes") or ""
        lines.append(
            f"- rank `{row.get('search_rank', '')}`, task `{row.get('original_task_id', '')}`: "
            f"{one_line(reason)}"
        )
    return lines or ["- none"]


def write_top50_report(
    rows: List[Dict[str, str]],
    summary: Dict[str, Any],
    invalid_values: Dict[str, List[Dict[str, str]]],
    missing_by_col: Dict[str, int],
    supplied_summary_mismatches: List[Dict[str, Any]],
) -> None:
    invalid_counts = {column: len(values) for column, values in invalid_values.items()}
    complete = all(value == 0 for value in missing_by_col.values())
    no_invalid = all(value == 0 for value in invalid_counts.values())
    supplied_match = len(supplied_summary_mismatches) == 0
    lines = [
        "# Full G3 Strong Composable Top50 Filled Analysis Report",
        "",
        h(r"\u3010\u672c\u6b21\u505a\u4e86\u4ec0\u4e48\u3011"),
        "Copied the uploaded top50 manual labels, recomputed distributions from row-level labels, cross-checked the supplied summary CSV, and prepared top100 review artifacts.",
        "",
        h(r"\u3010top50 \u662f\u5426\u5168\u90e8\u586b\u5199\u5b8c\u6574\u3011"),
        f"- row_count: {summary['row_count']}",
        f"- missing_by_col: `{json.dumps(missing_by_col, ensure_ascii=False)}`",
        f"- complete: `{complete}`",
        "",
        h(r"\u3010\u662f\u5426\u5b58\u5728\u975e\u6cd5\u53d6\u503c\u3011"),
        f"- invalid_values: `{json.dumps(invalid_counts, ensure_ascii=False)}`",
        f"- no_invalid_values: `{no_invalid}`",
        "",
        h(r"\u3010\u4eba\u5de5 summary CSV \u662f\u5426\u548c\u9010\u884c\u7edf\u8ba1\u4e00\u81f4\u3011"),
        f"- supplied_summary_matches_computed: `{supplied_match}`",
        f"- mismatch_count: {len(supplied_summary_mismatches)}",
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
        h(r"\u3010not_eligible \u6bd4\u4f8b\u3011"),
        f"- {summary['not_eligible_count']}/{summary['row_count']} = {summary['not_eligible_rate']}%",
        "",
        h(r"\u3010strong_composable \u6837\u672c\u6458\u8981\u3011"),
        *reason_lines(rows, "strong_composable", 8),
        "",
        h(r"\u3010ordinary_multi \u6837\u672c\u6458\u8981\u3011"),
        *reason_lines(rows, "ordinary_multi", 8),
        "",
        h(r"\u3010not_eligible \u6837\u672c\u6458\u8981\u3011"),
        *reason_lines(rows, "not_eligible", 8),
        "",
        h(r"\u3010\u662f\u5426\u5efa\u8bae\u6269\u5230 top100\u3011"),
        "Yes. Top50 hit rate is 26.0%, still above the 20% threshold. Expand to top100 manual confirmation before any full cleaning.",
    ]
    write_text(TOP50_REPORT_PATH, lines)


def write_comparison_report(summary: Dict[str, Any]) -> None:
    lines = [
        "# Full G3 Strong Composable Dry-run vs Top20 vs Top50",
        "",
        h(r"\u3010\u5bf9\u6bd4\u7ed3\u679c\u3011"),
        "- dry-run audit: candidate_count=16, strong_composable=0, ordinary_multi=11, not_eligible=5, hit_rate=0.0%",
        "- full G3 top20: candidate_count=20, strong_composable=7, ordinary_multi=9, not_eligible=4, hit_rate=35.0%",
        f"- full G3 top50: candidate_count=50, strong_composable={summary['strong_composable_count']}, ordinary_multi={summary['ordinary_multi_count']}, not_eligible={summary['not_eligible_count']}, hit_rate={summary['strong_composable_hit_rate']}%",
        "",
        h(r"\u3010\u89c4\u5219\u4fe1\u53f7\u3011"),
        "Dependency screening clearly improves recall compared with dry-run audit, but ordinary_multi remains the largest category. Human confirmation is still necessary.",
        "",
        h(r"\u3010\u5bf9 composable \u4e3b\u4efb\u52a1\u7684\u542b\u4e49\u3011"),
        "Top50 confirms that full G3 contains usable strong_composable candidates, but raw G3 cannot be directly used as the composable benchmark. It must go through dependency screening, semantic gate, leakage gate, and manual confirmation.",
        "",
        h(r"\u3010\u662f\u5426\u8db3\u4ee5\u7ee7\u7eed\u5230 top100\u3011"),
        "Yes. The top50 hit rate remains above 20%, so top100 confirmation is justified. Still do not run full cleaning yet.",
    ]
    write_text(COMPARISON_REPORT_PATH, lines)


def write_next_step_report(summary: Dict[str, Any]) -> None:
    lines = [
        "# Full G3 Strong Composable Top50 Next Step Recommendation",
        "",
        h(r"\u30101. \u73b0\u5728\u662f\u5426\u5efa\u8bae\u8dd1\u5168\u91cf\u6e05\u6d17\uff1f"),
        "No. Top50 is a validation checkpoint, not final dataset construction.",
        "",
        h(r"\u30102. \u662f\u5426\u5efa\u8bae\u6269\u5230 top100 \u4eba\u5de5\u786e\u8ba4\uff1f"),
        f"Yes. Top50 strong_composable hit rate is {summary['strong_composable_hit_rate']}%, above the 20% threshold.",
        "",
        h(r"\u30103. top50 \u8bf4\u660e\u4e86\u4ec0\u4e48\uff1f"),
        "It shows that strong composable positives exist in full G3, but ordinary_multi is still common and semantic mismatch remains non-trivial.",
        "",
        h(r"\u30104. \u4ec0\u4e48\u65f6\u5019\u624d\u80fd\u5199\u6b63\u5f0f\u6e05\u6d17\u811a\u672c\uff1f"),
        "After top100 confirmation, if the strong hit rate is stable and the rule buckets are clear, write the formal cleaning script. The script should encode semantic alignment gate, leakage gate, ordinary_multi separation, and composable positive eligibility.",
        "",
        h(r"\u30105. \u4e0b\u4e00\u6b65\u5efa\u8bae\u3011"),
        "Fill rows 51-100 in `full_g3_strong_composable_top100_to_confirm.csv`, then analyze the full top100 confirmation before deciding whether composable enters the main benchmark.",
    ]
    write_text(NEXT_STEP_REPORT_PATH, lines)


def make_top100_to_confirm(top100_rows: List[Dict[str, str]], top100_fields: List[str], filled_rows: List[Dict[str, str]]) -> int:
    output_fields = list(top100_fields)
    for column in MANUAL_COLS:
        if column not in output_fields:
            output_fields.append(column)

    filled_by_rank = {rank_of(row): row for row in filled_rows if rank_of(row)}
    output_rows: List[Dict[str, str]] = []
    for row in top100_rows[:100]:
        rank = rank_of(row)
        output_row = {column: row.get(column, "") for column in output_fields}
        if 1 <= rank <= 50 and rank in filled_by_rank:
            filled_row = filled_by_rank[rank]
            for column in MANUAL_COLS:
                output_row[column] = filled_row.get(column, "")
        elif 51 <= rank <= 100:
            for column in MANUAL_COLS:
                output_row[column] = ""
        output_rows.append(output_row)
    return write_csv(TOP100_TO_CONFIRM_PATH, output_rows, output_fields)


def write_top100_guideline() -> None:
    lines = [
        "# Full G3 Strong Composable Top100 Human Confirm Guideline",
        "",
        h(r"\u3010\u586b\u5199\u76ee\u6807\u3011"),
        "Rows 1-50 already contain manual labels. Fill rows 51-100 using the same criteria.",
        "",
        h(r"\u3010strong_composable \u5224\u65ad\u6807\u51c6\u3011"),
        "Only mark strong_composable when a later API/service step uses an earlier step's returned entity, ID, result, filter, or decision signal.",
        "",
        h(r"\u3010ordinary_multi \u5224\u65ad\u6807\u51c6\u3011"),
        "If tasks are parallel or merely listed together, mark ordinary_multi even when dependency keywords appear.",
        "",
        h(r"\u3010not_eligible \u5224\u65ad\u6807\u51c6\u3011"),
        "Use not_eligible for query-gold semantic mismatch, missing/invalid gold, or other blocking issues.",
        "",
        h(r"\u3010\u6b63\u4f8b\u5fc5\u8981\u6761\u4ef6\u3011"),
        "A strong positive needs: `semantic_alignment_ok`, `no_blocking_leak`, and a clear dependency chain.",
        "",
        h(r"\u3010\u53ef\u590d\u5236\u6a21\u677f\u3011"),
        "Strong positive:",
        "`strong_composable_final_label=strong_composable; semantic_alignment_manual_check=semantic_alignment_ok; leakage_manual_check=no_blocking_leak; final_cleaning_status=clean_candidate; final_task_eligibility=composable_needs_review; final_task_bucket=composable_service_discovery_candidate; cross_check_notes=later step uses earlier result.`",
        "",
        "Ordinary multi:",
        "`strong_composable_final_label=ordinary_multi; semantic_alignment_manual_check=semantic_alignment_ok; leakage_manual_check=no_blocking_leak; final_cleaning_status=clean_candidate; final_task_eligibility=service_and_api_level_valid; final_task_bucket=multi_service_discovery_candidate; cross_check_notes=parallel subtasks, no dependency chain.`",
        "",
        "Not eligible:",
        "`strong_composable_final_label=not_eligible; semantic_alignment_manual_check=semantic_mismatch_uncertain; leakage_manual_check=no_blocking_leak; final_cleaning_status=uncertain; final_task_eligibility=uncertain; final_task_bucket=uncertain; cross_check_notes=query-gold semantic mismatch or other blocking issue.`",
    ]
    write_text(TOP100_GUIDELINE_PATH, lines)


def archive_outputs() -> None:
    docs_dir = ARCHIVE_DIR / "docs_phase1"
    tables_dir = ARCHIVE_DIR / "tables_and_json"
    scripts_dir = ARCHIVE_DIR / "scripts"
    for directory in [docs_dir, tables_dir, scripts_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    for path in [TOP50_REPORT_PATH, COMPARISON_REPORT_PATH, NEXT_STEP_REPORT_PATH, TOP100_GUIDELINE_PATH]:
        shutil.copy2(path, docs_dir / path.name)

    for path in [
        TOP50_FILLED_PATH,
        USER_SUMMARY_CSV_PATH,
        SUMMARY_JSON_PATH,
        COMPUTED_SUMMARY_CSV_PATH,
        TOP100_TO_CONFIRM_PATH,
    ]:
        if path.exists():
            shutil.copy2(path, tables_dir / path.name)

    shutil.copy2(SCRIPT_PATH, scripts_dir / SCRIPT_PATH.name)
    manifest = [
        "# Run Archive: 2026-06-24 full G3 strong composable top50 analysis",
        "",
        "This is a copy-only archive for this processing turn.",
        "",
        "Contents:",
        "- docs_phase1: top50 analysis, comparison report, next-step report, top100 guideline",
        "- tables_and_json: uploaded top50 files, computed summaries, top100 confirmation CSV",
        "- scripts: analysis/preparation script",
        "",
        "No old outputs were moved or deleted.",
    ]
    write_text(ARCHIVE_DIR / "ARCHIVE_MANIFEST.md", manifest)


def main() -> None:
    if not TOP50_FILLED_PATH.exists():
        raise SystemExit(f"Missing manual file: {TOP50_FILLED_PATH}")
    top100_rows, top100_fields = read_csv(TOP100_PATH)
    filled_rows, _filled_fields = read_csv(TOP50_FILLED_PATH)
    top50_rows = choose_top50_rows(filled_rows)
    if len(top50_rows) != 50:
        raise SystemExit(f"Expected 50 filled top50 rows, found {len(top50_rows)}")

    missing_by_col = {
        column: sum(1 for row in top50_rows if not (row.get(column, "") or "").strip())
        for column in MANUAL_COLS
    }
    invalid_values = collect_invalid_values(top50_rows)
    summary = build_summary(top50_rows)
    computed_table = computed_summary_table(top50_rows)
    mismatches = compare_supplied_summary(computed_table)
    summary["supplied_summary_csv"] = str(USER_SUMMARY_CSV_PATH)
    summary["supplied_summary_mismatch_count"] = len(mismatches)
    summary["supplied_summary_mismatches"] = mismatches

    SUMMARY_JSON_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(COMPUTED_SUMMARY_CSV_PATH, computed_table, ["field", "label", "count"])
    write_top50_report(top50_rows, summary, invalid_values, missing_by_col, mismatches)
    write_comparison_report(summary)
    write_next_step_report(summary)
    top100_rows_written = make_top100_to_confirm(top100_rows, top100_fields, top50_rows)
    write_top100_guideline()
    archive_outputs()

    print(
        json.dumps(
            {
                "summary": summary,
                "summary_json": str(SUMMARY_JSON_PATH),
                "computed_summary_csv": str(COMPUTED_SUMMARY_CSV_PATH),
                "top50_report": str(TOP50_REPORT_PATH),
                "comparison_report": str(COMPARISON_REPORT_PATH),
                "next_step_report": str(NEXT_STEP_REPORT_PATH),
                "top100_to_confirm": str(TOP100_TO_CONFIRM_PATH),
                "top100_rows_written": top100_rows_written,
                "top100_guideline": str(TOP100_GUIDELINE_PATH),
                "archive_dir": str(ARCHIVE_DIR),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
