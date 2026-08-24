from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


DOC_DIR = Path("docs/phase1")
FINALQA_DIR = Path("outputs/qwen_semcap_judge_v1_4d_step3/finalqa100")
ARCHIVE_DIR = Path("outputs/run_archives/2026-07-01_qwen_step3_finalqa100_reliability_audit")


SEMANTIC_ALLOWED = {"ok", "uncertain", "mismatch", ""}
COVERAGE_ALLOWED = {"coverage_ok", "coverage_uncertain", "coverage_mismatch", ""}
CONF_ALLOWED = {"high", "medium", "low", ""}
RISK_ALLOWED = {"high", "medium", "low", ""}
GOLD_ONLY_ALLOWED = {"pass", "fail", "uncertain", ""}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def qa_item_id_from_custom_id(custom_id: str) -> str:
    tail = str(custom_id or "").split("::")[-1]
    if tail.endswith("_seed42"):
        tail = tail.rsplit("_seed42", 1)[0]
    if "::perturbed_seed" in tail:
        tail = tail.split("::perturbed_seed", 1)[0]
    return tail


def parse_json_list(value: str) -> tuple[list[Any], bool]:
    text = str(value or "").strip()
    if not text:
        return [], False
    try:
        parsed = json.loads(text)
    except Exception:
        return [], True
    return parsed if isinstance(parsed, list) else [], not isinstance(parsed, list)


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def high_conf(value: str) -> bool:
    text = str(value or "").strip().lower()
    if text == "high":
        return True
    try:
        return float(text) >= 0.80
    except Exception:
        return False


def safe_rate(num: int, den: int, empty_value: float = 0.0) -> float:
    if den == 0:
        return empty_value
    return round(num / den, 4)


def dist(rows: list[dict[str, str]], field: str) -> Counter[str]:
    return Counter(row.get(field, "") or "<blank>" for row in rows)


def table(counter: Counter[str] | dict[str, int]) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    if not counter:
        lines.append("| <empty> | 0 |")
        return lines
    for key, value in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {value} |")
    return lines


def crosstab(rows: list[dict[str, str]], left: str, right: str) -> dict[str, dict[str, int]]:
    out: dict[str, Counter[str]] = {}
    for row in rows:
        key = row.get(left, "") or "<blank>"
        if key not in out:
            out[key] = Counter()
        out[key][row.get(right, "") or "<blank>"] += 1
    return {key: dict(counter) for key, counter in sorted(out.items())}


def invalid_enum_count(rows: list[dict[str, str]]) -> tuple[int, list[dict[str, str]]]:
    checks = {
        "QWEN_semantic_alignment_check": SEMANTIC_ALLOWED,
        "QWEN_semantic_alignment_confidence": CONF_ALLOWED,
        "QWEN_capability_coverage_check": COVERAGE_ALLOWED,
        "QWEN_capability_coverage_confidence": CONF_ALLOWED,
        "QWEN_decision_risk_level": RISK_ALLOWED,
        "QWEN_gold_only_coverage_check": GOLD_ONLY_ALLOWED,
        "QWEN_capability_inference_risk": RISK_ALLOWED,
        "QWEN_guarded_capability_coverage_check": COVERAGE_ALLOWED,
        "QWEN_guarded_capability_coverage_confidence": CONF_ALLOWED,
    }
    invalid: list[dict[str, str]] = []
    for row in rows:
        for field, allowed in checks.items():
            value = row.get(field, "")
            if value not in allowed:
                invalid.append({"custom_id": row.get("custom_id", ""), "field": field, "value": value})
    return len(invalid), invalid


def top_guard_reasons(rows: list[dict[str, str]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        for reason in str(row.get("QWEN_guarded_blocking_reasons", "")).split(";"):
            if reason:
                counter[reason] += 1
    return counter


def build_trace(
    reviewed_rows: list[dict[str, str]],
    raw_rows: list[dict[str, str]],
    guarded_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    human_by_id = {row.get("qa_item_id", ""): row for row in reviewed_rows}
    raw_by_custom = {row.get("custom_id", ""): row for row in raw_rows}
    trace: list[dict[str, Any]] = []
    for guarded in guarded_rows:
        custom_id = guarded.get("custom_id", "")
        qa_item_id = qa_item_id_from_custom_id(custom_id)
        human = human_by_id.get(qa_item_id, {})
        raw = raw_by_custom.get(custom_id, {})
        guarded_keep = guarded.get("QWEN_guarded_capability_coverage_check") == "coverage_ok"
        high_keep = guarded_keep and high_conf(guarded.get("QWEN_guarded_capability_coverage_confidence", ""))
        trace.append(
            {
                "qa_item_id": qa_item_id,
                "custom_id": custom_id,
                "task_id": guarded.get("task_id", ""),
                "task_type": guarded.get("task_type", ""),
                "human_final": human.get("qa_final_decision", ""),
                "human_severity": human.get("qa_severity", ""),
                "human_error_type": human.get("qa_error_type", ""),
                "human_semantic": human.get("qa_semantic_alignment_check", ""),
                "human_capability": human.get("qa_capability_coverage_check", ""),
                "QWEN_parse_status": guarded.get("QWEN_parse_status", ""),
                "QWEN_raw_capability_coverage_check": raw.get("QWEN_capability_coverage_check", guarded.get("QWEN_capability_coverage_check", "")),
                "QWEN_raw_capability_coverage_confidence": raw.get("QWEN_capability_coverage_confidence", guarded.get("QWEN_capability_coverage_confidence", "")),
                "QWEN_guarded_capability_coverage_check": guarded.get("QWEN_guarded_capability_coverage_check", ""),
                "QWEN_guarded_capability_coverage_confidence": guarded.get("QWEN_guarded_capability_coverage_confidence", ""),
                "qwen_guarded_keep": str(guarded_keep).lower(),
                "qwen_high_conf_keep": str(high_keep).lower(),
                "QWEN_gold_only_coverage_check": guarded.get("QWEN_gold_only_coverage_check", ""),
                "QWEN_capability_inference_risk": guarded.get("QWEN_capability_inference_risk", ""),
                "QWEN_uses_non_gold_candidate_capability": guarded.get("QWEN_uses_non_gold_candidate_capability", ""),
                "QWEN_service_family_inference_risk": guarded.get("QWEN_service_family_inference_risk", ""),
                "QWEN_explicit_tool_name_leak_bias_risk": guarded.get("QWEN_explicit_tool_name_leak_bias_risk", ""),
                "QWEN_guarded_blocking_reasons": guarded.get("QWEN_guarded_blocking_reasons", ""),
                "QWEN_reason": guarded.get("QWEN_reason", ""),
            }
        )
    return trace


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Qwen Step3 finalQA100 against human final QA labels.")
    parser.add_argument("--reviewed-csv", type=Path, default=Path("outputs/final_qa_v1_5e/final_qa_review_items_v1_5e_gpt_manual_reviewed.csv"))
    parser.add_argument("--predictions-csv", type=Path, default=FINALQA_DIR / "predictions/qwen_step3_predictions_finalqa100.csv")
    parser.add_argument("--raw-output-jsonl", type=Path, default=FINALQA_DIR / "predictions/qwen_step3_raw_finalqa100.jsonl")
    parser.add_argument("--guarded-csv", type=Path, default=FINALQA_DIR / "eval/qwen_step3_guarded_predictions_finalqa100.csv")
    parser.add_argument("--trace-output", type=Path, default=FINALQA_DIR / "eval/qwen_step3_finalqa100_eval_trace.csv")
    parser.add_argument("--summary-output", type=Path, default=FINALQA_DIR / "eval/qwen_step3_finalqa100_eval_summary.json")
    args = parser.parse_args()

    reviewed_rows = read_csv(args.reviewed_csv)
    raw_pred_rows = read_csv(args.predictions_csv)
    guarded_rows = read_csv(args.guarded_csv)
    if len(reviewed_rows) != 100:
        raise ValueError(f"Expected 100 human reviewed rows, got {len(reviewed_rows)}")
    if len(guarded_rows) != 100:
        raise ValueError(f"Expected 100 guarded prediction rows, got {len(guarded_rows)}")

    trace = build_trace(reviewed_rows, raw_pred_rows, guarded_rows)
    fieldnames = list(trace[0].keys()) if trace else []
    write_csv(args.trace_output, trace, fieldnames)

    parse_counts = dist(guarded_rows, "QWEN_parse_status")
    api_failed_count = parse_counts.get("api_failed", 0)
    parse_ok_rate = safe_rate(parse_counts.get("ok", 0), len(guarded_rows))
    schema_failed_count = parse_counts.get("schema_failed", 0)
    invalid_count, invalid_examples = invalid_enum_count(guarded_rows)
    evidence_empty_count = 0
    evidence_parse_failed_count = 0
    for row in guarded_rows:
        evidence, failed = parse_json_list(row.get("QWEN_requirement_coverage_evidence_json", ""))
        if failed:
            evidence_parse_failed_count += 1
        if not evidence:
            evidence_empty_count += 1
    new_fields = [
        "QWEN_requirement_coverage_evidence_json",
        "QWEN_uses_non_gold_candidate_capability",
        "QWEN_gold_only_coverage_check",
        "QWEN_service_family_inference_risk",
        "QWEN_explicit_tool_name_leak_bias_risk",
        "QWEN_capability_inference_risk",
    ]
    new_required_fields_present = all(field in guarded_rows[0] for field in new_fields) if guarded_rows else False
    raw_text = read_text(args.raw_output_jsonl)
    no_api_key_in_logs = ("Authorization" not in raw_text) and ("Bearer " not in raw_text) and ("sk-" not in raw_text)

    human_keep = [row for row in trace if row["human_final"] == "keep_for_cleaning_candidate"]
    human_remove = [row for row in trace if row["human_final"] == "remove"]
    human_uncertain = [row for row in trace if row["human_final"] == "uncertain"]
    human_critical = [row for row in trace if row["human_severity"] == "critical"]
    high_conf_keep = [row for row in trace if row["qwen_high_conf_keep"] == "true"]
    guarded_keep = [row for row in trace if row["qwen_guarded_keep"] == "true"]
    critical_false_keep = [row for row in high_conf_keep if row["human_severity"] == "critical"]
    remove_false_keep = [row for row in high_conf_keep if row["human_final"] == "remove"]
    uncertain_high_conf_keep = [row for row in high_conf_keep if row["human_final"] == "uncertain"]

    guard_downgrade_count = sum(
        1
        for row in guarded_rows
        if row.get("QWEN_capability_coverage_check") == "coverage_ok"
        and row.get("QWEN_guarded_capability_coverage_check") != "coverage_ok"
    )
    top_reasons = top_guard_reasons(guarded_rows)

    high_conf_precision_den = len(high_conf_keep)
    high_confidence_keep_precision_like = safe_rate(
        sum(1 for row in high_conf_keep if row["human_final"] == "keep_for_cleaning_candidate"),
        high_conf_precision_den,
        empty_value=1.0,
    )
    remove_capture = safe_rate(len(human_remove) - len(remove_false_keep), len(human_remove), empty_value=1.0)
    critical_capture = safe_rate(len(human_critical) - len(critical_false_keep), len(human_critical), empty_value=1.0)
    coverage_ok_recall_on_keep = safe_rate(
        sum(1 for row in human_keep if row["qwen_guarded_keep"] == "true"),
        len(human_keep),
        empty_value=0.0,
    )
    remove_false_keep_rate = safe_rate(len(remove_false_keep), len(human_remove), empty_value=0.0)

    pass_hard_gates = (
        len(guarded_rows) == 100
        and parse_ok_rate >= 0.95
        and schema_failed_count == 0
        and invalid_count == 0
        and len(critical_false_keep) == 0
        and critical_capture == 1.0
        and len(remove_false_keep) == 0
        and remove_capture >= 0.80
        and high_confidence_keep_precision_like >= 0.90
        and no_api_key_in_logs
    )

    false_keep_examples = [
        {
            "qa_item_id": row["qa_item_id"],
            "task_id": row["task_id"],
            "human_final": row["human_final"],
            "human_severity": row["human_severity"],
            "human_error_type": row["human_error_type"],
            "qwen_guarded_label": row["QWEN_guarded_capability_coverage_check"],
            "qwen_confidence": row["QWEN_guarded_capability_coverage_confidence"],
            "reason": row["QWEN_reason"][:500],
            "blocking_reasons": row["QWEN_guarded_blocking_reasons"],
        }
        for row in (critical_false_keep + remove_false_keep + uncertain_high_conf_keep)[:25]
    ]

    summary = {
        "generated_time": now_text(),
        "reviewed_csv": str(args.reviewed_csv),
        "predictions_csv": str(args.predictions_csv),
        "guarded_csv": str(args.guarded_csv),
        "rows": len(guarded_rows),
        "api_failed_count": api_failed_count,
        "parse_ok_rate": parse_ok_rate,
        "schema_failed_count": schema_failed_count,
        "invalid_enum_count": invalid_count,
        "invalid_enum_examples": invalid_examples[:25],
        "evidence_empty_count": evidence_empty_count,
        "evidence_parse_failed_count": evidence_parse_failed_count,
        "new_required_fields_present": new_required_fields_present,
        "no_api_key_in_logs": no_api_key_in_logs,
        "human_keep_count": len(human_keep),
        "human_uncertain_count": len(human_uncertain),
        "human_remove_count": len(human_remove),
        "human_critical_count": len(human_critical),
        "task_type_x_human_final": crosstab(trace, "task_type", "human_final"),
        "human_final_distribution": dict(Counter(row["human_final"] for row in trace)),
        "human_severity_distribution": dict(Counter(row["human_severity"] for row in trace)),
        "raw_QWEN_capability_coverage_check_distribution": dict(dist(guarded_rows, "QWEN_capability_coverage_check")),
        "guarded_QWEN_capability_coverage_check_distribution": dict(dist(guarded_rows, "QWEN_guarded_capability_coverage_check")),
        "raw_coverage_ok_count": sum(1 for row in guarded_rows if row.get("QWEN_capability_coverage_check") == "coverage_ok"),
        "guarded_coverage_ok_count": len(guarded_keep),
        "guard_downgrade_count": guard_downgrade_count,
        "top_guarded_blocking_reasons": dict(top_reasons.most_common(20)),
        "gold_only_coverage_check_distribution": dict(dist(guarded_rows, "QWEN_gold_only_coverage_check")),
        "capability_inference_risk_distribution": dict(dist(guarded_rows, "QWEN_capability_inference_risk")),
        "service_family_inference_risk_count": sum(1 for row in guarded_rows if truthy(row.get("QWEN_service_family_inference_risk", ""))),
        "uses_non_gold_candidate_capability_count": sum(1 for row in guarded_rows if truthy(row.get("QWEN_uses_non_gold_candidate_capability", ""))),
        "explicit_tool_name_leak_bias_risk_count": sum(1 for row in guarded_rows if truthy(row.get("QWEN_explicit_tool_name_leak_bias_risk", ""))),
        "critical_false_keep_count": len(critical_false_keep),
        "remove_false_keep_count": len(remove_false_keep),
        "remove_false_keep_rate": remove_false_keep_rate,
        "remove_capture": remove_capture,
        "critical_capture": critical_capture,
        "high_confidence_keep_precision_like": high_confidence_keep_precision_like,
        "high_confidence_keep_count": len(high_conf_keep),
        "coverage_ok_recall_on_keep": coverage_ok_recall_on_keep,
        "uncertain_high_conf_keep_count": len(uncertain_high_conf_keep),
        "false_keep_examples": false_keep_examples,
        "passes_hard_gates": pass_hard_gates,
        "can_accept_qwen_step3_finalqa100_as_auxiliary_guard": pass_hard_gates,
        "can_use_qwen_for_v1_5f_boundary_annotation": pass_hard_gates,
        "can_run_qwen_full2168_next": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "recommended_next_step": (
            "implement v1.5f local tightening dry-run on 2168 clean candidates before any Qwen full2168"
            if pass_hard_gates
            else "inspect Qwen false keeps on finalQA100 and rely on human-derived v1.5f policy tightening, not Qwen full run"
        ),
    }
    write_json(args.summary_output, summary)

    lines = [
        "# Qwen Step3 finalQA100 Reliability Report v1.4d",
        "",
        f"Generated time: {now_text()}",
        f"Input reviewed CSV: `{args.reviewed_csv}`",
        f"Input predictions CSV: `{args.predictions_csv}`",
        f"Input guarded CSV: `{args.guarded_csv}`",
        f"Sample count: {len(guarded_rows)}",
        "",
        "## Basic Run Metrics",
        "",
        f"- rows: {summary['rows']}",
        f"- api_failed_count: {api_failed_count}",
        f"- parse_ok_rate: {parse_ok_rate}",
        f"- schema_failed_count: {schema_failed_count}",
        f"- invalid_enum_count: {invalid_count}",
        f"- evidence_empty_count: {evidence_empty_count}",
        f"- evidence_parse_failed_count: {evidence_parse_failed_count}",
        f"- new_required_fields_present: {str(new_required_fields_present).lower()}",
        f"- no_api_key_in_logs: {str(no_api_key_in_logs).lower()}",
        "",
        "## Human Final Distribution",
        "",
        *table(Counter(row["human_final"] for row in trace)),
        "",
        "## Qwen Raw Capability Coverage",
        "",
        *table(dist(guarded_rows, "QWEN_capability_coverage_check")),
        "",
        "## Qwen Guarded Capability Coverage",
        "",
        *table(dist(guarded_rows, "QWEN_guarded_capability_coverage_check")),
        "",
        "## Reliability Metrics",
        "",
        f"- critical_false_keep_count: {len(critical_false_keep)}",
        f"- remove_false_keep_count: {len(remove_false_keep)}",
        f"- remove_false_keep_rate: {remove_false_keep_rate}",
        f"- remove_capture: {remove_capture}",
        f"- critical_capture: {critical_capture}",
        f"- high_confidence_keep_precision_like: {high_confidence_keep_precision_like}",
        f"- high_confidence_keep_count: {len(high_conf_keep)}",
        f"- coverage_ok_recall_on_keep: {coverage_ok_recall_on_keep}",
        f"- uncertain_high_conf_keep_count: {len(uncertain_high_conf_keep)}",
        f"- guard_downgrade_count: {guard_downgrade_count}",
        "",
        "## Top Guarded Blocking Reasons",
        "",
        *table(Counter(dict(top_reasons.most_common(20)))),
        "",
        "## False Keep Examples",
        "",
    ]
    if false_keep_examples:
        for item in false_keep_examples:
            lines.append(
                f"- {item['qa_item_id']} / {item['task_id']} / human={item['human_final']} / severity={item['human_severity']} / "
                f"qwen={item['qwen_guarded_label']} {item['qwen_confidence']} / error={item['human_error_type']}"
            )
    else:
        lines.append("- None under the high-confidence guarded coverage_ok definition.")
    lines.extend(
        [
            "",
            "This reliability report evaluates Qwen as an auxiliary fail-closed guard only. It does not replace human final labels and does not authorize full cleaning, split, baseline, or training.",
        ]
    )
    reliability_report = DOC_DIR / "qwen_step3_finalqa100_reliability_report_v1_4d.md"
    write_md(reliability_report, lines)

    go_lines = [
        "# Qwen Step3 finalQA100 Go / No-Go v1.4d",
        "",
        f"Generated time: {now_text()}",
        f"Input summary JSON: `{args.summary_output}`",
        f"Sample count: {len(guarded_rows)}",
        "",
        f"- can_accept_qwen_step3_finalqa100_as_auxiliary_guard: {str(pass_hard_gates).lower()}",
        f"- can_use_qwen_for_v1_5f_boundary_annotation: {str(pass_hard_gates).lower()}",
        "- can_run_qwen_full2168_next: false",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        f"- recommended_next_step: {summary['recommended_next_step']}",
        "",
        "## Hard Gates",
        "",
        f"- rows = 100: {str(len(guarded_rows) == 100).lower()}",
        f"- parse_ok_rate >= 0.95: {parse_ok_rate}",
        f"- schema_failed_count = 0: {schema_failed_count}",
        f"- invalid_enum_count = 0: {invalid_count}",
        f"- critical_false_keep_count = 0: {len(critical_false_keep)}",
        f"- critical_capture = 1.0: {critical_capture}",
        f"- remove_false_keep_count = 0: {len(remove_false_keep)}",
        f"- remove_capture >= 0.80: {remove_capture}",
        f"- high_confidence_keep_precision_like >= 0.90: {high_confidence_keep_precision_like}",
        f"- no_api_key_in_logs = true: {str(no_api_key_in_logs).lower()}",
        "",
        "No Qwen full2168, full cleaning, final clean dataset, split, baseline, or training was run.",
    ]
    go_report = DOC_DIR / "qwen_step3_finalqa100_go_no_go_v1_4d.md"
    write_md(go_report, go_lines)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in [
        args.predictions_csv,
        args.raw_output_jsonl,
        args.guarded_csv,
        args.trace_output,
        args.summary_output,
        reliability_report,
        go_report,
    ]:
        if path.exists():
            shutil.copy2(path, ARCHIVE_DIR / path.name)

    print(f"rows: {summary['rows']}")
    print(f"parse_ok_rate: {parse_ok_rate}")
    print(f"schema_failed_count: {schema_failed_count}")
    print(f"invalid_enum_count: {invalid_count}")
    print(f"critical_false_keep_count: {len(critical_false_keep)}")
    print(f"remove_false_keep_count: {len(remove_false_keep)}")
    print(f"remove_capture: {remove_capture}")
    print(f"critical_capture: {critical_capture}")
    print(f"high_confidence_keep_precision_like: {high_confidence_keep_precision_like}")
    print(f"coverage_ok_recall_on_keep: {coverage_ok_recall_on_keep}")
    print(f"guard_downgrade_count: {guard_downgrade_count}")
    print(f"passes_hard_gates: {pass_hard_gates}")
    print(f"summary_output: {args.summary_output}")
    return 0 if pass_hard_gates else 2


if __name__ == "__main__":
    raise SystemExit(main())
