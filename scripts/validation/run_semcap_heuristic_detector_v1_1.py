from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, List

from semcap_v1_1_common import (
    PREDICTION_FIELDS,
    ensure_dir,
    now_text,
    read_csv,
    run_semcap_v1_detector,
    table_lines,
    value_counter,
    write_csv,
    write_md,
)


DEFAULT_OUTPUT_DIR = Path("outputs/semcap_detector_v1_implementation_v1_1")
COMBINED = DEFAULT_OUTPUT_DIR / "combined_semcap_calibration_180.csv"
V0_8_INPUT = Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_input_tasks.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SemCap heuristic detector v1.1.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current working directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--combined-input", type=Path, default=COMBINED, help="Combined calibration CSV.")
    parser.add_argument("--v0-8-input", type=Path, default=V0_8_INPUT, help="v0.8 sample task-level CSV.")
    return parser.parse_args()


def run_for_rows(rows: List[Dict[str, str]], record_field: str) -> List[Dict[str, object]]:
    predictions = []
    for row in rows:
        record_id = row.get(record_field, "") or row.get("record_id", "") or row.get("task_id", "")
        predictions.append(run_semcap_v1_detector(row, record_id=record_id))
    return predictions


def make_report(output_path: Path, combined_rows: List[Dict[str, str]], v08_rows: List[Dict[str, str]], combined_pred: List[Dict[str, object]], v08_pred: List[Dict[str, object]], input_paths: List[Path]) -> None:
    lines = [
        "# SemCap Heuristic Detector v1.1 Report",
        "",
        f"Generated time: {now_text()}",
        "",
        "Input files:",
        *[f"- `{path}`" for path in input_paths],
        "",
        f"Combined calibration sample count: {len(combined_rows)}",
        f"v0.8 sample count: {len(v08_rows)}",
        "",
        "Scope: detector prediction only. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Detector boundary",
        "",
        "The detector predicts only semantic alignment and capability coverage. `coverage_ok_but_policy_blocked_candidate` is an advisory explanation path, not a final cleaning decision.",
        "",
        "## Combined capability prediction distribution",
        "",
        *table_lines(value_counter(combined_pred, "capability_coverage_pred")),
        "",
        "## Combined semantic prediction distribution",
        "",
        *table_lines(value_counter(combined_pred, "semantic_alignment_pred")),
        "",
        "## v0.8 capability prediction distribution",
        "",
        *table_lines(value_counter(v08_pred, "capability_coverage_pred")),
        "",
        "## v0.8 semantic prediction distribution",
        "",
        *table_lines(value_counter(v08_pred, "semantic_alignment_pred")),
        "",
        "## coverage_ok_but_policy_blocked_candidate",
        "",
        f"- combined: {sum(1 for row in combined_pred if row.get('coverage_ok_but_policy_blocked_candidate') == 'true')}",
        f"- v0.8 sample: {sum(1 for row in v08_pred if row.get('coverage_ok_but_policy_blocked_candidate') == 'true')}",
        "",
        "## Representative high-confidence coverage_ok predictions",
        "",
        "| record_id | task_id | reason |",
        "|---|---|---|",
    ]
    for row in [item for item in combined_pred if item.get("capability_coverage_pred") == "coverage_ok" and item.get("capability_coverage_confidence") == "high"][:12]:
        lines.append(f"| {row.get('record_id')} | {row.get('task_id')} | {str(row.get('capability_coverage_reason', ''))[:180]} |")
    lines.extend(
        [
            "",
            "## Representative coverage_mismatch predictions",
            "",
            "| record_id | task_id | reason |",
            "|---|---|---|",
        ]
    )
    for row in [item for item in combined_pred if item.get("capability_coverage_pred") == "coverage_mismatch"][:12]:
        lines.append(f"| {row.get('record_id')} | {row.get('task_id')} | {str(row.get('capability_coverage_reason', ''))[:180]} |")
    write_md(output_path, lines)


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    combined_rows = read_csv(root / args.combined_input)
    v08_rows = read_csv(root / args.v0_8_input)

    combined_pred = run_for_rows(combined_rows, "record_id")
    round3_pred = [row for row in combined_pred if next((src for src in combined_rows if src.get("record_id") == row.get("record_id")), {}).get("calibration_source") == "round3"]
    semcap80_pred = [row for row in combined_pred if next((src for src in combined_rows if src.get("record_id") == row.get("record_id")), {}).get("calibration_source") == "semcap80"]
    v08_pred = run_for_rows(v08_rows, "v0_8_sample_id")

    write_csv(output_dir / "semcap_predictions_combined_180_v1.csv", combined_pred, PREDICTION_FIELDS)
    write_csv(output_dir / "semcap_predictions_round3_v1.csv", round3_pred, PREDICTION_FIELDS)
    write_csv(output_dir / "semcap_predictions_semcap80_v1.csv", semcap80_pred, PREDICTION_FIELDS)
    write_csv(output_dir / "semcap_predictions_v0_8_sample_v1.csv", v08_pred, PREDICTION_FIELDS)
    make_report(
        root / "docs/phase1/semcap_heuristic_detector_v1_1_report.md",
        combined_rows,
        v08_rows,
        combined_pred,
        v08_pred,
        [args.combined_input, args.v0_8_input],
    )

    print("Wrote SemCap v1.1 predictions.")
    print("combined rows:", len(combined_pred))
    print("round3 rows:", len(round3_pred))
    print("semcap80 rows:", len(semcap80_pred))
    print("v0.8 rows:", len(v08_pred))
    print("combined capability distribution:", value_counter(combined_pred, "capability_coverage_pred"))
    print("No full cleaning, split, baseline, or model training was run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
