#!/usr/bin/env python
"""Generate the full-G3 top100 manual-confirmation CSV from existing files.

Scope guard: this script only merges existing top50 manual labels into the
existing top100 candidate table. It does not rerun full-G3 search, full
cleaning, baselines, or model training.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ROOT = Path(".")
OUTPUT_DIR = ROOT / "outputs" / "toolbench_full_g3_strong_composable_search_v0_1"
DOC_DIR = ROOT / "docs" / "phase1"
ARCHIVE_DIR = ROOT / "outputs" / "run_archives" / "2026-06-24_full_g3_top100_to_confirm_generation"

TOP100_PATH = OUTPUT_DIR / "full_g3_strong_composable_candidates_top100.csv"
TOP50_FILLED_PATH = OUTPUT_DIR / "full_g3_strong_composable_top50_filled.csv"
TOP100_TO_CONFIRM_PATH = OUTPUT_DIR / "full_g3_strong_composable_top100_to_confirm.csv"
SUMMARY_PATH = OUTPUT_DIR / "full_g3_strong_composable_top100_to_confirm_generation_summary.json"
REPORT_PATH = DOC_DIR / "full_g3_strong_composable_top100_to_confirm_generation_report.md"
SCRIPT_PATH = ROOT / "scripts" / "build_dataset" / "generate_full_g3_top100_to_confirm.py"

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


def rank_text(row: Dict[str, str]) -> str:
    return (row.get("search_rank", "") or "").strip()


def original_task_id(row: Dict[str, str]) -> str:
    return (row.get("original_task_id", "") or "").strip()


def is_nonblank(value: str | None) -> bool:
    return bool((value or "").strip())


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


def prepare_output_rows(
    top100_rows: List[Dict[str, str]],
    top100_fields: List[str],
    top50_rows: List[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], List[str], Dict[str, Any]]:
    output_fields = list(top100_fields)
    for column in MANUAL_COLS:
        if column not in output_fields:
            output_fields.append(column)

    filled_by_rank = {rank_text(row): row for row in top50_rows if rank_text(row)}
    filled_by_task_id = {
        original_task_id(row): row for row in top50_rows if original_task_id(row)
    }

    output_rows: List[Dict[str, str]] = []
    merge_details: List[Dict[str, str]] = []
    for index, source_row in enumerate(top100_rows[:100], start=1):
        output_row = {column: source_row.get(column, "") for column in output_fields}
        if index <= 50:
            matched_row = None
            match_key = ""
            if rank_text(source_row) and rank_text(source_row) in filled_by_rank:
                matched_row = filled_by_rank[rank_text(source_row)]
                match_key = f"search_rank={rank_text(source_row)}"
            elif original_task_id(source_row) and original_task_id(source_row) in filled_by_task_id:
                matched_row = filled_by_task_id[original_task_id(source_row)]
                match_key = f"original_task_id={original_task_id(source_row)}"
            if matched_row:
                for column in MANUAL_COLS:
                    output_row[column] = matched_row.get(column, "")
                merge_details.append(
                    {
                        "row_index": str(index),
                        "search_rank": rank_text(source_row),
                        "original_task_id": original_task_id(source_row),
                        "match_key": match_key,
                        "merged_label": matched_row.get("strong_composable_final_label", ""),
                    }
                )
        else:
            for column in MANUAL_COLS:
                output_row[column] = ""
        output_rows.append(output_row)

    first50_rows = output_rows[:50]
    later_rows = output_rows[50:100]
    first50_merged_count = sum(
        1 for row in first50_rows if is_nonblank(row.get("strong_composable_final_label"))
    )
    later_blank_by_col = {
        column: sum(1 for row in later_rows if not is_nonblank(row.get(column)))
        for column in MANUAL_COLS
    }
    invalid_values = collect_invalid_values(first50_rows)
    stats = {
        "merge_details": merge_details,
        "first50_merged_count": first50_merged_count,
        "later_blank_by_col": later_blank_by_col,
        "rows_51_100_all_manual_cols_blank": all(value == len(later_rows) for value in later_blank_by_col.values()),
        "invalid_values": invalid_values,
        "invalid_value_counts": {column: len(values) for column, values in invalid_values.items()},
    }
    return output_rows, output_fields, stats


def write_report(
    top100_rows: List[Dict[str, str]],
    top50_rows: List[Dict[str, str]],
    output_rows: List[Dict[str, str]],
    stats: Dict[str, Any],
    missing_inputs: List[str],
) -> None:
    input_status = {
        str(TOP100_PATH): TOP100_PATH.exists(),
        str(TOP50_FILLED_PATH): TOP50_FILLED_PATH.exists(),
    }
    no_invalid = all(count == 0 for count in stats["invalid_value_counts"].values())
    lines = [
        "# Full G3 Strong Composable Top100 To Confirm Generation Report",
        "",
        h(r"\u3010\u672c\u6b21\u505a\u4e86\u4ec0\u4e48\u3011"),
        "Read the existing top100 candidate table and the existing top50 filled manual labels, then generated a top100 manual-confirmation table. Rows 1-50 keep the existing manual labels; rows 51-100 are blank for human confirmation.",
        "",
        h(r"\u3010\u8f93\u5165\u6587\u4ef6\u662f\u5426\u5b58\u5728\u3011"),
        f"`{json.dumps(input_status, ensure_ascii=False)}`",
        f"- missing_required_inputs: `{json.dumps(missing_inputs, ensure_ascii=False)}`",
        "",
        h(r"\u3010top100 \u5019\u9009\u8868\u884c\u6570\u3011"),
        f"- {len(top100_rows)}",
        "",
        h(r"\u3010top50 \u5df2\u786e\u8ba4\u8868\u884c\u6570\u3011"),
        f"- {len(top50_rows)}",
        "",
        h(r"\u3010\u8f93\u51fa top100_to_confirm \u884c\u6570\u3011"),
        f"- {len(output_rows)}",
        "",
        h(r"\u3010\u524d 50 \u884c\u662f\u5426\u6210\u529f\u5408\u5e76\u4eba\u5de5\u6807\u7b7e\u3011"),
        f"- merged_filled_rows: {stats['first50_merged_count']}",
        f"- success: `{stats['first50_merged_count'] == 50}`",
        "",
        h(r"\u3010\u7b2c 51\u2013100 \u884c\u4eba\u5de5\u5217\u662f\u5426\u4e3a\u7a7a\u3011"),
        f"- blank_by_col: `{json.dumps(stats['later_blank_by_col'], ensure_ascii=False)}`",
        f"- all_blank: `{stats['rows_51_100_all_manual_cols_blank']}`",
        "- 第 51–100 行等待人工确认。",
        "",
        h(r"\u3010\u662f\u5426\u5b58\u5728\u975e\u6cd5\u53d6\u503c\u3011"),
        f"- invalid_value_counts: `{json.dumps(stats['invalid_value_counts'], ensure_ascii=False)}`",
        f"- no_invalid_values: `{no_invalid}`",
        "",
        h(r"\u3010\u662f\u5426\u91cd\u65b0\u641c\u7d22 full G3\u3011"),
        "- 没有重新搜索 full G3。",
        "",
        h(r"\u3010\u662f\u5426\u8dd1\u5168\u91cf\u6e05\u6d17 / baseline / \u8bad\u7ec3\u3011"),
        "- 没有跑全量清洗。",
        "- 没有 baseline。",
        "- 没有训练模型。",
    ]
    write_text(REPORT_PATH, lines)


def archive_outputs() -> None:
    docs_dir = ARCHIVE_DIR / "docs_phase1"
    tables_dir = ARCHIVE_DIR / "tables_and_json"
    scripts_dir = ARCHIVE_DIR / "scripts"
    for directory in [docs_dir, tables_dir, scripts_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    for path in [REPORT_PATH]:
        shutil.copy2(path, docs_dir / path.name)
    for path in [TOP100_TO_CONFIRM_PATH, SUMMARY_PATH]:
        shutil.copy2(path, tables_dir / path.name)
    shutil.copy2(SCRIPT_PATH, scripts_dir / SCRIPT_PATH.name)
    manifest = [
        "# Run Archive: 2026-06-24 full G3 top100 to confirm generation",
        "",
        "This is a copy-only archive for this processing turn.",
        "",
        "Contents:",
        "- docs_phase1: generation report",
        "- tables_and_json: generated top100_to_confirm CSV and summary JSON",
        "- scripts: generation script",
        "",
        "No old outputs were moved or deleted.",
        "No full-G3 search, full cleaning, baseline, or model training was run.",
    ]
    write_text(ARCHIVE_DIR / "ARCHIVE_MANIFEST.md", manifest)


def main() -> None:
    missing_inputs = []
    if not TOP100_PATH.exists():
        missing_inputs.append(str(TOP100_PATH))
    if not TOP50_FILLED_PATH.exists():
        missing_inputs.append(str(TOP50_FILLED_PATH))
    if missing_inputs:
        print(json.dumps({"missing_required_inputs": missing_inputs}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    top100_rows, top100_fields = read_csv(TOP100_PATH)
    top50_rows, _top50_fields = read_csv(TOP50_FILLED_PATH)
    output_rows, output_fields, stats = prepare_output_rows(top100_rows, top100_fields, top50_rows)
    output_count = write_csv(TOP100_TO_CONFIRM_PATH, output_rows, output_fields)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "top100_rows": len(top100_rows[:100]),
        "merged_filled_rows": stats["first50_merged_count"],
        "blank_rows_to_confirm": 50,
        "missing_required_inputs": missing_inputs,
        "output_file": str(TOP100_TO_CONFIRM_PATH),
        "ready_for_human_confirmation": (
            output_count == 100
            and stats["first50_merged_count"] == 50
            and stats["rows_51_100_all_manual_cols_blank"]
            and not missing_inputs
        ),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(top100_rows[:100], top50_rows, output_rows, stats, missing_inputs)
    archive_outputs()

    print(
        json.dumps(
            {
                "summary": summary,
                "report": str(REPORT_PATH),
                "archive_dir": str(ARCHIVE_DIR),
                "manual_col_blank_check": stats["later_blank_by_col"],
                "invalid_value_counts": stats["invalid_value_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
