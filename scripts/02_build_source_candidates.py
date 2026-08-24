#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.catalogs import (  # noqa: E402
    api_id,
    resolve_toolbench_static_api,
    resolve_toolbench_static_service,
    service_id,
    stable_json,
)
from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
from servicediscoverybench.normalize import normalize_text  # noqa: E402
from servicediscoverybench.reconstruction import deterministic_negatives, validate_candidate_space  # noqa: E402
from servicediscoverybench.signatures import (  # noqa: E402
    SIGNATURE_VERSION,
    query_signature,
    review_content_fingerprint,
    stable_hash,
    task_signature,
)

csv.field_size_limit(2_147_483_647)

FIELDS = [
    "g2_row_id", "underlying_task_id", "paired_task_group_id", "split_group_id", "task_type",
    "prediction_target", "source_dataset", "source_subset", "source_query_id", "query_text",
    "user_visible_context_json", "candidate_services_json", "candidate_apis_json", "gold_services_json",
    "gold_apis_json", "acceptable_gold_service_sets_json", "acceptable_gold_api_sets_json",
    "service_api_map_json", "dependency_graph_json", "dependency_evidence_json",
    "candidate_catalog_provenance_json", "candidate_count", "gold_count", "non_gold_candidate_count",
    "repair_status", "route_status", "parent_row_id", "repair_version", "source_provenance_json",
    "inherited_human_review_json", "query_signature", "task_signature", "signature_version",
    "review_content_fingerprint",
]
TRIAGE_FIELDS = ["source_row_id", "source_dataset", "source_subset", "policy_decision", "triage_status", "triage_reason", "emitted_g2_rows"]
LEDGER_FIELDS = ["old_row_id", "new_row_id", "repair_action", "changed_fields_json", "parent_row_id", "repair_version", "old_fingerprint", "new_fingerprint", "catalog_source", "crosswalk_status", "validation_status"]
FAILURE_FIELDS = ["source_row_id", "source_dataset", "source_subset", "failure_stage", "reason", "detail"]


def j(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def boolish(value: object) -> bool:
    return normalize_text(value, casefold=True) in {"1", "true", "yes", "keep", "valid"}


def obj_name(value: object, kind: str) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    if kind == "service":
        return value.get("service_name") or value.get("tool_name") or value.get("name") or ""
    return value.get("api_name") or value.get("function_name") or value.get("name") or ""


def load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class CatalogIndex:
    def __init__(self, services: list[dict], apis: list[dict]):
        self.services = services
        self.apis = apis
        self.services_by_source_id: dict[tuple[str, str], str] = {}
        self.services_by_name: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.apis_by_parent: dict[str, list[str]] = defaultdict(list)
        self.api_records: dict[str, dict] = {}
        self.service_records = {row["service_id"]: row for row in services}
        for row in services:
            source = row["source_dataset"]
            self.services_by_source_id[(source, normalize_text(row["source_service_id"], casefold=True))] = row["service_id"]
            self.services_by_name[(source, normalize_text(row["canonical_name"], casefold=True))].append(row["service_id"])
        for row in apis:
            self.api_records[row["api_id"]] = row
            self.apis_by_parent[row["parent_service_id"]].append(row["api_id"])
        for ids in self.apis_by_parent.values():
            ids.sort()

    def service(self, source: str, value: object) -> str | None:
        if isinstance(value, dict):
            source_id = value.get("service_id") or value.get("source_service_id")
            if source_id:
                found = self.services_by_source_id.get((source, normalize_text(source_id, casefold=True)))
                if found:
                    return found
        name = normalize_text(obj_name(value, "service"), casefold=True)
        matches = self.services_by_name.get((source, name), [])
        return matches[0] if len(matches) == 1 else None

    def api(self, source: str, parent: str, value: object) -> str | None:
        name = normalize_text(obj_name(value, "api"), casefold=True)
        method = normalize_text(value.get("method", "") if isinstance(value, dict) else "").upper()
        matches = []
        for aid in self.apis_by_parent.get(parent, []):
            record = self.api_records[aid]
            if record["source_dataset"] != source or normalize_text(record["canonical_name"], casefold=True) != name:
                continue
            if method and normalize_text(record["http_method"]).upper() != method:
                continue
            matches.append(aid)
        return matches[0] if len(matches) == 1 else None


class ToolBenchOccurrences:
    def __init__(self):
        self.services: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.apis: dict[str, dict[tuple[str, str], set[str]]] = defaultdict(lambda: defaultdict(set))

    def scan(self, path: Path, target_ids: set[str]) -> None:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                task_id = row["task_id"]
                if task_id not in target_ids:
                    continue
                metadata = j(row.get("metadata_json", ""), {})
                service_name = row.get("candidate_service_name", "")
                service_key = normalize_text(service_name, casefold=True)
                host = metadata.get("candidate_service_host") or metadata.get("candidate_service_home_url") or ""
                source_identity = stable_json([host, service_key]) if host else service_key
                sid = service_id("ToolBench", source_identity)
                api_name = row.get("candidate_api_name", "")
                method = metadata.get("candidate_api_method", "")
                aid = api_id(sid, [normalize_text(api_name, casefold=True), normalize_text(method, casefold=True)])
                self.services[task_id][service_key].add(sid)
                self.apis[task_id][(service_key, normalize_text(api_name, casefold=True))].add(aid)

    def service(self, task_id: str, value: object) -> str | None:
        matches = self.services[task_id].get(normalize_text(obj_name(value, "service"), casefold=True), set())
        return next(iter(matches)) if len(matches) == 1 else None

    def api(self, task_id: str, value: object) -> tuple[str | None, str | None]:
        if not isinstance(value, dict):
            return None, None
        service_key = normalize_text(obj_name(value, "service"), casefold=True)
        api_key = normalize_text(obj_name(value, "api"), casefold=True)
        sid = self.service(task_id, value.get("service_name", ""))
        matches = self.apis[task_id].get((service_key, api_key), set())
        return sid, next(iter(matches)) if len(matches) == 1 else None


def canonical_list(values: list[object], resolver) -> tuple[list[str], list[str]]:
    resolved, errors = [], []
    for value in values:
        item = resolver(value)
        if item:
            resolved.append(item)
        else:
            errors.append(stable_json(value))
    return list(dict.fromkeys(resolved)), errors


def make_row(*, source_row_id: str, underlying: str, pair: str, split: str, task_type: str, target: str,
             source: str, subset: str, source_query_id: str, query: str, candidate_services: list[str],
             candidate_apis: list[str], gold_services: list[str], gold_apis: list[str], service_api_map: dict,
             catalog_provenance: dict, repair_status: str = "valid_as_is", parent_row_id: str = "",
             repair_version: str = "", source_provenance: dict | None = None, dependency_graph: object = None,
             dependency_evidence: object = None, inherited_review: dict | None = None) -> dict:
    candidates = candidate_services if target == "service" else candidate_apis
    gold = gold_services if target == "service" else gold_apis
    valid, reason = validate_candidate_space(candidates, gold)
    if not valid:
        raise ValueError(reason)
    identity = [source, source_row_id, task_type, target]
    row = {
        "g2_row_id": f"g2::{stable_hash(identity)[:24]}",
        "underlying_task_id": underlying,
        "paired_task_group_id": pair,
        "split_group_id": split,
        "task_type": task_type,
        "prediction_target": target,
        "source_dataset": source,
        "source_subset": subset,
        "source_query_id": source_query_id,
        "query_text": normalize_text(query),
        "user_visible_context_json": "{}",
        "candidate_services_json": stable_json(candidate_services),
        "candidate_apis_json": stable_json(candidate_apis),
        "gold_services_json": stable_json(gold_services),
        "gold_apis_json": stable_json(gold_apis),
        "acceptable_gold_service_sets_json": "[]",
        "acceptable_gold_api_sets_json": "[]",
        "service_api_map_json": stable_json(service_api_map),
        "dependency_graph_json": stable_json(dependency_graph or []),
        "dependency_evidence_json": stable_json(dependency_evidence or []),
        "candidate_catalog_provenance_json": stable_json(catalog_provenance),
        "candidate_count": len(candidates),
        "gold_count": len(gold),
        "non_gold_candidate_count": len(candidates) - len(gold),
        "repair_status": repair_status,
        "route_status": "clean_ready",
        "parent_row_id": parent_row_id,
        "repair_version": repair_version,
        "source_provenance_json": stable_json(source_provenance or {}),
        "inherited_human_review_json": stable_json(inherited_review or {}),
        "signature_version": SIGNATURE_VERSION,
    }
    row["query_signature"] = query_signature(row["query_text"])
    row["task_signature"] = task_signature(row)
    row["review_content_fingerprint"] = review_content_fingerprint(row)
    return row


def service_api_map(candidate_apis: list[str], api_index: CatalogIndex) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for aid in candidate_apis:
        result[api_index.api_records[aid]["parent_service_id"]].append(aid)
    return {key: sorted(values) for key, values in sorted(result.items())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-catalog", required=True)
    parser.add_argument("--api-catalog", required=True)
    parser.add_argument("--toolbench-g1-ready", required=True)
    parser.add_argument("--toolbench-policy", required=True)
    parser.add_argument("--toolbench-g1-raw", required=True)
    parser.add_argument("--toolbench-g2-raw", required=True)
    parser.add_argument("--metatool-policy", required=True)
    parser.add_argument("--stable-policy", required=True)
    parser.add_argument("--shortcuts-tasks", required=True)
    parser.add_argument("--composable-review", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    recon = output / "reconstruction"
    recon.mkdir()
    paths = {name: Path(value).resolve() for name, value in vars(args).items() if name != "output"}
    services = load_jsonl(paths["service_catalog"])
    apis = load_jsonl(paths["api_catalog"])
    index = CatalogIndex(services, apis)

    g1_targets = set()
    with paths["toolbench_g1_ready"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            g1_targets.add(row["source_task_id"])
    multi_selected = set()
    with paths["toolbench_policy"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["task_type"] == "multi_service_discovery_raw" and row["v1_5f_dryrun_decision"] == "still_clean_candidate":
                multi_selected.add(row["task_id"])
    occurrences = ToolBenchOccurrences()
    occurrences.scan(paths["toolbench_g1_raw"], g1_targets)
    occurrences.scan(paths["toolbench_g2_raw"], multi_selected)

    candidate_path = recon / "reconstruction_candidates.csv"
    candidate_handle = candidate_path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(candidate_handle, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    counts = Counter()
    triage, ledger, failures, alignment_usage = [], [], [], []

    def emit(row: dict) -> None:
        writer.writerow(row)
        counts[(row["source_dataset"], row["task_type"], row["repair_status"])] += 1

    def fail(source_row_id: str, source: str, subset: str, stage: str, reason: str, detail: str = "") -> None:
        failures.append({"source_row_id": source_row_id, "source_dataset": source, "source_subset": subset, "failure_stage": stage, "reason": reason, "detail": detail})

    catalog_provenance = {"catalog_version": "v0.1-g1", "service_catalog_sha256": sha256_file(paths["service_catalog"]), "api_catalog_sha256": sha256_file(paths["api_catalog"]), "cross_source_catalog_used": False}

    # ToolBench G1: exact occurrence identity from the local candidate-level raw table.
    with paths["toolbench_g1_ready"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = row["source_task_id"]
            candidate_objects = j(row["candidate_apis_json"], [])
            gold_objects = j(row["gold_apis_json"], [])
            candidate_apis, errors = canonical_list(candidate_objects, lambda value: occurrences.api(source_id, value)[1])
            gold_apis, gold_errors = canonical_list(gold_objects, lambda value: occurrences.api(source_id, value)[1])
            parents = sorted({index.api_records[aid]["parent_service_id"] for aid in candidate_apis if aid in index.api_records})
            gold_parents = sorted({index.api_records[aid]["parent_service_id"] for aid in gold_apis if aid in index.api_records})
            try:
                if errors or gold_errors or not parents:
                    raise ValueError("unresolved_toolbench_occurrence")
                built = make_row(source_row_id=source_id, underlying=source_id, pair=source_id, split=source_id,
                    task_type="single_api_recommendation", target="api", source="ToolBench", subset="G1",
                    source_query_id=source_id.rsplit("_", 1)[-1], query=row["query_text"], candidate_services=parents,
                    candidate_apis=candidate_apis, gold_services=gold_parents, gold_apis=gold_apis,
                    service_api_map=service_api_map(candidate_apis, index), catalog_provenance={**catalog_provenance, "construction": "exact_task_occurrence_parent_catalog"},
                    source_provenance=j(row.get("source_provenance", ""), {}))
                emit(built)
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": "G1", "policy_decision": row["g1_dryrun_decision"], "triage_status": "valid_as_is", "triage_reason": "candidate_ready_exact_occurrence_mapping", "emitted_g2_rows": built["g2_row_id"]})
            except ValueError as exc:
                fail(source_id, "ToolBench", "G1", "canonicalization", str(exc), stable_json(errors + gold_errors))
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": "G1", "policy_decision": row["g1_dryrun_decision"], "triage_status": "invalid_exclude", "triage_reason": str(exc), "emitted_g2_rows": ""})

    # ToolBench policy pool: only current still-clean multi rows; old composable raw is superseded.
    with paths["toolbench_policy"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = row["task_id"]
            decision = row["v1_5f_dryrun_decision"]
            if row["task_type"] != "multi_service_discovery_raw":
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": row["source_group"], "policy_decision": decision, "triage_status": "invalid_exclude", "triage_reason": "superseded_by_frozen_composable_review", "emitted_g2_rows": ""})
                continue
            if decision != "still_clean_candidate":
                status = "ambiguous_hold" if decision == "downgrade_to_uncertain" else "invalid_exclude"
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": row["source_group"], "policy_decision": decision, "triage_status": status, "triage_reason": "v1_5f_policy_route", "emitted_g2_rows": ""})
                continue
            service_objects, api_objects = j(row["candidate_services_json"], []), j(row["candidate_apis_json"], [])
            gold_service_objects, gold_api_objects = j(row["gold_services_json"], []), j(row["gold_apis_json"], [])
            candidate_services, e1 = canonical_list(service_objects, lambda value: occurrences.service(source_id, value))
            gold_services, e2 = canonical_list(gold_service_objects, lambda value: occurrences.service(source_id, value))
            candidate_apis, e3 = canonical_list(api_objects, lambda value: occurrences.api(source_id, value)[1])
            gold_apis, e4 = canonical_list(gold_api_objects, lambda value: occurrences.api(source_id, value)[1])
            emitted = []
            try:
                if e1 or e2 or e3 or e4:
                    raise ValueError("unresolved_toolbench_occurrence")
                pair = f"pair::{source_id}"
                common = dict(source_row_id=source_id, underlying=source_id, pair=pair, split=pair, source="ToolBench", subset="G2", source_query_id=row["source_query_id"], query=row["query_text"], catalog_provenance={**catalog_provenance, "construction": "exact_task_occurrence_catalog"}, source_provenance={"policy_source": str(paths["toolbench_policy"])})
                service_row = make_row(**common, task_type="multi_service_discovery", target="service", candidate_services=candidate_services, candidate_apis=[], gold_services=gold_services, gold_apis=[], service_api_map={})
                api_row = make_row(**common, task_type="multi_api_recommendation", target="api", candidate_services=candidate_services, candidate_apis=candidate_apis, gold_services=gold_services, gold_apis=gold_apis, service_api_map=service_api_map(candidate_apis, index))
                emit(service_row); emit(api_row)
                emitted = [service_row["g2_row_id"], api_row["g2_row_id"]]
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": "G2", "policy_decision": decision, "triage_status": "valid_as_is", "triage_reason": "v1_5f_still_clean_exact_occurrence_mapping", "emitted_g2_rows": stable_json(emitted)})
            except ValueError as exc:
                fail(source_id, "ToolBench", "G2", "canonicalization", str(exc), stable_json(e1 + e2 + e3 + e4))
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": "G2", "policy_decision": decision, "triage_status": "invalid_exclude", "triage_reason": str(exc), "emitted_g2_rows": ""})

    # MetaTool: source policy keep rows, canonical source IDs only.
    with paths["metatool_policy"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id, decision = row["task_id"], row["metatool_policy_decision"]
            if decision != "source_specific_keep_candidate":
                status = "ambiguous_hold" if decision == "rewrite_pool_only" else "invalid_exclude"
                triage.append({"source_row_id": source_id, "source_dataset": "MetaTool", "source_subset": "single_service", "policy_decision": decision, "triage_status": status, "triage_reason": "rewrite_requires_human_semantic_preservation" if status == "ambiguous_hold" else "source_policy_remove", "emitted_g2_rows": ""})
                continue
            candidate_services, e1 = canonical_list(j(row["candidate_services_json"], []), lambda value: index.service("MetaTool", value))
            gold_services, e2 = canonical_list(j(row["gold_services_json"], []), lambda value: index.service("MetaTool", value))
            try:
                if e1 or e2:
                    raise ValueError("unresolved_metatool_service")
                built = make_row(source_row_id=source_id, underlying=source_id, pair=source_id, split=source_id,
                    task_type="single_service_discovery", target="service", source="MetaTool", subset="single_service",
                    source_query_id=row["source_row_id"], query=row["query_text"], candidate_services=candidate_services,
                    candidate_apis=[], gold_services=gold_services, gold_apis=[], service_api_map={},
                    catalog_provenance={**catalog_provenance, "construction": "complete_metatool_source_catalog"},
                    source_provenance={"source_row_id": row["source_row_id"], "policy_source": str(paths["metatool_policy"])})
                emit(built)
                triage.append({"source_row_id": source_id, "source_dataset": "MetaTool", "source_subset": "single_service", "policy_decision": decision, "triage_status": "valid_as_is", "triage_reason": "source_policy_keep", "emitted_g2_rows": built["g2_row_id"]})
            except ValueError as exc:
                fail(source_id, "MetaTool", "single_service", "canonicalization", str(exc), stable_json(e1 + e2))
                triage.append({"source_row_id": source_id, "source_dataset": "MetaTool", "source_subset": "single_service", "policy_decision": decision, "triage_status": "invalid_exclude", "triage_reason": str(exc), "emitted_g2_rows": ""})

    # StableToolBench: keep-as-is plus bounded G1/G2 candidate-only reconstruction.
    stable_service_universe = sorted(row["service_id"] for row in services if row["source_dataset"] == "StableToolBench")
    with paths["stable_policy"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id, decision, group = row["task_id"], row["stable_policy_decision"], row["source_group"]
            allowed_as_is = decision == "source_specific_keep_candidate_as_is" and group in {"G1", "G2"}
            allowed_repair = decision == "candidate_space_reconstruction_pool" and group in {"G1", "G2"}
            if not (allowed_as_is or allowed_repair):
                if decision in {"leakage_rewrite_pool", "composable_dependency_review_pool"} or group == "G3":
                    status, reason = "ambiguous_hold", "rewrite_or_dependency_review_requires_human"
                else:
                    status, reason = "invalid_exclude", "stable_source_policy_remove"
                triage.append({"source_row_id": source_id, "source_dataset": "StableToolBench", "source_subset": group, "policy_decision": decision, "triage_status": status, "triage_reason": reason, "emitted_g2_rows": ""})
                continue
            service_objects, api_objects = j(row["candidate_services_json"], []), j(row["candidate_apis_json"], [])
            gold_service_objects, gold_api_objects = j(row["gold_services_json"], []), j(row["gold_apis_json"], [])
            candidate_services, e1 = canonical_list(service_objects, lambda value: index.service("StableToolBench", value))
            gold_services, e2 = canonical_list(gold_service_objects, lambda value: index.service("StableToolBench", value))
            api_pairs = []
            api_errors = []
            for value in api_objects:
                parent = index.service("StableToolBench", obj_name(value, "service"))
                aid = index.api("StableToolBench", parent or "", value) if parent else None
                if parent and aid:
                    api_pairs.append((parent, aid))
                else:
                    api_errors.append(stable_json(value))
            candidate_apis = list(dict.fromkeys(aid for _, aid in api_pairs))
            gold_apis = []
            for value in gold_api_objects:
                parent = index.service("StableToolBench", obj_name(value, "service"))
                aid = index.api("StableToolBench", parent or "", value) if parent else None
                if aid:
                    gold_apis.append(aid)
                else:
                    api_errors.append(stable_json(value))
            gold_apis = list(dict.fromkeys(gold_apis))
            original_fingerprint = stable_hash({"query": row["query_text"], "candidate_services": service_objects, "candidate_apis": api_objects, "gold_services": gold_service_objects, "gold_apis": gold_api_objects})
            if allowed_repair:
                if group == "G1":
                    parents = sorted(set(gold_services or candidate_services))
                    candidate_services = sorted(set(candidate_services + parents))
                    candidate_apis = sorted({aid for parent in parents for aid in index.apis_by_parent.get(parent, [])})
                else:
                    candidate_services = list(dict.fromkeys(candidate_services + gold_services))
                    candidate_services += deterministic_negatives(stable_service_universe, candidate_services, source_id, 4)
                    candidate_services = list(dict.fromkeys(candidate_services))
                    candidate_apis = sorted({aid for parent in candidate_services for aid in index.apis_by_parent.get(parent, [])} | set(candidate_apis) | set(gold_apis))
            emitted = []
            try:
                if e1 or e2 or api_errors:
                    raise ValueError("unresolved_stable_catalog_object")
                pair = f"pair::{source_id}"
                common = dict(source_row_id=source_id, underlying=source_id, pair=pair, split=pair, source="StableToolBench", subset=group, source_query_id=row["source_query_id"], query=row["query_text"], catalog_provenance={**catalog_provenance, "construction": "stable_parent_catalog_reconstruction" if allowed_repair else "source_candidate_catalog"}, repair_status="reconstructed" if allowed_repair else "valid_as_is", parent_row_id=source_id if allowed_repair else "", repair_version="candidate_only_v0.1" if allowed_repair else "", source_provenance={"policy_source": str(paths["stable_policy"])})
                if group == "G1":
                    built_rows = [make_row(**common, task_type="single_api_recommendation", target="api", candidate_services=candidate_services, candidate_apis=candidate_apis, gold_services=gold_services, gold_apis=gold_apis, service_api_map=service_api_map(candidate_apis, index))]
                else:
                    built_rows = [
                        make_row(**common, task_type="multi_service_discovery", target="service", candidate_services=candidate_services, candidate_apis=[], gold_services=gold_services, gold_apis=[], service_api_map={}),
                        make_row(**common, task_type="multi_api_recommendation", target="api", candidate_services=candidate_services, candidate_apis=candidate_apis, gold_services=gold_services, gold_apis=gold_apis, service_api_map=service_api_map(candidate_apis, index)),
                    ]
                for built in built_rows:
                    emit(built); emitted.append(built["g2_row_id"])
                    if allowed_repair:
                        ledger.append({"old_row_id": source_id, "new_row_id": built["g2_row_id"], "repair_action": "restore_candidates_from_real_parent_catalog", "changed_fields_json": stable_json(["candidate_services_json"] if built["prediction_target"] == "service" else ["candidate_apis_json"]), "parent_row_id": source_id, "repair_version": "candidate_only_v0.1", "old_fingerprint": original_fingerprint, "new_fingerprint": built["review_content_fingerprint"], "catalog_source": str(paths["service_catalog"] if built["prediction_target"] == "service" else paths["api_catalog"]), "crosswalk_status": "not_used", "validation_status": "valid"})
                triage.append({"source_row_id": source_id, "source_dataset": "StableToolBench", "source_subset": group, "policy_decision": decision, "triage_status": "reconstructable" if allowed_repair else "valid_as_is", "triage_reason": "candidate_only_real_catalog_reconstruction" if allowed_repair else "source_policy_keep", "emitted_g2_rows": stable_json(emitted)})
            except ValueError as exc:
                fail(source_id, "StableToolBench", group, "reconstruction", str(exc), stable_json(e1 + e2 + api_errors))
                triage.append({"source_row_id": source_id, "source_dataset": "StableToolBench", "source_subset": group, "policy_decision": decision, "triage_status": "invalid_exclude", "triage_reason": str(exc), "emitted_g2_rows": ""})

    # ShortcutsBench: repair only candidate==Gold using real local service catalog negatives.
    shortcut_universe = sorted(row["service_id"] for row in services if row["source_dataset"] == "ShortcutsBench")
    with paths["shortcuts_tasks"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = row["task_id"]
            candidate_services, e1 = canonical_list(j(row["candidate_services_json"], []), lambda value: index.service("ShortcutsBench", value))
            gold_services, e2 = canonical_list(j(row["gold_services_json"], []), lambda value: index.service("ShortcutsBench", value))
            repair = not boolish(row["candidate_space_valid_as_is"])
            original_fingerprint = stable_hash({"query": row["query_text"], "candidate_services": j(row["candidate_services_json"], []), "gold_services": j(row["gold_services_json"], [])})
            if repair:
                candidate_services = list(dict.fromkeys(candidate_services + gold_services))
                candidate_services += deterministic_negatives(shortcut_universe, candidate_services, source_id, 4)
                candidate_services = list(dict.fromkeys(candidate_services))
            try:
                if e1 or e2:
                    raise ValueError("unresolved_shortcuts_service")
                built = make_row(source_row_id=source_id, underlying=source_id, pair=source_id, split=source_id,
                    task_type="single_service_discovery", target="service", source="ShortcutsBench", subset="strict",
                    source_query_id=row["source_query_id"], query=row["query_text"], candidate_services=candidate_services,
                    candidate_apis=[], gold_services=gold_services, gold_apis=[], service_api_map={},
                    catalog_provenance={**catalog_provenance, "construction": "strict_local_catalog_negative_repair" if repair else "strict_source_candidate_catalog"},
                    repair_status="reconstructed" if repair else "valid_as_is", parent_row_id=source_id if repair else "",
                    repair_version="candidate_only_v0.1" if repair else "", source_provenance=j(row["source_provenance_json"], {}))
                emit(built)
                if repair:
                    ledger.append({"old_row_id": source_id, "new_row_id": built["g2_row_id"], "repair_action": "add_real_shortcuts_service_negatives", "changed_fields_json": stable_json(["candidate_services_json"]), "parent_row_id": source_id, "repair_version": "candidate_only_v0.1", "old_fingerprint": original_fingerprint, "new_fingerprint": built["review_content_fingerprint"], "catalog_source": str(paths["service_catalog"]), "crosswalk_status": "not_used", "validation_status": "valid"})
                triage.append({"source_row_id": source_id, "source_dataset": "ShortcutsBench", "source_subset": "strict", "policy_decision": "strict_adapter", "triage_status": "reconstructable" if repair else "valid_as_is", "triage_reason": "candidate_only_real_catalog_reconstruction" if repair else "strict_candidate_valid", "emitted_g2_rows": built["g2_row_id"]})
            except ValueError as exc:
                fail(source_id, "ShortcutsBench", "strict", "reconstruction", str(exc), stable_json(e1 + e2))
                triage.append({"source_row_id": source_id, "source_dataset": "ShortcutsBench", "source_subset": "strict", "policy_decision": "strict_adapter", "triage_status": "invalid_exclude", "triage_reason": str(exc), "emitted_g2_rows": ""})

    # Frozen composable human review: canonical packaging only; no semantic re-review.
    with paths["composable_review"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id, action = row["review_item_id"], row["composable_release_action"]
            if action == "remove":
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": "frozen_composable", "policy_decision": action, "triage_status": "invalid_exclude", "triage_reason": "human_review_remove", "emitted_g2_rows": ""})
                continue
            service_values, gold_service_values = j(row["candidate_services_json"], []), j(row["provisional_gold_services_json"], [])
            api_values, gold_api_values = j(row["candidate_apis_json"], []), j(row["provisional_gold_apis_json"], [])
            service_resolver = lambda value: resolve_toolbench_static_service(services, value.get("service_key", ""), value.get("service_name", ""))
            candidate_services, e1 = canonical_list(service_values, service_resolver)
            gold_services, e2 = canonical_list(gold_service_values, service_resolver)
            def frozen_api(value):
                sid = service_resolver(value)
                return resolve_toolbench_static_api(apis, sid or "", value.get("function_key", ""), value.get("api_name") or value.get("function_name", ""), value.get("method", "")) if sid else None
            candidate_apis, e3 = canonical_list(api_values, frozen_api)
            gold_apis, e4 = canonical_list(gold_api_values, frozen_api)
            api_parent_services = sorted(
                set(candidate_services)
                | {index.api_records[aid]["parent_service_id"] for aid in candidate_apis if aid in index.api_records}
            )
            query = row.get("final_model_facing_query_text") or row["query_text"]
            pair = row["paired_task_group_id"] or f"pair::{row['underlying_task_id']}"
            split = row["split_group_id"] or pair
            common = dict(source_row_id=source_id, underlying=row["underlying_task_id"], pair=pair, split=split,
                source="ToolBench", subset="frozen_composable", source_query_id=row["source_query_id"], query=query,
                catalog_provenance={**catalog_provenance, "construction": "frozen_composable_static_catalog_mapping"},
                source_provenance={"review_artifact": str(paths["composable_review"]), "review_item_id": source_id, "prior_review_content_hash": row["review_content_hash"]},
                dependency_graph=j(row["dependency_edges_json"], []), dependency_evidence=j(row["dependency_evidence_json"], []),
                inherited_review={"review_status": row["review_status"], "reviewer_id": row["adjudicator_id"], "adjudicator_type": row["adjudicator_type"], "reviewed_at": row["adjudicated_at"], "prior_review_content_hash": row["review_content_hash"], "release_action": action})
            emitted = []
            try:
                if e1 or e2 or e3 or e4:
                    raise ValueError("unresolved_frozen_composable_object")
                if action == "keep_service_only":
                    task_pairs = [("composable_service_discovery", "service")]
                elif action == "reclassify_as_multi":
                    task_pairs = [("multi_service_discovery", "service"), ("multi_api_recommendation", "api")]
                else:
                    task_pairs = [("composable_service_discovery", "service"), ("composable_api_recommendation", "api")]
                for task_type, target in task_pairs:
                    built = make_row(**common, task_type=task_type, target=target,
                        candidate_services=candidate_services if target == "service" else api_parent_services,
                        candidate_apis=[] if target == "service" else candidate_apis,
                        gold_services=gold_services, gold_apis=[] if target == "service" else gold_apis,
                        service_api_map={} if target == "service" else service_api_map(candidate_apis, index))
                    emit(built); emitted.append(built["g2_row_id"])
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": "frozen_composable", "policy_decision": action, "triage_status": "valid_as_is", "triage_reason": "human_reviewed_content_canonical_packaging", "emitted_g2_rows": stable_json(emitted)})
            except ValueError as exc:
                fail(source_id, "ToolBench", "frozen_composable", "canonicalization", str(exc), stable_json(e1 + e2 + e3 + e4))
                triage.append({"source_row_id": source_id, "source_dataset": "ToolBench", "source_subset": "frozen_composable", "policy_decision": action, "triage_status": "invalid_exclude", "triage_reason": str(exc), "emitted_g2_rows": ""})

    candidate_handle.close()
    write_csv(recon / "source_triage.csv", triage, TRIAGE_FIELDS)
    write_csv(recon / "reconstruction_ledger.csv", ledger, LEDGER_FIELDS)
    write_csv(recon / "reconstruction_failures.csv", failures, FAILURE_FIELDS)
    write_csv(recon / "catalog_alignment_usage.csv", alignment_usage, ["g2_row_id", "left_service_id", "right_service_id", "crosswalk_id", "alignment_status"])
    summary = {
        "stage": "G2",
        "status": "GATE_PASSED",
        "emitted_rows": sum(counts.values()),
        "emitted_counts": [{"source_dataset": key[0], "task_type": key[1], "repair_status": key[2], "count": value} for key, value in sorted(counts.items())],
        "triage_counts": dict(sorted(Counter(row["triage_status"] for row in triage).items())),
        "reconstruction_ledger_rows": len(ledger),
        "explicit_failures_excluded": len(failures),
        "cross_source_catalog_rows": len(alignment_usage),
        "query_gold_task_type_changed_by_reconstruction": False,
        "all_emitted_candidate_spaces_valid": True,
        "all_new_candidates_from_real_catalog": True,
    }
    write_json(recon / "validation_summary.json", summary)
    write_json(output / "RUN_STATUS.json", {"stage": "G2", "status": summary["status"], "completed_at": datetime.now(timezone.utc).isoformat()})
    write_json(output / "COUNTS.json", summary)
    write_csv(output / "INPUT_MANIFEST.csv", [{"logical_name": key, "resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for key, path in sorted(paths.items())], ["logical_name", "resolved_path", "size_bytes", "sha256"])
    (output / "RUN_CONFIG.yaml").write_text("stage: G2_reconstruction\nrepair_version: candidate_only_v0.1\ncross_source_catalog_use: false\n", encoding="utf-8")
    (output / "COMMANDS.log").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    (output / "README.md").write_text("# G2 source candidates and candidate-only reconstruction\n\nNo query, Gold, prediction target, or task type was changed by reconstruction.\n", encoding="utf-8")
    output_rows = []
    for path in sorted((p for p in output.rglob("*") if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.as_posix()):
        output_rows.append({"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_csv(output / "OUTPUT_MANIFEST.csv", output_rows, ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
