from __future__ import annotations

import argparse
from pathlib import Path

from deepseek_semcap_v1_4d_common import (
    CALIBRATION_180,
    DOC_DIR,
    EVAL_DIR,
    PREDICTION_DIR,
    ensure_dir,
    read_csv,
    table_lines,
    write_csv,
    write_json,
    write_md,
)


def normalize_semantic(value: str) -> str:
    value = (value or "").strip()
    if value in {"ok", "uncertain", "mismatch"}:
        return value
    if value == "semantic_alignment_ok":
        return "ok"
    if value == "semantic_mismatch_uncertain":
        return "mismatch"
    return value or "unknown"


def normalize_coverage(value: str) -> str:
    value = (value or "").strip()
    if value in {"coverage_ok", "coverage_uncertain", "coverage_mismatch"}:
        return value
    return value or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate DeepSeek SemCap predictions on calibration 180.")
    parser.add_argument("--calibration", type=Path, default=CALIBRATION_180)
    parser.add_argument("--predictions", type=Path, default=PREDICTION_DIR / "deepseek_semcap_predictions_calibration_180.csv")
    parser.add_argument("--summary", type=Path, default=EVAL_DIR / "deepseek_calibration_eval_summary_v1_4d.json")
    parser.add_argument("--trace", type=Path, default=EVAL_DIR / "deepseek_calibration_eval_trace_v1_4d.csv")
    args = parser.parse_args()
    if not args.calibration.exists():
        raise FileNotFoundError(f"Missing calibration file: {args.calibration}")
    if not args.predictions.exists():
        raise FileNotFoundError(f"Missing DeepSeek calibration predictions: {args.predictions}")
    ensure_dir(args.summary.parent)
    human_rows = read_csv(args.calibration)
    pred_rows = read_csv(args.predictions)
    by_task = {row.get("task_id", ""): row for row in pred_rows}
    trace_rows = []
    for human in human_rows:
        pred = by_task.get(human.get("task_id", ""), {})
        human_sem = normalize_semantic(human.get("semantic_alignment_check", ""))
        human_cov = normalize_coverage(human.get("capability_coverage_check", ""))
        pred_sem = normalize_semantic(pred.get("deepseek_semantic_alignment_check", ""))
        pred_cov = normalize_coverage(pred.get("deepseek_capability_coverage_check", ""))
        parse_ok = pred.get("deepseek_parse_status") == "ok"
        dangerous_false_keep = human_cov == "coverage_mismatch" and pred_cov == "coverage_ok" and pred.get("deepseek_capability_coverage_confidence") == "high"
        mismatch_captured = human_cov == "coverage_mismatch" and pred_cov in {"coverage_mismatch", "coverage_uncertain"}
        precision_like_den = pred_cov == "coverage_ok" and pred.get("deepseek_capability_coverage_confidence") == "high"
        precision_like_ok = precision_like_den and human_cov == "coverage_ok"
        trace_rows.append(
            {
                "record_id": human.get("record_id", ""),
                "task_id": human.get("task_id", ""),
                "manual_final_decision": human.get("manual_final_decision", ""),
                "human_semantic_alignment_check": human_sem,
                "human_capability_coverage_check": human_cov,
                "deepseek_parse_status": pred.get("deepseek_parse_status", "missing_prediction"),
                "deepseek_semantic_alignment_check": pred_sem,
                "deepseek_capability_coverage_check": pred_cov,
                "deepseek_capability_coverage_confidence": pred.get("deepseek_capability_coverage_confidence", ""),
                "semantic_agree": str(parse_ok and human_sem == pred_sem),
                "capability_agree": str(parse_ok and human_cov == pred_cov),
                "dangerous_false_keep": str(dangerous_false_keep),
                "coverage_mismatch_captured": str(mismatch_captured),
                "high_conf_coverage_ok_precision_like_denominator": str(precision_like_den),
                "high_conf_coverage_ok_precision_like_ok": str(precision_like_ok),
            }
        )
    n = len(trace_rows)
    parse_ok_count = sum(1 for row in trace_rows if row["deepseek_parse_status"] == "ok")
    semantic_agree = sum(1 for row in trace_rows if row["semantic_agree"] == "True")
    capability_agree = sum(1 for row in trace_rows if row["capability_agree"] == "True")
    dangerous_false_keep = sum(1 for row in trace_rows if row["dangerous_false_keep"] == "True")
    mismatch_total = sum(1 for row in trace_rows if row["human_capability_coverage_check"] == "coverage_mismatch")
    mismatch_captured = sum(1 for row in trace_rows if row["coverage_mismatch_captured"] == "True")
    precision_den = sum(1 for row in trace_rows if row["high_conf_coverage_ok_precision_like_denominator"] == "True")
    precision_ok = sum(1 for row in trace_rows if row["high_conf_coverage_ok_precision_like_ok"] == "True")
    coverage_ok_total = sum(1 for row in trace_rows if row["human_capability_coverage_check"] == "coverage_ok")
    coverage_ok_recall = sum(1 for row in trace_rows if row["human_capability_coverage_check"] == "coverage_ok" and row["deepseek_capability_coverage_check"] == "coverage_ok")

    summary = {
        "row_count": n,
        "parse_ok_count": parse_ok_count,
        "parse_ok_rate": round(parse_ok_count / n, 4) if n else 0,
        "semantic_agreement": round(semantic_agree / n, 4) if n else 0,
        "capability_agreement": round(capability_agree / n, 4) if n else 0,
        "dangerous_false_keep": dangerous_false_keep,
        "coverage_mismatch_capture": round(mismatch_captured / mismatch_total, 4) if mismatch_total else 0,
        "high_confidence_coverage_ok_precision_like": round(precision_ok / precision_den, 4) if precision_den else 0,
        "coverage_ok_recall": round(coverage_ok_recall / coverage_ok_total, 4) if coverage_ok_total else 0,
        "parse_failed_count": sum(1 for row in trace_rows if row["deepseek_parse_status"] not in {"ok", "missing_prediction"}),
        "missing_prediction_count": sum(1 for row in trace_rows if row["deepseek_parse_status"] == "missing_prediction"),
    }
    summary["calibration_passed"] = (
        summary["dangerous_false_keep"] == 0
        and summary["coverage_mismatch_capture"] >= 0.9
        and summary["high_confidence_coverage_ok_precision_like"] >= 0.85
        and summary["parse_ok_rate"] >= 0.95
    )
    write_csv(args.trace, trace_rows, list(trace_rows[0].keys()) if trace_rows else [])
    write_json(args.summary, summary)
    write_md(
        DOC_DIR / "deepseek_semcap_calibration_eval_report_v1_4d.md",
        [
            "# DeepSeek SemCap Calibration Eval Report v1.4d",
            "",
            f"Input calibration: `{args.calibration}`",
            f"Input predictions: `{args.predictions}`",
            f"Sample count: {n}",
            "",
            "## Summary",
            "",
            *[f"- {key}: {value}" for key, value in summary.items()],
            "",
            "Calibration must pass before full 2,168 execution is considered.",
            "No final clean data, split, baseline, or training is generated here.",
        ],
    )
    print(f"dangerous_false_keep: {dangerous_false_keep}")
    print(f"coverage_mismatch_capture: {summary['coverage_mismatch_capture']}")
    print(f"calibration_passed: {summary['calibration_passed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
