#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.leakage import find_exact_surface, is_generic_common_surface  # noqa: E402
from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
from servicediscoverybench.reconstruction import validate_candidate_space  # noqa: E402

csv.field_size_limit(2_147_483_647)

TASK_TYPES = (
    "single_service_discovery", "single_api_recommendation", "multi_service_discovery",
    "multi_api_recommendation", "composable_service_discovery", "composable_api_recommendation",
)
EXPECTED_FIELDS = [
    "benchmark_task_id", "underlying_task_id", "paired_task_group_id", "split_group_id", "task_type",
    "prediction_target", "source_dataset", "source_subset", "query_text", "user_visible_context_json",
    "candidate_services_json", "candidate_apis_json", "gold_services_json", "gold_apis_json",
    "acceptable_gold_service_sets_json", "acceptable_gold_api_sets_json", "service_api_map_json",
    "dependency_graph_json", "candidate_count", "gold_count", "query_signature", "task_signature", "signature_version",
]
FORBIDDEN = {"reviewer_id", "final_decision", "failure_reason", "policy_label", "inherited_human_review_json", "repair_status"}


def load_jsonl(path: Path, field: str) -> dict[str, dict]:
    result = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                result[row[field]] = row
    return result


def api_surfaces(row: dict) -> set[str]:
    return set(filter(None, [row.get("canonical_name", ""), row.get("operation_id", ""), row.get("endpoint", "")]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--service-catalog", required=True)
    parser.add_argument("--api-catalog", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    candidate_root = Path(args.candidate_root).resolve()
    service_path = Path(args.service_catalog).resolve()
    api_path = Path(args.api_catalog).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    services = load_jsonl(service_path, "service_id")
    apis = load_jsonl(api_path, "api_id")

    paired_gold_apis: dict[str, set[str]] = defaultdict(set)
    for task_type in TASK_TYPES:
        path = candidate_root / "tasks" / f"{task_type}.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["prediction_target"] == "api":
                    paired_gold_apis[row["paired_task_group_id"]].update(json.loads(row["gold_apis_json"]))

    provenance_path = candidate_root / "manifests" / "task_provenance.csv"
    with provenance_path.open("r", encoding="utf-8-sig", newline="") as handle:
        provenance = {row["benchmark_task_id"]: row for row in csv.DictReader(handle)}
    evidence_path = candidate_root / "manifests" / "dependency_evidence.jsonl"
    evidence_ids = set()
    with evidence_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                evidence_ids.add(json.loads(line)["benchmark_task_id"])

    issues = []
    seen = set()
    counts = Counter()
    pair_state: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"underlying": set(), "split": set(), "types": set()})
    for task_type in TASK_TYPES:
        path = candidate_root / "tasks" / f"{task_type}.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != EXPECTED_FIELDS:
                issues.append({"severity": "ERROR", "code": "PUBLIC_SCHEMA_HEADER_MISMATCH", "record_id": task_type, "detail": json.dumps(reader.fieldnames)})
            if FORBIDDEN.intersection(reader.fieldnames or []):
                issues.append({"severity": "ERROR", "code": "FORBIDDEN_PUBLIC_FIELD", "record_id": task_type, "detail": json.dumps(sorted(FORBIDDEN.intersection(reader.fieldnames or [])))})
            for row in reader:
                task_id = row["benchmark_task_id"]
                if task_id in seen:
                    issues.append({"severity": "ERROR", "code": "DUPLICATE_BENCHMARK_TASK_ID", "record_id": task_id, "detail": ""})
                seen.add(task_id)
                if row["task_type"] != task_type:
                    issues.append({"severity": "ERROR", "code": "TASK_FILE_TYPE_MISMATCH", "record_id": task_id, "detail": row["task_type"]})
                expected_target = "service" if task_type.endswith("service_discovery") else "api"
                if row["prediction_target"] != expected_target:
                    issues.append({"severity": "ERROR", "code": "TARGET_TYPE_MISMATCH", "record_id": task_id, "detail": row["prediction_target"]})
                candidate_services, candidate_apis = json.loads(row["candidate_services_json"]), json.loads(row["candidate_apis_json"])
                gold_services, gold_apis = json.loads(row["gold_services_json"]), json.loads(row["gold_apis_json"])
                candidates = candidate_services if expected_target == "service" else candidate_apis
                gold = gold_services if expected_target == "service" else gold_apis
                valid, reason = validate_candidate_space(candidates, gold)
                if not valid:
                    issues.append({"severity": "ERROR", "code": "INVALID_CANDIDATE_SPACE", "record_id": task_id, "detail": reason})
                if int(row["candidate_count"]) != len(candidates) or int(row["gold_count"]) != len(gold):
                    issues.append({"severity": "ERROR", "code": "COUNT_MISMATCH", "record_id": task_id, "detail": ""})
                if (set(candidate_services) | set(gold_services)) - services.keys():
                    issues.append({"severity": "ERROR", "code": "UNKNOWN_SERVICE_ID", "record_id": task_id, "detail": ""})
                if (set(candidate_apis) | set(gold_apis)) - apis.keys():
                    issues.append({"severity": "ERROR", "code": "UNKNOWN_API_ID", "record_id": task_id, "detail": ""})
                parents = {apis[aid]["parent_service_id"] for aid in candidate_apis if aid in apis}
                if not parents.issubset(candidate_services):
                    issues.append({"severity": "ERROR", "code": "API_PARENT_MAPPING_INCOMPLETE", "record_id": task_id, "detail": json.dumps(sorted(parents - set(candidate_services)))})
                if expected_target == "service":
                    for sid in gold_services:
                        if find_exact_surface(row["query_text"], services[sid]["canonical_name"]) and not is_generic_common_surface(services[sid]["canonical_name"]):
                            issues.append({"severity": "ERROR", "code": "EXACT_SERVICE_LEAK", "record_id": task_id, "detail": sid})
                    for aid in paired_gold_apis.get(row["paired_task_group_id"], set()):
                        if any(find_exact_surface(row["query_text"], surface) and not is_generic_common_surface(surface) for surface in api_surfaces(apis[aid])):
                            issues.append({"severity": "ERROR", "code": "EXACT_API_CLUE_IN_SERVICE_TASK", "record_id": task_id, "detail": aid})
                else:
                    for aid in gold_apis:
                        if any(find_exact_surface(row["query_text"], surface) and not is_generic_common_surface(surface) for surface in api_surfaces(apis[aid])):
                            issues.append({"severity": "ERROR", "code": "EXACT_API_LEAK", "record_id": task_id, "detail": aid})
                if task_id not in provenance:
                    issues.append({"severity": "ERROR", "code": "MISSING_PROVENANCE", "record_id": task_id, "detail": ""})
                if task_type.startswith("composable_"):
                    if not json.loads(row["dependency_graph_json"]):
                        issues.append({"severity": "ERROR", "code": "EMPTY_COMPOSABLE_GRAPH", "record_id": task_id, "detail": ""})
                    if task_id not in evidence_ids:
                        issues.append({"severity": "ERROR", "code": "MISSING_COMPOSABLE_EVIDENCE", "record_id": task_id, "detail": ""})
                pair = pair_state[row["paired_task_group_id"]]
                pair["underlying"].add(row["underlying_task_id"])
                pair["split"].add(row["split_group_id"])
                pair["types"].add(task_type)
                counts[task_type] += 1

    for pair_id, state in pair_state.items():
        if len(state["underlying"]) != 1 or len(state["split"]) != 1:
            issues.append({"severity": "ERROR", "code": "PAIR_IDENTITY_INCONSISTENT", "record_id": pair_id, "detail": json.dumps({key: sorted(value) for key, value in state.items()})})
    for task_id in provenance.keys() - seen:
        issues.append({"severity": "ERROR", "code": "ORPHAN_PROVENANCE", "record_id": task_id, "detail": ""})
    summary = {
        "stage": "G3_independent_validation",
        "status": "GATE_PASSED" if not issues else "BLOCKED",
        "validated_rows": len(seen),
        "task_counts": dict(sorted(counts.items())),
        "errors": len(issues),
        "schema_headers_exact": not any(row["code"] == "PUBLIC_SCHEMA_HEADER_MISMATCH" for row in issues),
        "exact_leaks_remaining": sum(row["code"].startswith("EXACT_") for row in issues),
        "provenance_complete": not any("PROVENANCE" in row["code"] for row in issues),
    }
    write_json(output / "VALIDATION_SUMMARY.json", summary)
    write_csv(output / "VALIDATION_ISSUES.csv", issues, ["severity", "code", "record_id", "detail"])
    inputs = [candidate_root / "tasks" / f"{name}.csv" for name in TASK_TYPES] + [provenance_path, evidence_path, service_path, api_path]
    write_csv(output / "INPUT_MANIFEST.csv", [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in inputs], ["resolved_path", "size_bytes", "sha256"])
    manifest = []
    for path in sorted((p for p in output.iterdir() if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.name):
        manifest.append({"relative_path": path.name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest, ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
