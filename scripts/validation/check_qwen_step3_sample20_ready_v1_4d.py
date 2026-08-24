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


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S %z")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def table(counter: dict[str, int]) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    for key, value in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {value} |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Step3 sample20 readiness before calibration180.")
    parser.add_argument(
        "--predictions",
        type=Path,
        default=OUTPUT_DIR / "predictions/qwen_step3_predictions_sample_20.csv",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=OUTPUT_DIR / "predictions/qwen_step3_raw_sample_20.jsonl",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=OUTPUT_DIR / "eval/qwen_step3_sample20_ready_check.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DOC_DIR / "qwen_step3_sample20_report_v1_4d.md",
    )
    parser.add_argument(
        "--go-no-go",
        type=Path,
        default=DOC_DIR / "qwen_step3_calibration_go_no_go_v1_4d.md",
    )
    args = parser.parse_args()
    if not args.predictions.exists():
        raise FileNotFoundError(f"Missing Step3 sample20 predictions: {args.predictions}")
    if not args.raw_output.exists():
        raise FileNotFoundError(f"Missing Step3 sample20 raw output: {args.raw_output}")

    rows = read_csv(args.predictions)
    allowed_sem = {"ok", "uncertain", "mismatch"}
    allowed_cov = {"coverage_ok", "coverage_uncertain", "coverage_mismatch"}
    allowed_conf = {"high", "medium", "low"}
    allowed_gold = {"pass", "fail", "uncertain"}
    allowed_risk = {"low", "medium", "high"}
    required_new = [
        "QWEN_requirement_coverage_evidence_json",
        "QWEN_uses_non_gold_candidate_capability",
        "QWEN_gold_only_coverage_check",
        "QWEN_service_family_inference_risk",
        "QWEN_explicit_tool_name_leak_bias_risk",
        "QWEN_capability_inference_risk",
    ]

    invalid: list[dict[str, str]] = []
    missing_new: list[dict[str, str]] = []
    evidence_empty: list[str] = []
    evidence_parse_failed: list[str] = []
    for row in rows:
        task_id = row.get("task_id", "")
        for field in required_new:
            if field not in row or row.get(field, "") == "":
                missing_new.append({"task_id": task_id, "field": field})
        enum_checks = [
            ("QWEN_semantic_alignment_check", allowed_sem),
            ("QWEN_semantic_alignment_confidence", allowed_conf),
            ("QWEN_capability_coverage_check", allowed_cov),
            ("QWEN_capability_coverage_confidence", allowed_conf),
            ("QWEN_gold_only_coverage_check", allowed_gold),
            ("QWEN_capability_inference_risk", allowed_risk),
        ]
        for field, allowed in enum_checks:
            if row.get(field, "") not in allowed:
                invalid.append({"task_id": task_id, "field": field, "value": row.get(field, "")})
        try:
            evidence = json.loads(row.get("QWEN_requirement_coverage_evidence_json", "[]"))
            if not isinstance(evidence, list) or not evidence:
                evidence_empty.append(task_id)
        except Exception:
            evidence_parse_failed.append(task_id)

    parse_counts = Counter(row.get("QWEN_parse_status", "") for row in rows)
    summary = {
        "generated_time": now_text(),
        "input_predictions": str(args.predictions),
        "input_raw_output": str(args.raw_output),
        "row_count": len(rows),
        "raw_line_count": sum(1 for _ in args.raw_output.open(encoding="utf-8")),
        "parse_status_distribution": dict(parse_counts),
        "parse_ok_count": parse_counts.get("ok", 0),
        "parse_ok_rate": round(parse_counts.get("ok", 0) / len(rows), 4) if rows else 0,
        "schema_failed_count": parse_counts.get("schema_failed", 0),
        "invalid_enum_count": len(invalid),
        "missing_new_required_field_count": len(missing_new),
        "evidence_empty_count": len(evidence_empty),
        "evidence_parse_failed_count": len(evidence_parse_failed),
        "new_required_fields_present": len(missing_new) == 0,
        "sample20_passed": (
            len(rows) == 20
            and parse_counts.get("ok", 0) >= 19
            and parse_counts.get("schema_failed", 0) == 0
            and not invalid
            and not missing_new
            and not evidence_empty
            and not evidence_parse_failed
        ),
        "semantic_distribution": dict(Counter(row.get("QWEN_semantic_alignment_check", "") for row in rows)),
        "capability_distribution": dict(Counter(row.get("QWEN_capability_coverage_check", "") for row in rows)),
        "gold_only_coverage_distribution": dict(Counter(row.get("QWEN_gold_only_coverage_check", "") for row in rows)),
        "capability_inference_risk_distribution": dict(Counter(row.get("QWEN_capability_inference_risk", "") for row in rows)),
        "uses_non_gold_candidate_capability_distribution": dict(Counter(row.get("QWEN_uses_non_gold_candidate_capability", "") for row in rows)),
        "service_family_inference_risk_distribution": dict(Counter(row.get("QWEN_service_family_inference_risk", "") for row in rows)),
        "explicit_tool_name_leak_bias_risk_distribution": dict(Counter(row.get("QWEN_explicit_tool_name_leak_bias_risk", "") for row in rows)),
        "invalid_enum_examples": invalid[:20],
        "missing_new_required_field_examples": missing_new[:20],
        "evidence_empty_examples": evidence_empty[:20],
        "evidence_parse_failed_examples": evidence_parse_failed[:20],
    }
    write_json(args.summary, summary)

    report_lines = [
        "# Qwen Step3 Sample20 Report v1.4d",
        "",
        f"Generated time: {summary['generated_time']}",
        "",
        "## Status",
        "",
        "Step3 sample20 has been run and passed the format/schema readiness gate." if summary["sample20_passed"] else "Step3 sample20 did not pass the readiness gate.",
        "",
        f"Input predictions: `{args.predictions}`",
        f"Input raw output: `{args.raw_output}`",
        f"Sample count: {len(rows)}",
        "",
        "## Gate Checks",
        "",
        f"- parse_ok_rate: {summary['parse_ok_rate']}",
        f"- schema_failed_count: {summary['schema_failed_count']}",
        f"- invalid_enum_count: {summary['invalid_enum_count']}",
        f"- new_required_fields_present: {str(summary['new_required_fields_present']).lower()}",
        f"- evidence_empty_count: {summary['evidence_empty_count']}",
        f"- evidence_parse_failed_count: {summary['evidence_parse_failed_count']}",
        f"- sample20_passed: {str(summary['sample20_passed']).lower()}",
        "",
        "## Parse Status Distribution",
        "",
        *table(summary["parse_status_distribution"]),
        "",
        "## Capability Distribution",
        "",
        *table(summary["capability_distribution"]),
        "",
        "## Gold-only Coverage Distribution",
        "",
        *table(summary["gold_only_coverage_distribution"]),
        "",
        "## Capability Inference Risk Distribution",
        "",
        *table(summary["capability_inference_risk_distribution"]),
        "",
        "## Boundary",
        "",
        "This report only approves running Step3 calibration180 next. It does not approve full2168, full cleaning, final clean dataset generation, split, baseline, or training.",
    ]
    write_text(args.report, "\n".join(report_lines) + "\n")

    go_status = "NO_GO_WAITING_FOR_STEP3_CALIBRATION180" if summary["sample20_passed"] else "NO_GO_STEP3_SAMPLE20_FAILED"
    go_lines = [
        "# Qwen Step3 Calibration Go / No-Go v1.4d",
        "",
        f"Generated time: {summary['generated_time']}",
        "",
        "## Go / No-Go Decision Qwen Step3 Calibration v1.4d",
        "",
        f"- can_accept_qwen_step3_sample20: {str(summary['sample20_passed']).lower()}",
        "- can_accept_qwen_step3_calibration180: false",
        "- can_run_qwen_full2168_next: false",
        "- can_generate_final_clean_dataset_now: false",
        "- can_create_split_now: false",
        "- can_run_baseline_now: false",
        "- can_train_model_now: false",
        "",
        f"Go / No-Go Decision: {go_status}",
        "",
        "recommended_next_step: "
        + ("run Qwen Step3 calibration180, then apply guard and evaluate." if summary["sample20_passed"] else "inspect Step3 sample20 failures before calibration180."),
        "",
        "## Boundary",
        "",
        "Qwen predictions are not human final labels. Do not run full2168, full cleaning, final clean dataset generation, split, baseline, or training at this stage.",
    ]
    write_text(args.go_no_go, "\n".join(go_lines) + "\n")

    print(f"sample20_passed: {summary['sample20_passed']}")
    print(f"parse_ok_rate: {summary['parse_ok_rate']}")
    print(f"schema_failed_count: {summary['schema_failed_count']}")
    print(f"invalid_enum_count: {summary['invalid_enum_count']}")
    print(f"new_required_fields_present: {summary['new_required_fields_present']}")
    print(f"summary: {args.summary}")
    print(f"report: {args.report}")
    return 0 if summary["sample20_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
