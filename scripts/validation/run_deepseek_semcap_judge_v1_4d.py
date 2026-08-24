from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from deepseek_semcap_v1_4d_common import (
    DEEPSEEK_PREDICTION_FIELDS,
    DEEPSEEK_SCHEMA,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DOC_DIR,
    PREDICTION_DIR,
    REQUEST_DIR,
    env_config,
    ensure_dir,
    read_jsonl,
    request_messages,
    schema_validate,
    table_lines,
    write_csv,
    write_jsonl,
    write_md,
)


def api_body(item: dict[str, Any], model: str, temperature: float, max_tokens: int, structured_mode: str, thinking: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": request_messages(item),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if structured_mode == "tool_call_strict":
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "emit_semcap_judgment",
                    "description": "Return semantic alignment and capability coverage judgment for a service discovery benchmark item.",
                    "parameters": DEEPSEEK_SCHEMA,
                    "strict": True,
                },
            }
        ]
        body["tool_choice"] = {"type": "function", "function": {"name": "emit_semcap_judgment"}}
    else:
        body["response_format"] = {"type": "json_object"}
    if thinking == "enabled":
        body["thinking"] = {"type": "enabled"}
    return body


def post_chat_completion(base_url: str, api_key: str, body: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url=url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_response(response: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None, str]:
    choices = response.get("choices") or []
    if not choices:
        return "api_failed", "", None, "missing choices"
    choice = choices[0]
    finish_reason = str(choice.get("finish_reason", ""))
    if finish_reason == "length":
        return "truncated", finish_reason, None, "finish_reason=length"
    message = choice.get("message") or {}
    raw_text = ""
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        raw_text = str((tool_calls[0].get("function") or {}).get("arguments", "") or "")
    else:
        raw_text = str(message.get("content", "") or "")
    if not raw_text.strip():
        return "empty_content", finish_reason, None, "empty content"
    try:
        parsed = json.loads(raw_text)
    except Exception as exc:
        return "json_failed", finish_reason, None, str(exc)
    ok, error = schema_validate(parsed)
    if not ok:
        return "schema_failed", finish_reason, parsed, error
    return "ok", finish_reason, parsed, ""


def empty_result(item: dict[str, Any], model: str, mode: str, thinking: str, status: str, finish_reason: str = "", reason: str = "") -> dict[str, Any]:
    return {
        "custom_id": item.get("custom_id", ""),
        "task_id": item.get("task_id", ""),
        "source_group": item.get("source_group", ""),
        "task_type": item.get("task_type", ""),
        "prediction_level": item.get("prediction_level", ""),
        "deepseek_model": model,
        "deepseek_structured_mode": mode,
        "deepseek_thinking": thinking,
        "deepseek_parse_status": status,
        "deepseek_finish_reason": finish_reason,
        "deepseek_semantic_alignment_check": "",
        "deepseek_semantic_alignment_confidence": "",
        "deepseek_capability_coverage_check": "",
        "deepseek_capability_coverage_confidence": "",
        "deepseek_core_requirements_json": "[]",
        "deepseek_covered_requirements_json": "[]",
        "deepseek_missing_requirements_json": "[]",
        "deepseek_extra_unrelated_gold_services_json": "[]",
        "deepseek_generic_search_overtrust": "",
        "deepseek_domain_specific_gap": "",
        "deepseek_wrong_gold_set": "",
        "deepseek_decision_risk_level": "",
        "deepseek_reason": reason,
        "prompt_token_count": "",
        "completion_token_count": "",
        "total_token_count": "",
        "api_latency_seconds": "",
    }


def result_from_payload(
    item: dict[str, Any],
    model: str,
    mode: str,
    thinking: str,
    status: str,
    finish_reason: str,
    payload: dict[str, Any] | None,
    usage: dict[str, Any],
    latency: float,
    reason: str,
) -> dict[str, Any]:
    result = empty_result(item, model, mode, thinking, status, finish_reason, reason)
    if payload:
        result.update(
            {
                "deepseek_semantic_alignment_check": payload.get("semantic_alignment_check", ""),
                "deepseek_semantic_alignment_confidence": payload.get("semantic_alignment_confidence", ""),
                "deepseek_capability_coverage_check": payload.get("capability_coverage_check", ""),
                "deepseek_capability_coverage_confidence": payload.get("capability_coverage_confidence", ""),
                "deepseek_core_requirements_json": json.dumps(payload.get("core_requirements", []), ensure_ascii=False),
                "deepseek_covered_requirements_json": json.dumps(payload.get("covered_requirements", []), ensure_ascii=False),
                "deepseek_missing_requirements_json": json.dumps(payload.get("missing_requirements", []), ensure_ascii=False),
                "deepseek_extra_unrelated_gold_services_json": json.dumps(payload.get("extra_unrelated_gold_services", []), ensure_ascii=False),
                "deepseek_generic_search_overtrust": str(payload.get("generic_search_overtrust", "")),
                "deepseek_domain_specific_gap": str(payload.get("domain_specific_gap", "")),
                "deepseek_wrong_gold_set": str(payload.get("wrong_gold_set", "")),
                "deepseek_decision_risk_level": payload.get("decision_risk_level", ""),
                "deepseek_reason": payload.get("reason", reason),
            }
        )
    result.update(
        {
            "prompt_token_count": usage.get("prompt_tokens", ""),
            "completion_token_count": usage.get("completion_tokens", ""),
            "total_token_count": usage.get("total_tokens", ""),
            "api_latency_seconds": f"{latency:.3f}" if latency else "",
        }
    )
    return result


def run_one(
    item: dict[str, Any],
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    max_tokens: int,
    structured_mode: str,
    thinking: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    modes = [structured_mode]
    if structured_mode == "tool_call_strict":
        modes.append("json_object")
    last_error = ""
    for mode in modes:
        for attempt in range(3):
            body = api_body(item, model, temperature, max_tokens, mode, thinking)
            started = time.time()
            try:
                response = post_chat_completion(base_url, api_key, body)
                latency = time.time() - started
                status, finish_reason, payload, parse_error = parse_response(response)
                usage = response.get("usage") or {}
                raw = {
                    "custom_id": item.get("custom_id", ""),
                    "task_id": item.get("task_id", ""),
                    "structured_mode": mode,
                    "parse_status": status,
                    "parse_error": parse_error,
                    "response": response,
                }
                if status == "empty_content" and attempt == 0:
                    last_error = parse_error
                    time.sleep(1.0)
                    continue
                return result_from_payload(item, model, mode, thinking, status, finish_reason, payload, usage, latency, parse_error), raw
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode("utf-8", errors="replace")[:1000]
                last_error = f"HTTP {exc.code}: {body_text}"
                if exc.code in {400, 404, 422} and mode == "tool_call_strict":
                    break
                time.sleep(2**attempt)
            except Exception as exc:
                last_error = str(exc)
                time.sleep(2**attempt)
    return empty_result(item, model, structured_mode, thinking, "api_failed", reason=last_error), {
        "custom_id": item.get("custom_id", ""),
        "task_id": item.get("task_id", ""),
        "parse_status": "api_failed",
        "parse_error": last_error,
    }


def write_run_report(output_csv: Path, rows: list[dict[str, Any]]) -> None:
    name = output_csv.name
    if "sample_20" in name:
        path = DOC_DIR / "deepseek_semcap_sample20_report_v1_4d.md"
    elif "calibration_180" in name:
        path = DOC_DIR / "deepseek_semcap_calibration_run_report_v1_4d.md"
    elif "clean_candidates" in name:
        path = DOC_DIR / "deepseek_semcap_full2168_run_report_v1_4d.md"
    else:
        path = DOC_DIR / "deepseek_semcap_run_report_v1_4d.md"
    parse_counts = Counter(row.get("deepseek_parse_status", "") for row in rows)
    coverage_counts = Counter(row.get("deepseek_capability_coverage_check", "") or "<blank>" for row in rows)
    lines = [
        f"# DeepSeek SemCap Run Report v1.4d: {name}",
        "",
        f"Output CSV: `{output_csv}`",
        f"Sample count: {len(rows)}",
        "",
        "## Parse Status Distribution",
        "",
        *table_lines(parse_counts),
        "",
        "## Capability Coverage Distribution",
        "",
        *table_lines(coverage_counts),
        "",
        f"- parse ok rate: {round(parse_counts.get('ok', 0) / len(rows), 4) if rows else 0}",
        f"- schema_failed count: {parse_counts.get('schema_failed', 0)}",
        f"- empty_content count: {parse_counts.get('empty_content', 0)}",
        f"- missing_requirements nonempty count: {sum(1 for row in rows if row.get('deepseek_missing_requirements_json') not in {'', '[]'})}",
        f"- extra_unrelated_gold_services nonempty count: {sum(1 for row in rows if row.get('deepseek_extra_unrelated_gold_services_json') not in {'', '[]'})}",
        "",
        "This report does not authorize final clean data, split, baseline, or training.",
    ]
    write_md(path, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeepSeek SemCap Judge v1.4d on a JSONL request file.")
    parser.add_argument("--input-jsonl", type=Path, default=REQUEST_DIR / "deepseek_semcap_request_sample_20.jsonl")
    parser.add_argument("--output-csv", type=Path, default=PREDICTION_DIR / "deepseek_semcap_predictions_sample_20.csv")
    parser.add_argument("--raw-output-jsonl", type=Path, default=PREDICTION_DIR / "deepseek_semcap_raw_sample_20.jsonl")
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_API_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.environ.get("DEEPSEEK_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-workers", type=int, default=4, help="Reserved for future concurrency; current runner is sequential for safer retry semantics.")
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--structured-mode", choices=["tool_call_strict", "json_object"], default=os.environ.get("DEEPSEEK_STRUCTURED_MODE", "tool_call_strict"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--thinking", choices=["enabled", "disabled"], default=os.environ.get("DEEPSEEK_THINKING", "disabled"))
    args = parser.parse_args()

    config = env_config()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set. Request files can be prepared, but the API runner will not call DeepSeek without this environment variable.")
    if args.model not in {"deepseek-v4-pro", "deepseek-v4-flash"}:
        raise ValueError(f"Unsupported DeepSeek model for v1.4d: {args.model}")
    if not args.input_jsonl.exists():
        raise FileNotFoundError(f"Missing input JSONL: {args.input_jsonl}")

    items = read_jsonl(args.input_jsonl)
    if args.limit:
        items = items[: args.limit]
    ensure_dir(args.output_csv.parent)
    existing: set[str] = set()
    if args.resume and args.output_csv.exists():
        import csv

        with args.output_csv.open("r", encoding="utf-8-sig", newline="") as f:
            existing = {row.get("custom_id", "") for row in csv.DictReader(f) if row.get("custom_id")}

    results: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if item.get("custom_id") in existing:
            continue
        result, raw = run_one(
            item,
            api_key,
            args.base_url,
            args.model,
            args.temperature,
            args.max_tokens,
            args.structured_mode,
            args.thinking,
        )
        results.append(result)
        raw_rows.append(raw)
        print(f"[{index}/{len(items)}] {item.get('custom_id')} parse_status={result.get('deepseek_parse_status')}")

    write_csv(args.output_csv, results, DEEPSEEK_PREDICTION_FIELDS)
    write_jsonl(args.raw_output_jsonl, raw_rows)
    write_run_report(args.output_csv, results)
    print(f"rows: {len(results)}")
    print(f"output_csv: {args.output_csv}")
    print(f"raw_output_jsonl: {args.raw_output_jsonl}")
    print(f"DEEPSEEK_API_KEY exists: {config['api_key_exists']} (value not printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
