#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate Round2 small dry-run review package for main four tasks.

Scope guard:
- No full cleaning.
- No baseline.
- No model training.
- No train/dev/test split.
- No top200 continuation.
- No full G3 search.
- No automatic final clean labels.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4"
DOCS_DIR = PROJECT_ROOT / "docs" / "phase1"
ARCHIVE_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "run_archives"
    / "2026-06-26_round2_small_dryrun_review_package_v0_4"
)

REQUIRED_INPUTS = [
    PROJECT_ROOT / "outputs" / "main_four_tasks_rule_validation_v0_3" / "manual40_rule_validation_predictions.csv",
    PROJECT_ROOT / "outputs" / "main_four_tasks_rule_validation_v0_3" / "manual40_rule_validation_comparison.csv",
    PROJECT_ROOT / "outputs" / "main_four_tasks_rule_validation_v0_3" / "manual40_rule_validation_fatal_errors.csv",
    PROJECT_ROOT / "outputs" / "main_four_tasks_rule_validation_v0_3" / "manual40_rule_validation_warnings.csv",
    DOCS_DIR / "main_four_tasks_rule_validation_manual40_v0_3_report.md",
    DOCS_DIR / "main_four_tasks_rule_validation_manual40_v0_3_next_step.md",
    PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "main_four_tasks_manual_gold_validation_set_40.csv",
    PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "main_four_tasks_manual_decisions_40_user_approved_round1.csv",
    PROJECT_ROOT / "outputs" / "main_four_tasks_dryrun_v0_2" / "multi_service_discovery_task_level.csv",
    PROJECT_ROOT / "outputs" / "main_four_tasks_dryrun_v0_2" / "multi_api_recommendation_task_level.csv",
    DOCS_DIR / "main_four_tasks_fail_closed_rule_update_after_manual40_v0_2.md",
    DOCS_DIR / "main_four_tasks_cleaning_script_validation_plan_after_manual40_v0_2.md",
    DOCS_DIR / "manual_audit_rule_v3_3.md",
    DOCS_DIR / "service_discovery_bench_v0_2_schema_draft.md",
]

MANUAL40_CSV = (
    PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_manual_check_v0_2"
    / "main_four_tasks_manual_gold_validation_set_40.csv"
)

RAW_G1 = PROJECT_ROOT / "external_sources" / "ToolBench" / "data" / "instruction" / "G1_query.json"
RAW_G2 = PROJECT_ROOT / "external_sources" / "ToolBench" / "data" / "instruction" / "G2_query.json"

PRIORITY_A = [
    PROJECT_ROOT / "outputs" / "toolbench_full_raw_v0_1_streaming" / "G1_task_level.csv",
    PROJECT_ROOT / "outputs" / "toolbench_full_raw_v0_1_streaming" / "G2_task_level.csv",
    PROJECT_ROOT / "outputs" / "toolbench_full_raw_v0_1_streaming" / "G1_candidate_level.csv",
    PROJECT_ROOT / "outputs" / "toolbench_full_raw_v0_1_streaming" / "G2_candidate_level.csv",
]
PRIORITY_B = [
    PROJECT_ROOT / "outputs" / "toolbench_full_raw_v0_1" / "toolbench_service_candidates_sample.csv",
    PROJECT_ROOT / "outputs" / "toolbench_full_raw_v0_1" / "toolbench_task_level_sample.csv",
]
PRIORITY_C = [RAW_G1, RAW_G2]

POOL_SERVICE_CSV = OUT_DIR / "round2_multi_service_candidates_pool.csv"
POOL_API_CSV = OUT_DIR / "round2_multi_api_candidates_pool.csv"
SELECTED_80_CSV = OUT_DIR / "round2_selected_80.csv"
SELECTED_SERVICE_CSV = OUT_DIR / "round2_selected_multi_service_40.csv"
SELECTED_API_CSV = OUT_DIR / "round2_selected_multi_api_40.csv"
HTML_APP = OUT_DIR / "main_four_tasks_round2_review_app_80.html"
SUMMARY_JSON = OUT_DIR / "round2_small_dryrun_summary_v0_4.json"
SAMPLING_REPORT = DOCS_DIR / "main_four_tasks_round2_small_dryrun_sampling_report_v0_4.md"
INSTRUCTION_DOC = DOCS_DIR / "main_four_tasks_round2_manual_review_instruction_v0_4.md"

TASK_FIELDS = [
    "round2_review_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "leak_status",
    "semantic_alignment_status",
    "cleaning_status",
    "task_eligibility",
    "task_bucket",
    "metadata_json",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "high_risk_generic_tracking",
    "high_risk_generic_address_or_postal",
    "high_risk_service_leak",
    "high_risk_candidate_count_close",
    "high_risk_gold_not_unique_possible",
    "mechanical_screening_bucket",
    "mechanical_screening_reason",
    "query_signature",
]

POOL_LIMIT_PER_TASK_TYPE = 260
SCAN_LIMIT_PER_GROUP = 6000
G1_API_POOL_TARGET = 100
G1_API_SELECTED_TARGET = 12
SELECT_TARGETS = {
    "high_confidence_candidate": 20,
    "boundary_review": 15,
    "high_risk_review": 5,
}


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def signature(*parts: Any) -> str:
    joined = "\n".join(normalize_text(part) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def iter_json_array(path: Path) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    buffer = ""
    started = False
    with path.open("r", encoding="utf-8") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            buffer += chunk
            pos = 0
            if not started:
                start = buffer.find("[")
                if start < 0:
                    continue
                pos = start + 1
                started = True
            while True:
                while pos < len(buffer) and buffer[pos] in " \r\n\t,":
                    pos += 1
                if pos >= len(buffer):
                    break
                if buffer[pos] == "]":
                    return
                try:
                    obj, end = decoder.raw_decode(buffer, pos)
                except json.JSONDecodeError:
                    break
                if isinstance(obj, dict):
                    yield obj
                pos = end
            buffer = buffer[pos:]


def get_gold_apis(task: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in task.get("relevant APIs", []) or []:
        if isinstance(item, list | tuple) and len(item) >= 2:
            out.append((str(item[0]), str(item[1])))
    return out


def api_key(service: Any, api: Any) -> tuple[str, str]:
    return normalize_text(service), normalize_text(api)


def query_mentions_any(query: str, names: Iterable[str]) -> int:
    q = normalize_text(query)
    for name in names:
        n = normalize_text(name)
        if len(n) >= 3 and n in q:
            return 1
    return 0


def unique_service_records(candidates: list[dict[str, Any]], gold_services: set[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    services: list[dict[str, Any]] = []
    for cand in candidates:
        service = str(cand.get("tool_name", "") or "")
        if not service or normalize_text(service) in seen:
            continue
        seen.add(normalize_text(service))
        services.append(
            {
                "category_name": cand.get("category_name", ""),
                "service_name": service,
                "service_description": "",
                "is_gold_service": normalize_text(service) in gold_services,
            }
        )
    return services


def api_records(candidates: list[dict[str, Any]], gold_api_keys: set[tuple[str, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for cand in candidates:
        service = str(cand.get("tool_name", "") or "")
        api = str(cand.get("api_name", "") or "")
        records.append(
            {
                "category_name": cand.get("category_name", ""),
                "service_name": service,
                "api_name": api,
                "api_description": cand.get("api_description", ""),
                "method": cand.get("method", ""),
                "is_gold_api": api_key(service, api) in gold_api_keys,
            }
        )
    return records


def counts_from_records(
    services: list[dict[str, Any]],
    apis: list[dict[str, Any]],
    gold_services: list[str],
    gold_apis: list[dict[str, str]],
) -> dict[str, int]:
    return {
        "candidate_service_count": len({normalize_text(s.get("service_name", "")) for s in services if s.get("service_name")}),
        "gold_service_count": len({normalize_text(s) for s in gold_services if s}),
        "candidate_api_count": len(apis),
        "gold_api_count": len(gold_apis),
    }


def annotate_task(task: dict[str, Any], group: str, task_type: str) -> dict[str, Any] | None:
    query = str(task.get("query", "") or "")
    source_query_id = str(task.get("query_id", "") or "")
    candidates = task.get("api_list", []) or []
    if not query or not isinstance(candidates, list) or not candidates:
        return None
    gold_pairs = get_gold_apis(task)
    if not gold_pairs:
        return None
    gold_services = sorted({service for service, _api in gold_pairs})
    gold_service_norms = {normalize_text(s) for s in gold_services}
    gold_api_keys = {api_key(service, api) for service, api in gold_pairs}
    services = unique_service_records(candidates, gold_service_norms)
    apis = api_records(candidates, gold_api_keys)
    gold_api_records = [{"service_name": service, "api_name": api} for service, api in gold_pairs]
    counts = counts_from_records(services, apis, gold_services, gold_api_records)
    query_mentions_gold_api = query_mentions_any(query, [api for _service, api in gold_pairs])
    query_mentions_gold_service = query_mentions_any(query, gold_services)
    if query_mentions_gold_api:
        leak_status = "api_leak"
        cleaning_status = "remove_api_leak_candidate"
    elif query_mentions_gold_service:
        leak_status = "service_leak_only"
        cleaning_status = "service_leak_only_review"
    else:
        leak_status = "no_obvious_leak"
        cleaning_status = "dryrun_candidate"
    metadata = {
        "source_query_id": source_query_id,
        "original_raw_task_type": "raw_toolbench_instruction",
        **counts,
        "query_mentions_any_gold_api": query_mentions_gold_api,
        "query_mentions_any_gold_service": query_mentions_gold_service,
        "dry_run_note": "round2 small dry-run candidate only; not full cleaning; not final label",
    }
    return {
        "round2_review_id": "",
        "task_id": f"ToolBench_{group}_{source_query_id}",
        "task_type": task_type,
        "source_dataset": "ToolBench",
        "source_group": group,
        "query_text": query,
        "candidate_services_json": json_dumps(services),
        "candidate_apis_json": json_dumps(apis),
        "gold_services_json": json_dumps(gold_services),
        "gold_apis_json": json_dumps(gold_api_records),
        "leak_status": leak_status,
        "semantic_alignment_status": "unverified_round2_dryrun",
        "cleaning_status": cleaning_status,
        "task_eligibility": "service_level_round2_candidate" if task_type == "multi_service_discovery" else "api_level_round2_candidate",
        "task_bucket": task_type,
        "metadata_json": json_dumps(metadata),
        **counts,
        "query_mentions_any_gold_api": query_mentions_gold_api,
        "query_mentions_any_gold_service": query_mentions_gold_service,
        "query_signature": signature(query),
    }


TRACKING_TERMS = re.compile(r"\b(track|tracking|shipment|package|parcel|mail|delivery|awb|cargo)\b", re.I)
ADDRESS_TERMS = re.compile(r"\b(address|postal|postcode|zip|cep|city|state|office|restaurant|post office)\b", re.I)


def add_hints(row: dict[str, Any]) -> dict[str, Any]:
    q = row["query_text"]
    gold_services = json.loads(row["gold_services_json"] or "[]")
    gold_text = " ".join(str(s) for s in gold_services).lower()
    generic_tracking = bool(TRACKING_TERMS.search(q)) and not int(row["query_mentions_any_gold_service"])
    generic_tracking = generic_tracking and any(
        term in gold_text
        for term in ["post", "tracking", "australia", "argentina", "container", "transnistria", "pack", "send"]
    )
    generic_address = bool(ADDRESS_TERMS.search(q)) and not int(row["query_mentions_any_gold_service"])
    generic_address = generic_address and any(term in gold_text for term in ["cep", "postal", "brazil", "turkey"])
    service_leak = row["leak_status"] == "service_leak_only" or int(row["query_mentions_any_gold_service"])
    if row["task_type"] == "multi_service_discovery":
        count_close = int(row["candidate_service_count"]) <= int(row["gold_service_count"]) + 1
    else:
        count_close = int(row["candidate_api_count"]) <= int(row["gold_api_count"]) + 1
    gold_not_unique = generic_tracking or generic_address or count_close
    reasons: list[str] = []
    if int(row["query_mentions_any_gold_api"]) or row["leak_status"] == "api_leak":
        bucket = "high_risk_review"
        reasons.append("possible API leak")
    elif service_leak:
        bucket = "boundary_review"
        reasons.append("service leak only or gold service mentioned")
    elif generic_tracking or generic_address or count_close or gold_not_unique:
        bucket = "boundary_review"
        if generic_tracking:
            reasons.append("generic tracking bound to specific service")
        if generic_address:
            reasons.append("generic address/postal lookup bound to specific service")
        if count_close:
            reasons.append("candidate/gold count is close")
        if gold_not_unique:
            reasons.append("gold may not be uniquely supported by query")
    else:
        bucket = "high_confidence_candidate"
        reasons.append("no obvious leak and candidate/gold counts provide choice space")
    if int(row["query_mentions_any_gold_api"]) and (generic_tracking or generic_address):
        bucket = "high_risk_review"
    row.update(
        {
            "high_risk_generic_tracking": int(generic_tracking),
            "high_risk_generic_address_or_postal": int(generic_address),
            "high_risk_service_leak": int(service_leak),
            "high_risk_candidate_count_close": int(count_close),
            "high_risk_gold_not_unique_possible": int(gold_not_unique),
            "mechanical_screening_bucket": bucket,
            "mechanical_screening_reason": "; ".join(reasons),
        }
    )
    return row


def source_availability() -> tuple[str, list[Path]]:
    if all(path.exists() for path in PRIORITY_A[:2]):
        return "priority_a_existing_streaming_task_level", PRIORITY_A
    if all(path.exists() for path in PRIORITY_B):
        return "priority_c_original_toolbench_streaming_extraction_because_priority_b_task_level_is_sample_only", PRIORITY_C
    if all(path.exists() for path in PRIORITY_C):
        return "priority_c_original_toolbench_streaming_extraction", PRIORITY_C
    return "missing", []


def build_exclude_keys() -> set[tuple[str, str]]:
    rows = read_csv(MANUAL40_CSV)
    return {(row["task_id"], row["task_type"]) for row in rows}


def candidate_passes(row: dict[str, Any]) -> bool:
    if row["task_type"] == "multi_service_discovery":
        return (
            row["source_group"] == "G2"
            and int(row["candidate_service_count"]) > int(row["gold_service_count"])
            and int(row["gold_service_count"]) >= 2
            and row["candidate_services_json"] not in {"", "[]"}
            and row["gold_services_json"] not in {"", "[]"}
            and row["source_group"] != "G3"
        )
    return (
        int(row["candidate_api_count"]) > int(row["gold_api_count"])
        and int(row["gold_api_count"]) >= 2
        and row["candidate_apis_json"] not in {"", "[]"}
        and row["gold_apis_json"] not in {"", "[]"}
        and row["source_group"] in {"G1", "G2"}
    )


def pool_has_enough(pool: list[dict[str, Any]]) -> bool:
    if len(pool) >= POOL_LIMIT_PER_TASK_TYPE:
        return True
    counts = Counter(row["mechanical_screening_bucket"] for row in pool)
    return all(counts.get(bucket, 0) >= target * 2 for bucket, target in SELECT_TARGETS.items()) and len(pool) >= 90


def build_pools(exclude_keys: set[tuple[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    service_pool: list[dict[str, Any]] = []
    api_pool: list[dict[str, Any]] = []
    seen_service_keys: set[tuple[str, str]] = set()
    seen_api_keys: set[tuple[str, str]] = set()
    scanned = {"G1": 0, "G2": 0}

    for group, path in [("G2", RAW_G2), ("G1", RAW_G1)]:
        if not path.exists():
            continue
        for task in iter_json_array(path):
            scanned[group] += 1
            if scanned[group] > SCAN_LIMIT_PER_GROUP:
                break
            if group == "G2":
                row = annotate_task(task, group, "multi_service_discovery")
                if row:
                    key = (row["task_id"], row["task_type"])
                    if key not in exclude_keys and key not in seen_service_keys and candidate_passes(row):
                        service_pool.append(add_hints(row))
                        seen_service_keys.add(key)
            row = annotate_task(task, group, "multi_api_recommendation")
            if row:
                key = (row["task_id"], row["task_type"])
                if key not in exclude_keys and key not in seen_api_keys and candidate_passes(row):
                    api_pool.append(add_hints(row))
                    seen_api_keys.add(key)
            if group == "G2" and pool_has_enough(service_pool) and pool_has_enough(api_pool):
                break
            if group == "G1" and sum(1 for row in api_pool if row["source_group"] == "G1") >= G1_API_POOL_TARGET:
                break
    return service_pool, api_pool, {"scanned": scanned, "scan_limit_per_group": SCAN_LIMIT_PER_GROUP}


def select_balanced(pool: list[dict[str, Any]], prefix: str, target_total: int = 40) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_query: set[str] = set()
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pool:
        by_bucket[row["mechanical_screening_bucket"]].append(row)
    for bucket, target in SELECT_TARGETS.items():
        for row in by_bucket.get(bucket, []):
            if len([r for r in selected if r["mechanical_screening_bucket"] == bucket]) >= target:
                break
            sig = row.get("query_signature") or signature(row.get("query_text", ""))
            if sig in seen_query:
                continue
            selected.append(row)
            seen_query.add(sig)
    if len(selected) < target_total:
        for row in pool:
            if len(selected) >= target_total:
                break
            sig = row.get("query_signature") or signature(row.get("query_text", ""))
            key = (row["task_id"], row["task_type"])
            if sig in seen_query or any((r["task_id"], r["task_type"]) == key for r in selected):
                continue
            selected.append(row)
            seen_query.add(sig)
    for idx, row in enumerate(selected, 1):
        row["round2_review_id"] = f"{prefix}-{idx:03d}"
    return selected[:target_total]


def select_api_balanced(pool: list[dict[str, Any]], target_total: int = 40) -> list[dict[str, Any]]:
    """Select API samples while explicitly covering G1 and G2 API-level cases."""
    g1_pool = [row for row in pool if row["source_group"] == "G1"]
    g2_pool = [row for row in pool if row["source_group"] == "G2"]
    selected: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_query: set[str] = set()

    for row in g1_pool:
        if len([r for r in selected if r["source_group"] == "G1"]) >= G1_API_SELECTED_TARGET:
            break
        key = (row["task_id"], row["task_type"])
        sig = row.get("query_signature") or signature(row.get("query_text", ""))
        if key in seen_keys or sig in seen_query:
            continue
        selected.append(row)
        seen_keys.add(key)
        seen_query.add(sig)

    remainder_target = target_total - len(selected)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in g2_pool:
        by_bucket[row["mechanical_screening_bucket"]].append(row)
    bucket_targets = {
        "high_confidence_candidate": max(0, 20 - sum(1 for r in selected if r["mechanical_screening_bucket"] == "high_confidence_candidate")),
        "boundary_review": max(0, 15 - sum(1 for r in selected if r["mechanical_screening_bucket"] == "boundary_review")),
        "high_risk_review": max(0, 5 - sum(1 for r in selected if r["mechanical_screening_bucket"] == "high_risk_review")),
    }
    for bucket, target in bucket_targets.items():
        for row in by_bucket.get(bucket, []):
            if len([r for r in selected if r["mechanical_screening_bucket"] == bucket]) >= SELECT_TARGETS[bucket]:
                break
            key = (row["task_id"], row["task_type"])
            sig = row.get("query_signature") or signature(row.get("query_text", ""))
            if key in seen_keys or sig in seen_query:
                continue
            selected.append(row)
            seen_keys.add(key)
            seen_query.add(sig)
            if len(selected) >= target_total:
                break
        if len(selected) >= target_total:
            break
    if len(selected) < target_total:
        for row in pool:
            key = (row["task_id"], row["task_type"])
            sig = row.get("query_signature") or signature(row.get("query_text", ""))
            if key in seen_keys or sig in seen_query:
                continue
            selected.append(row)
            seen_keys.add(key)
            seen_query.add(sig)
            if len(selected) >= target_total:
                break
    selected = selected[:target_total]
    for idx, row in enumerate(selected, 1):
        row["round2_review_id"] = f"R2-MA-{idx:03d}"
    return selected


def csv_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{field: row.get(field, "") for field in TASK_FIELDS} for row in rows]


def escape_script_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def generate_html(rows: list[dict[str, Any]]) -> str:
    data = csv_records(rows)
    app_json = escape_script_json(data)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Main Four Tasks Round2 Review App 80</title>
<style>
body {{ margin:0; font-family: Arial, "Microsoft YaHei", sans-serif; background:#f7f7f4; color:#222; }}
header {{ padding:16px 20px; background:#1f2937; color:white; }}
header h1 {{ margin:0 0 8px; font-size:20px; }}
header details {{ background:#374151; padding:10px 12px; border-radius:6px; }}
.layout {{ display:grid; grid-template-columns: 360px 1fr; height: calc(100vh - 126px); }}
.sidebar {{ border-right:1px solid #ddd; overflow:auto; background:white; }}
.filters {{ padding:12px; position:sticky; top:0; background:white; border-bottom:1px solid #ddd; z-index:2; }}
input, select, textarea, button {{ font: inherit; }}
input, select, textarea {{ width:100%; box-sizing:border-box; padding:7px; margin:4px 0 8px; border:1px solid #c9c9c9; border-radius:4px; }}
button {{ padding:8px 10px; border:1px solid #999; background:#fff; border-radius:4px; cursor:pointer; }}
button.primary {{ background:#2563eb; color:white; border-color:#2563eb; }}
.sample {{ padding:10px 12px; border-bottom:1px solid #eee; cursor:pointer; }}
.sample.active {{ background:#e0ecff; border-left:4px solid #2563eb; }}
.sample .rid {{ font-weight:700; }}
.badge {{ display:inline-block; padding:2px 6px; border-radius:999px; background:#e5e7eb; margin:2px; font-size:12px; }}
.badge.high_confidence_candidate {{ background:#d1fae5; }}
.badge.boundary_review {{ background:#fef3c7; }}
.badge.high_risk_review {{ background:#fee2e2; }}
main {{ overflow:auto; padding:18px 22px; }}
section {{ background:white; border:1px solid #ddd; border-radius:6px; padding:14px; margin-bottom:14px; }}
h2 {{ margin:0 0 10px; font-size:18px; }}
h3 {{ margin:14px 0 8px; font-size:15px; }}
pre {{ white-space:pre-wrap; background:#f3f4f6; padding:10px; border-radius:4px; overflow:auto; }}
.grid {{ display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:12px; }}
.tree-service {{ border:1px solid #ddd; border-radius:6px; padding:10px; margin:8px 0; }}
.gold {{ color:#b45309; font-weight:bold; }}
.warning {{ color:#b91c1c; font-weight:bold; }}
.hint-list li {{ margin:4px 0; }}
.manual-grid {{ display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:12px; }}
.nav {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }}
.small {{ font-size:12px; color:#555; }}
</style>
</head>
<body>
<header>
<h1>Main Four Tasks Round2 Small Dry-run Review App 80</h1>
<details open>
<summary>审核说明</summary>
<ul>
<li>这是 Round2 small dry-run，不是 final data。</li>
<li><code>mechanical_screening_bucket</code> 只是提示，不是 final label。</li>
<li>不确定就选 <code>uncertain</code>，不要强行 keep。</li>
<li>query 不能唯一支持 gold 时选 <code>uncertain</code>。</li>
<li>service/API 边界不清时选 <code>uncertain</code>。</li>
</ul>
</details>
</header>
<div class="layout">
<aside class="sidebar">
<div class="filters">
<label>搜索 task/query/service/api<input id="search" placeholder="search"></label>
<label>task_type<select id="taskTypeFilter"><option value="">全部</option><option>multi_service_discovery</option><option>multi_api_recommendation</option></select></label>
<label>mechanical bucket<select id="bucketFilter"><option value="">全部</option><option>high_confidence_candidate</option><option>boundary_review</option><option>high_risk_review</option></select></label>
<label>审核状态<select id="doneFilter"><option value="">全部</option><option value="done">已审核</option><option value="todo">未审核</option></select></label>
<button class="primary" onclick="exportCsv()">Export decisions CSV</button>
</div>
<div id="list"></div>
</aside>
<main>
<div class="nav">
<button onclick="prevItem()">上一条</button>
<button onclick="nextItem()">下一条</button>
<button onclick="clearCurrent()">清空当前样本</button>
<button onclick="clearAll()">清空全部判断</button>
</div>
<div id="detail"></div>
</main>
</div>
<script>
const DATA = {app_json};
const STORAGE_KEY = "main_four_tasks_round2_review_app_80_v0_4_decisions";
const emptyDecision = () => ({{
  manual_semantic_alignment: "",
  manual_leak_check: "",
  manual_candidate_gold_validity: "",
  manual_task_type_check: "",
  manual_final_decision: "",
  manual_decision_reason: ""
}});
let currentIndex = 0;
let decisions = loadDecisions();
function loadDecisions() {{
  try {{ return Object.assign({{}}, JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}")); }}
  catch {{ return {{}}; }}
}}
function saveDecisions() {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions)); }}
function decisionFor(id) {{ if (!decisions[id]) decisions[id] = emptyDecision(); return decisions[id]; }}
function isDone(id) {{ const d = decisionFor(id); return d.manual_semantic_alignment && d.manual_leak_check && d.manual_candidate_gold_validity && d.manual_task_type_check && d.manual_final_decision && d.manual_decision_reason.trim(); }}
function parseJson(text, fallback) {{ try {{ return JSON.parse(text || "[]"); }} catch {{ return fallback || []; }} }}
function textOf(row) {{ return Object.values(row).join(" ").toLowerCase(); }}
function filteredData() {{
  const q = document.getElementById("search").value.toLowerCase().trim();
  const tt = document.getElementById("taskTypeFilter").value;
  const bucket = document.getElementById("bucketFilter").value;
  const done = document.getElementById("doneFilter").value;
  return DATA.map((row, idx)=>[row, idx]).filter(([row, idx]) => {{
    if (q && !textOf(row).includes(q)) return false;
    if (tt && row.task_type !== tt) return false;
    if (bucket && row.mechanical_screening_bucket !== bucket) return false;
    if (done === "done" && !isDone(row.round2_review_id)) return false;
    if (done === "todo" && isDone(row.round2_review_id)) return false;
    return true;
  }});
}}
function renderList() {{
  const box = document.getElementById("list");
  box.innerHTML = "";
  filteredData().forEach(([row, idx]) => {{
    const div = document.createElement("div");
    div.className = "sample" + (idx === currentIndex ? " active" : "");
    div.onclick = () => {{ currentIndex = idx; render(); }};
    div.innerHTML = `<div class="rid">${{row.round2_review_id}} ${{isDone(row.round2_review_id) ? "✓" : ""}}</div>
      <div class="small">${{row.task_id}}</div>
      <span class="badge">${{row.task_type}}</span><span class="badge">${{row.source_group}}</span>
      <span class="badge">${{row.leak_status}}</span><span class="badge ${{row.mechanical_screening_bucket}}">${{row.mechanical_screening_bucket}}</span>`;
    box.appendChild(div);
  }});
}}
function option(value, label, selected) {{ return `<option value="${{value}}" ${{value===selected ? "selected" : ""}}>${{label || value || "未填写"}}</option>`; }}
function selectHtml(id, values, selected) {{ return `<select id="${{id}}" onchange="updateDecision('${{id}}', this.value)">${{values.map(v => option(v, v || "未填写", selected)).join("")}}</select>`; }}
function updateDecision(field, value) {{
  const row = DATA[currentIndex];
  decisionFor(row.round2_review_id)[field] = value;
  saveDecisions();
  renderList();
}}
function updateReason(value) {{
  const row = DATA[currentIndex];
  decisionFor(row.round2_review_id).manual_decision_reason = value;
  saveDecisions();
  renderList();
}}
function hierarchy(row) {{
  const services = parseJson(row.candidate_services_json, []);
  const apis = parseJson(row.candidate_apis_json, []);
  const goldApis = parseJson(row.gold_apis_json, []);
  const goldServices = new Set(parseJson(row.gold_services_json, []).map(String));
  const serviceNames = new Set(services.map(s => s.service_name));
  const goldApiKeys = new Set(goldApis.map(a => `${{a.service_name}}|||${{a.api_name}}`));
  const grouped = {{}};
  apis.forEach(api => {{
    const svc = api.service_name || "(missing service_name)";
    if (!grouped[svc]) grouped[svc] = [];
    grouped[svc].push(api);
  }});
  return Object.keys(grouped).map(svc => {{
    const warn = serviceNames.has(svc) ? "" : `<div class="warning">WARNING: API service name not found in candidate_services_json</div>`;
    const svcGold = goldServices.has(svc) ? ` <span class="gold">[GOLD_SERVICE]</span>` : "";
    const items = grouped[svc].map(api => {{
      const isGold = goldApiKeys.has(`${{api.service_name}}|||${{api.api_name}}`) ? ` <span class="gold">[GOLD_API]</span>` : "";
      return `<li>API: ${{api.api_name || "(empty)"}}${{isGold}}<div class="small">${{api.api_description || ""}}</div></li>`;
    }}).join("");
    return `<div class="tree-service"><strong>Service: ${{svc}}${{svcGold}}</strong>${{warn}}<ul>${{items}}</ul></div>`;
  }}).join("");
}}
function renderDetail() {{
  const row = DATA[currentIndex];
  const d = decisionFor(row.round2_review_id);
  const taskOptions = row.task_type === "multi_service_discovery"
    ? ["", "valid_multi_service_discovery", "should_be_multi_api", "should_be_single_service", "ordinary_or_unclear", "not_eligible"]
    : ["", "valid_multi_api_recommendation", "should_be_multi_service", "should_be_single_api", "ordinary_or_unclear", "not_eligible"];
  document.getElementById("detail").innerHTML = `
  <section><h2>${{row.round2_review_id}} | ${{row.task_id}}</h2>
    <span class="badge">${{row.task_type}}</span><span class="badge">${{row.source_group}}</span><span class="badge ${{row.mechanical_screening_bucket}}">${{row.mechanical_screening_bucket}}</span>
    <h3>Query</h3><pre>${{row.query_text}}</pre>
  </section>
  <section><h2>Service/API Hierarchy View</h2>${{hierarchy(row)}}</section>
  <section><h2>Rule-based Hints</h2>
    <ul class="hint-list">
      <li>candidate_service_count: ${{row.candidate_service_count}}, gold_service_count: ${{row.gold_service_count}}</li>
      <li>candidate_api_count: ${{row.candidate_api_count}}, gold_api_count: ${{row.gold_api_count}}</li>
      <li>query_mentions_any_gold_api: ${{row.query_mentions_any_gold_api}}</li>
      <li>query_mentions_any_gold_service: ${{row.query_mentions_any_gold_service}}</li>
      <li>high_risk_generic_tracking: ${{row.high_risk_generic_tracking}}</li>
      <li>high_risk_generic_address_or_postal: ${{row.high_risk_generic_address_or_postal}}</li>
      <li>high_risk_service_leak: ${{row.high_risk_service_leak}}</li>
      <li>high_risk_candidate_count_close: ${{row.high_risk_candidate_count_close}}</li>
      <li>high_risk_gold_not_unique_possible: ${{row.high_risk_gold_not_unique_possible}}</li>
      <li>mechanical_screening_reason: ${{row.mechanical_screening_reason}}</li>
    </ul>
  </section>
  <section><h2>Raw Candidate/Gold JSON</h2>
    <div class="grid"><div><h3>Candidate Services</h3><pre>${{JSON.stringify(parseJson(row.candidate_services_json, []), null, 2)}}</pre></div>
    <div><h3>Gold Services</h3><pre>${{JSON.stringify(parseJson(row.gold_services_json, []), null, 2)}}</pre></div>
    <div><h3>Candidate APIs</h3><pre>${{JSON.stringify(parseJson(row.candidate_apis_json, []), null, 2)}}</pre></div>
    <div><h3>Gold APIs</h3><pre>${{JSON.stringify(parseJson(row.gold_apis_json, []), null, 2)}}</pre></div></div>
  </section>
  <section><h2>人工填写</h2>
    <p class="small">审核顺序：先看 query 真正需求，再看 gold 是否被唯一支持，再看 candidate 是否有真实选择空间。不确定就选 uncertain。</p>
    <div class="manual-grid">
      <label>manual_semantic_alignment${{selectHtml("manual_semantic_alignment", ["", "semantic_alignment_ok", "semantic_alignment_uncertain", "semantic_mismatch_uncertain"], d.manual_semantic_alignment)}}</label>
      <label>manual_leak_check${{selectHtml("manual_leak_check", ["", "no_blocking_leak", "api_leak_blocking", "service_leak_only", "leak_uncertain"], d.manual_leak_check)}}</label>
      <label>manual_candidate_gold_validity${{selectHtml("manual_candidate_gold_validity", ["", "valid", "candidate_set_too_small", "gold_incomplete", "gold_wrong", "uncertain"], d.manual_candidate_gold_validity)}}</label>
      <label>manual_task_type_check${{selectHtml("manual_task_type_check", taskOptions, d.manual_task_type_check)}}</label>
      <label>manual_final_decision${{selectHtml("manual_final_decision", ["", "keep_for_cleaning_candidate", "uncertain", "remove"], d.manual_final_decision)}}</label>
    </div>
    <label>manual_decision_reason<textarea rows="5" oninput="updateReason(this.value)">${{d.manual_decision_reason || ""}}</textarea></label>
  </section>
  <section><h2>Metadata</h2><pre>${{JSON.stringify(row, null, 2)}}</pre></section>`;
}}
function render() {{ renderList(); renderDetail(); }}
function prevItem() {{ currentIndex = Math.max(0, currentIndex - 1); render(); }}
function nextItem() {{ currentIndex = Math.min(DATA.length - 1, currentIndex + 1); render(); }}
function clearCurrent() {{ const row = DATA[currentIndex]; decisions[row.round2_review_id] = emptyDecision(); saveDecisions(); render(); }}
function clearAll() {{ if (confirm("确认清空全部人工判断？")) {{ decisions = {{}}; saveDecisions(); render(); }} }}
function csvEscape(v) {{ const s = String(v ?? ""); return /[\",\\n]/.test(s) ? `"${{s.replaceAll('"','""')}}"` : s; }}
function exportCsv() {{
  const fields = ["round2_review_id","task_id","task_type","source_group","leak_status","mechanical_screening_bucket","manual_semantic_alignment","manual_leak_check","manual_candidate_gold_validity","manual_task_type_check","manual_final_decision","manual_decision_reason"];
  const lines = [fields.join(",")];
  DATA.forEach(row => {{
    const d = decisionFor(row.round2_review_id);
    const obj = Object.assign({{}}, row, d);
    lines.push(fields.map(f => csvEscape(obj[f])).join(","));
  }});
  const blob = new Blob(["\\ufeff" + lines.join("\\n")], {{type:"text/csv;charset=utf-8"}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "main_four_tasks_round2_manual_decisions_80.csv"; a.click();
  URL.revokeObjectURL(url);
}}
["search","taskTypeFilter","bucketFilter","doneFilter"].forEach(id => document.getElementById(id).addEventListener("input", renderList));
render();
</script>
</body>
</html>"""


def selected_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(rows),
        "task_type": dict(Counter(r["task_type"] for r in rows)),
        "bucket": dict(Counter(r["mechanical_screening_bucket"] for r in rows)),
        "leak_status": dict(Counter(r["leak_status"] for r in rows)),
        "source_group": dict(Counter(r["source_group"] for r in rows)),
        "generic_tracking": sum(int(r["high_risk_generic_tracking"]) for r in rows),
        "generic_address": sum(int(r["high_risk_generic_address_or_postal"]) for r in rows),
        "service_leak": sum(int(r["high_risk_service_leak"]) for r in rows),
    }


def md_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def write_reports(
    service_pool: list[dict[str, Any]],
    api_pool: list[dict[str, Any]],
    selected_service: list[dict[str, Any]],
    selected_api: list[dict[str, Any]],
    source_mode: str,
    extraction_summary: dict[str, Any],
    exclude_keys: set[tuple[str, str]],
) -> None:
    selected = selected_service + selected_api
    bucket_counts = Counter(r["mechanical_screening_bucket"] for r in selected)
    report = [
        "# Main Four Tasks Round2 Small Dry-run Sampling Report v0.4",
        "",
        "## 本阶段做了什么",
        "",
        "生成 Round2 small dry-run 候选池、80 条人工审核样本、交互式 HTML 审核页面和审核说明。",
        "",
        "## 为什么这是 small dry-run，不是 full cleaning",
        "",
        "本阶段只抽取 80 条用于人工复核。`mechanical_screening_bucket` 只是提示，不是 final label；没有生成正式 clean 数据。",
        "",
        "## 使用了哪些数据源",
        "",
        f"- source_mode: `{source_mode}`",
        f"- extraction_summary: `{json.dumps(extraction_summary, ensure_ascii=False)}`",
        "",
        "## manual40 排除情况",
        "",
        f"- exclude_key 使用 `task_id + task_type`",
        f"- manual40 exclude_key 数量：{len(exclude_keys)}",
        "",
        "## 候选池规模",
        "",
        f"- multi_service pool: {len(service_pool)}",
        f"- multi_api pool: {len(api_pool)}",
        "",
        "## selected 80 总数",
        "",
        f"- selected total: {len(selected)}",
        "",
        "## multi_service 40 的组成",
        "",
        md_table(["bucket", "count"], sorted(Counter(r["mechanical_screening_bucket"] for r in selected_service).items())),
        "",
        "## multi_api 40 的组成",
        "",
        md_table(["bucket", "count"], sorted(Counter(r["mechanical_screening_bucket"] for r in selected_api).items())),
        "",
        "## high_confidence / boundary / high_risk 分布",
        "",
        md_table(["bucket", "count"], sorted(bucket_counts.items())),
        "",
        "## leak_status 分布",
        "",
        md_table(["leak_status", "count"], sorted(Counter(r["leak_status"] for r in selected).items())),
        "",
        "## source_group 分布",
        "",
        md_table(["source_group", "count"], sorted(Counter(r["source_group"] for r in selected).items())),
        "",
        "## candidate/gold count 分布",
        "",
        f"- candidate_service_count: {dict(Counter(r['candidate_service_count'] for r in selected))}",
        f"- gold_service_count: {dict(Counter(r['gold_service_count'] for r in selected))}",
        f"- candidate_api_count: {dict(Counter(r['candidate_api_count'] for r in selected))}",
        f"- gold_api_count: {dict(Counter(r['gold_api_count'] for r in selected))}",
        "",
        "## 是否覆盖 generic tracking/address 高风险样本",
        "",
        f"- generic tracking: {sum(int(r['high_risk_generic_tracking']) for r in selected)}",
        f"- generic address/postal: {sum(int(r['high_risk_generic_address_or_postal']) for r in selected)}",
        "",
        "## 是否覆盖 service_leak_only",
        "",
        f"- service_leak_only / high_risk_service_leak: {sum(int(r['high_risk_service_leak']) for r in selected)}",
        "",
        "## 是否有重复 task_id + task_type",
        "",
        f"- duplicate keys: {len(selected) - len({(r['task_id'], r['task_type']) for r in selected})}",
        "",
        "## 是否建议现在 full cleaning",
        "",
        "不建议 full cleaning。",
        "",
        "## 是否建议现在 baseline",
        "",
        "不建议 baseline。",
        "",
        "下一步是人工审核 Round2 HTML 页面并导出 decisions CSV。",
    ]
    SAMPLING_REPORT.write_text("\n".join(report), encoding="utf-8")

    instruction = [
        "# Main Four Tasks Round2 Manual Review Instruction v0.4",
        "",
        "## 1. 如何打开 HTML 页面",
        "",
        f"双击打开 `{HTML_APP}`，或在浏览器中打开该本地文件。",
        "",
        "## 2. 如何逐条审核",
        "",
        "从左侧选择样本，右侧按 query、候选 service/API、gold service/API、hierarchy view 和 hints 逐项检查。",
        "",
        "## 3. mechanical_screening_bucket 怎么理解",
        "",
        "`high_confidence_candidate`、`boundary_review`、`high_risk_review` 都只是机械提示，不是最终标签。",
        "",
        "## 4. 哪些情况应该 keep",
        "",
        "query 能明确支持 gold，candidate/gold 有真实选择空间，无阻塞 leak，service/API 层级合理。",
        "",
        "## 5. 哪些情况应该 uncertain",
        "",
        "query 不能唯一支持 gold、generic tracking/address 绑定具体服务、service leak only、service/API 边界不清、candidate/gold 数量过近。",
        "",
        "## 6. 哪些情况应该 remove",
        "",
        "strong API leak、明显 semantic mismatch、gold wrong、not eligible。",
        "",
        "## 7. 为什么不确定不能强行 keep",
        "",
        "benchmark 的目标是评估服务/API 发现能力；不确定样本进入 clean 会把 gold 噪声变成模型错误。",
        "",
        "## 8. 导出的 decisions CSV 后续用于什么",
        "",
        "用于和 manual40 合并分析，更新 fail-closed 规则，并验证清洗脚本是否稳定。",
        "",
        "## 9. Round2 审核完成后如何和 manual40 合并分析",
        "",
        "用 `round2_review_id` 保留 Round2 唯一记录，用 `task_id + task_type` 对齐原始 dry-run，和 manual40 分开统计后再合并比较。",
        "",
        "## 10. 什么条件下才能考虑正式清洗脚本",
        "",
        "至少需要 manual40 + Round2 都通过 rule validation，fatal error 为 0，uncertain 比例下降并且人工审核分布稳定。",
    ]
    INSTRUCTION_DOC.write_text("\n".join(instruction), encoding="utf-8")


def archive_outputs() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    paths = [
        POOL_SERVICE_CSV,
        POOL_API_CSV,
        SELECTED_80_CSV,
        SELECTED_SERVICE_CSV,
        SELECTED_API_CSV,
        HTML_APP,
        SAMPLING_REPORT,
        INSTRUCTION_DOC,
        SUMMARY_JSON,
        Path(__file__),
    ]
    for path in paths:
        if path.exists():
            rel = path.relative_to(PROJECT_ROOT)
            target = ARCHIVE_DIR / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def main() -> None:
    missing_required = [str(p) for p in REQUIRED_INPUTS if not p.exists()]
    source_mode, source_paths = source_availability()
    missing_source = source_mode == "missing"
    if missing_required or missing_source:
        print(json.dumps({"missing_required": missing_required, "source_mode": source_mode}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    exclude_keys = build_exclude_keys()
    service_pool, api_pool, extraction_summary = build_pools(exclude_keys)
    selected_service = select_balanced(service_pool, "R2-MS", 40)
    selected_api = select_api_balanced(api_pool, 40)
    selected = selected_service + selected_api

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(POOL_SERVICE_CSV, csv_records(service_pool), TASK_FIELDS)
    write_csv(POOL_API_CSV, csv_records(api_pool), TASK_FIELDS)
    write_csv(SELECTED_SERVICE_CSV, csv_records(selected_service), TASK_FIELDS)
    write_csv(SELECTED_API_CSV, csv_records(selected_api), TASK_FIELDS)
    write_csv(SELECTED_80_CSV, csv_records(selected), TASK_FIELDS)
    HTML_APP.write_text(generate_html(selected), encoding="utf-8")
    write_reports(service_pool, api_pool, selected_service, selected_api, source_mode, extraction_summary, exclude_keys)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "found_all_required_inputs": True,
        "source_mode": source_mode,
        "source_paths": [str(p) for p in source_paths],
        "manual40_exclude_key_count": len(exclude_keys),
        "candidate_pool": {"multi_service": len(service_pool), "multi_api": len(api_pool)},
        "selected": selected_stats(selected),
        "selected_multi_service": selected_stats(selected_service),
        "selected_multi_api": selected_stats(selected_api),
        "manual40_overlap_count": sum(1 for r in selected if (r["task_id"], r["task_type"]) in exclude_keys),
        "duplicate_selected_task_type_keys": len(selected) - len({(r["task_id"], r["task_type"]) for r in selected}),
        "outputs": {
            "pool_service_csv": str(POOL_SERVICE_CSV),
            "pool_api_csv": str(POOL_API_CSV),
            "selected_80_csv": str(SELECTED_80_CSV),
            "selected_service_csv": str(SELECTED_SERVICE_CSV),
            "selected_api_csv": str(SELECTED_API_CSV),
            "html_app": str(HTML_APP),
            "sampling_report": str(SAMPLING_REPORT),
            "instruction_doc": str(INSTRUCTION_DOC),
            "archive": str(ARCHIVE_DIR),
        },
        "guardrails": {
            "full_cleaning": False,
            "baseline": False,
            "training": False,
            "split": False,
            "top200": False,
            "full_g3_search": False,
            "auto_final_clean_labels": False,
            "mechanical_bucket_is_final_label": False,
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_outputs()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
