#!/usr/bin/env python
"""Prepare starting status and source-specific policy specs for v0.2.

Reporting/specification only. No merge, no final dataset, no split, no baseline,
no training, no Qwen/API calls, and no HTML generation.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIRED_INPUTS = [
    "docs/phase1/external_qa_manual_reviewed_by_gpt55pro_analysis_v0_1.md",
    "docs/phase1/external_qa_manual_reviewed_by_gpt55pro_go_no_go_v0_1.md",
    "docs/phase1/external_qa_csv_summary_report_manual_reviewed_by_gpt55pro_v0_1.md",
    "docs/phase1/external_qa_csv_validation_report_manual_reviewed_by_gpt55pro_v0_1.md",
    "docs/phase1/external_source_integration_strategy_v0_1.md",
    "docs/phase1/metatool_single_service_adapter_report_v0_1.md",
    "docs/phase1/stabletoolbench_solvable_adapter_report_v0_1.md",
    "docs/phase1/metatool_single_service_review_plan_v0_1.md",
    "docs/phase1/stabletoolbench_solvable_review_plan_v0_1.md",
    "outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100_manual_reviewed_by_gpt55pro.csv",
    "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_manual_reviewed_by_gpt55pro.csv",
    "outputs/external_sources_adapters_v0_1/metatool/metatool_single_service_task_level_raw.csv",
    "outputs/external_sources_adapters_v0_1/metatool/metatool_plugin_service_catalog.csv",
    "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_solvable_task_level_raw.csv",
    "outputs/external_qa_v0_1/external_qa_manual_reviewed_by_gpt55pro_combined_summary_v0_1.json",
    "outputs/external_sources_adapters_v0_1/metatool/metatool_adapter_summary.json",
    "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_adapter_summary.json",
]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check_inputs(project_root: Path) -> list[str]:
    return [rel for rel in REQUIRED_INPUTS if not (project_root / rel).exists()]


def write_missing(project_root: Path, missing: list[str]) -> None:
    out = project_root / "outputs/external_source_policy_v0_2/MISSING_INPUTS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Missing Inputs For External Source Policy v0.2",
        "",
        f"Generated time: {now()}",
        "",
        "The run stopped because required inputs are missing. No fallback path was guessed.",
        "",
    ]
    lines.extend(f"- `{item}`" for item in missing)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_starting_status(project_root: Path) -> dict[str, Any]:
    combined = read_json(project_root / "outputs/external_qa_v0_1/external_qa_manual_reviewed_by_gpt55pro_combined_summary_v0_1.json")
    meta = combined["metatool"]
    stable = combined["stabletoolbench"]
    meta_adapter = read_json(project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_adapter_summary.json")
    stable_adapter = read_json(project_root / "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_adapter_summary.json")
    status = {
        "generated_time": now(),
        "metatool": {
            "raw_rows": meta_adapter.get("task_rows"),
            "service_catalog_size": meta_adapter.get("service_catalog_size"),
            "unmatched_gold_service_count": meta_adapter.get("unmatched_gold_service_count"),
            "adapter_service_leakage_risk_count": meta_adapter.get("service_leakage_risk_count"),
            "rows_reviewed": meta.get("rows"),
            "keep_uncertain_remove_critical": {
                "keep": meta.get("keep_count"),
                "uncertain": meta.get("uncertain_count"),
                "remove": meta.get("remove_count"),
                "critical": meta.get("critical_count"),
            },
            "leakage_distribution": meta.get("leakage_check_distribution"),
            "candidate_validity_distribution": meta.get("candidate_validity_distribution"),
            "service_catalog_check_distribution": meta.get("service_catalog_check_distribution"),
        },
        "stabletoolbench": {
            "raw_rows_total": stable_adapter.get("total_rows"),
            "rows_by_group": stable_adapter.get("rows_by_group"),
            "rows_reviewed": stable.get("rows"),
            "keep_uncertain_remove_critical": {
                "keep": stable.get("keep_count"),
                "uncertain": stable.get("uncertain_count"),
                "remove": stable.get("remove_count"),
                "critical": stable.get("critical_count"),
            },
            "leakage_distribution": stable.get("leakage_check_distribution"),
            "candidate_validity_distribution": stable.get("candidate_validity_distribution"),
            "task_type_check_distribution": stable.get("task_type_check_distribution"),
            "stable_group_distribution": stable.get("stable_group_distribution"),
        },
        "combined": {
            "rows": combined["combined"].get("rows"),
            "qa_final_decision_distribution": combined["combined"].get("qa_final_decision_distribution"),
            "qa_severity_distribution": combined["combined"].get("qa_severity_distribution"),
            "critical_count": combined["combined"].get("critical_count"),
        },
        "can_merge_external_sources_now": False,
        "can_generate_final_clean_dataset_now": False,
        "notes": [
            "These reviewed CSVs are policy-design inputs.",
            "They do not authorize final clean dataset generation.",
            "MetaTool and StableToolBench QA results must not be mixed with ToolBench-core QA pass/fail rates.",
        ],
    }
    write_json(project_root / "outputs/external_source_policy_v0_2/external_source_policy_starting_status.json", status)

    md = project_root / "docs/phase1/external_source_policy_starting_status_v0_2.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# External Source Policy Starting Status v0.2",
        "",
        f"Generated time: {status['generated_time']}",
        "",
        "## MetaTool",
        "",
        f"- rows reviewed = {meta.get('rows')}",
        f"- keep/uncertain/remove/critical = {status['metatool']['keep_uncertain_remove_critical']}",
        f"- leakage distribution = `{meta.get('leakage_check_distribution')}`",
        f"- candidate validity distribution = `{meta.get('candidate_validity_distribution')}`",
        f"- service catalog check distribution = `{meta.get('service_catalog_check_distribution')}`",
        "",
        "## StableToolBench",
        "",
        f"- rows reviewed = {stable.get('rows')}",
        f"- keep/uncertain/remove/critical = {status['stabletoolbench']['keep_uncertain_remove_critical']}",
        f"- leakage distribution = `{stable.get('leakage_check_distribution')}`",
        f"- candidate validity distribution = `{stable.get('candidate_validity_distribution')}`",
        f"- task type check distribution = `{stable.get('task_type_check_distribution')}`",
        f"- stable group distribution = `{stable.get('stable_group_distribution')}`",
        "",
        "## Combined",
        "",
        f"- combined rows = {combined['combined'].get('rows')}",
        f"- combined keep/uncertain/remove = `{combined['combined'].get('qa_final_decision_distribution')}`",
        f"- combined critical = {combined['combined'].get('critical_count')}",
        "",
        "## Boundary",
        "",
        "- These reviewed CSVs are policy-design inputs.",
        "- They do not authorize final clean dataset generation.",
        "- MetaTool and StableToolBench QA results must not be mixed with ToolBench-core QA pass/fail rates.",
        "- can_merge_external_sources_now = false",
        "- can_generate_final_clean_dataset_now = false",
    ]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status


def write_metatool_policy(project_root: Path) -> None:
    text = f"""# MetaTool Leakage / Rewrite Policy v0.2

Generated time: {now()}

Scope: `single_service_discovery_external` only. This policy is a dry-run source-specific policy and does not create clean final data.

## M1_exact_gold_service_name_leak_blocking

If the query explicitly contains the full gold service/plugin/tool name or a unique alias and the user is naming that service itself, mark as blocking. Blocking rows cannot enter the main service discovery clean set. They may enter `rewrite_pool`.

## M2_partial_or_common_word_overlap_not_automatic_blocking

If the service name contains common words such as `search`, `review`, `weather`, `translate`, `calculator`, `writer`, or `assistant`, common-word overlap alone is not a hard leak. Mark as `leak_uncertain` and require human or refined alias checking.

## M3_source_or_platform_name_leak

If the query explicitly asks to use, ask, retrieve from, according to, or get something from X, and X is the gold service/source/plugin name, mark `service_leak_blocking`. If the query only describes a need without naming the service, mark `no_obvious_leak`.

## M4_missing_context_for_standalone_query

If the query depends on absent context such as an above product, link, file, previous conversation, or document, the row is not standalone and should be `source_specific_remove` or `source_specific_uncertain`.

## M5_semantic_alignment_gate

The query must be supported by the gold service description. If the query asks about the website/service itself while the gold service performs a different function, mark `semantic_mismatch_blocking`.

## M6_candidate_catalog_validity

The 199-service catalog is structurally valid for this adapter, but each row still requires gold-in-candidate checking. Unmatched gold service must block the row.

## M7_rewrite_policy_boundary

This dry-run does not rewrite queries. It only marks `rewrite_needed` and writes `rewrite_candidate_pool`. Future rewrites must be re-reviewed after rewriting.

## Sampling Note

The MetaTool QA pack may oversample leakage-risk rows. Do not extrapolate `94/100 leak_uncertain` directly to all 20,614 raw rows.

## Source Role

MetaTool remains a candidate source for `single_service_discovery_external`. It does not provide an API-level benchmark unless a future API/interface mapping is built and reviewed.
"""
    (project_root / "docs/phase1/metatool_leakage_rewrite_policy_v0_2.md").write_text(text, encoding="utf-8")


def write_stable_policy(project_root: Path) -> None:
    text = f"""# StableToolBench Filtering / Reconstruction Policy v0.2

Generated time: {now()}

Scope: StableToolBench solvable raw rows. This policy is a dry-run source-specific filter and reconstruction planner, not a final clean benchmark builder.

## S1_candidate_space_invalid_gate

If candidate APIs equal gold APIs, or there are no plausible negative distractors, the row is not a valid recommendation benchmark row as-is. Send it to `candidate_space_reconstruction_pool`.

## S2_service_or_api_leak_gate

If the query directly names gold service/API names, block from the main clean set. Hard API leak should be removed or sent to rewrite pool. Service leak may enter rewrite pool but not clean data as-is.

## S3_demo_test_unsupported_source_gate

Demo Project, FastAPI Project, generic test/demo/sample/healthcheck/order catalog sources must not enter clean benchmark. Mark remove.

## S4_missing_core_requirement_gate

If the query requires capabilities beyond the gold APIs, or gold documentation is insufficient to prove coverage, mark uncertain/remove.

## S5_g3_composable_dependency_gate

G3 is not automatically composable. A row needs an explicit dependency chain where a later step depends on a previous output/entity/judgment. Parallel tasks are `composable_not_strong_dependency`.

## S6_task_type_guess_gate

G1/G2/G3 are source groups, not final task labels. `task_type_guess` must be validated by rules or human review.

## S7_candidate_space_reconstruction_boundary

This round does not build the final reconstructed candidate set. It only outputs reconstruction pools and plans. Future reconstruction must ensure gold APIs are included, distractors are reasonable, no API leak is introduced, and reconstructed rows are re-reviewed.

## Source Role

StableToolBench is useful as stable/solvable support material, but raw solvable rows are not naturally valid service/API recommendation rows. Current reviewed100 failed because of critical rows, many removes, candidate-space invalid cases, and weak composable validity.
"""
    (project_root / "docs/phase1/stabletoolbench_filter_reconstruction_policy_v0_2.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare external source policy v0.2 status and specs.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    missing = check_inputs(project_root)
    if missing:
        write_missing(project_root, missing)
        raise SystemExit("Missing required inputs. See outputs/external_source_policy_v0_2/MISSING_INPUTS.md")
    status = write_starting_status(project_root)
    write_metatool_policy(project_root)
    write_stable_policy(project_root)
    print(json.dumps({"starting_status_generated": True, "metatool_policy_generated": True, "stable_policy_generated": True, "combined_rows": status["combined"]["rows"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
