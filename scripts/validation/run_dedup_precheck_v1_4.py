from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

from full_clean_v1_4_common import (
    DEDUP_DIR,
    DOC_DIR,
    OUTPUT_DIR,
    TASK_BUCKET_DIR,
    compact_json,
    parse_json_list,
    ensure_dir,
    now_text,
    open_csv_writer,
    write_json,
    write_md,
)


DEDUP_EXTRA_FIELDS = [
    "dedup_group_id",
    "dedup_group_size",
    "is_dedup_representative",
    "dedup_reason",
    "dedup_key_preview",
]


def normalized_query(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def gold_combo(row: dict[str, str]) -> str:
    services = sorted(str(item).strip().lower() for item in parse_json_list(row.get("gold_services_json")) if str(item).strip())
    apis = []
    for item in parse_json_list(row.get("gold_apis_json")):
        if isinstance(item, dict):
            apis.append(f"{str(item.get('service_name','')).strip().lower()}::{str(item.get('api_name','')).strip().lower()}")
    return compact_json({"services": services, "apis": sorted(apis), "query": normalized_query(row.get("query_text", ""))})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run duplicate precheck over v1.4 dry-run clean candidates.")
    parser.add_argument("--input", type=Path, default=TASK_BUCKET_DIR / "dryrun_clean_candidate_all.csv")
    parser.add_argument("--output", type=Path, default=DEDUP_DIR / "dryrun_clean_candidate_dedup_trace_v1_4.csv")
    parser.add_argument("--summary", type=Path, default=DEDUP_DIR / "dedup_precheck_summary_v1_4.json")
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Missing clean candidate bucket: {args.input}")
    rows: list[dict[str, str]] = []
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]

    key_maps: dict[str, dict[str, list[int]]] = {
        "task_signature": defaultdict(list),
        "query_signature": defaultdict(list),
        "normalized_query": defaultdict(list),
        "query_gold_signature": defaultdict(list),
    }
    for idx, row in enumerate(rows):
        key_maps["task_signature"][row.get("task_signature", "")].append(idx)
        key_maps["query_signature"][row.get("query_signature", "")].append(idx)
        key_maps["normalized_query"][normalized_query(row.get("query_text", ""))].append(idx)
        key_maps["query_gold_signature"][gold_combo(row)].append(idx)

    duplicate_memberships: dict[int, list[tuple[str, str, list[int]]]] = defaultdict(list)
    for key_type, mapping in key_maps.items():
        for key, indices in mapping.items():
            if key and len(indices) > 1:
                for idx in indices:
                    duplicate_memberships[idx].append((key_type, key, indices))

    group_id_by_signature: dict[tuple[str, str], str] = {}
    next_group = 1
    output_fields = fieldnames + [field for field in DEDUP_EXTRA_FIELDS if field not in fieldnames]
    out_f, writer = open_csv_writer(args.output, output_fields)
    duplicate_row_count = 0
    try:
        for idx, row in enumerate(rows):
            memberships = duplicate_memberships.get(idx, [])
            out = dict(row)
            if memberships:
                duplicate_row_count += 1
                memberships = sorted(memberships, key=lambda item: (-len(item[2]), item[0]))
                key_type, key, indices = memberships[0]
                sig = (key_type, key)
                if sig not in group_id_by_signature:
                    group_id_by_signature[sig] = f"DUP-{next_group:06d}"
                    next_group += 1
                first_idx = min(indices)
                out.update(
                    {
                        "dedup_group_id": group_id_by_signature[sig],
                        "dedup_group_size": str(len(indices)),
                        "is_dedup_representative": "yes" if idx == first_idx else "no",
                        "dedup_reason": ";".join(sorted({item[0] for item in memberships})),
                        "dedup_key_preview": key[:200],
                    }
                )
            else:
                out.update(
                    {
                        "dedup_group_id": "",
                        "dedup_group_size": "1",
                        "is_dedup_representative": "yes",
                        "dedup_reason": "",
                        "dedup_key_preview": "",
                    }
                )
            writer.writerow({field: out.get(field, "") for field in output_fields})
    finally:
        out_f.close()

    summary = {
        "generated_time": now_text(),
        "input_file": str(args.input),
        "output_file": str(args.output),
        "clean_candidate_rows": len(rows),
        "duplicate_group_count": len(group_id_by_signature),
        "duplicate_task_rows": duplicate_row_count,
        "unique_or_representative_rows": len(rows) - duplicate_row_count + len(group_id_by_signature),
        "is_final_clean_dataset": False,
    }
    write_json(args.summary, summary)
    lines = [
        "# Dedup Precheck Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{args.input}`",
        f"Output file: `{args.output}`",
        f"Dry-run clean candidate rows: {len(rows)}",
        f"Duplicate group count: {len(group_id_by_signature)}",
        f"Duplicate task rows: {duplicate_row_count}",
        "",
        "This is a duplicate precheck only. It does not delete or split any data.",
    ]
    write_md(DOC_DIR / "dedup_precheck_report_v1_4.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
