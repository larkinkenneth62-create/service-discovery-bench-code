#!/usr/bin/env python3
"""Mine already-reviewed local HTML/CSV artifacts for reusable Chinese query translations."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QA_ROOT = ROOT / "ServiceDiscoveryBench-v0.1-candidate" / "qa"
TRANSLATION_FIELDS = (
    "query_translation_zh", "query_text_zh", "query_text_zh_auto", "query_zh", "queryZh",
)
PLACEHOLDERS = (
    "请人工根据英文", "待补译", "未提供经人工校验", "不生成译文", "请以左侧原文为准",
)


def cjk_count(text: str) -> int:
    return sum("\u3400" <= char <= "\u9fff" for char in text)


def usable_translation(text: str) -> bool:
    value = text.strip()
    return bool(value) and cjk_count(value) >= 4 and not any(marker in value for marker in PLACEHOLDERS)


def source_usable_translation(text: str, source: Path, field: str) -> bool:
    if not usable_translation(text):
        return False
    if field == "query_text_zh_auto" and "metatool" in source.as_posix().lower():
        return False
    latin = sum(char.isascii() and char.isalpha() for char in text)
    return field != "query_text_zh_auto" or cjk_count(text) >= max(4, latin // 2)


def nested_values(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from nested_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_values(child)


def collect_records(value: Any, source: Path, found: dict[str, list[dict[str, str]]]) -> None:
    for item in nested_values(value):
        if not isinstance(item, dict):
            continue
        query = str(item.get("query_text", "")).strip()
        if not query:
            continue
        for field in TRANSLATION_FIELDS:
            translated = str(item.get(field, "")).strip()
            if source_usable_translation(translated, source, field):
                found[query].append({"translation": translated.removeprefix("中文翻译：").strip(), "source": str(source), "field": field})
                break


def json_candidates_from_html(path: Path) -> Iterator[Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    for match in re.finditer(r'<script[^>]+id=["\']review-data["\'][^>]*>([\s\S]*?)</script>', text, re.I):
        try:
            yield json.loads(html.unescape(match.group(1)).strip())
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for marker in ("const DATA =", "const DATA=", "const ROWS=", "const ITEMS="):
        start = 0
        while (offset := text.find(marker, start)) >= 0:
            payload_start = offset + len(marker)
            while payload_start < len(text) and text[payload_start].isspace():
                payload_start += 1
            if payload_start < len(text) and text[payload_start] in "[{":
                try:
                    value, consumed = decoder.raw_decode(text[payload_start:])
                    yield value
                    start = payload_start + consumed
                    continue
                except json.JSONDecodeError:
                    pass
            start = payload_start + 1
    blobs = set(re.findall(r'(?:atob\(|_B64\s*=\s*)["\']([A-Za-z0-9+/=]{100,})["\']', text))
    for blob in blobs:
        try:
            yield json.loads(base64.b64decode(blob).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            pass


def read_csv_records(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    args = parser.parse_args()
    qa_root = args.qa_root.resolve()
    final_rows = []
    for path in sorted((qa_root / "blind_packs").rglob("*_blind_pack.csv")):
        final_rows.extend(read_csv_records(path))
    final_queries = {row["query_text"].strip() for row in final_rows}
    found: dict[str, list[dict[str, str]]] = defaultdict(list)
    id_to_queries: dict[str, set[str]] = defaultdict(set)

    def index_record_ids(records: list[dict[str, str]]) -> None:
        for record in records:
            query = str(record.get("query_text", "")).strip()
            if not query:
                continue
            for field in ("review_item_id", "task_id", "benchmark_task_id", "source_task_id", "underlying_task_id"):
                identifier = str(record.get(field, "")).strip()
                if identifier:
                    id_to_queries[identifier].add(query)

    preferred_csvs = [
        ROOT / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2_reviewed.csv",
    ]
    for path in preferred_csvs:
        if path.exists():
            records = read_csv_records(path)
            index_record_ids(records)
            collect_records(records, path, found)

    composable_rows_path = ROOT / "outputs/composable_paired_task_preparation_v0_3_3/composable_paired_task_review_items_v0_3_3.csv"
    composable_translations_path = ROOT / "outputs/composable_paired_task_preparation_v0_3_3/composable_query_translations_zh_v0_3_3.json"
    if composable_rows_path.exists() and composable_translations_path.exists():
        translations = json.loads(composable_translations_path.read_text(encoding="utf-8"))
        composable_records = read_csv_records(composable_rows_path)
        index_record_ids(composable_records)
        for row in composable_records:
            query = row.get("query_text", "").strip()
            translated = str(translations.get(row.get("review_item_id", ""), "")).strip()
            if query and usable_translation(translated):
                found[query].append({
                    "translation": translated,
                    "source": str(composable_translations_path),
                    "field": "review_item_id lookup",
                })

    tranche_rows_path = ROOT / "outputs/composable_authoritative_review_v1_0_2/toolbench_composable_review_tranche_A_103.csv"
    tranche_translations_path = ROOT / "outputs/composable_authoritative_review_v1_0_2/toolbench_tranche_A_query_translations_zh_v1_0_2.json"
    if tranche_rows_path.exists() and tranche_translations_path.exists():
        translations = json.loads(tranche_translations_path.read_text(encoding="utf-8"))
        tranche_records = read_csv_records(tranche_rows_path)
        index_record_ids(tranche_records)
        for row in tranche_records:
            query = row.get("query_text", "").strip()
            translated = str(translations.get(row.get("review_item_id", ""), "")).strip()
            if query and usable_translation(translated):
                found[query].append({
                    "translation": translated,
                    "source": str(tranche_translations_path),
                    "field": "review_item_id lookup",
                })

    preferred_html_globs = (
        "outputs/composable_paired_task_preparation_v0_3_3/*.html",
        "outputs/composable_authoritative_review_v1_0_2/*.html",
        "outputs/main_four_tasks_round2_small_dryrun_v0_4/*.html",
        "outputs/main_four_tasks_manual_check_v0_2/*.html",
    )
    for pattern in preferred_html_globs:
        for path in sorted(ROOT.glob(pattern)):
            for value in json_candidates_from_html(path):
                collect_records(value, path, found)

    # Broader local-only recovery. Size caps avoid loading giant raw candidate pools.
    for path in sorted((ROOT / "outputs").rglob("*.csv")):
        if path in preferred_csvs or path in {composable_rows_path, tranche_rows_path} or path.stat().st_size > 30_000_000:
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                header = next(csv.reader(handle), [])
            if "query_text" not in header:
                continue
            records = read_csv_records(path)
            index_record_ids(records)
            if any(field in header for field in TRANSLATION_FIELDS):
                collect_records(records, path, found)
        except (OSError, UnicodeError, csv.Error):
            continue

    for path in sorted((ROOT / "outputs").rglob("*.html")):
        if path.stat().st_size > 20_000_000:
            continue
        try:
            prefix = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "query_text" not in prefix or not any(field in prefix for field in TRANSLATION_FIELDS):
            continue
        for value in json_candidates_from_html(path):
            records = [item for item in nested_values(value) if isinstance(item, dict) and item.get("query_text")]
            index_record_ids(records)
            collect_records(value, path, found)

    for path in sorted((ROOT / "outputs").rglob("*.json")):
        if "translation" not in path.name.lower() or path.stat().st_size > 5_000_000:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        for identifier, candidate in value.items():
            translated = candidate if isinstance(candidate, str) else (
                candidate.get("query_translation_zh", "") if isinstance(candidate, dict) else ""
            )
            translated = str(translated).strip()
            if not source_usable_translation(translated, path, "query_translation_zh"):
                continue
            for query in id_to_queries.get(str(identifier), set()):
                found[query].append({"translation": translated, "source": str(path), "field": "identifier join"})

    chosen_by_query: dict[str, dict[str, str]] = {}
    conflicts = []
    for query in sorted(final_queries):
        candidates = found.get(query, [])
        if not candidates:
            continue
        unique = {candidate["translation"] for candidate in candidates}
        if len(unique) > 1:
            conflicts.append({"query_text": query, "candidate_count": len(unique), "sources": " | ".join(sorted({c["source"] for c in candidates}))})
        chosen_by_query[query] = candidates[0]

    by_id = {}
    for row in final_rows:
        query = row["query_text"].strip()
        if query in chosen_by_query:
            by_id[row["benchmark_task_id"]] = chosen_by_query[query]["translation"]
    missing_queries = sorted(final_queries - chosen_by_query.keys())
    output = qa_root / "query_translations_zh.json"
    output.write_text(json.dumps(dict(sorted(by_id.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    missing_path = qa_root / "reports" / "query_translation_zh_missing.csv"
    with missing_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_sha256", "query_text"])
        writer.writeheader()
        for query in missing_queries:
            writer.writerow({"query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(), "query_text": query})
    conflict_path = qa_root / "reports" / "query_translation_zh_conflicts.csv"
    with conflict_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_text", "candidate_count", "sources"])
        writer.writeheader()
        writer.writerows(conflicts)
    report = {
        "status": "PARTIAL" if missing_queries else "PASS",
        "method": "local reuse only; no workspace query sent to an external service",
        "html_pack_rows": len(final_rows),
        "unique_task_ids": len({row["benchmark_task_id"] for row in final_rows}),
        "unique_queries": len(final_queries),
        "translated_unique_queries": len(chosen_by_query),
        "missing_unique_queries": len(missing_queries),
        "translated_task_ids": len(by_id),
        "conflicting_reuse_candidates": len(conflicts),
    }
    (qa_root / "reports" / "query_translation_zh_reuse_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
