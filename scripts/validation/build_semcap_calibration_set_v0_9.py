"""Build v0.9 semcap calibration sets."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from semcap_detector_v0_9_utils import (
    AUDIT_EVIDENCE,
    DOCS_DIR,
    OUTPUT_DIR,
    ROUNDED_CALIBRATION_COLUMNS,
    ROUND3_REVIEWED,
    count_by,
    distribution_rows,
    ensure_dirs,
    markdown_table,
    now_str,
    read_csv,
    write_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build v0.9 Round3 semcap calibration set.")
    parser.add_argument("--round3", type=Path, default=ROUND3_REVIEWED)
    parser.add_argument("--evidence", type=Path, default=AUDIT_EVIDENCE)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def write_schema_doc(path: Path) -> None:
    lines = [
        "# Semantic / Capability Detector Schema v0.9",
        "",
        f"Generated time: {now_str()}",
        "",
        "Detector output is a pilot prediction, not human final. Only high-confidence `coverage_ok` + semantic `ok` may be considered for future clean-ready candidates, and even then only after validation.",
        "",
        "## semantic_alignment_detector",
        "",
        "Inputs: `query_text`, `gold_services_json`, `gold_apis_json`, `candidate_services_json`, `candidate_apis_json`.",
        "",
        "Outputs:",
        "",
        "- `semantic_alignment_check`: `ok`, `uncertain`, `mismatch`",
        "- `semantic_alignment_confidence`: `high`, `medium`, `low`",
        "- `semantic_alignment_reason`",
        "- `semantic_mismatch_type`: `domain_mismatch`, `task_intent_mismatch`, `wrong_entity_type`, `wrong_data_type`, `wrong_geography_or_carrier`, `partial_match_only`, `insufficient_api_description`, `not_enough_information`, `none`",
        "",
        "## capability_coverage_detector",
        "",
        "Inputs: `query_text`, gold service/API names, gold service/API descriptions, candidate API descriptions.",
        "",
        "Outputs:",
        "",
        "- `capability_coverage_check`: `coverage_ok`, `coverage_uncertain`, `coverage_mismatch`",
        "- `capability_coverage_confidence`: `high`, `medium`, `low`",
        "- `core_requirements`",
        "- `covered_requirements`",
        "- `missing_requirements`",
        "- `capability_mismatch_type`: `missing_required_capability`, `wrong_api_function`, `wrong_service_domain`, `wrong_geographic_scope`, `wrong_entity_scope`, `requires_external_capability`, `gold_only_partial`, `insufficient_description`, `none`",
        "- `capability_coverage_reason`",
        "",
        "## Policy Reminder",
        "",
        "Do not reward gold just because it is present in the candidate list. No leak does not imply coverage. Partial coverage should be `coverage_uncertain` or `coverage_mismatch`, not clean-ready.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_report(path: Path, round3_rows: List[Dict[str, str]], evidence_rows: List[Dict[str, str]]) -> None:
    lines = [
        "# SemCap Calibration Set Report v0.9",
        "",
        f"Generated time: {now_str()}",
        f"Round3 input: `{ROUND3_REVIEWED}`",
        f"Evidence input: `{AUDIT_EVIDENCE}`",
        f"Round3 calibration rows: {len(round3_rows)}",
        f"Evidence rows available: {len(evidence_rows)}",
        "",
        "Scope: calibration-set construction only. No full cleaning, final clean dataset, split, baseline, or model training was run.",
        "",
        "Round3 is the main calibration set because it has explicit `capability_coverage_check`. manual40/Round2 evidence is auxiliary when native capability labels are not available.",
    ]
    for key, title in [
        ("semantic_alignment_check", "Semantic Alignment Distribution"),
        ("capability_coverage_check", "Capability Coverage Distribution"),
        ("manual_final_decision", "Manual Final Decision Distribution"),
        ("risk_category", "Risk Category Distribution"),
        ("task_type", "Task Type Distribution"),
    ]:
        lines.extend(["", f"## {title}", ""])
        lines.extend(markdown_table(distribution_rows(round3_rows, key), ["value", "count"], max_rows=40))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.round3.exists() or not args.evidence.exists():
        print("ERROR: missing Round3 or evidence input.")
        return 1
    _, round3_rows = read_csv(args.round3)
    _, evidence_rows = read_csv(args.evidence)
    round3_out = args.output_dir / "semcap_calibration_round3_100.csv"
    evidence_out = args.output_dir / "semcap_calibration_evidence_available.csv"
    write_csv(round3_out, round3_rows, ROUNDED_CALIBRATION_COLUMNS)
    write_csv(evidence_out, evidence_rows)
    write_schema_doc(args.docs_dir / "semantic_capability_detector_schema_v0_9.md")
    write_report(args.docs_dir / "semcap_calibration_set_report_v0_9.md", round3_rows, evidence_rows)
    print(f"Wrote {round3_out} ({len(round3_rows)} rows)")
    print(f"Wrote {evidence_out} ({len(evidence_rows)} rows)")
    print(f"Round3 capability distribution: {count_by(round3_rows, 'capability_coverage_check')}")
    print(f"Wrote {args.docs_dir / 'semantic_capability_detector_schema_v0_9.md'}")
    print(f"Wrote {args.docs_dir / 'semcap_calibration_set_report_v0_9.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
