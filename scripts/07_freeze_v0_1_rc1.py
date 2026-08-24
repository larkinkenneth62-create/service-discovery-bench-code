#!/usr/bin/env python3
"""Freeze ServiceDiscoveryBench v0.1-rc1 only after an evidenced G4 pass."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402

TASK_TYPES = (
    "single_service_discovery", "single_api_recommendation", "multi_service_discovery",
    "multi_api_recommendation", "composable_service_discovery", "composable_api_recommendation",
)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def write_blocked(output: Path, *, code: str, detail: dict) -> int:
    output.mkdir(parents=True, exist_ok=False)
    status = {"stage": "RC1_FREEZE", "status": code, "rc1_frozen": False, **detail}
    write_json(output / "RUN_STATUS.json", status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 3


def candidate_bucket(count: int) -> str:
    if count <= 5:
        return "2-5"
    if count <= 20:
        return "6-20"
    if count <= 50:
        return "21-50"
    if count <= 100:
        return "51-100"
    return "101+"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--qa-run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    candidate = Path(args.candidate_root).resolve()
    qa_run = Path(args.qa_run).resolve()
    output = Path(args.output).resolve()
    qa_status_path = qa_run / "qa" / "reports" / "QA_STATUS.json"
    qa_status = json.loads(qa_status_path.read_text(encoding="utf-8")) if qa_status_path.exists() else {}
    if qa_status.get("status") != "GATE_PASSED" or qa_status.get("g4_gate_passed") is not True:
        return write_blocked(output, code="BLOCKED_G4_NOT_PASSED", detail={
            "g4_status": qa_status.get("status", "MISSING"),
            "g4_gate_passed": bool(qa_status.get("g4_gate_passed", False)),
        })

    assembly_path = candidate / "reports" / "assembly_validation_summary.json"
    assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
    if assembly.get("status") != "GATE_PASSED" or assembly.get("public_prompt_forbidden_fields_present") is not False:
        return write_blocked(output, code="BLOCKED_G3_NOT_PASSED", detail={"g3_status": assembly.get("status", "MISSING")})
    _, summary_rows = read_csv(qa_run / "qa" / "reviews" / "human_reviews_summary.csv")
    unresolved = [row for row in summary_rows if not row.get("final_qa_decision") or row.get("qa_resolution_status") in {"primary_pending", "secondary_pending", "adjudication_pending"}]
    uncertain = [row for row in summary_rows if row.get("final_qa_decision") == "uncertain"]
    removals = [row for row in summary_rows if row.get("final_qa_decision") == "remove"]
    if unresolved or uncertain:
        return write_blocked(output, code="BLOCKED_QA_ROWS_NOT_CLOSED", detail={
            "unresolved_rows": len(unresolved),
            "uncertain_rows": len(uncertain),
            "remove_rows_ready_for_exclusion": len(removals),
        })
    exclusion_ids = {row["benchmark_task_id"] for row in removals}

    output.mkdir(parents=True, exist_ok=False)
    rc1 = output / "ServiceDiscoveryBench-v0.1-rc1"
    rc1.mkdir()
    for name in ("catalogs", "tasks", "manifests", "reports"):
        shutil.copytree(candidate / name, rc1 / name)
    shutil.copytree(candidate / "qa", rc1 / "qa")
    for source in (qa_run / "qa").rglob("*"):
        relative = source.relative_to(qa_run / "qa")
        destination = rc1 / "qa" / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    _, review_rows = read_csv(qa_run / "qa" / "reviews" / "human_reviews_long.csv")
    removal_review_by_id = {}
    for row in review_rows:
        task_id = row.get("benchmark_task_id", "")
        if task_id in exclusion_ids and row.get("final_decision") == "remove":
            removal_review_by_id[task_id] = row
    exclusion_ledger = []
    for summary in sorted(removals, key=lambda row: row["benchmark_task_id"]):
        task_id = summary["benchmark_task_id"]
        review = removal_review_by_id.get(task_id, {})
        exclusion_ledger.append({
            "benchmark_task_id": task_id,
            "task_type": summary["task_type"],
            "final_qa_decision": "remove",
            "qa_resolution_status": summary["qa_resolution_status"],
            "review_id": review.get("review_id", ""),
            "review_round": review.get("review_round", ""),
            "reviewer_id": review.get("reviewer_id", ""),
            "error_type": review.get("error_type", ""),
            "severity": review.get("severity", ""),
            "notes": review.get("notes", ""),
            "content_fingerprint": summary["content_fingerprint"],
            "closure_action": "excluded_from_rc1_tasks",
        })
    write_csv(rc1 / "qa" / "reports" / "qa_exclusion_ledger.csv", exclusion_ledger, [
        "benchmark_task_id", "task_type", "final_qa_decision", "qa_resolution_status", "review_id",
        "review_round", "reviewer_id", "error_type", "severity", "notes", "content_fingerprint",
        "closure_action",
    ])

    counts = {}
    task_fields = None
    all_ids = set()
    removed_from_tasks = 0
    source_counts: Counter[tuple[str, str, str]] = Counter()
    bucket_counts: Counter[tuple[str, str]] = Counter()
    for task_type in TASK_TYPES:
        fields, rows = read_csv(rc1 / "tasks" / f"{task_type}.csv")
        if task_fields is None:
            task_fields = fields
        elif fields != task_fields:
            raise ValueError(f"task schema mismatch: {task_type}")
        retained_rows = [row for row in rows if row["benchmark_task_id"] not in exclusion_ids]
        removed_from_tasks += len(rows) - len(retained_rows)
        write_csv(rc1 / "tasks" / f"{task_type}.csv", retained_rows, fields)
        ids = [row["benchmark_task_id"] for row in retained_rows]
        if len(ids) != len(set(ids)) or all_ids.intersection(ids):
            raise ValueError(f"duplicate task IDs: {task_type}")
        all_ids.update(ids)
        counts[task_type] = len(retained_rows)
        for row in retained_rows:
            source_counts[(task_type, row["source_dataset"], row["source_subset"])] += 1
            bucket_counts[(task_type, candidate_bucket(int(row["candidate_count"])))] += 1
    if set(counts) != set(TASK_TYPES) or any(value <= 0 for value in counts.values()):
        raise ValueError("all six task files must be non-empty")
    if removed_from_tasks != len(exclusion_ids):
        raise ValueError(f"expected to exclude {len(exclusion_ids)} task rows, excluded {removed_from_tasks}")

    provenance_path = rc1 / "manifests" / "task_provenance.csv"
    provenance_fields, provenance_rows = read_csv(provenance_path)
    retained_provenance = [row for row in provenance_rows if row["benchmark_task_id"] not in exclusion_ids]
    write_csv(provenance_path, retained_provenance, provenance_fields)
    if {row["benchmark_task_id"] for row in retained_provenance} != all_ids:
        raise ValueError("RC1 task/provenance ID coverage mismatch after QA exclusions")

    routing_path = rc1 / "manifests" / "routing_ledger.csv"
    routing_fields, routing_rows = read_csv(routing_path)
    for row in routing_rows:
        if row.get("retained_row_id") in exclusion_ids:
            row["route_status"] = "human_qa_excluded"
            row["reason"] = "blocking_human_qa_decision"
    write_csv(routing_path, routing_rows, routing_fields)

    original_counts = dict(assembly.get("task_counts", {}))
    assembly.update({
        "retained_rows": sum(counts.values()),
        "task_counts": counts,
        "human_qa_completed": True,
        "human_qa_excluded_rows": len(exclusion_ids),
        "pre_human_qa_task_counts": original_counts,
    })
    write_json(rc1 / "reports" / "assembly_validation_summary.json", assembly)
    write_csv(rc1 / "reports" / "task_counts_by_source.csv", [
        {"task_type": key[0], "source_dataset": key[1], "source_subset": key[2], "count": count}
        for key, count in sorted(source_counts.items())
    ], ["task_type", "source_dataset", "source_subset", "count"])
    write_csv(rc1 / "reports" / "task_counts_by_candidate_bucket.csv", [
        {"task_type": key[0], "candidate_count_bucket": key[1], "count": count}
        for key, count in sorted(bucket_counts.items())
    ], ["task_type", "candidate_count_bucket", "count"])
    (rc1 / "reports" / "assembly_summary.md").write_text(
        "# RC1 assembly summary\n\n"
        f"G3 structural validation passed. G4 human QA excluded {len(exclusion_ids)} blocking rows at freeze. "
        f"The frozen six-task RC1 contains {sum(counts.values())} rows.\n",
        encoding="utf-8",
    )

    (rc1 / "README.md").write_text(
        "# ServiceDiscoveryBench v0.1-rc1\n\n"
        "This is the frozen release candidate created only after the G4 human-only QA gate passed. "
        f"The freeze excluded {len(exclusion_ids)} rows marked remove by the authoritative human review. "
        "It is not the final public release until G5 splits, G6 baselines, source-terms review, and final release validation pass.\n",
        encoding="utf-8",
    )
    (rc1 / "SCHEMA.md").write_text("# Schema\n\nAuthoritative machine-readable schemas are recorded in the project `schemas/` directory. JSON-valued CSV fields use stable serialization; candidate and Gold values are canonical IDs.\n", encoding="utf-8")
    (rc1 / "DATA_CARD.md").write_text(f"# Data Card — RC1\n\nHuman QA gate: passed under the single-human-review policy. {len(exclusion_ids)} rows with authoritative `remove` decisions were excluded at freeze and are recorded in `qa/reports/qa_exclusion_ledger.csv`. Six-task counts are recorded in `reports/assembly_validation_summary.json`. Composable retains the accepted 95 service / 92 API scale limitation. This RC1 remains subject to split, baseline, and license/terms gates.\n", encoding="utf-8")
    (rc1 / "LICENSES_AND_SOURCE_TERMS.md").write_text("# Licenses and source terms\n\nRelease remains conditional on source-specific license and terms review. This file must be completed before public release; absence of a resolved term blocks `RELEASE_READY`.\n", encoding="utf-8")

    sums = []
    for path in sorted((path for path in rc1.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt"), key=lambda p: p.as_posix()):
        sums.append(f"{sha256_file(path)}  {path.relative_to(rc1).as_posix()}")
    (rc1 / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    status = {
        "stage": "RC1_FREEZE", "status": "FROZEN_RC1", "rc1_frozen": True,
        "task_counts": counts, "total_rows": sum(counts.values()),
        "human_qa_excluded_rows": len(exclusion_ids),
        "g3_gate_passed": True, "g4_gate_passed": True,
        "g5_gate_passed": False, "g6_gate_passed": False, "release_ready": False,
    }
    write_json(output / "RUN_STATUS.json", status)
    inputs = [qa_status_path, assembly_path, qa_run / "qa" / "reviews" / "human_reviews_summary.csv"]
    inputs.extend(candidate / "tasks" / f"{task_type}.csv" for task_type in TASK_TYPES)
    write_csv(output / "INPUT_MANIFEST.csv", [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in inputs], ["resolved_path", "size_bytes", "sha256"])
    (output / "COMMANDS.log").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    files = [path for path in output.rglob("*") if path.is_file() and path.name != "OUTPUT_MANIFEST.csv"]
    write_csv(output / "OUTPUT_MANIFEST.csv", [{"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in sorted(files)], ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
