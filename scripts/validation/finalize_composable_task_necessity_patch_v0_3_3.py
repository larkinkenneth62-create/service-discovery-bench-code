#!/usr/bin/env python3
"""Finalize v0.3.3 UI evidence, status reports, and the immutable run archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HUMAN_FIELDS = [
    "dependency_required_for_query",
    "upstream_already_satisfies_subgoal",
    "full_query_subgoals_covered_by_gold_chain",
    "disconnected_parallel_subgoals_present",
    "cross_service_dependency_valid",
    "dependency_edge_valid",
    "dependency_evidence_sufficient",
    "composition_final_label",
    "query_gold_chain_alignment",
    "service_gold_complete",
    "service_candidate_space_valid",
    "service_leakage_final",
    "service_level_eligible",
    "api_gold_complete",
    "api_candidate_space_valid",
    "api_parent_mapping_valid",
    "api_leakage_final",
    "api_level_eligible",
    "composable_release_action",
    "adjudicator_id",
    "adjudicator_type",
    "adjudicated_at",
    "adjudication_notes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize v0.3.3 task-necessity review-pack evidence and archive."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path(
            "outputs/run_archives/2026-07-15_composable_task_necessity_patch_v0_3_3"
        ),
    )
    return parser.parse_args()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required JSON does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required CSV does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader), list(reader.fieldnames)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    patch_dir = root / "outputs/composable_task_necessity_patch_v0_3_3"
    pack_dir = root / "outputs/composable_paired_task_preparation_v0_3_3"
    docs_dir = root / "docs/phase1"
    archive_dir = (root / args.archive_dir).resolve() if not args.archive_dir.is_absolute() else args.archive_dir.resolve()

    summary_path = patch_dir / "composable_task_necessity_patch_summary_v0_3_3.json"
    hash_path = patch_dir / "before_after_sha256_v0_3_3.json"
    review_csv = pack_dir / "composable_paired_task_review_items_v0_3_3.csv"
    html_path = pack_dir / "composable_paired_task_review_app_v0_3_3.html"
    html_manifest_path = pack_dir / "composable_paired_task_review_app_v0_3_3_manifest.json"
    translation_path = pack_dir / "composable_query_translations_zh_v0_3_3.json"
    static_path = pack_dir / "composable_review_app_static_validation_v0_3_3.json"
    edge_path = pack_dir / "browser_validation/composable_review_app_edge_validation_v0_3_3.json"

    summary = load_json(summary_path)
    hashes = load_json(hash_path)
    manifest = load_json(html_manifest_path)
    translations = load_json(translation_path)
    static = load_json(static_path)
    edge = load_json(edge_path)
    rows, columns = read_csv_rows(review_csv)

    require(len(rows) == 97, f"Expected 97 review rows, got {len(rows)}")
    require(len(columns) == 141, f"Expected 141 review columns, got {len(columns)}")
    require(all(field in columns for field in HUMAN_FIELDS), "Review CSV lacks required human fields")
    autofilled = sum(
        1 for row in rows if any(str(row.get(field, "")).strip() for field in HUMAN_FIELDS)
    )
    require(autofilled == 0, f"Human fields were autofilled in {autofilled} rows")
    require(len(translations) == 97, f"Expected 97 query translations, got {len(translations)}")
    require(manifest.get("input_rows") == 97, "HTML manifest row count is not 97")
    require(manifest.get("input_columns") == 141, "HTML manifest column count is not 141")
    require(manifest.get("query_translation_count") == 97, "HTML translation count is not 97")
    require(static.get("required_static_features") == "ok", "Static HTML validation did not pass")
    require(static.get("embedded_rows") == 97, "Static HTML row count is not 97")
    require(static.get("columns") == 141, "Static HTML column count is not 141")
    require(static.get("human_fields") == 23, "Static HTML human-field count is not 23")
    require(edge.get("passed") is True, "Real Edge validation did not pass")
    require(edge.get("rows") == 97, "Edge validation row count is not 97")
    require(edge.get("csv_export", {}).get("columnCount") == 141, "Edge export columns are not 141")
    require(edge.get("downloaded_csv_rows") == 97, "Edge downloaded CSV rows are not 97")

    old_review = root / "outputs/composable_paired_task_preparation_v0_3_2/composable_paired_task_review_items_v0_3_2.csv"
    old_master = root / "outputs/composable_paired_task_preparation_v0_3_2/composable_underlying_tasks_master_v0_3_2.csv"
    extractor = root / "scripts/validation/composable_dependency_extractor_v0_3_2.py"
    require(sha256(old_review) == hashes["before"]["v0_3_2_review_pack"], "v0.3.2 review pack changed")
    require(sha256(old_master) == hashes["before"]["v0_3_2_master"], "v0.3.2 master changed")
    require(sha256(extractor) == hashes["before"]["dependency_extractor_v0_3_2"], "v0.3.2 extractor changed")
    require(sha256(review_csv) == hashes["after"]["v0_3_3_review_pack"], "v0.3.3 review CSV changed after construction")

    generated_at = now()
    master_plan = root / "docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md"
    hashes["finalized_at"] = generated_at
    hashes["after"]["master_plan"] = sha256(master_plan)
    write_json(hash_path, hashes)
    summary.update(
        {
            "finalized_at": generated_at,
            "review_app_generated": True,
            "review_app_static_validation_passed": True,
            "review_app_edge_validation_passed": True,
            "review_app_html": str(html_path),
            "review_app_html_sha256": sha256(html_path),
            "review_app_html_bytes": html_path.stat().st_size,
            "review_app_query_translation_count": 97,
            "review_app_human_field_count": 23,
            "review_app_export_rows": 97,
            "review_app_export_columns": 141,
            "review_app_dropdown_count": 0,
            "review_app_quick_preset_count": 8,
            "review_app_desktop_layout_passed": True,
            "review_app_compact_layout_passed": True,
            "human_fields_autofilled_count": autofilled,
            "can_resume_composable_human_review": False,
            "recommended_next_step": "report structural candidate shortage; do not relax paired-composable rules or resume human review.",
        }
    )
    write_json(summary_path, summary)

    fixed_keys = [
        "v0_3_2_input_rows",
        "gold_service_count_lt_2_count",
        "same_service_only_dependency_count",
        "no_cross_service_dependency_count",
        "failed_dependency_count",
        "exact_service_leak_count",
        "exact_api_leak_count",
        "redundant_recomputation_risk_count",
        "parallel_subgoal_risk_count",
        "retained_old_rows",
        "replacement_rows",
        "final_v0_3_3_rows",
        "final_unique_tasks",
        "final_cross_service_dependency_valid_count",
        "final_service_candidate_valid_count",
        "final_api_candidate_valid_count",
        "human_fields_autofilled_count",
    ]
    fixed_lines = "\n".join(f"- {key} = `{summary[key]}`" for key in fixed_keys)
    go_no_go_path = docs_dir / "composable_task_necessity_patch_go_no_go_v0_3_3.md"
    go_no_go_path.write_text(
        f"""# Composable Task Necessity Patch Go / No-Go v0.3.3

Generated at: `{generated_at}`

Machine rule spec: `v1.0`

## Fixed fields

{fixed_lines}

## UI verification

- bilingual query translations: `97 / 97`
- embedded rows / columns: `97 / 141`
- human fields: `23`, all blank in the source CSV
- static JavaScript/data validation: `passed`
- real Edge desktop and 900px compact validation: `passed`
- quick presets / auto-next / localStorage / import / export: `passed`
- exported CSV: `97 rows x 141 columns`
- dropdown controls: `0`

## Decision

- can_resume_composable_human_review = `false`
- reason = `97 structurally eligible tasks are below the hard minimum of 100`
- can_claim_composable_service_benchmark_now = `false`
- can_claim_composable_api_benchmark_now = `false`
- can_start_full_six_task_assembly = `false`
- can_generate_final_dataset = `false`
- recommended_next_step = `{summary['recommended_next_step']}`

The generated HTML is an inspection artifact, not authorization to resume review. Do not relax the cross-service, leak, failed-call, candidate-space, or task-necessity rules to fill the missing three tasks.
""",
        encoding="utf-8",
    )

    report_path = docs_dir / "composable_task_necessity_patch_report_v0_3_3.md"
    report_path.write_text(
        f"""# Composable Task Necessity Patch v0.3.3 Report

Generated at: `{generated_at}`

## Inputs

- old review: `{old_review}`
- old master: `{old_master}`
- corrected ranked reserve: `{root / 'outputs/composable_dependency_extractor_patch_v0_3_2/corrected_underlying_task_candidates_ranked.csv'}`
- corrected dependency edges: `{root / 'outputs/composable_dependency_extractor_patch_v0_3_2/corrected_dependency_edge_candidates.jsonl'}`
- frozen rules: `{root / 'docs/project/SERVICEDISCOVERYBENCH_COMPOSABLE_MACHINE_REVIEW_RULES.md'}`
- v0.3.3 review CSV: `{review_csv}`
- UI manifest: `{html_manifest_path}`
- static validation: `{static_path}`
- real Edge validation: `{edge_path}`

## Scope and integrity

The frozen v1.0 machine rules were applied to the existing v0.3.2 pack and corrected strong reserve. This run did not rescan raw ToolBench directories, rerun corpus mining, rewrite the dependency extractor, call Qwen or any external API, fill human fields, freeze sources, assemble six tasks, generate a final dataset, split data, run baselines, or train a model. Hash checks confirm that the v0.3.2 review/master files and extractor remain unchanged.

## v0.3.2 re-audit

- input rows: `{summary['v0_3_2_input_rows']}`
- gold service count < 2: `{summary['gold_service_count_lt_2_count']}`
- same-service-only / no cross-service dependency: `{summary['same_service_only_dependency_count']}` / `{summary['no_cross_service_dependency_count']}`
- failed dependency: `{summary['failed_dependency_count']}`
- exact service/API leak: `{summary['exact_service_leak_count']}` / `{summary['exact_api_leak_count']}`
- redundancy/parallel risks: `{summary['redundant_recomputation_risk_count']}` / `{summary['parallel_subgoal_risk_count']}`

## v0.3.3 pack

- retained old rows: `{summary['retained_old_rows']}`
- replacement rows: `{summary['replacement_rows']}`
- final rows / unique tasks: `{summary['final_v0_3_3_rows']}` / `{summary['final_unique_tasks']}`
- cross-service valid: `{summary['final_cross_service_dependency_valid_count']}`
- service/API candidate-space valid: `{summary['final_service_candidate_valid_count']}` / `{summary['final_api_candidate_valid_count']}`
- human fields autofilled: `{summary['human_fields_autofilled_count']}`

## Review UI

The v0.3.3 single-file HTML embeds all 97 rows, direct Chinese query translations, Service/API hierarchy, machine-only necessity evidence, five blank human necessity fields, eight click presets, search/filter, auto-next, localStorage, CSV import, and UTF-8 CSV export. It explicitly states that a valid trace edge is insufficient and that shared input, retry, redundant recomputation, and parallel subgoals are not true composition. Real Edge regression passed at 1600x1000 and 900x1200 with no horizontal overflow.

## Boundary and decision

Machine statuses are routing evidence, not `true_composable` labels. The hard Go condition requires at least 100 structurally eligible rows; only 97 were found. Therefore `can_resume_composable_human_review=false`. The three-row shortage must be reported rather than filled with same-service-only, leaked, failed-call, redundant, or otherwise ineligible records.
""",
        encoding="utf-8",
    )

    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_sources = [
        root / "docs/project/SERVICEDISCOVERYBENCH_COMPOSABLE_MACHINE_REVIEW_RULES.md",
        master_plan,
        go_no_go_path,
        report_path,
        summary_path,
        hash_path,
        review_csv,
        pack_dir / "composable_underlying_tasks_master_v0_3_3.csv",
        pack_dir / "composable_service_discovery_provisional_rows_v0_3_3.csv",
        pack_dir / "composable_api_recommendation_provisional_rows_v0_3_3.csv",
        pack_dir / "composable_double_annotation_subset_40_v0_3_3.csv",
        pack_dir / "v0_3_2_to_v0_3_3_review_migration_manifest.csv",
        html_path,
        html_manifest_path,
        translation_path,
        pack_dir / "composable_query_translations_new_zh_v0_3_3.json",
        static_path,
        edge_path,
        pack_dir / "browser_validation/composable_review_app_v0_3_3_desktop_1600x1000.png",
        pack_dir / "browser_validation/composable_review_app_v0_3_3_compact_900x1200.png",
        root / "scripts/validation/composable_task_necessity_gate_v0_3_3.py",
        root / "scripts/validation/run_composable_task_necessity_patch_v0_3_3.py",
        root / "scripts/validation/build_composable_query_translations_v0_3_3.py",
        root / "scripts/validation/build_composable_review_app_v0_3_3.py",
        root / "scripts/validation/check_composable_review_app_v0_3_3.cjs",
        root / "scripts/validation/validate_composable_review_app_edge_v0_3_3.py",
        root / "scripts/validation/finalize_composable_task_necessity_patch_v0_3_3.py",
        root / "tests/validation/test_composable_task_necessity_gate_v0_3_3.py",
    ]
    for source in archive_sources:
        require(source.exists(), f"Archive source is missing: {source}")
        shutil.copy2(source, archive_dir / source.name)

    manifest_path = archive_dir / "archive_manifest_v0_3_3.json"
    archived = sorted(path for path in archive_dir.iterdir() if path.is_file() and path != manifest_path)
    archive_manifest = {
        "generated_at": generated_at,
        "archive_dir": str(archive_dir),
        "file_count": len(archived),
        "constraints": {
            "raw_trace_rescanned": False,
            "corpus_mining_run": False,
            "dependency_extractor_rewritten": False,
            "human_fields_autofilled": False,
            "source_frozen": False,
            "six_task_assembly_run": False,
            "final_dataset_generated": False,
            "split_created": False,
            "baseline_run": False,
            "model_trained": False,
            "qwen_used": False,
            "external_api_used": False,
            "can_resume_composable_human_review": False,
        },
        "files": [
            {"filename": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in archived
        ],
    }
    write_json(manifest_path, archive_manifest)

    for key in fixed_keys:
        print(f"{key}={summary[key]}")
    print(f"can_resume_composable_human_review={str(summary['can_resume_composable_human_review']).lower()}")
    print(f"recommended_next_step={summary['recommended_next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
