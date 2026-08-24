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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def parse_json_list(value: str) -> list[Any]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def nonempty_json_list(value: str) -> bool:
    return len(parse_json_list(value)) > 0


def guard_row(row: dict[str, str]) -> dict[str, Any]:
    reasons: list[str] = []
    clear_missing_gold_evidence = False
    evidence_rows = parse_json_list(row.get("QWEN_requirement_coverage_evidence_json", ""))

    if row.get("QWEN_parse_status") != "ok":
        reasons.append("parse_status_not_ok")
    if truthy(row.get("QWEN_uses_non_gold_candidate_capability", "")):
        reasons.append("uses_non_gold_candidate_capability")
    if row.get("QWEN_gold_only_coverage_check") != "pass":
        reasons.append("gold_only_coverage_check_not_pass")
        if row.get("QWEN_gold_only_coverage_check") == "fail":
            clear_missing_gold_evidence = True
    if not evidence_rows:
        reasons.append("requirement_coverage_evidence_empty")
    for index, evidence in enumerate(evidence_rows):
        if not isinstance(evidence, dict):
            reasons.append(f"requirement_evidence_{index}_invalid")
            clear_missing_gold_evidence = True
            continue
        if evidence.get("evidence_is_from_gold") is not True:
            reasons.append(f"requirement_evidence_{index}_not_from_gold")
            clear_missing_gold_evidence = True
        if evidence.get("is_covered") is not True:
            reasons.append(f"requirement_evidence_{index}_not_covered")
            clear_missing_gold_evidence = True
    if nonempty_json_list(row.get("QWEN_missing_requirements_json", "")):
        reasons.append("missing_requirements_nonempty")
        clear_missing_gold_evidence = True
    if nonempty_json_list(row.get("QWEN_extra_unrelated_gold_services_json", "")):
        reasons.append("extra_unrelated_gold_services_nonempty")
        clear_missing_gold_evidence = True
    if truthy(row.get("QWEN_generic_search_overtrust", "")):
        reasons.append("generic_search_overtrust")
    if truthy(row.get("QWEN_domain_specific_gap", "")):
        reasons.append("domain_specific_gap")
        clear_missing_gold_evidence = True
    if truthy(row.get("QWEN_wrong_gold_set", "")):
        reasons.append("wrong_gold_set")
        clear_missing_gold_evidence = True
    if truthy(row.get("QWEN_service_family_inference_risk", "")):
        reasons.append("service_family_inference_risk")
    if truthy(row.get("QWEN_explicit_tool_name_leak_bias_risk", "")):
        reasons.append("explicit_tool_name_leak_bias_risk")
    if row.get("QWEN_capability_inference_risk") == "high":
        reasons.append("capability_inference_risk_high")

    original_cov = row.get("QWEN_capability_coverage_check", "")
    original_conf = row.get("QWEN_capability_coverage_confidence", "")
    guarded_cov = original_cov
    guarded_conf = original_conf
    if original_cov == "coverage_ok" and reasons:
        guarded_cov = "coverage_mismatch" if clear_missing_gold_evidence else "coverage_uncertain"
        guarded_conf = "medium" if guarded_cov == "coverage_mismatch" else "low"
    elif row.get("QWEN_parse_status") != "ok":
        guarded_cov = "coverage_uncertain"
        guarded_conf = "low"

    out = dict(row)
    out["QWEN_guarded_capability_coverage_check"] = guarded_cov
    out["QWEN_guarded_capability_coverage_confidence"] = guarded_conf
    out["QWEN_guarded_blocking_reasons"] = ";".join(dict.fromkeys(reasons))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply conservative Step3 guard to Qwen SemCap predictions.")
    parser.add_argument(
        "--predictions",
        type=Path,
        default=OUTPUT_DIR / "predictions/qwen_step3_predictions_calibration_180.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "eval/qwen_step3_guarded_predictions_calibration_180.csv",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DOC_DIR / "qwen_step3_guard_report_v1_4d.md",
    )
    args = parser.parse_args()
    if not args.predictions.exists():
        raise FileNotFoundError(f"Missing Step3 predictions: {args.predictions}")

    rows = read_csv(args.predictions)
    guarded = [guard_row(row) for row in rows]
    fieldnames = list(rows[0].keys()) if rows else []
    for field in [
        "QWEN_guarded_capability_coverage_check",
        "QWEN_guarded_capability_coverage_confidence",
        "QWEN_guarded_blocking_reasons",
    ]:
        if field not in fieldnames:
            fieldnames.append(field)
    write_csv(args.output, guarded, fieldnames)

    reason_counts = Counter()
    downgraded = 0
    for row in guarded:
        if row.get("QWEN_capability_coverage_check") == "coverage_ok" and row.get("QWEN_guarded_capability_coverage_check") != "coverage_ok":
            downgraded += 1
        for reason in str(row.get("QWEN_guarded_blocking_reasons", "")).split(";"):
            if reason:
                reason_counts[reason] += 1

    lines = [
        "# Qwen Step3 Guard Report v1.4d",
        "",
        f"Generated time: {now_text()}",
        f"Input predictions: `{args.predictions}`",
        f"Output guarded predictions: `{args.output}`",
        f"Sample count: {len(rows)}",
        "",
        "## Guard Summary",
        "",
        f"- original coverage_ok count: {sum(1 for row in rows if row.get('QWEN_capability_coverage_check') == 'coverage_ok')}",
        f"- downgraded coverage_ok count: {downgraded}",
        f"- guarded coverage distribution: {dict(Counter(row.get('QWEN_guarded_capability_coverage_check', '') for row in guarded))}",
        "",
        "## Blocking Reason Distribution",
        "",
        "| reason | count |",
        "|---|---:|",
        *[f"| {key} | {value} |" for key, value in sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "",
        "This guard does not generate final clean labels and does not replace human final decisions.",
    ]
    write_text(args.report, "\n".join(lines) + "\n")

    print(f"rows: {len(rows)}")
    print(f"downgraded_coverage_ok: {downgraded}")
    print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
