from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from qwen_semcap_v1_4d_common import (
    DOC_DIR,
    OUTPUT_DIR,
    PREDICTION_DIR,
    V14C_TASK_TRACE,
    read_csv,
    table_lines,
    write_csv,
    write_json,
    write_md,
)


def json_nonempty(value: str) -> bool:
    try:
        data = json.loads(value or "[]")
        return isinstance(data, list) and len(data) > 0
    except Exception:
        return bool(value)


def is_true(value: str) -> bool:
    return str(value or "").lower() == "true"


def route(row: dict[str, str], pred: dict[str, str]) -> tuple[str, str, list[str]]:
    if row.get("dryrun_decision_v1_4c") != "dryrun_clean_candidate":
        return row.get("dryrun_decision_v1_4c", "dryrun_not_clean"), row.get("dryrun_bucket_v1_4c", ""), ["not_v1_4c_clean_candidate"]
    hard_reasons = str(row.get("blocking_reasons_v1_4c", "")).lower()
    for marker in ["api_leak", "gold_missing", "choice_space", "service_leak", "task_type"]:
        if marker in hard_reasons:
            return "QWEN_removed_hard_policy_block", "QWEN_hard_policy_block", [f"hard_policy:{marker}"]
    if not pred:
        return "QWEN_uncertain_missing_prediction", "QWEN_missing_prediction", ["missing_QWEN_prediction"]
    if pred.get("QWEN_parse_status") != "ok":
        return "QWEN_uncertain_parse_failed", "QWEN_parse_failed", [f"parse_status:{pred.get('QWEN_parse_status')}"]
    if pred.get("QWEN_semantic_alignment_check") == "mismatch":
        return "QWEN_removed_semantic_mismatch", "QWEN_removed_semantic_mismatch", ["semantic_mismatch"]
    if pred.get("QWEN_semantic_alignment_check") == "uncertain":
        return "QWEN_uncertain_semcap", "QWEN_uncertain_semantic_alignment", ["semantic_uncertain"]
    if pred.get("QWEN_capability_coverage_check") == "coverage_mismatch":
        return "QWEN_removed_capability_mismatch", "QWEN_removed_capability_mismatch", ["coverage_mismatch"]
    if pred.get("QWEN_capability_coverage_check") == "coverage_uncertain":
        return "QWEN_uncertain_semcap", "QWEN_uncertain_capability_coverage", ["coverage_uncertain"]
    if json_nonempty(pred.get("QWEN_missing_requirements_json", "")):
        return "QWEN_removed_or_uncertain_missing_requirement", "QWEN_missing_requirement", ["missing_requirements_nonempty"]
    if json_nonempty(pred.get("QWEN_extra_unrelated_gold_services_json", "")):
        return "QWEN_removed_wrong_gold_set", "QWEN_extra_unrelated_gold", ["extra_unrelated_gold_services"]
    if is_true(pred.get("QWEN_generic_search_overtrust", "")):
        return "QWEN_uncertain_or_removed_generic_search_overtrust", "QWEN_generic_search_overtrust", ["generic_search_overtrust"]
    if is_true(pred.get("QWEN_domain_specific_gap", "")):
        return "QWEN_removed_capability_mismatch", "QWEN_domain_specific_gap", ["domain_specific_gap"]
    if is_true(pred.get("QWEN_wrong_gold_set", "")):
        return "QWEN_removed_wrong_gold_set", "QWEN_wrong_gold_set", ["wrong_gold_set"]
    if (
        pred.get("QWEN_semantic_alignment_check") == "ok"
        and pred.get("QWEN_capability_coverage_check") == "coverage_ok"
        and pred.get("QWEN_decision_risk_level") in {"low", "medium"}
    ):
        return "qwen_assisted_clean_candidate", "qwen_assisted_clean_candidate", ["all_qwen_semcap_clean_conditions_passed"]
    return "QWEN_uncertain_semcap", "QWEN_uncertain_residual", ["residual_uncertain"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply QWEN SemCap policy re-gating to v1.4c trace.")
    parser.add_argument("--task-trace", type=Path, default=V14C_TASK_TRACE)
    parser.add_argument("--predictions", type=Path, default=PREDICTION_DIR / "qwen_semcap_predictions_v1_4c_clean_candidates.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "qwen_assisted_clean_task_trace_v1_4d.csv")
    args = parser.parse_args()
    if not args.task_trace.exists():
        raise FileNotFoundError(f"Missing v1.4c task trace: {args.task_trace}")
    if not args.predictions.exists():
        raise FileNotFoundError(f"Missing QWEN full clean-candidate predictions: {args.predictions}")
    rows = read_csv(args.task_trace)
    preds = {row.get("task_id", ""): row for row in read_csv(args.predictions)}
    out_rows = []
    for row in rows:
        pred = preds.get(row.get("task_id", ""), {})
        decision, bucket, reasons = route(row, pred)
        out = dict(row)
        for key, value in pred.items():
            if key.startswith("QWEN_"):
                out[key] = value
        out["qwen_assisted_decision_v1_4d"] = decision
        out["qwen_assisted_bucket_v1_4d"] = bucket
        out["qwen_assisted_reasons_v1_4d"] = json.dumps(reasons, ensure_ascii=False)
        out["is_qwen_assisted_clean_candidate_v1_4d"] = str(decision == "qwen_assisted_clean_candidate")
        out_rows.append(out)
    fieldnames = list(out_rows[0].keys()) if out_rows else []
    write_csv(args.output, out_rows, fieldnames)
    decision_counts = Counter(row["qwen_assisted_decision_v1_4d"] for row in out_rows)
    clean_count = decision_counts.get("qwen_assisted_clean_candidate", 0)
    summary = {
        "input_task_trace": str(args.task_trace),
        "input_predictions": str(args.predictions),
        "output": str(args.output),
        "row_count": len(out_rows),
        "decision_distribution": dict(decision_counts),
        "qwen_assisted_clean_candidate_count": clean_count,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
    }
    write_json(OUTPUT_DIR / "qwen_assisted_clean_trace_summary_v1_4d.json", summary)
    write_md(
        DOC_DIR / "qwen_assisted_clean_trace_report_v1_4d.md",
        [
            "# QWEN-Assisted Clean Trace Report v1.4d",
            "",
            f"Input task trace: `{args.task_trace}`",
            f"Input predictions: `{args.predictions}`",
            f"Output trace: `{args.output}`",
            f"Rows: {len(out_rows)}",
            "",
            "## Decision Distribution",
            "",
            *table_lines(decision_counts),
            "",
            "QWEN does not override hard deterministic policy gates.",
            "No final clean data, split, baseline, or training is generated here.",
        ],
    )
    print(f"qwen_assisted_clean_candidate_count: {clean_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


