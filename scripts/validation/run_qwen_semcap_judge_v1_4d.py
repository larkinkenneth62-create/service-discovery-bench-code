from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from qwen_semcap_v1_4d_common import (
    QWEN_PREDICTION_FIELDS,
    QWEN_SCHEMA,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DOC_DIR,
    PREDICTION_DIR,
    REQUEST_DIR,
    api_key_from_env,
    env_config,
    ensure_dir,
    is_allowed_qwen_model,
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
                    "parameters": QWEN_SCHEMA,
                    "strict": True,
                },
            }
        ]
        body["tool_choice"] = {"type": "function", "function": {"name": "emit_semcap_judgment"}}
    elif structured_mode == "json_object":
        body["response_format"] = {"type": "json_object"}
    elif structured_mode == "plain_json":
        # Qwen/DashScope compatible-mode can reject response_format for some
        # models/accounts. The prompt still asks for strict JSON, and we parse
        # the text content against the same schema.
        pass
    else:
        raise ValueError(f"Unsupported structured mode: {structured_mode}")
    if thinking == "enabled":
        body["thinking"] = {"type": "enabled"}
    return body


def completion_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def post_chat_completion(base_url: str, api_key: str, body: dict[str, Any], timeout: int = 90) -> dict[str, Any]:
    url = completion_url(base_url)
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


def extract_json_text(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def normalize_payload_for_schema(payload: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(payload, dict):
        return None, ["payload is not a JSON object"]

    normalized = {key: payload.get(key) for key in QWEN_SCHEMA["properties"].keys() if key in payload}
    notes: list[str] = []
    dropped = sorted(set(payload.keys()) - set(normalized.keys()))
    if dropped:
        notes.append("dropped extra fields: " + ", ".join(dropped))

    semantic_aliases = {
        "aligned": "ok",
        "alignment_ok": "ok",
        "semantic_alignment_ok": "ok",
        "yes": "ok",
        "not_aligned": "mismatch",
        "not ok": "mismatch",
        "semantic_mismatch": "mismatch",
        "mismatched": "mismatch",
        "unknown": "uncertain",
    }
    coverage_aliases = {
        "ok": "coverage_ok",
        "yes": "coverage_ok",
        "covered": "coverage_ok",
        "coverage": "coverage_ok",
        "uncertain": "coverage_uncertain",
        "partial": "coverage_uncertain",
        "partially_covered": "coverage_uncertain",
        "coverage_partial": "coverage_uncertain",
        "mismatch": "coverage_mismatch",
        "not_covered": "coverage_mismatch",
        "not covered": "coverage_mismatch",
        "coverage_gap": "coverage_mismatch",
    }

    def normalize_string_enum(field: str, aliases: dict[str, str]) -> None:
        value = normalized.get(field)
        if isinstance(value, str):
            key = value.strip().lower()
            key = key.replace("-", "_")
            if key in aliases:
                normalized[field] = aliases[key]
                notes.append(f"{field}: {value!r} -> {normalized[field]!r}")
            elif value != value.strip():
                normalized[field] = value.strip()
                notes.append(f"{field}: stripped whitespace")

    normalize_string_enum("semantic_alignment_check", semantic_aliases)
    normalize_string_enum("capability_coverage_check", coverage_aliases)

    for field in ("semantic_alignment_confidence", "capability_coverage_confidence", "decision_risk_level"):
        value = normalized.get(field)
        if isinstance(value, str):
            clean = value.strip().lower()
            if clean != value:
                normalized[field] = clean
                notes.append(f"{field}: normalized case/whitespace")

    for field in ("generic_search_overtrust", "domain_specific_gap", "wrong_gold_set"):
        value = normalized.get(field)
        if isinstance(value, str):
            clean = value.strip().lower()
            if clean in {"true", "yes", "1"}:
                normalized[field] = True
                notes.append(f"{field}: string -> true")
            elif clean in {"false", "no", "0"}:
                normalized[field] = False
                notes.append(f"{field}: string -> false")

    return normalized, notes


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
    raw_text = extract_json_text(raw_text)
    try:
        parsed = json.loads(raw_text)
    except Exception as exc:
        return "json_failed", finish_reason, None, str(exc)
    parsed, normalize_notes = normalize_payload_for_schema(parsed)
    if parsed is None:
        return "schema_failed", finish_reason, None, "; ".join(normalize_notes)
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
        "QWEN_model": model,
        "QWEN_structured_mode": mode,
        "QWEN_thinking": thinking,
        "QWEN_parse_status": status,
        "QWEN_finish_reason": finish_reason,
        "QWEN_semantic_alignment_check": "",
        "QWEN_semantic_alignment_confidence": "",
        "QWEN_capability_coverage_check": "",
        "QWEN_capability_coverage_confidence": "",
        "QWEN_core_requirements_json": "[]",
        "QWEN_covered_requirements_json": "[]",
        "QWEN_missing_requirements_json": "[]",
        "QWEN_extra_unrelated_gold_services_json": "[]",
        "QWEN_generic_search_overtrust": "",
        "QWEN_domain_specific_gap": "",
        "QWEN_wrong_gold_set": "",
        "QWEN_decision_risk_level": "",
        "QWEN_reason": reason,
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
                "QWEN_semantic_alignment_check": payload.get("semantic_alignment_check", ""),
                "QWEN_semantic_alignment_confidence": payload.get("semantic_alignment_confidence", ""),
                "QWEN_capability_coverage_check": payload.get("capability_coverage_check", ""),
                "QWEN_capability_coverage_confidence": payload.get("capability_coverage_confidence", ""),
                "QWEN_core_requirements_json": json.dumps(payload.get("core_requirements", []), ensure_ascii=False),
                "QWEN_covered_requirements_json": json.dumps(payload.get("covered_requirements", []), ensure_ascii=False),
                "QWEN_missing_requirements_json": json.dumps(payload.get("missing_requirements", []), ensure_ascii=False),
                "QWEN_extra_unrelated_gold_services_json": json.dumps(payload.get("extra_unrelated_gold_services", []), ensure_ascii=False),
                "QWEN_generic_search_overtrust": str(payload.get("generic_search_overtrust", "")),
                "QWEN_domain_specific_gap": str(payload.get("domain_specific_gap", "")),
                "QWEN_wrong_gold_set": str(payload.get("wrong_gold_set", "")),
                "QWEN_decision_risk_level": payload.get("decision_risk_level", ""),
                "QWEN_reason": payload.get("reason", reason),
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
    if structured_mode in {"tool_call_strict", "json_object"}:
        # Fallback to plain JSON prompt when Qwen rejects tool calls or
        # response_format with HTTP 400/422.
        modes.append("json_object")
        modes.append("plain_json")
    # Avoid duplicate modes while preserving order.
    modes = list(dict.fromkeys(modes))
    last_error = ""
    last_http_headers: dict[str, str] = {}
    last_mode = structured_mode
    for mode in modes:
        last_mode = mode
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
                body_text = exc.read().decode("utf-8", errors="replace")
                headers = dict(exc.headers.items()) if exc.headers else {}
                last_http_headers = {str(k): str(v) for k, v in headers.items()}
                last_error = f"HTTP {exc.code} {exc.reason}: {body_text[:2000] if body_text else '<empty body>'}"
                if exc.code in {400, 404, 422} and mode == "tool_call_strict":
                    break
                time.sleep(2**attempt)
            except Exception as exc:
                last_error = str(exc)
                time.sleep(2**attempt)
    return empty_result(item, model, last_mode, thinking, "api_failed", reason=last_error), {
        "custom_id": item.get("custom_id", ""),
        "task_id": item.get("task_id", ""),
        "request_url": completion_url(base_url),
        "model": model,
        "structured_mode": last_mode,
        "request_body_bytes": len(json.dumps(api_body(item, model, temperature, max_tokens, last_mode, thinking), ensure_ascii=False).encode("utf-8")),
        "parse_status": "api_failed",
        "parse_error": last_error,
        "http_headers": last_http_headers,
        "note": "API key is intentionally not recorded.",
    }


def write_run_report(output_csv: Path, rows: list[dict[str, Any]]) -> None:
    name = output_csv.name
    if "sample_20" in name:
        path = DOC_DIR / "qwen_semcap_sample20_report_v1_4d.md"
    elif "calibration_180" in name:
        path = DOC_DIR / "qwen_semcap_calibration_run_report_v1_4d.md"
    elif "clean_candidates" in name:
        path = DOC_DIR / "qwen_semcap_full2168_run_report_v1_4d.md"
    else:
        path = DOC_DIR / "qwen_semcap_run_report_v1_4d.md"
    parse_counts = Counter(row.get("QWEN_parse_status", "") for row in rows)
    coverage_counts = Counter(row.get("QWEN_capability_coverage_check", "") or "<blank>" for row in rows)
    lines = [
        f"# Qwen SemCap Run Report v1.4d: {name}",
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
        f"- missing_requirements nonempty count: {sum(1 for row in rows if row.get('QWEN_missing_requirements_json') not in {'', '[]'})}",
        f"- extra_unrelated_gold_services nonempty count: {sum(1 for row in rows if row.get('QWEN_extra_unrelated_gold_services_json') not in {'', '[]'})}",
        "",
        "This report does not authorize final clean data, split, baseline, or training.",
    ]
    write_md(path, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run QWEN SemCap Judge v1.4d on a JSONL request file.")
    parser.add_argument("--input-jsonl", type=Path, default=REQUEST_DIR / "qwen_semcap_request_sample_20.jsonl")
    parser.add_argument("--output-csv", type=Path, default=PREDICTION_DIR / "qwen_semcap_predictions_sample_20.csv")
    parser.add_argument("--raw-output-jsonl", type=Path, default=PREDICTION_DIR / "qwen_semcap_raw_sample_20.jsonl")
    parser.add_argument("--model", default=os.environ.get("QWEN_API_MODEL", DEFAULT_MODEL))
    parser.add_argument("--base-url", default=os.environ.get("QWEN_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-workers", type=int, default=4, help="Reserved for future concurrency; current runner is sequential for safer retry semantics.")
    parser.add_argument("--max-tokens", type=int, default=1600)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--structured-mode", choices=["tool_call_strict", "json_object", "plain_json"], default=os.environ.get("QWEN_STRUCTURED_MODE", "plain_json"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--thinking", choices=["enabled", "disabled"], default=os.environ.get("QWEN_THINKING", "disabled"))
    args = parser.parse_args()

    config = env_config()
    api_key = api_key_from_env()
    if not api_key:
        raise RuntimeError("QWEN_API_KEY / DASHSCOPE_API_KEY is not set. Request files can be prepared, but the API runner will not call Qwen without one of these environment variables.")
    if not is_allowed_qwen_model(args.model):
        raise ValueError(f"Unsupported Qwen model for v1.4d: {args.model}. Use a Qwen/DashScope model such as qwen-plus, qwen-max, qwen-turbo, or another qwen*/qwq*/qvq* model.")
    if not args.input_jsonl.exists():
        raise FileNotFoundError(f"Missing input JSONL: {args.input_jsonl}")

    items = read_jsonl(args.input_jsonl)
    if args.limit:
        items = items[: args.limit]
    ensure_dir(args.output_csv.parent)
    existing: set[str] = set()
    resume_kept_rows: list[dict[str, Any]] = []
    if args.resume and args.output_csv.exists():
        with args.output_csv.open("r", encoding="utf-8-sig", newline="") as f:
            prior_rows = list(csv.DictReader(f))
        resume_kept_rows = [
            row
            for row in prior_rows
            if row.get("custom_id") and row.get("QWEN_parse_status") == "ok"
        ]
        existing = {row.get("custom_id", "") for row in resume_kept_rows}
        dropped_rows = len(prior_rows) - len(resume_kept_rows)
        if dropped_rows:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            csv_backup = args.output_csv.with_name(args.output_csv.stem + f".retry_backup_{stamp}" + args.output_csv.suffix)
            shutil.copy2(args.output_csv, csv_backup)
            if args.raw_output_jsonl.exists():
                raw_backup = args.raw_output_jsonl.with_name(args.raw_output_jsonl.stem + f".retry_backup_{stamp}" + args.raw_output_jsonl.suffix)
                shutil.copy2(args.raw_output_jsonl, raw_backup)
            raw_keep_ids = existing
            raw_kept: list[str] = []
            if args.raw_output_jsonl.exists() and raw_keep_ids:
                with args.raw_output_jsonl.open("r", encoding="utf-8") as raw_in:
                    for line in raw_in:
                        if not line.strip():
                            continue
                        try:
                            raw_obj = json.loads(line)
                        except Exception:
                            continue
                        if raw_obj.get("custom_id") in raw_keep_ids and raw_obj.get("parse_status") == "ok":
                            raw_kept.append(json.dumps(raw_obj, ensure_ascii=False) + "\n")
            with args.output_csv.open("w", encoding="utf-8-sig", newline="") as rewrite_f:
                rewrite_writer = csv.DictWriter(rewrite_f, fieldnames=QWEN_PREDICTION_FIELDS, extrasaction="ignore")
                rewrite_writer.writeheader()
                for row in resume_kept_rows:
                    rewrite_writer.writerow({field: row.get(field, "") for field in QWEN_PREDICTION_FIELDS})
            with args.raw_output_jsonl.open("w", encoding="utf-8") as raw_out:
                raw_out.writelines(raw_kept)
            print(f"resume: kept {len(resume_kept_rows)} ok rows; backed up and scheduled {dropped_rows} non-ok rows for retry")

    results: list[dict[str, Any]] = []
    csv_mode = "a" if args.resume and args.output_csv.exists() else "w"
    raw_mode = "a" if args.resume and args.raw_output_jsonl.exists() else "w"
    with args.output_csv.open(csv_mode, encoding="utf-8-sig", newline="") as csv_f, args.raw_output_jsonl.open(raw_mode, encoding="utf-8") as raw_f:
        writer = csv.DictWriter(csv_f, fieldnames=QWEN_PREDICTION_FIELDS, extrasaction="ignore")
        if csv_mode == "w":
            writer.writeheader()
            csv_f.flush()
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
            writer.writerow({field: result.get(field, "") for field in QWEN_PREDICTION_FIELDS})
            csv_f.flush()
            raw_f.write(json.dumps(raw, ensure_ascii=False) + "\n")
            raw_f.flush()
            print(f"[{index}/{len(items)}] {item.get('custom_id')} parse_status={result.get('QWEN_parse_status')}")

    write_run_report(args.output_csv, results)
    print(f"rows: {len(results)}")
    print(f"output_csv: {args.output_csv}")
    print(f"raw_output_jsonl: {args.raw_output_jsonl}")
    print(f"QWEN_API_KEY/DASHSCOPE_API_KEY exists: {config['api_key_exists']} (value not printed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


