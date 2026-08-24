"""Evaluate v0.9 semcap heuristic detector on Round3 reviewed labels."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from semcap_detector_v0_9_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    count_by,
    distribution_rows,
    ensure_dirs,
    markdown_table,
    now_str,
    pct,
    read_csv,
    write_csv,
    write_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate v0.9 semcap detector against Round3 human labels.")
    parser.add_argument("--predictions", type=Path, default=OUTPUT_DIR / "semcap_predictions_round3_heuristic.csv")
    parser.add_argument("--calibration", type=Path, default=OUTPUT_DIR / "semcap_calibration_round3_100.csv")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.predictions.exists() or not args.calibration.exists():
        print("ERROR: missing Round3 eval input.")
        return 1
    _, preds = read_csv(args.predictions)
    _, labels = read_csv(args.calibration)
    label_by_id = {row.get("round3_review_id") or row.get("task_id"): row for row in labels}
    trace: List[Dict[str, object]] = []
    for pred in preds:
        label = label_by_id.get(pred.get("record_id")) or label_by_id.get(pred.get("task_id")) or {}
        sem_h = label.get("semantic_alignment_check", "")
        cap_h = label.get("capability_coverage_check", "")
        sem_p = pred.get("semantic_alignment_pred", "")
        cap_p = pred.get("capability_coverage_pred", "")
        cap_conf = pred.get("capability_coverage_confidence", "")
        trace.append(
            {
                **pred,
                "human_semantic_alignment_check": sem_h,
                "human_capability_coverage_check": cap_h,
                "human_manual_final_decision": label.get("manual_final_decision", ""),
                "human_risk_category": label.get("risk_category", ""),
                "semantic_match": "yes" if sem_h == sem_p else "no",
                "capability_match": "yes" if cap_h == cap_p else "no",
                "dangerous_false_keep": "yes"
                if cap_h == "coverage_mismatch" and cap_p == "coverage_ok" and cap_conf == "high"
                else "no",
                "mismatch_captured_or_uncertain": "yes"
                if cap_h == "coverage_mismatch" and cap_p in {"coverage_mismatch", "coverage_uncertain"}
                else ("not_applicable" if cap_h != "coverage_mismatch" else "no"),
                "high_conf_coverage_ok": "yes" if cap_p == "coverage_ok" and cap_conf == "high" else "no",
            }
        )
    total = len(trace)
    sem_agree = sum(1 for row in trace if row["semantic_match"] == "yes")
    cap_agree = sum(1 for row in trace if row["capability_match"] == "yes")
    human_mismatch = [row for row in trace if row["human_capability_coverage_check"] == "coverage_mismatch"]
    mismatch_captured = [row for row in human_mismatch if row["mismatch_captured_or_uncertain"] == "yes"]
    dangerous = [row for row in trace if row["dangerous_false_keep"] == "yes"]
    high_conf_ok = [row for row in trace if row["high_conf_coverage_ok"] == "yes"]
    high_conf_ok_true = [row for row in high_conf_ok if row["human_capability_coverage_check"] == "coverage_ok"]
    summary = {
        "generated_time": now_str(),
        "row_count": total,
        "semantic_alignment_agreement": {"count": sem_agree, "rate": pct(sem_agree, total)},
        "capability_coverage_agreement": {"count": cap_agree, "rate": pct(cap_agree, total)},
        "human_coverage_mismatch_count": len(human_mismatch),
        "coverage_mismatch_recall_or_uncertain_capture": {
            "count": len(mismatch_captured),
            "rate": pct(len(mismatch_captured), len(human_mismatch)),
        },
        "dangerous_false_keep_count": len(dangerous),
        "high_confidence_coverage_ok_count": len(high_conf_ok),
        "high_confidence_coverage_ok_precision_like": {
            "count": len(high_conf_ok_true),
            "rate": pct(len(high_conf_ok_true), len(high_conf_ok)),
        },
        "human_capability_distribution": count_by(trace, "human_capability_coverage_check"),
        "pred_capability_distribution": count_by(trace, "capability_coverage_pred"),
        "human_semantic_distribution": count_by(trace, "human_semantic_alignment_check"),
        "pred_semantic_distribution": count_by(trace, "semantic_alignment_pred"),
        "minimum_safety_thresholds": {
            "dangerous_false_keep_equals_0": len(dangerous) == 0,
            "high_confidence_coverage_ok_precision_like_gte_85": (len(high_conf_ok) == 0 or len(high_conf_ok_true) / len(high_conf_ok) >= 0.85),
            "coverage_mismatch_recall_or_uncertain_capture_gte_90": (len(human_mismatch) == 0 or len(mismatch_captured) / len(human_mismatch) >= 0.90),
        },
    }
    summary["minimum_safety_passed"] = all(summary["minimum_safety_thresholds"].values())
    write_csv(args.output_dir / "semcap_round3_eval_trace.csv", trace)
    write_json(args.output_dir / "semcap_round3_eval_summary.json", summary)
    write_report(args.docs_dir / "semcap_detector_round3_eval_report_v0_9.md", trace, summary)
    print(f"Wrote {args.output_dir / 'semcap_round3_eval_summary.json'}")
    print(f"Wrote {args.output_dir / 'semcap_round3_eval_trace.csv'}")
    print(f"Dangerous false keep: {len(dangerous)}")
    print(f"Coverage mismatch capture: {summary['coverage_mismatch_recall_or_uncertain_capture']['rate']}")
    print(f"High-confidence coverage_ok precision-like: {summary['high_confidence_coverage_ok_precision_like']['rate']}")
    print(f"Wrote {args.docs_dir / 'semcap_detector_round3_eval_report_v0_9.md'}")
    return 0


def write_report(path: Path, trace: List[Dict[str, object]], summary: Dict[str, object]) -> None:
    dangerous = [row for row in trace if row["dangerous_false_keep"] == "yes"]
    errors = [row for row in trace if row["capability_match"] == "no"][:25]
    lines = [
        "# SemCap Detector Round3 Eval Report v0.9",
        "",
        f"Generated time: {now_str()}",
        f"Sample count: {summary['row_count']}",
        "",
        "Scope: evaluation against Round3 human labels only. Predictions are not human final labels. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Key Safety Metrics",
        "",
        f"- semantic alignment agreement: {summary['semantic_alignment_agreement']['count']}/{summary['row_count']} ({summary['semantic_alignment_agreement']['rate']})",
        f"- capability coverage agreement: {summary['capability_coverage_agreement']['count']}/{summary['row_count']} ({summary['capability_coverage_agreement']['rate']})",
        f"- dangerous false keep: {summary['dangerous_false_keep_count']}",
        f"- coverage mismatch recall-or-uncertain capture: {summary['coverage_mismatch_recall_or_uncertain_capture']['rate']}",
        f"- high-confidence coverage_ok precision-like: {summary['high_confidence_coverage_ok_precision_like']['rate']}",
        f"- minimum safety passed: {summary['minimum_safety_passed']}",
        "",
        "## Human vs Prediction Distributions",
        "",
        "### Human capability",
        "",
    ]
    lines.extend(markdown_table(distribution_rows(trace, "human_capability_coverage_check"), ["value", "count"], max_rows=20))
    lines.extend(["", "### Predicted capability", ""])
    lines.extend(markdown_table(distribution_rows(trace, "capability_coverage_pred"), ["value", "count"], max_rows=20))
    lines.extend(["", "## Dangerous False Keep Samples", ""])
    lines.extend(markdown_table(dangerous, ["record_id", "task_id", "human_capability_coverage_check", "capability_coverage_pred", "capability_coverage_confidence", "query_text"], max_rows=20))
    lines.extend(["", "## Capability Error Examples", ""])
    lines.extend(markdown_table(errors, ["record_id", "task_id", "human_capability_coverage_check", "capability_coverage_pred", "capability_coverage_reason", "query_text"], max_rows=25))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
