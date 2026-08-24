from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

from full_clean_v1_4b_common import DEDUP_DIR, DOC_DIR, TASK_BUCKET_DIR, now_text, open_csv_writer, write_json, write_md


EXTRA = ["dedup_group_id", "dedup_group_size", "is_dedup_representative", "dedup_reason"]


def normq(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run v1.4b dedup precheck.")
    parser.add_argument("--input", type=Path, default=TASK_BUCKET_DIR / "dryrun_clean_candidate_task_level_v1_4b.csv")
    parser.add_argument("--output", type=Path, default=DEDUP_DIR / "dryrun_clean_candidate_dedup_trace_v1_4b.csv")
    args = parser.parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Missing clean bucket: {args.input}")
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        base_fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        for key in [row.get("task_signature", ""), row.get("query_signature", ""), normq(row.get("query_text", ""))]:
            if key:
                groups[key].append(i)
    dup_members = defaultdict(list)
    gid = {}
    group_no = 1
    for key, idxs in groups.items():
        if len(idxs) <= 1:
            continue
        gid[key] = f"DUP14B-{group_no:06d}"
        group_no += 1
        for idx in idxs:
            dup_members[idx].append((key, idxs))
    fieldnames = base_fieldnames + [f for f in EXTRA if f not in base_fieldnames]
    out_f, writer = open_csv_writer(args.output, fieldnames)
    dup_rows = 0
    try:
        for i, row in enumerate(rows):
            out = dict(row)
            if i in dup_members:
                key, idxs = sorted(dup_members[i], key=lambda item: -len(item[1]))[0]
                dup_rows += 1
                out.update({"dedup_group_id": gid[key], "dedup_group_size": str(len(idxs)), "is_dedup_representative": "yes" if i == min(idxs) else "no", "dedup_reason": "signature_or_query_duplicate"})
            else:
                out.update({"dedup_group_id": "", "dedup_group_size": "1", "is_dedup_representative": "yes", "dedup_reason": ""})
            writer.writerow({f: out.get(f, "") for f in fieldnames})
    finally:
        out_f.close()
    summary = {"generated_time": now_text(), "clean_candidate_rows": len(rows), "duplicate_group_count": len(gid), "duplicate_task_rows": dup_rows}
    write_json(DEDUP_DIR / "dedup_precheck_summary_v1_4b.json", summary)
    write_md(DOC_DIR / "dedup_precheck_report_v1_4b.md", ["# Dedup Precheck Report v1.4b", "", f"Generated time: {now_text()}", f"Clean rows: {len(rows)}", f"Duplicate group count: {len(gid)}", "No deletion was performed."])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
