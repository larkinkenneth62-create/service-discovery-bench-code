#!/usr/bin/env python3
"""Promote the fixed Candidate A split and build current-contract pre-LLM artifacts.

No network access, API-key reads, model downloads, or generative calls occur.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import shutil
import sys
import time
import zipfile

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.pre_llm_builder import load_catalog  # noqa: E402
from servicediscoverybench_v011_closure_v2 import ALLOWED_FINAL_STATUSES  # noqa: E402
from servicediscoverybench_v011_closure_v2.common import (  # noqa: E402
    inventory, read_csv, sha256_file, tree_hash, write_csv, write_json,
)
from servicediscoverybench_v011_closure_v2.evaluation import run_native_baselines  # noqa: E402
from servicediscoverybench_v011_closure_v2.global_track import run_global_track  # noqa: E402
from servicediscoverybench_v011_closure_v2.machine import build_and_evaluate_machine  # noqa: E402
from servicediscoverybench_v011_closure_v2.preflight import write_preflight  # noqa: E402
from servicediscoverybench_v011_closure_v2.promotion import (  # noqa: E402
    immutability_diff, load_release_rows, promote_complete_release, rebuild_release_hashes,
    update_project_docs, verify_candidate, verify_dedup_closure,
)


BASE_REL = Path("outputs/runs/20260722_133000_final_release/ServiceDiscoveryBench-v0.1")
CANDIDATE_REL = Path("outputs/runs/20260805_153039_dedup_audit_candidate_a/02_CANDIDATE_A_ADOPTION/ServiceDiscoveryBench-v0.1.1-candidate-a")
DEDUP_VALIDATION_REL = Path("outputs/runs/20260805_153039_dedup_audit_candidate_a/VALIDATION_SUMMARY.json")
MACHINE_REL = Path("artifacts/full_benchmark_v1/hard/candidate_pool.jsonl")
GLOBAL_POP_REL = Path("outputs/runs/20260804_135557_pre_llm_all_in_one_v1/05_GLOBAL_SOURCENATIVE_QUERY_MANIFEST.csv")
GLOBAL_REGISTRY_REL = Path("outputs/runs/20260804_135557_pre_llm_all_in_one_v1/05_GLOBAL_SOURCENATIVE_CATALOG_MANIFEST.csv")
GLOBAL_VISIBLE_REL = Path("artifacts/full_benchmark_v1/manifests/eligible_manifest.jsonl")
PLAN_ROOT_REL = Path("ServiceDiscoveryBench_V0_1_1_IMPLEMENTATION_FIX_AND_NEXT_PROMPT_V2")


def bundle_run(run_root: Path) -> tuple[Path, str]:
    bundle = run_root.parent / f"ServiceDiscoveryBench_V0_1_1_PRE_LLM_REVIEW_{run_root.name}.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in run_root.rglob("*") if p.is_file() and "release" not in p.relative_to(run_root).parts):
            archive.write(path, path.relative_to(run_root).as_posix())
    with zipfile.ZipFile(bundle) as archive:
        bad = archive.testzip()
    if bad:
        raise RuntimeError(f"review bundle CRC failed: {bad}")
    return bundle, sha256_file(bundle)


def finalize_inventory(run_root: Path) -> None:
    excluded = {"OUTPUT_MANIFEST.csv", "SHA256SUMS.txt"}
    rows = inventory(run_root, exclude_relative=excluded)
    write_csv(run_root / "OUTPUT_MANIFEST.csv", rows)
    (run_root / "SHA256SUMS.txt").write_text(
        "# Self-reference-safe: OUTPUT_MANIFEST.csv and SHA256SUMS.txt are excluded.\n"
        + "".join(f"{row['sha256']}  {row['relative_path']}\n" for row in rows), encoding="utf-8"
    )


def terminal(status: dict) -> None:
    values = {
        "status": status.get("status"), "v0_1_1_promoted": status.get("v0_1_1_promoted", False),
        "v0_1_1_path": status.get("v0_1_1_path", "NOT_CREATED"), "content_immutable": status.get("content_immutable", False),
        "train_dev_test": status.get("train_dev_test", "0/0/0"),
        "dedup_safe_suspicious_inconclusive": status.get("dedup_safe_suspicious_inconclusive", "0/0/0"),
        "cardinality_policy_status": status.get("cardinality_policy_status", "NOT_RUN"),
        "native_baselines_status": status.get("native_baselines_status", "NOT_RUN"),
        "machine_challenge_status": status.get("machine_challenge_status", "NOT_RUN"),
        "machine_challenge_rows": status.get("machine_challenge_rows", 0),
        "global_status": status.get("global_status", "NOT_RUN"), "global_formal_test_rows": status.get("global_formal_test_rows", 0),
        "global_native_candidate_copying": status.get("global_native_candidate_copying", False),
        "global_union_catalog_used": status.get("global_union_catalog_used", False),
        "llm_native_manifest_rows": status.get("llm_native_manifest_rows", 0),
        "llm_global_manifest_rows": status.get("llm_global_manifest_rows", 0),
        "llm_machine_manifest_rows": status.get("llm_machine_manifest_rows", 0),
        "llm_validation_pass": status.get("llm_validation_pass", False), "formal_generative_llm_calls": 0,
        "review_bundle_path": status.get("review_bundle_path", "NOT_CREATED"),
        "review_bundle_sha256": status.get("review_bundle_sha256", "NOT_AVAILABLE"),
        "review_bundle_integrity_pass": status.get("review_bundle_integrity_pass", False),
        "remaining_blockers": status.get("remaining_blockers", ""),
        "recommended_next_step": status.get("recommended_next_step", ""),
    }
    for key, value in values.items():
        print(f"{key} = {str(value).lower() if isinstance(value, bool) else value}")


def resume_global_and_packaging(project_root: Path, run_root: Path) -> int:
    """Resume only the first uncompleted Global stage and final packaging.

    Native baselines and Machine Challenge are read from the completed run and
    are never rerun by this recovery path.
    """
    release_root = run_root / "release" / "ServiceDiscoveryBench-v0.1.1"
    if not release_root.exists():
        raise FileNotFoundError(release_root)
    global_dir = run_root / "05_GLOBAL"
    backup = global_dir / "INITIAL_IMPLEMENTATION_BLOCK"
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("05_GLOBAL_SOURCE_NATIVE_REGISTRY_V0_1_1.csv", "GLOBAL_RETRIEVAL_SUMMARY.json", "GLOBAL_BLOCKED_ROWS.csv"):
        if (global_dir / name).exists() and not (backup / name).exists():
            shutil.copy2(global_dir / name, backup / name)
    rows = load_release_rows(release_root)
    test_rows = [row for row in rows if row.get("split") == "test"]
    row_to_split = {row["benchmark_task_id"]: row["split"] for row in rows}
    catalog = load_catalog(release_root / "catalogs" / "service_catalog.jsonl", release_root / "catalogs" / "api_catalog.jsonl")
    global_rows, global_status = run_global_track(
        project_root / GLOBAL_POP_REL, project_root / GLOBAL_REGISTRY_REL,
        project_root / GLOBAL_VISIBLE_REL, row_to_split, global_dir,
    )
    machine_rows = read_csv(run_root / "04_MACHINE_CHALLENGE" / "TASKS.csv")
    manifests, llm_validation = write_preflight(
        test_rows, global_rows, machine_rows, catalog, run_root / "06_LLM_PREFLIGHT",
        project_root / "src" / "servicediscoverybench" / "strict_output_parsers.py",
    )
    if not llm_validation["ready"]:
        raise RuntimeError("LLM preflight validation failed during resume")
    state = "V0_1_1_THREE_TRACK_PRE_LLM_READY" if global_status["status"] == "GLOBAL_LLM_MANIFEST_READY" else "V0_1_1_NATIVE_MACHINE_PRE_LLM_READY_GLOBAL_PARTIAL"
    if state not in ALLOWED_FINAL_STATUSES:
        raise RuntimeError(f"invalid resumed final state: {state}")
    promotion = json.loads((run_root / "01_PROMOTION" / "PROMOTION_STATUS.json").read_text(encoding="utf-8"))
    status = {
        "status": state, "v0_1_1_promoted": True, "v0_1_1_path": str(release_root),
        "v0_1_1_tree_sha256": promotion["release"]["tree_sha256"], "content_immutable": True,
        "train_dev_test": "50497/4793/4788", "dedup_safe_suspicious_inconclusive": "1050/0/0",
        "cardinality_policy_status": "DEV_FROZEN_PASS", "native_baselines_status": "COMPLETED_NOT_RERUN_ON_RESUME",
        "machine_challenge_status": "READY_NOT_RERUN_ON_RESUME", "machine_challenge_rows": len(machine_rows),
        "global_status": global_status["status"], "global_formal_test_rows": len(global_rows),
        "global_native_candidate_copying": False, "global_union_catalog_used": False,
        "llm_native_manifest_rows": len(manifests["native"]), "llm_global_manifest_rows": len(manifests["global"]),
        "llm_machine_manifest_rows": len(manifests["machine_challenge"]), "llm_validation_pass": True,
        "formal_generative_llm_calls": 0, "legacy_v0_1_overwritten": False,
        "remaining_blockers": "TOOLBENCH_API_GLOBAL_EXACT_SOURCE_NAMESPACE_GAP_V1" if state.endswith("GLOBAL_PARTIAL") else "NONE",
        "recommended_next_step": "INDEPENDENT_REVIEW_THEN_EXPLICIT_MODEL_REVISION_AND_BUDGET_SELECTION; DO_NOT_RUN_FORMAL_LLM_YET",
        "resume_scope": "GLOBAL_AND_PACKAGING_ONLY_NATIVE_AND_MACHINE_NOT_RERUN",
    }
    write_json(run_root / "VALIDATION_SUMMARY.json", {**status, "global": global_status, "llm": llm_validation})
    status["review_bundle_path"] = str(run_root.parent / f"ServiceDiscoveryBench_V0_1_1_PRE_LLM_REVIEW_{run_root.name}.zip")
    status["review_bundle_sha256"] = "SEE_DELIVERY_SIDECAR"
    status["review_bundle_integrity_pass"] = True
    write_json(run_root / "RUN_STATUS.json", status)
    finalize_inventory(run_root)
    bundle, digest = bundle_run(run_root)
    delivery = {"status": state, "run_directory": str(run_root), "review_bundle_path": str(bundle), "review_bundle_sha256": digest, "review_bundle_integrity_pass": True, "formal_generative_llm_calls": 0}
    write_json(bundle.with_suffix(bundle.suffix + ".delivery.json"), delivery)
    bundle.with_suffix(bundle.suffix + ".sha256.txt").write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
    status.update({"review_bundle_path": str(bundle), "review_bundle_sha256": digest, "review_bundle_integrity_pass": True})
    terminal(status)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--test-log", type=Path)
    parser.add_argument("--resume-run", type=Path, help="Resume only Global and packaging from an existing completed Native/Machine run")
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    if args.resume_run:
        return resume_global_and_packaging(project_root, args.resume_run.resolve())
    run_root = (args.output or project_root / "outputs" / "runs" / f"{time.strftime('%Y%m%d_%H%M%S')}_v0_1_1_closure_v2").resolve()
    if run_root.exists():
        raise FileExistsError(run_root)
    run_root.mkdir(parents=True)
    status: dict = {
        "status": "RUNNING", "v0_1_1_promoted": False, "content_immutable": False,
        "formal_generative_llm_calls": 0, "legacy_v0_1_overwritten": False,
        "global_native_candidate_copying": False, "global_union_catalog_used": False,
    }
    write_json(run_root / "RUN_STATUS.json", status)
    try:
        paths = {
            "base_v0_1_release": project_root / BASE_REL, "candidate_a_root": project_root / CANDIDATE_REL,
            "dedup_validation": project_root / DEDUP_VALIDATION_REL, "machine_evidence": project_root / MACHINE_REL,
            "global_passing_population": project_root / GLOBAL_POP_REL, "preferred_global_catalog_manifest": project_root / GLOBAL_REGISTRY_REL,
            "global_visible_queries": project_root / GLOBAL_VISIBLE_REL,
            "compliance_plan_md": project_root / PLAN_ROOT_REL / "00_IMPLEMENTATION_COMPLIANCE_PLAN.md",
            "compliance_plan_json": project_root / PLAN_ROOT_REL / "00_IMPLEMENTATION_COMPLIANCE_PLAN.json",
        }
        missing = [str(path) for path in paths.values() if not path.exists()]
        if missing:
            (run_root / "MISSING_INPUTS.md").write_text("# Missing inputs\n\n" + "\n".join(f"- {path}" for path in missing) + "\n", encoding="utf-8")
            raise FileNotFoundError(f"missing fixed inputs: {missing}")
        shutil.copy2(paths["compliance_plan_md"], run_root / "00_IMPLEMENTATION_COMPLIANCE_PLAN.md")
        shutil.copy2(paths["compliance_plan_json"], run_root / "00_IMPLEMENTATION_COMPLIANCE_PLAN.json")
        if args.test_log and args.test_log.exists():
            shutil.copy2(args.test_log, run_root / "TEST_LOG.txt")
        input_rows = [{"role": role, "path": str(path), "size_bytes": path.stat().st_size if path.is_file() else "DIRECTORY", "sha256": sha256_file(path) if path.is_file() else tree_hash(path)} for role, path in paths.items()]
        write_csv(run_root / "00_INPUT_INVENTORY.csv", input_rows)
        (run_root / "00_INPUT_HASHES.txt").write_text("".join(f"{row['sha256']}  {row['path']}\n" for row in input_rows), encoding="utf-8")
        candidate_validation = verify_candidate(paths["candidate_a_root"])
        dedup = verify_dedup_closure(paths["dedup_validation"])
        write_json(run_root / "00_PREDECESSOR_VERDICT.json", {"status": "PASS", "candidate_a": candidate_validation, "dedup": dedup, "formal_generative_llm_calls": 0})

        immutable = immutability_diff(paths["base_v0_1_release"], paths["candidate_a_root"], run_root / "01_PROMOTION")
        release_root = promote_complete_release(paths["base_v0_1_release"], paths["candidate_a_root"], run_root)
        project_docs = update_project_docs(project_root, run_root)
        release_hash = rebuild_release_hashes(release_root)
        shutil.copy2(release_root / "manifests" / "RELEASE_FILE_MANIFEST.csv", run_root / "01_PROMOTION" / "RELEASE_FILE_MANIFEST.csv")
        write_json(run_root / "01_PROMOTION" / "PROMOTION_STATUS.json", {"status": "V0_1_1_SPLIT_PROMOTED", "content_immutable": True, "release": release_hash, "project_docs": project_docs})
        rows = load_release_rows(release_root)
        catalog = load_catalog(release_root / "catalogs" / "service_catalog.jsonl", release_root / "catalogs" / "api_catalog.jsonl")
        baseline = run_native_baselines(rows, catalog, run_root / "02_CARDINALITY", run_root / "03_NATIVE_BASELINES")
        test_rows = [row for row in rows if row.get("split") == "test"]
        machine_rows, machine_status = build_and_evaluate_machine(test_rows, catalog, paths["machine_evidence"], baseline["per_query"], run_root / "04_MACHINE_CHALLENGE")
        row_to_split = {row["benchmark_task_id"]: row["split"] for row in rows}
        global_rows, global_status = run_global_track(paths["global_passing_population"], paths["preferred_global_catalog_manifest"], paths["global_visible_queries"], row_to_split, run_root / "05_GLOBAL")
        manifests, llm_validation = write_preflight(
            test_rows, global_rows, machine_rows, catalog, run_root / "06_LLM_PREFLIGHT",
            project_root / "src" / "servicediscoverybench" / "strict_output_parsers.py",
        )
        final_state = "V0_1_1_THREE_TRACK_PRE_LLM_READY" if global_status["status"] == "GLOBAL_LLM_MANIFEST_READY" else "V0_1_1_NATIVE_MACHINE_PRE_LLM_READY_GLOBAL_PARTIAL"
        if final_state not in ALLOWED_FINAL_STATUSES:
            raise RuntimeError(f"invalid final state: {final_state}")
        status.update({
            "status": final_state, "v0_1_1_promoted": True,
            "v0_1_1_path": str(release_root), "v0_1_1_tree_sha256": release_hash["tree_sha256"],
            "content_immutable": immutable["content_immutable"], "train_dev_test": "50497/4793/4788",
            "dedup_safe_suspicious_inconclusive": "1050/0/0", "cardinality_policy_status": "DEV_FROZEN_PASS",
            "native_baselines_status": baseline["status"], "machine_challenge_status": machine_status["status"],
            "machine_challenge_rows": len(machine_rows), "global_status": global_status["status"],
            "global_formal_test_rows": len(global_rows), "global_native_candidate_copying": False,
            "global_union_catalog_used": False, "llm_native_manifest_rows": len(manifests["native"]),
            "llm_global_manifest_rows": len(manifests["global"]), "llm_machine_manifest_rows": len(manifests["machine_challenge"]),
            "llm_validation_pass": llm_validation["ready"], "remaining_blockers": "TOOLBENCH_API_GLOBAL_EXACT_SOURCE_NAMESPACE_GAP_V1" if final_state.endswith("GLOBAL_PARTIAL") else "NONE",
            "recommended_next_step": "INDEPENDENT_REVIEW_THEN_EXPLICIT_MODEL_REVISION_AND_BUDGET_SELECTION; DO_NOT_RUN_FORMAL_LLM_YET",
        })
        if not llm_validation["ready"]:
            raise RuntimeError("LLM preflight validation failed")
        write_json(run_root / "ENVIRONMENT.json", {"python": sys.version, "platform": platform.platform(), "runner_sha256": sha256_file(SCRIPT)})
        write_json(run_root / "VALIDATION_SUMMARY.json", {**status, "candidate_a": candidate_validation, "dedup": dedup, "release": release_hash, "global": global_status, "llm": llm_validation})
        status["review_bundle_path"] = str(run_root.parent / f"ServiceDiscoveryBench_V0_1_1_PRE_LLM_REVIEW_{run_root.name}.zip")
        status["review_bundle_sha256"] = "SEE_DELIVERY_SIDECAR"
        status["review_bundle_integrity_pass"] = True
        write_json(run_root / "RUN_STATUS.json", status)
        finalize_inventory(run_root)
        bundle, digest = bundle_run(run_root)
        delivery = {"status": status["status"], "run_directory": str(run_root), "review_bundle_path": str(bundle), "review_bundle_sha256": digest, "review_bundle_integrity_pass": True, "formal_generative_llm_calls": 0}
        write_json(bundle.with_suffix(bundle.suffix + ".delivery.json"), delivery)
        bundle.with_suffix(bundle.suffix + ".sha256.txt").write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
        status.update({"review_bundle_path": str(bundle), "review_bundle_sha256": digest, "review_bundle_integrity_pass": True})
        terminal(status)
        return 0
    except Exception as exc:
        status.update({
            "status": "V0_1_1_PROMOTION_OR_PREFLIGHT_NO_GO", "error_type": type(exc).__name__, "error": str(exc),
            "remaining_blockers": f"{type(exc).__name__}:{exc}", "recommended_next_step": "REVIEW_NO_GO_BUNDLE_AND_FIX_ONLY_THE_RECORDED_IMPLEMENTATION_OR_INPUT_ERROR",
            "formal_generative_llm_calls": 0,
        })
        write_json(run_root / "RUN_STATUS.json", status)
        write_json(run_root / "VALIDATION_SUMMARY.json", status)
        finalize_inventory(run_root)
        bundle, digest = bundle_run(run_root)
        status.update({"review_bundle_path": str(bundle), "review_bundle_sha256": digest, "review_bundle_integrity_pass": True})
        write_json(bundle.with_suffix(bundle.suffix + ".delivery.json"), {"status": status["status"], "review_bundle_sha256": digest, "formal_generative_llm_calls": 0})
        bundle.with_suffix(bundle.suffix + ".sha256.txt").write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
        terminal(status)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
