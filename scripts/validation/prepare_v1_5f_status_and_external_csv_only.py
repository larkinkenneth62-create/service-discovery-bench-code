#!/usr/bin/env python
"""Prepare v1.5f branch status and external QA CSV-only review packs.

This script does not create HTML, does not call Qwen or external APIs, does not
merge external sources, and does not create final datasets/splits/baselines.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_INPUTS = [
    "docs/phase1/final_qa_v1_5e_go_no_go_report.md",
    "docs/phase1/final_qa_v1_5e_failure_taxonomy.md",
    "docs/phase1/policy_v1_5f_tightening_plan_from_final_qa.md",
    "docs/phase1/llm_judge_reliability_protocol_v0_1.md",
    "docs/phase1/qwen_step3_finalqa100_reliability_validation_report_v1_4d.md",
    "docs/phase1/qwen_step3_finalqa100_go_no_go_v1_4d.md",
    "docs/phase1/external_source_recovery_inventory_v1_5f_pre.md",
    "docs/phase1/external_source_integration_strategy_v0_1.md",
    "docs/phase1/external_source_recovery_go_no_go_v1_5f_pre.md",
    "docs/phase1/metatool_single_service_adapter_report_v0_1.md",
    "docs/phase1/metatool_single_service_review_plan_v0_1.md",
    "docs/phase1/stabletoolbench_solvable_adapter_report_v0_1.md",
    "docs/phase1/stabletoolbench_solvable_review_plan_v0_1.md",
    "docs/phase1/shortcutsbench_source_check_v0_1.md",
    "outputs/final_qa_v1_5e/final_qa_review_items_v1_5e_gpt_manual_reviewed.csv",
    "outputs/full_clean_dryrun_v1_4c/full_clean_task_trace_v1_4c.csv",
    "outputs/full_clean_dryrun_v1_4c/full_clean_dryrun_summary_v1_4c.json",
    "outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100.csv",
    "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_or_all.csv",
    "outputs/external_sources_adapters_v0_1/metatool/metatool_adapter_summary.json",
    "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_adapter_summary.json",
    "outputs/external_sources_adapters_v0_1/shortcutsbench/shortcutsbench_source_check.json",
]

METATOOL_HUMAN_FIELDS = [
    "qa_final_decision",
    "qa_semantic_alignment_check",
    "qa_candidate_validity_check",
    "qa_service_catalog_check",
    "qa_leakage_check",
    "qa_error_type",
    "qa_severity",
    "qa_notes",
    "reviewer_id",
    "reviewed_at",
]

STABLE_HUMAN_FIELDS = [
    "qa_final_decision",
    "qa_semantic_alignment_check",
    "qa_capability_coverage_check",
    "qa_candidate_validity_check",
    "qa_task_type_check",
    "qa_leakage_check",
    "qa_error_type",
    "qa_severity",
    "qa_notes",
    "reviewer_id",
    "reviewed_at",
]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def compact_json_string(value: str) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True)
    except Exception:
        return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def ensure_missing_inputs(project_root: Path) -> list[str]:
    return [rel for rel in REQUIRED_INPUTS if not (project_root / rel).exists()]


def write_missing(project_root: Path, missing: list[str]) -> None:
    out = project_root / "outputs/current_next_step_v1_5f/MISSING_INPUTS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Missing Inputs For v1.5f Current Step",
        "",
        f"Generated time: {now()}",
        "",
        "The run stopped because required inputs are missing. No fallback data was used.",
        "",
    ]
    lines.extend(f"- `{item}`" for item in missing)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def prepare_metatool_csv(project_root: Path) -> Path:
    src = project_root / "outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100.csv"
    dst = project_root / "outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100_csv_only.csv"
    rows = read_csv(src)
    for row in rows:
        for key in ["candidate_services_json", "gold_services_json"]:
            row[key] = compact_json_string(row.get(key, ""))
        warnings = row.get("adapter_warnings", "")
        row["service_leakage_risk"] = "yes" if "service_leakage_risk" in warnings else "no"
        row["gold_service_unmatched"] = "yes" if "gold_service_unmatched" in warnings else "no"
        for field in METATOOL_HUMAN_FIELDS:
            row[field] = ""
    preferred = [
        "review_item_id",
        "task_id",
        "source_dataset",
        "task_type",
        "query_text",
        "candidate_services_json",
        "gold_services_json",
        "source_tool_or_plugin_name",
        "adapter_warnings",
        "service_leakage_risk",
        "gold_service_unmatched",
    ]
    fieldnames = preferred + [f for f in METATOOL_HUMAN_FIELDS if f not in preferred]
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.insert(-len(METATOOL_HUMAN_FIELDS), key)
    write_csv(dst, rows, fieldnames)
    return dst


def prepare_stable_csv(project_root: Path) -> Path:
    src = project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_or_all.csv"
    dst = project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_or_all_csv_only.csv"
    rows = read_csv(src)
    for row in rows:
        if "review_item_id" not in row or not row.get("review_item_id"):
            row["review_item_id"] = row.get("qa_item_id", "")
        row["adapter_warnings"] = row.get("adapter_notes", "")
        for key in [
            "candidate_services_json",
            "candidate_apis_json",
            "gold_services_json",
            "gold_apis_json",
            "available_tools_or_apis_json",
            "gold_tools_or_apis_json",
        ]:
            if key in row:
                row[key] = compact_json_string(row.get(key, ""))
        for field in STABLE_HUMAN_FIELDS:
            row[field] = ""
    preferred = [
        "review_item_id",
        "task_id",
        "source_dataset",
        "stable_group",
        "task_type_guess",
        "query_text",
        "available_tools_or_apis_json",
        "gold_tools_or_apis_json",
        "adapter_warnings",
        "requires_composable_dependency_check",
    ]
    fieldnames = preferred + [f for f in rows[0].keys() if f not in preferred and f not in STABLE_HUMAN_FIELDS]
    fieldnames += [f for f in STABLE_HUMAN_FIELDS if f not in fieldnames]
    write_csv(dst, rows, fieldnames)
    return dst


def write_branch_status(project_root: Path) -> dict[str, Any]:
    full_summary = read_json(project_root / "outputs/full_clean_dryrun_v1_4c/full_clean_dryrun_summary_v1_4c.json")
    meta = read_json(project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_adapter_summary.json")
    stable = read_json(project_root / "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_adapter_summary.json")
    shortcuts = read_json(project_root / "outputs/external_sources_adapters_v0_1/shortcutsbench/shortcutsbench_source_check.json")
    finalqa_rows = read_csv(project_root / "outputs/final_qa_v1_5e/final_qa_review_items_v1_5e_gpt_manual_reviewed.csv")
    final_dist = Counter(row.get("qa_final_decision", "") for row in finalqa_rows)
    severity_dist = Counter(row.get("qa_severity", "") for row in finalqa_rows)

    summary = {
        "generated_time": now(),
        "branches": {
            "ToolBench-core": {
                "source": "ToolBench",
                "current_state": "v1.4c dry-run clean candidates",
                "v1_4c_clean_candidate_count": full_summary.get("v1_4c_clean_candidate_count"),
                "final_qa_v1_5e": "NO_GO_V1_6_AS_IS",
                "final_qa_decision_distribution": dict(final_dist),
                "final_qa_severity_distribution": dict(severity_dist),
                "next_step": "v1.5f deterministic tightening dry-run",
                "can_generate_final_dataset": False,
            },
            "MetaTool-single": {
                "source_present": True,
                "rows": meta.get("task_rows"),
                "services": meta.get("service_catalog_size"),
                "adapter_built": True,
                "unmatched_gold_service_count": meta.get("unmatched_gold_service_count"),
                "service_leakage_risk_count": meta.get("service_leakage_risk_count"),
                "next_step": "human review 100-row CSV QA pack",
                "can_merge_now": False,
            },
            "StableToolBench-solvable": {
                "source_present": True,
                "rows_total": stable.get("total_rows"),
                "rows_by_group": stable.get("rows_by_group"),
                "adapter_built": True,
                "next_step": "human review 100-row CSV QA pack",
                "g3_requires_dependency_chain_review": True,
                "can_merge_now": False,
            },
            "ShortcutsBench": {
                "source_present": shortcuts.get("source_present"),
                "extracted_parseable": shortcuts.get("extracted_files_parseable"),
                "adapter_built": False,
                "next_step": "hold / future strict adapter",
                "can_merge_now": False,
            },
        },
        "can_generate_full_six_task_benchmark_now": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
    }
    out_json = project_root / "outputs/current_next_step_v1_5f/current_branch_status_summary.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = project_root / "docs/phase1/current_branch_status_after_external_recovery_v1_5f.md"
    lines = [
        "# Current Branch Status After External Recovery v1.5f",
        "",
        f"Generated time: {summary['generated_time']}",
        "",
        "## ToolBench-core branch",
        "",
        "- source: ToolBench",
        f"- current state: v1.4c dry-run clean candidates = {full_summary.get('v1_4c_clean_candidate_count')}",
        "- final QA v1.5e = NO_GO_V1_6_AS_IS",
        "- next step = v1.5f deterministic tightening dry-run",
        "- can_generate_final_dataset = false",
        "",
        "## MetaTool-single branch",
        "",
        "- source present = true",
        f"- rows = {meta.get('task_rows')}",
        f"- services = {meta.get('service_catalog_size')}",
        "- adapter built = true",
        f"- unmatched_gold_service_count = {meta.get('unmatched_gold_service_count')}",
        f"- service_leakage_risk_count = {meta.get('service_leakage_risk_count')}",
        "- next step = human review 100-row CSV QA pack",
        "- can_merge_now = false",
        "",
        "## StableToolBench-solvable branch",
        "",
        "- source present = true",
        f"- rows total = {stable.get('total_rows')}",
        f"- G1 = {stable.get('rows_by_group', {}).get('G1')}",
        f"- G2 = {stable.get('rows_by_group', {}).get('G2')}",
        f"- G3 = {stable.get('rows_by_group', {}).get('G3')}",
        "- adapter built = true",
        "- next step = human review 100-row CSV QA pack",
        "- G3 requires dependency-chain review",
        "- can_merge_now = false",
        "",
        "## ShortcutsBench branch",
        "",
        f"- source present = {shortcuts.get('source_present')}",
        f"- extracted parseable = {shortcuts.get('extracted_files_parseable')}",
        "- adapter built = false",
        "- next step = hold / future strict adapter",
        "- can_merge_now = false",
        "",
        "## Fixed No-Go Flags",
        "",
        "- can_generate_full_six_task_benchmark_now = false",
        "- can_generate_final_clean_dataset_now = false",
        "- can_create_split_now = false",
        "- can_run_baseline_now = false",
        "- can_train_model_now = false",
    ]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def write_external_docs(project_root: Path, meta_csv: Path, stable_csv: Path) -> None:
    instruction = project_root / "docs/phase1/external_qa_csv_human_review_instruction_v0_1.md"
    instruction.write_text(
        f"""# External QA CSV Human Review Instruction v0.1

Generated time: {now()}

## Review Mode

- Review mode: CSV-only.
- No HTML review app is generated.
- The reviewer fills only the right-side `qa_*`, `reviewer_id`, and `reviewed_at` fields.
- Empty `qa_*` fields mean pending review, not pass/fail.

## Files To Review

- MetaTool: `{meta_csv}`
- StableToolBench: `{stable_csv}`

## MetaTool Review Guide

- Check whether `query_text` semantically matches `gold_services_json`.
- Check whether the 199-service candidate catalog is a valid choice space.
- Check whether `query_text` directly leaks the gold plugin/service name.
- If the query directly contains the gold plugin/service name, it should not enter the main service discovery clean set unless a future rewrite policy is created.
- Check whether `adapter_warnings`, `service_leakage_risk`, or `gold_service_unmatched` should block the sample.
- MetaTool is currently only `single_service_discovery_external`; do not treat it as an API-level benchmark.

## StableToolBench Review Guide

- Check whether `query_text` and `gold_tools_or_apis_json` are semantically aligned.
- Check whether candidate APIs form a real choice space.
- Check whether G1/G2/G3 `task_type_guess` is reasonable.
- G3 cannot be marked strong composable only because it comes from G3.
- G3 needs an explicit dependency chain to enter composable.
- Check leakage, missing core requirement, and invalid candidate/gold structure.
""",
        encoding="utf-8",
    )

    field_dict = project_root / "docs/phase1/external_qa_csv_field_dictionary_v0_1.md"
    field_dict.write_text(
        f"""# External QA CSV Field Dictionary v0.1

Generated time: {now()}

## Shared Human Fields

- `qa_final_decision`: allowed values are `keep_for_cleaning_candidate`, `uncertain`, `remove`.
- `qa_semantic_alignment_check`: allowed values are `ok`, `uncertain`, `mismatch`.
- `qa_candidate_validity_check`: allowed values are `valid`, `uncertain`, `invalid`.
- `qa_leakage_check`: allowed values are `no_obvious_leak`, `service_leak_blocking`, `api_leak_blocking`, `leak_uncertain`.
- `qa_error_type`: may be empty only when the row is still pending or clean with `qa_severity=none`; use semicolon-separated tags for multiple issues.
- `qa_severity`: allowed values are `none`, `low`, `medium`, `high`, `critical`.
- `qa_notes`: free-form reviewer notes.
- `reviewer_id`: reviewer identifier.
- `reviewed_at`: review timestamp, preferably ISO-like date/time.

## MetaTool-Specific Fields

- `qa_service_catalog_check`: allowed values are `valid_catalog`, `catalog_uncertain`, `invalid_catalog`, `not_applicable`.
- `service_leakage_risk`: adapter-level hint, not a human decision.
- `gold_service_unmatched`: adapter-level hint, not a human decision.

## StableToolBench-Specific Fields

- `qa_capability_coverage_check`: allowed values are `coverage_ok`, `coverage_uncertain`, `coverage_mismatch`, `not_applicable`.
- `qa_task_type_check`: allowed values are `task_type_ok`, `task_type_uncertain`, `task_type_invalid`, `composable_not_strong_dependency`, `not_applicable`.
- `requires_composable_dependency_check`: adapter-level hint. For G3 rows, manually verify dependency chain.

## Suggested `qa_error_type` Tags

`service_leak`; `api_leak`; `semantic_mismatch`; `capability_mismatch`; `candidate_space_invalid`; `invalid_gold_service`; `invalid_gold_api`; `missing_core_requirement`; `wrong_task_type`; `not_strong_composable`; `adapter_warning_blocking`; `unsupported_external_source`; `duplicate_or_nonrepresentative`; `other`.
""",
        encoding="utf-8",
    )

    criteria = project_root / "docs/phase1/external_qa_csv_go_no_go_criteria_v0_1.md"
    criteria.write_text(
        f"""# External QA CSV Go/No-Go Criteria v0.1

Generated time: {now()}

## Required Conditions Before Source-Specific Cleaning

- `critical_count = 0`.
- All rows must be reviewed; otherwise status remains `pending`.
- High remove/uncertain rates mean the source cannot be directly merged.
- Leakage-risk samples must be counted separately and cannot be blindly extrapolated.
- MetaTool / StableToolBench QA results must not be mixed into ToolBench-core final QA statistics.
- Even if external QA passes, it only permits source-specific clean policy design, not final dataset generation.

## Always False In This Stage

- can_generate_final_clean_dataset_now = false
- can_merge_external_sources_now = false
- can_generate_full_six_task_benchmark_now = false
- can_create_split_now = false
- can_run_baseline_now = false
- can_train_model_now = false
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare v1.5f branch status and external QA CSV-only packs.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current working directory.")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    missing = ensure_missing_inputs(project_root)
    if missing:
        write_missing(project_root, missing)
        raise SystemExit(f"Missing required inputs. See outputs/current_next_step_v1_5f/MISSING_INPUTS.md")

    status = write_branch_status(project_root)
    meta_csv = prepare_metatool_csv(project_root)
    stable_csv = prepare_stable_csv(project_root)
    write_external_docs(project_root, meta_csv, stable_csv)

    result = {
        "generated_time": now(),
        "branch_status_dashboard_generated": True,
        "external_review_mode": "csv_only",
        "metatool_csv_review_pack_generated": meta_csv.exists(),
        "metatool_rows": count_csv_rows(meta_csv),
        "stabletoolbench_csv_review_pack_generated": stable_csv.exists(),
        "stabletoolbench_rows": count_csv_rows(stable_csv),
        "html_review_app_generated": False,
        "status_json": "outputs/current_next_step_v1_5f/current_branch_status_summary.json",
        "status_doc": "docs/phase1/current_branch_status_after_external_recovery_v1_5f.md",
        "fixed_no_go": {
            "can_generate_full_six_task_benchmark_now": status["can_generate_full_six_task_benchmark_now"],
            "can_generate_final_clean_dataset_now": status["can_generate_final_clean_dataset_now"],
            "can_create_split_now": status["can_create_split_now"],
            "can_run_baseline_now": status["can_run_baseline_now"],
            "can_train_model_now": status["can_train_model_now"],
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
