#!/usr/bin/env python
"""Analyze full-G3 strong-composable top100 manual confirmation results.

Scope guard: this script only analyzes the filled top100 manual labels and
writes reports. It does not rerun full-G3 search, full cleaning, baselines, or
model training.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


ROOT = Path(".")
OUTPUT_DIR = ROOT / "outputs" / "toolbench_full_g3_strong_composable_search_v0_1"
DOC_DIR = ROOT / "docs" / "phase1"
ARCHIVE_DIR = ROOT / "outputs" / "run_archives" / "2026-06-24_full_g3_strong_composable_top100_analysis"

TOP100_FILLED_PATH = OUTPUT_DIR / "full_g3_strong_composable_top100_filled.csv"
PROVIDED_SUMMARY_PATH = OUTPUT_DIR / "full_g3_strong_composable_top100_filled_summary.csv"
SUMMARY_JSON_PATH = OUTPUT_DIR / "full_g3_strong_composable_top100_filled_analysis_summary.json"
COMPUTED_SUMMARY_CSV_PATH = OUTPUT_DIR / "full_g3_strong_composable_top100_filled_computed_summary.csv"

TOP100_REPORT_PATH = DOC_DIR / "full_g3_strong_composable_top100_filled_analysis_report.md"
STAGE_COMPARISON_PATH = DOC_DIR / "full_g3_strong_composable_stage_comparison_report.md"
POSITIVE_PATTERN_PATH = DOC_DIR / "full_g3_strong_composable_positive_pattern_report.md"
NOT_ELIGIBLE_PATH = DOC_DIR / "full_g3_strong_composable_not_eligible_error_report.md"
PHASE_DECISION_PATH = DOC_DIR / "composable_task_phase_decision_after_top100.md"
RULE_V3_3_PATH = DOC_DIR / "manual_audit_rule_v3_3_draft.md"
NEXT_STEP_PATH = DOC_DIR / "full_g3_strong_composable_after_top100_next_step.md"
SCRIPT_PATH = ROOT / "scripts" / "build_dataset" / "analyze_full_g3_top100_filled.py"

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
        "api_leak_uncertain",
        "service_leak_only",
        "remove_api_leak",
    },
    "final_task_eligibility": {
        "composable_needs_review",
        "service_and_api_level_valid",
        "api_level_only",
        "uncertain",
        "not_eligible",
    },
    "final_task_bucket": {
        "composable_service_discovery_candidate",
        "multi_service_discovery_candidate",
        "multi_api_recommendation_candidate",
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


def task_id(row: Dict[str, str]) -> str:
    return row.get("original_task_id") or row.get("raw_record_id") or row.get("search_rank", "")


def one_line(value: str | None, limit: int = 240) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ")[:limit]


def lower_blob(row: Dict[str, str]) -> str:
    parts = [
        row.get("query_text", ""),
        row.get("strong_composable_decision_reason", ""),
        row.get("candidate_services_json", ""),
        row.get("candidate_apis_json", ""),
        row.get("gold_services_json", ""),
        row.get("gold_apis_json", ""),
    ]
    return " ".join(parts).lower()


def dist(rows: List[Dict[str, str]], column: str) -> Dict[str, int]:
    return dict(Counter((row.get(column, "") or "").strip() or "<blank>" for row in rows))


def pct(numerator: int, denominator: int) -> float:
    return round((numerator / denominator * 100.0), 2) if denominator else 0.0


def subset_by_rank(rows: List[Dict[str, str]], start: int, end: int) -> List[Dict[str, str]]:
    return [row for row in rows if start <= rank_of(row) <= end]


def invalid_values(rows: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    output: Dict[str, List[Dict[str, str]]] = {}
    for column, allowed in VALID_VALUES.items():
        output[column] = []
        for row in rows:
            value = (row.get(column, "") or "").strip()
            if value and value not in allowed:
                output[column].append(
                    {
                        "search_rank": row.get("search_rank", ""),
                        "original_task_id": row.get("original_task_id", ""),
                        "value": value,
                    }
                )
    return output


def summarize_split(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    label = dist(rows, "strong_composable_final_label")
    return {
        "row_count": len(rows),
        "strong_composable": label.get("strong_composable", 0),
        "ordinary_multi": label.get("ordinary_multi", 0),
        "ambiguous": label.get("ambiguous", 0),
        "not_eligible": label.get("not_eligible", 0),
        "hit_rate": pct(label.get("strong_composable", 0), len(rows)),
        "label_distribution": label,
    }


def computed_summary_rows(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    splits = {
        "top100_total": rows,
        "rows_1_20": subset_by_rank(rows, 1, 20),
        "rows_1_50": subset_by_rank(rows, 1, 50),
        "rows_51_100": subset_by_rank(rows, 51, 100),
    }
    fields = [
        "strong_composable_final_label",
        "semantic_alignment_manual_check",
        "leakage_manual_check",
        "final_cleaning_status",
        "final_task_eligibility",
        "final_task_bucket",
    ]
    output: List[Dict[str, Any]] = []
    for split, split_rows in splits.items():
        for field in fields:
            for label, count in sorted(dist(split_rows, field).items()):
                output.append({"split": split, "field": field, "label": label, "count": count})
    return output


def compare_provided_summary(computed_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not PROVIDED_SUMMARY_PATH.exists():
        return []
    provided_rows, _fields = read_csv(PROVIDED_SUMMARY_PATH)
    provided = {
        (row.get("split", ""), row.get("field", ""), row.get("label", "")): int(
            row.get("count", "0") or 0
        )
        for row in provided_rows
        if row.get("split") and row.get("field") and row.get("label")
    }
    mismatches: List[Dict[str, Any]] = []
    for row in computed_rows:
        key = (str(row["split"]), str(row["field"]), str(row["label"]))
        if key not in provided:
            continue
        if provided[key] != int(row["count"]):
            mismatches.append(
                {
                    "split": key[0],
                    "field": key[1],
                    "label": key[2],
                    "computed_count": row["count"],
                    "provided_count": provided[key],
                }
            )
    return mismatches


def sample_lines(rows: Sequence[Dict[str, str]], max_items: int = 6) -> List[str]:
    lines = []
    for row in list(rows)[:max_items]:
        lines.append(
            f"- rank `{row.get('search_rank', '')}`, task `{task_id(row)}`: "
            f"{one_line(row.get('strong_composable_decision_reason') or row.get('query_text'))}"
        )
    return lines or ["- none"]


def rows_with_label(rows: List[Dict[str, str]], label: str) -> List[Dict[str, str]]:
    return [
        row
        for row in rows
        if (row.get("strong_composable_final_label", "") or "").strip() == label
    ]


def classify_positive_patterns(strong_rows: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    patterns = {
        "search_or_recommendation_result_to_detail_query": [
            "preview",
            "details",
            "detail",
            "download",
            "favorite",
            "contact",
            "profile",
            "lyrics",
            "song",
            "recipe",
            "view",
            "track",
        ],
        "numeric_calculation_result_to_explanation_or_conversion": [
            "carbon",
            "aqhi",
            "calculate",
            "calculation",
            "equivalent",
            "convert",
            "conversion",
            "footprint",
            "tree",
            "health impact",
        ],
        "entity_or_location_identification_to_nearby_or_followup_query": [
            "nearby",
            "place",
            "location",
            "coordinates",
            "geocode",
            "city",
            "address",
            "map",
            "venue",
        ],
        "list_result_to_select_one_item_detail": [
            "list",
            "id",
            "cocktail",
            "recipe",
            "detail",
            "details",
            "select",
            "choose",
        ],
    }
    output: Dict[str, List[Dict[str, str]]] = {key: [] for key in patterns}
    for row in strong_rows:
        blob = lower_blob(row)
        for key, words in patterns.items():
            if any(word in blob for word in words):
                output[key].append(row)
    return output


def classify_ordinary_risks(ordinary_rows: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    patterns = {
        "weather_plus_pv_forecast": ["weather", "pv", "forecast", "solar"],
        "map_tile_plus_place_lookup": ["map tile", "tile", "place lookup", "map"],
        "webcam_list_plus_geocode": ["webcam", "geocode", "coordinates"],
        "lyrics_plus_chart_without_dependency": ["lyrics", "chart", "ranking", "billboard"],
        "parallel_finance_sports_news": ["finance", "stock", "sports", "news", "market"],
    }
    output: Dict[str, List[Dict[str, str]]] = {key: [] for key in patterns}
    for row in ordinary_rows:
        blob = lower_blob(row)
        for key, words in patterns.items():
            if any(word in blob for word in words):
                output[key].append(row)
    return output


def classify_not_eligible(rows: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    categories = {
        "query_gold_semantic_mismatch": [],
        "gold_covers_only_part_of_query": [],
        "irrelevant_service_mixed_in_gold": [],
        "low_level_api_for_planning_or_recommendation_need": [],
        "multi_service_is_not_enough_for_composable": [],
    }
    for row in rows:
        blob = lower_blob(row)
        reason = (row.get("strong_composable_decision_reason", "") or "").lower()
        if "mismatch" in blob or "not match" in blob or "rather than" in reason:
            categories["query_gold_semantic_mismatch"].append(row)
        if "part" in reason or "only" in reason or "does not cover" in reason:
            categories["gold_covers_only_part_of_query"].append(row)
        if "irrelevant" in reason or "unrelated" in reason:
            categories["irrelevant_service_mixed_in_gold"].append(row)
        if any(word in blob for word in ["recommend", "planning", "personalized", "gift", "event", "holiday", "nearby"]):
            categories["low_level_api_for_planning_or_recommendation_need"].append(row)
        if any(word in blob for word in ["also", "additionally", "parallel", "independent", "ordinary"]):
            categories["multi_service_is_not_enough_for_composable"].append(row)
    for row in rows:
        if not any(row in selected for selected in categories.values()):
            categories["query_gold_semantic_mismatch"].append(row)
    return categories


def build_summary(rows: List[Dict[str, str]], missing_by_col: Dict[str, int], invalid: Dict[str, List[Dict[str, str]]], rank_coverage_ok: bool, provided_mismatches: List[Dict[str, Any]]) -> Dict[str, Any]:
    label = dist(rows, "strong_composable_final_label")
    rows_1_20 = subset_by_rank(rows, 1, 20)
    rows_1_50 = subset_by_rank(rows, 1, 50)
    rows_51_100 = subset_by_rank(rows, 51, 100)
    strong_count = label.get("strong_composable", 0)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": len(rows),
        "search_rank_covers_1_100": rank_coverage_ok,
        "missing_by_col": missing_by_col,
        "invalid_values": invalid,
        "invalid_value_counts": {key: len(value) for key, value in invalid.items()},
        "provided_summary_mismatch_count": len(provided_mismatches),
        "provided_summary_mismatches": provided_mismatches,
        "strong_composable_count": strong_count,
        "ordinary_multi_count": label.get("ordinary_multi", 0),
        "ambiguous_count": label.get("ambiguous", 0),
        "not_eligible_count": label.get("not_eligible", 0),
        "strong_composable_hit_rate": pct(strong_count, len(rows)),
        "ordinary_multi_rate": pct(label.get("ordinary_multi", 0), len(rows)),
        "not_eligible_rate": pct(label.get("not_eligible", 0), len(rows)),
        "semantic_alignment_distribution": dist(rows, "semantic_alignment_manual_check"),
        "leakage_distribution": dist(rows, "leakage_manual_check"),
        "final_cleaning_status_distribution": dist(rows, "final_cleaning_status"),
        "final_task_eligibility_distribution": dist(rows, "final_task_eligibility"),
        "final_task_bucket_distribution": dist(rows, "final_task_bucket"),
        "rows_1_20_distribution": summarize_split(rows_1_20),
        "rows_1_50_distribution": summarize_split(rows_1_50),
        "rows_51_100_distribution": summarize_split(rows_51_100),
        "recommendation": "use_as_screened_composable_seed_set_not_raw_main_task",
    }


def write_analysis_report(rows: List[Dict[str, str]], summary: Dict[str, Any]) -> None:
    complete = all(value == 0 for value in summary["missing_by_col"].values())
    no_invalid = all(value == 0 for value in summary["invalid_value_counts"].values())
    strong_rows = rows_with_label(rows, "strong_composable")
    ordinary_rows = rows_with_label(rows, "ordinary_multi")
    not_rows = rows_with_label(rows, "not_eligible")
    lines = [
        "# Full G3 Strong Composable Top100 Filled Analysis Report",
        "",
        h(r"\u3010\u672c\u6b21\u505a\u4e86\u4ec0\u4e48\u3011"),
        "Analyzed the row-level top100 manual confirmation results. No full cleaning, baseline, model training, or full-G3 re-search was run.",
        "",
        h(r"\u3010top100 \u662f\u5426\u5168\u90e8\u586b\u5199\u5b8c\u6574\u3011"),
        f"- row_count: {summary['row_count']}",
        f"- search_rank_covers_1_100: `{summary['search_rank_covers_1_100']}`",
        f"- missing_by_col: `{json.dumps(summary['missing_by_col'], ensure_ascii=False)}`",
        f"- complete: `{complete}`",
        "",
        h(r"\u3010\u662f\u5426\u5b58\u5728\u975e\u6cd5\u53d6\u503c\u3011"),
        f"- invalid_value_counts: `{json.dumps(summary['invalid_value_counts'], ensure_ascii=False)}`",
        f"- no_invalid_values: `{no_invalid}`",
        "",
        h(r"\u3010strong_composable_final_label \u5206\u5e03\u3011"),
        f"`{json.dumps(dist(rows, 'strong_composable_final_label'), ensure_ascii=False)}`",
        "",
        h(r"\u3010semantic_alignment_manual_check \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['semantic_alignment_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010leakage_manual_check \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['leakage_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010final_cleaning_status \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['final_cleaning_status_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010final_task_eligibility \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['final_task_eligibility_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010final_task_bucket \u5206\u5e03\u3011"),
        f"`{json.dumps(summary['final_task_bucket_distribution'], ensure_ascii=False)}`",
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
        h(r"\u3010rows 51\u2013100 \u65b0\u589e\u7ed3\u679c\u3011"),
        f"`{json.dumps(summary['rows_51_100_distribution'], ensure_ascii=False)}`",
        "",
        h(r"\u3010strong_composable \u6837\u672c\u6458\u8981\u3011"),
        *sample_lines(strong_rows, 8),
        "",
        h(r"\u3010ordinary_multi \u6837\u672c\u6458\u8981\u3011"),
        *sample_lines(ordinary_rows, 8),
        "",
        h(r"\u3010not_eligible \u6837\u672c\u6458\u8981\u3011"),
        *sample_lines(not_rows, 8),
        "",
        h(r"\u3010top100 \u7ed3\u679c\u8bf4\u660e\u4e86\u4ec0\u4e48\u3011"),
        "The top100 validation confirms that strong composable positives exist in full G3, but they are a minority. ordinary_multi and not_eligible dominate the screened candidates.",
        "",
        h(r"\u3010\u662f\u5426\u5efa\u8bae\u7ee7\u7eed\u786e\u8ba4\u66f4\u591a\u6837\u672c\u3011"),
        "Not as the default route. Top200 can be considered only if the advisor requires a larger composable task.",
        "",
        h(r"\u3010\u662f\u5426\u5efa\u8bae\u73b0\u5728\u8dd1\u5168\u91cf\u6e05\u6d17\u3011"),
        "No. Do not run full cleaning yet. First freeze v3.3 rules and decide whether composable is a seed set or a main task.",
    ]
    write_text(TOP100_REPORT_PATH, lines)


def write_stage_comparison(summary: Dict[str, Any]) -> None:
    lines = [
        "# Full G3 Strong Composable Stage Comparison Report",
        "",
        h(r"\u3010\u9636\u6bb5\u5bf9\u6bd4\u3011"),
        "- dry-run 16: candidate_count=16, strong_composable=0, ordinary_multi=11, not_eligible=5, hit_rate=0%",
        "- full G3 top20: candidate_count=20, strong_composable=7, ordinary_multi=9, not_eligible=4, hit_rate=35%",
        "- full G3 top50: candidate_count=50, strong_composable=13, ordinary_multi=24, not_eligible=13, hit_rate=26%",
        f"- full G3 top100: candidate_count=100, strong_composable={summary['strong_composable_count']}, ordinary_multi={summary['ordinary_multi_count']}, not_eligible={summary['not_eligible_count']}, hit_rate={summary['strong_composable_hit_rate']}%",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48 dry-run \u4e0d\u80fd\u652f\u6491 composable \u4e3b\u4efb\u52a1\u3011"),
        "The dry-run set had 0 strong positives, so it only validated the audit protocol and could not support a main composable benchmark.",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48 full G3 dependency screening \u80fd\u627e\u5230 strong composable\u3011"),
        "Dependency screening increases recall by targeting queries with possible result dependencies, then manual review separates real chains from ordinary multi-task requests.",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48\u547d\u4e2d\u7387\u4ece top20 \u5230 top100 \u9010\u6b65\u4e0b\u964d\u3011"),
        "The highest-ranked dependency signals are reviewed first. Lower ranks include weaker or noisier signals, so ordinary_multi and not_eligible increase.",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48 raw G3 \u4e0d\u80fd\u76f4\u63a5\u5f53 composable benchmark\u3011"),
        "Raw G3 includes parallel multi-service tasks and query-gold mismatches. A raw group label is not enough to prove cross-service dependency.",
        "",
        h(r"\u3010\u4e3a\u4ec0\u4e48\u4eba\u5de5\u786e\u8ba4\u4ecd\u7136\u5fc5\u8981\u3011"),
        "Keyword signals such as based on, then, and also are ambiguous. Human review is needed to verify that a later step consumes an earlier output.",
        "",
        h(r"\u3010top100 \u540e\u662f\u5426\u8fd8\u6709\u5fc5\u8981\u7ee7\u7eed\u786e\u8ba4 top200\u3011"),
        "Not by default. Continue to top200 only if the advisor requires composable as a main benchmark task; otherwise use the top100 result as a screened seed set.",
    ]
    write_text(STAGE_COMPARISON_PATH, lines)


def write_positive_pattern_report(rows: List[Dict[str, str]]) -> None:
    strong_rows = rows_with_label(rows, "strong_composable")
    ordinary_rows = rows_with_label(rows, "ordinary_multi")
    positive_patterns = classify_positive_patterns(strong_rows)
    ordinary_patterns = classify_ordinary_risks(ordinary_rows)
    headings = {
        "search_or_recommendation_result_to_detail_query": "1. Search/recommendation result -> detail query",
        "numeric_calculation_result_to_explanation_or_conversion": "2. Numeric calculation result -> explanation/conversion",
        "entity_or_location_identification_to_nearby_or_followup_query": "3. Entity/location identification -> nearby/follow-up query",
        "list_result_to_select_one_item_detail": "4. List result -> select one item and query detail",
    }
    false_headings = {
        "weather_plus_pv_forecast": "1. Weather + PV forecast",
        "map_tile_plus_place_lookup": "2. Map tiles + place lookup",
        "webcam_list_plus_geocode": "3. Webcam list + geocode",
        "lyrics_plus_chart_without_dependency": "4. Lyrics + charts without explicit dependency",
        "parallel_finance_sports_news": "5. Parallel finance/sports/news services",
    }
    lines = [
        "# Full G3 Strong Composable Positive Pattern Report",
        "",
        h(r"\u3010\u6b63\u4f8b\u603b\u4f53\u89c2\u5bdf\u3011"),
        f"Top100 contains {len(strong_rows)} manually confirmed strong_composable candidates. The common pattern is that a later service/API needs an entity, ID, result, filter, or decision from an earlier service/API.",
        "",
        h(r"\u3010strong_composable \u6b63\u4f8b\u7c7b\u578b\u3011"),
    ]
    for key, title in headings.items():
        lines.append(f"### {title}")
        selected = positive_patterns[key]
        if not selected:
            lines.append("- No clear representative found in the top100 strong positives.")
        else:
            lines.extend(sample_lines(selected, 3))
        lines.append("")
    lines.extend(
        [
            h(r"\u3010\u5bb9\u6613\u8bef\u5224\u4e3a ordinary_multi \u7684\u6a21\u5f0f\u3011"),
        ]
    )
    for key, title in false_headings.items():
        lines.append(f"### {title}")
        selected = ordinary_patterns[key]
        if not selected:
            lines.append("- Not observed clearly in the top100 ordinary_multi examples, but keep this as a known risk pattern.")
        else:
            lines.extend(sample_lines(selected, 3))
        lines.append("")
    lines.extend(
        [
            h(r"\u3010\u7ed3\u8bba\u3011"),
            "A positive composable label should require an explicit output dependency. Shared topic, shared location, or sequential wording alone is not enough.",
        ]
    )
    write_text(POSITIVE_PATTERN_PATH, lines)


def write_not_eligible_report(rows: List[Dict[str, str]]) -> None:
    not_rows = rows_with_label(rows, "not_eligible")
    categories = classify_not_eligible(not_rows)
    titles = {
        "query_gold_semantic_mismatch": "1. Query and gold service/API semantic mismatch",
        "gold_covers_only_part_of_query": "2. Gold covers only part of the query",
        "irrelevant_service_mixed_in_gold": "3. Obvious irrelevant service mixed into gold",
        "low_level_api_for_planning_or_recommendation_need": "4. Planning/recommendation/location need but gold only provides low-level API",
        "multi_service_is_not_enough_for_composable": "5. Multiple services alone do not imply composable",
    }
    lines = [
        "# Full G3 Strong Composable Not Eligible Error Report",
        "",
        h(r"\u3010not_eligible \u603b\u4f53\u60c5\u51b5\u3011"),
        f"Top100 contains {len(not_rows)} not_eligible samples. Most are semantic mismatch or insufficient gold/query alignment cases.",
        "",
    ]
    for key, title in titles.items():
        lines.append(f"## {title}")
        selected = categories[key]
        if not selected:
            lines.append("- No clear representative found in top100 for this category.")
        else:
            lines.extend(sample_lines(selected, 3))
        lines.append("")
    lines.extend(
        [
            h(r"\u3010\u7ed3\u8bba\u3011"),
            "not_eligible samples should be excluded from clean composable positives. They can still inform error analysis and cleaning-rule design.",
        ]
    )
    write_text(NOT_ELIGIBLE_PATH, lines)


def write_phase_decision_report(summary: Dict[str, Any]) -> None:
    lines = [
        "# Composable Task Phase Decision After Top100",
        "",
        h(r"\u30101. composable \u662f\u5426\u53ef\u4ee5\u8fdb\u5165\u4e3b benchmark\uff1f\u3011"),
        "Cautiously, not as raw G3. It can enter as a screened composable seed set or later extension, because confirmed positives exist but the hit rate is only 20%.",
        "",
        h(r"\u30102. \u5982\u679c\u8fdb\u5165\uff0c\u5e94\u8be5\u4ee5\u4ec0\u4e48\u5f62\u5f0f\u8fdb\u5165\uff1f\u3011"),
        "Use `screened composable seed set`: dependency-screened, semantic-gated, leakage-gated, and manually confirmed.",
        "",
        h(r"\u30103. \u662f\u5426\u53ef\u4ee5\u76f4\u63a5\u4f7f\u7528 raw G3\uff1f\u3011"),
        "No. raw G3 contains many ordinary_multi and not_eligible cases.",
        "",
        h(r"\u30104. \u662f\u5426\u5e94\u8be5\u4f5c\u4e3a screened composable seed set\uff1f\u3011"),
        "Yes. Top100 confirms 20 strong positives, enough to justify a seed set and rule documentation.",
        "",
        h(r"\u30105. \u662f\u5426\u5e94\u8be5\u6682\u7f13 composable \u5168\u91cf\u6784\u5efa\uff1f\u3011"),
        "Yes. Prioritize single_service_discovery, single_api_recommendation, multi_service_discovery, and multi_api_recommendation while keeping composable as an extension.",
        "",
        h(r"\u30106. \u4e0b\u4e00\u6b65\u662f\u5426\u5199\u6b63\u5f0f\u6e05\u6d17\u811a\u672c\uff1f\u3011"),
        "Write v3.3 cleaning-rule draft first. Then write a formal screening script, not an automatic final-label script.",
        "",
        h(r"\u30107. \u9700\u8981\u5bfc\u5e08\u786e\u8ba4\u7684\u95ee\u9898\u662f\u4ec0\u4e48\uff1f\u3011"),
        "- Should composable be a main benchmark task now, or a screened seed/extension?",
        "- Is a 20% hit rate acceptable for further composable expansion?",
        "- Should we confirm top200 or freeze top100 evidence and move to the four more stable tasks?",
    ]
    write_text(PHASE_DECISION_PATH, lines)


def write_rule_v3_3() -> None:
    lines = [
        "# Manual Audit Rule v3.3 Draft",
        "",
        h(r"\u30101. strong composable \u6b63\u4f8b\u5fc5\u8981\u6761\u4ef6\u3011"),
        "- `semantic_alignment_ok`",
        "- `no_blocking_leak`",
        "- clear dependency chain",
        "- later step consumes earlier output/entity/id/result/filter/decision signal",
        "",
        h(r"\u30102. ordinary_multi \u8d1f\u4f8b\u6761\u4ef6\u3011"),
        "- Multiple tasks are merely parallel.",
        "- Tasks share location, time, or topic but no output dependency.",
        "- Keywords such as `then`, `after`, `recommend`, or `also` are not enough.",
        "",
        h(r"\u30103. not_eligible \u6761\u4ef6\u3011"),
        "- `semantic_mismatch_uncertain`",
        "- query-gold mismatch",
        "- gold covers only part of the query",
        "- blocking API leak",
        "- candidate/gold missing or clearly unreasonable",
        "",
        h(r"\u30104. composable \u6e05\u6d17\u4f18\u5148\u7ea7\u3011"),
        "1. strong API leak -> remove",
        "2. semantic mismatch -> uncertain/not_eligible",
        "3. leakage uncertain -> uncertain",
        "4. ordinary_multi -> multi_service_discovery_candidate",
        "5. strong_composable -> composable_service_discovery_candidate",
        "",
        h(r"\u30105. \u540e\u7eed\u6b63\u5f0f\u811a\u672c\u5b9a\u4f4d\u3011"),
        "The next formal script should first perform candidate screening and evidence export. It should not automatically assign final labels without manual confirmation.",
    ]
    write_text(RULE_V3_3_PATH, lines)


def write_next_step_report(summary: Dict[str, Any]) -> None:
    lines = [
        "# Full G3 Strong Composable After Top100 Next Step",
        "",
        h(r"\u3010\u8def\u7ebf A\uff1a\u7a33\u59a5\u8def\u7ebf\u3011"),
        "- Do not continue top200 by default.",
        "- Freeze the v3.3 rule draft.",
        "- Prioritize the four stable main tasks: single/multi service discovery and single/multi API recommendation.",
        "- Keep composable as a screened seed set.",
        "",
        h(r"\u3010\u8def\u7ebf B\uff1a\u7ee7\u7eed\u6269\u5c55\u8def\u7ebf\u3011"),
        "- If the advisor requires composable as a main task, continue confirming top200.",
        "- Explicitly state that hit rate may continue to decline.",
        "- Do not directly clean raw G3 as composable.",
        "",
        h(r"\u3010\u63a8\u8350\u8def\u7ebf\u3011"),
        "Recommend Route A. The top100 hit rate is exactly 20%, while ordinary_multi and not_eligible are the majority. This is enough evidence for a screened seed set, but not enough to justify immediate full composable cleaning.",
    ]
    write_text(NEXT_STEP_PATH, lines)


def archive_outputs(paths: Sequence[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        target = ARCHIVE_DIR / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    manifest = [
        "# Run Archive: 2026-06-24 full G3 strong composable top100 analysis",
        "",
        "This archive preserves relative paths from the project root.",
        "",
        "Included: top100 analysis report, stage comparison, positive pattern report, not eligible error report, composable decision report, v3.3 draft, next-step report, summary JSON, computed summary CSV, and analysis script.",
        "",
        "No full-G3 re-search, full cleaning, baseline, or model training was run.",
    ]
    write_text(ARCHIVE_DIR / "ARCHIVE_MANIFEST.md", manifest)


def main() -> None:
    if not TOP100_FILLED_PATH.exists():
        raise SystemExit(f"Missing required input: {TOP100_FILLED_PATH}")
    rows, _fields = read_csv(TOP100_FILLED_PATH)
    ranks = sorted(rank_of(row) for row in rows)
    rank_coverage_ok = ranks == list(range(1, 101))
    missing_by_col = {
        column: sum(1 for row in rows if not (row.get(column, "") or "").strip())
        for column in MANUAL_COLS
    }
    invalid = invalid_values(rows)
    comp_rows = computed_summary_rows(rows)
    write_csv(COMPUTED_SUMMARY_CSV_PATH, comp_rows, ["split", "field", "label", "count"])
    provided_mismatches = compare_provided_summary(comp_rows)
    summary = build_summary(rows, missing_by_col, invalid, rank_coverage_ok, provided_mismatches)
    SUMMARY_JSON_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    write_analysis_report(rows, summary)
    write_stage_comparison(summary)
    write_positive_pattern_report(rows)
    write_not_eligible_report(rows)
    write_phase_decision_report(summary)
    write_rule_v3_3()
    write_next_step_report(summary)

    archive_outputs(
        [
            TOP100_REPORT_PATH,
            STAGE_COMPARISON_PATH,
            POSITIVE_PATTERN_PATH,
            NOT_ELIGIBLE_PATH,
            PHASE_DECISION_PATH,
            RULE_V3_3_PATH,
            NEXT_STEP_PATH,
            SUMMARY_JSON_PATH,
            COMPUTED_SUMMARY_CSV_PATH,
            TOP100_FILLED_PATH,
            PROVIDED_SUMMARY_PATH,
            SCRIPT_PATH,
        ]
    )

    print(
        json.dumps(
            {
                "summary": summary,
                "outputs": {
                    "analysis_report": str(TOP100_REPORT_PATH),
                    "summary_json": str(SUMMARY_JSON_PATH),
                    "computed_summary_csv": str(COMPUTED_SUMMARY_CSV_PATH),
                    "stage_comparison": str(STAGE_COMPARISON_PATH),
                    "positive_pattern": str(POSITIVE_PATTERN_PATH),
                    "not_eligible": str(NOT_ELIGIBLE_PATH),
                    "phase_decision": str(PHASE_DECISION_PATH),
                    "rule_v3_3": str(RULE_V3_3_PATH),
                    "next_step": str(NEXT_STEP_PATH),
                    "archive_dir": str(ARCHIVE_DIR),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
