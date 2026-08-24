from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path("outputs/qwen_semcap_judge_v1_4d_step3")
REQUIRED_INPUTS = [
    Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_eval_summary_v1_4d.json"),
    Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_eval_trace_v1_4d.csv"),
    Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_failure_cases_v1_4d.csv"),
    Path("outputs/qwen_semcap_judge_v1_4d/predictions/qwen_semcap_predictions_calibration_180.csv"),
    Path("outputs/qwen_semcap_judge_v1_4d/predictions/qwen_semcap_raw_calibration_180.jsonl"),
    Path("docs/phase1/qwen_semcap_calibration_eval_report_v1_4d.md"),
    Path("docs/phase1/qwen_semcap_calibration_go_no_go_v1_4d.md"),
    Path("docs/phase1/qwen_semcap_calibration180_run_report_v1_4d.md"),
    Path("outputs/semcap_detector_v1_implementation_v1_1/combined_semcap_calibration_180.csv"),
    Path("scripts/validation/qwen_semcap_v1_4d_common.py"),
    Path("scripts/validation/run_qwen_semcap_judge_v1_4d.py"),
    Path("scripts/validation/evaluate_qwen_semcap_on_calibration_v1_4d.py"),
    Path("docs/phase1/semcap_v1_3_tightening_rules_candidate.md"),
    Path("docs/phase1/policy_v1_4c_tightening_plan.md"),
    Path("docs/phase1/semcap_v1_2_tightening_rules_candidate.md"),
    Path("docs/phase1/manual_audit_rule_v4_2_candidate.md"),
    Path("docs/phase1/final_qa_clean_candidate_failure_taxonomy_v1_5c.md"),
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S %z")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def path_exists(path: Path) -> bool:
    return path.exists()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Qwen v1.4d Step3 input readiness.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    missing = [str(path) for path in REQUIRED_INPUTS if not path.exists()]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if missing:
        lines = [
            "# Missing Qwen Step3 Inputs",
            "",
            f"Generated time: {now_text()}",
            "",
            "The following required inputs are missing:",
            "",
            *[f"- `{path}`" for path in missing],
        ]
        write_text(args.output_dir / "MISSING_INPUTS.md", "\n".join(lines) + "\n")
        print(f"missing inputs: {len(missing)}")
        return 2

    summary_path = Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_eval_summary_v1_4d.json")
    trace_path = Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_eval_trace_v1_4d.csv")
    failure_path = Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_failure_cases_v1_4d.csv")
    pred_path = Path("outputs/qwen_semcap_judge_v1_4d/predictions/qwen_semcap_predictions_calibration_180.csv")
    human_path = Path("outputs/semcap_detector_v1_implementation_v1_1/combined_semcap_calibration_180.csv")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    trace_rows = read_csv(trace_path)
    failure_rows = read_csv(failure_path)
    pred_rows = read_csv(pred_path)
    human_rows = read_csv(human_path)
    dangerous_rows = [row for row in trace_rows if row.get("dangerous_false_keep") == "yes"]
    pred_counts = Counter(row.get("QWEN_parse_status", "") or "<blank>" for row in pred_rows)

    forbidden_outputs = {
        "qwen_full2168_predictions": Path("outputs/qwen_semcap_judge_v1_4d/predictions/qwen_semcap_predictions_v1_4c_clean_candidates.csv"),
        "qwen_step3_full2168_predictions": Path("outputs/qwen_semcap_judge_v1_4d_step3/predictions/qwen_step3_predictions_full2168.csv"),
        "final_clean_dataset_v1_6": Path("outputs/final_clean_dataset_v1_6"),
        "split_dataset": Path("outputs/splits"),
        "baseline_results": Path("outputs/baselines"),
    }
    forbidden_status = {name: path.exists() for name, path in forbidden_outputs.items()}

    report_data = {
        "generated_time": now_text(),
        "required_input_count": len(REQUIRED_INPUTS),
        "missing_inputs": missing,
        "eval_summary_exists": summary_path.exists(),
        "eval_trace_exists": trace_path.exists(),
        "failure_cases_csv_exists": failure_path.exists(),
        "calibration_predictions_exist": pred_path.exists(),
        "calibration_human_labels_exist": human_path.exists(),
        "trace_rows": len(trace_rows),
        "failure_case_rows": len(failure_rows),
        "prediction_rows": len(pred_rows),
        "human_label_rows": len(human_rows),
        "dangerous_false_keep": summary.get("dangerous_false_keep"),
        "dangerous_false_keep_rows_in_trace": len(dangerous_rows),
        "parse_ok_rate": summary.get("parse_ok_rate"),
        "schema_failed_count": summary.get("schema_failed_count"),
        "prediction_parse_status_distribution": dict(pred_counts),
        "full2168_has_not_been_run": not forbidden_status["qwen_full2168_predictions"] and not forbidden_status["qwen_step3_full2168_predictions"],
        "final_clean_dataset_has_not_been_generated": not forbidden_status["final_clean_dataset_v1_6"],
        "forbidden_output_status": {name: {"path": str(path), "exists": exists} for name, (path, exists) in zip(forbidden_outputs.keys(), [(p, forbidden_status[n]) for n, p in forbidden_outputs.items()])},
        "input_check_passed": (
            len(trace_rows) == 180
            and len(pred_rows) == 180
            and len(human_rows) == 180
            and summary.get("dangerous_false_keep") == 5
            and len(dangerous_rows) == 5
            and float(summary.get("parse_ok_rate", 0)) == 1.0
            and int(summary.get("schema_failed_count", -1)) == 0
            and not forbidden_status["qwen_full2168_predictions"]
            and not forbidden_status["qwen_step3_full2168_predictions"]
            and not forbidden_status["final_clean_dataset_v1_6"]
        ),
    }
    write_json(args.output_dir / "input_schema_summary.json", report_data)

    lines = [
        "# Qwen Step3 Input Check Report v1.4d",
        "",
        f"Generated time: {report_data['generated_time']}",
        "",
        "## Required Inputs",
        "",
        *[f"- OK: `{path}`" for path in REQUIRED_INPUTS],
        "",
        "## Key Checks",
        "",
        f"- eval summary exists: {str(report_data['eval_summary_exists']).lower()}",
        f"- eval trace exists: {str(report_data['eval_trace_exists']).lower()}",
        f"- failure cases CSV exists: {str(report_data['failure_cases_csv_exists']).lower()}",
        f"- calibration predictions exist: {str(report_data['calibration_predictions_exist']).lower()}",
        f"- calibration human labels exist: {str(report_data['calibration_human_labels_exist']).lower()}",
        f"- dangerous_false_keep: {report_data['dangerous_false_keep']}",
        f"- dangerous_false_keep rows in trace: {report_data['dangerous_false_keep_rows_in_trace']}",
        f"- parse_ok_rate: {report_data['parse_ok_rate']}",
        f"- schema_failed_count: {report_data['schema_failed_count']}",
        f"- full2168 has not been run: {str(report_data['full2168_has_not_been_run']).lower()}",
        f"- final clean dataset has not been generated: {str(report_data['final_clean_dataset_has_not_been_generated']).lower()}",
        f"- input_check_passed: {str(report_data['input_check_passed']).lower()}",
        "",
        "## Boundary",
        "",
        "This check does not run full2168, full cleaning, final clean dataset generation, split, baseline, or training.",
    ]
    write_text(args.output_dir / "input_check_report.md", "\n".join(lines) + "\n")

    print(f"input_check_passed: {report_data['input_check_passed']}")
    print(f"dangerous_false_keep: {report_data['dangerous_false_keep']}")
    print(f"parse_ok_rate: {report_data['parse_ok_rate']}")
    print(f"schema_failed_count: {report_data['schema_failed_count']}")
    return 0 if report_data["input_check_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
