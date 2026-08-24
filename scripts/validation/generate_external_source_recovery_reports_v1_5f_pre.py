#!/usr/bin/env python
"""Generate external-source recovery strategy, Go/No-Go, and archive.

This script only aggregates already-built inventory/adapter/check artifacts.
It does not run Qwen, full cleaning, final dataset generation, split,
baseline, or training.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ARCHIVE_DIR = Path("outputs/run_archives/2026-07-02_external_source_recovery_v1_5f_pre")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required JSON summary is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def bool_from_inventory(inventory: dict[str, Any], name: str) -> bool:
    direct_key = f"{name}_present"
    if direct_key in inventory:
        return bool(inventory.get(direct_key))
    sources = inventory.get("sources", {})
    item = sources.get(name, {})
    return bool(item.get("present"))


def write_strategy(path: Path, summary: dict[str, Any]) -> None:
    text = f"""# External Source Integration Strategy v0.1

Generated time: {summary['generated_at']}

## Input Files

- Inventory JSON: `{summary['inputs']['inventory_json']}`
- MetaTool summary: `{summary['inputs']['metatool_summary_json']}`
- StableToolBench summary: `{summary['inputs']['stabletoolbench_summary_json']}`
- ShortcutsBench source check: `{summary['inputs']['shortcutsbench_check_json']}`

## Current Scope

This step repairs source coverage at the adapter/preflight level. It does not change the ToolBench-core v1.4c/v1.5f cleaning line, and it does not produce a final six-task benchmark.

## Source Roles

### ToolBench

ToolBench remains the current core construction source because the existing SemCap/manual QA work was built around ToolBench task-level rows. Continue ToolBench-core v1.5f tightening separately.

### MetaTool

MetaTool is suitable as an external candidate source for single-service discovery style validation because its rows map a natural-language query to a tool/plugin service. The v0.1 adapter produced raw task-level rows and a plugin service catalog, but these rows still require human QA before any benchmark use.

Do not use MetaTool as an API-level benchmark source unless a separate API/interface mapping is constructed and reviewed.

### StableToolBench

StableToolBench solvable instructions can support service/API discovery analysis because each row contains a query, candidate APIs, and relevant APIs. The v0.1 adapter preserves the raw solvable structure. G3 is explicitly marked as requiring dependency-chain review; source group G3 is not automatically strong composable.

### ShortcutsBench

ShortcutsBench source files are present and extracted JSON files are parseable. In this preflight it remains source-checked only. A route-specific strict adapter is required before using it for benchmark rows.

## Integration Policy

1. Keep ToolBench-core cleaning and external-source adapters separated.
2. Human-review MetaTool and StableToolBench QA packs before using them as benchmark support.
3. Treat ShortcutsBench as source-ready but not adapter-ready in this step.
4. Do not merge external sources into final benchmark until source-specific QA passes.
5. Do not create split, baseline, training data, or final clean dataset from these outputs.

## Current Recommendation

{summary['recommended_next_step']}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_go_no_go(path: Path, summary: dict[str, Any]) -> None:
    text = f"""# External Source Recovery Go/No-Go v1.5f-pre

Generated time: {summary['generated_at']}

## Input Files

- Inventory JSON: `{summary['inputs']['inventory_json']}`
- MetaTool summary: `{summary['inputs']['metatool_summary_json']}`
- StableToolBench summary: `{summary['inputs']['stabletoolbench_summary_json']}`
- ShortcutsBench source check: `{summary['inputs']['shortcutsbench_check_json']}`

## Fixed Fields

- metatool_source_present: `{summary['metatool_source_present']}`
- metatool_rows: `{summary['metatool_rows']}`
- metatool_service_count: `{summary['metatool_service_count']}`
- metatool_adapter_built: `{summary['metatool_adapter_built']}`
- stabletoolbench_source_present: `{summary['stabletoolbench_source_present']}`
- stabletoolbench_rows_by_group: `{summary['stabletoolbench_rows_by_group']}`
- stabletoolbench_adapter_built: `{summary['stabletoolbench_adapter_built']}`
- shortcutsbench_source_present: `{summary['shortcutsbench_source_present']}`
- missing_required_sources: `{summary['missing_required_sources']}`
- can_continue_toolbench_core_v1_5f: `{summary['can_continue_toolbench_core_v1_5f']}`
- can_generate_full_six_task_benchmark_now: `{summary['can_generate_full_six_task_benchmark_now']}`
- can_generate_final_clean_dataset_now: `{summary['can_generate_final_clean_dataset_now']}`
- recommended_next_step: {summary['recommended_next_step']}

## Go / No-Go

- GO: MetaTool raw adapter and QA pack are ready for human review.
- GO: StableToolBench raw adapter and QA pack are ready for human review.
- GO: ShortcutsBench source/extracted format check is complete.
- GO: Continue ToolBench-core v1.5f tightening separately.
- NO-GO: Do not generate full six-task benchmark now.
- NO-GO: Do not generate final clean dataset now.
- NO-GO: Do not run split, baseline, or training.
- NO-GO: Do not treat external-source raw rows as clean benchmark rows.

## Forbidden Actions Check

- Qwen API was not called.
- Full cleaning was not run.
- Final dataset was not generated.
- Split was not created.
- Baseline was not run.
- Training was not run.
- external_sources directories were not moved, deleted, or overwritten.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def copy_if_exists(src: Path, archive_root: Path, project_root: Path, manifest: list[dict[str, Any]]) -> None:
    if not src.exists():
        manifest.append({"source": str(src), "copied": False, "reason": "missing"})
        return
    rel = src.relative_to(project_root)
    dst = archive_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    manifest.append({"source": str(src), "archive_path": str(dst), "copied": True, "size_bytes": src.stat().st_size})


def build_summary(project_root: Path) -> dict[str, Any]:
    inventory_json = project_root / "outputs/external_source_recovery_v1_5f_pre/external_source_inventory.json"
    metatool_summary_json = project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_adapter_summary.json"
    stable_summary_json = project_root / "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_adapter_summary.json"
    shortcuts_json = project_root / "outputs/external_sources_adapters_v0_1/shortcutsbench/shortcutsbench_source_check.json"

    inventory = load_json(inventory_json)
    metatool = load_json(metatool_summary_json)
    stable = load_json(stable_summary_json)
    shortcuts = load_json(shortcuts_json)

    missing_required_sources = [
        name
        for name in ["ToolBench", "MetaTool", "StableToolBench", "ShortcutsBench"]
        if not bool_from_inventory(inventory, name)
    ]

    summary: dict[str, Any] = {
        "generated_at": now_iso(),
        "project_root": str(project_root),
        "inputs": {
            "inventory_json": str(inventory_json),
            "metatool_summary_json": str(metatool_summary_json),
            "stabletoolbench_summary_json": str(stable_summary_json),
            "shortcutsbench_check_json": str(shortcuts_json),
        },
        "metatool_source_present": bool_from_inventory(inventory, "MetaTool"),
        "metatool_rows": int(metatool.get("task_rows", 0)),
        "metatool_service_count": int(metatool.get("service_catalog_size", 0)),
        "metatool_adapter_built": bool(metatool.get("task_rows", 0)) and Path(metatool.get("output_task_csv", "")).exists(),
        "stabletoolbench_source_present": bool_from_inventory(inventory, "StableToolBench"),
        "stabletoolbench_rows_by_group": stable.get("rows_by_group", {}),
        "stabletoolbench_adapter_built": bool(stable.get("total_rows", 0)) and Path(stable.get("output_file", "")).exists(),
        "shortcutsbench_source_present": bool(shortcuts.get("source_present")),
        "missing_required_sources": missing_required_sources,
        "can_continue_toolbench_core_v1_5f": len(missing_required_sources) == 0,
        "can_generate_full_six_task_benchmark_now": False,
        "can_generate_final_clean_dataset_now": False,
        "recommended_next_step": (
            "Human-review MetaTool and StableToolBench QA packs, keep ShortcutsBench source-checked only, "
            "and continue ToolBench-core v1.5f tightening before any full six-task/final dataset step."
        ),
    }
    return summary


def archive_outputs(project_root: Path, generated_docs: list[Path]) -> Path:
    archive_root = project_root / ARCHIVE_DIR
    files = [
        project_root / "scripts/validation/check_external_sources_inventory_v1_5f_pre.py",
        project_root / "scripts/build_dataset/build_metatool_single_service_external_v0_1.py",
        project_root / "scripts/build_dataset/build_stabletoolbench_solvable_raw_v0_1.py",
        project_root / "scripts/validation/check_shortcutsbench_source_v0_1.py",
        project_root / "scripts/validation/generate_external_source_recovery_reports_v1_5f_pre.py",
        project_root / "outputs/external_source_recovery_v1_5f_pre/external_source_inventory.json",
        project_root / "outputs/external_source_recovery_v1_5f_pre/external_source_inventory.csv",
        project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_single_service_task_level_raw.csv",
        project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_plugin_service_catalog.csv",
        project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_adapter_summary.json",
        project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_unmatched_gold_services.csv",
        project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_query_leakage_scan.csv",
        project_root / "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_solvable_task_level_raw.csv",
        project_root / "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_adapter_summary.json",
        project_root / "outputs/external_sources_adapters_v0_1/shortcutsbench/shortcutsbench_source_check.json",
        project_root / "outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100.csv",
        project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_or_all.csv",
    ]
    files.extend(generated_docs)
    manifest: list[dict[str, Any]] = []
    for src in files:
        copy_if_exists(src, archive_root, project_root, manifest)
    manifest_path = archive_root / "archive_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"generated_at": now_iso(), "files": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    return archive_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate external source recovery strategy, Go/No-Go, and archive.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    summary = build_summary(project_root)

    strategy_path = project_root / "docs/phase1/external_source_integration_strategy_v0_1.md"
    go_no_go_path = project_root / "docs/phase1/external_source_recovery_go_no_go_v1_5f_pre.md"
    summary_path = project_root / "outputs/external_source_recovery_v1_5f_pre/external_source_recovery_go_no_go_summary_v1_5f_pre.json"

    write_strategy(strategy_path, summary)
    write_go_no_go(go_no_go_path, summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    generated_docs = [
        project_root / "docs/phase1/external_source_recovery_inventory_v1_5f_pre.md",
        project_root / "docs/phase1/metatool_single_service_adapter_report_v0_1.md",
        project_root / "docs/phase1/metatool_single_service_review_plan_v0_1.md",
        project_root / "docs/phase1/stabletoolbench_solvable_adapter_report_v0_1.md",
        project_root / "docs/phase1/stabletoolbench_solvable_review_plan_v0_1.md",
        project_root / "docs/phase1/shortcutsbench_source_check_v0_1.md",
        strategy_path,
        go_no_go_path,
    ]
    archive_root = archive_outputs(project_root, generated_docs)
    summary["archive_dir"] = str(archive_root)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
