from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from qwen_semcap_v1_4d_common import (
    CALIBRATION_180,
    QWEN_SCHEMA,
    DOC_DIR,
    OUTPUT_DIR,
    PROMPT_DOC,
    REQUEST_DIR,
    SCHEMA_PATH,
    V14C_TASK_TRACE,
    as_list,
    build_prompt_text,
    clean_candidate_rows,
    ensure_dir,
    now_text,
    parse_jsonish,
    read_csv,
    stable_score,
    table_lines,
    truncate_text,
    write_json,
    write_jsonl,
    write_md,
)


def service_name_of(item: dict[str, Any]) -> str:
    return str(
        item.get("service_name")
        or item.get("tool_name")
        or item.get("api_name")
        or item.get("name")
        or item.get("service")
        or ""
    )


def api_name_of(item: dict[str, Any]) -> str:
    return str(item.get("api_name") or item.get("name") or item.get("endpoint") or "")


def service_description_of(item: dict[str, Any]) -> str:
    return truncate_text(
        item.get("service_description")
        or item.get("tool_description")
        or item.get("description")
        or "",
        800,
    )


def api_description_of(item: dict[str, Any]) -> str:
    return truncate_text(item.get("api_description") or item.get("description") or "", 1000)


def normalize_gold_services(row: dict[str, str]) -> set[str]:
    values = as_list(row.get("gold_services_json"))
    return {str(value.get("service_name") if isinstance(value, dict) else value) for value in values if value}


def normalize_gold_apis(row: dict[str, str]) -> set[tuple[str, str]]:
    values = as_list(row.get("gold_apis_json"))
    out: set[tuple[str, str]] = set()
    for value in values:
        if isinstance(value, dict):
            out.add((str(value.get("service_name", "")), str(value.get("api_name", ""))))
        else:
            out.add(("", str(value)))
    return out


def build_candidate_services(row: dict[str, str], gold_services: set[str]) -> list[dict[str, Any]]:
    services = []
    for item in as_list(row.get("candidate_services_json")):
        if not isinstance(item, dict):
            continue
        name = service_name_of(item)
        services.append(
            {
                "service_name": name,
                "service_description": service_description_of(item),
                "is_gold_service": name in gold_services or str(item.get("is_gold_service", "")).lower() in {"1", "true"},
            }
        )
    return services


def build_gold_services(row: dict[str, str], candidate_services: list[dict[str, Any]], gold_services: set[str]) -> list[dict[str, str]]:
    by_name = {service["service_name"]: service for service in candidate_services}
    return [
        {
            "service_name": name,
            "service_description": str(by_name.get(name, {}).get("service_description", "")),
        }
        for name in sorted(gold_services)
    ]


def build_api_lists(
    row: dict[str, str],
    gold_apis: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    raw_apis = [item for item in as_list(row.get("candidate_apis_json")) if isinstance(item, dict)]
    gold_api_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    query_terms = {term.lower() for term in str(row.get("query_text", "")).replace(",", " ").split() if len(term) >= 4}

    def is_gold(item: dict[str, Any]) -> bool:
        service_name = str(item.get("service_name", ""))
        api_name = api_name_of(item)
        return (service_name, api_name) in gold_apis or ("", api_name) in gold_apis or str(item.get("is_gold_api", "")).lower() in {"1", "true"}

    def api_payload(item: dict[str, Any]) -> dict[str, Any]:
        service_name = str(item.get("service_name", ""))
        api_name = api_name_of(item)
        return {
            "service_name": service_name,
            "api_name": api_name,
            "api_description": api_description_of(item),
            "is_gold_api": is_gold(item),
        }

    for item in raw_apis:
        payload = api_payload(item)
        if payload["is_gold_api"]:
            gold_api_rows.append({k: payload[k] for k in ["service_name", "api_name", "api_description"]})
        candidates.append(payload)

    def priority(item: dict[str, Any]) -> tuple[int, int]:
        text = f"{item.get('api_name', '')} {item.get('api_description', '')}".lower()
        term_hit = any(term in text for term in query_terms)
        return (0 if item.get("is_gold_api") else 1, 0 if term_hit else 1)

    candidates.sort(key=priority)
    truncation_applied = len(candidates) > 40
    candidates = candidates[:40]
    return gold_api_rows, candidates, truncation_applied


def request_from_row(row: dict[str, str], source_kind: str) -> dict[str, Any]:
    gold_services = normalize_gold_services(row)
    gold_apis = normalize_gold_apis(row)
    candidate_services = build_candidate_services(row, gold_services)
    gold_service_payload = build_gold_services(row, candidate_services, gold_services)
    gold_api_payload, candidate_apis, truncation_applied = build_api_lists(row, gold_apis)
    custom_id = f"qwen_semcap::{source_kind}::{row.get('record_id') or row.get('task_id')}"
    return {
        "custom_id": custom_id,
        "task_id": row.get("task_id", ""),
        "record_id": row.get("record_id", ""),
        "source_kind": source_kind,
        "source_group": row.get("source_group", ""),
        "task_type": row.get("task_type", ""),
        "prediction_level": row.get("prediction_level", "service") or "service",
        "query_text": row.get("query_text", ""),
        "gold_services": gold_service_payload,
        "gold_apis": gold_api_payload,
        "candidate_services": candidate_services,
        "candidate_apis_brief": candidate_apis,
        "existing_policy_signals": {
            "api_leak_detector_status": row.get("api_leak_detector_status", ""),
            "service_leak_detector_status": row.get("service_leak_detector_status", ""),
            "candidate_service_count": row.get("candidate_service_count", ""),
            "gold_service_count": row.get("gold_service_count", ""),
            "candidate_api_count": row.get("candidate_api_count", ""),
            "gold_api_count": row.get("gold_api_count", ""),
            "v1_4c_dryrun_bucket": row.get("dryrun_bucket_v1_4c", ""),
        },
        "truncation_applied": truncation_applied,
    }


def build_sample20(clean_requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(clean_requests, key=lambda row: stable_score("sample20", row.get("task_id", ""), row.get("query_text", "")))
    return ranked[:20]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build QWEN SemCap request JSONL files for v1.4d.")
    parser.add_argument("--task-trace", type=Path, default=V14C_TASK_TRACE)
    parser.add_argument("--calibration", type=Path, default=CALIBRATION_180)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    if not args.task_trace.exists():
        raise FileNotFoundError(f"Missing v1.4c task trace: {args.task_trace}")
    if not args.calibration.exists():
        raise FileNotFoundError(f"Missing calibration 180: {args.calibration}")
    ensure_dir(REQUEST_DIR)

    clean_rows = clean_candidate_rows()
    calibration_rows = read_csv(args.calibration)
    clean_requests = [request_from_row(row, "v1_4c_clean_candidate") for row in clean_rows]
    calibration_requests = [request_from_row(row, "calibration_180") for row in calibration_rows]
    sample20 = build_sample20(clean_requests)

    clean_path = REQUEST_DIR / "qwen_semcap_requests_v1_4c_clean_candidates.jsonl"
    calibration_path = REQUEST_DIR / "qwen_semcap_requests_calibration_180.jsonl"
    sample_path = REQUEST_DIR / "qwen_semcap_request_sample_20.jsonl"
    write_jsonl(clean_path, clean_requests)
    write_jsonl(calibration_path, calibration_requests)
    write_jsonl(sample_path, sample20)
    write_json(SCHEMA_PATH, QWEN_SCHEMA)

    prompt_lines = [
        "# QWEN SemCap Judge Prompt v1.4d",
        "",
        f"Generated time: {now_text()}",
        "",
        "This prompt is used only to judge semantic alignment and capability coverage.",
        "It must not decide final clean/remove and must not replace deterministic gates.",
        "",
        "```text",
        build_prompt_text(),
        "```",
    ]
    write_md(PROMPT_DOC, prompt_lines)

    report = [
        "# QWEN SemCap Request Build Report v1.4d",
        "",
        f"Generated time: {now_text()}",
        f"Input task trace: `{args.task_trace}`",
        f"Input calibration: `{args.calibration}`",
        "",
        "## Output Files",
        "",
        f"- clean candidate requests: `{clean_path}`",
        f"- calibration 180 requests: `{calibration_path}`",
        f"- sample20 requests: `{sample_path}`",
        f"- schema: `{SCHEMA_PATH}`",
        f"- prompt: `{PROMPT_DOC}`",
        "",
        "## Counts",
        "",
        f"- v1.4c clean candidate requests: {len(clean_requests)}",
        f"- calibration requests: {len(calibration_requests)}",
        f"- sample20 requests: {len(sample20)}",
        "",
        "## Truncation",
        "",
        f"- clean requests with API truncation: {sum(1 for row in clean_requests if row.get('truncation_applied'))}",
        f"- calibration requests with API truncation: {sum(1 for row in calibration_requests if row.get('truncation_applied'))}",
        "",
        "No local paths, API keys, account identifiers, advisor notes, raw logs, or human reviewer identities are included in request payloads.",
    ]
    write_md(args.output_dir / "request_build_report.md", report)

    print(f"clean candidate requests: {len(clean_requests)}")
    print(f"calibration requests: {len(calibration_requests)}")
    print(f"sample20 requests: {len(sample20)}")
    print(f"request dir: {REQUEST_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


