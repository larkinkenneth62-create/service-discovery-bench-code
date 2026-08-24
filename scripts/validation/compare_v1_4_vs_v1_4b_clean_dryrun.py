from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import DIFF_DIR, DOC_DIR, OUTPUT_DIR, V14_TASK_TRACE, V15C_PATCH, now_text, open_csv_writer, table_lines, write_json, write_md


def load_v14(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return {row["task_id"]: row for row in csv.DictReader(f)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare v1.4 and v1.4b dry-run decisions.")
    parser.add_argument("--v14", type=Path, default=V14_TASK_TRACE)
    parser.add_argument("--v14b", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4b.csv")
    parser.add_argument("--patch", type=Path, default=V15C_PATCH)
    args = parser.parse_args()
    if not args.v14.exists() or not args.v14b.exists():
        raise FileNotFoundError("Missing v1.4 or v1.4b task trace")
    DIFF_DIR.mkdir(parents=True, exist_ok=True)
    old = load_v14(args.v14)
    failure_ids = set()
    if args.patch.exists():
        with args.patch.open("r", encoding="utf-8-sig", newline="") as f:
            failure_ids = {row["task_id"] for row in csv.DictReader(f)}
    out_path = DIFF_DIR / "v1_4_vs_v1_4b_task_diff.csv"
    fields = ["task_id", "old_decision", "old_bucket", "new_decision", "new_bucket", "change_type", "is_v1_5c_failure_id"]
    out_f, writer = open_csv_writer(out_path, fields)
    c = Counter()
    failure_bucket = Counter()
    found_fail = still_clean = 0
    try:
        with args.v14b.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                old_row = old.get(row.get("task_id", ""), {})
                old_dec = old_row.get("dryrun_decision", "")
                old_bucket = old_row.get("dryrun_bucket", "")
                new_dec = row.get("dryrun_decision_v1_4b", "")
                new_bucket = row.get("dryrun_bucket_v1_4b", "")
                change = "unchanged" if old_dec == new_dec and old_bucket == new_bucket else f"{old_dec}->{new_dec}"
                c[change] += 1
                is_fail = row.get("task_id", "") in failure_ids
                if is_fail:
                    found_fail += 1
                    failure_bucket[new_bucket] += 1
                    if new_dec == "dryrun_clean_candidate":
                        still_clean += 1
                writer.writerow({"task_id": row.get("task_id", ""), "old_decision": old_dec, "old_bucket": old_bucket, "new_decision": new_dec, "new_bucket": new_bucket, "change_type": change, "is_v1_5c_failure_id": str(is_fail).lower()})
    finally:
        out_f.close()
    v14_clean = sum(1 for row in old.values() if row.get("dryrun_decision") == "dryrun_clean_candidate")
    with args.v14b.open("r", encoding="utf-8-sig", newline="") as f:
        v14b_clean = sum(1 for row in csv.DictReader(f) if row.get("dryrun_decision_v1_4b") == "dryrun_clean_candidate")
    summary = {"generated_time": now_text(), "v1_4_clean_candidate_count": v14_clean, "v1_4b_clean_candidate_count": v14b_clean, "clean_candidate_delta": v14b_clean - v14_clean, "change_distribution": dict(c), "failure_ids_total": len(failure_ids), "failure_ids_found_count": found_fail, "failure_ids_still_clean_count": still_clean, "failure_ids_moved_out_count": found_fail - still_clean, "failure_new_bucket_distribution": dict(failure_bucket)}
    write_json(DIFF_DIR / "v1_4_vs_v1_4b_summary.json", summary)
    write_md(DOC_DIR / "v1_4_vs_v1_4b_diff_report.md", ["# v1.4 vs v1.4b Diff Report", "", f"Generated time: {now_text()}", f"v1.4 clean candidate count: {v14_clean}", f"v1.4b clean candidate count: {v14b_clean}", f"clean candidate delta: {v14b_clean - v14_clean}", f"v1.5c failure ids still clean count: {still_clean}", "", "## Change Distribution", "", *table_lines(c), "", "## Failure New Bucket Distribution", "", *table_lines(failure_bucket)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
