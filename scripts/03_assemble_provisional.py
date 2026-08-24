#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.catalogs import stable_json  # noqa: E402
from servicediscoverybench.leakage import DETECTOR_VERSION, find_exact_surface, is_generic_common_surface  # noqa: E402
from servicediscoverybench.manifests import sha256_file, write_csv, write_json, write_jsonl  # noqa: E402
from servicediscoverybench.normalize import normalize_text  # noqa: E402
from servicediscoverybench.signatures import stable_hash  # noqa: E402

csv.field_size_limit(2_147_483_647)

TASK_TYPES = (
    "single_service_discovery",
    "single_api_recommendation",
    "multi_service_discovery",
    "multi_api_recommendation",
    "composable_service_discovery",
    "composable_api_recommendation",
)
PUBLIC_FIELDS = [
    "benchmark_task_id", "underlying_task_id", "paired_task_group_id", "split_group_id", "task_type",
    "prediction_target", "source_dataset", "source_subset", "query_text", "user_visible_context_json",
    "candidate_services_json", "candidate_apis_json", "gold_services_json", "gold_apis_json",
    "acceptable_gold_service_sets_json", "acceptable_gold_api_sets_json", "service_api_map_json",
    "dependency_graph_json", "candidate_count", "gold_count", "query_signature", "task_signature",
    "signature_version",
]
PROVENANCE_FIELDS = [
    "benchmark_task_id", "g2_row_id", "source_dataset", "source_subset", "source_query_id", "repair_status",
    "parent_row_id", "repair_version", "review_content_fingerprint", "source_provenance_json",
    "candidate_catalog_provenance_json", "inherited_human_review_json", "g3_route_status",
]


def load_jsonl(path: Path, field: str) -> tuple[dict[str, dict], dict[str, str]]:
    rows, names = {}, {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[row[field]] = row
                names[row[field]] = normalize_text(row["canonical_name"], casefold=True)
    return rows, names


def surfaces_for_api(record: dict) -> list[str]:
    return list(dict.fromkeys(filter(None, [record.get("canonical_name", ""), record.get("operation_id", ""), record.get("endpoint", "")])))


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


def semantic_key(row: dict, service_names: dict[str, str], api_rows: dict[str, dict], api_names: dict[str, str]) -> str:
    candidate_services = json.loads(row["candidate_services_json"])
    gold_services = json.loads(row["gold_services_json"])
    candidate_apis = json.loads(row["candidate_apis_json"])
    gold_apis = json.loads(row["gold_apis_json"])

    def api_label(aid: str) -> list[str]:
        record = api_rows[aid]
        return [service_names[record["parent_service_id"]], api_names[aid], normalize_text(record.get("http_method"), casefold=True)]

    value = {
        "query": normalize_text(row["query_text"], casefold=True),
        "task_type": row["task_type"],
        "candidate_services": sorted(service_names[sid] for sid in candidate_services),
        "gold_services": sorted(service_names[sid] for sid in gold_services),
        "candidate_apis": sorted(api_label(aid) for aid in candidate_apis),
        "gold_apis": sorted(api_label(aid) for aid in gold_apis),
        "dependency_graph": json.loads(row["dependency_graph_json"]),
    }
    return stable_hash(value)


def leakage_hits(row: dict, paired_gold_apis: dict[str, set[str]], service_rows: dict[str, dict], api_rows: dict[str, dict]) -> list[dict]:
    hits = []
    query = row["query_text"]
    if row["prediction_target"] == "service":
        for sid in json.loads(row["gold_services_json"]):
            for hit in find_exact_surface(query, service_rows[sid]["canonical_name"]):
                hits.append({**hit, "target_id": sid, "field_name": "query_text", "leakage_level": "service"})
        for aid in sorted(paired_gold_apis.get(row["paired_task_group_id"], set())):
            for surface in surfaces_for_api(api_rows[aid]):
                for hit in find_exact_surface(query, surface):
                    hits.append({**hit, "target_id": aid, "field_name": "query_text", "leakage_level": "api_clue_in_service_task"})
    else:
        for aid in json.loads(row["gold_apis_json"]):
            for surface in surfaces_for_api(api_rows[aid]):
                for hit in find_exact_surface(query, surface):
                    hits.append({**hit, "target_id": aid, "field_name": "query_text", "leakage_level": "api"})
    unique = {}
    for hit in hits:
        key = (hit["target_id"], hit["normalized_surface"], hit["start_offset"], hit["end_offset"], hit["leakage_level"])
        unique[key] = hit
    return list(unique.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--g2-candidates", required=True)
    parser.add_argument("--service-catalog", required=True)
    parser.add_argument("--api-catalog", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = {key: Path(value).resolve() for key, value in vars(args).items() if key != "output"}
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    candidate_root = output / "candidate"
    tasks_dir = candidate_root / "tasks"
    manifests_dir = candidate_root / "manifests"
    reports_dir = candidate_root / "reports"
    for directory in (tasks_dir, manifests_dir, reports_dir):
        directory.mkdir(parents=True, exist_ok=True)

    service_rows, service_names = load_jsonl(paths["service_catalog"], "service_id")
    api_rows, api_names = load_jsonl(paths["api_catalog"], "api_id")

    paired_gold_apis: dict[str, set[str]] = defaultdict(set)
    with paths["g2_candidates"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["prediction_target"] == "api":
                paired_gold_apis[row["paired_task_group_id"]].update(json.loads(row["gold_apis_json"]))

    clean_meta = []
    route_ledger, all_hits, common_overlap_hits = [], [], []
    common_overlap_row_ids = set()
    duplicate_groups: dict[str, list[dict]] = defaultdict(list)
    with paths["g2_candidates"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row_hits = leakage_hits(row, paired_gold_apis, service_rows, api_rows)
            blocking_hits = [hit for hit in row_hits if not is_generic_common_surface(hit["normalized_surface"])]
            generic_hits = [hit for hit in row_hits if is_generic_common_surface(hit["normalized_surface"])]
            for hit in generic_hits:
                common_overlap_hits.append({"g2_row_id": row["g2_row_id"], **hit, "automatic_blocking": "false"})
                common_overlap_row_ids.add(row["g2_row_id"])
            if blocking_hits:
                for hit in blocking_hits:
                    all_hits.append({"g2_row_id": row["g2_row_id"], **hit})
                route_ledger.append({"g2_row_id": row["g2_row_id"], "route_status": "excluded_exact_leak", "reason": "prediction_target_aware_exact_leak", "retained_row_id": ""})
                continue
            key = semantic_key(row, service_names, api_rows, api_names)
            meta = {
                "g2_row_id": row["g2_row_id"], "source_dataset": row["source_dataset"], "task_type": row["task_type"],
                "underlying_task_id": row["underlying_task_id"], "paired_task_group_id": row["paired_task_group_id"],
                "split_group_id": row["split_group_id"], "query_signature": row["query_signature"], "semantic_key": key,
            }
            clean_meta.append(meta)
            duplicate_groups[key].append(meta)

    dropped, retained_for_key = set(), {}
    dedup_ledger = []
    source_priority = {"StableToolBench": 0, "ToolBench": 1, "MetaTool": 2, "ShortcutsBench": 3}
    for key, group in duplicate_groups.items():
        winner = sorted(group, key=lambda item: (source_priority.get(item["source_dataset"], 9), item["g2_row_id"]))[0]
        retained_for_key[key] = winner["g2_row_id"]
        for item in group:
            if item["g2_row_id"] == winner["g2_row_id"]:
                continue
            dropped.add(item["g2_row_id"])
            dedup_ledger.append({
                "dropped_g2_row_id": item["g2_row_id"], "retained_g2_row_id": winner["g2_row_id"],
                "semantic_task_key": key, "reason": "exact_semantic_duplicate_prefer_stable" if winner["source_dataset"] == "StableToolBench" and item["source_dataset"] == "ToolBench" else "exact_semantic_duplicate_deterministic_keep",
                "dropped_source": item["source_dataset"], "retained_source": winner["source_dataset"],
            })
            route_ledger.append({"g2_row_id": item["g2_row_id"], "route_status": "deduplicated", "reason": dedup_ledger[-1]["reason"], "retained_row_id": winner["g2_row_id"]})

    retained_meta = [item for item in clean_meta if item["g2_row_id"] not in dropped]
    by_query: dict[str, list[dict]] = defaultdict(list)
    for item in retained_meta:
        by_query[item["query_signature"]].append(item)
    split_override = {}
    for query_sig, group in by_query.items():
        if len({item["underlying_task_id"] for item in group}) > 1:
            shared = f"split::query::{query_sig[:24]}"
            for item in group:
                split_override[item["g2_row_id"]] = shared

    retained_ids = {item["g2_row_id"] for item in retained_meta}
    benchmark_ids = {row_id: f"sdb-v0.1::{stable_hash(['benchmark_task', row_id])[:24]}" for row_id in retained_ids}
    task_handles, task_writers = {}, {}
    for task_type in TASK_TYPES:
        handle = (tasks_dir / f"{task_type}.csv").open("w", encoding="utf-8-sig", newline="")
        task_handles[task_type] = handle
        task_writers[task_type] = csv.DictWriter(handle, fieldnames=PUBLIC_FIELDS, extrasaction="ignore", lineterminator="\n")
        task_writers[task_type].writeheader()

    provenance, dependency_evidence = [], []
    counts_source, counts_bucket, counts_task = Counter(), Counter(), Counter()
    with paths["g2_candidates"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row_id = row["g2_row_id"]
            if row_id not in retained_ids:
                continue
            task_type = row["task_type"]
            benchmark_id = benchmark_ids[row_id]
            public = {field: row.get(field, "") for field in PUBLIC_FIELDS}
            public["benchmark_task_id"] = benchmark_id
            public["split_group_id"] = split_override.get(row_id, row["split_group_id"])
            task_writers[task_type].writerow(public)
            provenance.append({
                "benchmark_task_id": benchmark_id, "g2_row_id": row_id, "source_dataset": row["source_dataset"],
                "source_subset": row["source_subset"], "source_query_id": row["source_query_id"],
                "repair_status": row["repair_status"], "parent_row_id": row["parent_row_id"], "repair_version": row["repair_version"],
                "review_content_fingerprint": row["review_content_fingerprint"], "source_provenance_json": row["source_provenance_json"],
                "candidate_catalog_provenance_json": row["candidate_catalog_provenance_json"],
                "inherited_human_review_json": row["inherited_human_review_json"], "g3_route_status": "clean_ready",
            })
            evidence = json.loads(row["dependency_evidence_json"])
            if evidence:
                dependency_evidence.append({"benchmark_task_id": benchmark_id, "evidence": evidence})
            count = int(row["candidate_count"])
            counts_source[(task_type, row["source_dataset"], row["source_subset"])] += 1
            counts_bucket[(task_type, candidate_bucket(count))] += 1
            counts_task[task_type] += 1
            route_ledger.append({"g2_row_id": row_id, "route_status": "clean_ready", "reason": "common_overlap_requires_human_qa" if row_id in common_overlap_row_ids else "passed_exact_leakage_and_dedup", "retained_row_id": benchmark_id})

    for handle in task_handles.values():
        handle.close()

    write_csv(manifests_dir / "task_provenance.csv", provenance, PROVENANCE_FIELDS)
    write_jsonl(manifests_dir / "dependency_evidence.jsonl", dependency_evidence)
    write_csv(manifests_dir / "routing_ledger.csv", route_ledger, ["g2_row_id", "route_status", "reason", "retained_row_id"])
    write_csv(manifests_dir / "dedup_ledger.csv", dedup_ledger, ["dropped_g2_row_id", "retained_g2_row_id", "semantic_task_key", "reason", "dropped_source", "retained_source"])
    write_csv(manifests_dir / "leakage_hits.csv", all_hits, ["g2_row_id", "target_id", "matched_surface", "normalized_surface", "field_name", "start_offset", "end_offset", "match_type", "leakage_level", "detector_version"])
    write_csv(manifests_dir / "common_overlap_hits.csv", common_overlap_hits, ["g2_row_id", "target_id", "matched_surface", "normalized_surface", "field_name", "start_offset", "end_offset", "match_type", "leakage_level", "detector_version", "automatic_blocking"])
    write_csv(reports_dir / "task_counts_by_source.csv", [{"task_type": key[0], "source_dataset": key[1], "source_subset": key[2], "count": value} for key, value in sorted(counts_source.items())], ["task_type", "source_dataset", "source_subset", "count"])
    write_csv(reports_dir / "task_counts_by_candidate_bucket.csv", [{"task_type": key[0], "candidate_count_bucket": key[1], "count": value} for key, value in sorted(counts_bucket.items())], ["task_type", "candidate_count_bucket", "count"])
    issues = []
    missing_tasks = [task_type for task_type in TASK_TYPES if counts_task[task_type] == 0]
    for task_type in missing_tasks:
        issues.append({"severity": "ERROR", "code": "EMPTY_TASK_FILE", "record_id": task_type, "detail": ""})
    summary = {
        "stage": "G3",
        "status": "GATE_PASSED" if not issues else "BLOCKED",
        "input_g2_rows": len(clean_meta) + len({row["g2_row_id"] for row in all_hits}),
        "retained_rows": sum(counts_task.values()),
        "task_counts": dict(sorted(counts_task.items())),
        "excluded_exact_leak_rows": len({row["g2_row_id"] for row in all_hits}),
        "exact_leak_hits": len(all_hits),
        "generic_common_overlap_rows_routed_to_human_qa": len(common_overlap_row_ids),
        "generic_common_overlap_hits": len(common_overlap_hits),
        "deduplicated_rows": len(dropped),
        "query_linked_split_overrides": len(split_override),
        "detector_version": DETECTOR_VERSION,
        "public_prompt_forbidden_fields_present": False,
        "human_qa_completed": False,
    }
    write_json(reports_dir / "assembly_validation_summary.json", summary)
    write_csv(reports_dir / "assembly_validation_issues.csv", issues, ["severity", "code", "record_id", "detail"])
    assembly_text = "# Provisional assembly summary\n\n" + "\n".join(f"- {key}: {value}" for key, value in summary.items()) + "\n\nStatus is provisional; human-only QA is not complete.\n"
    (reports_dir / "assembly_summary.md").write_text(assembly_text, encoding="utf-8")
    write_json(output / "RUN_STATUS.json", {"stage": "G3", "status": summary["status"], "completed_at": datetime.now(timezone.utc).isoformat()})
    write_json(output / "COUNTS.json", summary)
    write_csv(output / "INPUT_MANIFEST.csv", [{"logical_name": key, "resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for key, path in sorted(paths.items())], ["logical_name", "resolved_path", "size_bytes", "sha256"])
    (output / "COMMANDS.log").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    (output / "RUN_CONFIG.yaml").write_text("stage: G3_provisional_assembly\nleakage_detector: exact_visible_surface_v1\ndedup: group_aware_semantic_v1\nhuman_qa_completed: false\n", encoding="utf-8")
    manifest = []
    for path in sorted((p for p in output.rglob("*") if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.as_posix()):
        manifest.append({"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest, ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
