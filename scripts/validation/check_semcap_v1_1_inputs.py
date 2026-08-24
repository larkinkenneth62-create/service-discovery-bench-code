from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from semcap_v1_1_common import HUMAN_FIELDS, ensure_dir, now_text, read_csv_with_fields, write_json, write_md


DEFAULT_OUTPUT_DIR = Path("outputs/semcap_detector_v1_implementation_v1_1")


INPUTS = {
    "semcap80_human_final": Path("outputs/semcap_detector_v1_validation_v1_0/semcap_pilot_review_items_80_user_reviewed.csv"),
    "semcap80_analysis_report": Path("docs/phase1/semcap80_human_review_analysis_v1_0.md"),
    "semcap80_summary_json": Path("outputs/semcap_detector_v1_validation_v1_0/semcap80_human_review_analysis_summary_v1_0.json"),
    "semcap80_vs_detector": Path("outputs/semcap_detector_v1_validation_v1_0/semcap80_human_review_vs_detector_comparison_v1_0.csv"),
    "semcap_v1_revision_plan": Path("docs/phase1/semcap_detector_v1_revision_plan_from_review80_v0_9_1.md"),
    "round3_calibration": Path("outputs/semantic_capability_detector_pilot_v0_9/semcap_calibration_round3_100.csv"),
    "round3_predictions_v0_9": Path("outputs/semantic_capability_detector_pilot_v0_9/semcap_predictions_round3_heuristic.csv"),
    "v0_8_input_tasks": Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_input_tasks.csv"),
    "v0_8_detector_trace": Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_detector_trace.csv"),
    "v0_8_policy_trace": Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_policy_trace.csv"),
    "v4_2_policy_doc": Path("docs/phase1/manual_audit_rule_v4_2_candidate.md"),
}


REQUIRED_PREDICTION_FIELDS = [
    "pilot_semantic_alignment_pred",
    "pilot_capability_coverage_pred",
    "policy_decision_pilot",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check inputs for SemCap detector v1.1 implementation.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current working directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory for reports.")
    return parser.parse_args()


def inspect_csv(path: Path) -> dict:
    rows, fields = read_csv_with_fields(path)
    return {"path": str(path), "exists": True, "row_count": len(rows), "columns": fields}


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    missing = []
    schema = {"generated_time": now_text(), "inputs": {}, "missing_inputs": []}
    lines = [
        "# SemCap v1.1 Input Check Report",
        "",
        f"Generated time: {schema['generated_time']}",
        f"Project root: `{root}`",
        "",
        "Scope: input validation only. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Input Files",
        "",
        "| name | path | exists | rows | notes |",
        "|---|---|---|---|---|",
    ]

    for name, rel_path in INPUTS.items():
        path = root / rel_path
        if not path.exists():
            missing.append(name)
            schema["inputs"][name] = {"path": str(rel_path), "exists": False}
            lines.append(f"| {name} | `{rel_path}` | no |  | missing |")
            continue
        if path.suffix.lower() == ".csv":
            info = inspect_csv(path)
            rows = info["row_count"]
            fields = info["columns"]
            notes = []
            if name == "semcap80_human_final" and rows != 80:
                notes.append("expected 80 rows")
            if name == "round3_calibration" and rows != 100:
                notes.append("expected 100 rows")
            if name == "v0_8_input_tasks" and rows != 300:
                notes.append("expected 300 rows")
            if name == "semcap80_human_final":
                missing_human = [field for field in HUMAN_FIELDS if field not in fields]
                empty_counts = {}
                rows_data, _ = read_csv_with_fields(path)
                for field in HUMAN_FIELDS:
                    empty_counts[field] = sum(1 for row in rows_data if not (row.get(field) or "").strip())
                if missing_human:
                    notes.append("missing human fields: " + ", ".join(missing_human))
                    missing.append(name + "_human_fields")
                if any(empty_counts.values()):
                    notes.append("empty human fields: " + json.dumps(empty_counts, ensure_ascii=False))
                missing_pred = [field for field in REQUIRED_PREDICTION_FIELDS if field not in fields]
                if missing_pred:
                    notes.append("missing v0.9 prediction fields: " + ", ".join(missing_pred))
                    missing.append(name + "_prediction_fields")
                info["human_field_empty_counts"] = empty_counts
            schema["inputs"][name] = info
            lines.append(f"| {name} | `{rel_path}` | yes | {rows} | {'; '.join(notes) if notes else 'ok'} |")
        else:
            schema["inputs"][name] = {"path": str(rel_path), "exists": True}
            lines.append(f"| {name} | `{rel_path}` | yes |  | ok |")

    schema["missing_inputs"] = missing
    schema["all_required_inputs_available"] = not missing
    write_json(output_dir / "input_schema_summary.json", schema)
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- all_required_inputs_available: `{str(not missing).lower()}`",
            "- full cleaning run: `false`",
            "- split run: `false`",
            "- baseline run: `false`",
            "- model training run: `false`",
        ]
    )
    write_md(output_dir / "input_check_report.md", lines)

    if missing:
        missing_lines = [
            "# Missing Inputs For SemCap v1.1",
            "",
            f"Generated time: {now_text()}",
            "",
            "The v1.1 run stopped because required inputs are missing or invalid.",
            "",
        ]
        for item in missing:
            missing_lines.append(f"- {item}")
        write_md(output_dir / "MISSING_INPUTS.md", missing_lines)
        print("Missing or invalid inputs:", ", ".join(missing), file=sys.stderr)
        return 1

    print(f"Wrote {output_dir / 'input_check_report.md'}")
    print(f"Wrote {output_dir / 'input_schema_summary.json'}")
    print("All required SemCap v1.1 inputs are available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
