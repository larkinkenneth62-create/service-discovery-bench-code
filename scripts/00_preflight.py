#!/usr/bin/env python3
"""Read-only G0 inventory for ServiceDiscoveryBench v0.1.

This script intentionally does not assemble tasks, judge semantics, create splits,
or run models. It records source/evidence locations, hashes, objective counts,
authority checks, historical references, and branch-scoped blockers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


EXPECTED_AUTHORITY_HASHES = {
    "final_acceptance_contract": "95ea33db18b8523ddd4312394b7b9e78cf9fea3c3d95d717ec07cb2c546e4cdc",
    "problem_register": "f666e9240a4d372b1375f3ca0eb5875b73be5ab4c041d47da9d462bba503a958",
    "current_state": "51fbc6cfa63dcecb3af4958871c2ddb1c01cf30f5f87aee0f926a5239644ce66",
    "execution_guide": "d7e8e089031ba294da44f425dfe94d64f4768503d34c04005eb6d3431155018c",
    "composable_resolution": "04292c26faaba3c9642070aa1680e3d9f5223697aedcf712fa001468065af627",
}

EXPECTED_COMPOSABLE_REVIEW_HASH = "e2d1f2b0f6e16f8fdd3d5b462085d50428b02a18a39f876f6ccb06c53a949f94"


@dataclass(frozen=True)
class InputSpec:
    logical_name: str
    path: str
    authority_role: str
    required: bool = True
    source_version_hint: str = ""
    notes: str = ""


@dataclass
class InventoryRow:
    logical_name: str
    resolved_path: str
    exists: bool
    file_type: str
    size_bytes: int | None
    sha256: str
    row_or_record_count: int | None
    schema_or_columns: str
    source_version_hint: str
    authority_role: str
    notes: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_listing_hash(path: Path) -> tuple[str, int, int]:
    """Hash a deterministic file listing, not the child file contents.

    The directory is discovery evidence rather than a consumed file input at G0.
    Later stages must content-hash each selected trace before consuming it.
    """
    digest = hashlib.sha256()
    count = 0
    size = 0
    for child in sorted((p for p in path.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
        rel = child.relative_to(path).as_posix()
        stat = child.stat()
        digest.update(f"{rel}\0{stat.st_size}\n".encode("utf-8"))
        count += 1
        size += stat.st_size
    return digest.hexdigest(), count, size


def count_csv(path: Path) -> tuple[int, list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        return sum(1 for _ in reader), header


def inspect_json_bytes(path: Path) -> tuple[int | None, str]:
    """Count items in a top-level JSON array without loading the file in memory."""
    in_string = False
    escape = False
    depth = 0
    root_seen = False
    item_started = False
    count = 0
    root_kind = "unknown"
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            for byte in chunk:
                char = chr(byte)
                if not root_seen:
                    if char.isspace():
                        continue
                    root_seen = True
                    if char == "[":
                        root_kind = "array"
                        depth = 1
                        continue
                    if char == "{":
                        root_kind = "object"
                        depth = 1
                    else:
                        root_kind = "scalar"
                    continue
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                    if root_kind == "array" and depth == 1:
                        item_started = True
                    continue
                if root_kind == "array" and depth == 1:
                    if char == "]":
                        if item_started:
                            count += 1
                        return count, "top-level JSON array"
                    if char == ",":
                        if item_started:
                            count += 1
                            item_started = False
                        continue
                    if not char.isspace():
                        item_started = True
                if char in "[{":
                    depth += 1
                elif char in "]}":
                    depth -= 1
    return (count if root_kind == "array" else None), f"top-level JSON {root_kind}"


def inspect_small_json(path: Path) -> tuple[int | None, str]:
    if path.stat().st_size > 64 * 1024 * 1024:
        return inspect_json_bytes(path)
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if isinstance(value, list):
        keys = sorted(value[0].keys()) if value and isinstance(value[0], dict) else []
        return len(value), json.dumps(keys, ensure_ascii=False)
    if isinstance(value, dict):
        return len(value), json.dumps(sorted(value.keys()), ensure_ascii=False)
    return 1, type(value).__name__


def inspect_file(path: Path) -> tuple[int | None, str]:
    with path.open("rb") as handle:
        magic = handle.read(4)
    if magic.startswith(b"PK\x03\x04"):
        return None, "PKZIP archive (source filename retains .json suffix)"
    suffix = path.suffix.lower()
    if suffix == ".csv":
        count, columns = count_csv(path)
        return count, json.dumps(columns, ensure_ascii=False)
    if suffix in {".json", ".extracted"}:
        return inspect_small_json(path)
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8-sig") as handle:
            return sum(1 for line in handle if line.strip()), "JSON Lines"
    return None, ""


def inventory_one(root: Path, spec: InputSpec) -> InventoryRow:
    raw_path = Path(spec.path)
    path = raw_path if raw_path.is_absolute() else root / raw_path
    path = path.resolve(strict=False)
    if not path.exists():
        return InventoryRow(
            spec.logical_name, str(path), False, "missing", None, "", None, "",
            spec.source_version_hint, spec.authority_role, spec.notes,
        )
    if path.is_dir():
        digest, count, size = directory_listing_hash(path)
        notes = "; ".join(filter(None, [spec.notes, "sha256 is deterministic path+size listing hash; selected files require content hashes before use"]))
        return InventoryRow(
            spec.logical_name, str(path), True, "directory", size, digest, count,
            "recursive file listing", spec.source_version_hint, spec.authority_role, notes,
        )
    count, schema = inspect_file(path)
    return InventoryRow(
        spec.logical_name, str(path), True, path.suffix.lower().lstrip(".") or "file",
        path.stat().st_size, sha256_file(path), count, schema,
        spec.source_version_hint, spec.authority_role, spec.notes,
    )


def base_specs(composable_review: str) -> list[InputSpec]:
    specs = [
        InputSpec("benchmark_master_plan", "docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md", "authoritative", True, "v1.1 header / v1.6 changelog"),
        InputSpec("final_acceptance_contract", "docs/project/FINAL_SAMPLE_ACCEPTANCE_CONTRACT_v1.md", "authoritative", True, "v1.10"),
        InputSpec("problem_register", "docs/project/SERVICEDISCOVERYBENCH_PROBLEM_REGISTER_AND_RESOLUTION_ORDER.md", "authoritative", True, "v1.24"),
        InputSpec("current_state", "docs/project/CURRENT_STATE.md", "authoritative", True, "v1.8"),
        InputSpec("authoritative_artifacts", "docs/project/AUTHORITATIVE_ARTIFACTS.md", "authoritative", True, "v2"),
        InputSpec("execution_guide", "docs/project/CODEX_SERVICEDISCOVERYBENCH_V0_1_EXECUTION_GUIDE.md", "authoritative", True, "v1.0"),
        InputSpec("composable_resolution", "docs/project/SERVICEDISCOVERYBENCH_COMPOSABLE_RESOLUTION_AND_FREEZE_v1_0.md", "owner_frozen_branch_decision", True, "v1.0"),
        InputSpec("toolbench_g1_query", "external_sources/ToolBench/data/instruction/G1_query.json", "raw_source"),
        InputSpec("toolbench_g2_query", "external_sources/ToolBench/data/instruction/G2_query.json", "raw_source"),
        InputSpec("toolbench_g3_query", "external_sources/ToolBench/data/instruction/G3_query.json", "raw_source"),
        InputSpec("toolbench_g1_instruction", "external_sources/ToolBench/data/test_instruction/G1_instruction.json", "raw_source"),
        InputSpec("toolbench_g2_instruction", "external_sources/ToolBench/data/test_instruction/G2_instruction.json", "raw_source"),
        InputSpec("toolbench_g3_instruction", "external_sources/ToolBench/data/test_instruction/G3_instruction.json", "raw_source"),
        InputSpec("toolbench_answers", "external_sources/ToolBench/data/answer", "raw_source_discovery"),
        InputSpec("toolbench_reproduction_data", "external_sources/ToolBench/reproduction_data", "raw_source_discovery"),
        InputSpec("stabletoolbench_g1", "external_sources/StableToolBench/solvable_queries/test_instruction/G1_instruction.json", "raw_source"),
        InputSpec("stabletoolbench_g2", "external_sources/StableToolBench/solvable_queries/test_instruction/G2_instruction.json", "raw_source"),
        InputSpec("stabletoolbench_g3", "external_sources/StableToolBench/solvable_queries/test_instruction/G3_instruction.json", "raw_source"),
        InputSpec("metatool_clean_data", "external_sources/MetaTool/dataset/data/all_clean_data.csv", "raw_source"),
        InputSpec("metatool_plugin_descriptions", "external_sources/MetaTool/dataset/plugin_des.json", "raw_source"),
        InputSpec("shortcutsbench_queries_archive", "external_sources/ShortcutsBench/generated_success_queries.json", "raw_source_archive", True, notes="PKZIP source; retained read-only"),
        InputSpec("shortcutsbench_records_archive", "external_sources/ShortcutsBench/1_final_detailed_records_filter_apis_leq_30.json", "raw_source_archive", True, notes="PKZIP source; retained read-only"),
        InputSpec("shortcutsbench_api_catalog_archive", "external_sources/ShortcutsBench/4_api_json_filter.json", "raw_source_archive", True, notes="PKZIP source; retained read-only"),
        InputSpec("shortcutsbench_queries", "external_sources/ShortcutsBench/generated_success_queries.json.extracted", "read_only_source_cache"),
        InputSpec("shortcutsbench_records", "external_sources/ShortcutsBench/1_final_detailed_records_filter_apis_leq_30.json.extracted", "read_only_source_cache"),
        InputSpec("shortcutsbench_api_catalog", "external_sources/ShortcutsBench/4_api_json_filter.json.extracted", "read_only_source_cache"),
        InputSpec("toolbench_g1_candidate_ready_pool", "outputs/toolbench_g1_single_api_dryrun_v0_1/toolbench_g1_single_api_ready_for_qa.csv", "candidate_evidence"),
        InputSpec("toolbench_g1_150row_qa_pack", "outputs/toolbench_g1_single_api_dryrun_v0_1/toolbench_g1_single_api_qa_items_v0_1.csv", "qa_evidence"),
        InputSpec("stabletoolbench_full_policy", "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_solvable_with_filter_policy_v0_2.csv", "historical_policy_evidence"),
        InputSpec("stabletoolbench_reviewed_pack", "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2_reviewed.csv", "prior_human_review_evidence"),
        InputSpec("metatool_full_policy", "outputs/external_source_policy_v0_2/metatool/metatool_single_service_with_leakage_policy_v0_2.csv", "historical_policy_evidence"),
        InputSpec("shortcutsbench_strict_task_pool", "outputs/shortcutsbench_strict_adapter_v0_1/shortcutsbench_strict_task_level_v0_1.csv", "candidate_evidence"),
        InputSpec("composable_reviewed_pack", composable_review, "human_review_evidence", True, "owner-supplied reviewed tranche A"),
        InputSpec("composable_dependency_evidence", "outputs/composable_dependency_extractor_patch_v0_3_2/corrected_dependency_edge_candidates.jsonl", "dependency_evidence"),
        InputSpec("composable_summary", "outputs/composable_task_necessity_patch_v0_3_3/composable_task_necessity_patch_summary_v0_3_3.json", "historical_machine_summary"),
        InputSpec("legacy_unified_schema", "outputs/unified_schema_v0_1/schema/service_discovery_bench_unified_schema_v0_1.json", "historical_schema_reference"),
    ]
    return specs


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def authority_checks(root: Path, rows: list[InventoryRow]) -> tuple[list[dict], list[dict]]:
    by_name = {row.logical_name: row for row in rows}
    checks: list[dict] = []
    issues: list[dict] = []
    for name in ("benchmark_master_plan", "final_acceptance_contract", "problem_register", "current_state", "authoritative_artifacts", "execution_guide", "composable_resolution"):
        ok = by_name[name].exists
        checks.append({"check": f"authority_readable:{name}", "passed": ok, "detail": by_name[name].resolved_path})
        if not ok:
            issues.append(issue("ERROR", "MISSING_AUTHORITY_FILE", name, "global", by_name[name].resolved_path))
    for name, expected in EXPECTED_AUTHORITY_HASHES.items():
        actual = by_name[name].sha256.lower()
        ok = actual == expected
        checks.append({"check": f"authority_hash:{name}", "passed": ok, "detail": actual})
        if not ok:
            issues.append(issue("ERROR", "AUTHORITY_HASH_MISMATCH", name, "global", f"expected={expected}; actual={actual}"))

    if all(by_name[n].exists for n in ("current_state", "problem_register", "final_acceptance_contract")):
        current = read_text(Path(by_name["current_state"].resolved_path))
        register = read_text(Path(by_name["problem_register"].resolved_path))
        contract = read_text(Path(by_name["final_acceptance_contract"].resolved_path))
        semantic_checks = {
            "design_decisions_zero": "design_decisions_requiring_user_confirmation = 0" in current and "design_decisions_requiring_user_confirmation = 0" in register,
            "composable_owner_resolved": "human review passed" in current and "COMP-03" in current and "RESOLVED" in register,
            "rep_01_02_resolved": "REP-01/REP-02" in current and "REP-01 / REP-02" in register,
            "eval_02_03_resolved": "EVAL-02/EVAL-03" in current and "EVAL-02 / EVAL-03" in register,
            "ac_01_09_frozen": all(f"AC-0{i}" in contract for i in range(1, 10)) and "FROZEN" in contract,
        }
        for name, ok in semantic_checks.items():
            checks.append({"check": name, "passed": ok, "detail": "authoritative text assertion"})
            if not ok:
                issues.append(issue("ERROR", "AUTHORITY_STATE_ASSERTION_FAILED", name, "global", "Required frozen state was not found verbatim"))
    return checks, issues


def issue(severity: str, code: str, logical_name: str, branch_scope: str, detail: str) -> dict:
    return {
        "severity": severity,
        "blocker_code": code,
        "logical_name": logical_name,
        "branch_scope": branch_scope,
        "detail": detail,
    }


def inspect_composable_review(row: InventoryRow) -> tuple[dict, list[dict]]:
    result = {
        "exists": row.exists,
        "rows": row.row_or_record_count,
        "human_confirmed_rows": 0,
        "adjudicator_id_nonblank": 0,
        "adjudicated_at_nonblank": 0,
        "review_content_hash_unique": 0,
        "reviewed_csv_hash_matches_freeze": False,
        "metadata_repair_lineage_valid": False,
        "review_status_completed_rows": 0,
        "semantic_decision_status": "RESOLVED_BY_OWNER",
        "packaging_status": "BLOCKED_BY_MISSING_PROVENANCE",
    }
    issues: list[dict] = []
    if not row.exists:
        issues.append(issue("ERROR", "MISSING_COMPOSABLE_REVIEW_ARTIFACT", row.logical_name, "composable_packaging", row.resolved_path))
        return result, issues
    path = Path(row.resolved_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    result["human_confirmed_rows"] = sum((r.get("adjudicator_type") or "").strip() == "human_confirmed" for r in records)
    result["adjudicator_id_nonblank"] = sum(bool((r.get("adjudicator_id") or "").strip()) for r in records)
    result["adjudicated_at_nonblank"] = sum(bool((r.get("adjudicated_at") or "").strip()) for r in records)
    result["review_content_hash_unique"] = len({(r.get("review_content_hash") or "").strip() for r in records if (r.get("review_content_hash") or "").strip()})
    result["reviewed_csv_hash_matches_freeze"] = row.sha256.lower() == EXPECTED_COMPOSABLE_REVIEW_HASH
    metadata_manifest = path.parent / "composable_review_metadata_manifest.json"
    if not result["reviewed_csv_hash_matches_freeze"] and metadata_manifest.exists():
        payload = json.loads(metadata_manifest.read_text(encoding="utf-8-sig"))
        result["metadata_repair_lineage_valid"] = all([
            payload.get("source_sha256") == EXPECTED_COMPOSABLE_REVIEW_HASH,
            payload.get("output_sha256") == row.sha256.lower(),
            payload.get("row_count") == 103,
            payload.get("adjudicator_id_populated") == 103,
            payload.get("review_status_completed") == 103,
            payload.get("review_content_hash_unchanged") is True,
            payload.get("semantic_rereview_performed") is False,
            set(payload.get("allowed_changed_fields", [])) == {"adjudicator_id", "review_status"},
        ])
    result["review_status_completed_rows"] = sum((r.get("review_status") or "").strip() == "HUMAN_REVIEW_COMPLETED" for r in records)
    if not (result["reviewed_csv_hash_matches_freeze"] or result["metadata_repair_lineage_valid"]):
        issues.append(issue("ERROR", "COMPOSABLE_REVIEW_HASH_MISMATCH", row.logical_name, "composable_packaging", f"expected={EXPECTED_COMPOSABLE_REVIEW_HASH}; actual={row.sha256.lower()}"))
    if len(records) != 103:
        issues.append(issue("ERROR", "COMPOSABLE_REVIEW_ROW_COUNT_MISMATCH", row.logical_name, "composable_packaging", f"expected=103; actual={len(records)}"))
    if result["review_content_hash_unique"] != len(records):
        issues.append(issue("ERROR", "COMPOSABLE_CONTENT_FINGERPRINT_INVALID", row.logical_name, "composable_packaging", f"unique={result['review_content_hash_unique']}/{len(records)}"))
    if result["human_confirmed_rows"] != len(records):
        issues.append(issue("ERROR", "COMPOSABLE_HUMAN_STATUS_INCOMPLETE", row.logical_name, "composable_packaging", f"human_confirmed={result['human_confirmed_rows']}/{len(records)}"))
    if result["adjudicator_id_nonblank"] != len(records):
        issues.append(issue("ERROR", "MISSING_COMPOSABLE_REVIEWER_ID", row.logical_name, "composable_packaging", f"adjudicator_id_nonblank={result['adjudicator_id_nonblank']}/{len(records)}"))
    if result["review_status_completed_rows"] != len(records):
        issues.append(issue("WARNING", "COMPOSABLE_REVIEW_STATUS_METADATA_STALE", row.logical_name, "composable_packaging", f"HUMAN_REVIEW_COMPLETED={result['review_status_completed_rows']}/{len(records)}; frozen resolution authorizes metadata-only repair"))
    if not issues:
        result["packaging_status"] = "PROVENANCE_LOCATED"
    return result, issues


def write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def output_manifest(output: Path) -> list[dict]:
    rows = []
    for path in sorted((p for p in output.rglob("*") if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.as_posix()):
        rows.append({
            "relative_path": path.relative_to(output).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--composable-review", required=True)
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    config = Path(args.config)
    if not config.is_absolute():
        config = (root / config).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = (root / output).resolve()
    composable_review = str(Path(args.composable_review).resolve())
    output.mkdir(parents=True, exist_ok=False)
    preflight = output / "preflight"
    preflight.mkdir()
    started = datetime.now(timezone.utc).isoformat()

    command = " ".join([shutil.which("python") or sys.executable, *sys.argv])
    (output / "COMMANDS.log").write_text(command + "\n", encoding="utf-8")
    config_text = config.read_text(encoding="utf-8-sig")
    (output / "RUN_CONFIG.yaml").write_text(
        config_text.rstrip() + f"\n\nrun:\n  stage: G0_preflight\n  started_at_utc: {started}\n  project_root: {json.dumps(str(root), ensure_ascii=False)}\n  composable_review: {json.dumps(composable_review, ensure_ascii=False)}\n",
        encoding="utf-8",
    )

    specs = base_specs(composable_review)
    rows = [inventory_one(root, spec) for spec in specs]
    inventory_fields = list(InventoryRow.__dataclass_fields__)
    inventory_dicts = [asdict(row) for row in rows]
    write_csv(preflight / "input_inventory.csv", inventory_dicts, inventory_fields)
    write_json(preflight / "input_inventory.json", inventory_dicts)
    write_csv(output / "INPUT_MANIFEST.csv", inventory_dicts, inventory_fields)

    checks, issues = authority_checks(root, rows)
    for row, spec in zip(rows, specs):
        if spec.required and not row.exists:
            issues.append(issue("ERROR", "MISSING_REQUIRED_INPUT", row.logical_name, "global", row.resolved_path))

    composable_row = next(row for row in rows if row.logical_name == "composable_reviewed_pack")
    composable_status, composable_issues = inspect_composable_review(composable_row)
    issues.extend(composable_issues)

    source_groups = {
        "ToolBench": ["toolbench_g1_query", "toolbench_g2_query", "toolbench_g3_query"],
        "StableToolBench": ["stabletoolbench_g1", "stabletoolbench_g2", "stabletoolbench_g3"],
        "MetaTool": ["metatool_clean_data", "metatool_plugin_descriptions"],
        "ShortcutsBench": ["shortcutsbench_queries", "shortcutsbench_records", "shortcutsbench_api_catalog"],
    }
    by_name = {row.logical_name: row for row in rows}
    source_status = {name: all(by_name[item].exists for item in names) for name, names in source_groups.items()}
    for source, ok in source_status.items():
        checks.append({"check": f"source_located:{source}", "passed": ok, "detail": ",".join(source_groups[source])})
        if not ok:
            issues.append(issue("ERROR", "SOURCE_NOT_LOCATED", source, source, "One or more expected source files are missing"))

    historical = sorted({
        p.relative_to(root).as_posix()
        for pattern in ("*source*freeze*v0_3*", "*source*freeze*v0_4*", "*handoff*v0_4*")
        for p in root.rglob(pattern)
        if p.exists()
    })
    historical_md = "# Historical / superseded references\n\nThese artifacts are retained and not deleted. They are evidence only and cannot override the five authoritative project documents.\n\n" + "\n".join(f"- `{p}` — `historical/superseded`" for p in historical) + "\n"
    (preflight / "historical_artifacts.md").write_text(historical_md, encoding="utf-8")

    hard_global_errors = [i for i in issues if i["severity"] == "ERROR" and i["branch_scope"] == "global"]
    gate_status = "FAIL" if hard_global_errors else ("PARTIAL" if composable_issues else "PASS")
    run_state = "BLOCKED" if hard_global_errors else ("PARTIAL" if composable_issues else "GATE_PASSED")
    next_action = (
        "Provide one fixed non-identifying adjudicator_id for the 103 frozen composable reviews; then apply the metadata-only adjudicator_id/review_status repair and rerun G0. No semantic re-review is requested."
        if composable_issues and not hard_global_errors
        else "Implement and run G1 catalog/crosswalk only after this preflight reports PASS."
    )

    authority_lines = ["# G0 Authority Check", "", f"Gate status: `{gate_status}`", "", "## Checks", ""]
    authority_lines.extend(f"- [{'x' if c['passed'] else ' '}] `{c['check']}` — {c['detail']}" for c in checks)
    authority_lines.extend(["", "## Authority precedence", "", "Owner latest confirmation > Acceptance Contract > Problem Register > Current State > Master Plan > historical artifacts.", ""])
    (preflight / "authority_check.md").write_text("\n".join(authority_lines), encoding="utf-8")

    source_lines = ["# Source Version Report", ""]
    for source, names in source_groups.items():
        source_lines.append(f"## {source} — {'located' if source_status[source] else 'missing'}")
        source_lines.extend(f"- `{by_name[name].resolved_path}` — `{by_name[name].sha256}` — records `{by_name[name].row_or_record_count}`" for name in names)
        source_lines.append("")
    (preflight / "source_version_report.md").write_text("\n".join(source_lines), encoding="utf-8")

    conflict_lines = [
        "# Conflict Resolution", "",
        "## Master Plan historical status vs current authority", "",
        "The Master Plan header and older source-freeze material retain historical NO-GO statements. Per the frozen precedence, CURRENT_STATE v1.8 and Problem Register v1.24 supersede those variable status claims.", "",
        "## Composable review provenance", "",
        "CURRENT_STATE and Problem Register resolve the semantic decision: Composable human review passed. The supplied reviewed CSV is therefore not re-judged by Codex.", "",
        f"Objective packaging audit: frozen_csv_hash_match={composable_status['reviewed_csv_hash_matches_freeze']}; human_confirmed={composable_status['human_confirmed_rows']}/{composable_status['rows']}; unique_review_content_hash={composable_status['review_content_hash_unique']}/{composable_status['rows']}; adjudicator_id_nonblank={composable_status['adjudicator_id_nonblank']}/{composable_status['rows']}; completed_review_status={composable_status['review_status_completed_rows']}/{composable_status['rows']}.", "",
        "The owner-supplied frozen resolution explicitly states that the 103 decisions are final, no re-review is required, and only reviewer metadata/status may be repaired without changing content. That latest branch decision governs over older variable-status and generic sampling text.", "",
        f"Result: semantic_decision_status=`{composable_status['semantic_decision_status']}`; packaging_status=`{composable_status['packaging_status']}`. The composable packaging branch is stopped until provenance is supplied; unrelated branches are not semantically reopened.", "",
    ]
    (output / "CONFLICT_RESOLUTION.md").write_text("\n".join(conflict_lines), encoding="utf-8")

    blocker_fields = ["severity", "blocker_code", "logical_name", "branch_scope", "detail"]
    write_csv(preflight / "blockers.csv", issues, blocker_fields)
    write_csv(output / "VALIDATION_ISSUES.csv", issues, blocker_fields)

    counts = {
        "inventory_entries": len(rows),
        "existing_entries": sum(row.exists for row in rows),
        "missing_entries": sum(not row.exists for row in rows),
        "source_status": source_status,
        "row_or_record_counts": {row.logical_name: row.row_or_record_count for row in rows},
        "blockers": len([i for i in issues if i["severity"] == "ERROR"]),
        "warnings": len([i for i in issues if i["severity"] == "WARNING"]),
    }
    write_json(output / "COUNTS.json", counts)
    validation = {
        "stage": "G0",
        "gate": "Preflight",
        "gate_status": gate_status,
        "checks": checks,
        "composable_review": composable_status,
        "hard_global_error_count": len(hard_global_errors),
        "branch_blocker_count": len([i for i in composable_issues if i["severity"] == "ERROR"]),
        "models_run": False,
        "human_labels_written": False,
        "split_generated": False,
        "baseline_run": False,
    }
    write_json(output / "VALIDATION_SUMMARY.json", validation)
    status = {
        "stage": "G0_preflight",
        "status": run_state,
        "gate_status": gate_status,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_root": str(root),
        "output": str(output),
        "python": sys.version,
        "platform": platform.platform(),
        "next_executable_action": next_action,
    }
    write_json(preflight / "preflight_status.json", status)
    write_json(output / "RUN_STATUS.json", status)
    (output / "README.md").write_text(
        f"# ServiceDiscoveryBench G0 Preflight\n\nStatus: `{run_state}` / gate `{gate_status}`.\n\nThis run is read-only with respect to source and prior reviewed artifacts. No model, human-label filling, task assembly, split, or baseline was executed.\n\nNext action: {next_action}\n",
        encoding="utf-8",
    )
    manifest = output_manifest(output)
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest, ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if not hard_global_errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
