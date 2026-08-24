#!/usr/bin/env python3
"""Fill missing blind-pack Chinese query aids with an offline Argos model.

Existing locally reviewed translations always win.  The script expects the
Argos runtime/model environment to be configured by the caller and never
contacts a translation service.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import argostranslate.translate


ROOT = Path(__file__).resolve().parents[2]
QA_ROOT = ROOT / "ServiceDiscoveryBench-v0.1-candidate" / "qa"


def read_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted((QA_ROOT / "blind_packs").rglob("*_blind_pack.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def main() -> int:
    rows = read_rows()
    output_path = QA_ROOT / "query_translations_zh.json"
    existing_by_id = json.loads(output_path.read_text(encoding="utf-8"))

    existing_by_query: dict[str, str] = {}
    ids_by_query: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        task_id = row["benchmark_task_id"]
        query = row["query_text"].strip()
        ids_by_query[query].append(task_id)
        if task_id in existing_by_id:
            existing_by_query.setdefault(query, existing_by_id[task_id].strip())

    translated_by_query = dict(existing_by_query)
    missing = sorted(set(ids_by_query) - set(existing_by_query))
    total = len(missing)
    for index, query in enumerate(missing, 1):
        translated = argostranslate.translate.translate(query, "en", "zh").strip()
        if not translated or translated == query:
            raise RuntimeError(f"offline translation failed for query {index}/{total}")
        translated_by_query[query] = translated
        if index == 1 or index % 25 == 0 or index == total:
            print(f"translated {index}/{total}", flush=True)

    corrections_path = QA_ROOT / "query_translation_corrections_zh.json"
    corrections = json.loads(corrections_path.read_text(encoding="utf-8"))
    applied_corrections = 0
    for query in ids_by_query:
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        if query_hash in corrections:
            translated_by_query[query] = corrections[query_hash].strip()
            applied_corrections += 1

    result = {
        task_id: translated_by_query[query]
        for query, task_ids in ids_by_query.items()
        for task_id in task_ids
    }
    output_path.write_text(
        json.dumps(dict(sorted(result.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    missing_report = QA_ROOT / "reports" / "query_translation_zh_missing.csv"
    with missing_report.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_sha256", "query_text"])
        writer.writeheader()

    report_path = QA_ROOT / "reports" / "query_translation_zh_build_report.json"
    previous = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    trusted_count = previous.get("trusted_reused_unique_queries", len(existing_by_query))
    offline_count = previous.get("offline_translated_unique_queries", len(missing))
    manifest = {
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "trusted local reuse, then offline Argos Translate en_zh 1.9",
        "external_query_transmission": False,
        "blind_pack_rows": len(rows),
        "unique_task_ids": len(result),
        "unique_queries": len(ids_by_query),
        "trusted_reused_unique_queries": trusted_count,
        "offline_translated_unique_queries": offline_count,
        "manually_corrected_unique_queries": applied_corrections,
        "missing_unique_queries": 0,
        "role": "Chinese reviewer aid; English query remains authoritative",
    }
    report_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
