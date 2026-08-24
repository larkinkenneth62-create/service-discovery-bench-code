#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.catalogs import (  # noqa: E402
    resolve_toolbench_static_api,
    resolve_toolbench_static_service,
)
from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
from servicediscoverybench.normalize import normalize_text  # noqa: E402


def parse_list(value: str) -> list[dict]:
    parsed = json.loads(value or "[]")
    return [item for item in parsed if isinstance(item, dict)]


def load_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def manifest_rows(output: Path) -> list[dict]:
    rows = []
    for path in sorted((p for p in output.rglob("*") if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.as_posix()):
        rows.append({
            "relative_path": path.relative_to(output).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", required=True)
    parser.add_argument("--service-catalog", required=True)
    parser.add_argument("--api-catalog", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    review_path = Path(args.review).resolve()
    service_path = Path(args.service_catalog).resolve()
    api_path = Path(args.api_catalog).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)

    services = load_jsonl(service_path)
    apis = load_jsonl(api_path)
    unresolved: list[dict] = []
    service_refs: dict[tuple[str, str], str] = {}
    api_refs: dict[tuple[str, str, str, str], str] = {}
    row_count = 0
    with review_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row_count += 1
            review_id = row.get("review_item_id", "")
            for field in ("candidate_services_json", "provisional_gold_services_json"):
                for item in parse_list(row.get(field, "")):
                    key = normalize_text(item.get("service_key") or item.get("service_name"), casefold=True)
                    name = normalize_text(item.get("service_name") or item.get("service_key"))
                    identity = (key, name)
                    sid = resolve_toolbench_static_service(services, key, name)
                    if sid:
                        service_refs[identity] = sid
                    else:
                        unresolved.append({"review_item_id": review_id, "object_type": "service", "source_field": field, "service_key": key, "service_name": name, "function_key": "", "api_name": "", "method": "", "reason": "no_unique_canonical_service"})
            for field in ("candidate_apis_json", "provisional_gold_apis_json"):
                for item in parse_list(row.get(field, "")):
                    key = normalize_text(item.get("service_key") or item.get("service_name"), casefold=True)
                    name = normalize_text(item.get("service_name") or item.get("service_key"))
                    function_key = normalize_text(item.get("function_key") or item.get("function_name") or item.get("api_name"), casefold=True)
                    api_name = normalize_text(item.get("api_name") or item.get("function_name") or function_key)
                    method = normalize_text(item.get("method", "")).upper()
                    sid = resolve_toolbench_static_service(services, key, name)
                    aid = resolve_toolbench_static_api(apis, sid, function_key, api_name, method) if sid else None
                    identity = (key, function_key, api_name, method)
                    if aid:
                        api_refs[identity] = aid
                    else:
                        unresolved.append({"review_item_id": review_id, "object_type": "api", "source_field": field, "service_key": key, "service_name": name, "function_key": function_key, "api_name": api_name, "method": method, "reason": "service_unresolved" if not sid else "no_unique_canonical_api"})

    summary = {
        "stage": "G1_composable_catalog_coverage",
        "status": "GATE_PASSED" if not unresolved else "BLOCKED",
        "review_rows": row_count,
        "unique_service_objects_resolved": len(service_refs),
        "unique_api_objects_resolved": len(api_refs),
        "unresolved_reference_occurrences": len(unresolved),
    }
    write_json(output / "VALIDATION_SUMMARY.json", summary)
    write_csv(
        output / "UNRESOLVED_REFERENCES.csv",
        unresolved,
        ["review_item_id", "object_type", "source_field", "service_key", "service_name", "function_key", "api_name", "method", "reason"],
    )
    inputs = [review_path, service_path, api_path]
    write_csv(
        output / "INPUT_MANIFEST.csv",
        [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in inputs],
        ["resolved_path", "size_bytes", "sha256"],
    )
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest_rows(output), ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not unresolved else 2


if __name__ == "__main__":
    raise SystemExit(main())
