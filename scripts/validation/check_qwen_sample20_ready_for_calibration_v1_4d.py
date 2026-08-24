from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from qwen_semcap_v1_4d_common import DOC_DIR, EVAL_DIR, ensure_dir, now_text, read_csv, table_lines, write_json, write_md


ALLOWED_SEMANTIC = {"ok", "uncertain", "mismatch"}
ALLOWED_COVERAGE = {"coverage_ok", "coverage_uncertain", "coverage_mismatch"}


def pct(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether Qwen sample20 is ready before calibration180.")
    parser.add_argument(
        "--sample20-predictions",
        type=Path,
        default=Path("outputs/qwen_semcap_judge_v1_4d/predictions/qwen_semcap_predictions_sample_20.csv"),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=EVAL_DIR / "qwen_sample20_ready_check_v1_4d.json",
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=DOC_DIR / "qwen_sample20_ready_check_report_v1_4d.md",
    )
    args = parser.parse_args()

    if not args.sample20_predictions.exists():
        raise FileNotFoundError(f"Missing sample20 prediction CSV: {args.sample20_predictions}")

    rows = read_csv(args.sample20_predictions)
    parse_counts = Counter(row.get("QWEN_parse_status", "") or "<blank>" for row in rows)
    semantic_counts = Counter(row.get("QWEN_semantic_alignment_check", "") or "<blank>" for row in rows)
    coverage_counts = Counter(row.get("QWEN_capability_coverage_check", "") or "<blank>" for row in rows)
    invalid_semantic = [
        row.get("task_id", "")
        for row in rows
        if row.get("QWEN_semantic_alignment_check", "") not in ALLOWED_SEMANTIC
    ]
    invalid_coverage = [
        row.get("task_id", "")
        for row in rows
        if row.get("QWEN_capability_coverage_check", "") not in ALLOWED_COVERAGE
    ]

    row_count = len(rows)
    ok_count = parse_counts.get("ok", 0)
    schema_failed_count = parse_counts.get("schema_failed", 0)
    invalid_enum_count = len(invalid_semantic) + len(invalid_coverage)
    summary = {
        "generated_time": now_text(),
        "input_file": str(args.sample20_predictions),
        "row_count": row_count,
        "parse_ok_count": ok_count,
        "sample20_parse_ok_rate": pct(ok_count, row_count),
        "schema_failed_count": schema_failed_count,
        "invalid_enum_count": invalid_enum_count,
        "semantic_all_ok_rate": pct(semantic_counts.get("ok", 0), row_count),
        "capability_distribution": dict(coverage_counts),
        "semantic_distribution": dict(semantic_counts),
        "parse_status_distribution": dict(parse_counts),
        "invalid_semantic_task_ids": invalid_semantic,
        "invalid_coverage_task_ids": invalid_coverage,
        "ready_for_calibration": row_count == 20 and ok_count == 20 and schema_failed_count == 0 and invalid_enum_count == 0,
    }
    write_json(args.output_json, summary)

    lines = [
        "# Qwen Sample20 Ready Check Report v1.4d",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Input file: `{args.sample20_predictions}`",
        f"Sample count: {row_count}",
        "",
        "## Result",
        "",
        f"- ready_for_calibration: {str(summary['ready_for_calibration']).lower()}",
        f"- sample20_parse_ok_rate: {summary['sample20_parse_ok_rate']}",
        f"- schema_failed_count: {schema_failed_count}",
        f"- invalid_enum_count: {invalid_enum_count}",
        f"- semantic_all_ok_rate: {summary['semantic_all_ok_rate']}",
        "",
        "## Parse Status Distribution",
        "",
        *table_lines(parse_counts),
        "",
        "## Semantic Distribution",
        "",
        *table_lines(semantic_counts),
        "",
        "## Capability Distribution",
        "",
        *table_lines(coverage_counts),
        "",
        "This report only checks sample20 format stability. It does not validate Qwen judgment quality.",
        "No full cleaning, split, baseline, or training is performed.",
    ]
    write_md(args.output_report, lines)

    print(f"sample20 rows: {row_count}")
    print(f"parse_ok_rate: {summary['sample20_parse_ok_rate']}")
    print(f"schema_failed_count: {schema_failed_count}")
    print(f"invalid_enum_count: {invalid_enum_count}")
    print(f"ready_for_calibration: {summary['ready_for_calibration']}")
    return 0 if summary["ready_for_calibration"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
