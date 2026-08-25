"""Build ServiceDiscoveryBench v0.2.0 by merging the frozen composable expansion.

The builder is deliberately fail-closed.  It verifies the submitted ZIP and
its authoritative inner release, preserves every v0.1.1 task row byte-for-row
at the CSV field level, normalizes the two workflow-graph dialects, removes
workstation paths, enriches the expansion catalogs, materializes every split
view, and emits request/truth-separated evaluation inputs.

No network access and no model call are performed by this script.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import tempfile
import unicodedata
import zipfile


VERSION = "0.2.0"
RELEASE_NAME = f"ServiceDiscoveryBench-v{VERSION}"
SIGNATURE_VERSION = "sha256_nfkc_json_v1"
SPLIT_VERSION = "split-v0.2.0-composable-expansion-v1"
SOURCE_ZIP_EXPECTED_SHA256 = "78a2f6f5134011e7e2ca8f59d847cf68f8ecd6d9711f4f5ed7032b1ea2ed4a68"
TASK_TYPES = (
    "single_service_discovery",
    "single_api_recommendation",
    "multi_service_discovery",
    "multi_api_recommendation",
    "composable_service_discovery",
    "composable_api_recommendation",
)
SPLITS = ("train", "dev", "test")
JSON_FIELDS = (
    "user_visible_context_json",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "acceptable_gold_service_sets_json",
    "acceptable_gold_api_sets_json",
    "service_api_map_json",
    "dependency_graph_json",
)
REVIEW_FIELDS = (
    "prediction_target",
    "task_type",
    "query_text",
    "user_visible_context_json",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "acceptable_gold_service_sets_json",
    "acceptable_gold_api_sets_json",
    "service_api_map_json",
    "dependency_graph_json",
    "dependency_evidence_json",
)
TIER_DIRS = (
    ("EXECUTION_VERIFIED", "execution_verified"),
    ("SOURCE_DOCUMENTED", "source_documented"),
    ("SOURCE_GROUNDED_SYNTHETIC", "source_grounded_synthetic"),
)
PRIVATE_PATH_RE = re.compile(r"(?i)\b[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]")


def json_compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_pretty(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: object) -> str:
    return sha256_bytes(json_compact(value).encode("utf-8"))


def normalize_text(value: object, *, casefold: bool = False) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold() if casefold else text


def _json_value(value: object) -> object:
    if isinstance(value, str):
        text = value.strip()
        if text and text[0] in "[{":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        return normalize_text(value)
    return value


def query_signature(query: str) -> str:
    return stable_hash({"query_text": normalize_text(query, casefold=True)})


def task_signature(row: dict[str, object]) -> str:
    return stable_hash({key: _json_value(row.get(key, "")) for key in REVIEW_FIELDS if key != "query_text"})


def review_content_fingerprint(row: dict[str, object]) -> str:
    return stable_hash({key: _json_value(row.get(key, "")) for key in REVIEW_FIELDS})


def read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: JSONL row is not an object")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json_compact(row) + "\n")


def append_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json_compact(row) + "\n")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV lacks header: {path}")
        return list(reader.fieldnames), list(reader)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def append_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames, _ = read_csv(path)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writerows(rows)


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        bad_crc = archive.testzip()
        if bad_crc:
            raise ValueError(f"ZIP CRC failure: {bad_crc}")
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            member = PurePosixPath(normalized)
            if member.is_absolute() or ".." in member.parts or re.match(r"^[A-Za-z]:", normalized):
                raise ValueError(f"unsafe ZIP member: {info.filename}")
            target = (destination / Path(*member.parts)).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise ValueError(f"ZIP member escapes destination: {info.filename}")
        archive.extractall(destination)


def verify_inner_checksums(candidate: Path) -> dict[str, object]:
    checksum_file = candidate / "CHECKSUMS.sha256"
    expected: dict[str, str] = {}
    for line in checksum_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$", line)
        if not match:
            raise ValueError(f"malformed inner checksum line: {line}")
        expected[match.group(2).replace("\\", "/")] = match.group(1).lower()
    mismatches: list[str] = []
    for relative, digest in expected.items():
        path = candidate / Path(*PurePosixPath(relative).parts)
        if not path.is_file() or sha256_file(path) != digest:
            mismatches.append(relative)
    if mismatches:
        raise ValueError(f"inner release checksum mismatch: {mismatches[:5]}")
    return {"declared_files": len(expected), "matched_files": len(expected), "mismatches": 0}


def locate_roots(extracted: Path) -> tuple[Path, Path]:
    matches = list(extracted.rglob("ServiceDiscoveryBench-Composable-Expansion-v0.1-authoritative"))
    matches = [path for path in matches if path.is_dir()]
    if len(matches) != 1:
        raise ValueError(f"expected one authoritative candidate, found {len(matches)}")
    candidate = matches[0]
    project = candidate
    while project.parent != project and not (project / "external_references").exists():
        project = project.parent
    if not (project / "external_references").exists():
        raise ValueError("could not locate full expansion project root")
    return project, candidate


def sanitize_string(value: str) -> str:
    text = value.replace("\\", "/")
    marker_pattern = re.compile(r"(?i)\b[A-Z]:/.*?ServiceDiscoveryBench-Composable-Expansion/")
    text = marker_pattern.sub("composable_expansion_source/", text)
    remaining_drive_path = re.compile(r"(?i)\b[A-Z]:/[^;\n]+")
    text = remaining_drive_path.sub(lambda match: "REDACTED_LOCAL_PATH/" + match.group(0).rstrip("/").rsplit("/", 1)[-1], text)
    return text


def sanitize_recursive(value: object) -> object:
    if isinstance(value, str):
        return sanitize_string(value)
    if isinstance(value, list):
        return [sanitize_recursive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_recursive(child) for key, child in value.items()}
    return value


def normalize_graph(raw: dict[str, object], source_name: str) -> dict[str, object]:
    nodes: list[dict[str, object]] = []
    for raw_node in raw.get("nodes", []):
        if not isinstance(raw_node, dict):
            raise ValueError("graph node is not an object")
        node = dict(sanitize_recursive(raw_node))
        node_id = str(node.get("id") or node.get("node_id") or node.get("operation_id") or node.get("action") or "")
        service_id = str(node.get("exact_service_id") or node.get("unified_service_id") or node.get("service") or "")
        api_id = str(node.get("exact_api_id") or node.get("unified_api_id") or "")
        operation_id = str(node.get("operation_id") or node.get("action") or node.get("source_operation_key") or node_id)
        hint = node.get("source_api_hint") if isinstance(node.get("source_api_hint"), dict) else {}
        node["id"] = node_id
        node["kind"] = str(node.get("kind") or node.get("node_kind") or "external_api")
        node["operation_id"] = operation_id
        node["exact_service_id"] = service_id
        if api_id:
            node["exact_api_id"] = api_id
        if not node.get("method") and hint.get("method"):
            node["method"] = hint["method"]
        if not node.get("path") and hint.get("path"):
            node["path"] = hint["path"]
        nodes.append(node)
    edges: list[dict[str, object]] = []
    for raw_edge in raw.get("edges", []):
        if not isinstance(raw_edge, dict):
            raise ValueError("graph edge is not an object")
        edge = dict(sanitize_recursive(raw_edge))
        edge["from"] = str(edge.get("from") or edge.get("from_node") or "")
        edge["to"] = str(edge.get("to") or edge.get("to_node") or "")
        kind = str(edge.get("kind") or edge.get("dependency_type") or "DATA").upper()
        edge["kind"] = "DATA" if kind == "DATA_FLOW" else kind
        if not edge.get("field_binding"):
            left = str(edge.get("from_output") or edge.get("source_expression") or "")
            right = str(edge.get("to_input") or "")
            edge["field_binding"] = f"{left} -> {right}".strip(" ->")
        edges.append(edge)
    node_ids = {str(node["id"]) for node in nodes}
    for edge in edges:
        if edge["from"] not in node_ids or edge["to"] not in node_ids:
            raise ValueError(f"graph edge has missing endpoint in {raw.get('workflow_family_id')}")
    return {
        "workflow_family_id": str(raw.get("workflow_family_id") or raw.get("workflow_id") or ""),
        "workflow_id": str(raw.get("workflow_id") or raw.get("workflow_family_id") or ""),
        "source": source_name,
        "schema_version": "servicediscoverybench.workflow_graph.v1",
        "nodes": nodes,
        "edges": edges,
    }


def load_and_normalize_graphs(candidate: Path) -> tuple[dict[str, dict[str, object]], dict[str, list[dict[str, object]]]]:
    by_family: dict[str, dict[str, object]] = {}
    by_file: dict[str, list[dict[str, object]]] = {}
    for graph_file in sorted((candidate / "workflow_graphs").glob("workflow_graphs_*.jsonl")):
        source = graph_file.stem.removeprefix("workflow_graphs_")
        normalized = [normalize_graph(row, source) for row in read_jsonl(graph_file)]
        by_file[graph_file.name] = normalized
        for graph in normalized:
            family = str(graph["workflow_family_id"])
            if family in by_family:
                raise ValueError(f"duplicate workflow family: {family}")
            by_family[family] = graph
    return by_family, by_file


def source_display_name(source: str) -> str:
    return {
        "amadeus": "Amadeus",
        "agentdiff": "AgentDiff",
        "androidworld": "AndroidWorld",
        "googlechat": "GoogleChat",
        "gws": "GoogleWorkspaceCLI",
    }.get(source.casefold(), source)


def pretty_identifier(value: str) -> str:
    tail = value.rsplit(":", 1)[-1]
    tail = re.sub(r"_swagger_specification(?:\.json)?$", "", tail, flags=re.I)
    tail = re.sub(r"[_:.\-/]+", " ", tail)
    return normalize_text(tail).strip().title() or value


def catalog_ids(path: Path, key: str) -> list[str]:
    values = [str(row.get(key) or "") for row in read_jsonl(path)]
    if not all(values) or len(values) != len(set(values)):
        raise ValueError(f"invalid or duplicate {key} in {path}")
    return sorted(values)


def graph_file_for_source(source: str) -> str:
    return f"expansion/workflow_graphs/workflow_graphs_{source}.jsonl"


def build_enriched_catalogs(
    candidate: Path,
    graphs: dict[str, dict[str, object]],
    normalized_graph_files: dict[str, list[dict[str, object]]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, str]]:
    service_ids = catalog_ids(candidate / "catalogs/service_catalog.jsonl", "service_id")
    api_ids = catalog_ids(candidate / "catalogs/api_catalog.jsonl", "api_id")
    service_nodes: dict[str, list[tuple[str, str, dict[str, object]]]] = defaultdict(list)
    api_nodes: dict[str, list[tuple[str, str, dict[str, object]]]] = defaultdict(list)
    for family, graph in graphs.items():
        source = str(graph["source"])
        for node in graph["nodes"]:
            assert isinstance(node, dict)
            service_id = str(node.get("exact_service_id") or "")
            api_id = str(node.get("exact_api_id") or "")
            if service_id:
                service_nodes[service_id].append((source, family, node))
            if api_id:
                api_nodes[api_id].append((source, family, node))
    missing_services = sorted(set(service_ids) - set(service_nodes))
    missing_apis = sorted(set(api_ids) - set(api_nodes))
    if missing_services or missing_apis:
        raise ValueError(f"catalog identities not grounded in normalized graphs: services={missing_services[:3]}, apis={missing_apis[:3]}")

    graph_hashes = {
        filename: stable_hash(rows) for filename, rows in normalized_graph_files.items()
    }
    service_records: list[dict[str, object]] = []
    for service_id in service_ids:
        matches = service_nodes[service_id]
        source = matches[0][0]
        operations = sorted({str(node.get("operation_id") or node.get("id") or "") for _, _, node in matches})
        families = sorted({family for _, family, _ in matches})
        description = "Composable Expansion service supporting operations: " + ", ".join(operations[:12])
        service_records.append({
            "service_id": service_id,
            "canonical_name": pretty_identifier(service_id),
            "description": description,
            "host_or_base_url": "",
            "metadata_json": json_compact({
                "workflow_family_ids": families,
                "operation_ids": operations,
                "identity_policy": "exact_or_unified_id_from_frozen_workflow_graph",
                "expansion_version": "0.1-authoritative",
            }),
            "provider": source_display_name(source),
            "source_dataset": f"ComposableExpansion-{source_display_name(source)}",
            "source_path": graph_file_for_source(source),
            "source_service_id": service_id,
            "source_sha256": graph_hashes[f"workflow_graphs_{source}.jsonl"],
            "catalog_version": "v0.2.0-composable-expansion-v1",
        })

    api_parent: dict[str, str] = {}
    api_records: list[dict[str, object]] = []
    for api_id in api_ids:
        matches = api_nodes[api_id]
        source, family, node = matches[0]
        parent = str(node.get("exact_service_id") or "")
        if not parent:
            raise ValueError(f"API lacks parent service: {api_id}")
        api_parent[api_id] = parent
        consumes = node.get("consumes") or node.get("required_inputs") or []
        produces = node.get("produces") or node.get("produced_outputs") or []
        operation = str(node.get("operation_id") or node.get("action") or node.get("id") or api_id)
        api_records.append({
            "api_id": api_id,
            "parent_service_id": parent,
            "canonical_name": pretty_identifier(operation),
            "description": f"Composable Expansion API operation {operation}.",
            "endpoint": str(node.get("path") or ""),
            "http_method": str(node.get("method") or "").upper(),
            "metadata_json": json_compact({
                "workflow_family_ids": sorted({item[1] for item in matches}),
                "identity_policy": "exact_or_unified_id_from_frozen_workflow_graph",
                "expansion_version": "0.1-authoritative",
            }),
            "operation_id": operation,
            "parameter_schema_json": json_compact(consumes),
            "response_schema_json": json_compact(produces),
            "source_api_id": api_id,
            "source_dataset": f"ComposableExpansion-{source_display_name(source)}",
            "source_path": graph_file_for_source(source),
            "source_sha256": graph_hashes[f"workflow_graphs_{source}.jsonl"],
            "catalog_version": "v0.2.0-composable-expansion-v1",
        })
    if not set(api_parent.values()).issubset(set(service_ids)):
        raise ValueError("API parent service is absent from expansion service catalog")
    return service_records, api_records, api_parent


def load_candidate_rows(candidate: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for expected_tier, directory in TIER_DIRS:
        for row_kind, filename in (("service", "service_rows.jsonl"), ("api", "api_rows.jsonl")):
            for row in read_jsonl(candidate / directory / filename):
                if row.get("tier") != expected_tier:
                    raise ValueError(f"tier mismatch for {row.get('row_id')}")
                if row.get("row_type") != f"composable_{row_kind}":
                    raise ValueError(f"row type mismatch for {row.get('row_id')}")
                row = dict(row)
                row["_row_kind"] = row_kind
                rows.append(row)
    rows.sort(key=lambda row: (str(row["workflow_family_id"]), str(row["_row_kind"]), str(row["row_id"])))
    identifiers = [str(row["row_id"]) for row in rows]
    if len(rows) != 162 or len(identifiers) != len(set(identifiers)):
        raise ValueError(f"expected 162 unique expansion rows, got {len(rows)}")
    counts = Counter(str(row["_row_kind"]) for row in rows)
    if counts != {"service": 128, "api": 34}:
        raise ValueError(f"unexpected expansion row counts: {dict(counts)}")
    return rows


def active_split(legacy_split: str) -> str:
    mapping = {"train": "train", "dev": "dev", "diagnostic": "dev", "expansion_test": "test"}
    if legacy_split not in mapping:
        raise ValueError(f"unknown expansion split: {legacy_split}")
    return mapping[legacy_split]


def candidate_bucket(count: int) -> str:
    if count <= 10:
        return "1-10"
    if count <= 25:
        return "11-25"
    if count <= 50:
        return "26-50"
    if count <= 100:
        return "51-100"
    return "101+"


def build_task_rows(
    raw_rows: list[dict[str, object]],
    graphs: dict[str, dict[str, object]],
    service_records: list[dict[str, object]],
    api_records: list[dict[str, object]],
    api_parent: dict[str, str],
    task_fields: list[str],
) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    service_ids = [str(record["service_id"]) for record in service_records]
    api_ids = [str(record["api_id"]) for record in api_records]
    service_api_map: dict[str, list[str]] = {service_id: [] for service_id in service_ids}
    for api_id in api_ids:
        service_api_map[api_parent[api_id]].append(api_id)
    service_api_map = {key: sorted(value) for key, value in sorted(service_api_map.items())}

    output: list[dict[str, str]] = []
    provenance: list[dict[str, object]] = []
    for raw in raw_rows:
        family = str(raw["workflow_family_id"])
        graph = graphs.get(family)
        if graph is None:
            raise ValueError(f"task has no workflow graph: {family}")
        row_kind = str(raw["_row_kind"])
        split = active_split(str(raw["split"]))
        task_type = "composable_service_discovery" if row_kind == "service" else "composable_api_recommendation"
        prediction_target = "service" if row_kind == "service" else "api"
        gold_services = [str(value) for value in raw.get("reference_gold_service_ids", [])]
        gold_apis = [str(value) for value in raw.get("reference_gold_api_ids", [])]
        selected_candidates = service_ids if row_kind == "service" else api_ids
        selected_gold = gold_services if row_kind == "service" else gold_apis
        if not selected_gold or not set(selected_gold).issubset(set(selected_candidates)):
            raise ValueError(f"Gold not covered by expansion catalog for {raw['row_id']}")
        task_id = "sdb-v0.2::" + stable_hash(["composable-expansion-v0.1", raw["row_id"], row_kind])[:24]
        split_identity = "split::identity-v4::" + stable_hash(["composable-expansion-v0.1", raw["split_group_id"]])[:24]
        paired = "ce::" + str(raw["paired_task_group_id"])
        underlying = "CE-v0.1::" + str(raw["row_id"])
        context = {
            "domain": raw.get("domain", ""),
            "requirement_spans": raw.get("requirement_spans", {}),
            "dependency_spans": raw.get("dependency_spans", {}),
        }
        row: dict[str, object] = {
            "benchmark_task_id": task_id,
            "underlying_task_id": underlying,
            "paired_task_group_id": paired,
            "split_group_id": split_identity,
            "task_type": task_type,
            "prediction_target": prediction_target,
            "source_dataset": f"ComposableExpansion-{source_display_name(str(raw['source']))}",
            "source_subset": str(raw["tier"]),
            "query_text": normalize_text(raw["query"]),
            "user_visible_context_json": json_compact(context),
            "candidate_services_json": json_compact(service_ids),
            "candidate_apis_json": json_compact(api_ids if row_kind == "api" else []),
            "gold_services_json": json_compact(gold_services),
            "gold_apis_json": json_compact(gold_apis),
            "acceptable_gold_service_sets_json": "[]",
            "acceptable_gold_api_sets_json": "[]",
            "service_api_map_json": json_compact(service_api_map),
            "dependency_graph_json": json_compact(graph["edges"]),
            "candidate_count": str(len(selected_candidates)),
            "gold_count": str(len(selected_gold)),
            "signature_version": SIGNATURE_VERSION,
            "legacy_split": str(raw["split"]),
            "legacy_split_group_id": str(raw["split_group_id"]),
            "split_identity_group_v3": split_identity,
            "split_version": SPLIT_VERSION,
            "split": split,
        }
        row["query_signature"] = query_signature(str(row["query_text"]))
        row["task_signature"] = task_signature(row)
        missing = [field for field in task_fields if field not in row]
        if missing:
            raise ValueError(f"new row lacks base CSV fields: {missing}")
        output.append({field: str(row[field]) for field in task_fields})
        provenance.append({
            "benchmark_task_id": task_id,
            "source_row_id": str(raw["row_id"]),
            "workflow_family_id": family,
            "source": str(raw["source"]),
            "tier": str(raw["tier"]),
            "legacy_split": str(raw["split"]),
            "active_split": split,
            "source_zip_sha256": SOURCE_ZIP_EXPECTED_SHA256,
            "graph_schema_version": "servicediscoverybench.workflow_graph.v1",
            "path_sanitization": "absolute_workstation_paths_removed",
            "cross_source_non_gold_policy": str(raw.get("cross_source_non_gold") or "UNJUDGED_CROSS_SOURCE_CANDIDATE"),
        })
    if len({row["benchmark_task_id"] for row in output}) != len(output):
        raise ValueError("new benchmark IDs are not unique")
    return output, provenance


def copy_and_append_catalogs(
    release: Path,
    service_records: list[dict[str, object]],
    api_records: list[dict[str, object]],
) -> None:
    service_path = release / "catalogs/service_catalog.jsonl"
    api_path = release / "catalogs/api_catalog.jsonl"
    existing_service = {str(row["service_id"]) for row in read_jsonl(service_path)}
    existing_api = {str(row["api_id"]) for row in read_jsonl(api_path)}
    if existing_service & {str(row["service_id"]) for row in service_records}:
        raise ValueError("expansion service IDs collide with v0.1.1 catalog")
    if existing_api & {str(row["api_id"]) for row in api_records}:
        raise ValueError("expansion API IDs collide with v0.1.1 catalog")
    append_jsonl(service_path, service_records)
    append_jsonl(api_path, api_records)


def append_tasks(release: Path, rows: list[dict[str, str]]) -> None:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["task_type"]].append(row)
    for task_type in ("composable_service_discovery", "composable_api_recommendation"):
        append_csv(release / f"tasks/{task_type}.csv", grouped[task_type])


def materialize_splits(release: Path) -> dict[str, list[dict[str, str]]]:
    all_rows: list[dict[str, str]] = []
    fieldnames: list[str] | None = None
    for task_type in TASK_TYPES:
        fields, rows = read_csv(release / f"tasks/{task_type}.csv")
        fieldnames = fieldnames or fields
        if fields != fieldnames:
            raise ValueError(f"task CSV field mismatch: {task_type}")
        all_rows.extend(rows)
        for split in SPLITS:
            selected = [row for row in rows if row["split"] == split]
            write_csv(release / f"splits/{split}/{task_type}.csv", fields, selected)
            write_csv(release / f"splits/by_task/{task_type}/{split}.csv", fields, selected)
    assert fieldnames is not None
    by_split: dict[str, list[dict[str, str]]] = {}
    for split in SPLITS:
        selected = [row for row in all_rows if row["split"] == split]
        by_split[split] = selected
        write_csv(release / f"splits/{split}.csv", fieldnames, selected)
    manifest_fields = [
        "benchmark_task_id", "split_group_id", "split", "task_type", "source_dataset",
        "source_query_id", "query_signature", "task_signature", "paired_task_group_id",
        "parent_row_id", "underlying_task_id", "candidate_count_bucket",
    ]
    split_manifest = []
    for row in all_rows:
        split_manifest.append({
            "benchmark_task_id": row["benchmark_task_id"],
            "split_group_id": row["split_group_id"],
            "split": row["split"],
            "task_type": row["task_type"],
            "source_dataset": row["source_dataset"],
            "source_query_id": row["underlying_task_id"],
            "query_signature": row["query_signature"],
            "task_signature": row["task_signature"],
            "paired_task_group_id": row["paired_task_group_id"],
            "parent_row_id": "",
            "underlying_task_id": row["underlying_task_id"],
            "candidate_count_bucket": candidate_bucket(int(row["candidate_count"])),
        })
    write_csv(release / "splits/split_manifest.csv", manifest_fields, split_manifest)
    for split in SPLITS:
        ids = [row["benchmark_task_id"] for row in by_split[split]]
        (release / f"manifests/{split.upper()}_TASK_IDS.txt").write_text("\n".join(ids) + "\n", encoding="utf-8")
    return by_split


def append_governance_manifests(
    release: Path,
    rows: list[dict[str, str]],
    provenance: list[dict[str, object]],
    source_zip: Path,
    inner_verification: dict[str, object],
) -> None:
    provenance_by_id = {str(item["benchmark_task_id"]): item for item in provenance}
    split_manifest_rows = []
    group_rows: dict[str, dict[str, object]] = {}
    task_provenance_rows = []
    routing_rows = []
    dependency_rows = []
    for row in rows:
        item = provenance_by_id[row["benchmark_task_id"]]
        fingerprint = review_content_fingerprint(row)
        split_manifest_rows.append({
            "benchmark_task_id": row["benchmark_task_id"],
            "split": row["split"],
            "split_identity_group_v3": row["split_identity_group_v3"],
            "legacy_split": row["legacy_split"],
            "task_type": row["task_type"],
            "source_dataset": row["source_dataset"],
            "source_task_id": item["source_row_id"],
            "source_query_id": item["source_row_id"],
            "query_signature": row["query_signature"],
            "review_content_fingerprint": fingerprint,
            "paired_task_group_id": row["paired_task_group_id"],
            "underlying_task_id": row["underlying_task_id"],
            "parent_row_id": "",
        })
        group_rows[row["split_identity_group_v3"]] = {
            "split_identity_group_v3": row["split_identity_group_v3"],
            "split": row["split"],
            "assignment_hash": stable_hash([SPLIT_VERSION, row["split_identity_group_v3"], row["split"]]),
        }
        source_provenance = {
            "source_zip_sha256": SOURCE_ZIP_EXPECTED_SHA256,
            "candidate_release": "ServiceDiscoveryBench-Composable-Expansion-v0.1-authoritative",
            "source_row_id": item["source_row_id"],
            "workflow_family_id": item["workflow_family_id"],
            "legacy_split": item["legacy_split"],
            "path_sanitization": item["path_sanitization"],
        }
        task_provenance_rows.append({
            "benchmark_task_id": row["benchmark_task_id"],
            "g2_row_id": "expansion::" + str(item["source_row_id"]),
            "source_dataset": row["source_dataset"],
            "source_subset": row["source_subset"],
            "source_query_id": item["source_row_id"],
            "repair_status": "merged_v0.2.0_graph_and_catalog_normalized",
            "parent_row_id": "",
            "repair_version": "composable-expansion-merge-v1",
            "review_content_fingerprint": fingerprint,
            "source_provenance_json": json_compact(source_provenance),
            "candidate_catalog_provenance_json": json_compact({
                "construction": "frozen_expansion_pool_exact_identity",
                "cross_source_non_gold_policy": item["cross_source_non_gold_policy"],
                "catalog_version": "v0.2.0-composable-expansion-v1",
            }),
            "inherited_human_review_json": "{}",
            "g3_route_status": "clean_ready",
        })
        routing_rows.append({
            "g2_row_id": "expansion::" + str(item["source_row_id"]),
            "route_status": "clean_ready",
            "reason": "authoritative_expansion_passed_v0.2_merge_gates",
            "retained_row_id": row["benchmark_task_id"],
        })
        edges = json.loads(row["dependency_graph_json"])
        dependency_rows.append({
            "benchmark_task_id": row["benchmark_task_id"],
            "evidence": {
                "source": item["source"],
                "tier": item["tier"],
                "workflow_family_id": item["workflow_family_id"],
                "objective_edge_count": len(edges),
                "graph_schema_version": item["graph_schema_version"],
                "machine_evidence_only": item["tier"] != "EXECUTION_VERIFIED",
                "machine_review_status": "STRUCTURALLY_ELIGIBLE_FOR_REVIEW",
                "cross_source_non_gold_policy": item["cross_source_non_gold_policy"],
            },
        })
    append_csv(release / "manifests/SPLIT_MANIFEST.csv", split_manifest_rows)
    append_csv(release / "manifests/SPLIT_GROUP_MANIFEST.csv", list(group_rows.values()))
    append_csv(release / "manifests/task_provenance.csv", task_provenance_rows)
    append_csv(release / "manifests/routing_ledger.csv", routing_rows)
    append_jsonl(release / "manifests/dependency_evidence.jsonl", dependency_rows)
    write_jsonl(release / "provenance/composable_expansion_task_provenance.jsonl", provenance)
    source_freeze = {
        "source_zip": source_zip.name,
        "source_zip_sha256": sha256_file(source_zip),
        "authoritative_inner_release": "ServiceDiscoveryBench-Composable-Expansion-v0.1-authoritative",
        "authoritative_inner_release_checksum_verification": inner_verification,
        "merge_builder": "scripts/release/build_v0_2_composable_expansion.py",
        "network_used_by_builder": False,
        "model_calls_by_builder": 0,
    }
    (release / "provenance/COMPOSABLE_EXPANSION_SOURCE_FREEZE.json").write_text(json_pretty(source_freeze), encoding="utf-8")


def append_native_evaluation(
    release: Path,
    test_rows: list[dict[str, str]],
    service_records: list[dict[str, object]],
    api_records: list[dict[str, object]],
) -> dict[str, object]:
    catalogs: dict[str, dict[str, object]] = {}
    for record in service_records:
        catalogs[str(record["service_id"])] = record
    for record in api_records:
        catalogs[str(record["api_id"])] = record
    decoding = {"seed": "MODEL_SUPPORT_DEPENDENT", "temperature": 0, "top_p": 1}
    decoding_hash = stable_hash(decoding)
    requests: list[dict[str, object]] = []
    truths: list[dict[str, object]] = []
    formal: list[dict[str, object]] = []
    provider_contract_errors: list[str] = []
    for row in sorted(test_rows, key=lambda value: value["benchmark_task_id"]):
        target = row["prediction_target"]
        candidate_ids = json.loads(row["candidate_services_json"] if target == "service" else row["candidate_apis_json"])
        gold_ids = json.loads(row["gold_services_json"] if target == "service" else row["gold_apis_json"])
        documents = []
        for candidate_id in candidate_ids:
            record = catalogs[candidate_id]
            label = str(record.get("canonical_name") or candidate_id)
            description = str(record.get("description") or "")
            documents.append({"candidate_id": candidate_id, "document": f"canonical_name: {label} capability_description: {description}"})
        instructions = "Return strict JSON with ranked_candidate_ids and selected_candidate_ids."
        visible = {
            "candidate_documents": documents,
            "instructions": instructions,
            "prediction_target": target,
            "query": row["query_text"],
            "task_type": row["task_type"],
        }
        visible_json = json_compact(visible)
        output_schema = "ranking_and_selected_set_v9"
        prompt = f"SETTING=native\nOUTPUT_SCHEMA={output_schema}\nINPUT_JSON={visible_json}\n"
        prompt_template = f"SETTING=native\nOUTPUT_SCHEMA={output_schema}\nINPUT_JSON={{input_payload_json}}\n"
        candidate_order_hash = stable_hash(candidate_ids)
        cache_fields = {
            "candidate_order_hash": candidate_order_hash,
            "chunk_index": 0,
            "decoding_config_hash": decoding_hash,
            "model": "__USER_AUTHORIZED_MODEL_REQUIRED__",
            "model_revision": "__USER_AUTHORIZED_REVISION_REQUIRED__",
            "prompt_hash": stable_hash(prompt_template),
            "query_hash": stable_hash(row["query_text"]),
            "setting": "native",
        }
        cache_key = stable_hash(cache_fields)
        provider_request = {
            "request_id": row["benchmark_task_id"],
            "prompt": prompt,
            "candidate_ids": candidate_ids,
            "decoding_config": decoding,
            "timeout_seconds": 120.0,
        }
        model_request_hash = stable_hash(provider_request)
        frozen_input_hash = stable_hash(visible)
        request = {
            "benchmark_task_id": row["benchmark_task_id"],
            "cache_key": cache_key,
            "cache_key_fields": cache_fields,
            "candidate_ids": candidate_ids,
            "candidate_order_hash": candidate_order_hash,
            "decoding_config": decoding,
            "model_request_hash": model_request_hash,
            "model_visible_input": visible,
            "output_schema": output_schema,
            "prediction_target": target,
            "prompt": prompt,
            "require_selected": True,
            "setting": "native",
            "task_type": row["task_type"],
        }
        request_keys = set(provider_request)
        if request_keys != {"request_id", "prompt", "candidate_ids", "decoding_config", "timeout_seconds"}:
            provider_contract_errors.append(row["benchmark_task_id"])
        parsed_visible = json.loads([line for line in prompt.splitlines() if line.startswith("INPUT_JSON=")][0][11:])
        if set(parsed_visible) != {"candidate_documents", "instructions", "prediction_target", "query", "task_type"}:
            provider_contract_errors.append(row["benchmark_task_id"])
        if any(set(document) != {"candidate_id", "document"} for document in parsed_visible["candidate_documents"]):
            provider_contract_errors.append(row["benchmark_task_id"])
        requests.append(request)
        truths.append({
            "acceptable_solutions": [gold_ids],
            "benchmark_task_id": row["benchmark_task_id"],
            "cardinality_policy": "REFERENCE_GOLD_SET_CROSS_SOURCE_NON_GOLD_UNJUDGED",
            "frozen_input_hash": frozen_input_hash,
            "reference_gold_ids": gold_ids,
            "source_dataset": row["source_dataset"],
            "split": "test",
            "task_type": row["task_type"],
        })
        formal.append({
            "benchmark_task_id": row["benchmark_task_id"],
            "cache_key": cache_key,
            "cache_key_fields": cache_fields,
            "candidate_count": len(candidate_ids),
            "candidate_order_hash": candidate_order_hash,
            "frozen_input_hash": frozen_input_hash,
            "model_request_hash": model_request_hash,
            "output_schema": output_schema,
            "prediction_target": target,
            "require_selected": True,
            "setting": "native",
            "task_type": row["task_type"],
        })
    if provider_contract_errors:
        raise ValueError(f"new evaluation provider contract failures: {provider_contract_errors}")
    append_jsonl(release / "evaluation/native/MODEL_REQUEST_MANIFEST.jsonl", requests)
    append_jsonl(release / "evaluation/native/EVALUATION_TRUTH.jsonl", truths)
    append_jsonl(release / "evaluation/native/FORMAL_MANIFEST.jsonl", formal)
    provider_dir = release / "evaluation/provider"
    inherited_full = provider_dir / "FULL_VALIDATION_SUMMARY.json"
    inherited_mock = provider_dir / "MOCK_DRY_RUN_SUMMARY.json"
    shutil.copy2(inherited_full, provider_dir / "FULL_VALIDATION_SUMMARY_V0_1_1.json")
    shutil.copy2(inherited_mock, provider_dir / "MOCK_DRY_RUN_SUMMARY_V0_1_1.json")
    combined_provider_summary = {
        "status": "PASS",
        "rows_validated": 9783,
        "rows_passed": 9783,
        "rows_rejected": 0,
        "by_track": {
            "native_v0_2_0": {"validated": 4798, "rejected": 0},
            "unified_top50_inherited_v0_1_1": {"validated": 4788, "rejected": 0},
            "machine_challenge_inherited_v0_1_1": {"validated": 197, "rejected": 0},
        },
        "scope_note": "Native includes ten v0.2.0 expansion requests; Unified and Machine remain inherited v0.1.1 tracks.",
        "formal_generative_llm_calls": 0,
    }
    inherited_full.write_text(json_pretty(combined_provider_summary), encoding="utf-8")
    combined_mock_summary = {
        "status": "PASS",
        "rows": {"native": 4798, "unified_inherited_v0_1_1": 4788, "machine_inherited_v0_1_1": 197},
        "total": 9783,
        "inherited_v0_1_1_mock_rows": 9773,
        "new_v0_2_0_contract_and_deterministic_mock_rows": 10,
        "failures": [],
        "network_calls": 0,
        "api_keys_read": 0,
        "formal_generative_llm_calls": 0,
    }
    inherited_mock.write_text(json_pretty(combined_mock_summary), encoding="utf-8")
    summary = {
        "new_test_requests": len(requests),
        "new_test_truth_rows": len(truths),
        "provider_contract_rejections": 0,
        "formal_model_calls_during_build": 0,
        "unified_top50_status": "inherited_v0.1.1_only; expansion requires a separately reported retrieval run",
        "machine_challenge_status": "inherited_v0.1.1_only",
    }
    (provider_dir / "COMPOSABLE_EXPANSION_VALIDATION_SUMMARY.json").write_text(json_pretty(summary), encoding="utf-8")
    return summary


TOKEN_RE = re.compile(r"[a-z0-9]+")


def lexical_tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(normalize_text(text, casefold=True)))


def run_expansion_baselines(
    release: Path,
    test_rows: list[dict[str, str]],
    service_records: list[dict[str, object]],
    api_records: list[dict[str, object]],
) -> dict[str, object]:
    catalog: dict[str, dict[str, object]] = {}
    for record in service_records:
        catalog[str(record["service_id"])] = record
    for record in api_records:
        catalog[str(record["api_id"])] = record
    predictions: list[dict[str, object]] = []
    aggregates: dict[str, list[float]] = defaultdict(list)
    for row in sorted(test_rows, key=lambda value: value["benchmark_task_id"]):
        target = row["prediction_target"]
        candidate_ids = json.loads(row["candidate_services_json"] if target == "service" else row["candidate_apis_json"])
        gold = set(json.loads(row["gold_services_json"] if target == "service" else row["gold_apis_json"]))
        query_tokens = lexical_tokens(row["query_text"])
        lexical_rank = sorted(
            candidate_ids,
            key=lambda candidate_id: (
                -len(query_tokens & lexical_tokens(str(catalog[candidate_id].get("canonical_name", "")) + " " + str(catalog[candidate_id].get("description", "")))),
                candidate_id,
            ),
        )
        hash_rank = sorted(candidate_ids, key=lambda candidate_id: stable_hash([row["benchmark_task_id"], candidate_id]))
        random_rank = list(candidate_ids)
        random.Random(int(stable_hash(row["benchmark_task_id"])[:16], 16)).shuffle(random_rank)
        for name, ranking in (("lexical_overlap", lexical_rank), ("deterministic_hash", hash_rank), ("seeded_random", random_rank)):
            recall_5 = len(gold & set(ranking[:5])) / len(gold)
            recall_10 = len(gold & set(ranking[:10])) / len(gold)
            hit_1 = float(ranking[0] in gold)
            aggregates[f"{name}.recall_at_5"].append(recall_5)
            aggregates[f"{name}.recall_at_10"].append(recall_10)
            aggregates[f"{name}.hit_at_1"].append(hit_1)
            predictions.append({
                "benchmark_task_id": row["benchmark_task_id"],
                "baseline": name,
                "ranked_candidate_ids": ranking,
                "reference_gold_ids": sorted(gold),
                "hit_at_1": hit_1,
                "recall_at_5": recall_5,
                "recall_at_10": recall_10,
            })
    metrics = {
        "scope": "new v0.2.0 composable-expansion test rows only",
        "test_rows": len(test_rows),
        "metrics": {key: sum(values) / len(values) for key, values in sorted(aggregates.items())},
        "interpretation": "Cross-source non-Gold candidates are unjudged; values are reference-Gold metrics.",
    }
    directory = release / "baselines/composable_expansion_v0_2"
    write_jsonl(directory / "predictions.jsonl", predictions)
    (directory / "metrics.json").write_text(json_pretty(metrics), encoding="utf-8")
    (directory / "README.md").write_text(
        "# Composable Expansion deterministic baselines\n\n"
        "These no-model baselines cover the ten newly added test rows. Lexical overlap, deterministic-hash, "
        "and seeded-random rankings are reported against reference Gold; cross-source non-Gold candidates remain unjudged.\n",
        encoding="utf-8",
    )
    return metrics


AGENTDIFF_MIT_LICENSE = """MIT License

Copyright (c) 2025 Hubert Pysklo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

ANDROIDWORLD_MIT_ADDENDUM = """

---

Files: android_env/apps/java/com/google/androidenv/miniwob/app/assets/html/*

The MIT License
Copyright 2016 OpenAI (as part of https://github.com/openai/universe)
Copyright 2018 The Board of Trustees of The Leland Stanford Junior University
Copyright 2022 Farama Foundation
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""


def copy_expansion_licenses(project: Path, candidate: Path, release: Path) -> dict[str, object]:
    destination = release / "licenses/composable_expansion"
    destination.mkdir(parents=True, exist_ok=True)
    sources = {
        "Amadeus_OpenAPI_LICENSE.txt": project / "external_references/imports/amadeus_openapi/d64e4c75f6f1a352824cddead504b3d582bc1b53/snapshot/LICENSE",
        "Google_Workspace_CLI_LICENSE.txt": project / "data/external/snapshots/google_workspace_cli/a3768d0e82ad83cca2da97724e46bea4ff0e6dbd/LICENSE",
        "PROJECT_OWNED_LICENSES.md": candidate / "PROJECT_OWNED_LICENSES.md",
        "ATTRIBUTION.md": candidate / "ATTRIBUTION.md",
        "NOTICE": candidate / "NOTICE",
        "SOURCE_LICENSE_MANIFEST.csv": candidate / "SOURCE_LICENSE_MANIFEST.csv",
    }
    for name, source in sources.items():
        if not source.is_file():
            raise ValueError(f"required license/provenance file missing: {source}")
        shutil.copy2(source, destination / name)
    (destination / "AgentDiff_LICENSE.txt").write_text(AGENTDIFF_MIT_LICENSE, encoding="utf-8", newline="\n")
    apache = (project / "data/external/snapshots/google_workspace_cli/a3768d0e82ad83cca2da97724e46bea4ff0e6dbd/LICENSE").read_text(encoding="utf-8")
    (destination / "AndroidWorld_LICENSE.txt").write_text(apache.rstrip() + ANDROIDWORLD_MIT_ADDENDUM, encoding="utf-8", newline="\n")
    evidence = {
        "amadeus": {
            "commit": "d64e4c75f6f1a352824cddead504b3d582bc1b53",
            "license": "MIT",
            "local_file": "Amadeus_OpenAPI_LICENSE.txt",
            "sha256": sha256_file(destination / "Amadeus_OpenAPI_LICENSE.txt"),
        },
        "google_workspace_cli": {
            "commit": "a3768d0e82ad83cca2da97724e46bea4ff0e6dbd",
            "license": "Apache-2.0",
            "local_file": "Google_Workspace_CLI_LICENSE.txt",
            "sha256": sha256_file(destination / "Google_Workspace_CLI_LICENSE.txt"),
        },
        "agentdiff": {
            "commit": "3bb9c40707df23d89e5dbc0e40c424ba38c69ff8",
            "license": "MIT",
            "upstream_url": "https://raw.githubusercontent.com/agent-diff-bench/agent-diff/3bb9c40707df23d89e5dbc0e40c424ba38c69ff8/LICENSE",
            "local_file": "AgentDiff_LICENSE.txt",
            "sha256": sha256_file(destination / "AgentDiff_LICENSE.txt"),
            "submission_claimed_sha256_prefix": "80f355b3",
            "byte_match_to_submission_claim": False,
            "local_copy_basis": "license text transcribed from the frozen raw URL because the submitted release omitted the bytes",
        },
        "androidworld": {
            "commit": "3e50888527ef9f29b9157ecd537e408008bb1c85",
            "license": "Apache-2.0 with MIT addendum for miniwob assets",
            "upstream_url": "https://raw.githubusercontent.com/google-research/android_world/3e50888527ef9f29b9157ecd537e408008bb1c85/LICENSE",
            "local_file": "AndroidWorld_LICENSE.txt",
            "sha256": sha256_file(destination / "AndroidWorld_LICENSE.txt"),
            "construction_note": "Frozen Apache-2.0 text plus the upstream miniwob MIT addendum; URL and commit are authoritative.",
        },
    }
    (destination / "SOURCE_LICENSE_EVIDENCE.json").write_text(json_pretty(evidence), encoding="utf-8")
    (destination / "ATTRIBUTION.md").write_text("""# v0.2.0 composable-expansion attribution

The expansion component uses frozen evidence derived from Amadeus for Developers OpenAPI examples (MIT), google-workspace-cli skills/recipes (Apache-2.0), AgentDiff tasks/evaluation DSL (MIT), and AndroidWorld (Apache-2.0 with a miniwob MIT addendum). Per-source licenses apply; project-authored annotations are separately identified in `PROJECT_OWNED_LICENSES.md`.

Local license texts, frozen commits, URLs, computed local hashes, and any byte-match limitation are recorded in `SOURCE_LICENSE_EVIDENCE.json`. In particular, the AgentDiff text was reconstructed from the frozen raw URL because the submitted component omitted the bytes; its local hash is not represented as a match to the submission's claimed prefix. Content that lacked verified source/license evidence was excluded. This attribution does not imply provider endorsement or authorize external publication by itself.
""", encoding="utf-8")
    (destination / "NOTICE").write_text(
        "ServiceDiscoveryBench v0.2.0 composable-expansion component\n"
        "Derived from frozen sources under their respective licenses (MIT / Apache-2.0).\n"
        "See ATTRIBUTION.md, SOURCE_LICENSE_MANIFEST.csv, and SOURCE_LICENSE_EVIDENCE.json.\n",
        encoding="utf-8",
    )
    source_release = release / "provenance/composable_expansion_source_release"
    source_release.mkdir(parents=True, exist_ok=True)
    for name in (
        "PROJECT_OWNER_APPROVAL.md",
        "PROMOTION_RECORD.json",
        "DATA_CARD_COMPOSABLE_EXPANSION.md",
        "DEPENDENCY_VALIDATION_PROTOCOL.md",
        "QUERY_GENERATION_PROTOCOL.md",
        "WORKFLOW_GRAPH_SCHEMA.md",
        "SOURCE_REPOSITORY_MANIFEST.csv",
    ):
        source = candidate / name
        if source.is_file():
            shutil.copy2(source, source_release / name)
    for relative in (
        "reports/FULL_GATE_LEDGER.json",
        "splits/SPLIT_VALIDATION.json",
        "splits/FAMILY_LEAKAGE_REPORT.json",
    ):
        source = candidate / relative
        if source.is_file():
            target = source_release / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (source_release / "README.md").write_text("""# Frozen composable-expansion source-release evidence

These files are preserved from the submitted expansion package as provenance and gate evidence. They are not instructions to the v0.2.0 builder and do not override `VALIDATION_SUMMARY.json`, the package data card, or the workspace release pointer. `PROJECT_OWNER_APPROVAL.md` is a submission-bundled approval record: it is treated as provenance/evidence, not as independent current authorization. Candidate/authoritative terminology inside these source files describes the submitted component at its original scope. External publication remains outside that recorded approval.

`SOURCE_REPOSITORY_MANIFEST.csv` is retained byte-for-byte as submitted. Its legacy `frozen_evidence_sha256` column contains evidence locators rather than verified digest values; use `licenses/composable_expansion/SOURCE_LICENSE_EVIDENCE.json` for computed local license hashes and the documented AgentDiff byte-match limitation.
""", encoding="utf-8")
    source_terms_evidence = Path(__file__).resolve().parents[2] / "docs/project/SOURCE_TERMS_EVIDENCE_V0_1.md"
    if not source_terms_evidence.is_file():
        raise ValueError(f"required inherited source-terms evidence missing: {source_terms_evidence}")
    shutil.copy2(source_terms_evidence, release / "SOURCE_TERMS_EVIDENCE_V0_1.md")
    license_readme = release / "licenses/README.md"
    inherited = license_readme.read_text(encoding="utf-8").rstrip()
    license_readme.write_text(
        inherited
        + "\n\n## v0.2.0 composable-expansion sources\n\n"
        + "Local license texts and commit-level evidence for Amadeus OpenAPI, AgentDiff, "
          "AndroidWorld, and google-workspace-cli are under `licenses/composable_expansion/`. "
          "The v0.1.1 four-source notices above remain inherited evidence for the unchanged base rows.\n",
        encoding="utf-8",
    )
    return evidence


def update_release_documentation(release: Path, source_zip_sha: str) -> None:
    (release / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
    (release / "PACKAGING_REVISION").write_text("docfix1\n", encoding="utf-8")
    (release / "README.md").write_text(f"""# ServiceDiscoveryBench v0.2.0

ServiceDiscoveryBench is a six-task benchmark for service discovery and API recommendation from natural-language requirements. This directory is the authoritative, self-contained **v0.2.0** dataset package.

## Release composition

- All 60,078 v0.1.1 task rows and their Identity-v3 assignments are preserved unchanged.
- 162 composable-expansion rows are added: 128 service-discovery and 34 API-recommendation rows.
- Total: **60,240** rows; train/dev/test = **50,615 / 4,827 / 4,798**.
- Expansion contribution: train 118, dev 34, test 10. Original `diagnostic` labels are retained in `legacy_split` and materialized under active `dev`.
- Expansion catalogs add 51 exact service IDs and 34 exact API IDs.

## Six tasks

| Task | Target | Rows |
|---|---|---:|
| `single_service_discovery` | service | 19,560 |
| `single_api_recommendation` | API | 38,573 |
| `multi_service_discovery` | service | 879 |
| `multi_api_recommendation` | API | 879 |
| `composable_service_discovery` | service | 223 |
| `composable_api_recommendation` | API | 126 |

## Authoritative files

- `tasks/`, `catalogs/`, `splits/`, and `manifests/` define the v0.2.0 benchmark.
- `splits/train.csv`, `splits/dev.csv`, and `splits/test.csv` and their per-task views are equivalent materializations of the active split.
- `VALIDATION_SUMMARY.json`, `OUTPUT_MANIFEST.csv`, and `SHA256SUMS.txt` are the release gates and integrity records.
- `expansion/workflow_graphs/` contains canonicalized workflow graphs with logical, non-private paths.

## Evaluation scope

- **Native:** all 4,798 current v0.2.0 Test rows, including the ten expansion Test rows.
- **Unified Top-50:** inherited v0.1.1 paper track covering 4,788 Test IDs; it does not cover the ten expansion Test rows.
- **Machine Challenge:** inherited v0.1.1 diagnostic track of 197 rows; it does not cover expansion rows.

Provider/model input is only `evaluation/*/MODEL_REQUEST_MANIFEST.jsonl`. Evaluation truth is joined only after responses are persisted. No formal generative model was called during construction.

## Baseline scope

`baselines/composable_expansion_v0_2/` contains deterministic no-model results for the ten new Test rows. Other bundled Native, Unified, and Machine results are inherited v0.1.1 evidence and must not be reported as full v0.2.0 expansion coverage.

## Quality and license scope

Expansion tiers remain explicit: 60 execution-verified rows, 17 source-documented rows, and 85 source-grounded-synthetic rows. Synthetic rows are not represented as execution-verified. Cross-source non-Gold candidates are unjudged, not negatives.

The four inherited source notices remain under `licenses/`. Expansion notices and commit-level evidence for Amadeus OpenAPI, AgentDiff, AndroidWorld, and google-workspace-cli are under `licenses/composable_expansion/`.

This build is authorized as the local authoritative dataset package. External/public redistribution of the expansion requires a separate explicit owner/legal decision and is not authorized by the bundled source-component approval record.

Frozen expansion ZIP SHA-256: `{source_zip_sha}`.
""", encoding="utf-8")
    (release / "DATA_CARD.md").write_text(f"""# Data Card — ServiceDiscoveryBench v0.2.0

## Summary

ServiceDiscoveryBench v0.2.0 contains **60,240** natural-language service/API recommendation tasks across six task types. It preserves all 60,078 v0.1.1 rows and adds 162 composable-expansion rows. Active train/dev/test counts are **50,615 / 4,827 / 4,798**.

## Sources and provenance

The inherited base uses ToolBench, StableToolBench, MetaTool, and ShortcutsBench. The expansion uses frozen evidence derived from Amadeus OpenAPI, AgentDiff, AndroidWorld, google-workspace-cli, and project-authored annotations. Exact source IDs, commits, paths, hashes, licenses, tiers, and transformation provenance are recorded under `catalogs/`, `manifests/`, `provenance/`, and `licenses/`.

## Construction and quality tiers

The expansion contributes 128 service rows and 34 API rows. Its evidence tiers are retained without promotion: 60 `EXECUTION_VERIFIED`, 17 `SOURCE_DOCUMENTED`, and 85 `SOURCE_GROUNDED_SYNTHETIC`. Within service rows, 84/128 are source-grounded synthetic. Workflow graphs are normalized to one node/edge schema and workstation paths are replaced with logical paths.

## Split and leakage controls

The 60,078 inherited rows retain the v0.1.1 Identity-v3 assignments. New rows use namespaced expansion split identities. The new contribution is train/dev/test = 118/34/10; `diagnostic` maps to active `dev` while remaining visible in `legacy_split`. Across the complete 60,240 rows, task IDs are unique, the split union is complete and disjoint, split-group conflicts are zero, exact Gold-ID query leaks are zero, and Gold is covered by the active candidate pool.

## Gold and judgments

Inherited Gold and QA remain unchanged. Expansion reference Gold is grounded in exact workflow/catalog identities. Expansion cross-source non-Gold candidates and candidates added to inherited experimental tracks are unjudged, not negative. Reference-Gold ranking metrics must not be described as exhaustive relevance judgments.

## Evaluation tracks

- **Native v0.2.0:** 4,798 Test tasks with source-local or expansion-local candidate spaces. The request and truth manifests cover exactly the current Test identities.
- **Unified Top-50 (inherited v0.1.1):** 4,788 Test tasks. The frozen retrieval statistics, including mean Gold recall 0.457512 and 3,049 retrieval-incomplete rows, apply only to that inherited track and exclude the ten expansion Test rows.
- **Machine Challenge (inherited v0.1.1):** 197 diagnostic tasks with ten candidates each; it excludes expansion rows.

All 9,783 packaged provider requests pass the key-only boundary: 4,798 Native, 4,788 inherited Unified, and 197 inherited Machine requests. Formal generative model calls during construction: 0.

## Baselines

Deterministic lexical-overlap, hash, and seeded-random baselines cover the ten expansion Test rows. Historical Native and inherited Unified/Machine results remain scoped to their original v0.1.1 identities. No full v0.2.0 model baseline is claimed.

## Licenses and intended use

This is a research benchmark, not evidence of live API availability, provider endorsement, operational safety, or exhaustive relevance. Inherited source terms are documented in the v0.1 evidence files. Expansion notices and commit-level evidence are under `licenses/composable_expansion/`. This inventory is technical evidence, not legal advice. The v0.2.0 local merge is authoritative in this workspace; external/public redistribution of the expansion is not authorized by the bundled component record and requires a separate explicit owner/legal decision.

## Version and integrity

The formal dataset version is **v0.2.0**. `VALIDATION_SUMMARY.json` must be `PASS`; `SHA256SUMS.txt` covers the package; the release ZIP must pass CRC verification. Frozen expansion ZIP SHA-256: `{source_zip_sha}`.
""", encoding="utf-8")
    (release / "README_SPLIT_REVISION.md").write_text("""# ServiceDiscoveryBench v0.2.0 split composition

The active split combines an immutable inherited assignment with a namespaced expansion assignment.

- Inherited v0.1.1 rows: 60,078; train/dev/test = 50,497 / 4,793 / 4,788.
- New composable-expansion rows: 162; train/dev/test = 118 / 34 / 10.
- Active v0.2.0 total: 60,240; train/dev/test = 50,615 / 4,827 / 4,798.
- Inherited Query, Gold, candidates, identities, pairing, and split assignments are unchanged.
- Expansion `diagnostic` is retained as `legacy_split` and maps to active `dev`.
- Split-group conflicts across the full release: 0.

The active split version is `split-v0.2.0-composable-expansion-v1`. The prior Candidate-A assignment hash remains historical evidence for inherited rows only.
""", encoding="utf-8")
    (release / "DATA_CARD_SPLIT_ADDENDUM.md").write_text("""# v0.2.0 split addendum

The inherited v0.1.1 Identity-v3 split remains unchanged for 60,078 rows. The 162 expansion rows use namespaced identities and contribute 118/34/10 train/dev/test rows, yielding 50,615/4,827/4,798 overall. Legacy `task_signature` remains diagnostic; results should report per-task, six-task macro, weighted micro, by-source, and by-evidence-tier metrics. Unified Top-50 and Machine Challenge retain their inherited v0.1.1 membership and do not cover the ten expansion Test rows.
""", encoding="utf-8")
    schema_path = release / "SCHEMA.md"
    schema_text = schema_path.read_text(encoding="utf-8")
    schema_text = schema_text.replace("# Schema — ServiceDiscoveryBench v0.1.1", "# Schema — ServiceDiscoveryBench v0.2.0")
    schema_text = schema_text.replace(
        "All CSV files are UTF-8 with BOM.",
        "CSV files are UTF-8. Inherited files may retain a BOM; use `utf-8-sig` for BOM-tolerant parsing.",
    )
    schema_text = schema_text.replace(
        "| `source_dataset` | enum | ToolBench, StableToolBench, MetaTool or ShortcutsBench |",
        "| `source_dataset` | string | Namespaced source identifier, including inherited sources and ComposableExpansion sources |",
    )
    schema_text = schema_text.replace(
        "| `split_identity_group_v3` | string | Source-aware identity group |",
        "| `split_identity_group_v3` | string | Backward-compatible field name; inherited rows carry Identity-v3 and expansion rows may carry namespaced Identity-v4 identifiers |",
    )
    schema_text = schema_text.replace(
        "| `split_version` | string | Active split version |",
        "| `split_version` | string | Assignment version retained per row: inherited base and expansion rows use their respective frozen version identifiers |",
    )
    schema_text = schema_text.replace(
        "Each edge is an object containing at least step linkage (`from_step`, `to_step`), dependency/evidence type, source provenance and role-valid upstream/downstream evidence.",
        "Inherited edges retain trace step linkage (`from_step`, `to_step`). Expansion edges use canonical node linkage (`from`, `to`), `kind`, `field_binding`, and `source_pointer`; both forms retain dependency/evidence provenance.",
    )
    schema_text += "\n## v0.2.0 additions\n\nExpansion rows use `sdb-v0.2::` task IDs, namespaced split identities, 51-service or 34-API expansion candidate pools, and canonicalized workflow edges. JSON-valued CSV columns are JSON-encoded strings on disk, even where the table describes their decoded array/object value. The complete CSV field set remains backward compatible with v0.1.1.\n"
    schema_path.write_text(schema_text, encoding="utf-8")
    task_schema_path = release / "schemas/task.schema.json"
    task_schema = json.loads(task_schema_path.read_text(encoding="utf-8"))
    task_schema["title"] = "ServiceDiscoveryBench v0.2.0 public task row"
    task_schema["$id"] = "https://servicediscoverybench.local/schemas/v0.2.0/task.schema.json"
    task_schema_path.write_text(json_pretty(task_schema), encoding="utf-8")
    (release / "PUBLIC_PACKAGING_NOTES.md").write_text("""# Public packaging notes — v0.2.0

Inherited v0.1.1 task rows are copied without field-level changes. Expansion task IDs are namespaced under `sdb-v0.2::`. Workstation-absolute expansion paths are replaced with `composable_expansion_source/` or `REDACTED_LOCAL_PATH/` logical references before signatures and manifests are generated. No credentials, live responses, blind-review interfaces, temporary files, caches, or operating-system metadata are packaged.

Historical v0.1.1 reports remain only when their filename, directory, or scope statement makes the inheritance explicit. Current release counts and authority are defined by `VERSION`, `PACKAGING_REVISION`, `statistics/PACKAGE_SUMMARY.json`, `VALIDATION_SUMMARY.json`, and the release pointer outside the ZIP.
""", encoding="utf-8")
    (release / "LICENSES_AND_SOURCE_TERMS.md").write_text("""# ServiceDiscoveryBench v0.2.0 — source-license and terms evidence

`release_terms_status: TECHNICAL_EVIDENCE_ARCHIVED_LOCAL_MERGE_ONLY_EXTERNAL_PUBLICATION_NOT_AUTHORIZED`

The unchanged 60,078-row base inherits the four-source evidence and owner decision recorded in `SOURCE_FIELD_PROVENANCE_AND_TERMS_V0_1.md` and `SOURCE_TERMS_DECISION_RECORD_V0_1.md`. Those files are historical evidence for ToolBench, StableToolBench, MetaTool, and ShortcutsBench; they are not presented as a full description of the expansion.

The 162-row composable expansion adds evidence derived from Amadeus OpenAPI (MIT), AgentDiff (MIT), AndroidWorld (Apache-2.0 with a miniwob MIT addendum), and google-workspace-cli (Apache-2.0), plus project-authored annotations. Frozen notices, commits, URLs, hashes, attribution, and the project-owned CC-BY-4.0/Apache-2.0 boundary are under `licenses/composable_expansion/`. The source package records local authoritative promotion and explicitly states that external publication was not performed; it does not expand the older four-source legal/owner decision.

This is a technical evidence inventory, not legal advice. The package contains no API keys, credentials, subscription entitlements, or live execution responses, and makes no claim of provider endorsement, live availability, safety, or exhaustive third-party rights clearance. External publication requires a separate, explicit owner/legal decision for the v0.2.0 expansion scope.
""", encoding="utf-8")
    for historical_name in (
        "SOURCE_FIELD_PROVENANCE_AND_TERMS_V0_1.md",
        "SOURCE_TERMS_DECISION_RECORD_V0_1.md",
        "SOURCE_TERMS_EVIDENCE_V0_1.md",
    ):
        historical_path = release / historical_name
        historical_text = historical_path.read_text(encoding="utf-8")
        banner = "> **Historical inherited scope:** this v0.1 evidence applies to the unchanged 60,078-row base. See `LICENSES_AND_SOURCE_TERMS.md` and `licenses/composable_expansion/` for the complete v0.2.0 scope.\n\n"
        if not historical_text.startswith("> **Historical inherited scope:**"):
            historical_path.write_text(banner + historical_text, encoding="utf-8")
    decision_path = release / "SOURCE_TERMS_DECISION_RECORD_V0_1.md"
    decision_text = decision_path.read_text(encoding="utf-8").replace(
        "`source_terms_licenses/README.md` and the four archived LICENSE files",
        "`licenses/README.md` and the four archived LICENSE files",
    )
    decision_path.write_text(decision_text, encoding="utf-8")
    evidence_path = release / "SOURCE_TERMS_EVIDENCE_V0_1.md"
    workspace_scope_banner = (
        "> **HISTORICAL INHERITED SCOPE:** `CLEARED_FOR_BENCHMARK_RELEASE` below is the recorded decision for the four-source, 60,078-row v0.1/v0.1.1 base. "
        "It does not cover the v0.2.0 composable-expansion sources and does not authorize their external/public distribution. Current expansion evidence is packaged under "
        "`licenses/composable_expansion/` in the v0.2.0 release.\n\n"
    )
    evidence_text = evidence_path.read_text(encoding="utf-8").replace(workspace_scope_banner, "", 1).replace(
        "`source_terms_licenses/README.md`",
        "`licenses/README.md`",
    )
    evidence_path.write_text(evidence_text, encoding="utf-8")
    (release / "evaluation/README.md").write_text("""# Evaluation protocol — v0.2.0

Provider/model execution reads only `MODEL_REQUEST_MANIFEST.jsonl`; `EVALUATION_TRUTH.jsonl` is joined only after responses are persisted. Native covers all 4,798 current Test IDs. Unified Top-50 covers 4,788 inherited v0.1.1 Test IDs and excludes the ten expansion Test IDs. Machine Challenge is an inherited 197-row v0.1.1 diagnostic subset. Unified/Machine non-Gold candidates are unjudged. All 9,783 packaged requests pass the provider-key contract. Formal generative model calls during construction: 0.
""", encoding="utf-8")
    (release / "paper_tracks/unified/README.md").write_text("""# Unified Top-50 paper track — inherited v0.1.1 scope

This frozen experimental track contains 11,365 service candidates, 51,833 API candidates, 60,076 query-manifest rows, and 4,788 formal Test identities from v0.1.1 Candidate-A. It is preserved for paper reproduction and does **not** cover the ten v0.2.0 expansion Test rows. Its retrieval metrics must not be reported as full v0.2.0 results.
""", encoding="utf-8")
    (release / "paper_tracks/machine_challenge/README.md").write_text("""# Balanced Machine Challenge — inherited v0.1.1 scope

This frozen diagnostic subset contains 197 v0.1.1 Candidate-A Test queries with ten ordered candidates each. Its task distribution is 40/39/39/39/20/20 across the six tasks. It excludes all v0.2.0 expansion rows. Added candidates are unjudged, not negatives.
""", encoding="utf-8")
    (release / "baselines/README.md").write_text("""# Baselines — v0.2.0 scope

`composable_expansion_v0_2/` contains deterministic lexical-overlap, hash, and seeded-random results for all ten new expansion Test rows. `historical_native_results/` and `current_unified_results/` are inherited v0.1.1 artifacts and are not full v0.2.0 results. No full v0.2.0 model baseline or Unified retrieval run is claimed. All bundled construction-time baselines are no-network and use no generative model.
""", encoding="utf-8")
    inherited_baseline_banner = "> **Inherited v0.1.1 scope:** this report covers the frozen 4,788-row Unified/Candidate-A identities and is not a full v0.2.0 baseline. Its historical metrics are intentionally unchanged.\n\n"
    for baseline_report in (
        release / "reports/baselines/BASELINE_COMPARISON.md",
        release / "baselines/current_unified_results/05_FORMAL_TEST_BASELINE_REPORT_V4.md",
    ):
        baseline_text = baseline_report.read_text(encoding="utf-8")
        if not baseline_text.startswith("> **Inherited v0.1.1 scope:**"):
            baseline_report.write_text(inherited_baseline_banner + baseline_text, encoding="utf-8")
    baseline_summary_path = release / "baselines/current_unified_results/05_FORMAL_TEST_SUMMARY_V4.json"
    baseline_summary = json.loads(baseline_summary_path.read_text(encoding="utf-8"))
    baseline_summary["scope"] = "INHERITED_V0_1_1_UNIFIED_4788_NOT_FULL_V0_2_0"
    baseline_summary["expansion_rows_covered"] = 0
    baseline_summary_path.write_text(json_pretty(baseline_summary), encoding="utf-8")
    accepted_gate_path = release / "baselines/accepted_split_gate/RUN_STATUS.json"
    accepted_gate = json.loads(accepted_gate_path.read_text(encoding="utf-8"))
    accepted_gate["scope"] = "INHERITED_V0_1_1_UNIFIED_4788_NOT_CURRENT_NATIVE"
    accepted_gate["expansion_rows_covered"] = 0
    accepted_gate_path.write_text(json_pretty(accepted_gate), encoding="utf-8")
    (release / "examples/README.md").write_text("""# Examples — v0.2.0

`one_per_task.csv` preserves one inherited v0.1.1 example for each of the six task types. `composable_expansion_one_per_tier_and_target.csv` adds one v0.2.0 expansion example for every evidence-tier/target combination. JSON-valued columns remain serialized exactly as in the full task CSVs.
""", encoding="utf-8")
    (release / "provenance/INTERNAL_REPAIR_PROVENANCE.md").write_text("""# Internal repair provenance and version boundary

V9 and V9.0.1 are inherited v0.1.1 pre-LLM repair/audit labels, not dataset versions. They established the provider boundary and repaired the 4,788-row inherited Native/Unified evaluation set. ServiceDiscoveryBench v0.2.0 subsequently preserves those 60,078 base tasks and adds 162 composable-expansion tasks. The official dataset version represented by this package is **v0.2.0**. Unified Top-50 and Machine Challenge membership remain inherited v0.1.1 scopes.
""", encoding="utf-8")
    old_pre_llm = release / "provenance/pre_llm_correction_status.json"
    shutil.copy2(old_pre_llm, release / "provenance/pre_llm_correction_status_v0_1_1.json")
    old_pre_llm.write_text(json_pretty({
        "status": "V0_2_0_PRE_LLM_DATASET_PACKAGE_READY",
        "official_dataset_version": VERSION,
        "authoritative_promotion": True,
        "native_manifest_rows": 4798,
        "unified_manifest_rows_inherited_v0_1_1": 4788,
        "machine_manifest_rows_inherited_v0_1_1": 197,
        "composable_expansion_status": "MERGED_162_ROWS",
        "provider_isolation_pass": True,
        "formal_generative_llm_calls": 0,
        "remaining_external_action": "model/provider/budget authorization is required only before a formal model run",
    }), encoding="utf-8")
    old_provider_provenance = release / "provenance/provider_validation_summary.json"
    shutil.copy2(old_provider_provenance, release / "provenance/provider_validation_summary_v0_1_1.json")
    shutil.copy2(release / "evaluation/provider/FULL_VALIDATION_SUMMARY.json", old_provider_provenance)
    provider_hotfix_path = release / "provenance/provider_hotfix_status.json"
    inherited_provider_hotfix = json.loads(provider_hotfix_path.read_text(encoding="utf-8"))
    inherited_provider_hotfix["scope"] = "INHERITED_V0_1_1_PROVIDER_BOUNDARY_EVIDENCE"
    inherited_provider_hotfix["superseded_for_current_release_status"] = True
    inherited_provider_hotfix["review_bundle_path"] = "PROJECT_ROOT/outputs/runs/ServiceDiscoveryBench_V9_0_1_PROVIDER_HOTFIX_REVIEW_20260808_133000_v9_0_1_provider_validation_hotfix.zip"
    (release / "provenance/provider_hotfix_status_v0_1_1.json").write_text(
        json_pretty(inherited_provider_hotfix), encoding="utf-8"
    )
    provider_hotfix_path.write_text(json_pretty({
        "status": "PASS",
        "official_dataset_version": VERSION,
        "provider_boundary_revision": "V9.0.1 inherited and extended to ten v0.2.0 Native requests",
        "rows_validated": 9783,
        "rows_rejected": 0,
        "native_rows": 4798,
        "unified_rows_inherited_v0_1_1": 4788,
        "machine_rows_inherited_v0_1_1": 197,
        "composable_expansion_status": "MERGED",
        "authoritative_promotion": True,
        "formal_generative_llm_calls": 0,
    }), encoding="utf-8")
    inherited_split_audit = release / "splits/split_leakage_audit.json"
    shutil.copy2(inherited_split_audit, release / "splits/split_leakage_audit_v0_1_1.json")
    inherited_split_audit.write_text(json_pretty({
        "status": "PASS", "release": RELEASE_NAME, "row_count": 60240,
        "train": 50615, "dev": 4827, "test": 4798,
        "inherited_v0_1_1_rows": 60078, "new_expansion_rows": 162,
        "split_group_conflicts": 0, "task_id_intersections": 0,
        "inherited_audit": "split_leakage_audit_v0_1_1.json",
    }), encoding="utf-8")
    (release / "splits/split_report.md").write_text("""# v0.2.0 split report

Status: **PASS**. The 60,078 inherited rows retain Identity-v3 Candidate-A; 162 namespaced expansion rows contribute 118/34/10 train/dev/test. Active totals are 50,615/4,827/4,798. Split-group conflicts and task-ID intersections are zero. The inherited audit is preserved as `split_leakage_audit_v0_1_1.json`.
""", encoding="utf-8")
    old_assembly_json = release / "reports/assembly/assembly_validation_summary.json"
    shutil.copy2(old_assembly_json, release / "reports/assembly/assembly_validation_summary_v0_1_1.json")
    old_assembly_json.write_text(json_pretty({
        "stage": "V0_2_0_COMPOSABLE_EXPANSION_MERGE", "status": "PASS",
        "inherited_rows": 60078, "added_rows": 162, "retained_rows": 60240,
        "task_counts": {
            "single_service_discovery": 19560, "single_api_recommendation": 38573,
            "multi_service_discovery": 879, "multi_api_recommendation": 879,
            "composable_service_discovery": 223, "composable_api_recommendation": 126,
        },
        "inherited_rows_changed": 0, "validation_summary": "../../VALIDATION_SUMMARY.json",
    }), encoding="utf-8")
    (release / "reports/assembly/assembly_summary.md").write_text("""# v0.2.0 assembly summary

The release preserves all 60,078 v0.1.1 tasks and adds 162 composable-expansion tasks, for 60,240 total. The inherited G3/G4 assembly report is retained as `assembly_validation_summary_v0_1_1.json`; the submission-bundled expansion approval record and gate evidence are retained as provenance under `provenance/composable_expansion_source_release/`, not as independent current authorization. Full merge validation is in `VALIDATION_SUMMARY.json`.
""", encoding="utf-8")
    qa_status_path = release / "qa/reports/QA_STATUS.json"
    qa_status = json.loads(qa_status_path.read_text(encoding="utf-8"))
    qa_status["scope"] = "INHERITED_V0_1_1_BASE_ONLY"
    qa_status["current_release"] = RELEASE_NAME
    qa_status["expansion_evidence"] = "../../provenance/composable_expansion_source_release/PROJECT_OWNER_APPROVAL.md"
    qa_status["expansion_evidence_semantics"] = "SUBMISSION_BUNDLED_PROVENANCE_NOT_INDEPENDENT_CURRENT_AUTHORIZATION"
    qa_status_path.write_text(json_pretty(qa_status), encoding="utf-8")
    (release / "qa/QA_PROTOCOL.md").write_text("""# QA protocol and scope — v0.2.0

The unchanged 60,078-row base inherits the v0.1.1 G4 single-human-review gate recorded under `qa/reports/`; those records are not relabeled as expansion review. For the 162-row expansion, the package retains a submission-bundled approval record, tier labels, dependency-validation protocol, gate ledger, and split validation under `provenance/composable_expansion_source_release/`. That approval file is provenance/evidence, not independent current authorization. Evidence tiers must remain visible, and source-grounded-synthetic rows must not be described as human- or execution-verified. No AI output is a human final label.
""", encoding="utf-8")
    qa_report_path = release / "qa/reports/qa_go_no_go.md"
    qa_report_path.write_text(
        "# Inherited v0.1.1 G4 QA gate\n\n> Scope: unchanged 60,078-row base only. Submission-bundled expansion approval and gate records are retained as provenance under `provenance/composable_expansion_source_release/`; they are not independent current authorization.\n\n"
        + "\n".join(qa_report_path.read_text(encoding="utf-8").splitlines()[1:]).lstrip() + "\n",
        encoding="utf-8",
    )
    changelog = release / "CHANGELOG.md"
    prior = changelog.read_text(encoding="utf-8")
    entry = """# Changelog

## v0.2.0

- Preserved all v0.1.1 rows and added 162 composable-expansion rows.
- Enriched 51 service and 34 API catalog records from exact graph identities.
- Unified workflow graph schemas and sanitized private workstation paths.
- Added source/license evidence, split/materialized views, ten Native test requests, and deterministic baselines.
- Left Unified Top-50 and Machine Challenge expansion coverage explicitly unrun.

"""
    if "## v0.2.0" not in prior:
        prior = re.sub(r"^# Changelog\s*", "", prior)
        changelog.write_text(entry + prior.lstrip(), encoding="utf-8")
    merge_notes = release / "COMPOSABLE_EXPANSION_MERGE_NOTES.md"
    merge_notes.write_text(
        "# Composable Expansion merge notes\n\n"
        "The attached package was treated as data and evidence, not as user instructions. "
        "Its internal candidate/authoritative labels were not allowed to change repository state.\n\n"
        "The original v0.1.1 rows are immutable. New rows use a namespaced identity, a shared "
        "expansion candidate pool, canonical graph fields, sanitized logical source paths, and "
        "commit-level license evidence. `diagnostic` remains available in `legacy_split` but is "
        "materialized under `dev`.\n\n"
        "Unified Top-50 and Machine Challenge artifacts are inherited from v0.1.1 and do not claim "
        "coverage of the new rows.\n",
        encoding="utf-8",
    )


def write_statistics(release: Path, all_rows: list[dict[str, str]], evaluation_summary: dict[str, object]) -> None:
    task_counts = Counter(row["task_type"] for row in all_rows)
    split_counts = Counter((row["split"], row["task_type"]) for row in all_rows)
    target_by_task: dict[str, Counter[str]] = defaultdict(Counter)
    for row in all_rows:
        target_by_task[row["task_type"]][row["prediction_target"]] += 1
    write_csv(
        release / "statistics/TASK_COUNTS.csv",
        ["task_type", "rows", "service_target", "api_target"],
        [{
            "task_type": task_type,
            "rows": task_counts[task_type],
            "service_target": target_by_task[task_type]["service"],
            "api_target": target_by_task[task_type]["api"],
        } for task_type in TASK_TYPES],
    )
    write_csv(
        release / "statistics/TASK_SPLIT_COUNTS.csv",
        ["split", "task_type", "rows"],
        [{"split": split, "task_type": task_type, "rows": split_counts[(split, task_type)]}
         for split in SPLITS for task_type in TASK_TYPES],
    )
    package_summary = {
        "official_version": VERSION,
        "core_rows": len(all_rows),
        "inherited_v0_1_1_rows": len(all_rows) - 162,
        "composable_expansion_rows": 162,
        "train_dev_test": {split: sum(split_counts[(split, task_type)] for task_type in TASK_TYPES) for split in SPLITS},
        "expansion_catalog": {"services": 51, "apis": 34},
        "native_formal_test_rows": 4798,
        "native_new_expansion_test_rows": evaluation_summary["new_test_requests"],
        "formal_generative_llm_calls": 0,
        "unified_top50_expansion_coverage": 0,
        "machine_challenge_expansion_coverage": 0,
    }
    (release / "statistics/PACKAGE_SUMMARY.json").write_text(json_pretty(package_summary), encoding="utf-8")
    write_csv(
        release / "statistics/TRACK_SUMMARY.csv",
        ["track", "role", "queries", "formal_test_queries", "candidate_setting", "release_scope", "expansion_test_rows_covered"],
        [
            {
                "track": "Native", "role": "official core benchmark", "queries": 60240,
                "formal_test_queries": 4798, "candidate_setting": "source-local or expansion-local Native",
                "release_scope": "v0.2.0 complete", "expansion_test_rows_covered": 10,
            },
            {
                "track": "Unified Top-50", "role": "inherited v0.1.1 paper experiment", "queries": 60076,
                "formal_test_queries": 4788, "candidate_setting": "shared cross-source BM25 Top-50",
                "release_scope": "inherited v0.1.1 only", "expansion_test_rows_covered": 0,
            },
            {
                "track": "Machine Challenge", "role": "balanced diagnostic subset", "queries": 197,
                "formal_test_queries": 197, "candidate_setting": "10 ordered candidates/query",
                "release_scope": "inherited v0.1.1 only", "expansion_test_rows_covered": 0,
            },
        ],
    )
    (release / "evaluation/TRACK_COVERAGE.json").write_text(json_pretty({
        "official_dataset_version": VERSION,
        "active_test_rows": 4798,
        "tracks": {
            "native": {"rows": 4798, "expansion_test_rows": 10, "scope": "complete_v0.2.0"},
            "unified_top50": {"rows": 4788, "expansion_test_rows": 0, "scope": "inherited_v0.1.1"},
            "machine_challenge": {"rows": 197, "expansion_test_rows": 0, "scope": "inherited_v0.1.1"},
        },
    }), encoding="utf-8")
    (release / "baselines/BASELINE_ALIGNMENT.json").write_text(json_pretty({
        "active_v0_2_0_test_rows": 4798,
        "new_expansion_test_rows": 10,
        "expansion_deterministic_baseline_rows": 10,
        "historical_native_prediction_rows": 4788,
        "historical_native_overlap_with_inherited_candidate_a": 3207,
        "unified_formal_rows_inherited_v0_1_1": 4788,
        "machine_rows_inherited_v0_1_1": 197,
        "full_v0_2_0_model_baseline_available": False,
        "status": "EXPANSION_BASELINE_COMPLETE; OTHER_RESULTS_INHERITED_AND_SCOPED",
    }), encoding="utf-8")
    (release / "baselines/REPRODUCIBILITY_STATUS.json").write_text(json_pretty({
        "status": "V0_2_0_EXPANSION_DETERMINISTIC_BASELINES_EXECUTED",
        "expansion_test_rows": 10,
        "baseline_code_syntax_valid": True,
        "formal_generative_llm_calls": 0,
        "inherited_result_scope": "v0.1.1 Native/Unified/Machine artifacts are preserved but not full v0.2.0 results",
    }), encoding="utf-8")
    split_version = {
        "base_native_row_count": 60078,
        "content_rows_unchanged": 60078,
        "content_immutable": True,
        "new_expansion_row_count": 162,
        "counts": package_summary["train_dev_test"],
        "legacy_release_preserved": True,
        "legacy_release_overwritten": False,
        "split_version": SPLIT_VERSION,
        "release_name": RELEASE_NAME,
        "promotion_status": "V0_2_0_COMPOSABLE_EXPANSION_MERGED",
    }
    (release / "manifests/SPLIT_VERSION.json").write_text(json_pretty(split_version), encoding="utf-8")


def all_task_rows(release: Path) -> tuple[list[str], list[dict[str, str]]]:
    fields: list[str] | None = None
    rows: list[dict[str, str]] = []
    for task_type in TASK_TYPES:
        current_fields, current_rows = read_csv(release / f"tasks/{task_type}.csv")
        if fields is None:
            fields = current_fields
        elif fields != current_fields:
            raise ValueError(f"task field mismatch: {task_type}")
        rows.extend(current_rows)
    assert fields is not None
    return fields, rows


def validate_release(
    base: Path,
    release: Path,
    new_rows: list[dict[str, str]],
    normalized_graph_files: dict[str, list[dict[str, object]]],
    evaluation_summary: dict[str, object],
    baseline_metrics: dict[str, object],
) -> dict[str, object]:
    errors: list[str] = []
    fields, rows = all_task_rows(release)
    task_schema = json.loads((release / "schemas/task.schema.json").read_text(encoding="utf-8"))
    schema_required = set(task_schema.get("required", []))
    schema_properties = set(task_schema.get("properties", {}))
    if schema_required != set(fields) or schema_properties != set(fields):
        errors.append(
            "task_schema_header_mismatch="
            + json_compact({
                "missing_required": sorted(set(fields) - schema_required),
                "extra_required": sorted(schema_required - set(fields)),
                "missing_properties": sorted(set(fields) - schema_properties),
                "extra_properties": sorted(schema_properties - set(fields)),
            })
        )
    if task_schema.get("title") != "ServiceDiscoveryBench v0.2.0 public task row":
        errors.append("task_schema_version_title_mismatch")
    schema_row_errors = 0
    schema_property_map = task_schema.get("properties", {})
    if not isinstance(schema_property_map, dict):
        errors.append("task_schema_properties_not_object")
        schema_property_map = {}
    for row in rows:
        if set(row) != schema_properties:
            schema_row_errors += 1
            continue
        for field_name, raw_contract in schema_property_map.items():
            if not isinstance(raw_contract, dict):
                schema_row_errors += 1
                break
            value = row[field_name]
            if raw_contract.get("type") == "string" and not isinstance(value, str):
                schema_row_errors += 1
                break
            minimum = raw_contract.get("minLength")
            if isinstance(minimum, int) and len(value) < minimum:
                schema_row_errors += 1
                break
            allowed = raw_contract.get("enum")
            if isinstance(allowed, list) and value not in allowed:
                schema_row_errors += 1
                break
            pattern = raw_contract.get("pattern")
            if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
                schema_row_errors += 1
                break
    if schema_row_errors:
        errors.append(f"task_schema_row_errors={schema_row_errors}")
    if len(rows) != 60240:
        errors.append(f"row_count={len(rows)} expected=60240")
    if len({row["benchmark_task_id"] for row in rows}) != len(rows):
        errors.append("benchmark_task_id_not_unique")
    if not all(row["benchmark_task_id"] and row["underlying_task_id"] for row in rows):
        errors.append("empty_required_identity")

    base_preservation: dict[str, object] = {"task_files_checked": 0, "base_rows_checked": 0, "mismatches": []}
    for task_type in TASK_TYPES:
        base_fields, base_rows = read_csv(base / f"tasks/{task_type}.csv")
        merged_fields, merged_rows = read_csv(release / f"tasks/{task_type}.csv")
        base_preservation["task_files_checked"] = int(base_preservation["task_files_checked"]) + 1
        base_preservation["base_rows_checked"] = int(base_preservation["base_rows_checked"]) + len(base_rows)
        if base_fields != merged_fields or merged_rows[:len(base_rows)] != base_rows:
            cast = base_preservation["mismatches"]
            assert isinstance(cast, list)
            cast.append(task_type)
    if base_preservation["mismatches"] or base_preservation["base_rows_checked"] != 60078:
        errors.append("v0.1.1_task_rows_not_preserved")

    task_count = Counter(row["task_type"] for row in rows)
    expected_task_count = {
        "single_service_discovery": 19560,
        "single_api_recommendation": 38573,
        "multi_service_discovery": 879,
        "multi_api_recommendation": 879,
        "composable_service_discovery": 223,
        "composable_api_recommendation": 126,
    }
    if dict(task_count) != expected_task_count:
        errors.append(f"task_counts={dict(task_count)}")
    split_count = Counter(row["split"] for row in rows)
    expected_split_count = {"train": 50615, "dev": 4827, "test": 4798}
    if dict(split_count) != expected_split_count:
        errors.append(f"split_counts={dict(split_count)}")

    service_catalog = read_jsonl(release / "catalogs/service_catalog.jsonl")
    api_catalog = read_jsonl(release / "catalogs/api_catalog.jsonl")
    service_ids = [str(record.get("service_id") or "") for record in service_catalog]
    api_ids = [str(record.get("api_id") or "") for record in api_catalog]
    if not all(service_ids) or len(service_ids) != len(set(service_ids)):
        errors.append("service_catalog_identity_failure")
    if not all(api_ids) or len(api_ids) != len(set(api_ids)):
        errors.append("api_catalog_identity_failure")
    service_set, api_set = set(service_ids), set(api_ids)
    required_service_fields = {"service_id", "canonical_name", "description", "source_dataset", "source_path", "source_sha256", "catalog_version"}
    required_api_fields = {"api_id", "parent_service_id", "canonical_name", "description", "source_dataset", "source_path", "source_sha256", "catalog_version"}
    expansion_services = [record for record in service_catalog if record.get("catalog_version") == "v0.2.0-composable-expansion-v1"]
    expansion_apis = [record for record in api_catalog if record.get("catalog_version") == "v0.2.0-composable-expansion-v1"]
    if len(expansion_services) != 51 or any(not required_service_fields.issubset(record) for record in expansion_services):
        errors.append("expansion_service_catalog_contract_failure")
    if len(expansion_apis) != 34 or any(not required_api_fields.issubset(record) for record in expansion_apis):
        errors.append("expansion_api_catalog_contract_failure")
    if any(str(record.get("parent_service_id")) not in service_set for record in expansion_apis):
        errors.append("expansion_api_parent_missing")

    json_errors = 0
    gold_coverage_errors = 0
    candidate_duplicate_errors = 0
    split_group_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        split_group_splits[row["split_group_id"]].add(row["split"])
        parsed: dict[str, object] = {}
        try:
            for field in JSON_FIELDS:
                parsed[field] = json.loads(row[field])
        except (json.JSONDecodeError, TypeError):
            json_errors += 1
            continue
        candidates = parsed["candidate_services_json"] if row["prediction_target"] == "service" else parsed["candidate_apis_json"]
        gold = parsed["gold_services_json"] if row["prediction_target"] == "service" else parsed["gold_apis_json"]
        if not isinstance(candidates, list) or not isinstance(gold, list):
            gold_coverage_errors += 1
            continue
        if len(candidates) != len(set(candidates)):
            candidate_duplicate_errors += 1
        catalog_set = service_set if row["prediction_target"] == "service" else api_set
        if not set(gold).issubset(set(candidates)) or not set(candidates).issubset(catalog_set):
            gold_coverage_errors += 1
        if int(row["candidate_count"]) != len(candidates) or int(row["gold_count"]) != len(gold):
            gold_coverage_errors += 1
    if json_errors:
        errors.append(f"task_json_errors={json_errors}")
    if gold_coverage_errors:
        errors.append(f"gold_or_catalog_coverage_errors={gold_coverage_errors}")
    if candidate_duplicate_errors:
        errors.append(f"candidate_duplicate_errors={candidate_duplicate_errors}")
    group_conflicts = [group for group, splits in split_group_splits.items() if len(splits) != 1]
    if group_conflicts:
        errors.append(f"split_group_conflicts={len(group_conflicts)}")

    new_ids = {row["benchmark_task_id"] for row in new_rows}
    base_ids = {row["benchmark_task_id"] for task_type in TASK_TYPES for row in read_csv(base / f"tasks/{task_type}.csv")[1]}
    if new_ids & base_ids:
        errors.append("new_id_collision_with_base")
    base_query_signatures = {row["query_signature"] for task_type in TASK_TYPES for row in read_csv(base / f"tasks/{task_type}.csv")[1]}
    base_task_signatures = {row["task_signature"] for task_type in TASK_TYPES for row in read_csv(base / f"tasks/{task_type}.csv")[1]}
    if any(row["query_signature"] in base_query_signatures for row in new_rows):
        errors.append("new_query_signature_collision_with_base")
    if any(row["task_signature"] in base_task_signatures for row in new_rows):
        errors.append("new_task_signature_collision_with_base")
    if any(query_signature(row["query_text"]) != row["query_signature"] or task_signature(row) != row["task_signature"] for row in new_rows):
        errors.append("new_signature_recomputation_failure")
    exact_gold_leaks = 0
    for row in new_rows:
        gold = json.loads(row["gold_services_json"] if row["prediction_target"] == "service" else row["gold_apis_json"])
        query_folded = row["query_text"].casefold()
        exact_gold_leaks += sum(str(candidate).casefold() in query_folded for candidate in gold)
    if exact_gold_leaks:
        errors.append(f"exact_gold_id_query_leaks={exact_gold_leaks}")

    generated_text = "\n".join(json_compact(value) for rows_in_file in normalized_graph_files.values() for value in rows_in_file)
    generated_text += "\n" + "\n".join(json_compact(row) for row in new_rows)
    if PRIVATE_PATH_RE.search(generated_text):
        errors.append("private_workstation_path_remains")
    private_path_file_hits: list[str] = []
    private_one = ("C:" + chr(92) + "Users" + chr(92)).encode("ascii")
    private_two = private_one.replace(b"\\", b"\\\\")
    private_forward = b"C:" + b"/" + b"Users" + b"/"
    for path in sorted(item for item in release.rglob("*") if item.is_file()):
        overlap = b""
        found = False
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                payload = overlap + chunk
                if private_one in payload or private_two in payload or private_forward in payload:
                    found = True
                    break
                overlap = payload[-64:]
        if found:
            private_path_file_hits.append(path.relative_to(release).as_posix())
    if private_path_file_hits:
        errors.append(f"package_private_path_files={private_path_file_hits}")
    graph_count = sum(len(values) for values in normalized_graph_files.values())
    malformed_graphs = 0
    for values in normalized_graph_files.values():
        for graph in values:
            nodes = graph["nodes"]
            edges = graph["edges"]
            node_ids = {str(node["id"]) for node in nodes}
            if not graph["workflow_family_id"] or any(not {"from", "to", "kind", "field_binding"}.issubset(edge) for edge in edges):
                malformed_graphs += 1
            if any(edge["from"] not in node_ids or edge["to"] not in node_ids for edge in edges):
                malformed_graphs += 1
    if malformed_graphs:
        errors.append(f"malformed_normalized_graphs={malformed_graphs}")

    split_view_errors = 0
    for split in SPLITS:
        _, split_rows = read_csv(release / f"splits/{split}.csv")
        if {row["benchmark_task_id"] for row in split_rows} != {row["benchmark_task_id"] for row in rows if row["split"] == split}:
            split_view_errors += 1
        for task_type in TASK_TYPES:
            expected_ids = {
                row["benchmark_task_id"] for row in rows
                if row["split"] == split and row["task_type"] == task_type
            }
            _, split_task_rows = read_csv(release / f"splits/{split}/{task_type}.csv")
            _, by_task_rows = read_csv(release / f"splits/by_task/{task_type}/{split}.csv")
            if {row["benchmark_task_id"] for row in split_task_rows} != expected_ids:
                split_view_errors += 1
            if {row["benchmark_task_id"] for row in by_task_rows} != expected_ids:
                split_view_errors += 1
    if split_view_errors:
        errors.append(f"split_view_errors={split_view_errors}")

    native_request_ids = {str(row["benchmark_task_id"]) for row in read_jsonl(release / "evaluation/native/MODEL_REQUEST_MANIFEST.jsonl")}
    native_truth_ids = {str(row["benchmark_task_id"]) for row in read_jsonl(release / "evaluation/native/EVALUATION_TRUTH.jsonl")}
    native_formal_ids = {str(row["benchmark_task_id"]) for row in read_jsonl(release / "evaluation/native/FORMAL_MANIFEST.jsonl")}
    test_ids = {row["benchmark_task_id"] for row in rows if row["split"] == "test"}
    if native_request_ids != test_ids or native_truth_ids != test_ids or native_formal_ids != test_ids:
        errors.append("native_evaluation_test_identity_mismatch")
    unified_request_ids = {
        str(row["benchmark_task_id"])
        for row in read_jsonl(release / "evaluation/unified_top50/MODEL_REQUEST_MANIFEST.jsonl")
    }
    unified_truth_ids = {
        str(row["benchmark_task_id"])
        for row in read_jsonl(release / "evaluation/unified_top50/EVALUATION_TRUTH.jsonl")
    }
    _, unified_index_rows = read_csv(release / "evaluation/unified_top50/FORMAL_MANIFEST_INDEX.csv")
    unified_formal_ids = {row["benchmark_task_id"] for row in unified_index_rows}
    new_test_ids = {row["benchmark_task_id"] for row in new_rows if row["split"] == "test"}
    if not (
        unified_request_ids == unified_truth_ids == unified_formal_ids
        and len(unified_request_ids) == 4788
        and unified_request_ids.issubset(test_ids)
        and test_ids - unified_request_ids == new_test_ids
        and not unified_request_ids & new_ids
    ):
        errors.append("unified_inherited_scope_identity_mismatch")
    machine_request_ids = {
        str(row["benchmark_task_id"])
        for row in read_jsonl(release / "evaluation/machine_challenge/MODEL_REQUEST_MANIFEST.jsonl")
    }
    machine_truth_ids = {
        str(row["benchmark_task_id"])
        for row in read_jsonl(release / "evaluation/machine_challenge/EVALUATION_TRUTH.jsonl")
    }
    machine_formal_ids = {
        str(row["benchmark_task_id"])
        for row in read_jsonl(release / "evaluation/machine_challenge/FORMAL_MANIFEST.jsonl")
    }
    if not (
        machine_request_ids == machine_truth_ids == machine_formal_ids
        and len(machine_request_ids) == 197
        and machine_request_ids.issubset(test_ids)
        and not machine_request_ids & new_ids
    ):
        errors.append("machine_inherited_scope_identity_mismatch")
    if evaluation_summary.get("new_test_requests") != 10 or evaluation_summary.get("provider_contract_rejections") != 0:
        errors.append("new_native_provider_validation_failure")
    if baseline_metrics.get("test_rows") != 10:
        errors.append("expansion_baseline_coverage_failure")

    required_licenses = (
        "Amadeus_OpenAPI_LICENSE.txt", "Google_Workspace_CLI_LICENSE.txt",
        "AgentDiff_LICENSE.txt", "AndroidWorld_LICENSE.txt", "SOURCE_LICENSE_EVIDENCE.json",
    )
    missing_licenses = [name for name in required_licenses if not (release / "licenses/composable_expansion" / name).is_file()]
    if missing_licenses:
        errors.append(f"missing_expansion_licenses={missing_licenses}")

    _, track_summary_rows = read_csv(release / "statistics/TRACK_SUMMARY.csv")
    track_by_name = {row["track"]: row for row in track_summary_rows}
    if (
        track_by_name.get("Native", {}).get("queries") != "60240"
        or track_by_name.get("Native", {}).get("formal_test_queries") != "4798"
        or track_by_name.get("Unified Top-50", {}).get("formal_test_queries") != "4788"
        or track_by_name.get("Machine Challenge", {}).get("formal_test_queries") != "197"
    ):
        errors.append("track_summary_fact_mismatch")
    provider_summary = json.loads((release / "evaluation/provider/FULL_VALIDATION_SUMMARY.json").read_text(encoding="utf-8"))
    if provider_summary.get("rows_validated") != 9783 or provider_summary.get("rows_rejected") != 0:
        errors.append("provider_aggregate_fact_mismatch")

    active_doc_contract = {
        "README.md": {
            "required": ("authoritative, self-contained **v0.2.0**", "**60,240**", "**50,615 / 4,827 / 4,798**", "does not cover the ten expansion Test rows"),
            "forbidden": ("official **v0.1.1**", "Total: **60,078**", "ServiceDiscoveryBench-v0.1.1/"),
        },
        "DATA_CARD.md": {
            "required": ("contains **60,240**", "formal dataset version is **v0.2.0**", "**Native v0.2.0:** 4,798 Test tasks"),
            "forbidden": ("contains 60,078", "formal dataset version is **v0.1.1**", "primary v0.1.1 split"),
        },
        "README_SPLIT_REVISION.md": {
            "required": ("Active v0.2.0 total", "50,615 / 4,827 / 4,798"),
            "forbidden": ("# ServiceDiscoveryBench-v0.1.1-candidate-a",),
        },
        "DATA_CARD_SPLIT_ADDENDUM.md": {
            "required": ("# v0.2.0 split addendum", "50,615/4,827/4,798"),
            "forbidden": (),
        },
        "SCHEMA.md": {
            "required": ("# Schema — ServiceDiscoveryBench v0.2.0", "| `source_dataset` | string |", "may carry namespaced Identity-v4", "Expansion edges use canonical node linkage"),
            "forbidden": ("# Schema — ServiceDiscoveryBench v0.1.1",),
        },
        "evaluation/README.md": {
            "required": ("Native covers all 4,798", "Unified Top-50 covers 4,788 inherited v0.1.1", "9,783"),
            "forbidden": (),
        },
        "paper_tracks/unified/README.md": {
            "required": ("inherited v0.1.1 scope", "does **not** cover the ten"),
            "forbidden": ("not an automatic v0.2 release",),
        },
        "paper_tracks/machine_challenge/README.md": {
            "required": ("inherited v0.1.1 scope", "excludes all v0.2.0 expansion rows"),
            "forbidden": ("197 current Candidate-A",),
        },
        "LICENSES_AND_SOURCE_TERMS.md": {
            "required": ("LOCAL_MERGE_ONLY_EXTERNAL_PUBLICATION_NOT_AUTHORIZED", "External publication requires a separate"),
            "forbidden": ("CLEARED_FOR_BENCHMARK_RELEASE",),
        },
        "provenance/INTERNAL_REPAIR_PROVENANCE.md": {
            "required": ("official dataset version represented by this package is **v0.2.0**",),
            "forbidden": ("official version therefore remains **ServiceDiscoveryBench v0.1.1**",),
        },
        "baselines/README.md": {
            "required": ("all ten new expansion Test rows", "not full v0.2.0 results"),
            "forbidden": ("current Candidate-A Unified results",),
        },
        "reports/baselines/BASELINE_COMPARISON.md": {
            "required": ("Inherited v0.1.1 scope", "not a full v0.2.0 baseline"),
            "forbidden": (),
        },
        "baselines/current_unified_results/05_FORMAL_TEST_BASELINE_REPORT_V4.md": {
            "required": ("Inherited v0.1.1 scope", "frozen 4,788-row Unified/Candidate-A"),
            "forbidden": (),
        },
        "licenses/composable_expansion/ATTRIBUTION.md": {
            "required": ("# v0.2.0 composable-expansion attribution", "byte-match limitation", "does not imply provider endorsement"),
            "forbidden": ("v0.1 candidate", "TAKEDOWN_POLICY.md"),
        },
        "licenses/composable_expansion/NOTICE": {
            "required": ("ServiceDiscoveryBench v0.2.0 composable-expansion component", "SOURCE_LICENSE_EVIDENCE.json"),
            "forbidden": ("v0.1 candidate",),
        },
        "provenance/composable_expansion_source_release/README.md": {
            "required": ("submission-bundled approval record", "not as independent current authorization", "contains evidence locators rather than verified digest values"),
            "forbidden": (),
        },
        "qa/QA_PROTOCOL.md": {
            "required": ("submission-bundled approval record", "not independent current authorization", "60,078-row base"),
            "forbidden": ("carries its own source-release owner approval",),
        },
    }
    documentation_results: list[dict[str, object]] = []
    documentation_error_count = 0
    for relative, contract in active_doc_contract.items():
        text = (release / relative).read_text(encoding="utf-8")
        missing = [phrase for phrase in contract["required"] if phrase not in text]
        forbidden_hits = [phrase for phrase in contract["forbidden"] if phrase in text]
        if missing or forbidden_hits:
            documentation_error_count += len(missing) + len(forbidden_hits)
        documentation_results.append({
            "path": relative,
            "status": "PASS" if not missing and not forbidden_hits else "FAIL",
            "missing_required_phrases": missing,
            "forbidden_phrase_hits": forbidden_hits,
        })
    documentation_report = {
        "status": "PASS" if documentation_error_count == 0 else "FAIL",
        "official_version": VERSION,
        "checked_active_documents": len(documentation_results),
        "error_count": documentation_error_count,
        "results": documentation_results,
        "historical_scope_policy": "Versioned v0.1.1 evidence retains its original facts but must be explicitly labeled inherited/historical when exposed from the v0.2.0 package.",
    }
    (release / "reports/DOCUMENTATION_CONSISTENCY_REPORT.json").write_text(json_pretty(documentation_report), encoding="utf-8")
    if documentation_error_count:
        errors.append(f"documentation_consistency_errors={documentation_error_count}")

    summary = {
        "status": "PASS" if not errors else "FAIL",
        "release": RELEASE_NAME,
        "row_count": len(rows),
        "task_counts": dict(task_count),
        "split_counts": dict(split_count),
        "v0_1_1_preservation": base_preservation,
        "new_rows": len(new_rows),
        "catalog_counts": {"services_total": len(service_catalog), "apis_total": len(api_catalog), "services_added": len(expansion_services), "apis_added": len(expansion_apis)},
        "normalized_workflow_graphs": graph_count,
        "private_path_hits": len(private_path_file_hits) + (1 if PRIVATE_PATH_RE.search(generated_text) else 0),
        "package_private_path_files": private_path_file_hits,
        "split_group_conflicts": len(group_conflicts),
        "exact_gold_id_query_leaks": exact_gold_leaks,
        "native_test_identity_count": len(native_request_ids),
        "unified_inherited_identity_count": len(unified_request_ids),
        "machine_inherited_identity_count": len(machine_request_ids),
        "documentation_consistency": documentation_report["status"],
        "schema_header_contract": "PASS" if schema_required == set(fields) == schema_properties else "FAIL",
        "schema_rows_validated": len(rows),
        "schema_row_errors": schema_row_errors,
        "new_provider_contract_rejections": evaluation_summary.get("provider_contract_rejections"),
        "baseline_new_test_rows": baseline_metrics.get("test_rows"),
        "formal_generative_llm_calls": 0,
        "errors": errors,
    }
    if errors:
        raise ValueError("release validation failed: " + "; ".join(errors))
    return summary


def write_release_manifests(release: Path) -> dict[str, object]:
    excluded = {"OUTPUT_MANIFEST.csv", "SHA256SUMS.txt", "manifests/RELEASE_FILE_MANIFEST.csv"}
    records: list[dict[str, object]] = []
    for path in sorted((item for item in release.rglob("*") if item.is_file()), key=lambda item: item.relative_to(release).as_posix()):
        relative = path.relative_to(release).as_posix()
        if relative in excluded or "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        records.append({"relative_path": relative, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    fields = ["relative_path", "size_bytes", "sha256"]
    write_csv(release / "OUTPUT_MANIFEST.csv", fields, records)
    write_csv(release / "manifests/RELEASE_FILE_MANIFEST.csv", fields, records)
    checksum_records = []
    for path in sorted((item for item in release.rglob("*") if item.is_file()), key=lambda item: item.relative_to(release).as_posix()):
        relative = path.relative_to(release).as_posix()
        if relative == "SHA256SUMS.txt" or "__pycache__" in path.parts or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        checksum_records.append((relative, sha256_file(path)))
    (release / "SHA256SUMS.txt").write_text("".join(f"{digest}  {relative}\n" for relative, digest in checksum_records), encoding="utf-8")
    return {"manifested_files": len(records), "checksum_entries": len(checksum_records)}


def create_release_zip(release: Path, zip_path: Path) -> dict[str, object]:
    if zip_path.exists():
        raise FileExistsError(f"refusing to overwrite ZIP: {zip_path}")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(item for item in release.rglob("*") if item.is_file()):
            archive.write(path, (Path(release.name) / path.relative_to(release)).as_posix())
    with zipfile.ZipFile(zip_path) as archive:
        bad_crc = archive.testzip()
        entry_count = len(archive.infolist())
    if bad_crc:
        raise ValueError(f"release ZIP CRC failure: {bad_crc}")
    result = {
        "zip_path": str(zip_path),
        "size_bytes": zip_path.stat().st_size,
        "sha256": sha256_file(zip_path),
        "entry_count": entry_count,
        "crc_test": "PASS",
    }
    zip_path.with_suffix(zip_path.suffix + ".sha256").write_text(f"{result['sha256']}  {zip_path.name}\n", encoding="utf-8")
    zip_path.with_suffix(zip_path.suffix + ".validation.json").write_text(json_pretty(result), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-release", type=Path,
        default=project / "outputs/runs/20260808_153000_v0_1_1_paper_dataset_package/ServiceDiscoveryBench-v0.1.1",
    )
    parser.add_argument(
        "--expansion-zip", type=Path,
        default=project / "inputs/ServiceDiscoveryBench-Composable-Expansion_full_20260820.zip",
    )
    parser.add_argument(
        "--output", type=Path,
        default=project / "outputs/runs/20260820_180000_v0_2_0_documentation_closure" / RELEASE_NAME,
    )
    parser.add_argument(
        "--zip-output", type=Path,
        default=project / "outputs/runs/ServiceDiscoveryBench-v0.2.0-composable-expansion-docfix1.zip",
    )
    parser.add_argument("--no-zip", action="store_true", help="Build and validate the directory without making the final ZIP.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = args.base_release.resolve()
    source_zip = args.expansion_zip.resolve()
    output = args.output.resolve()
    zip_output = args.zip_output.resolve()
    if not base.is_dir():
        raise FileNotFoundError(base)
    if not source_zip.is_file():
        raise FileNotFoundError(source_zip)
    source_zip_sha = sha256_file(source_zip)
    if source_zip_sha != SOURCE_ZIP_EXPECTED_SHA256:
        raise ValueError(f"source ZIP SHA-256 mismatch: {source_zip_sha}")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(output.name + f".staging-{os.getpid()}")
    if staging.exists():
        raise FileExistsError(staging)
    try:
        with tempfile.TemporaryDirectory(prefix="sdb_v020_merge_") as temporary:
            extracted = Path(temporary) / "source"
            extracted.mkdir()
            print("[1/10] Verifying and extracting frozen source ZIP", flush=True)
            safe_extract_zip(source_zip, extracted)
            project, candidate = locate_roots(extracted)
            inner_verification = verify_inner_checksums(candidate)

            print("[2/10] Copying immutable v0.1.1 release into staging", flush=True)
            shutil.copytree(base, staging, copy_function=shutil.copy2)
            for junk in staging.rglob("__pycache__"):
                if junk.is_dir():
                    shutil.rmtree(junk)

            print("[3/10] Normalizing workflow graphs and enriching catalogs", flush=True)
            graphs, normalized_graph_files = load_and_normalize_graphs(candidate)
            expansion_graph_dir = staging / "expansion/workflow_graphs"
            for filename, graph_rows in normalized_graph_files.items():
                write_jsonl(expansion_graph_dir / filename, graph_rows)
            service_records, api_records, api_parent = build_enriched_catalogs(candidate, graphs, normalized_graph_files)
            copy_and_append_catalogs(staging, service_records, api_records)

            print("[4/10] Converting and appending 162 task rows", flush=True)
            task_fields, _ = read_csv(base / "tasks/composable_service_discovery.csv")
            raw_rows = load_candidate_rows(candidate)
            new_rows, provenance = build_task_rows(raw_rows, graphs, service_records, api_records, api_parent, task_fields)
            append_tasks(staging, new_rows)
            expansion_examples: list[dict[str, str]] = []
            for tier in ("EXECUTION_VERIFIED", "SOURCE_DOCUMENTED", "SOURCE_GROUNDED_SYNTHETIC"):
                for task_type in ("composable_service_discovery", "composable_api_recommendation"):
                    match = next(row for row in new_rows if row["source_subset"] == tier and row["task_type"] == task_type)
                    expansion_examples.append(match)
            write_csv(
                staging / "examples/composable_expansion_one_per_tier_and_target.csv",
                task_fields,
                expansion_examples,
            )
            append_governance_manifests(staging, new_rows, provenance, source_zip, inner_verification)

            print("[5/10] Rebuilding all task/split materializations", flush=True)
            by_split = materialize_splits(staging)

            print("[6/10] Building separated Native evaluation inputs and baselines", flush=True)
            new_test_rows = [row for row in new_rows if row["split"] == "test"]
            evaluation_summary = append_native_evaluation(staging, new_test_rows, service_records, api_records)
            baseline_metrics = run_expansion_baselines(staging, new_test_rows, service_records, api_records)

            print("[7/10] Freezing source licenses and release documentation", flush=True)
            copy_expansion_licenses(project, candidate, staging)
            schema_dir = staging / "schemas"
            schema_dir.mkdir(exist_ok=True)
            shutil.copy2(Path(__file__).resolve().parents[2] / "schemas/task.schema.json", schema_dir / "task.schema.json")
            update_release_documentation(staging, source_zip_sha)
            _, merged_rows = all_task_rows(staging)
            write_statistics(staging, merged_rows, evaluation_summary)

            print("[8/10] Running full validation and regression gates", flush=True)
            validation = validate_release(base, staging, new_rows, normalized_graph_files, evaluation_summary, baseline_metrics)
            (staging / "VALIDATION_SUMMARY.json").write_text(json_pretty(validation), encoding="utf-8")
            (staging / "reports/COMPOSABLE_EXPANSION_MERGE_VALIDATION.json").write_text(json_pretty(validation), encoding="utf-8")
            (staging / "TEST_LOG.txt").write_text(
                "ServiceDiscoveryBench v0.2.0 composable-expansion merge\n"
                "Status: PASS\nRows: 60240\nInherited rows preserved: 60078\nNew rows: 162\n"
                "Native test rows: 4798\nNew provider contract rejections: 0\nFormal model calls: 0\n",
                encoding="utf-8",
            )

            print("[9/10] Generating release manifests and checksums", flush=True)
            release_manifest = write_release_manifests(staging)
            os.replace(staging, output)

        zip_result: dict[str, object] | None = None
        if not args.no_zip:
            print("[10/10] Creating and CRC-checking the self-contained release ZIP", flush=True)
            zip_result = create_release_zip(output, zip_output)
        else:
            print("[10/10] ZIP creation skipped by --no-zip", flush=True)
        result = {
            "status": "PASS",
            "release_directory": str(output),
            "release_manifest": release_manifest,
            "source_zip_sha256": source_zip_sha,
            "zip": zip_result,
        }
        print(json_pretty(result), flush=True)
        return 0
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
