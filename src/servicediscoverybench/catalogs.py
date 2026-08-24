from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .normalize import normalize_text


CATALOG_VERSION = "v0.1-g1"


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: object, length: int = 24) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()[:length]


def safe_component(value: object) -> str:
    text = normalize_text(value, casefold=True)
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-")
    return text[:80]


def service_id(namespace: str, source_identity: object) -> str:
    ns = safe_component(namespace)
    identity = safe_component(source_identity)
    if not identity or len(identity) > 64:
        identity = stable_hash(source_identity)
    return f"svc::{ns}::{identity}"


def api_id(parent_service_id: str, source_identity: object) -> str:
    return f"api::{parent_service_id}::{stable_hash(source_identity)}"


def resolve_toolbench_static_service(services: Iterable[dict], service_key: str, service_name: str) -> str | None:
    """Resolve a frozen ToolBench static object without name-only guessing."""
    rows = [row for row in services if row.get("source_dataset") == "ToolBench"]
    explicit_id = service_id("ToolBench", f"static::{normalize_text(service_key, casefold=True)}")
    if any(row.get("service_id") == explicit_id for row in rows):
        return explicit_id
    name = normalize_text(service_name, casefold=True)
    matches = [row["service_id"] for row in rows if normalize_text(row.get("canonical_name"), casefold=True) == name]
    return matches[0] if len(matches) == 1 else None


def resolve_toolbench_static_api(
    apis: Iterable[dict], parent_service_id: str, function_key: str, api_name: str, method: str
) -> str | None:
    """Resolve an API first by frozen function key, then by exact sibling signature."""
    siblings = [row for row in apis if row.get("parent_service_id") == parent_service_id]
    key = normalize_text(function_key, casefold=True)
    key_matches = [row["api_id"] for row in siblings if normalize_text(row.get("source_api_id"), casefold=True) == key]
    if len(key_matches) == 1:
        return key_matches[0]
    name = normalize_text(api_name, casefold=True)
    http_method = normalize_text(method).upper()
    signature_matches = [
        row["api_id"]
        for row in siblings
        if normalize_text(row.get("canonical_name"), casefold=True) == name
        and normalize_text(row.get("http_method")).upper() == http_method
    ]
    return signature_matches[0] if len(signature_matches) == 1 else None


def choose_description(left: str, right: str) -> str:
    candidates = [normalize_text(left), normalize_text(right)]
    return sorted(candidates, key=lambda text: (-len(text), text))[0]


def merge_catalog_record(existing: dict, incoming: dict, immutable: Iterable[str]) -> dict:
    for key in immutable:
        if existing.get(key) != incoming.get(key):
            raise ValueError(f"conflicting {key} for {existing.get('service_id') or existing.get('api_id')}")
    result = dict(existing)
    result["description"] = choose_description(existing.get("description", ""), incoming.get("description", ""))
    provenance = set(json.loads(existing.get("metadata_json") or "{}").get("observed_source_rows", []))
    provenance.update(json.loads(incoming.get("metadata_json") or "{}").get("observed_source_rows", []))
    metadata = json.loads(existing.get("metadata_json") or "{}")
    metadata["observed_source_rows"] = sorted(provenance)
    result["metadata_json"] = stable_json(metadata)
    return result


def build_name_only_crosswalk(services: Iterable[dict]) -> list[dict]:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for service in services:
        key = normalize_text(service["canonical_name"], casefold=True)
        if key:
            by_name[key].append(service)
    rows = []
    for name, group in sorted(by_name.items()):
        by_source: dict[str, list[dict]] = defaultdict(list)
        for service in group:
            by_source[service["source_dataset"]].append(service)
        sources = sorted(by_source)
        for i, left_source in enumerate(sources):
            for right_source in sources[i + 1 :]:
                for left in by_source[left_source]:
                    for right in by_source[right_source]:
                        left_host = normalize_text(left.get("host_or_base_url"), casefold=True)
                        right_host = normalize_text(right.get("host_or_base_url"), casefold=True)
                        exact_host = bool(left_host and left_host == right_host)
                        status = "exact_provider_host_match" if exact_host else "ambiguous_no_merge"
                        method = "provider_host_exact" if exact_host else "normalized_name_signal_only"
                        rows.append({
                            "crosswalk_id": f"xw::{stable_hash([left['service_id'], right['service_id']])}",
                            "left_source_dataset": left_source,
                            "left_service_id": left["service_id"],
                            "right_source_dataset": right_source,
                            "right_service_id": right["service_id"],
                            "alignment_status": status,
                            "alignment_method": method,
                            "provider_host_evidence_json": stable_json({"left": left_host, "right": right_host, "exact": exact_host}),
                            "endpoint_signature_evidence_json": "[]",
                            "schema_description_evidence_json": stable_json({"normalized_name_equal": True, "normalized_name": name}),
                            "catalog_version_evidence_json": stable_json({"left": left["catalog_version"], "right": right["catalog_version"]}),
                            "reviewer_id": "",
                            "reviewed_at": "",
                            "notes": "Name equality alone is not merge authority." if not exact_host else "Automatically aligned only by exact non-empty provider/host evidence.",
                        })
    return rows


def validate_catalogs(services: list[dict], apis: list[dict], crosswalk: list[dict]) -> list[dict]:
    issues: list[dict] = []
    service_ids = [row["service_id"] for row in services]
    api_ids = [row["api_id"] for row in apis]
    if len(service_ids) != len(set(service_ids)):
        issues.append({"severity": "ERROR", "code": "DUPLICATE_SERVICE_ID", "record_id": "", "detail": "service_id is not globally unique"})
    if len(api_ids) != len(set(api_ids)):
        issues.append({"severity": "ERROR", "code": "DUPLICATE_API_ID", "record_id": "", "detail": "api_id is not globally unique"})
    known_services = set(service_ids)
    for row in services:
        for field in ("service_id", "canonical_name", "source_dataset", "source_path", "source_sha256", "catalog_version"):
            if not row.get(field):
                issues.append({"severity": "ERROR", "code": "MISSING_SERVICE_FIELD", "record_id": row.get("service_id", ""), "detail": field})
    for row in apis:
        if row.get("parent_service_id") not in known_services:
            issues.append({"severity": "ERROR", "code": "MISSING_PARENT_SERVICE", "record_id": row.get("api_id", ""), "detail": row.get("parent_service_id", "")})
        for field in ("api_id", "parent_service_id", "canonical_name", "source_dataset", "source_path", "source_sha256", "catalog_version"):
            if not row.get(field):
                issues.append({"severity": "ERROR", "code": "MISSING_API_FIELD", "record_id": row.get("api_id", ""), "detail": field})
    allowed = {"exact_source_identity", "exact_provider_host_match", "verified_manual_same_service", "needs_manual_confirmation", "ambiguous_no_merge", "conflict_no_merge"}
    for row in crosswalk:
        if row["alignment_status"] not in allowed:
            issues.append({"severity": "ERROR", "code": "INVALID_CROSSWALK_STATUS", "record_id": row["crosswalk_id"], "detail": row["alignment_status"]})
    return issues
