from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path("outputs/qwen_semcap_judge_v1_4d_step3")
DOC_DIR = Path("docs/phase1")
EXPECTED_DANGEROUS = {
    "R3-011": "ToolBench_G1_141",
    "SCV09-013": "ToolBench_G2_69",
    "SCV09-014": "ToolBench_G2_70",
    "SCV09-034": "ToolBench_G1_53",
    "SCV09-076": "ToolBench_G2_56",
}


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


def taxonomy_for(row: dict[str, str]) -> dict[str, str]:
    record_id = row.get("record_id", "")
    query = row.get("query_text", "").lower()
    qwen_reason = row.get("QWEN_reason", "")

    if record_id == "R3-011":
        return {
            "failure_type": "product_discovery_vs_product_detail_gap;candidate_only_api_used_as_gold_evidence;insufficient_gold_api_evidence",
            "root_cause": "Qwen treated adjacent product/spec/detail capability as enough for latest laptop discovery, price comparison, and review requirements.",
            "required_prompt_fix": "Require a gold-only evidence matrix for every core requirement; product detail/spec/review APIs must not imply latest product discovery or market price search.",
            "required_policy_guard": "Block high-confidence coverage_ok when any requirement lacks explicit gold service/API evidence or evidence comes only from non-gold candidates.",
        }
    if record_id == "SCV09-013":
        return {
            "failure_type": "geographic_or_carrier_scope_overgeneralization;explicit_tool_name_leak_treated_as_coverage;service_family_overgeneralization;insufficient_gold_api_evidence",
            "root_cause": "Qwen overgeneralized carrier/office service capability from a named Argentine carrier context and treated service-family proximity as coverage for flyer distribution logistics.",
            "required_prompt_fix": "State that explicit service/carrier names in the query are not evidence of coverage; require gold evidence for the actual requested operation and geographic/scope constraints.",
            "required_policy_guard": "Downgrade coverage_ok if explicit tool/service mention may bias the judgment or if carrier/geographic scope is inferred without gold API evidence.",
        }
    if record_id == "SCV09-014":
        return {
            "failure_type": "barcode_parser_vs_qr_generator_gap;candidate_only_api_used_as_gold_evidence;explicit_tool_name_leak_treated_as_coverage;insufficient_gold_api_evidence",
            "root_cause": "Qwen conflated parsing/validation/barcode-related APIs with generating QR codes or invitations, and relied on adjacent candidate capabilities.",
            "required_prompt_fix": "Separate parser/validator/decoder APIs from generator APIs; a parser does not generate QR codes unless the gold API explicitly says create/generate QR code.",
            "required_policy_guard": "Block high-confidence coverage_ok when requirement evidence cites non-gold candidate APIs or when gold-only coverage check is not pass.",
        }
    if record_id == "SCV09-034":
        return {
            "failure_type": "service_family_overgeneralization;insufficient_gold_api_evidence;candidate_only_api_used_as_gold_evidence",
            "root_cause": "Qwen inferred profile-picture/business-data coverage from WhatsApp/phone-number service family similarity without enough explicit gold API evidence.",
            "required_prompt_fix": "Require exact gold API evidence for each requested field; service-family similarity cannot imply profile picture or business data coverage.",
            "required_policy_guard": "Downgrade if service_family_inference_risk is true or any requested field lacks gold evidence in requirement_coverage_evidence.",
        }
    if record_id == "SCV09-076":
        return {
            "failure_type": "geographic_or_carrier_scope_overgeneralization;explicit_tool_name_leak_treated_as_coverage;service_family_overgeneralization;insufficient_gold_api_evidence",
            "root_cause": "Qwen treated shipping quote/carrier-related capability as sufficient for Argentina conference package shipping despite insufficient gold API evidence and scope uncertainty.",
            "required_prompt_fix": "Require gold evidence for package shipping quote details and carrier/geographic scope; do not treat named carrier or service-family proximity as coverage.",
            "required_policy_guard": "Downgrade high-confidence coverage_ok when geographic/carrier scope is inferred, explicit-tool leak bias is present, or gold_only_coverage_check is fail/uncertain.",
        }

    if "qr" in query or "barcode" in query:
        failure_type = "barcode_parser_vs_qr_generator_gap;insufficient_gold_api_evidence"
        root_cause = "Qwen may have conflated parser/validator capability with generation capability."
    elif "ship" in query or "carrier" in query or "correo" in query or "argentina" in query:
        failure_type = "geographic_or_carrier_scope_overgeneralization;service_family_overgeneralization"
        root_cause = "Qwen may have overgeneralized carrier/geographic scope."
    elif "laptop" in query or "product" in query:
        failure_type = "product_discovery_vs_product_detail_gap;insufficient_gold_api_evidence"
        root_cause = "Qwen may have overgeneralized product detail capability to product discovery."
    else:
        failure_type = "insufficient_gold_api_evidence"
        root_cause = "Qwen high-confidence coverage_ok lacks sufficiently specific gold-only evidence."
    return {
        "failure_type": failure_type,
        "root_cause": root_cause,
        "required_prompt_fix": "Require gold-only evidence for every core requirement and use conservative uncertain when evidence is insufficient.",
        "required_policy_guard": "Downgrade coverage_ok when gold-only evidence matrix is incomplete or risk flags are present.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Qwen v1.4d dangerous false keep cases.")
    parser.add_argument(
        "--failure-cases",
        type=Path,
        default=Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_failure_cases_v1_4d.csv"),
    )
    parser.add_argument(
        "--trace",
        type=Path,
        default=Path("outputs/qwen_semcap_judge_v1_4d/eval/qwen_calibration_eval_trace_v1_4d.csv"),
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=OUTPUT_DIR / "qwen_dangerous_false_keep_analysis.csv",
    )
    parser.add_argument(
        "--taxonomy-md",
        type=Path,
        default=DOC_DIR / "qwen_dangerous_false_keep_failure_taxonomy_v1_4d.md",
    )
    args = parser.parse_args()

    if not args.failure_cases.exists():
        raise FileNotFoundError(f"Missing failure cases CSV: {args.failure_cases}")
    if not args.trace.exists():
        raise FileNotFoundError(f"Missing eval trace CSV: {args.trace}")

    trace_rows = read_csv(args.trace)
    dangerous = [row for row in trace_rows if row.get("dangerous_false_keep") == "yes"]
    by_record = {row.get("record_id", ""): row for row in dangerous}
    missing_expected = [
        f"{record_id}/{task_id}"
        for record_id, task_id in EXPECTED_DANGEROUS.items()
        if record_id not in by_record or by_record[record_id].get("task_id") != task_id
    ]
    if missing_expected:
        raise RuntimeError("Missing expected dangerous false keep rows: " + ", ".join(missing_expected))

    out_rows: list[dict[str, Any]] = []
    for row in dangerous:
        tax = taxonomy_for(row)
        out_rows.append(
            {
                "record_id": row.get("record_id", ""),
                "task_id": row.get("task_id", ""),
                "query_text": row.get("query_text", ""),
                "human_capability_coverage_check": row.get("human_capability_coverage_check", ""),
                "QWEN_capability_coverage_check": row.get("QWEN_capability_coverage_check", ""),
                "QWEN_capability_coverage_confidence": row.get("QWEN_capability_coverage_confidence", ""),
                "QWEN_reason": row.get("QWEN_reason", ""),
                "failure_type": tax["failure_type"],
                "root_cause": tax["root_cause"],
                "required_prompt_fix": tax["required_prompt_fix"],
                "required_policy_guard": tax["required_policy_guard"],
                "should_block_full2168": "yes",
            }
        )

    fieldnames = [
        "record_id",
        "task_id",
        "query_text",
        "human_capability_coverage_check",
        "QWEN_capability_coverage_check",
        "QWEN_capability_coverage_confidence",
        "QWEN_reason",
        "failure_type",
        "root_cause",
        "required_prompt_fix",
        "required_policy_guard",
        "should_block_full2168",
    ]
    write_csv(args.output_csv, out_rows, fieldnames)

    type_counts = Counter()
    for row in out_rows:
        for item in row["failure_type"].split(";"):
            type_counts[item] += 1

    lines = [
        "# Qwen Dangerous False Keep Failure Taxonomy v1.4d",
        "",
        f"Generated time: {now_text()}",
        f"Input trace: `{args.trace}`",
        f"Input failure cases: `{args.failure_cases}`",
        f"Analyzed dangerous false keep cases: {len(out_rows)}",
        "",
        "## Boundary",
        "",
        "These rows are used only for failure taxonomy and regression evaluation.",
        "They must not be hard-coded into Qwen judge prompts, policies, or cleaning rules.",
        "Exact failure rows are not included as few-shot examples in the Step3 prompt.",
        "",
        "## Failure Type Distribution",
        "",
        "| failure_type | count |",
        "|---|---:|",
        *[f"| {key} | {value} |" for key, value in sorted(type_counts.items())],
        "",
        "## Case Summaries",
        "",
    ]
    for row in out_rows:
        lines.extend(
            [
                f"### {row['record_id']} / {row['task_id']}",
                "",
                f"- human capability: `{row['human_capability_coverage_check']}`",
                f"- Qwen capability: `{row['QWEN_capability_coverage_check']}` / `{row['QWEN_capability_coverage_confidence']}`",
                f"- failure_type: `{row['failure_type']}`",
                f"- root_cause: {row['root_cause']}",
                f"- required_prompt_fix: {row['required_prompt_fix']}",
                f"- required_policy_guard: {row['required_policy_guard']}",
                f"- should_block_full2168: `{row['should_block_full2168']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Required Step3 Fix Themes",
            "",
            "- Gold-only evidence rule: only gold services/APIs can prove coverage.",
            "- Requirement evidence matrix: each core requirement must cite gold evidence.",
            "- No service-family inference: adjacent service families are not enough.",
            "- Parser/generator distinction: parse/validate/decode is not generate/create.",
            "- Leak is not coverage: explicit tool names cannot prove capability.",
            "- Conservative tie-breaker: uncertain beats high-confidence coverage_ok when evidence is incomplete.",
        ]
    )
    write_text(args.taxonomy_md, "\n".join(lines) + "\n")

    print(f"dangerous_false_keep_cases: {len(out_rows)}")
    print(f"output_csv: {args.output_csv}")
    print(f"taxonomy_md: {args.taxonomy_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
