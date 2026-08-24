#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.catalogs import (  # noqa: E402
    CATALOG_VERSION,
    api_id,
    build_name_only_crosswalk,
    service_id,
    stable_json,
    validate_catalogs,
)
from servicediscoverybench.manifests import sha256_file, write_csv, write_json, write_jsonl  # noqa: E402
from servicediscoverybench.normalize import normalize_text  # noqa: E402


SERVICE_FIELDS = [
    "service_id", "canonical_name", "description", "provider", "host_or_base_url",
    "source_dataset", "source_service_id", "catalog_version", "source_path", "source_sha256", "metadata_json",
]
API_FIELDS = [
    "api_id", "parent_service_id", "canonical_name", "description", "endpoint", "http_method", "operation_id",
    "parameter_schema_json", "response_schema_json", "source_dataset", "source_api_id", "catalog_version",
    "source_path", "source_sha256", "metadata_json",
]
CROSSWALK_FIELDS = [
    "crosswalk_id", "left_source_dataset", "left_service_id", "right_source_dataset", "right_service_id",
    "alignment_status", "alignment_method", "provider_host_evidence_json", "endpoint_signature_evidence_json",
    "schema_description_evidence_json", "catalog_version_evidence_json", "reviewer_id", "reviewed_at", "notes",
]


def parse_json(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class CatalogBuilder:
    def __init__(self):
        self.services: dict[str, dict] = {}
        self.apis: dict[str, dict] = {}
        self.issues: list[dict] = []

    def add_service(self, *, source: str, source_identity: str, name: str, description: str, provider: str,
                    host: str, source_path: Path, source_sha: str, metadata: dict) -> str | None:
        name = normalize_text(name)
        if not name:
            self.issues.append({"severity": "WARNING", "code": "EMPTY_SERVICE_NAME", "record_id": source_identity, "detail": str(source_path)})
            return None
        sid = service_id(source, source_identity)
        record = {
            "service_id": sid,
            "canonical_name": name,
            "description": normalize_text(description),
            "provider": normalize_text(provider),
            "host_or_base_url": normalize_text(host),
            "source_dataset": source,
            "source_service_id": normalize_text(source_identity),
            "catalog_version": CATALOG_VERSION,
            "source_path": str(source_path),
            "source_sha256": source_sha,
            "metadata_json": stable_json(metadata),
        }
        existing = self.services.get(sid)
        if existing is None:
            self.services[sid] = record
        else:
            if normalize_text(existing["canonical_name"], casefold=True) != normalize_text(name, casefold=True):
                self.issues.append({"severity": "ERROR", "code": "SERVICE_ID_CONTENT_CONFLICT", "record_id": sid, "detail": f"{existing['canonical_name']} != {name}"})
            if len(record["description"]) > len(existing["description"]):
                existing["description"] = record["description"]
            if not existing["host_or_base_url"] and record["host_or_base_url"]:
                existing["host_or_base_url"] = record["host_or_base_url"]
        return sid

    def add_api(self, *, parent: str, source: str, source_identity: str, name: str, description: str, endpoint: str,
                method: str, operation_id: str, parameters: object, response: object, source_path: Path, source_sha: str,
                metadata: dict) -> str | None:
        name = normalize_text(name)
        if not parent or not name:
            self.issues.append({"severity": "WARNING", "code": "EMPTY_API_OR_PARENT", "record_id": source_identity, "detail": str(source_path)})
            return None
        aid = api_id(parent, [normalize_text(source_identity, casefold=True), normalize_text(method, casefold=True)])
        record = {
            "api_id": aid,
            "parent_service_id": parent,
            "canonical_name": name,
            "description": normalize_text(description),
            "endpoint": normalize_text(endpoint),
            "http_method": normalize_text(method).upper(),
            "operation_id": normalize_text(operation_id),
            "parameter_schema_json": stable_json(parameters),
            "response_schema_json": stable_json(response),
            "source_dataset": source,
            "source_api_id": normalize_text(source_identity),
            "catalog_version": CATALOG_VERSION,
            "source_path": str(source_path),
            "source_sha256": source_sha,
            "metadata_json": stable_json(metadata),
        }
        existing = self.apis.get(aid)
        if existing is None:
            self.apis[aid] = record
        else:
            immutable = ("parent_service_id", "source_dataset")
            if any(existing[key] != record[key] for key in immutable) or normalize_text(existing["canonical_name"], casefold=True) != normalize_text(name, casefold=True):
                self.issues.append({"severity": "ERROR", "code": "API_ID_CONTENT_CONFLICT", "record_id": aid, "detail": source_identity})
            if len(record["description"]) > len(existing["description"]):
                existing["description"] = record["description"]
        return aid


def toolbench(builder: CatalogBuilder, paths: list[Path], hashes: dict[Path, str]) -> None:
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                metadata = parse_json(row.get("metadata_json", ""), {})
                name = row.get("candidate_service_name", "")
                host = metadata.get("candidate_service_host") or metadata.get("candidate_service_home_url") or ""
                normalized_name = normalize_text(name, casefold=True)
                # A RapidAPI host is strong evidence, but the source can expose
                # conflicting named/versioned catalogs on one host. Preserve
                # those as separate source records instead of silently merging.
                source_identity = stable_json([host, normalized_name]) if host else normalized_name
                sid = builder.add_service(
                    source="ToolBench", source_identity=source_identity, name=name,
                    description=row.get("candidate_service_description", ""), provider="RapidAPI" if host else "",
                    host=host, source_path=path, source_sha=hashes[path],
                    metadata={"category": row.get("candidate_category", ""), "identity_basis": "host_plus_source_name" if host else "normalized_source_name"},
                )
                api_name = row.get("candidate_api_name", "")
                endpoint = api_name if api_name.strip().startswith("/") else ""
                required = parse_json(metadata.get("candidate_required_parameters_json", "[]"), [])
                optional = parse_json(metadata.get("candidate_optional_parameters_json", "[]"), [])
                builder.add_api(
                    parent=sid or "", source="ToolBench", source_identity=api_name, name=api_name,
                    description=row.get("candidate_api_description", ""), endpoint=endpoint,
                    method=metadata.get("candidate_api_method", ""), operation_id="" if endpoint else api_name,
                    parameters={"required": required, "optional": optional}, response={}, source_path=path,
                    source_sha=hashes[path], metadata={"category": row.get("candidate_category", "")},
                )


def metatool(builder: CatalogBuilder, path: Path, source_sha: str) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            builder.add_service(
                source="MetaTool", source_identity=row["service_id"], name=row["service_name"],
                description=row.get("service_description", ""), provider="", host="", source_path=path,
                source_sha=source_sha, metadata={"identity_basis": "source_service_id"},
            )


def composable_catalog(builder: CatalogBuilder, path: Path, source_sha: str) -> None:
    """Add only real static-catalog objects referenced by the frozen review pack."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    service_objects: dict[tuple[str, str], dict] = {}
    api_objects: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        for field in ("candidate_services_json", "provisional_gold_services_json"):
            for value in parse_json(row.get(field, ""), []):
                if isinstance(value, dict):
                    key = normalize_text(value.get("service_key") or value.get("service_name"), casefold=True)
                    name = normalize_text(value.get("service_name") or value.get("service_key"))
                    service_objects[(key, name)] = value
        for field in ("candidate_apis_json", "provisional_gold_apis_json"):
            for value in parse_json(row.get(field, ""), []):
                if isinstance(value, dict):
                    service_key = normalize_text(value.get("service_key") or value.get("service_name"), casefold=True)
                    service_name_value = normalize_text(value.get("service_name") or value.get("service_key"))
                    service_objects.setdefault((service_key, service_name_value), value)
                    api_key = normalize_text(value.get("function_key") or value.get("function_name") or value.get("api_name"), casefold=True)
                    api_objects[(service_key, service_name_value, api_key)] = value

    def name_index() -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for sid, record in builder.services.items():
            if record["source_dataset"] == "ToolBench":
                index.setdefault(normalize_text(record["canonical_name"], casefold=True), []).append(sid)
        return index

    index = name_index()
    resolved: dict[tuple[str, str], str] = {}
    for (source_key, name), value in sorted(service_objects.items()):
        matches = index.get(normalize_text(name, casefold=True), [])
        if len(matches) == 1:
            resolved[(source_key, name)] = matches[0]
            continue
        source_identity = f"static::{source_key or normalize_text(name, casefold=True)}"
        sid = builder.add_service(
            source="ToolBench", source_identity=source_identity, name=name,
            description=value.get("service_description", ""), provider="RapidAPI", host="",
            source_path=path, source_sha=source_sha,
            metadata={"identity_basis": "frozen_composable_static_service_key", "catalog_source_path": value.get("catalog_source_path", "")},
        )
        if sid:
            resolved[(source_key, name)] = sid
            index.setdefault(normalize_text(name, casefold=True), []).append(sid)
    for (source_key, name, source_api_key), value in sorted(api_objects.items()):
        sid = resolved.get((source_key, name))
        if not sid:
            continue
        api_name_value = value.get("api_name") or value.get("function_name") or source_api_key
        method_value = normalize_text(value.get("method", "")).upper()
        existing_api = next(
            (
                record
                for record in builder.apis.values()
                if record["parent_service_id"] == sid
                and normalize_text(record["canonical_name"], casefold=True)
                == normalize_text(api_name_value, casefold=True)
                and record["http_method"] == method_value
            ),
            None,
        )
        if existing_api is not None:
            continue
        endpoint = value.get("url", "") if str(value.get("url", "")).startswith("/") else ""
        builder.add_api(
            parent=sid, source="ToolBench", source_identity=value.get("function_key") or value.get("function_name") or api_name_value,
            name=api_name_value, description=value.get("api_description", ""), endpoint=endpoint,
            method=method_value, operation_id=value.get("function_name", ""),
            parameters={}, response={}, source_path=path, source_sha=source_sha,
            metadata={"identity_basis": "frozen_composable_static_function_key", "catalog_source_path": value.get("catalog_source_path", ""), "url": value.get("url", "")},
        )


def service_name(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("service_name") or value.get("tool_name") or value.get("name") or ""
    return ""


def stabletoolbench(builder: CatalogBuilder, path: Path, source_sha: str) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            services = parse_json(row.get("candidate_services_json", ""), [])
            apis = parse_json(row.get("candidate_apis_json", ""), [])
            service_ids: dict[str, str] = {}
            for value in services:
                name = service_name(value)
                key = normalize_text(name, casefold=True)
                if key not in service_ids:
                    sid = builder.add_service(
                        source="StableToolBench", source_identity=key, name=name, description="", provider="", host="",
                        source_path=path, source_sha=source_sha,
                        metadata={"identity_basis": "normalized_source_name", "source_group": row.get("source_group", "")},
                    )
                    if sid:
                        service_ids[key] = sid
            for value in apis:
                if not isinstance(value, dict):
                    continue
                parent_name = service_name(value)
                key = normalize_text(parent_name, casefold=True)
                sid = service_ids.get(key)
                if sid is None:
                    sid = builder.add_service(
                        source="StableToolBench", source_identity=key, name=parent_name, description="", provider="", host="",
                        source_path=path, source_sha=source_sha, metadata={"identity_basis": "api_parent_name"},
                    )
                    if sid:
                        service_ids[key] = sid
                name = value.get("api_name") or value.get("name") or ""
                builder.add_api(
                    parent=sid or "", source="StableToolBench", source_identity=name, name=name,
                    description=value.get("api_description", ""), endpoint=name if str(name).startswith("/") else "",
                    method=value.get("method", ""), operation_id="" if str(name).startswith("/") else name,
                    parameters={}, response={}, source_path=path, source_sha=source_sha,
                    metadata={"category": value.get("category_name", ""), "identity_basis": "parent_and_api_name"},
                )


def shortcuts(builder: CatalogBuilder, tasks_path: Path, api_path: Path, hashes: dict[Path, str]) -> None:
    bundle_by_name: dict[str, str] = {}
    description_by_name: dict[str, str] = {}
    with tasks_path.open("r", encoding="utf-8-sig", newline="") as handle:
        task_rows = list(csv.DictReader(handle))
    for row in task_rows:
        gold = parse_json(row.get("gold_services_json", ""), [])
        for value in gold:
            if isinstance(value, dict):
                name = service_name(value)
                bundle_by_name[normalize_text(name, casefold=True)] = row.get("source_bundle_id", "")
                description_by_name[normalize_text(name, casefold=True)] = value.get("service_description", "")
    for row in task_rows:
        for value in parse_json(row.get("candidate_services_json", ""), []):
            if not isinstance(value, dict):
                continue
            name = service_name(value)
            key = normalize_text(name, casefold=True)
            source_identity = bundle_by_name.get(key) or key
            builder.add_service(
                source="ShortcutsBench", source_identity=source_identity, name=name,
                description=value.get("service_description", ""), provider=source_identity if "." in source_identity else "",
                host="", source_path=tasks_path, source_sha=hashes[tasks_path],
                metadata={"identity_basis": "bundle_id" if source_identity != key else "normalized_strict_catalog_name"},
            )
    with api_path.open("r", encoding="utf-8-sig") as handle:
        app_records = json.load(handle)
    reverse_bundle = {bundle: name for name, bundle in bundle_by_name.items()}
    for app in app_records:
        bundle = normalize_text(app.get("AppName", ""))
        display_key = reverse_bundle.get(bundle, "")
        display_name = next((name for name in description_by_name if name == display_key), "") or bundle
        sid = builder.add_service(
            source="ShortcutsBench", source_identity=bundle, name=display_name,
            description=description_by_name.get(display_key, ""), provider=bundle, host="", source_path=api_path,
            source_sha=hashes[api_path], metadata={"identity_basis": "bundle_id"},
        )
        actions = ((app.get("extract.actionsdata") or {}).get("actions") or {})
        for api in app.get("APIs") or []:
            api_name = normalize_text(api.get("APIName", ""))
            action_key = api_name.rsplit(".", 1)[-1]
            action = actions.get(action_key) or {}
            title = ((action.get("title") or {}).get("key") or api_name)
            parameters = action.get("parameters") or []
            builder.add_api(
                parent=sid or "", source="ShortcutsBench", source_identity=api_name, name=api_name,
                description=title, endpoint="", method="INTENT", operation_id=action_key,
                parameters=parameters, response=action.get("outputType") or {}, source_path=api_path,
                source_sha=hashes[api_path], metadata={"bundle_id": bundle, "is_discoverable": action.get("isDiscoverable")},
            )


def manifest_rows(output: Path) -> list[dict]:
    rows = []
    for path in sorted((p for p in output.rglob("*") if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.as_posix()):
        rows.append({"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolbench-candidate", action="append", required=True)
    parser.add_argument("--metatool-catalog", required=True)
    parser.add_argument("--stable-tasks", required=True)
    parser.add_argument("--shortcuts-tasks", required=True)
    parser.add_argument("--shortcuts-api-source", required=True)
    parser.add_argument("--composable-review", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    paths = [Path(p).resolve() for p in args.toolbench_candidate]
    metatool_path = Path(args.metatool_catalog).resolve()
    stable_path = Path(args.stable_tasks).resolve()
    shortcuts_tasks = Path(args.shortcuts_tasks).resolve()
    shortcuts_api = Path(args.shortcuts_api_source).resolve()
    composable_review = Path(args.composable_review).resolve()
    all_inputs = paths + [metatool_path, stable_path, shortcuts_tasks, shortcuts_api, composable_review]
    hashes = {path: sha256_file(path) for path in all_inputs}

    builder = CatalogBuilder()
    toolbench(builder, paths, hashes)
    composable_catalog(builder, composable_review, hashes[composable_review])
    metatool(builder, metatool_path, hashes[metatool_path])
    stabletoolbench(builder, stable_path, hashes[stable_path])
    shortcuts(builder, shortcuts_tasks, shortcuts_api, hashes)
    services = sorted(builder.services.values(), key=lambda row: row["service_id"])
    apis = sorted(builder.apis.values(), key=lambda row: row["api_id"])
    crosswalk = build_name_only_crosswalk(services)
    issues = builder.issues + validate_catalogs(services, apis, crosswalk)
    errors = [row for row in issues if row["severity"] == "ERROR"]
    status = "GATE_PASSED" if not errors else "BLOCKED"

    catalogs_dir = output / "catalogs"
    write_jsonl(catalogs_dir / "service_catalog.jsonl", services)
    write_jsonl(catalogs_dir / "api_catalog.jsonl", apis)
    write_csv(catalogs_dir / "service_catalog_crosswalk.csv", crosswalk, CROSSWALK_FIELDS)
    write_csv(output / "VALIDATION_ISSUES.csv", issues, ["severity", "code", "record_id", "detail"])
    input_rows = [{"logical_name": path.stem, "resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": hashes[path]} for path in all_inputs]
    write_csv(output / "INPUT_MANIFEST.csv", input_rows, ["logical_name", "resolved_path", "size_bytes", "sha256"])
    counts = {
        "services": len(services), "apis": len(apis), "crosswalk_rows": len(crosswalk),
        "services_by_source": dict(Counter(row["source_dataset"] for row in services)),
        "apis_by_source": dict(Counter(row["source_dataset"] for row in apis)),
        "crosswalk_by_status": dict(Counter(row["alignment_status"] for row in crosswalk)),
        "errors": len(errors), "warnings": len(issues) - len(errors),
    }
    write_json(output / "COUNTS.json", counts)
    write_json(output / "VALIDATION_SUMMARY.json", {
        "stage": "G1", "gate": "Catalogs/crosswalk", "status": status,
        "canonical_ids_unique": not any(row["code"] in {"DUPLICATE_SERVICE_ID", "DUPLICATE_API_ID"} for row in errors),
        "all_api_parents_resolved": not any(row["code"] == "MISSING_PARENT_SERVICE" for row in errors),
        "source_provenance_present": not any(row["code"] in {"MISSING_SERVICE_FIELD", "MISSING_API_FIELD"} for row in errors),
        "llm_or_embedding_merge_used": False,
    })
    write_json(output / "RUN_STATUS.json", {
        "stage": "G1_catalogs", "status": status,
        "started_and_finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "next_executable_action": "Run G2 source candidate construction/reconstruction." if status == "GATE_PASSED" else "Resolve G1 validation errors and rerun in a new directory.",
    })
    (output / "RUN_CONFIG.yaml").write_text(
        "stage: G1_catalogs\ncatalog_version: v0.1-g1\nnormalization_version: nfkc_ws_v1\nautomatic_cross_source_merge: exact_nonempty_provider_host_only\nname_only_alignment: ambiguous_no_merge\n",
        encoding="utf-8",
    )
    (output / "COMMANDS.log").write_text(" ".join([sys.executable, *sys.argv]) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        f"# G1 Catalogs/crosswalk\n\nStatus: `{status}`. Services: {len(services)}; APIs: {len(apis)}; crosswalk rows: {len(crosswalk)}. Name-only matches are `ambiguous_no_merge`; no LLM, embedding, or fuzzy automatic merge was used.\n",
        encoding="utf-8",
    )
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest_rows(output), ["relative_path", "size_bytes", "sha256"])
    print(json.dumps({"status": status, **counts}, ensure_ascii=False, indent=2))
    return 0 if status == "GATE_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
