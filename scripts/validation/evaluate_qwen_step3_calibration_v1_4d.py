from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


DOC_DIR = Path("docs/phase1")
OUTPUT_DIR = Path("outputs/qwen_semcap_judge_v1_4d_step3")
OLD_DANGEROUS_IDS = {"R3-011", "SCV09-013", "SCV09-014", "SCV09-034", "SCV09-076"}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S %z")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def pct(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def normalize_semantic(value: str) -> str:
    value = (value or "").strip()
    return {
        "semantic_alignment_ok": "ok",
        "semantic_mismatch_uncertain": "mismatch",
        "semantic_alignment_uncertain": "uncertain",
    }.get(value, value or "unknown")


def normalize_coverage(value: str) -> str:
    value = (value or "").strip()
    return {
        "ok": "coverage_ok",
        "uncertain": "coverage_uncertain",
        "mismatch": "coverage_mismatch",
    }.get(value, value or "unknown")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Qwen Step3 guarded calibration predictions.")
    parser.add_argument(
        "--calibration",
        type=Path,
        default=Path("outputs/semcap_detector_v1_implementation_v1_1/combined_semcap_calibration_180.csv"),
    )
    parser.add_argument(
        "--guarded-predictions",
        type=Path,
        default=OUTPUT_DIR / "eval/qwen_step3_guarded_predictions_calibration_180.csv",
    )
    parser.add_argument(
        "--old-summary",
        type=Path,
        default=Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_eval_summary_v1_4d.json"),
    )
    parser.add_argument("--trace", type=Path, default=OUTPUT_DIR / "eval/qwen_step3_calibration_eval_trace.csv")
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "eval/qwen_step3_calibration_eval_summary.json")
    parser.add_argument("--report", type=Path, default=DOC_DIR / "qwen_step3_calibration_eval_report_v1_4d.md")
    parser.add_argument("--go-no-go", type=Path, default=DOC_DIR / "qwen_step3_calibration_go_no_go_v1_4d.md")
    args = parser.parse_args()

    if not args.calibration.exists():
        raise FileNotFoundError(f"Missing calibration human labels: {args.calibration}")
    if not args.guarded_predictions.exists():
        raise FileNotFoundError(f"Missing guarded Step3 predictions: {args.guarded_predictions}")
    if not args.old_summary.exists():
        raise FileNotFoundError(f"Missing old summary: {args.old_summary}")

    human_rows = read_csv(args.calibration)
    pred_rows = read_csv(args.guarded_predictions)
    old_summary = json.loads(args.old_summary.read_text(encoding="utf-8"))
    pred_by_task = {row.get("task_id", ""): row for row in pred_rows if row.get("task_id", "")}

    trace: list[dict[str, Any]] = []
    for human in human_rows:
        pred = pred_by_task.get(human.get("task_id", ""), {})
        human_sem = normalize_semantic(human.get("semantic_alignment_check", ""))
        human_cov = normalize_coverage(human.get("capability_coverage_check", ""))
        pred_sem = normalize_semantic(pred.get("QWEN_semantic_alignment_check", ""))
        guarded_cov = normalize_coverage(pred.get("QWEN_guarded_capability_coverage_check", pred.get("QWEN_capability_coverage_check", "")))
        guarded_conf = pred.get("QWEN_guarded_capability_coverage_confidence", pred.get("QWEN_capability_coverage_confidence", ""))
        parse_status = pred.get("QWEN_parse_status", "missing_prediction") or "missing_prediction"
        parse_ok = parse_status == "ok"
        dangerous = human_cov == "coverage_mismatch" and guarded_cov == "coverage_ok" and guarded_conf == "high"
        dangerous_any = human_cov == "coverage_mismatch" and guarded_cov == "coverage_ok"
        mismatch_capture = human_cov == "coverage_mismatch" and guarded_cov in {"coverage_mismatch", "coverage_uncertain"}
        high_conf_ok_den = guarded_cov == "coverage_ok" and guarded_conf == "high"
        high_conf_ok_correct = high_conf_ok_den and human_cov == "coverage_ok"
        old_5_still_high = human.get("record_id", "") in OLD_DANGEROUS_IDS and guarded_cov == "coverage_ok" and guarded_conf == "high"
        trace.append(
            {
                "calibration_source": human.get("calibration_source", ""),
                "record_id": human.get("record_id", ""),
                "task_id": human.get("task_id", ""),
                "task_type": human.get("task_type", ""),
                "manual_final_decision": human.get("manual_final_decision", ""),
                "human_semantic_alignment_check": human_sem,
                "human_capability_coverage_check": human_cov,
                "QWEN_parse_status": parse_status,
                "QWEN_semantic_alignment_check": pred_sem,
                "QWEN_capability_coverage_check": pred.get("QWEN_capability_coverage_check", ""),
                "QWEN_capability_coverage_confidence": pred.get("QWEN_capability_coverage_confidence", ""),
                "QWEN_guarded_capability_coverage_check": guarded_cov,
                "QWEN_guarded_capability_coverage_confidence": guarded_conf,
                "QWEN_guarded_blocking_reasons": pred.get("QWEN_guarded_blocking_reasons", ""),
                "QWEN_uses_non_gold_candidate_capability": pred.get("QWEN_uses_non_gold_candidate_capability", ""),
                "QWEN_gold_only_coverage_check": pred.get("QWEN_gold_only_coverage_check", ""),
                "QWEN_service_family_inference_risk": pred.get("QWEN_service_family_inference_risk", ""),
                "QWEN_explicit_tool_name_leak_bias_risk": pred.get("QWEN_explicit_tool_name_leak_bias_risk", ""),
                "QWEN_capability_inference_risk": pred.get("QWEN_capability_inference_risk", ""),
                "semantic_agree": parse_ok and human_sem == pred_sem,
                "capability_agree": parse_ok and human_cov == guarded_cov,
                "dangerous_false_keep": dangerous,
                "dangerous_false_keep_any_conf": dangerous_any,
                "coverage_mismatch_captured": mismatch_capture,
                "high_confidence_coverage_ok_precision_like_denominator": high_conf_ok_den,
                "high_confidence_coverage_ok_precision_like_correct": high_conf_ok_correct,
                "old_5_dangerous_false_keep_still_coverage_ok_high": old_5_still_high,
                "query_text": human.get("query_text", ""),
            }
        )

    n = len(trace)
    parse_counts = Counter(row["QWEN_parse_status"] for row in trace)
    human_cov_counts = Counter(row["human_capability_coverage_check"] for row in trace)
    guarded_cov_counts = Counter(row["QWEN_guarded_capability_coverage_check"] for row in trace)
    human_sem_counts = Counter(row["human_semantic_alignment_check"] for row in trace)
    pred_sem_counts = Counter(row["QWEN_semantic_alignment_check"] for row in trace)
    mismatch_total = human_cov_counts.get("coverage_mismatch", 0)
    high_den = sum(1 for row in trace if row["high_confidence_coverage_ok_precision_like_denominator"])
    high_ok = sum(1 for row in trace if row["high_confidence_coverage_ok_precision_like_correct"])

    guard_counts = {
        "uses_non_gold_candidate_capability_count": sum(1 for row in pred_rows if truthy(row.get("QWEN_uses_non_gold_candidate_capability", ""))),
        "gold_only_coverage_check_fail_count": sum(1 for row in pred_rows if row.get("QWEN_gold_only_coverage_check") == "fail"),
        "service_family_inference_risk_count": sum(1 for row in pred_rows if truthy(row.get("QWEN_service_family_inference_risk", ""))),
        "explicit_tool_name_leak_bias_risk_count": sum(1 for row in pred_rows if truthy(row.get("QWEN_explicit_tool_name_leak_bias_risk", ""))),
        "capability_inference_risk_high_count": sum(1 for row in pred_rows if row.get("QWEN_capability_inference_risk") == "high"),
    }
    summary: dict[str, Any] = {
        "generated_time": now_text(),
        "row_count": n,
        "prediction_row_count": len(pred_rows),
        "parse_ok_rate": pct(parse_counts.get("ok", 0), n),
        "schema_failed_count": parse_counts.get("schema_failed", 0),
        "missing_prediction_count": parse_counts.get("missing_prediction", 0),
        "semantic_agreement": pct(sum(1 for row in trace if row["semantic_agree"]), n),
        "capability_agreement": pct(sum(1 for row in trace if row["capability_agree"]), n),
        "dangerous_false_keep": sum(1 for row in trace if row["dangerous_false_keep"]),
        "dangerous_false_keep_any_conf": sum(1 for row in trace if row["dangerous_false_keep_any_conf"]),
        "coverage_mismatch_capture": pct(sum(1 for row in trace if row["coverage_mismatch_captured"]), mismatch_total),
        "high_confidence_coverage_ok_precision_like": pct(high_ok, high_den),
        "coverage_ok_recall": pct(
            sum(1 for row in trace if row["human_capability_coverage_check"] == "coverage_ok" and row["QWEN_guarded_capability_coverage_check"] == "coverage_ok"),
            human_cov_counts.get("coverage_ok", 0),
        ),
        "old_5_dangerous_false_keep_still_coverage_ok_high": sum(1 for row in trace if row["old_5_dangerous_false_keep_still_coverage_ok_high"]),
        "parse_status_distribution": dict(parse_counts),
        "human_capability_distribution": dict(human_cov_counts),
        "step3_guarded_capability_distribution": dict(guarded_cov_counts),
        "human_semantic_distribution": dict(human_sem_counts),
        "step3_semantic_distribution": dict(pred_sem_counts),
        "guard_counts": guard_counts,
        "old_vs_step3": {
            "old_dangerous_false_keep": old_summary.get("dangerous_false_keep"),
            "step3_dangerous_false_keep": None,
            "old_dangerous_false_keep_any_conf": old_summary.get("dangerous_false_keep_any_conf"),
            "step3_dangerous_false_keep_any_conf": None,
            "old_coverage_mismatch_capture": old_summary.get("coverage_mismatch_capture"),
            "step3_coverage_mismatch_capture": None,
            "old_high_confidence_coverage_ok_precision_like": old_summary.get("high_confidence_coverage_ok_precision_like"),
            "step3_high_confidence_coverage_ok_precision_like": None,
            "old_coverage_ok_recall": old_summary.get("coverage_ok_recall"),
            "step3_coverage_ok_recall": None,
            "old_capability_agreement": old_summary.get("capability_agreement"),
            "step3_capability_agreement": None,
            "old_semantic_agreement": old_summary.get("semantic_agreement"),
            "step3_semantic_agreement": None,
        },
    }
    for key in [
        "dangerous_false_keep",
        "dangerous_false_keep_any_conf",
        "coverage_mismatch_capture",
        "high_confidence_coverage_ok_precision_like",
        "coverage_ok_recall",
        "capability_agreement",
        "semantic_agreement",
    ]:
        summary["old_vs_step3"][f"step3_{key}"] = summary[key]

    pass_gate = (
        summary["dangerous_false_keep"] == 0
        and summary["dangerous_false_keep_any_conf"] == 0
        and summary["coverage_mismatch_capture"] >= 0.90
        and summary["high_confidence_coverage_ok_precision_like"] >= 0.85
        and summary["parse_ok_rate"] >= 0.95
        and summary["schema_failed_count"] == 0
        and summary["old_5_dangerous_false_keep_still_coverage_ok_high"] == 0
    )
    summary["go_no_go"] = {
        "go_no_go_decision": "GO_FOR_QWEN_STEP3_FULL2168_AFTER_USER_CONFIRMATION" if pass_gate else "NO_GO_INSPECT_STEP3_FAILURES",
        "can_accept_qwen_step3_sample20": True,
        "can_accept_qwen_step3_calibration180": pass_gate,
        "can_run_qwen_full2168_next": pass_gate,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "recommended_next_step": (
            "run Qwen Step3 full2168 only after user explicitly sets ALLOW_QWEN_FULL_RUN=true"
            if pass_gate
            else "do not continue Qwen full run; inspect Step3 failure cases or use Qwen as a negative filter only"
        ),
    }

    fieldnames = list(trace[0].keys()) if trace else []
    write_csv(args.trace, trace, fieldnames)
    write_json(args.summary, summary)

    report_lines = [
        "# Qwen Step3 Calibration Eval Report v1.4d",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Input calibration: `{args.calibration}`",
        f"Input guarded predictions: `{args.guarded_predictions}`",
        f"Sample count: {n}",
        "",
        "## Old vs Step3",
        "",
        "| metric | old v1.4d | step3 |",
        "|---|---:|---:|",
        *[
            f"| {metric} | {summary['old_vs_step3'].get('old_' + metric)} | {summary['old_vs_step3'].get('step3_' + metric)} |"
            for metric in [
                "dangerous_false_keep",
                "dangerous_false_keep_any_conf",
                "coverage_mismatch_capture",
                "high_confidence_coverage_ok_precision_like",
                "coverage_ok_recall",
                "capability_agreement",
                "semantic_agreement",
            ]
        ],
        "",
        "## Guard Counts",
        "",
        *[f"- {key}: {value}" for key, value in guard_counts.items()],
        "",
        "## Key Gate Metrics",
        "",
        f"- parse_ok_rate: {summary['parse_ok_rate']}",
        f"- schema_failed_count: {summary['schema_failed_count']}",
        f"- dangerous_false_keep: {summary['dangerous_false_keep']}",
        f"- dangerous_false_keep_any_conf: {summary['dangerous_false_keep_any_conf']}",
        f"- coverage_mismatch_capture: {summary['coverage_mismatch_capture']}",
        f"- high_confidence_coverage_ok_precision_like: {summary['high_confidence_coverage_ok_precision_like']}",
        f"- old_5_dangerous_false_keep_still_coverage_ok_high: {summary['old_5_dangerous_false_keep_still_coverage_ok_high']}",
        "",
        "No full2168, final clean dataset, split, baseline, or training is generated by this evaluation.",
    ]
    write_text(args.report, "\n".join(report_lines) + "\n")

    go = summary["go_no_go"]
    go_lines = [
        "# Qwen Step3 Calibration Go / No-Go v1.4d",
        "",
        f"Generated time: {summary['generated_time']}",
        "",
        "## Go / No-Go Decision Qwen Step3 Calibration v1.4d",
        "",
        f"- can_accept_qwen_step3_sample20: {str(go['can_accept_qwen_step3_sample20']).lower()}",
        f"- can_accept_qwen_step3_calibration180: {str(go['can_accept_qwen_step3_calibration180']).lower()}",
        f"- can_run_qwen_full2168_next: {str(go['can_run_qwen_full2168_next']).lower()}",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        "",
        f"Go / No-Go Decision: {go['go_no_go_decision']}",
        "",
        f"recommended_next_step: {go['recommended_next_step']}",
        "",
        "Qwen predictions are not human final labels.",
    ]
    write_text(args.go_no_go, "\n".join(go_lines) + "\n")

    print(f"step3 rows: {n}")
    print(f"step3 parse_ok_rate: {summary['parse_ok_rate']}")
    print(f"step3 dangerous_false_keep: {summary['dangerous_false_keep']}")
    print(f"step3 dangerous_false_keep_any_conf: {summary['dangerous_false_keep_any_conf']}")
    print(f"old_5_still_high_conf_coverage_ok: {summary['old_5_dangerous_false_keep_still_coverage_ok_high']}")
    print(f"Go / No-Go Decision Qwen Step3 Calibration: {go['go_no_go_decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
