#!/usr/bin/env python3
"""Build deterministic group-aware splits after the G4 gate has passed."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
from servicediscoverybench.splits import (  # noqa: E402
    assign_components,
    audit_passed,
    build_split_components,
    candidate_bucket,
    reverse_leakage_audit,
)

csv.field_size_limit(2_147_483_647)

TASK_TYPES = (
    "single_service_discovery", "single_api_recommendation", "multi_service_discovery",
    "multi_api_recommendation", "composable_service_discovery", "composable_api_recommendation",
)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def blocked_output(output: Path, qa_status: dict, root: Path) -> int:
    output.mkdir(parents=True, exist_ok=False)
    status = {
        "stage": "G5",
        "status": "BLOCKED_G4_NOT_PASSED",
        "g4_status": qa_status.get("status", "MISSING"),
        "g4_gate_passed": bool(qa_status.get("g4_gate_passed", False)),
        "split_files_written": 0,
    }
    write_json(output / "RUN_STATUS.json", status)
    write_csv(output / "INPUT_MANIFEST.csv", [{
        "logical_name": "rc1_or_candidate_root", "resolved_path": str(root),
        "size_bytes": "", "sha256": "directory_not_hashed",
    }], ["logical_name", "resolved_path", "size_bytes", "sha256"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rc1-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=20260719)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--dev-ratio", type=float, default=0.1)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    args = parser.parse_args()
    root = Path(args.rc1_root).resolve()
    output = Path(args.output).resolve()
    qa_status_path = root / "qa" / "reports" / "QA_STATUS.json"
    qa_status = json.loads(qa_status_path.read_text(encoding="utf-8")) if qa_status_path.exists() else {}
    if qa_status.get("status") != "GATE_PASSED" or qa_status.get("g4_gate_passed") is not True:
        return blocked_output(output, qa_status, root)

    output.mkdir(parents=True, exist_ok=False)
    splits_root = output / "splits"
    (splits_root / "by_task").mkdir(parents=True)
    public_fields: list[str] | None = None
    rows: list[dict[str, str]] = []
    task_paths = []
    for task_type in TASK_TYPES:
        path = root / "tasks" / f"{task_type}.csv"
        fields, task_rows = read_csv(path)
        if public_fields is None:
            public_fields = fields
        elif fields != public_fields:
            raise ValueError(f"task schema mismatch: {path}")
        rows.extend(task_rows)
        task_paths.append(path)
    if not rows or public_fields is None:
        raise ValueError("empty RC1 task frame")

    provenance_path = root / "manifests" / "task_provenance.csv"
    _, provenance_rows = read_csv(provenance_path)
    provenance = {row["benchmark_task_id"]: row for row in provenance_rows}
    enriched = []
    for row in rows:
        prov = provenance.get(row["benchmark_task_id"], {})
        enriched.append({
            **row,
            "source_query_id": prov.get("source_query_id", ""),
            "parent_row_id": prov.get("parent_row_id", ""),
            "candidate_count_bucket": candidate_bucket(row["candidate_count"]),
        })

    row_to_group = build_split_components(enriched)
    ratios = {"train": args.train_ratio, "dev": args.dev_ratio, "test": args.test_ratio}
    assignment = assign_components(enriched, row_to_group, ratios=ratios, seed=args.seed)
    row_to_split = assignment.row_to_split
    collisions = reverse_leakage_audit(enriched, row_to_split)
    if not audit_passed(collisions):
        write_json(splits_root / "split_leakage_audit.json", {"status": "FAIL", "collisions": collisions})
        write_json(output / "RUN_STATUS.json", {"stage": "G5", "status": "BLOCKED_SPLIT_LEAKAGE", "g5_gate_passed": False})
        return 2

    split_rows: dict[str, list[dict[str, str]]] = {name: [] for name in ratios}
    by_task_split: dict[tuple[str, str], list[dict[str, str]]] = {}
    manifest_rows = []
    distribution = Counter()
    for row in rows:
        row_id = row["benchmark_task_id"]
        split = row_to_split[row_id]
        output_row = dict(row)
        output_row["split_group_id"] = row_to_group[row_id]
        split_rows[split].append(output_row)
        by_task_split.setdefault((row["task_type"], split), []).append(output_row)
        prov = provenance.get(row_id, {})
        bucket = candidate_bucket(row["candidate_count"])
        manifest_rows.append({
            "benchmark_task_id": row_id, "split_group_id": row_to_group[row_id], "split": split,
            "task_type": row["task_type"], "source_dataset": row["source_dataset"],
            "source_query_id": prov.get("source_query_id", ""), "query_signature": row["query_signature"],
            "task_signature": row["task_signature"], "paired_task_group_id": row["paired_task_group_id"],
            "parent_row_id": prov.get("parent_row_id", ""), "underlying_task_id": row["underlying_task_id"],
            "candidate_count_bucket": bucket,
        })
        distribution[(split, row["task_type"], row["source_dataset"], bucket)] += 1

    for split, values in split_rows.items():
        write_csv(splits_root / f"{split}.csv", values, public_fields)
    for task_type in TASK_TYPES:
        directory = splits_root / "by_task" / task_type
        for split in ratios:
            write_csv(directory / f"{split}.csv", by_task_split.get((task_type, split), []), public_fields)
    write_csv(splits_root / "split_manifest.csv", manifest_rows, list(manifest_rows[0]))
    write_csv(splits_root / "split_distribution.csv", [{
        "split": key[0], "task_type": key[1], "source_dataset": key[2],
        "candidate_count_bucket": key[3], "row_count": value,
    } for key, value in sorted(distribution.items())], ["split", "task_type", "source_dataset", "candidate_count_bucket", "row_count"])
    audit = {
        "status": "PASS", "hard_key_intersections_zero": True, "collisions": collisions,
        "row_count": len(rows), "component_count": len(set(row_to_group.values())), "seed": args.seed,
        "algorithm_version": "deterministic_component_greedy_balance_v1",
    }
    write_json(splits_root / "split_leakage_audit.json", audit)
    counts = {name: len(values) for name, values in split_rows.items()}
    (splits_root / "split_report.md").write_text(
        "# G5 group-aware split report\n\n"
        f"Status: **GATE_PASSED**. Seed: `{args.seed}`. Algorithm: `deterministic_component_greedy_balance_v1`.\n\n"
        + "\n".join(f"- {name}: {count}" for name, count in counts.items())
        + f"\n- connected components: {len(set(row_to_group.values()))}\n- all hard reverse-audit intersections: 0\n",
        encoding="utf-8",
    )
    status = {"stage": "G5", "status": "GATE_PASSED", "g5_gate_passed": True, "rows": len(rows), "components": len(set(row_to_group.values())), "split_counts": counts}
    write_json(output / "RUN_STATUS.json", status)
    input_paths = [qa_status_path, provenance_path, *task_paths]
    write_csv(output / "INPUT_MANIFEST.csv", [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in input_paths], ["resolved_path", "size_bytes", "sha256"])
    (output / "COMMANDS.log").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    (output / "RUN_CONFIG.yaml").write_text(f"stage: G5_group_aware_split\nsplit_seed: {args.seed}\ntrain_ratio: {args.train_ratio}\ndev_ratio: {args.dev_ratio}\ntest_ratio: {args.test_ratio}\nalgorithm_version: deterministic_component_greedy_balance_v1\n", encoding="utf-8")
    files = [path for path in output.rglob("*") if path.is_file() and path.name != "OUTPUT_MANIFEST.csv"]
    write_csv(output / "OUTPUT_MANIFEST.csv", [{"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in sorted(files)], ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
