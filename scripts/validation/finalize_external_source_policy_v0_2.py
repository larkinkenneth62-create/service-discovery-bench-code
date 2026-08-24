#!/usr/bin/env python
"""Finalize external source-specific policy dry-run v0.2 reports and archive."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ARCHIVE_DIR = Path("outputs/run_archives/2026-07-05_external_source_specific_policy_dryrun_v0_2")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_go_no_go(project_root: Path, meta_summary: dict[str, Any], stable_summary: dict[str, Any]) -> dict[str, Any]:
    meta_reg = meta_summary["regression"]
    stable_reg = stable_summary["regression"]
    can_meta = bool(meta_reg.get("passes_acceptance"))
    can_stable = bool(stable_reg.get("passes_acceptance"))
    recommended_parts = []
    if can_meta:
        recommended_parts.append("human review metatool v0.2 leakage policy QA pack")
    else:
        recommended_parts.append("revise MetaTool leakage policy before more QA")
    if can_stable:
        recommended_parts.append("human review stabletoolbench v0.2 filter policy QA pack")
    else:
        recommended_parts.append("revise StableToolBench filter policy before more QA")
    recommended = "; ".join(recommended_parts) + "; do not merge external sources yet"
    report = {
        "generated_time": now(),
        "can_accept_metatool_leakage_policy_dryrun": can_meta,
        "can_accept_stabletoolbench_filter_policy_dryrun": can_stable,
        "can_merge_metatool_now": False,
        "can_merge_stabletoolbench_now": False,
        "can_merge_external_sources_now": False,
        "can_generate_full_six_task_benchmark_now": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "review_mode": "csv_only",
        "html_review_app_generated": False,
        "recommended_next_step": recommended,
        "metatool_regression": meta_reg,
        "stabletoolbench_regression": stable_reg,
    }
    path = project_root / "docs/phase1/external_source_specific_policy_go_no_go_v0_2.md"
    lines = [
        "# External Source-Specific Policy Go/No-Go v0.2",
        "",
        f"Generated time: {report['generated_time']}",
        "",
        "## Fixed Fields",
        "",
    ]
    for key in [
        "can_accept_metatool_leakage_policy_dryrun",
        "can_accept_stabletoolbench_filter_policy_dryrun",
        "can_merge_metatool_now",
        "can_merge_stabletoolbench_now",
        "can_merge_external_sources_now",
        "can_generate_full_six_task_benchmark_now",
        "can_generate_final_clean_dataset_now",
        "can_create_split_now",
        "can_run_baseline_now",
        "can_train_model_now",
        "review_mode",
        "html_review_app_generated",
    ]:
        lines.append(f"- {key}: `{report[key]}`")
    lines.extend(
        [
            "",
            "## MetaTool Acceptance",
            "",
            f"- service_leak_blocking_capture_rate: {meta_reg.get('service_leak_blocking_capture_rate')}",
            f"- remove_capture_rate: {meta_reg.get('remove_capture_rate')}",
            f"- keep_retention_rate: {meta_reg.get('keep_retention_rate')}",
            f"- no_hardcoded_id_rules_used: `{meta_reg.get('no_hardcoded_id_rules_used')}`",
            f"- passes_acceptance: `{meta_reg.get('passes_acceptance')}`",
            "",
            "## StableToolBench Acceptance",
            "",
            f"- critical_capture_rate: {stable_reg.get('critical_capture_rate')}",
            f"- remove_capture_rate: {stable_reg.get('remove_capture_rate')}",
            f"- candidate_space_invalid_capture_rate: {stable_reg.get('candidate_space_invalid_capture_rate')}",
            f"- composable_not_strong_dependency_capture_rate: {stable_reg.get('composable_not_strong_dependency_capture_rate')}",
            f"- no_hardcoded_id_rules_used: `{stable_reg.get('no_hardcoded_id_rules_used')}`",
            f"- passes_acceptance: `{stable_reg.get('passes_acceptance')}`",
            "",
            "## Boundary",
            "",
            "- Even when dry-run policies pass, external sources cannot be merged yet.",
            "- No final clean dataset, split, baseline, training, Qwen call, external API call, or HTML app was produced.",
            "",
            "## Recommended Next Step",
            "",
            recommended,
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(project_root / "outputs/external_source_policy_v0_2/external_source_specific_policy_go_no_go_summary_v0_2.json", report)
    return report


def archive(project_root: Path) -> Path:
    rels = [
        "docs/phase1/external_source_policy_starting_status_v0_2.md",
        "outputs/external_source_policy_v0_2/external_source_policy_starting_status.json",
        "docs/phase1/metatool_leakage_rewrite_policy_v0_2.md",
        "outputs/external_source_policy_v0_2/metatool/metatool_single_service_with_leakage_policy_v0_2.csv",
        "outputs/external_source_policy_v0_2/metatool/metatool_leakage_policy_summary_v0_2.json",
        "outputs/external_source_policy_v0_2/metatool/metatool_rewrite_candidate_pool_v0_2.csv",
        "outputs/external_source_policy_v0_2/metatool/metatool_leakage_policy_rule_hit_counts_v0_2.csv",
        "outputs/external_source_policy_v0_2/metatool/metatool_reviewed100_policy_regression_trace_v0_2.csv",
        "outputs/external_source_policy_v0_2/metatool/metatool_reviewed100_policy_regression_summary_v0_2.json",
        "docs/phase1/metatool_leakage_policy_dryrun_report_v0_2.md",
        "docs/phase1/metatool_leakage_policy_review_plan_v0_2.md",
        "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv",
        "docs/phase1/stabletoolbench_filter_reconstruction_policy_v0_2.md",
        "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_solvable_with_filter_policy_v0_2.csv",
        "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_filter_policy_summary_v0_2.json",
        "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_candidate_space_reconstruction_pool_v0_2.csv",
        "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_leakage_rewrite_pool_v0_2.csv",
        "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_composable_dependency_review_pool_v0_2.csv",
        "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_policy_rule_hit_counts_v0_2.csv",
        "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_reviewed100_policy_regression_trace_v0_2.csv",
        "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_reviewed100_policy_regression_summary_v0_2.json",
        "docs/phase1/stabletoolbench_filter_policy_dryrun_report_v0_2.md",
        "docs/phase1/stabletoolbench_filter_policy_review_plan_v0_2.md",
        "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv",
        "docs/phase1/external_source_specific_policy_go_no_go_v0_2.md",
        "outputs/external_source_policy_v0_2/external_source_specific_policy_go_no_go_summary_v0_2.json",
        "scripts/validation/prepare_external_source_policy_v0_2.py",
        "scripts/validation/run_metatool_leakage_policy_dryrun_v0_2.py",
        "scripts/validation/run_stabletoolbench_filter_policy_dryrun_v0_2.py",
        "scripts/validation/finalize_external_source_policy_v0_2.py",
    ]
    archive_root = project_root / ARCHIVE_DIR
    manifest = []
    for rel in rels:
        src = project_root / rel
        if not src.exists():
            manifest.append({"source": rel, "copied": False, "reason": "missing"})
            continue
        dst = archive_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        manifest.append({"source": rel, "archive_path": str(dst), "copied": True, "size_bytes": src.stat().st_size})
    write_json(archive_root / "archive_manifest.json", {"generated_time": now(), "files": manifest})
    return archive_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize external source policy v0.2.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    meta = read_json(project_root / "outputs/external_source_policy_v0_2/metatool/metatool_leakage_policy_summary_v0_2.json")
    stable = read_json(project_root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_filter_policy_summary_v0_2.json")
    go = write_go_no_go(project_root, meta, stable)
    archive_root = archive(project_root)
    final = {
        "metatool_policy_dryrun_completed": True,
        "metatool_total_rows": meta.get("total_rows"),
        "metatool_source_specific_keep_candidate_count": meta.get("decision_counts", {}).get("source_specific_keep_candidate", 0),
        "metatool_source_specific_uncertain_count": meta.get("decision_counts", {}).get("source_specific_uncertain", 0),
        "metatool_source_specific_remove_count": meta.get("decision_counts", {}).get("source_specific_remove", 0),
        "metatool_rewrite_pool_count": meta.get("rewrite_pool_count"),
        "metatool_service_leak_blocking_capture_rate": meta.get("regression", {}).get("service_leak_blocking_capture_rate"),
        "metatool_remove_capture_rate": meta.get("regression", {}).get("remove_capture_rate"),
        "metatool_keep_retention_rate": meta.get("regression", {}).get("keep_retention_rate"),
        "can_accept_metatool_leakage_policy_dryrun": go["can_accept_metatool_leakage_policy_dryrun"],
        "stabletoolbench_policy_dryrun_completed": True,
        "stabletoolbench_total_rows": stable.get("total_rows"),
        "stabletoolbench_keep_candidate_as_is_count": stable.get("decision_counts", {}).get("source_specific_keep_candidate_as_is", 0),
        "stabletoolbench_candidate_space_reconstruction_pool_count": stable.get("candidate_space_reconstruction_pool_count"),
        "stabletoolbench_leakage_rewrite_pool_count": stable.get("leakage_rewrite_pool_count"),
        "stabletoolbench_composable_dependency_review_pool_count": stable.get("composable_dependency_review_pool_count"),
        "stabletoolbench_source_specific_remove_count": stable.get("decision_counts", {}).get("source_specific_remove", 0),
        "stabletoolbench_critical_capture_rate": stable.get("regression", {}).get("critical_capture_rate"),
        "stabletoolbench_remove_capture_rate": stable.get("regression", {}).get("remove_capture_rate"),
        "stabletoolbench_candidate_space_invalid_capture_rate": stable.get("regression", {}).get("candidate_space_invalid_capture_rate"),
        "stabletoolbench_composable_not_strong_dependency_capture_rate": stable.get("regression", {}).get("composable_not_strong_dependency_capture_rate"),
        "can_accept_stabletoolbench_filter_policy_dryrun": go["can_accept_stabletoolbench_filter_policy_dryrun"],
        "can_merge_external_sources_now": False,
        "can_generate_final_clean_dataset_now": False,
        "review_mode": "csv_only",
        "html_review_app_generated": False,
        "recommended_next_step": go["recommended_next_step"],
        "archive_dir": str(archive_root),
    }
    print(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
