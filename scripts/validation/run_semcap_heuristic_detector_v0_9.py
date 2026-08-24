"""Run conservative heuristic semantic/capability detector pilot v0.9."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from semcap_detector_v0_9_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    PREDICTION_COLUMNS,
    count_by,
    distribution_rows,
    ensure_dirs,
    markdown_table,
    now_str,
    read_csv,
    semcap_predict,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run v0.9 semcap heuristic detector on Round3 and v0.8 small sample.")
    parser.add_argument("--round3-input", type=Path, default=OUTPUT_DIR / "semcap_calibration_round3_100.csv")
    parser.add_argument("--v08-input", type=Path, default=Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_detector_trace.csv"))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def run_predictions(rows: List[Dict[str, str]], id_col: str) -> List[Dict[str, object]]:
    preds = []
    for idx, row in enumerate(rows, start=1):
        record_id = row.get(id_col) or row.get("v0_8_sample_id") or row.get("round3_review_id") or f"REC-{idx:03d}"
        preds.append(semcap_predict(row, record_id))
    return preds


def write_report(path: Path, round3_preds: List[Dict[str, object]], v08_preds: List[Dict[str, object]]) -> None:
    lines = [
        "# SemCap Heuristic Detector Report v0.9",
        "",
        f"Generated time: {now_str()}",
        f"Round3 prediction rows: {len(round3_preds)}",
        f"v0.8 sample prediction rows: {len(v08_preds)}",
        "",
        "Scope: heuristic detector pilot only. Predictions are not human final labels. No full cleaning, final clean dataset, split, baseline, or model training was run.",
        "",
        "The detector is conservative: uncertainty is preferred over unsupported high-confidence ok.",
    ]
    for title, rows in [("Round3", round3_preds), ("v0.8 Sample", v08_preds)]:
        lines.extend(["", f"## {title} semantic_alignment_pred", ""])
        lines.extend(markdown_table(distribution_rows(rows, "semantic_alignment_pred"), ["value", "count"], max_rows=20))
        lines.extend(["", f"## {title} capability_coverage_pred", ""])
        lines.extend(markdown_table(distribution_rows(rows, "capability_coverage_pred"), ["value", "count"], max_rows=20))
        lines.extend(["", f"## {title} requires_human_review", ""])
        lines.extend(markdown_table(distribution_rows(rows, "requires_human_review"), ["value", "count"], max_rows=20))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.round3_input.exists() or not args.v08_input.exists():
        print("ERROR: missing semcap detector input.")
        return 1
    _, round3_rows = read_csv(args.round3_input)
    _, v08_rows = read_csv(args.v08_input)
    round3_preds = run_predictions(round3_rows, "round3_review_id")
    v08_preds = run_predictions(v08_rows, "v0_8_sample_id")
    round3_out = args.output_dir / "semcap_predictions_round3_heuristic.csv"
    v08_out = args.output_dir / "semcap_predictions_v0_8_sample_heuristic.csv"
    write_csv(round3_out, round3_preds, PREDICTION_COLUMNS)
    write_csv(v08_out, v08_preds, PREDICTION_COLUMNS)
    report = args.docs_dir / "semcap_heuristic_detector_report_v0_9.md"
    write_report(report, round3_preds, v08_preds)
    print(f"Wrote {round3_out} ({len(round3_preds)} rows)")
    print(f"Wrote {v08_out} ({len(v08_preds)} rows)")
    print(f"Round3 capability predictions: {count_by(round3_preds, 'capability_coverage_pred')}")
    print(f"v0.8 capability predictions: {count_by(v08_preds, 'capability_coverage_pred')}")
    print(f"Wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
