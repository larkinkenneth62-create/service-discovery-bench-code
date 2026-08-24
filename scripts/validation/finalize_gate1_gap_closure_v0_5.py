#!/usr/bin/env python
"""Validate, report, and archive the authorized Gate 1 v0.5 work products."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from source_qa_review_validator_v0_4_2 import HUMAN_FIELDS
except ImportError:
    from scripts.validation.source_qa_review_validator_v0_4_2 import HUMAN_FIELDS


ARCHIVE_RELATIVE = "outputs/run_archives/2026-07-10_gate1_adjudication_and_g1_composable_gap_closure_v0_5"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def csv_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def check(name: str, passed: bool, observed: Any, expected: Any) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "observed": observed, "expected": expected}


def write_go_no_go(path: Path, generated_at: str, progress: dict[str, Any], g1: dict[str, Any], comp: dict[str, Any]) -> None:
    fields = {
        "benchmark_only_scope_frozen": True,
        "novel_method_required": False,
        "gate_0_status": "complete",
        "gate_1_status": "active",
        "gate_2_status": "blocked",
        "gate_3_inventory_status": "complete",
        "gate_3_candidate_dryrun_status": "complete",
        "gate_4_inventory_status": "complete",
        "gate_4_crosswalk_status": "complete",
        "core_human_rows_total": 296,
        "supplementary_human_rows_total": 55,
        "pending_human_rows": progress["total_pending_rows"],
        "can_freeze_metatool_now": False,
        "can_freeze_toolbench_now": False,
        "can_freeze_stabletoolbench_now": False,
        "can_include_shortcuts_supplement_now": False,
        "can_start_six_task_candidate_assembly": False,
        "can_generate_final_dataset": False,
        "can_create_split": False,
        "can_run_real_baselines": False,
    }
    lines = [
        "# Post-v0.4.2 Adjudication and Gap-Closure Go/No-Go v0.5",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Adjudication progress input: `{progress.get('packs_dir', '')}`",
        f"- G1 summary input: `{g1.get('raw_input', '')}`",
        f"- Composable inventory input: `{comp.get('inventory_input', '')}`",
        "",
        "## Fixed Status Fields",
        "",
    ]
    lines.extend(f"- `{key} = {str(value).lower() if isinstance(value, bool) else value}`" for key, value in fields.items())
    lines.extend(
        [
            "",
            "## Gap-Closure Evidence",
            "",
            f"- ToolBench G1 raw rows: `{g1['raw_rows']}`",
            f"- G1 candidate ready for QA: `{g1['decision_distribution'].get('candidate_ready_for_qa', 0)}`",
            f"- G1 QA pack rows: `{g1['qa_pack_rows']}`; all human fields remain blank.",
            f"- Composable inventory rows: `{comp['composable_inventory_rows']}`",
            f"- Composable rows represented in current v0.4.2 packs: `{comp['composable_in_current_review_pack_count']}`",
            f"- Unreviewed uncertain composable recovery queue: `{comp['unreviewed_uncertain_composable_candidates']}`",
            "- No strong-composable Gold count is claimed from evidence availability alone.",
            "",
            "## Decision",
            "",
            "`NO_GO_SOURCE_FREEZE_AND_ASSEMBLY`",
            "",
            "## Recommended Next Step",
            "",
            "complete source-specific human adjudication; validate and freeze each passed source independently; "
            "then review the ToolBench G1 single-API QA pack; use current composable adjudication results to "
            "decide whether recovery from the uncertain queue is required.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_validation_report(path: Path, generated_at: str, checks: list[dict[str, Any]]) -> None:
    passed = sum(item["passed"] for item in checks)
    lines = [
        "# Gate 1 Adjudication and Gap-Closure Validation v0.5",
        "",
        f"- Generated at: `{generated_at}`",
        f"- Checks passed: **{passed}/{len(checks)}**",
        f"- Overall status: `{'PASS' if passed == len(checks) else 'FAIL'}`",
        "",
        "| Check | Passed | Observed | Expected |",
        "|---|---:|---|---|",
    ]
    for item in checks:
        lines.append(
            f"| {item['check']} | {str(item['passed']).lower()} | "
            f"`{item['observed']}` | `{item['expected']}` |"
        )
    lines.extend(
        [
            "",
            "This validation covers authorized preparation outputs only. It does not validate a frozen source, "
            "six-task assembly, final dataset, split, baseline, or training artifact because none was produced.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive(root: Path, archive_root: Path, inputs: list[Path]) -> list[dict[str, Any]]:
    if archive_root.exists():
        raise FileExistsError(f"Archive already exists; refusing to overwrite: {archive_root}")
    archive_root.mkdir(parents=True)
    files: list[Path] = []
    for source in inputs:
        if not source.exists():
            continue
        if source.is_dir():
            files.extend(path for path in source.rglob("*") if path.is_file())
        else:
            files.append(source)
    unique = sorted(set(path.resolve() for path in files), key=lambda item: str(item).lower())
    manifest: list[dict[str, Any]] = []
    for source in unique:
        relative = source.relative_to(root)
        destination = archive_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest.append(
            {
                "relative_path": relative.as_posix(),
                "size_bytes": source.stat().st_size,
                "sha256": sha256(destination),
            }
        )
    write_json(
        archive_root / "archive_manifest.json",
        {"generated_at": now_iso(), "file_count": len(manifest), "files": manifest},
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate, report, and optionally archive Gate 1 v0.5 outputs.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--archive", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    generated_at = now_iso()

    progress_path = root / "outputs/adjudication_progress_v0_5/review_progress_summary.json"
    source_preflight_path = root / "outputs/source_freeze_analyzer_preparation_v0_5/preflight_summary.json"
    g1_path = root / "outputs/toolbench_g1_single_api_dryrun_v0_1/toolbench_g1_single_api_summary.json"
    comp_path = root / "outputs/composable_recovery_preparation_v0_1/composable_recovery_preparation_summary.json"
    for required in [progress_path, source_preflight_path, g1_path, comp_path]:
        if not required.exists():
            parser.error(f"Required summary does not exist: {required}")
    progress, source_preflight, g1, comp = map(read_json, [progress_path, source_preflight_path, g1_path, comp_path])

    g1_dir = root / "outputs/toolbench_g1_single_api_dryrun_v0_1"
    annotated_rows = csv_count(g1_dir / "toolbench_g1_single_api_candidates_annotated.csv")
    ready_rows = csv_count(g1_dir / "toolbench_g1_single_api_ready_for_qa.csv")
    reconstruction_rows = csv_count(g1_dir / "toolbench_g1_single_api_reconstruction_needed.csv")
    leakage_rows = csv_count(g1_dir / "toolbench_g1_single_api_leakage_hold.csv")
    qa_rows = read_csv(g1_dir / "toolbench_g1_single_api_qa_items_v0_1.csv")
    human_nonblank = sum(
        any(str(row.get(field) or "").strip() for field in HUMAN_FIELDS) for row in qa_rows
    )
    comp_dir = root / "outputs/composable_recovery_preparation_v0_1"
    crosswalk_rows = csv_count(comp_dir / "composable_current_review_crosswalk.csv")
    recovery_rows = csv_count(comp_dir / "composable_unreviewed_recovery_queue.csv")
    evidence_total = sum(
        comp.get(key, 0)
        for key in [
            "strong_objective_evidence_available_count",
            "partial_evidence_count",
            "sequence_only_count",
            "no_evidence_count",
            "source_unavailable_count",
        ]
    )
    decision_total = sum(g1["decision_distribution"].get(label, 0) for label in [
        "candidate_ready_for_qa", "reconstruction_needed", "leakage_hold", "mapping_uncertain", "excluded"
    ])
    checks = [
        check("core pending rows", progress["core_hard_path"]["pending_rows"] == 296, progress["core_hard_path"]["pending_rows"], 296),
        check("supplementary pending rows", progress["supplementary"]["pending_rows"] == 55, progress["supplementary"]["pending_rows"], 55),
        check("source-freeze preflight waits", source_preflight["status"] == "WAITING_FOR_HUMAN_ADJUDICATION", source_preflight["status"], "WAITING_FOR_HUMAN_ADJUDICATION"),
        check("no frozen pool files written", source_preflight["frozen_pool_files_written"] == 0, source_preflight["frozen_pool_files_written"], 0),
        check("G1 annotated row count", annotated_rows == g1["raw_rows"], annotated_rows, g1["raw_rows"]),
        check("G1 decision partition", decision_total == g1["raw_rows"], decision_total, g1["raw_rows"]),
        check("G1 ready CSV count", ready_rows == g1["decision_distribution"].get("candidate_ready_for_qa", 0), ready_rows, g1["decision_distribution"].get("candidate_ready_for_qa", 0)),
        check("G1 reconstruction CSV count", reconstruction_rows == g1["decision_distribution"].get("reconstruction_needed", 0), reconstruction_rows, g1["decision_distribution"].get("reconstruction_needed", 0)),
        check("G1 leakage CSV count", leakage_rows == g1["decision_distribution"].get("leakage_hold", 0), leakage_rows, g1["decision_distribution"].get("leakage_hold", 0)),
        check("G1 QA rows in allowed range", 100 <= len(qa_rows) <= 150, len(qa_rows), "100..150"),
        check("G1 QA human fields blank", human_nonblank == 0, human_nonblank, 0),
        check("composable crosswalk count", crosswalk_rows == comp["composable_in_current_review_pack_count"], crosswalk_rows, comp["composable_in_current_review_pack_count"]),
        check("composable recovery queue count", recovery_rows == comp["unreviewed_uncertain_composable_candidates"], recovery_rows, comp["unreviewed_uncertain_composable_candidates"]),
        check("composable evidence partition", evidence_total == comp["composable_inventory_rows"], evidence_total, comp["composable_inventory_rows"]),
        check("no strong-composable labels generated", comp["strong_composable_labels_generated"] == 0, comp["strong_composable_labels_generated"], 0),
    ]

    go_no_go_path = root / "docs/phase1/post_v0_4_2_adjudication_and_gap_closure_go_no_go_v0_5.md"
    validation_report_path = root / "docs/phase1/gate1_gap_closure_validation_report_v0_5.md"
    validation_summary_path = root / "outputs/gate1_gap_closure_validation_v0_5/validation_summary.json"
    write_go_no_go(go_no_go_path, generated_at, progress, g1, comp)
    write_validation_report(validation_report_path, generated_at, checks)
    validation_summary = {
        "generated_at": generated_at,
        "tests_run": len(checks),
        "tests_passed": sum(item["passed"] for item in checks),
        "tests_failed": sum(not item["passed"] for item in checks),
        "checks": checks,
        "source_freeze_executed": False,
        "six_task_assembly_executed": False,
        "final_dataset_generated": False,
        "split_created": False,
        "real_baseline_run": False,
        "training_run": False,
    }
    write_json(validation_summary_path, validation_summary)
    if validation_summary["tests_failed"]:
        print(json.dumps(validation_summary, ensure_ascii=False, indent=2))
        return 1

    archive_manifest_count = 0
    if args.archive:
        backups = sorted((root / "docs/project").glob("SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.pre_v0_5_backup_*.md"))
        archive_inputs = [
            root / "docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md",
            root / "docs/project/README_PROJECT_GOVERNANCE.md",
            root / "docs/phase1/benchmark_gate_status_v0_5.md",
            root / "docs/phase1/human_adjudication_progress_report_v0_5.md",
            root / "docs/phase1/source_freeze_analyzer_preparation_v0_5.md",
            root / "docs/phase1/toolbench_g1_single_api_candidate_dryrun_report_v0_1.md",
            root / "docs/phase1/composable_evidence_crosswalk_and_recovery_queue_v0_1.md",
            go_no_go_path,
            validation_report_path,
            root / "scripts/validation/check_v0_4_2_adjudication_progress.py",
            root / "scripts/validation/analyze_v0_4_2_reviewed_and_prepare_source_freeze.py",
            root / "scripts/validation/build_toolbench_g1_single_api_dryrun_v0_1.py",
            root / "scripts/validation/prepare_composable_evidence_crosswalk_v0_1.py",
            root / "scripts/validation/finalize_gate1_gap_closure_v0_5.py",
            root / "outputs/adjudication_progress_v0_5",
            root / "outputs/source_freeze_analyzer_preparation_v0_5",
            root / "outputs/toolbench_g1_single_api_dryrun_v0_1",
            root / "outputs/composable_recovery_preparation_v0_1",
            root / "outputs/gate1_gap_closure_validation_v0_5",
        ] + backups
        manifest = archive(root, root / ARCHIVE_RELATIVE, archive_inputs)
        archive_manifest_count = len(manifest)

    result = {
        **validation_summary,
        "go_no_go_report": str(go_no_go_path.resolve()),
        "validation_report": str(validation_report_path.resolve()),
        "archive_created": bool(args.archive),
        "archive_path": str((root / ARCHIVE_RELATIVE).resolve()) if args.archive else "",
        "archive_manifest_file_count": archive_manifest_count,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
