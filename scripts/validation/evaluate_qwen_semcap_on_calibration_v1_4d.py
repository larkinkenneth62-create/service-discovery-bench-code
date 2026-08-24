from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from qwen_semcap_v1_4d_common import (
    CALIBRATION_180,
    DOC_DIR,
    EVAL_DIR,
    PREDICTION_DIR,
    ensure_dir,
    now_text,
    read_csv,
    table_lines,
    write_csv,
    write_json,
    write_md,
)


ALLOWED_SEMANTIC = {"ok", "uncertain", "mismatch"}
ALLOWED_COVERAGE = {"coverage_ok", "coverage_uncertain", "coverage_mismatch"}


def normalize_semantic(value: str) -> str:
    value = (value or "").strip()
    aliases = {
        "semantic_alignment_ok": "ok",
        "semantic_mismatch_uncertain": "mismatch",
        "semantic_alignment_uncertain": "uncertain",
    }
    return aliases.get(value, value or "unknown")


def normalize_coverage(value: str) -> str:
    value = (value or "").strip()
    aliases = {
        "ok": "coverage_ok",
        "uncertain": "coverage_uncertain",
        "mismatch": "coverage_mismatch",
    }
    return aliases.get(value, value or "unknown")


def pct(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def yes(value: bool) -> str:
    return "yes" if value else "no"


def json_array_nonempty(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text == "[]":
        return False
    try:
        parsed = json.loads(text)
        return isinstance(parsed, list) and len(parsed) > 0
    except Exception:
        return bool(text)


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def example_rows(rows: list[dict[str, Any]], predicate, limit: int = 10) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        if not predicate(row):
            continue
        out.append(
            {
                "record_id": row.get("record_id", ""),
                "task_id": row.get("task_id", ""),
                "query_text": row.get("query_text", "")[:240],
                "human_semantic_alignment_check": row.get("human_semantic_alignment_check", ""),
                "human_capability_coverage_check": row.get("human_capability_coverage_check", ""),
                "QWEN_semantic_alignment_check": row.get("QWEN_semantic_alignment_check", ""),
                "QWEN_capability_coverage_check": row.get("QWEN_capability_coverage_check", ""),
                "QWEN_capability_coverage_confidence": row.get("QWEN_capability_coverage_confidence", ""),
                "QWEN_reason": row.get("QWEN_reason", "")[:260],
            }
        )
        if len(out) >= limit:
            break
    return out


def add_example_section(lines: list[str], title: str, rows: list[dict[str, str]]) -> None:
    lines.extend(["", f"## {title}", ""])
    if not rows:
        lines.append("No examples.")
        return
    for row in rows:
        lines.extend(
            [
                f"- `{row['record_id']}` / `{row['task_id']}`",
                f"  - human semantic/capability: {row['human_semantic_alignment_check']} / {row['human_capability_coverage_check']}",
                f"  - Qwen semantic/capability: {row['QWEN_semantic_alignment_check']} / {row['QWEN_capability_coverage_check']} ({row['QWEN_capability_coverage_confidence']})",
                f"  - query: {row['query_text']}",
                f"  - Qwen reason: {row['QWEN_reason']}",
            ]
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Qwen SemCap predictions on human-labeled calibration180.")
    parser.add_argument("--calibration", type=Path, default=CALIBRATION_180)
    parser.add_argument("--predictions", type=Path, default=PREDICTION_DIR / "qwen_semcap_predictions_calibration_180.csv")
    parser.add_argument("--summary", type=Path, default=EVAL_DIR / "qwen_calibration_eval_summary_v1_4d.json")
    parser.add_argument("--trace", type=Path, default=EVAL_DIR / "qwen_calibration_eval_trace_v1_4d.csv")
    parser.add_argument("--report", type=Path, default=DOC_DIR / "qwen_semcap_calibration_eval_report_v1_4d.md")
    parser.add_argument("--run-report", type=Path, default=DOC_DIR / "qwen_semcap_calibration180_run_report_v1_4d.md")
    parser.add_argument("--go-no-go-report", type=Path, default=DOC_DIR / "qwen_semcap_calibration_go_no_go_v1_4d.md")
    args = parser.parse_args()

    if not args.calibration.exists():
        raise FileNotFoundError(f"Missing calibration file: {args.calibration}")
    if not args.predictions.exists():
        raise FileNotFoundError(
            f"Missing Qwen calibration predictions: {args.predictions}. "
            "Run the 180-item Qwen call first; do not evaluate sample20 as calibration."
        )

    human_rows = read_csv(args.calibration)
    pred_rows = read_csv(args.predictions)
    pred_by_task = {row.get("task_id", ""): row for row in pred_rows if row.get("task_id", "")}

    trace_rows: list[dict[str, Any]] = []
    invalid_enum_rows: list[str] = []
    for human in human_rows:
        pred = pred_by_task.get(human.get("task_id", ""), {})
        human_sem = normalize_semantic(human.get("semantic_alignment_check", ""))
        human_cov = normalize_coverage(human.get("capability_coverage_check", ""))
        pred_sem = normalize_semantic(pred.get("QWEN_semantic_alignment_check", ""))
        pred_cov = normalize_coverage(pred.get("QWEN_capability_coverage_check", ""))
        parse_status = pred.get("QWEN_parse_status", "missing_prediction") or "missing_prediction"
        pred_conf = (pred.get("QWEN_capability_coverage_confidence", "") or "").strip().lower()
        parse_ok = parse_status == "ok"
        invalid_enum = (
            bool(pred)
            and parse_ok
            and (pred_sem not in ALLOWED_SEMANTIC or pred_cov not in ALLOWED_COVERAGE)
        )
        if invalid_enum:
            invalid_enum_rows.append(human.get("task_id", ""))

        dangerous_false_keep = human_cov == "coverage_mismatch" and pred_cov == "coverage_ok" and pred_conf == "high"
        dangerous_false_keep_any_conf = human_cov == "coverage_mismatch" and pred_cov == "coverage_ok"
        coverage_mismatch_captured = human_cov == "coverage_mismatch" and pred_cov in {"coverage_mismatch", "coverage_uncertain"}
        high_conf_ok_den = pred_cov == "coverage_ok" and pred_conf == "high"
        high_conf_ok_correct = high_conf_ok_den and human_cov == "coverage_ok"
        coverage_ok_recalled = human_cov == "coverage_ok" and pred_cov == "coverage_ok"
        coverage_ok_over_conservative = human_cov == "coverage_ok" and pred_cov in {"coverage_uncertain", "coverage_mismatch"}
        semantic_mismatch_captured = human_sem == "mismatch" and pred_sem in {"mismatch", "uncertain"}
        semantic_uncertain_captured = human_sem == "uncertain" and pred_sem in {"mismatch", "uncertain"}

        trace_rows.append(
            {
                "calibration_source": human.get("calibration_source", ""),
                "record_id": human.get("record_id", ""),
                "task_id": human.get("task_id", ""),
                "task_type": human.get("task_type", ""),
                "review_bucket": human.get("review_bucket", ""),
                "risk_category": human.get("risk_category", ""),
                "risk_subtype": human.get("risk_subtype", ""),
                "query_text": human.get("query_text", ""),
                "manual_final_decision": human.get("manual_final_decision", ""),
                "human_semantic_alignment_check": human_sem,
                "human_capability_coverage_check": human_cov,
                "QWEN_parse_status": parse_status,
                "QWEN_semantic_alignment_check": pred_sem,
                "QWEN_semantic_alignment_confidence": pred.get("QWEN_semantic_alignment_confidence", ""),
                "QWEN_capability_coverage_check": pred_cov,
                "QWEN_capability_coverage_confidence": pred.get("QWEN_capability_coverage_confidence", ""),
                "QWEN_missing_requirements_json": pred.get("QWEN_missing_requirements_json", ""),
                "QWEN_extra_unrelated_gold_services_json": pred.get("QWEN_extra_unrelated_gold_services_json", ""),
                "QWEN_generic_search_overtrust": pred.get("QWEN_generic_search_overtrust", ""),
                "QWEN_domain_specific_gap": pred.get("QWEN_domain_specific_gap", ""),
                "QWEN_wrong_gold_set": pred.get("QWEN_wrong_gold_set", ""),
                "QWEN_decision_risk_level": pred.get("QWEN_decision_risk_level", ""),
                "QWEN_reason": pred.get("QWEN_reason", ""),
                "semantic_agree": yes(parse_ok and human_sem == pred_sem),
                "capability_agree": yes(parse_ok and human_cov == pred_cov),
                "dangerous_false_keep": yes(dangerous_false_keep),
                "dangerous_false_keep_any_conf": yes(dangerous_false_keep_any_conf),
                "coverage_mismatch_captured": yes(coverage_mismatch_captured),
                "high_confidence_coverage_ok_precision_like_denominator": yes(high_conf_ok_den),
                "high_confidence_coverage_ok_precision_like_correct": yes(high_conf_ok_correct),
                "coverage_ok_recalled": yes(coverage_ok_recalled),
                "coverage_ok_over_conservative": yes(coverage_ok_over_conservative),
                "semantic_mismatch_captured": yes(semantic_mismatch_captured),
                "semantic_uncertain_captured": yes(semantic_uncertain_captured),
                "invalid_enum": yes(invalid_enum),
            }
        )

    n = len(trace_rows)
    parse_counts = Counter(row["QWEN_parse_status"] for row in trace_rows)
    qwen_sem_counts = Counter(row["QWEN_semantic_alignment_check"] for row in trace_rows)
    qwen_cov_counts = Counter(row["QWEN_capability_coverage_check"] for row in trace_rows)
    human_sem_counts = Counter(row["human_semantic_alignment_check"] for row in trace_rows)
    human_cov_counts = Counter(row["human_capability_coverage_check"] for row in trace_rows)

    parse_ok_count = parse_counts.get("ok", 0)
    schema_failed_count = parse_counts.get("schema_failed", 0)
    semantic_agree = sum(1 for row in trace_rows if row["semantic_agree"] == "yes")
    capability_agree = sum(1 for row in trace_rows if row["capability_agree"] == "yes")
    dangerous_false_keep = sum(1 for row in trace_rows if row["dangerous_false_keep"] == "yes")
    dangerous_false_keep_any_conf = sum(1 for row in trace_rows if row["dangerous_false_keep_any_conf"] == "yes")
    mismatch_total = human_cov_counts.get("coverage_mismatch", 0)
    mismatch_captured = sum(1 for row in trace_rows if row["coverage_mismatch_captured"] == "yes")
    high_conf_ok_den = sum(1 for row in trace_rows if row["high_confidence_coverage_ok_precision_like_denominator"] == "yes")
    high_conf_ok_correct = sum(1 for row in trace_rows if row["high_confidence_coverage_ok_precision_like_correct"] == "yes")
    coverage_ok_total = human_cov_counts.get("coverage_ok", 0)
    coverage_ok_recalled = sum(1 for row in trace_rows if row["coverage_ok_recalled"] == "yes")
    coverage_ok_over_conservative = sum(1 for row in trace_rows if row["coverage_ok_over_conservative"] == "yes")
    semantic_mismatch_total = human_sem_counts.get("mismatch", 0)
    semantic_mismatch_captured = sum(1 for row in trace_rows if row["semantic_mismatch_captured"] == "yes")
    semantic_uncertain_total = human_sem_counts.get("uncertain", 0)
    semantic_uncertain_captured = sum(1 for row in trace_rows if row["semantic_uncertain_captured"] == "yes")

    examples = {
        "human_coverage_mismatch_but_qwen_coverage_ok": example_rows(
            trace_rows,
            lambda row: row["human_capability_coverage_check"] == "coverage_mismatch"
            and row["QWEN_capability_coverage_check"] == "coverage_ok",
        ),
        "human_semantic_mismatch_but_qwen_semantic_ok": example_rows(
            trace_rows,
            lambda row: row["human_semantic_alignment_check"] == "mismatch"
            and row["QWEN_semantic_alignment_check"] == "ok",
        ),
        "human_coverage_ok_but_qwen_coverage_mismatch": example_rows(
            trace_rows,
            lambda row: row["human_capability_coverage_check"] == "coverage_ok"
            and row["QWEN_capability_coverage_check"] == "coverage_mismatch",
        ),
        "qwen_parse_failed": example_rows(trace_rows, lambda row: row["QWEN_parse_status"] not in {"ok", "missing_prediction"}),
        "qwen_schema_failed": example_rows(trace_rows, lambda row: row["QWEN_parse_status"] == "schema_failed"),
    }

    semantic_mismatch_capture_rate = pct(semantic_mismatch_captured, semantic_mismatch_total)
    summary: dict[str, Any] = {
        "generated_time": now_text(),
        "input_calibration": str(args.calibration),
        "input_predictions": str(args.predictions),
        "row_count": n,
        "prediction_row_count": len(pred_rows),
        "parse_ok_count": parse_ok_count,
        "parse_ok_rate": pct(parse_ok_count, n),
        "schema_failed_count": schema_failed_count,
        "invalid_enum_count": len(invalid_enum_rows),
        "missing_prediction_count": parse_counts.get("missing_prediction", 0),
        "semantic_agreement": pct(semantic_agree, n),
        "capability_agreement": pct(capability_agree, n),
        "dangerous_false_keep": dangerous_false_keep,
        "dangerous_false_keep_any_conf": dangerous_false_keep_any_conf,
        "coverage_mismatch_total": mismatch_total,
        "coverage_mismatch_capture": pct(mismatch_captured, mismatch_total),
        "high_confidence_coverage_ok_precision_like_denominator": high_conf_ok_den,
        "high_confidence_coverage_ok_precision_like": pct(high_conf_ok_correct, high_conf_ok_den),
        "coverage_ok_total": coverage_ok_total,
        "coverage_ok_recall": pct(coverage_ok_recalled, coverage_ok_total),
        "coverage_ok_over_conservative_rate": pct(coverage_ok_over_conservative, coverage_ok_total),
        "semantic_all_ok_rate": pct(qwen_sem_counts.get("ok", 0), n),
        "semantic_mismatch_total": semantic_mismatch_total,
        "semantic_mismatch_capture": semantic_mismatch_capture_rate,
        "semantic_uncertain_total": semantic_uncertain_total,
        "semantic_uncertain_capture": pct(semantic_uncertain_captured, semantic_uncertain_total),
        "capability_all_ok_rate": pct(qwen_cov_counts.get("coverage_ok", 0), n),
        "coverage_ok_rate": pct(qwen_cov_counts.get("coverage_ok", 0), n),
        "coverage_mismatch_rate": pct(qwen_cov_counts.get("coverage_mismatch", 0), n),
        "coverage_uncertain_rate": pct(qwen_cov_counts.get("coverage_uncertain", 0), n),
        "parse_status_distribution": dict(parse_counts),
        "human_semantic_distribution": dict(human_sem_counts),
        "qwen_semantic_distribution": dict(qwen_sem_counts),
        "human_capability_distribution": dict(human_cov_counts),
        "qwen_capability_distribution": dict(qwen_cov_counts),
        "invalid_enum_task_ids": invalid_enum_rows,
        "error_examples": examples,
    }

    pass_parse = summary["parse_ok_rate"] >= 0.95 and schema_failed_count == 0 and len(invalid_enum_rows) == 0
    pass_coverage = (
        dangerous_false_keep == 0
        and summary["coverage_mismatch_capture"] >= 0.9
        and summary["high_confidence_coverage_ok_precision_like"] >= 0.85
    )
    pass_semantic = semantic_mismatch_total == 0 or semantic_mismatch_capture_rate >= 0.8
    can_accept_calibration = bool(pass_parse and pass_coverage and pass_semantic and dangerous_false_keep_any_conf == 0)
    go_no_go = {
        "go_no_go_decision": "GO_FOR_QWEN_FULL2168_NEXT" if can_accept_calibration else "NO_GO_INSPECT_CALIBRATION_FAILURES",
        "can_accept_qwen_sample20": True,
        "can_accept_qwen_calibration180": can_accept_calibration,
        "can_run_qwen_full2168_next": can_accept_calibration,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "recommended_next_step": (
            "set ALLOW_QWEN_FULL_RUN=true and run Qwen on v1.4c clean candidates 2168"
            if can_accept_calibration
            else "inspect calibration failure cases and revise Qwen prompt/schema/policy before full2168"
        ),
        "pass_parse_gate": pass_parse,
        "pass_coverage_gate": pass_coverage,
        "pass_semantic_gate": pass_semantic,
    }
    summary["go_no_go"] = go_no_go

    fieldnames = list(trace_rows[0].keys()) if trace_rows else []
    write_csv(args.trace, trace_rows, fieldnames)
    write_json(args.summary, summary)

    token_fields = ["prompt_token_count", "completion_token_count", "total_token_count"]
    token_usage: dict[str, int] = {}
    for field in token_fields:
        total = 0
        for row in pred_rows:
            try:
                total += int(float(row.get(field, "") or 0))
            except Exception:
                pass
        token_usage[field] = total

    run_lines = [
        "# Qwen SemCap Calibration180 Run Report v1.4d",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Input predictions: `{args.predictions}`",
        f"Input calibration: `{args.calibration}`",
        f"Sample count: {n}",
        "",
        "## Run Statistics",
        "",
        f"- rows: {len(pred_rows)}",
        f"- schema_failed_count: {schema_failed_count}",
        f"- invalid_enum_count: {len(invalid_enum_rows)}",
        f"- missing_requirements_nonempty_count: {sum(1 for row in pred_rows if json_array_nonempty(row.get('QWEN_missing_requirements_json', '')))}",
        f"- extra_gold_nonempty_count: {sum(1 for row in pred_rows if json_array_nonempty(row.get('QWEN_extra_unrelated_gold_services_json', '')))}",
        f"- generic_search_overtrust_true_count: {sum(1 for row in pred_rows if truthy(row.get('QWEN_generic_search_overtrust', '')))}",
        f"- domain_specific_gap_true_count: {sum(1 for row in pred_rows if truthy(row.get('QWEN_domain_specific_gap', '')))}",
        f"- wrong_gold_set_true_count: {sum(1 for row in pred_rows if truthy(row.get('QWEN_wrong_gold_set', '')))}",
        f"- prompt_token_count_total: {token_usage['prompt_token_count']}",
        f"- completion_token_count_total: {token_usage['completion_token_count']}",
        f"- total_token_count_total: {token_usage['total_token_count']}",
        "",
        "## Parse Status Distribution",
        "",
        *table_lines(parse_counts),
        "",
        "## Semantic Distribution",
        "",
        *table_lines(qwen_sem_counts),
        "",
        "## Capability Distribution",
        "",
        *table_lines(qwen_cov_counts),
        "",
        "This report summarizes the Qwen calibration180 run only. It does not approve full cleaning.",
    ]
    write_md(args.run_report, run_lines)

    lines = [
        "# Qwen SemCap Calibration Eval Report v1.4d",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Input calibration: `{args.calibration}`",
        f"Input predictions: `{args.predictions}`",
        f"Sample count: {n}",
        "",
        "## Basic Metrics",
        "",
        f"- row_count: {n}",
        f"- parse_ok_rate: {summary['parse_ok_rate']}",
        f"- schema_failed_count: {schema_failed_count}",
        f"- invalid_enum_count: {len(invalid_enum_rows)}",
        f"- semantic_agreement: {summary['semantic_agreement']}",
        f"- capability_agreement: {summary['capability_agreement']}",
        "",
        "## Safety Metrics",
        "",
        f"- dangerous_false_keep: {dangerous_false_keep}",
        f"- dangerous_false_keep_any_conf: {dangerous_false_keep_any_conf}",
        f"- coverage_mismatch_capture: {summary['coverage_mismatch_capture']}",
        f"- high_confidence_coverage_ok_precision_like: {summary['high_confidence_coverage_ok_precision_like']}",
        f"- coverage_ok_recall: {summary['coverage_ok_recall']}",
        f"- coverage_ok_over_conservative_rate: {summary['coverage_ok_over_conservative_rate']}",
        "",
        "## Bias Diagnostics",
        "",
        f"- semantic_all_ok_rate: {summary['semantic_all_ok_rate']}",
        f"- semantic_mismatch_capture: {summary['semantic_mismatch_capture']}",
        f"- semantic_uncertain_capture: {summary['semantic_uncertain_capture']}",
        f"- capability_all_ok_rate: {summary['capability_all_ok_rate']}",
        f"- coverage_ok_rate: {summary['coverage_ok_rate']}",
        f"- coverage_mismatch_rate: {summary['coverage_mismatch_rate']}",
        f"- coverage_uncertain_rate: {summary['coverage_uncertain_rate']}",
        "",
        "## Human Semantic Distribution",
        "",
        *table_lines(human_sem_counts),
        "",
        "## Qwen Semantic Distribution",
        "",
        *table_lines(qwen_sem_counts),
        "",
        "## Human Capability Distribution",
        "",
        *table_lines(human_cov_counts),
        "",
        "## Qwen Capability Distribution",
        "",
        *table_lines(qwen_cov_counts),
    ]
    add_example_section(lines, "Human Coverage Mismatch but Qwen Coverage Ok", examples["human_coverage_mismatch_but_qwen_coverage_ok"])
    add_example_section(lines, "Human Semantic Mismatch but Qwen Semantic Ok", examples["human_semantic_mismatch_but_qwen_semantic_ok"])
    add_example_section(lines, "Human Coverage Ok but Qwen Coverage Mismatch", examples["human_coverage_ok_but_qwen_coverage_mismatch"])
    add_example_section(lines, "Qwen Parse Failed", examples["qwen_parse_failed"])
    add_example_section(lines, "Qwen Schema Failed", examples["qwen_schema_failed"])
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "Qwen predictions are evaluation evidence only. They are not human final labels.",
            "No final clean dataset, split, baseline, or training is generated here.",
        ]
    )
    write_md(args.report, lines)

    go_lines = [
        "# Qwen SemCap Calibration Go / No-Go v1.4d",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Input calibration: `{args.calibration}`",
        f"Input predictions: `{args.predictions}`",
        f"Sample count: {n}",
        "",
        "## Go / No-Go Decision Qwen Calibration v1.4d",
        "",
        f"- can_accept_qwen_sample20: {str(go_no_go['can_accept_qwen_sample20']).lower()}",
        f"- can_accept_qwen_calibration180: {str(go_no_go['can_accept_qwen_calibration180']).lower()}",
        f"- can_run_qwen_full2168_next: {str(go_no_go['can_run_qwen_full2168_next']).lower()}",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        "",
        f"Go / No-Go Decision: {go_no_go['go_no_go_decision']}",
        "",
        f"recommended_next_step: {go_no_go['recommended_next_step']}",
        "",
        "## Gate Details",
        "",
        f"- pass_parse_gate: {str(pass_parse).lower()}",
        f"- pass_coverage_gate: {str(pass_coverage).lower()}",
        f"- pass_semantic_gate: {str(pass_semantic).lower()}",
        f"- dangerous_false_keep: {dangerous_false_keep}",
        f"- dangerous_false_keep_any_conf: {dangerous_false_keep_any_conf}",
        f"- coverage_mismatch_capture: {summary['coverage_mismatch_capture']}",
        f"- high_confidence_coverage_ok_precision_like: {summary['high_confidence_coverage_ok_precision_like']}",
        f"- semantic_mismatch_capture: {summary['semantic_mismatch_capture']}",
        "",
        "Do not generate final clean dataset, split, baseline, or training artifacts at this stage.",
    ]
    write_md(args.go_no_go_report, go_lines)

    print(f"calibration rows: {n}")
    print(f"parse_ok_rate: {summary['parse_ok_rate']}")
    print(f"schema_failed_count: {schema_failed_count}")
    print(f"dangerous_false_keep: {dangerous_false_keep}")
    print(f"dangerous_false_keep_any_conf: {dangerous_false_keep_any_conf}")
    print(f"coverage_mismatch_capture: {summary['coverage_mismatch_capture']}")
    print(f"high_confidence_coverage_ok_precision_like: {summary['high_confidence_coverage_ok_precision_like']}")
    print(f"semantic_mismatch_capture: {summary['semantic_mismatch_capture']}")
    print(f"Go / No-Go Decision Qwen Calibration v1.4d: {go_no_go['go_no_go_decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
