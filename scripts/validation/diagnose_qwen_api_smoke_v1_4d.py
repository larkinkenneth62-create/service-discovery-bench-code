from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from qwen_semcap_v1_4d_common import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OUTPUT_DIR,
    api_key_from_env,
    ensure_dir,
    write_json,
    write_md,
)


def completion_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def post_json(url: str, api_key: str, body: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
    req = urllib.request.Request(
        url=url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8")
        return {
            "ok": True,
            "status": resp.status,
            "headers": dict(resp.headers.items()),
            "body": json.loads(text),
        }


def make_body(model: str, variant: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": 'Return exactly this JSON object: {"ok": true}'},
        ],
    }
    if variant == "minimal":
        return body
    if variant == "with_temperature":
        body["temperature"] = 0
        body["max_tokens"] = 64
        return body
    if variant == "json_object":
        body["temperature"] = 0
        body["max_tokens"] = 64
        body["response_format"] = {"type": "json_object"}
        return body
    raise ValueError(f"Unknown variant: {variant}")


def native_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/services/aigc/text-generation/generation"):
        return base
    return base + "/services/aigc/text-generation/generation"


def make_native_body(model: str, variant: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "input": {
            "messages": [
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": 'Return exactly this JSON object: {"ok": true}'},
            ]
        },
    }
    if variant in {"with_temperature", "json_object"}:
        body["parameters"] = {"temperature": 0, "max_tokens": 64, "result_format": "message"}
    return body


def try_one(api_key: str, base_url: str, model: str, variant: str, api_style: str) -> dict[str, Any]:
    if api_style == "openai_compatible":
        url = completion_url(base_url)
        body = make_body(model, variant)
    elif api_style == "dashscope_native":
        url = native_url(base_url)
        body = make_native_body(model, variant)
    else:
        raise ValueError(f"Unknown api_style: {api_style}")
    started = time.time()
    try:
        response = post_json(url, api_key, body)
        response.update(
            {
                "base_url": base_url,
                "request_url": url,
                "model": model,
                "variant": variant,
                "api_style": api_style,
                "request_body_bytes": len(json.dumps(body, ensure_ascii=False).encode("utf-8")),
                "latency_seconds": round(time.time() - started, 3),
                "note": "API key is intentionally not recorded.",
            }
        )
        return response
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "base_url": base_url,
            "request_url": url,
            "model": model,
            "variant": variant,
            "api_style": api_style,
            "status": exc.code,
            "reason": str(exc.reason),
            "body_text": body_text[:2000] if body_text else "<empty body>",
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "request_body_bytes": len(json.dumps(body, ensure_ascii=False).encode("utf-8")),
            "latency_seconds": round(time.time() - started, 3),
            "note": "API key is intentionally not recorded.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "base_url": base_url,
            "request_url": url,
            "model": model,
            "variant": variant,
            "api_style": api_style,
            "status": "exception",
            "reason": str(exc),
            "body_text": "",
            "headers": {},
            "request_body_bytes": len(json.dumps(body, ensure_ascii=False).encode("utf-8")),
            "latency_seconds": round(time.time() - started, 3),
            "note": "API key is intentionally not recorded.",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a minimal Qwen/DashScope OpenAI-compatible API smoke test.")
    parser.add_argument("--base-url", default=os.environ.get("QWEN_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.environ.get("QWEN_API_MODEL", DEFAULT_MODEL))
    parser.add_argument("--try-common-models", action="store_true")
    parser.add_argument("--try-common-base-urls", action="store_true")
    parser.add_argument("--dashscope-base-url", default=os.environ.get("QWEN_DASHSCOPE_BASE_URL", ""))
    parser.add_argument("--try-dashscope-native", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "diagnostics/qwen_api_smoke_diagnosis_v1_4d.json")
    args = parser.parse_args()

    api_key = api_key_from_env()
    if not api_key:
        raise RuntimeError("QWEN_API_KEY / DASHSCOPE_API_KEY is not set.")

    openai_base_urls = [args.base_url]
    if args.try_common_base_urls:
        for base_url in [
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ]:
            if base_url not in openai_base_urls:
                openai_base_urls.append(base_url)

    native_base_urls = []
    if args.try_dashscope_native:
        if args.dashscope_base_url:
            native_base_urls.append(args.dashscope_base_url)
        if "/compatible-mode/v1" in args.base_url:
            native_base_urls.append(args.base_url.replace("/compatible-mode/v1", "/api/v1"))
        for base_url in [
            "https://dashscope.aliyuncs.com/api/v1",
            "https://dashscope-intl.aliyuncs.com/api/v1",
        ]:
            if base_url not in native_base_urls:
                native_base_urls.append(base_url)

    models = [args.model]
    if args.try_common_models:
        for model in ["qwen-plus", "qwen-turbo", "qwen-max"]:
            if model not in models:
                models.append(model)
    variants = ["minimal", "with_temperature", "json_object"]
    results = []
    for base_url in openai_base_urls:
        for model in models:
            for variant in variants:
                result = try_one(api_key, base_url, model, variant, "openai_compatible")
                results.append(result)
                print(f"api_style=openai_compatible base_url={base_url} model={model} variant={variant} ok={result.get('ok')} status={result.get('status')} reason={result.get('reason', '')}")
                if result.get("ok"):
                    break
            if any(result.get("ok") and result.get("model") == model and result.get("base_url") == base_url for result in results):
                break
        if any(result.get("ok") and result.get("base_url") == base_url for result in results):
            break

    if not any(result.get("ok") for result in results):
        for base_url in native_base_urls:
            for model in models:
                for variant in ["minimal", "with_temperature"]:
                    result = try_one(api_key, base_url, model, variant, "dashscope_native")
                    results.append(result)
                    print(f"api_style=dashscope_native base_url={base_url} model={model} variant={variant} ok={result.get('ok')} status={result.get('status')} reason={result.get('reason', '')}")
                    if result.get("ok"):
                        break
                if any(result.get("ok") and result.get("model") == model and result.get("base_url") == base_url for result in results):
                    break
            if any(result.get("ok") and result.get("base_url") == base_url for result in results):
                break

    ensure_dir(args.output.parent)
    write_json(args.output, {"results": results})
    report_path = OUTPUT_DIR / "diagnostics/qwen_api_smoke_diagnosis_v1_4d.md"
    write_md(
        report_path,
        [
            "# Qwen API Smoke Diagnosis v1.4d",
            "",
            f"Output JSON: `{args.output}`",
            "",
            "This smoke test sends a minimal prompt only. It does not use benchmark data, does not run sample20/full cleaning/split/baseline/training, and does not record the API key.",
            "",
            "## Results",
            "",
            "| api_style | model | variant | ok | status | reason/body |",
            "|---|---|---|---:|---|---|",
            *[
                f"| {r.get('api_style')} | {r.get('model')} | {r.get('variant')} | {r.get('ok')} | {r.get('status')} | {str(r.get('reason') or r.get('body_text') or '')[:180]} |"
                for r in results
            ],
        ],
    )
    print(f"diagnosis_json={args.output}")
    print(f"diagnosis_report={report_path}")
    return 0 if any(result.get("ok") for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
