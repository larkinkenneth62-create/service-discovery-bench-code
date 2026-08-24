"""Run deterministic/heuristic detectors for v0.8 small sample."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from small_full_pipeline_v0_8_utils import (
    DETECTOR_COLUMNS,
    DOCS_DIR,
    OUTPUT_DIR,
    count_by,
    ensure_dirs,
    markdown_table,
    now_str,
    read_csv,
    run_detectors,
    status_distribution,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run v0.8 deterministic/heuristic detectors on small sample.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "small_full_pipeline_input_tasks.csv")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def write_report(path: Path, input_path: Path, rows: List[Dict[str, object]]) -> None:
    sections = [
        ("prediction_level", "Prediction Level"),
        ("candidate_space_status", "Candidate Space Status"),
        ("gold_in_candidate_services", "Gold In Candidate Services"),
        ("gold_in_candidate_apis", "Gold In Candidate APIs"),
        ("task_type_eligibility_status", "Task Type Eligibility Status"),
        ("api_leak_detector_status", "API Leak Detector Status"),
        ("api_leak_strength", "API Leak Strength"),
        ("service_leak_detector_status", "Service Leak Detector Status"),
        ("semantic_alignment_check", "Semantic Alignment Check"),
        ("capability_coverage_check", "Capability Coverage Check"),
        ("requires_semantic_review", "Requires Semantic Review"),
        ("requires_capability_review", "Requires Capability Review"),
    ]
    lines = [
        "# Small Full-Pipeline Detector Report v0.8",
        "",
        f"Generated time: {now_str()}",
        f"Input file: `{input_path}`",
        f"Sample count: {len(rows)}",
        "",
        "Scope: deterministic/heuristic detector trace only. No full cleaning, final clean dataset, split, baseline, or model training was run.",
        "",
        "Semantic alignment and capability coverage are intentionally marked `missing_or_unavailable` unless already supplied by a trusted reviewed field. This v0.8 sample does not treat dry-run status fields as trusted semantic/capability labels.",
    ]
    for key, title in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(markdown_table(status_distribution(rows, key), ["value", "count"], max_rows=40))
    lines.extend(
        [
            "",
            "## Detector Notes",
            "",
            "- `candidate_space_validator`, `gold_in_candidate_validator`, and `task_type_eligibility_validator` are deterministic with basic JSON/name normalization.",
            "- `api_leak_detector` and `service_leak_detector` are heuristic; weak/generic matches are warnings, not automatic remove decisions.",
            "- `semantic_alignment_detector` and `capability_coverage_detector` are not implemented as automatic detectors in v0.8; rows requiring these judgments must fail closed into review later.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.input.exists():
        print(f"ERROR: missing small sample input: {args.input}")
        return 1
    _, rows = read_csv(args.input)
    detector_rows = [run_detectors(row) for row in rows]
    out_csv = args.output_dir / "small_full_pipeline_detector_trace.csv"
    write_csv(out_csv, detector_rows, DETECTOR_COLUMNS)
    report = args.docs_dir / "small_full_pipeline_detector_report_v0_8.md"
    write_report(report, args.input, detector_rows)
    print(f"Detector rows: {len(detector_rows)}")
    print(f"prediction_level distribution: {count_by(detector_rows, 'prediction_level')}")
    print(f"api_leak_detector_status distribution: {count_by(detector_rows, 'api_leak_detector_status')}")
    print(f"service_leak_detector_status distribution: {count_by(detector_rows, 'service_leak_detector_status')}")
    print(f"Wrote {out_csv}")
    print(f"Wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
