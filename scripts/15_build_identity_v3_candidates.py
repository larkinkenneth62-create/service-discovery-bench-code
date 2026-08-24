#!/usr/bin/env python3
"""Drop-in replacement for the broken V5 split identity/optimizer script.

This script:
- preserves the authoritative Native v0.1 release and legacy split;
- rebuilds split identity v3 with source-local namespacing;
- solves train/dev/test jointly with SciPy HiGHS MILP;
- materializes A/B/C candidates and validates H01-H15;
- never runs downstream baselines or LLM preflight if no valid split exists;
- never promotes or overwrites the authoritative split.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
import time
import zipfile
from collections import Counter, defaultdict

SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.split_identity_v3 import (  # noqa: E402
    build_identity_v3,
    scope_decisions_as_rows,
    stable_hash,
)
from servicediscoverybench.joint_split_optimizer_v3 import (  # noqa: E402
    OptimizerConfig,
    TASKS,
    candidate_summary,
    choose_recommended_candidate,
    solve_split_candidate,
)

csv.field_size_limit(2_147_483_647)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def first_nonempty(row: dict[str, object], *fields: str) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def load_authoritative_rows(release_root: Path) -> list[dict[str, object]]:
    provenance_path = release_root / "manifests" / "task_provenance.csv"
    split_path = release_root / "splits" / "split_manifest.csv"
    if not provenance_path.exists() or not split_path.exists():
        raise FileNotFoundError("authoritative provenance/split manifest not found")
    provenance = {row["benchmark_task_id"]: row for row in read_csv(provenance_path)}
    legacy_split = {row["benchmark_task_id"]: row for row in read_csv(split_path)}
    rows: list[dict[str, object]] = []
    for task_type in TASKS:
        task_path = release_root / "tasks" / f"{task_type}.csv"
        if not task_path.exists():
            raise FileNotFoundError(task_path)
        for raw in read_csv(task_path):
            task_id = raw.get("benchmark_task_id", "")
            if task_id not in provenance or task_id not in legacy_split:
                raise ValueError(f"missing provenance/split row for {task_id}")
            prov = provenance[task_id]
            split_row = legacy_split[task_id]
            row: dict[str, object] = dict(raw)
            row.update(
                {
                    "task_type": raw.get("task_type") or task_type,
                    "source_dataset": raw.get("source_dataset") or prov.get("source_dataset") or prov.get("source"),
                    "source_task_id": first_nonempty(prov, "source_task_id", "g2_row_id", "source_row_id"),
                    "source_query_id": first_nonempty(prov, "source_query_id", "query_id", "instruction_id"),
                    "parent_row_id": first_nonempty(raw, "parent_row_id") or first_nonempty(prov, "parent_row_id"),
                    "review_content_fingerprint": first_nonempty(raw, "review_content_fingerprint")
                    or first_nonempty(prov, "review_content_fingerprint"),
                    "paired_task_group_id": first_nonempty(raw, "paired_task_group_id")
                    or first_nonempty(prov, "paired_task_group_id"),
                    "underlying_task_id": first_nonempty(raw, "underlying_task_id")
                    or first_nonempty(prov, "underlying_task_id"),
                    "query_signature": first_nonempty(raw, "query_signature")
                    or first_nonempty(prov, "query_signature"),
                    "task_signature": first_nonempty(raw, "task_signature")
                    or first_nonempty(prov, "task_signature"),
                    "legacy_split": split_row.get("split", ""),
                    "legacy_group": split_row.get("split_group_id", ""),
                    "split_group_id": split_row.get("split_group_id", ""),
                }
            )
            rows.append(row)
    if len(rows) != 60_078:
        raise ValueError(f"expected 60,078 authoritative rows, got {len(rows)}")
    if len({str(row["benchmark_task_id"]) for row in rows}) != len(rows):
        raise ValueError("duplicate benchmark_task_id")
    return rows


def content_hash(rows: list[dict[str, object]]) -> str:
    fields = (
        "benchmark_task_id",
        "query_text",
        "task_type",
        "prediction_target",
        "candidate_services_json",
        "candidate_apis_json",
        "gold_services_json",
        "gold_apis_json",
        "acceptable_gold_service_sets_json",
        "acceptable_gold_api_sets_json",
        "paired_task_group_id",
    )
    payload = [[str(row.get(field) or "") for field in fields] for row in sorted(rows, key=lambda r: str(r["benchmark_task_id"]))]
    return stable_hash(payload)


def write_identity_outputs(out: Path, rows: list[dict[str, object]], identity) -> None:
    write_csv(out / "01_IDENTITY_FIELD_SCOPE_AUDIT.csv", scope_decisions_as_rows(identity.scope_decisions))
    write_json(out / "01_IDENTITY_COLLISION_SUMMARY.json", identity.collision_summary)
    write_csv(out / "01_V3_RELATION_EDGES_FINAL.csv", identity.relation_edges)
    write_csv(
        out / "01_V3_GROUP_MANIFEST_FINAL.csv",
        [
            {
                "benchmark_task_id": row["benchmark_task_id"],
                "split_identity_group_v3": identity.row_to_group[str(row["benchmark_task_id"])],
                "source_dataset": row.get("source_dataset", ""),
                "source_query_id": row.get("source_query_id", ""),
                "source_task_id": row.get("source_task_id", ""),
                "legacy_task_signature": row.get("task_signature", ""),
            }
            for row in rows
        ],
    )
    write_csv(
        out / "01_V3_GROUP_SIZE_DISTRIBUTION_FINAL.csv",
        [
            {"split_identity_group_v3": group_id, "row_count": count}
            for group_id, count in sorted(identity.group_sizes.items())
        ],
    )
    (out / "01_IDENTITY_FIELD_SCOPE_DECISION.md").write_text(
        "# Identity v3 field scopes\n\n"
        "- source_query_id/source_task_id/paired_task_group_id/underlying_task_id: source-local namespace.\n"
        "- query_signature/review_content_fingerprint: global exact-content duplicate relation.\n"
        "- parent_row_id: direct benchmark row reference when exact; otherwise source-local.\n"
        "- legacy task_signature and split_group_id: diagnostic only.\n",
        encoding="utf-8",
    )


def write_candidate(out: Path, rows: list[dict[str, object]], identity, candidate) -> None:
    candidate_dir = out / "02_CANDIDATES" / candidate.candidate_name
    candidate_dir.mkdir(parents=True, exist_ok=True)
    split_manifest: list[dict[str, object]] = []
    for row in rows:
        task_id = str(row["benchmark_task_id"])
        split_manifest.append(
            {
                "benchmark_task_id": task_id,
                "split": candidate.row_to_split.get(task_id, ""),
                "split_identity_group_v3": identity.row_to_group[task_id],
                "legacy_split": row.get("legacy_split", ""),
                "task_type": row.get("task_type", ""),
                "source_dataset": row.get("source_dataset", ""),
                "source_task_id": row.get("source_task_id", ""),
                "source_query_id": row.get("source_query_id", ""),
                "query_signature": row.get("query_signature", ""),
                "review_content_fingerprint": row.get("review_content_fingerprint", ""),
                "paired_task_group_id": row.get("paired_task_group_id", ""),
                "underlying_task_id": row.get("underlying_task_id", ""),
                "parent_row_id": row.get("parent_row_id", ""),
            }
        )
    write_csv(candidate_dir / "SPLIT_MANIFEST.csv", split_manifest)
    group_rows = []
    for group_id, split in sorted(candidate.group_to_split.items()):
        group_rows.append(
            {
                "split_identity_group_v3": group_id,
                "split": split,
                "assignment_hash": candidate.assignment_hash,
            }
        )
    write_csv(candidate_dir / "SPLIT_GROUP_MANIFEST.csv", group_rows)
    write_csv(candidate_dir / "HARD_CONSTRAINT_RESULTS.csv", candidate.constraint_results)
    write_csv(
        candidate_dir / "MOVE_LEDGER.csv",
        [
            {
                "benchmark_task_id": row["benchmark_task_id"],
                "from_split": row.get("legacy_split", ""),
                "to_split": candidate.row_to_split.get(str(row["benchmark_task_id"]), ""),
            }
            for row in rows
            if candidate.row_to_split.get(str(row["benchmark_task_id"]), "") != row.get("legacy_split", "")
        ],
    )
    for filename, grouping in (
        ("TASK_SPLIT_DISTRIBUTION.csv", ("split", "task_type")),
        ("SOURCE_SPLIT_DISTRIBUTION.csv", ("split", "source_dataset")),
        ("TASK_SOURCE_SPLIT_DISTRIBUTION.csv", ("split", "task_type", "source_dataset")),
    ):
        counts = Counter(tuple(record[field] for field in grouping) for record in split_manifest)
        write_csv(
            candidate_dir / filename,
            [
                {**{field: key[index] for index, field in enumerate(grouping)}, "row_count": count}
                for key, count in sorted(counts.items())
            ],
        )
    write_json(candidate_dir / "STATUS.json", candidate_summary(candidate))
    write_json(candidate_dir / "SOLVER_METADATA.json", candidate.solver_metadata)


def make_review_bundle(out: Path) -> tuple[Path, str]:
    bundle_dir = out / "bundles"
    bundle_dir.mkdir(exist_ok=True)
    bundle = bundle_dir / f"ServiceDiscoveryBench_SPLIT_V3_FIXED_CODE_REVIEW_{out.name}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob("*")):
            if path.is_file() and "bundles" not in path.relative_to(out).parts:
                archive.write(path, path.relative_to(out).as_posix())
    with zipfile.ZipFile(bundle) as archive:
        bad = archive.testzip()
    if bad:
        raise RuntimeError(f"review bundle integrity failure: {bad}")
    digest = sha256_file(bundle)
    bundle.with_suffix(bundle.suffix + ".sha256.txt").write_text(f"{digest}  {bundle.name}\n", encoding="utf-8")
    return bundle, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-root",
        type=Path,
        default=ROOT / "outputs/runs/20260722_133000_final_release/ServiceDiscoveryBench-v0.1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / f"outputs/runs/{time.strftime('%Y%m%d_%H%M%S')}_split_v3_fixed_code",
    )
    parser.add_argument("--time-limit-seconds", type=float, default=900.0)
    parser.add_argument("--mip-rel-gap", type=float, default=0.0)
    args = parser.parse_args()
    out = args.output.resolve()
    args.release_root = args.release_root.resolve()
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)

    rows = load_authoritative_rows(args.release_root)
    before_hash = content_hash(rows)
    identity = build_identity_v3(rows)
    write_identity_outputs(out, rows, identity)

    config = OptimizerConfig(time_limit_seconds=args.time_limit_seconds, mip_rel_gap=args.mip_rel_gap)
    candidates = [
        solve_split_candidate(rows, identity.row_to_group, name, config=config)
        for name in ("A_PROPORTIONAL", "B_REPRESENTATIVE", "C_MINIMAL_CHANGE")
    ]
    for candidate in candidates:
        write_candidate(out, rows, identity, candidate)
    recommended = choose_recommended_candidate(candidates)

    comparison = [candidate_summary(candidate) for candidate in candidates]
    write_json(out / "03_CANDIDATE_COMPARISON.json", comparison)
    write_csv(
        out / "03_CANDIDATE_COMPARISON.csv",
        [
            {
                "candidate": candidate.candidate_name,
                "valid": candidate.valid,
                "solver_status": candidate.solver_status,
                "train_rows": candidate.counts.get("train", 0),
                "dev_rows": candidate.counts.get("dev", 0),
                "test_rows": candidate.counts.get("test", 0),
                "maximum_task_test_share": candidate.distribution_metrics.get("maximum_task_test_share"),
                "minimum_task_test_rows": candidate.distribution_metrics.get("minimum_task_test_rows"),
                "cell_test_l1": candidate.distribution_metrics.get("cell_test_l1"),
                "task_test_l1": candidate.distribution_metrics.get("task_test_l1"),
                "moved_rows": candidate.moved_rows,
                "moved_groups": candidate.moved_groups,
                "assignment_hash": candidate.assignment_hash,
                "failed_constraints": "|".join(
                    row["constraint"] for row in candidate.constraint_results if not row["passed"]
                ),
            }
            for candidate in candidates
        ],
    )
    write_json(
        out / "03_RECOMMENDATION_EVIDENCE.json",
        {
            "selection_is_not_hardcoded": True,
            "common_recommendation_order": [
                "validity",
                "maximum_task_test_share",
                "task_source_distribution_l1",
                "task_distribution_l1",
                "source_distribution_l1",
                "minimum_task_and_cell_coverage",
                "moved_rows",
                "moved_groups",
            ],
            "recommended_candidate": recommended.candidate_name if recommended else None,
            "candidates": comparison,
        },
    )
    (out / "03_RECOMMENDED_CANDIDATE.md").write_text(
        "# Split v3 recommendation\n\n"
        + (
            f"Recommended candidate: `{recommended.candidate_name}`. This is a candidate only and is not authoritative.\n"
            if recommended
            else "`NO_VALID_SPLIT_CANDIDATE`. Downstream stages are prohibited.\n"
        ),
        encoding="utf-8",
    )

    after_hash = content_hash(rows)
    if before_hash != after_hash:
        raise RuntimeError("authoritative row content changed in memory")
    validation = {
        "native_row_count": len(rows),
        "authoritative_content_hash_before": before_hash,
        "authoritative_content_hash_after": after_hash,
        "content_immutable": before_hash == after_hash,
        "identity_summary": identity.collision_summary,
        "valid_candidate_count": sum(candidate.valid for candidate in candidates),
        "recommended_candidate": recommended.candidate_name if recommended else None,
        "authoritative_split_overwritten": False,
        "authoritative_promotion": False,
        "formal_generative_llm_calls": 0,
    }
    write_json(out / "04_VALIDATION_SUMMARY.json", validation)
    write_json(
        out / "04_ENVIRONMENT.json",
        {
            "python": sys.version,
            "platform": platform.platform(),
            "script_sha256": sha256_file(SCRIPT_PATH),
        },
    )
    status = {
        "status": (
            "SPLIT_V3_VALID_CANDIDATES_READY_USER_APPROVAL_REQUIRED"
            if recommended
            else "SPLIT_V3_NO_VALID_CANDIDATE"
        ),
        "recommended_candidate": recommended.candidate_name if recommended else None,
        "valid_candidate_count": sum(candidate.valid for candidate in candidates),
        "identity_group_count": identity.collision_summary["group_count"],
        "identity_max_group_size": identity.collision_summary["max_group_size"],
        "source_local_cross_source_edges": identity.collision_summary[
            "source_local_cross_source_edges_after_namespacing"
        ],
        "authoritative_split_overwritten": False,
        "authoritative_promotion": False,
        "formal_generative_llm_calls": 0,
        "recommended_next_step": (
            "USER_REVIEW_VALID_SPLIT_CANDIDATES_THEN_RUN_SCRIPT_16"
            if recommended
            else "INSPECT_SCOPE_AND_SOLVER_EVIDENCE"
        ),
    }
    write_json(out / "RUN_STATUS.json", status)

    files = [path for path in out.rglob("*") if path.is_file() and "bundles" not in path.relative_to(out).parts]
    write_csv(
        out / "OUTPUT_MANIFEST.csv",
        [
            {
                "relative_path": path.relative_to(out).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(files)
        ],
    )
    (out / "SHA256SUMS.txt").write_text(
        "\n".join(
            f"{sha256_file(path)}  {path.relative_to(out).as_posix()}" for path in sorted(files)
        )
        + "\n",
        encoding="utf-8",
    )
    bundle, bundle_hash = make_review_bundle(out)
    status.update(
        {
            "review_bundle_path": (bundle.relative_to(ROOT).as_posix() if bundle.is_relative_to(ROOT) else str(bundle)),
            "review_bundle_sha256": bundle_hash,
            "review_bundle_integrity_pass": True,
        }
    )
    write_json(out / "RUN_STATUS.json", status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if recommended else 2


if __name__ == "__main__":
    raise SystemExit(main())
