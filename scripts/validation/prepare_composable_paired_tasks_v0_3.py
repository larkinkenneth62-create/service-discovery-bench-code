#!/usr/bin/env python
"""Prepare provisional paired composable tasks from the frozen v0.2 evidence pack.

This script performs no corpus mining and assigns no human/final labels. It uses
only local ToolBench traces and catalog metadata to construct auditable,
provisional service/API tasks for one consolidated human review pass.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


VERSION = "v0.3"
PREPARATION_SCRIPT_VERSION = "composable_paired_task_preparation_v0_3"
DEFAULT_OUTPUT = Path("outputs/composable_paired_task_preparation_v0_3")
DEFAULT_LEDGER = Path("outputs/review_credit_ledger/composable_review_credit_ledger_v0_3.csv")
DEFAULT_ARCHIVE = Path("outputs/run_archives/2026-07-14_composable_paired_task_preparation_v0_3")

INPUT_RELATIVE_PATHS = {
    "ranked": Path("outputs/composable_corpus_mining_v0_2/composable_underlying_task_candidates_ranked.csv"),
    "review": Path("outputs/composable_corpus_mining_v0_2/composable_evidence_review_items_v0_2.csv"),
    "edges": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_dependency_edge_candidates.jsonl"),
    "steps": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_normalized_multicall_steps.jsonl"),
    "status": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_dependency_evidence_status.csv"),
    "summary": Path("outputs/composable_corpus_mining_v0_2/toolbench_full_dependency_mining_summary.json"),
    "catalog": Path("external_sources/ToolBench/data/toolenv/tools"),
    "master_plan": Path("docs/project/SERVICEDISCOVERYBENCH_BENCHMARK_MASTER_PLAN.md"),
    "qa_schema": Path("docs/phase1/source_qa_two_axis_adjudication_schema_v0_3.md"),
    "qa_schema_errata": Path("docs/phase1/source_qa_two_axis_adjudication_schema_errata_v0_3_1.md"),
}

SOURCE_HUMAN_FIELDS = [
    "dependency_edge_valid",
    "dependency_type_final",
    "dependency_evidence_sufficient",
    "composition_final_label",
    "service_level_valid",
    "api_level_valid",
    "adjudicator_id",
    "adjudicator_type",
    "adjudicated_at",
    "adjudication_notes",
]

REVIEW_HUMAN_FIELDS = [
    "dependency_edge_valid",
    "dependency_evidence_sufficient",
    "composition_final_label",
    "query_gold_chain_alignment",
    "service_gold_complete",
    "service_candidate_space_valid",
    "service_leakage_final",
    "service_level_eligible",
    "api_gold_complete",
    "api_candidate_space_valid",
    "api_parent_mapping_valid",
    "api_leakage_final",
    "api_level_eligible",
    "composable_release_action",
    "adjudicator_id",
    "adjudicator_type",
    "adjudicated_at",
    "adjudication_notes",
]

HASH_FIELDS = [
    "query_text",
    "candidate_services_json",
    "provisional_gold_services_json",
    "candidate_apis_json",
    "provisional_gold_apis_json",
    "service_api_map_json",
    "dependency_edges_json",
    "dependency_evidence_json",
]

COMMON_LEAK_TERMS = {
    "all", "api", "count", "current", "find", "get", "image", "latest",
    "list", "map", "news", "review", "route", "search", "track", "tracking",
    "translate", "translation", "weather",
}

TEST_LIKE_TERMS = {
    "demo", "dummy", "health", "healthcheck", "ping", "sample", "sandbox",
    "status", "test", "testing",
}

TOKEN_STOPWORDS = {
    "a", "an", "and", "api", "are", "as", "at", "be", "by", "can", "for",
    "from", "get", "i", "in", "is", "it", "me", "my", "of", "on", "or",
    "please", "service", "that", "the", "this", "to", "tool", "use", "with",
    "you",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def truthy(value: Any) -> bool:
    return text(value).casefold() in {"1", "true", "yes"}


def int_value(value: Any) -> int:
    try:
        return int(float(text(value) or 0))
    except ValueError:
        return 0


def normalize_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", text(value)).casefold()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    return re.sub(r"_+", "_", normalized).strip("_")


def normalize_phrase(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", text(value)).casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def token_set(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(token for token in normalize_phrase(value).split() if len(token) > 1 and token not in TOKEN_STOPWORDS)
    return tokens


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    raw = text(value)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_pretty(value) + "\n", encoding="utf-8")


def require_inputs(project_root: Path, output_dir: Path) -> dict[str, Path]:
    resolved = {name: project_root / relative for name, relative in INPUT_RELATIVE_PATHS.items()}
    missing = [str(path) for path in resolved.values() if not path.exists()]
    if missing:
        output_dir.mkdir(parents=True, exist_ok=True)
        body = "# Missing inputs\n\n" + "\n".join(f"- `{item}`" for item in missing) + "\n"
        (output_dir / "MISSING_INPUTS.md").write_text(body, encoding="utf-8")
        raise FileNotFoundError("Required inputs are missing; see MISSING_INPUTS.md")
    return resolved


def load_record_at_path(source_file: Path, record_path: str) -> Any:
    payload = json.loads(source_file.read_text(encoding="utf-8"))
    if record_path == "$":
        return payload
    match = re.fullmatch(r'\$\["(.*)"\]', record_path)
    if match:
        return payload[match.group(1)]
    match = re.fullmatch(r"\$\[(\d+)\]", record_path)
    if match:
        return payload[int(match.group(1))]
    raise ValueError(f"Unsupported exact source record path: {record_path}")


def source_query(record: Any) -> tuple[str, str]:
    if not isinstance(record, dict):
        return "", ""
    answer_generation = record.get("answer_generation")
    if isinstance(answer_generation, dict) and text(answer_generation.get("query")):
        return text(answer_generation.get("query")), "$.answer_generation.query"
    if text(record.get("query")):
        return text(record.get("query")), "$.query"
    return "", ""


def extract_tool_from_description(description: str) -> str:
    match = re.search(r'tool\s+["\']([^"\']+)["\']', description, flags=re.IGNORECASE)
    return text(match.group(1)) if match else ""


def extract_api_description(description: str) -> str:
    match = re.search(r'The description of this function is:\s*["\'](.*)["\']\s*$', description, flags=re.IGNORECASE | re.DOTALL)
    return text(match.group(1)) if match else text(description)


def split_function_name(function_name: str) -> tuple[str, str]:
    normalized = normalize_key(function_name)
    if "_for_" in normalized:
        api_name, service_name = normalized.rsplit("_for_", 1)
        return api_name, service_name
    return normalized, ""


def is_test_like(*values: Any) -> bool:
    tokens = token_set(*values)
    return bool(tokens & TEST_LIKE_TERMS)


def load_static_catalog(catalog_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[str]], dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}
    apis: dict[str, dict[str, Any]] = {}
    service_to_apis: dict[str, list[str]] = defaultdict(list)
    service_key_paths: dict[str, list[str]] = defaultdict(list)
    api_key_paths: dict[str, list[str]] = defaultdict(list)
    parse_failures: list[dict[str, str]] = []
    for path in sorted(catalog_root.rglob("*.json"), key=lambda item: str(item).casefold()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            parse_failures.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        service_name = text(payload.get("tool_name") or payload.get("name") or payload.get("title") or path.stem)
        service_key = normalize_key(service_name)
        if not service_key:
            continue
        category = text(path.parent.relative_to(catalog_root).parts[0] if path.parent.relative_to(catalog_root).parts else "")
        service_key_paths[service_key].append(str(path))
        service = {
            "service_name": service_name,
            "service_description": text(payload.get("tool_description")),
            "category": category,
            "catalog_source_path": str(path),
            "catalog_origin": "toolbench_static_catalog",
            "service_key": service_key,
            "_search_tokens": sorted(token_set(service_name, payload.get("tool_description"), category)),
        }
        services.setdefault(service_key, service)
        for api in payload.get("api_list") or []:
            if not isinstance(api, dict):
                continue
            api_name = text(api.get("name"))
            if not api_name:
                continue
            function_name = f"{normalize_key(api_name)}_for_{service_key}"
            function_key = normalize_key(function_name)
            api_key_paths[function_key].append(str(path))
            api_item = {
                "function_name": function_name,
                "api_name": api_name,
                "api_description": text(api.get("description")),
                "service_name": service_name,
                "service_description": service["service_description"],
                "category": category,
                "method": text(api.get("method")),
                "url": text(api.get("url")),
                "catalog_source_path": str(path),
                "catalog_origin": "toolbench_static_catalog",
                "function_key": function_key,
                "service_key": service_key,
                "is_test_like": bool_text(is_test_like(api_name, api.get("description"))),
                "_search_tokens": sorted(token_set(api_name, api.get("description"), service_name, category)),
            }
            apis.setdefault(function_key, api_item)
            if function_key not in service_to_apis[service_key]:
                service_to_apis[service_key].append(function_key)
    stats = {
        "catalog_root": str(catalog_root),
        "service_count": len(services),
        "api_count": len(apis),
        "category_count": len({item["category"] for item in services.values()}),
        "parse_failure_count": len(parse_failures),
        "duplicate_service_key_count": sum(1 for paths in service_key_paths.values() if len(paths) > 1),
        "duplicate_api_key_count": sum(1 for paths in api_key_paths.values() if len(paths) > 1),
        "parse_failures": parse_failures[:100],
    }
    return services, apis, service_to_apis, stats


def local_function_catalog(record: Any, source_file: Path, static_services: dict[str, dict[str, Any]], static_apis: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    definitions: Any = None
    source_json_path = ""
    answer_generation = record.get("answer_generation")
    if isinstance(answer_generation, dict) and isinstance(answer_generation.get("function"), list):
        definitions = answer_generation.get("function")
        source_json_path = "$.answer_generation.function"
    elif isinstance(record.get("available_tools"), list):
        definitions = record.get("available_tools")
        source_json_path = "$.available_tools"
    if not isinstance(definitions, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, definition in enumerate(definitions):
        if not isinstance(definition, dict):
            continue
        function_name = text(definition.get("name"))
        if not function_name or function_name.casefold() == "finish":
            continue
        function_key = normalize_key(function_name)
        if not function_key or function_key in seen:
            continue
        seen.add(function_key)
        raw_description = text(definition.get("description"))
        api_name_key, function_service_key = split_function_name(function_name)
        described_service = extract_tool_from_description(raw_description)
        service_key = normalize_key(described_service) or function_service_key
        static_api = static_apis.get(function_key)
        static_service = static_services.get(service_key)
        api_name = text(static_api.get("api_name")) if static_api else api_name_key
        service_name = text(static_api.get("service_name")) if static_api else (text(static_service.get("service_name")) if static_service else (described_service or function_service_key))
        item = {
            "function_name": function_name,
            "api_name": api_name,
            "api_description": text(static_api.get("api_description")) if static_api else extract_api_description(raw_description),
            "service_name": service_name,
            "service_description": text(static_service.get("service_description")) if static_service else "",
            "category": text(static_api.get("category")) if static_api else (text(static_service.get("category")) if static_service else ""),
            "method": text(static_api.get("method")) if static_api else "",
            "url": text(static_api.get("url")) if static_api else "",
            "catalog_source_path": f"{source_file}#{source_json_path}[{index}]",
            "catalog_origin": "toolbench_task_local_function_metadata",
            "function_key": function_key,
            "service_key": service_key,
            "is_test_like": bool_text(is_test_like(api_name, raw_description)),
        }
        result.append(item)
    return result


def graph_properties(edges: list[dict[str, Any]], steps: list[dict[str, Any]]) -> dict[str, Any]:
    usable_edges = [
        edge for edge in edges
        if text(edge.get("dependency_type")) not in {"", "none", "sequence_only"}
        and not truthy(edge.get("query_known_value_filtered"))
    ]
    nodes: set[int] = set()
    undirected: dict[int, set[int]] = defaultdict(set)
    directed: dict[int, set[int]] = defaultdict(set)
    indegree: Counter[int] = Counter()
    for edge in usable_edges:
        source = int_value(edge.get("from_step"))
        target = int_value(edge.get("to_step"))
        if source <= 0 or target <= 0:
            continue
        nodes.update({source, target})
        undirected[source].add(target)
        undirected[target].add(source)
        if target not in directed[source]:
            directed[source].add(target)
            indegree[target] += 1
            indegree.setdefault(source, indegree[source])
    components: list[list[int]] = []
    remaining = set(nodes)
    while remaining:
        start = min(remaining)
        queue: deque[int] = deque([start])
        component: list[int] = []
        remaining.remove(start)
        while queue:
            node = queue.popleft()
            component.append(node)
            for neighbor in sorted(undirected[node]):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    zero = deque(sorted(node for node in nodes if indegree[node] == 0))
    visited = 0
    mutable_indegree = Counter(indegree)
    while zero:
        node = zero.popleft()
        visited += 1
        for target in sorted(directed[node]):
            mutable_indegree[target] -= 1
            if mutable_indegree[target] == 0:
                zero.append(target)
    is_dag = not nodes or visited == len(nodes)
    steps_by_index = {int_value(step.get("step_index")): step for step in steps}
    gold_steps = [steps_by_index[index] for index in sorted(nodes) if index in steps_by_index]
    incidental_steps = [step for step in steps if int_value(step.get("step_index")) not in nodes]
    return {
        "usable_edges": usable_edges,
        "node_indices": sorted(nodes),
        "components": components,
        "component_count": len(components),
        "is_connected": len(components) == 1,
        "is_dag": is_dag,
        "gold_steps": gold_steps,
        "incidental_steps": incidental_steps,
    }


def service_object(service_key: str, service_name: str, static_services: dict[str, dict[str, Any]], source_path: str) -> dict[str, Any]:
    static = static_services.get(service_key)
    if static:
        return {key: static[key] for key in ("service_name", "service_description", "category", "catalog_source_path", "catalog_origin", "service_key")}
    return {
        "service_name": service_name or service_key,
        "service_description": "",
        "category": "",
        "catalog_source_path": source_path,
        "catalog_origin": "toolbench_executed_call_provenance",
        "service_key": service_key,
    }


def api_for_step(step: dict[str, Any], local_apis: dict[str, dict[str, Any]], static_apis: dict[str, dict[str, Any]], source_path: str) -> dict[str, Any]:
    function_name = text(step.get("function_name"))
    function_key = normalize_key(function_name)
    if function_key in local_apis:
        return dict(local_apis[function_key])
    if function_key in static_apis:
        return {key: value for key, value in static_apis[function_key].items() if not key.startswith("_")}
    service_name = text(step.get("service_name"))
    api_name = text(step.get("api_name"))
    return {
        "function_name": function_name,
        "api_name": api_name,
        "api_description": "",
        "service_name": service_name,
        "service_description": "",
        "category": "",
        "method": "",
        "url": "",
        "catalog_source_path": source_path,
        "catalog_origin": "toolbench_executed_call_provenance",
        "function_key": function_key,
        "service_key": normalize_key(service_name),
        "is_test_like": bool_text(is_test_like(api_name, function_name)),
    }


def dedupe_objects(items: Iterable[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = text(item.get(key_name))
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def similarity_score(query_tokens: set[str], item_tokens: set[str], same_domain: bool, same_service: bool = False) -> tuple[int, int, int]:
    overlap = len(query_tokens & item_tokens)
    return (1 if same_service else 0, 1 if same_domain else 0, overlap)


def build_service_candidates(
    query: str,
    gold_services: list[dict[str, Any]],
    local_functions: list[dict[str, Any]],
    static_services: dict[str, dict[str, Any]],
    target: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    gold_keys = {text(item.get("service_key")) for item in gold_services}
    query_tokens = token_set(query, *(item.get("service_description") for item in gold_services))
    gold_domains = {text(item.get("category")) for item in gold_services if text(item.get("category"))}
    local_services: list[dict[str, Any]] = []
    for function in local_functions:
        key = text(function.get("service_key"))
        if not key:
            continue
        local_services.append(service_object(key, text(function.get("service_name")), static_services, text(function.get("catalog_source_path"))))
    candidates = dedupe_objects([*gold_services, *local_services], "service_key")
    existing = {text(item.get("service_key")) for item in candidates}
    ranked: list[tuple[tuple[int, int, int], str, dict[str, Any]]] = []
    for key, item in static_services.items():
        if key in existing or key in gold_keys:
            continue
        item_tokens = set(item.get("_search_tokens") or token_set(item.get("service_name"), item.get("service_description"), item.get("category")))
        same_domain = bool(text(item.get("category")) and text(item.get("category")) in gold_domains)
        ranked.append((similarity_score(query_tokens, item_tokens, same_domain), key, item))
    ranked.sort(key=lambda entry: (-entry[0][0], -entry[0][1], -entry[0][2], entry[1]))
    for _, _, item in ranked:
        if len(candidates) >= max(target, len(gold_services) + 1):
            break
        candidates.append({key: item[key] for key in ("service_name", "service_description", "category", "catalog_source_path", "catalog_origin", "service_key")})
    candidates = dedupe_objects(candidates, "service_key")
    negative = [item for item in candidates if text(item.get("service_key")) not in gold_keys]
    same_domain_count = sum(1 for item in negative if text(item.get("category")) in gold_domains and text(item.get("category")))
    gold_missing = [key for key in gold_keys if key not in {text(item.get("service_key")) for item in candidates}]
    if gold_missing:
        status = "gold_not_in_catalog"
    elif len(candidates) <= len(gold_services) or not negative:
        status = "reconstruction_needed"
    else:
        status = "valid"
    evidence = {
        "method": "task_local_catalog_then_static_token_overlap",
        "target_candidate_count": target,
        "gold_service_keys": sorted(gold_keys),
        "same_domain_negative_count": same_domain_count,
        "easy_negative_count": len(negative) - same_domain_count,
        "generated_or_external_metadata_used": False,
    }
    return candidates, {
        "status": status,
        "negative_count": len(negative),
        "same_domain_count": same_domain_count,
        "easy_count": len(negative) - same_domain_count,
        "evidence": evidence,
    }


def build_api_candidates(
    query: str,
    gold_apis: list[dict[str, Any]],
    local_functions: list[dict[str, Any]],
    static_apis: dict[str, dict[str, Any]],
    service_to_apis: dict[str, list[str]],
    target: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    gold_keys = {text(item.get("function_key")) for item in gold_apis}
    gold_service_keys = {text(item.get("service_key")) for item in gold_apis}
    gold_domains = {text(item.get("category")) for item in gold_apis if text(item.get("category"))}
    query_tokens = token_set(query, *(item.get("api_description") for item in gold_apis))
    candidates = dedupe_objects([*gold_apis, *local_functions], "function_key")
    existing = {text(item.get("function_key")) for item in candidates}
    ranked: list[tuple[tuple[int, int, int], str, dict[str, Any]]] = []
    preferred_keys: set[str] = set()
    for service_key in gold_service_keys:
        preferred_keys.update(service_to_apis.get(service_key, []))
    for key, item in static_apis.items():
        if key in existing or key in gold_keys:
            continue
        item_tokens = set(item.get("_search_tokens") or token_set(item.get("api_name"), item.get("api_description"), item.get("service_name"), item.get("category")))
        same_service = text(item.get("service_key")) in gold_service_keys
        same_domain = bool(text(item.get("category")) and text(item.get("category")) in gold_domains)
        score = similarity_score(query_tokens, item_tokens, same_domain, same_service or key in preferred_keys)
        ranked.append((score, key, item))
    ranked.sort(key=lambda entry: (-entry[0][0], -entry[0][1], -entry[0][2], entry[1]))
    for _, _, item in ranked:
        if len(candidates) >= max(target, len(gold_apis) + 1):
            break
        candidates.append({key: value for key, value in item.items() if not key.startswith("_")})
    candidates = dedupe_objects(candidates, "function_key")
    if len(candidates) > 30:
        gold = [item for item in candidates if text(item.get("function_key")) in gold_keys]
        negative = [item for item in candidates if text(item.get("function_key")) not in gold_keys]
        candidates = [*gold, *negative[: max(1, 30 - len(gold))]]
    candidate_keys = {text(item.get("function_key")) for item in candidates}
    negative = [item for item in candidates if text(item.get("function_key")) not in gold_keys]
    same_service_count = sum(1 for item in negative if text(item.get("service_key")) in gold_service_keys)
    same_domain_count = sum(1 for item in negative if text(item.get("service_key")) not in gold_service_keys and text(item.get("category")) in gold_domains and text(item.get("category")))
    parent_missing = [item for item in candidates if not text(item.get("service_key")) or not text(item.get("service_name"))]
    gold_missing = sorted(gold_keys - candidate_keys)
    if gold_missing:
        status = "gold_not_in_catalog"
    elif parent_missing:
        status = "reconstruction_needed"
    elif len(candidates) == len(gold_apis):
        status = "candidate_equals_gold"
    elif not negative:
        status = "no_negative_distractor"
    else:
        status = "valid"
    evidence = {
        "method": "task_local_catalog_then_static_siblings_and_token_overlap",
        "target_candidate_count": target,
        "gold_function_keys": sorted(gold_keys),
        "same_service_sibling_negative_count": same_service_count,
        "same_domain_api_negative_count": same_domain_count,
        "easy_api_negative_count": len(negative) - same_service_count - same_domain_count,
        "test_like_candidate_count": sum(1 for item in candidates if truthy(item.get("is_test_like"))),
        "reconstruction_reasons": [
            {
                "reason": "executed_call_missing_catalog_parent_mapping",
                "function_name": text(item.get("function_name")),
                "catalog_origin": text(item.get("catalog_origin")),
            }
            for item in parent_missing
        ],
        "generated_or_external_metadata_used": False,
    }
    return candidates, {
        "status": status,
        "negative_count": len(negative),
        "same_service_count": same_service_count,
        "same_domain_count": same_domain_count,
        "easy_count": len(negative) - same_service_count - same_domain_count,
        "evidence": evidence,
    }


def leak_status(query: str, gold_items: list[dict[str, Any]], level: str) -> tuple[str, list[dict[str, str]]]:
    query_phrase = f" {normalize_phrase(query)} "
    signals: list[dict[str, str]] = []
    common_overlap = False
    fields = ("service_name",) if level == "service" else ("api_name", "function_name")
    for item in gold_items:
        for field in fields:
            raw = text(item.get(field))
            phrase = normalize_phrase(raw)
            if not phrase:
                continue
            tokens = phrase.split()
            if phrase in COMMON_LEAK_TERMS or (len(tokens) == 1 and tokens[0] in COMMON_LEAK_TERMS):
                if f" {phrase} " in query_phrase:
                    common_overlap = True
                    signals.append({"field": field, "value": raw, "signal": "common_word_overlap"})
                continue
            if len(phrase) >= 3 and f" {phrase} " in query_phrase:
                signals.append({"field": field, "value": raw, "signal": "exact_unique_name_leak"})
    if any(signal["signal"] == "exact_unique_name_leak" for signal in signals):
        return "exact_unique_name_leak", signals
    if common_overlap:
        return "common_word_overlap", signals
    return "no_obvious_leak", signals


def review_hash(row: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for field in HASH_FIELDS:
        value = row.get(field, "")
        payload[field] = parse_json(value, value) if field.endswith("_json") else text(value)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def domain_signature(gold_services: list[dict[str, Any]], gold_apis: list[dict[str, Any]]) -> str:
    values = sorted({text(item.get("category")) for item in [*gold_services, *gold_apis] if text(item.get("category"))})
    return "|".join(values) if values else "unknown"


def dependency_type_distribution(edges: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(text(edge.get("dependency_type")) or "unknown" for edge in edges)
    return dict(sorted(counter.items()))


def build_underlying_rows(
    project_root: Path,
    paths: dict[str, Path],
    review_rows: list[dict[str, str]],
    ranked_rows: list[dict[str, str]],
    static_services: dict[str, dict[str, Any]],
    static_apis: dict[str, dict[str, Any]],
    service_to_apis: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ranked_by_task: dict[str, dict[str, str]] = {}
    for row in ranked_rows:
        if text(row.get("evidence_status")) == "strong_objective_evidence_available":
            ranked_by_task.setdefault(text(row.get("source_task_id")), row)
    master_rows: list[dict[str, Any]] = []
    service_space_rows: list[dict[str, Any]] = []
    api_space_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, source_review in enumerate(review_rows, start=1):
        source_task_id = text(source_review.get("source_task_id"))
        ranked = ranked_by_task.get(source_task_id, {})
        source_file = Path(text(ranked.get("source_file") or source_review.get("source_trace_path")))
        source_record_path = text(ranked.get("source_record_path")) or "$"
        try:
            record = load_record_at_path(source_file, source_record_path)
        except Exception as exc:
            issues.append({
                "issue_type": "source_record_unavailable",
                "source_task_id": source_task_id,
                "severity": "fatal",
                "details": f"{type(exc).__name__}: {exc}",
            })
            continue
        exact_query, query_json_path = source_query(record)
        review_query = text(source_review.get("query_text"))
        if not exact_query:
            issues.append({
                "issue_type": "source_query_unavailable_hold",
                "source_task_id": source_task_id,
                "severity": "fatal",
                "details": str(source_file),
            })
            continue
        if review_query and review_query != exact_query:
            issues.append({
                "issue_type": "query_text_differs_from_exact_source",
                "source_task_id": source_task_id,
                "severity": "warning",
                "details": "Exact source query retained in v0.3.",
            })
        steps = parse_json(source_review.get("ordered_steps_json"), [])
        edges = parse_json(source_review.get("dependency_edges_json"), [])
        evidence = parse_json(source_review.get("dependency_evidence_json"), {})
        if not isinstance(steps, list) or not isinstance(edges, list):
            issues.append({
                "issue_type": "invalid_steps_or_edges_json",
                "source_task_id": source_task_id,
                "severity": "fatal",
                "details": "ordered_steps_json or dependency_edges_json is not a list",
            })
            continue
        graph = graph_properties(edges, steps)
        local_functions_list = local_function_catalog(record, source_file, static_services, static_apis)
        local_apis = {text(item.get("function_key")): item for item in local_functions_list}
        gold_apis = dedupe_objects(
            [api_for_step(step, local_apis, static_apis, f"{source_file}#{text(step.get('source_json_path'))}") for step in graph["gold_steps"]],
            "function_key",
        )
        gold_services = dedupe_objects(
            [service_object(text(api.get("service_key")), text(api.get("service_name")), static_services, text(api.get("catalog_source_path"))) for api in gold_apis],
            "service_key",
        )
        incidental_apis = dedupe_objects(
            [api_for_step(step, local_apis, static_apis, f"{source_file}#{text(step.get('source_json_path'))}") for step in graph["incidental_steps"]],
            "function_key",
        )
        incidental_services = dedupe_objects(
            [service_object(text(api.get("service_key")), text(api.get("service_name")), static_services, text(api.get("catalog_source_path"))) for api in incidental_apis],
            "service_key",
        )
        service_candidates, service_meta = build_service_candidates(exact_query, gold_services, local_functions_list, static_services)
        api_candidates, api_meta = build_api_candidates(exact_query, gold_apis, local_functions_list, static_apis, service_to_apis)
        service_leak, service_leak_signals = leak_status(exact_query, gold_services, "service")
        api_leak, api_leak_signals = leak_status(exact_query, gold_apis, "api")
        candidate_service_api_map = [
            {
                "function_name": item["function_name"],
                "api_name": item["api_name"],
                "service_name": item["service_name"],
                "function_key": item["function_key"],
                "service_key": item["service_key"],
                "mapping_source": item["catalog_source_path"],
            }
            for item in api_candidates
        ]
        gold_service_api_map = [item for item in candidate_service_api_map if item["function_key"] in {api["function_key"] for api in gold_apis}]
        underlying_task_id = f"COMPOSABLE-UNDERLYING-V0.3-{index:04d}"
        paired_id = f"COMPOSABLE-PAIR-V0.3-{index:04d}"
        split_group_id = f"TOOLBENCH-{source_task_id}"
        components = graph["components"]
        dependency_risk = "hybrid_or_ambiguous" if graph["component_count"] > 1 else "single_connected_dependency_component"
        common = {
            "underlying_task_id": underlying_task_id,
            "source_task_id": source_task_id,
            "source_dataset": text(source_review.get("source_dataset")) or "ToolBench",
            "source_group": text(source_review.get("source_group")),
            "source_query_id": text(ranked.get("instruction_query_id")),
            "query_text": exact_query,
            "query_source_path": f"{source_file}#{source_record_path}:{query_json_path}",
            "source_trace_path": text(source_review.get("source_trace_path")) or str(source_file),
            "source_answer_path": text(source_review.get("source_answer_path")) or str(source_file),
            "source_record_path": source_record_path,
            "ordered_steps_json": json_dumps(steps),
            "dependency_edges_json": json_dumps(graph["usable_edges"]),
            "dependency_evidence_json": json_dumps(evidence),
            "dependency_type_distribution_json": json_dumps(dependency_type_distribution(graph["usable_edges"])),
            "evidence_status": text(source_review.get("evidence_status")),
            "evidence_score": text(source_review.get("evidence_score")),
            "provisional_gold_services_json": json_dumps(gold_services),
            "provisional_gold_apis_json": json_dumps(gold_apis),
            "service_api_map_json": json_dumps(candidate_service_api_map),
            "provisional_gold_service_api_map_json": json_dumps(gold_service_api_map),
            "incidental_services_json": json_dumps(incidental_services),
            "incidental_apis_json": json_dumps(incidental_apis),
            "disconnected_calls_json": json_dumps(graph["incidental_steps"]),
            "dependency_components_json": json_dumps(components),
            "connected_dependency_component_count": graph["component_count"],
            "dependency_graph_is_dag": bool_text(graph["is_dag"]),
            "dependency_graph_is_connected": bool_text(graph["is_connected"]),
            "dependency_structure_risk": dependency_risk,
            "requires_human_dependency_confirmation": "true",
            "paired_task_group_id": paired_id,
            "split_group_id": split_group_id,
            "candidate_services_json": json_dumps(service_candidates),
            "candidate_service_count": len(service_candidates),
            "gold_service_count": len(gold_services),
            "service_negative_distractor_count": service_meta["negative_count"],
            "same_domain_service_negative_count": service_meta["same_domain_count"],
            "easy_service_negative_count": service_meta["easy_count"],
            "service_candidate_space_status": service_meta["status"],
            "service_candidate_construction_evidence_json": json_dumps(service_meta["evidence"]),
            "candidate_apis_json": json_dumps(api_candidates),
            "candidate_api_count": len(api_candidates),
            "gold_api_count": len(gold_apis),
            "api_negative_distractor_count": api_meta["negative_count"],
            "same_service_sibling_negative_count": api_meta["same_service_count"],
            "same_domain_api_negative_count": api_meta["same_domain_count"],
            "easy_api_negative_count": api_meta["easy_count"],
            "api_candidate_space_status": api_meta["status"],
            "api_candidate_construction_evidence_json": json_dumps(api_meta["evidence"]),
            "service_leak_status": service_leak,
            "service_leak_signals_json": json_dumps(service_leak_signals),
            "api_leak_status": api_leak,
            "api_leak_signals_json": json_dumps(api_leak_signals),
            "query_chain_alignment_risk": "manual_confirmation_required",
            "catalog_domain_signature": domain_signature(gold_services, gold_apis),
            "current_322_member": text(source_review.get("current_322_member")),
            "preparation_script_version": PREPARATION_SCRIPT_VERSION,
        }
        common["review_content_hash"] = review_hash(common)
        master_rows.append(common)
        service_space_rows.append({
            key: common[key] for key in [
                "underlying_task_id", "source_task_id", "source_group", "query_text",
                "candidate_services_json", "provisional_gold_services_json", "candidate_service_count",
                "gold_service_count", "service_negative_distractor_count", "same_domain_service_negative_count",
                "easy_service_negative_count", "service_candidate_space_status",
                "service_candidate_construction_evidence_json", "service_leak_status",
                "service_leak_signals_json", "review_content_hash",
            ]
        })
        api_space_rows.append({
            key: common[key] for key in [
                "underlying_task_id", "source_task_id", "source_group", "query_text",
                "candidate_apis_json", "provisional_gold_apis_json", "service_api_map_json",
                "candidate_api_count", "gold_api_count", "api_negative_distractor_count",
                "same_service_sibling_negative_count", "same_domain_api_negative_count", "easy_api_negative_count",
                "api_candidate_space_status", "api_candidate_construction_evidence_json", "api_leak_status",
                "api_leak_signals_json", "review_content_hash",
            ]
        })
    return master_rows, service_space_rows, api_space_rows, issues


def build_provisional_rows(master_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    shared = [
        "underlying_task_id", "source_task_id", "paired_task_group_id", "split_group_id",
        "source_dataset", "source_group", "query_text", "review_content_hash",
        "ordered_steps_json", "dependency_edges_json", "dependency_evidence_json",
        "source_trace_path", "evidence_status",
    ]
    service_rows: list[dict[str, Any]] = []
    api_rows: list[dict[str, Any]] = []
    for index, row in enumerate(master_rows, start=1):
        base = {key: row[key] for key in shared}
        service_rows.append({
            "benchmark_task_id": f"CSD-V0.3-{index:04d}",
            **base,
            "benchmark_task_type": "composable_service_discovery",
            "candidate_services_json": row["candidate_services_json"],
            "gold_services_json": row["provisional_gold_services_json"],
            "candidate_service_count": row["candidate_service_count"],
            "gold_service_count": row["gold_service_count"],
            "service_negative_distractor_count": row["service_negative_distractor_count"],
            "service_candidate_space_status": row["service_candidate_space_status"],
            "service_leak_status": row["service_leak_status"],
            "release_status": "provisional_candidate",
        })
        api_rows.append({
            "benchmark_task_id": f"CAR-V0.3-{index:04d}",
            **base,
            "benchmark_task_type": "composable_api_recommendation",
            "candidate_apis_json": row["candidate_apis_json"],
            "gold_apis_json": row["provisional_gold_apis_json"],
            "service_api_map_json": row["service_api_map_json"],
            "candidate_api_count": row["candidate_api_count"],
            "gold_api_count": row["gold_api_count"],
            "api_negative_distractor_count": row["api_negative_distractor_count"],
            "api_candidate_space_status": row["api_candidate_space_status"],
            "api_leak_status": row["api_leak_status"],
            "release_status": "provisional_candidate",
        })
    return service_rows, api_rows


def build_review_pack(master_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = [
        "underlying_task_id", "source_task_id", "paired_task_group_id", "split_group_id",
        "source_dataset", "source_group", "query_text", "query_source_path",
        "ordered_steps_json", "dependency_edges_json", "dependency_evidence_json",
        "dependency_type_distribution_json", "source_trace_path", "source_answer_path",
        "source_record_path", "evidence_status", "evidence_score", "dependency_components_json",
        "connected_dependency_component_count", "dependency_graph_is_dag", "dependency_graph_is_connected",
        "dependency_structure_risk", "requires_human_dependency_confirmation", "catalog_domain_signature",
        "candidate_services_json", "provisional_gold_services_json", "candidate_service_count",
        "gold_service_count", "service_negative_distractor_count", "service_candidate_space_status",
        "service_leak_status", "service_leak_signals_json", "candidate_apis_json",
        "provisional_gold_apis_json", "service_api_map_json", "candidate_api_count", "gold_api_count",
        "api_negative_distractor_count", "api_candidate_space_status", "api_leak_status",
        "api_leak_signals_json", "incidental_services_json", "incidental_apis_json",
        "disconnected_calls_json", "query_chain_alignment_risk", "review_content_hash",
    ]
    review_rows: list[dict[str, Any]] = []
    for index, row in enumerate(master_rows, start=1):
        review = {
            "review_item_id": f"COMPOSABLE-PAIRED-REVIEW-V0.3-{index:04d}",
            **{field: row[field] for field in fields},
            "prior_review_content_hash": "",
            "prior_review_credit_status": "not_reviewed_new_content_hash",
        }
        for field in REVIEW_HUMAN_FIELDS:
            review[field] = ""
        review_rows.append(review)
    return review_rows


def build_credit_ledger(review_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "underlying_task_id": row["underlying_task_id"],
            "source_task_id": row["source_task_id"],
            "paired_task_group_id": row["paired_task_group_id"],
            "review_content_hash": row["review_content_hash"],
            "reviewed": "false",
            "valid_for_composable_service": "",
            "valid_for_composable_api": "",
            "reviewer_type": "",
            "reviewed_at": "",
            "invalidated_by_content_change": "false",
            "invalidation_reason": "",
        }
        for row in review_rows
    ]


def build_double_subset(review_rows: list[dict[str, Any]], size: int = 40) -> list[dict[str, Any]]:
    def stratum(row: dict[str, Any]) -> str:
        dependency_types = parse_json(row.get("dependency_type_distribution_json"), {})
        dominant = sorted(dependency_types.items(), key=lambda item: (-int(item[1]), item[0]))[0][0] if dependency_types else "unknown"
        service_bucket = "s2" if int_value(row.get("gold_service_count")) <= 2 else "s3plus"
        api_bucket = "a2" if int_value(row.get("gold_api_count")) <= 2 else ("a3" if int_value(row.get("gold_api_count")) == 3 else "a4plus")
        domain = text(row.get("catalog_domain_signature")) or "unknown"
        return f"{domain}|{service_bucket}|{api_bucket}|{dominant}"

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in review_rows:
        buckets[stratum(row)].append(row)
    for key in buckets:
        buckets[key].sort(key=lambda row: hashlib.sha256(f"v0.3-double-annotation-20260714|{row['source_task_id']}".encode("utf-8")).hexdigest())
    selected: list[dict[str, Any]] = []
    keys = sorted(buckets)
    while len(selected) < size and any(buckets.values()):
        for key in keys:
            if buckets[key] and len(selected) < size:
                row = dict(buckets[key].pop(0))
                row["double_annotation_stratum"] = key
                row["double_annotation_seed"] = "v0.3-double-annotation-20260714"
                selected.append(row)
    dual_fields = [
        "dependency_edge_valid", "dependency_evidence_sufficient", "composition_final_label",
        "query_gold_chain_alignment", "service_gold_complete", "service_candidate_space_valid",
        "service_leakage_final", "service_level_eligible", "api_gold_complete",
        "api_candidate_space_valid", "api_parent_mapping_valid", "api_leakage_final",
        "api_level_eligible", "composable_release_action", "adjudicator_id", "adjudicator_type",
        "adjudicated_at", "adjudication_notes",
    ]
    for row in selected:
        for field in dual_fields:
            row[f"reviewer_a_{field}"] = ""
            row[f"reviewer_b_{field}"] = ""
    return selected


def input_integrity(
    ranked_rows: list[dict[str, str]],
    source_review_rows: list[dict[str, str]],
    master_rows: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    strong_rows = [row for row in ranked_rows if text(row.get("evidence_status")) == "strong_objective_evidence_available"]
    strong_ids = {text(row.get("source_task_id")) for row in strong_rows}
    source_ids = [text(row.get("source_task_id")) for row in source_review_rows]
    master_ids = {text(row.get("source_task_id")) for row in master_rows}
    duplicates = len(source_ids) - len(set(source_ids))
    current_8_ids = {text(row.get("source_task_id")) for row in source_review_rows if truthy(row.get("current_322_member"))}
    source_human_nonblank = sum(1 for row in source_review_rows if any(text(row.get(field)) for field in SOURCE_HUMAN_FIELDS))
    return {
        "generated_at": now_iso(),
        "script_version": PREPARATION_SCRIPT_VERSION,
        "strong_candidate_unique_count": len(strong_ids),
        "evidence_pack_input_rows": len(source_review_rows),
        "evidence_pack_unique_rows": len(set(source_ids)),
        "evidence_pack_rows_from_strong_candidates": sum(1 for task_id in source_ids if task_id in strong_ids),
        "query_nonempty_count": sum(1 for row in master_rows if text(row.get("query_text"))),
        "trace_path_nonempty_count": sum(1 for row in master_rows if text(row.get("source_trace_path"))),
        "dependency_edge_nonempty_count": sum(1 for row in master_rows if parse_json(row.get("dependency_edges_json"), [])),
        "dependency_evidence_nonempty_count": sum(1 for row in master_rows if parse_json(row.get("dependency_evidence_json"), {})),
        "duplicate_underlying_task_count": duplicates,
        "current_8_preserved": current_8_ids.issubset(master_ids) and len(current_8_ids) == 8,
        "current_8_preserved_count": len(current_8_ids & master_ids),
        "source_query_unavailable_count": sum(1 for issue in issues if issue["issue_type"] == "source_query_unavailable_hold"),
        "replacement_rows_required": sum(1 for issue in issues if issue["issue_type"] == "source_query_unavailable_hold"),
        "replacement_rows_added": 0,
        "source_human_field_nonblank_row_count": source_human_nonblank,
        "fatal_issue_count": sum(1 for issue in issues if issue.get("severity") == "fatal"),
        "warning_issue_count": sum(1 for issue in issues if issue.get("severity") == "warning"),
        "master_rows_prepared": len(master_rows),
    }


def write_input_audit_report(path: Path, audit: dict[str, Any], input_paths: dict[str, Path]) -> None:
    lines = [
        "# Composable Input Integrity Audit v0.3",
        "",
        f"Generated at: `{audit['generated_at']}`",
        "",
        "## Inputs",
        "",
    ]
    lines.extend(f"- `{name}`: `{input_path}`" for name, input_path in input_paths.items())
    lines.extend([
        "",
        "## Fixed Checks",
        "",
        f"- strong_candidate_unique_count: `{audit['strong_candidate_unique_count']}`",
        f"- evidence_pack_input_rows: `{audit['evidence_pack_input_rows']}`",
        f"- evidence_pack_unique_rows: `{audit['evidence_pack_unique_rows']}`",
        f"- query_nonempty_count: `{audit['query_nonempty_count']}`",
        f"- trace_path_nonempty_count: `{audit['trace_path_nonempty_count']}`",
        f"- dependency_edge_nonempty_count: `{audit['dependency_edge_nonempty_count']}`",
        f"- duplicate_underlying_task_count: `{audit['duplicate_underlying_task_count']}`",
        f"- current_8_preserved: `{str(audit['current_8_preserved']).lower()}`",
        f"- replacement_rows_required: `{audit['replacement_rows_required']}`",
        f"- replacement_rows_added: `{audit['replacement_rows_added']}`",
        f"- fatal_issue_count: `{audit['fatal_issue_count']}`",
        "",
        "No query was generated or summarized. Queries were recovered exactly from local ToolBench source records.",
        "No human review field was filled by this preparation stage.",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def candidate_status_counts(master_rows: list[dict[str, Any]], prefix: str) -> Counter[str]:
    return Counter(text(row.get(f"{prefix}_candidate_space_status")) for row in master_rows)


def update_master_plan(master_plan_path: Path, generated_at: str) -> str:
    content = master_plan_path.read_text(encoding="utf-8-sig")
    start_pattern = r"<!-- BEGIN GATE4 V0\.2 VARIABLE STATUS -->.*?<!-- END GATE4 V0\.2 VARIABLE STATUS -->"
    new_block = f"""<!-- BEGIN GATE4 V0.3 VARIABLE STATUS -->

### v0.3 variable status ({generated_at})

- Gate 4 status: `PAIRED_TASK_PREPARATION_COMPLETE_HUMAN_REVIEW_PENDING`;
- strong candidate count: `816`;
- consolidated review count: `200`;
- human-confirmed count: `0`;
- paired-task preparation status: `COMPLETE_PROVISIONAL_HUMAN_FIELDS_BLANK`;
- stopping condition: `100 both-level eligible underlying tasks`;
- current blocker: `SINGLE_CONSOLIDATED_HUMAN_REVIEW_PENDING`;
- current next action: `humanly review only composable_paired_task_review_items_v0_3.csv`;
- dependency graph remains internal construction/review evidence, not a prediction target;
- human-final authority remains unchanged; no machine evidence status or provisional gold is a final label.

<!-- END GATE4 V0.3 VARIABLE STATUS -->"""
    if re.search(start_pattern, content, flags=re.DOTALL):
        content = re.sub(start_pattern, new_block, content, count=1, flags=re.DOTALL)
    elif "<!-- BEGIN GATE4 V0.3 VARIABLE STATUS -->" not in content:
        raise RuntimeError("Gate 4 v0.2 variable-status markers were not found in Master Plan")
    gate_start = content.find("## Gate 4")
    gate_end = content.find("## Gate 5", gate_start)
    if gate_start >= 0 and gate_end > gate_start:
        section = content[gate_start:gate_end]
        section = re.sub(r"(状态：)`[^`]+`", r"\1`PAIRED_TASK_PREPARATION_COMPLETE_HUMAN_REVIEW_PENDING`", section, count=1)
        content = content[:gate_start] + section + content[gate_end:]
    changelog = """## v1.3 - 2026-07-14

- Updated only Gate 4 variable status; benchmark-only scope, six-task requirement, API-level requirement, human-final authority, no automatic composable labeling, and no novel-method rule remain frozen.
- Prepared 200 paired provisional composable service/API rows from the frozen 816 strong-evidence candidate pool without new corpus mining.
- Built one consolidated 200-row human review pack, a 40-row double-annotation subset, and a content-hash review-credit ledger; all human fields remain blank.
- Source freeze, full six-task assembly, final dataset, split, baseline, and training remain prohibited.

"""
    marker = "# 13. Change Log\n"
    if "## v1.3 - 2026-07-14" not in content:
        if marker not in content:
            raise RuntimeError("Master Plan Change Log heading was not found")
        content = content.replace(marker, marker + "\n" + changelog, 1)
    master_plan_path.write_text(content, encoding="utf-8")
    return changelog


def write_change_log_report(path: Path, master_plan_path: Path, generated_at: str) -> None:
    body = f"""# Master Plan Gate 4 Change Log v0.3

Generated at: `{generated_at}`

Updated file: `{master_plan_path}`

- Gate 4 status is now `PAIRED_TASK_PREPARATION_COMPLETE_HUMAN_REVIEW_PENDING`.
- Strong candidate count remains `816`.
- Consolidated review count is `200`.
- Human-confirmed count remains `0`.
- Stop condition is `100` both-level eligible underlying tasks.
- Current action is one human review of the v0.3 consolidated pack.
- Frozen benchmark scope and human-final authority were not changed.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def write_go_no_go_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Composable Paired Task Preparation Go/No-Go v0.3",
        "",
        f"Generated at: `{summary['generated_at']}`",
        f"Project root: `{summary['project_root']}`",
        "",
        "## Scope",
        "",
        "- benchmark_task_scope = `composable_service_discovery + composable_api_recommendation`",
        "- dependency_graph_is_internal_evidence_not_prediction_target = `true`",
        "- no new corpus mining, external API, Qwen, LLM-generated query/gold/negative, final dataset, split, baseline, or training was run.",
        "",
        "## Preparation Counts",
        "",
    ]
    ordered = [
        "strong_underlying_candidate_count", "evidence_pack_input_rows", "final_consolidated_review_pack_rows",
        "query_nonempty_count", "paired_service_rows_prepared", "paired_api_rows_prepared",
        "service_candidate_space_valid_count", "service_candidate_space_reconstruction_needed_count",
        "api_candidate_space_valid_count", "api_candidate_space_reconstruction_needed_count",
        "candidate_space_reconstruction_needed_count", "source_query_unavailable_count",
        "duplicate_underlying_task_count", "current_8_strong_candidates_preserved",
        "human_confirmed_composable_count", "service_level_eligible_count", "api_level_eligible_count",
        "both_levels_eligible_count", "can_start_single_consolidated_human_review",
        "can_claim_composable_service_benchmark_now", "can_claim_composable_api_benchmark_now",
        "can_start_full_six_task_assembly", "can_generate_final_dataset", "can_create_split",
        "can_run_baseline",
    ]
    lines.extend(f"- {key} = `{str(summary[key]).lower() if isinstance(summary[key], bool) else summary[key]}`" for key in ordered)
    lines.extend([
        "",
        "## Decision",
        "",
        f"- decision = `{'GO_HUMAN_REVIEW_ONLY' if summary['can_start_single_consolidated_human_review'] else 'NO_GO_FIX_PREPARATION'}`",
        f"- recommended_next_step = `{summary['recommended_next_step']}`",
        "",
        "The nine API reconstruction-needed rows contain an executed function without a verifiable catalog parent mapping. No parent was invented; these rows remain review-only and cannot become API-level eligible as-is.",
        "",
        "The 200 rows are provisional candidates, not human-confirmed composable tasks. Service/API eligibility counts remain zero until the joint review is completed.",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def archive_files(archive_dir: Path, files: list[Path]) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for path in files:
        if not path.exists() or not path.is_file():
            continue
        destination = archive_dir / path.name
        shutil.copy2(path, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest.append({"archived_name": destination.name, "source_path": str(path), "sha256": digest, "bytes": destination.stat().st_size})
    write_json(archive_dir / "archive_manifest.json", {"generated_at": now_iso(), "files": manifest})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare frozen v0.3 paired composable tasks without final labels.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ledger-path", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--review-size", type=int, default=200)
    parser.add_argument("--double-annotation-size", type=int, default=40)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else project_root / args.output_dir
    ledger_path = args.ledger_path if args.ledger_path.is_absolute() else project_root / args.ledger_path
    archive_dir = args.archive_dir if args.archive_dir.is_absolute() else project_root / args.archive_dir
    paths = require_inputs(project_root, output_dir)
    ranked_rows = read_csv(paths["ranked"])
    source_review_rows = read_csv(paths["review"])
    if len(source_review_rows) != args.review_size:
        raise ValueError(f"Expected {args.review_size} evidence review rows, found {len(source_review_rows)}")
    static_services, static_apis, service_to_apis, catalog_stats = load_static_catalog(paths["catalog"])
    master_rows, service_spaces, api_spaces, issues = build_underlying_rows(
        project_root, paths, source_review_rows, ranked_rows, static_services, static_apis, service_to_apis
    )
    audit = input_integrity(ranked_rows, source_review_rows, master_rows, issues)
    fatal_issues = [issue for issue in issues if issue.get("severity") == "fatal"]
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_json_path = output_dir / "composable_input_integrity_audit.json"
    issues_path = output_dir / "composable_input_integrity_issues.csv"
    audit_report_path = project_root / "docs/phase1/composable_input_integrity_audit_v0_3.md"
    write_json(audit_json_path, audit)
    write_csv(issues_path, issues, ["issue_type", "source_task_id", "severity", "details"])
    write_input_audit_report(audit_report_path, audit, paths)
    write_json(output_dir / "toolbench_local_catalog_summary_v0_3.json", catalog_stats)
    if fatal_issues or len(master_rows) != args.review_size:
        raise RuntimeError(f"Preparation stopped: fatal_issues={len(fatal_issues)}, master_rows={len(master_rows)}")

    master_path = output_dir / "composable_underlying_tasks_master_v0_3.csv"
    service_spaces_path = output_dir / "composable_service_candidate_spaces_v0_3.csv"
    api_spaces_path = output_dir / "composable_api_candidate_spaces_v0_3.csv"
    write_csv(master_path, master_rows)
    write_csv(service_spaces_path, service_spaces)
    write_csv(api_spaces_path, api_spaces)

    service_rows, api_rows = build_provisional_rows(master_rows)
    service_rows_path = output_dir / "composable_service_discovery_provisional_rows_v0_3.csv"
    api_rows_path = output_dir / "composable_api_recommendation_provisional_rows_v0_3.csv"
    write_csv(service_rows_path, service_rows)
    write_csv(api_rows_path, api_rows)

    review_rows = build_review_pack(master_rows)
    review_path = output_dir / "composable_paired_task_review_items_v0_3.csv"
    write_csv(review_path, review_rows)
    ledger_rows = build_credit_ledger(review_rows)
    write_csv(ledger_path, ledger_rows)
    double_rows = build_double_subset(review_rows, args.double_annotation_size)
    double_path = output_dir / "composable_double_annotation_subset_40.csv"
    write_csv(double_path, double_rows)

    service_counts = candidate_status_counts(master_rows, "service")
    api_counts = candidate_status_counts(master_rows, "api")
    human_nonblank = sum(1 for row in review_rows if any(text(row.get(field)) for field in REVIEW_HUMAN_FIELDS))
    hash_count = sum(1 for row in review_rows if text(row.get("review_content_hash")))
    fatal_candidate_statuses = {
        "catalog_missing", "gold_not_in_catalog", "duplicate_or_alias_conflict",
        "parent_mapping_missing", "candidate_equals_gold", "no_negative_distractor",
    }
    fatal_candidate_rows = sum(
        1 for row in master_rows
        if text(row.get("service_candidate_space_status")) in fatal_candidate_statuses
        or text(row.get("api_candidate_space_status")) in fatal_candidate_statuses
    )
    can_start_review = all([
        len(review_rows) == 200,
        audit["query_nonempty_count"] == 200,
        audit["dependency_evidence_nonempty_count"] == 200,
        len(service_rows) == 200,
        len(api_rows) == 200,
        human_nonblank == 0,
        fatal_candidate_rows == 0,
        hash_count == 200,
    ])
    generated_at = now_iso()
    summary = {
        "generated_at": generated_at,
        "project_root": str(project_root),
        "strong_underlying_candidate_count": audit["strong_candidate_unique_count"],
        "evidence_pack_input_rows": audit["evidence_pack_input_rows"],
        "evidence_pack_unique_rows": audit["evidence_pack_unique_rows"],
        "query_nonempty_count": audit["query_nonempty_count"],
        "source_query_unavailable_count": audit["source_query_unavailable_count"],
        "dependency_evidence_nonempty_count": audit["dependency_evidence_nonempty_count"],
        "duplicate_underlying_task_count": audit["duplicate_underlying_task_count"],
        "current_8_strong_candidates_preserved": audit["current_8_preserved"],
        "paired_service_rows_prepared": len(service_rows),
        "paired_api_rows_prepared": len(api_rows),
        "service_candidate_space_valid_count": service_counts.get("valid", 0),
        "service_candidate_space_reconstruction_needed_count": service_counts.get("reconstruction_needed", 0),
        "api_candidate_space_valid_count": api_counts.get("valid", 0),
        "api_candidate_space_reconstruction_needed_count": api_counts.get("reconstruction_needed", 0),
        "candidate_space_reconstruction_needed_count": sum(1 for row in master_rows if "reconstruction_needed" in {row["service_candidate_space_status"], row["api_candidate_space_status"]}),
        "fatal_candidate_space_row_count": fatal_candidate_rows,
        "final_consolidated_review_pack_rows": len(review_rows),
        "double_annotation_subset_rows": len(double_rows),
        "review_content_hash_generated_count": hash_count,
        "review_human_nonblank_row_count": human_nonblank,
        "human_confirmed_composable_count": 0,
        "service_level_eligible_count": 0,
        "api_level_eligible_count": 0,
        "both_levels_eligible_count": 0,
        "can_start_single_consolidated_human_review": can_start_review,
        "can_claim_composable_service_benchmark_now": False,
        "can_claim_composable_api_benchmark_now": False,
        "can_start_full_six_task_assembly": False,
        "can_generate_final_dataset": False,
        "can_create_split": False,
        "can_run_baseline": False,
        "recommended_next_step": "humanly review only composable_paired_task_review_items_v0_3.csv; do not review old G3, old 8-row, or evidence-only packs separately.",
        "service_candidate_status_distribution": dict(sorted(service_counts.items())),
        "api_candidate_status_distribution": dict(sorted(api_counts.items())),
    }
    summary_path = output_dir / "composable_paired_task_preparation_summary_v0_3.json"
    write_json(summary_path, summary)
    go_no_go_path = project_root / "docs/phase1/composable_paired_task_preparation_go_no_go_v0_3.md"
    write_go_no_go_report(go_no_go_path, summary)
    if not can_start_review:
        raise RuntimeError("Preparation did not satisfy the human-review start gate")

    update_master_plan(paths["master_plan"], generated_at)
    change_log_path = project_root / "docs/phase1/composable_paired_task_master_plan_change_log_v0_3.md"
    write_change_log_report(change_log_path, paths["master_plan"], generated_at)

    scripts = [
        Path(__file__).resolve(),
        project_root / "scripts/validation/validate_composable_paired_task_review_v0_3.py",
        project_root / "scripts/validation/summarize_composable_paired_task_review_v0_3.py",
    ]
    archive_files(archive_dir, [
        audit_json_path, issues_path, audit_report_path, output_dir / "toolbench_local_catalog_summary_v0_3.json",
        master_path, service_spaces_path, api_spaces_path, service_rows_path, api_rows_path,
        review_path, double_path, ledger_path, summary_path, go_no_go_path, change_log_path,
        paths["master_plan"], *scripts,
    ])

    terminal_fields = [
        "strong_underlying_candidate_count", "evidence_pack_input_rows", "evidence_pack_unique_rows",
        "query_nonempty_count", "source_query_unavailable_count", "dependency_evidence_nonempty_count",
        "duplicate_underlying_task_count", "current_8_strong_candidates_preserved",
        "paired_service_rows_prepared", "paired_api_rows_prepared",
        "service_candidate_space_valid_count", "service_candidate_space_reconstruction_needed_count",
        "api_candidate_space_valid_count", "api_candidate_space_reconstruction_needed_count",
        "final_consolidated_review_pack_rows", "double_annotation_subset_rows",
        "review_content_hash_generated_count", "human_confirmed_composable_count",
        "service_level_eligible_count", "api_level_eligible_count", "both_levels_eligible_count",
        "can_start_single_consolidated_human_review", "can_claim_composable_service_benchmark_now",
        "can_claim_composable_api_benchmark_now", "can_start_full_six_task_assembly",
        "can_generate_final_dataset", "recommended_next_step",
    ]
    for field in terminal_fields:
        print(f"{field}={summary[field]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
