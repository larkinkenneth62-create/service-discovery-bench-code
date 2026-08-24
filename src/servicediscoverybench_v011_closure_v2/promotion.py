from __future__ import annotations

from collections import Counter, defaultdict
import difflib
import json
from pathlib import Path
import shutil
from typing import Any, Mapping

from servicediscoverybench.joint_split_optimizer_v3 import TASKS

from . import EXPECTED_ASSIGNMENT_HASH
from .common import append_once, inventory, read_csv, sha256_file, text, tree_hash, write_csv, write_json


EXPECTED_SPLITS = {"train": 50_497, "dev": 4_793, "test": 4_788}
EXPECTED_TEST_TASKS = {
    "single_service_discovery": 1_559,
    "single_api_recommendation": 3_043,
    "multi_service_discovery": 73,
    "multi_api_recommendation": 73,
    "composable_service_discovery": 20,
    "composable_api_recommendation": 20,
}
EXPECTED_TEST_SOURCES = {"MetaTool": 1_554, "ShortcutsBench": 5, "StableToolBench": 15, "ToolBench": 3_214}
ALLOWED_ROW_CHANGES = {
    "split", "split_group_id", "split_identity_group_v3", "split_version", "legacy_split", "legacy_split_group_id"
}
OVERLAY_MANIFESTS = {
    "SPLIT_MANIFEST.csv", "SPLIT_GROUP_MANIFEST.csv", "SPLIT_VERSION.json",
    "TRAIN_TASK_IDS.txt", "DEV_TASK_IDS.txt", "TEST_TASK_IDS.txt",
}
REQUIRED_RELEASE_ASSETS = {
    "README.md", "DATA_CARD.md", "SCHEMA.md", "LICENSES_AND_SOURCE_TERMS.md",
    "catalogs", "examples", "qa", "reports", "tasks", "splits", "manifests",
}


def _load_task_rows(root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for task in TASKS:
        path = root / "tasks" / f"{task}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        for row in read_csv(path):
            task_id = text(row.get("benchmark_task_id"))
            if not task_id or task_id in result:
                raise ValueError(f"missing/duplicate benchmark_task_id: {task_id!r}")
            result[task_id] = row
    return result


def verify_candidate(candidate_root: Path) -> dict[str, Any]:
    version = json.loads((candidate_root / "manifests" / "SPLIT_VERSION.json").read_text(encoding="utf-8"))
    hashes = {key: text(version.get(key)) for key in ("candidate_assignment_hash", "candidate_a_assignment_hash", "assignment_hash") if text(version.get(key))}
    if len(set(hashes.values())) > 1:
        raise ValueError(f"conflicting Candidate A assignment hashes: {hashes}")
    assignment_hash = next(iter(hashes.values()), "")
    if assignment_hash != EXPECTED_ASSIGNMENT_HASH:
        raise ValueError(f"Candidate A hash {assignment_hash!r} != expected")
    split_rows = read_csv(candidate_root / "manifests" / "SPLIT_MANIFEST.csv")
    ids = [text(row.get("benchmark_task_id")) for row in split_rows]
    if len(split_rows) != 60_078 or len(set(ids)) != 60_078:
        raise ValueError("Candidate A must contain 60,078 unique task IDs")
    counts = Counter(text(row.get("split")) for row in split_rows)
    if dict(counts) != EXPECTED_SPLITS:
        raise ValueError(f"split counts mismatch: {counts}")
    test_rows = [row for row in split_rows if text(row.get("split")) == "test"]
    task_counts = Counter(text(row.get("task_type")) for row in test_rows)
    source_counts = Counter(text(row.get("source_dataset")) for row in test_rows)
    if dict(task_counts) != EXPECTED_TEST_TASKS or dict(source_counts) != EXPECTED_TEST_SOURCES:
        raise ValueError("Candidate A test task/source distribution mismatch")
    relation_fields = (
        "split_identity_group_v3", "paired_task_group_id", "underlying_task_id", "parent_row_id",
        "query_signature", "review_content_fingerprint",
    )
    conflicts: dict[str, int] = {}
    for field in relation_fields:
        groups: dict[str, set[str]] = defaultdict(set)
        for row in split_rows:
            value = text(row.get(field))
            if value:
                groups[value].add(text(row.get("split")))
        conflicts[field] = sum(len(values) > 1 for values in groups.values())
    if any(conflicts.values()):
        raise ValueError(f"Identity v3 relation conflicts: {conflicts}")
    return {
        "assignment_hash": assignment_hash,
        "assignment_hash_field": next(key for key, value in hashes.items() if value == assignment_hash),
        "unique_task_ids": len(set(ids)), "split_counts": dict(counts),
        "test_task_counts": dict(task_counts), "test_source_counts": dict(source_counts),
        "relation_conflicts": conflicts, "relation_conflict_total": sum(conflicts.values()),
    }


def verify_dedup_closure(validation_path: Path) -> dict[str, Any]:
    value = json.loads(validation_path.read_text(encoding="utf-8"))
    audit = value.get("dedup_audit") or value.get("dedup") or {}
    result = {
        "status": audit.get("status"),
        "safe": int(audit.get("safe_row_count", -1)),
        "suspicious": int(audit.get("suspicious_row_count", -1)),
        "inconclusive": int(audit.get("inconclusive_row_count", -1)),
        "ledger_rows": int(audit.get("ledger_row_count", -1)),
    }
    if result != {
        "status": "PASS_ALL_DEDUP_ROWS_SUPPORTED_AS_TRUE_DUPLICATES",
        "safe": 1050, "suspicious": 0, "inconclusive": 0, "ledger_rows": 1050,
    }:
        raise ValueError(f"dedup closure mismatch: {result}")
    return result


def immutability_diff(base_root: Path, candidate_root: Path, output_dir: Path) -> dict[str, Any]:
    base = _load_task_rows(base_root)
    candidate = _load_task_rows(candidate_root)
    if set(base) != set(candidate):
        raise ValueError("base and Candidate A task ID sets differ")
    diffs: list[dict[str, str]] = []
    for task_id in sorted(base):
        left, right = base[task_id], candidate[task_id]
        for field in sorted((set(left) | set(right)) - ALLOWED_ROW_CHANGES):
            if text(left.get(field)) != text(right.get(field)):
                diffs.append({"benchmark_task_id": task_id, "field": field, "v0_1": text(left.get(field)), "v0_1_1": text(right.get(field))})
    write_csv(output_dir / "01_V0_1_VS_V0_1_1_IMMUTABILITY_DIFF.csv", diffs, ["benchmark_task_id", "field", "v0_1", "v0_1_1"])
    report = {
        "base_rows": len(base), "candidate_rows": len(candidate),
        "allowed_changed_fields": sorted(ALLOWED_ROW_CHANGES), "non_allowed_difference_count": len(diffs),
        "content_immutable": not diffs,
    }
    (output_dir / "01_V0_1_VS_V0_1_1_IMMUTABILITY_REPORT.md").write_text(
        "# v0.1 vs v0.1.1 immutability report\n\n"
        f"- Rows aligned: {len(base):,}\n- Non-allowed field differences: {len(diffs)}\n"
        f"- Content immutable: `{str(not diffs).lower()}`\n- Allowed fields: {', '.join(sorted(ALLOWED_ROW_CHANGES))}\n",
        encoding="utf-8",
    )
    if diffs:
        raise ValueError(f"non-allowed v0.1/v0.1.1 differences: {len(diffs)}")
    return report


def promote_complete_release(base_root: Path, candidate_root: Path, run_root: Path) -> Path:
    missing = sorted(name for name in REQUIRED_RELEASE_ASSETS if not (base_root / name).exists())
    if missing:
        raise FileNotFoundError(f"base v0.1 missing required assets: {missing}")
    release_root = run_root / "release" / "ServiceDiscoveryBench-v0.1.1"
    shutil.copytree(base_root, release_root)
    for directory in ("tasks", "splits"):
        shutil.copytree(candidate_root / directory, release_root / directory, dirs_exist_ok=True)
    for name in OVERLAY_MANIFESTS:
        source = candidate_root / "manifests" / name
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copy2(source, release_root / "manifests" / name)
    if (candidate_root / "reports").exists():
        shutil.copytree(candidate_root / "reports", release_root / "reports" / "split_v0_1_1", dirs_exist_ok=True)
    for name in ("README_SPLIT_REVISION.md", "DATA_CARD_SPLIT_ADDENDUM.md"):
        if (candidate_root / name).exists():
            shutil.copy2(candidate_root / name, release_root / name)
    note = (
        "\n## v0.1.1 split revision\n\n"
        "The benchmark content is unchanged. The source-aware Identity v3 A_PROPORTIONAL split is the v0.1.1 primary split. "
        "Legacy task_signature is DIAGNOSTIC_ONLY, and historical v0.1 is permanently preserved.\n"
    )
    for name in ("README.md", "DATA_CARD.md", "SCHEMA.md", "CHANGELOG.md"):
        append_once(release_root / name, "## v0.1.1 split revision", note)
    split_version_path = release_root / "manifests" / "SPLIT_VERSION.json"
    version = json.loads(split_version_path.read_text(encoding="utf-8"))
    version.update({
        "release_name": "ServiceDiscoveryBench-v0.1.1",
        "candidate_assignment_hash": EXPECTED_ASSIGNMENT_HASH,
        "legacy_release_preserved": True,
        "content_rows_unchanged": 60_078,
        "promotion_status": "V0_1_1_SPLIT_PROMOTED",
    })
    write_json(split_version_path, version)
    return release_root


def update_project_docs(project_root: Path, run_root: Path) -> dict[str, Any]:
    target_dir = run_root / "09_PROJECT_DOCS"
    before_dir = target_dir / "BEFORE"
    before_dir.mkdir(parents=True, exist_ok=True)
    marker = "## ServiceDiscoveryBench v0.1.1 (Identity v3 / A_PROPORTIONAL)"
    section = (
        f"\n{marker}\n\n"
        "ServiceDiscoveryBench-v0.1 remains preserved. The versioned v0.1.1 release keeps all 60,078 content rows unchanged and adopts the approved source-aware Identity v3 A_PROPORTIONAL split (50,497/4,793/4,788). Legacy task_signature is DIAGNOSTIC_ONLY.\n"
    )
    changed: dict[str, bool] = {}
    for name in ("CURRENT_STATE.md", "AUTHORITATIVE_ARTIFACTS.md"):
        path = project_root / "docs" / "project" / name
        before = path.read_text(encoding="utf-8")
        shutil.copy2(path, before_dir / name)
        changed[name] = append_once(path, marker, section)
        after = path.read_text(encoding="utf-8")
        diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=f"before/{name}", tofile=f"after/{name}"))
        (target_dir / f"{name}.diff").write_text(diff, encoding="utf-8")
        shutil.copy2(path, target_dir / name)
    return {"changed": changed, "historical_v0_1_entry_deleted": False}


def rebuild_release_hashes(release_root: Path) -> dict[str, Any]:
    excluded = {"manifests/RELEASE_FILE_MANIFEST.csv", "SHA256SUMS.txt"}
    rows = inventory(release_root, exclude_relative=excluded)
    write_csv(release_root / "manifests" / "RELEASE_FILE_MANIFEST.csv", rows)
    (release_root / "SHA256SUMS.txt").write_text(
        "# Self-reference-safe: RELEASE_FILE_MANIFEST.csv and SHA256SUMS.txt are excluded.\n"
        + "".join(f"{row['sha256']}  {row['relative_path']}\n" for row in rows), encoding="utf-8"
    )
    append_once(
        release_root / "README.md", "### Release hash policy",
        "\n### Release hash policy\n\n`manifests/RELEASE_FILE_MANIFEST.csv` and `SHA256SUMS.txt` exclude both files to avoid self-reference.\n",
    )
    # README changed after the first inventory; rebuild once under the same exclusion rule.
    rows = inventory(release_root, exclude_relative=excluded)
    write_csv(release_root / "manifests" / "RELEASE_FILE_MANIFEST.csv", rows)
    (release_root / "SHA256SUMS.txt").write_text(
        "# Self-reference-safe: RELEASE_FILE_MANIFEST.csv and SHA256SUMS.txt are excluded.\n"
        + "".join(f"{row['sha256']}  {row['relative_path']}\n" for row in rows), encoding="utf-8"
    )
    return {"file_count": len(rows), "tree_sha256": tree_hash(release_root, exclude_relative=excluded), "self_reference_safe": True}


def load_release_rows(release_root: Path) -> list[dict[str, str]]:
    rows = list(_load_task_rows(release_root).values())
    if len(rows) != 60_078:
        raise ValueError("release row count mismatch")
    return rows
