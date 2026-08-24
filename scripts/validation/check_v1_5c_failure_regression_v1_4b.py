from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import (
    DOC_DIR,
    OUTPUT_DIR,
    REGRESSION_DIR,
    V14_TASK_TRACE,
    V15C_PATCH,
    append_csv_row,
    ensure_dir,
    now_text,
    open_csv_writer,
    policy_decide_v14b,
    table_lines,
    write_json,
    write_md,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v1.5c 32 failure regression under v1.4b policy.")
    parser.add_argument("--patch", type=Path, default=V15C_PATCH)
    parser.add_argument("--semcap", type=Path, default=OUTPUT_DIR / "full_raw_semcap_v1_2_trace_v1_4b.csv")
    parser.add_argument("--v14-task", type=Path, default=V14_TASK_TRACE)
    args = parser.parse_args()
    if not args.patch.exists() or not args.semcap.exists():
        raise FileNotFoundError("Missing v1.5c patch or v1.4b SemCap trace")
    ensure_dir(REGRESSION_DIR)
    patch_rows = {}
    with args.patch.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            patch_rows[row["task_id"]] = row
    found = {}
    with args.semcap.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or []) + [
            "qa_item_id",
            "regression_expected",
            "regression_pass",
        ]
        out_path = REGRESSION_DIR / "v1_5c_failure_regression_trace_v1_4b.csv"
        out_f, writer = open_csv_writer(out_path, fieldnames)
        try:
            for row in reader:
                task_id = row.get("task_id", "")
                if task_id not in patch_rows:
                    continue
                out = policy_decide_v14b(row)
                out["qa_item_id"] = patch_rows[task_id].get("qa_item_id", "")
                out["regression_expected"] = "not_clean_candidate"
                out["regression_pass"] = str(out.get("dryrun_decision_v1_4b") != "dryrun_clean_candidate").lower()
                append_csv_row(writer, out, fieldnames)
                found[task_id] = out
        finally:
            out_f.close()
    bucket_counter = Counter(row.get("dryrun_bucket_v1_4b", "") for row in found.values())
    still_clean = [row for row in found.values() if row.get("dryrun_decision_v1_4b") == "dryrun_clean_candidate"]
    summary = {
        "generated_time": now_text(),
        "failure_ids_total": len(patch_rows),
        "failure_ids_found_in_full_trace": len(found),
        "failure_ids_still_clean_candidate": len(still_clean),
        "failure_ids_downgraded_to_uncertain_or_removed": len(found) - len(still_clean),
        "missing_failure_task_ids": sorted(set(patch_rows) - set(found)),
        "new_bucket_distribution": dict(bucket_counter),
        "can_accept_v1_4b": len(still_clean) == 0 and len(found) == len(patch_rows),
        "can_prepare_impacted_clean_candidate_qa": len(still_clean) == 0 and len(found) == len(patch_rows),
    }
    write_json(REGRESSION_DIR / "v1_5c_failure_regression_summary_v1_4b.json", summary)
    lines = [
        "# v1.5c Failure Regression Report v1.4b",
        "",
        f"Generated time: {now_text()}",
        f"Failure patch: `{args.patch}`",
        f"SemCap v1.2 trace: `{args.semcap}`",
        "",
        f"- failure_ids_total: {summary['failure_ids_total']}",
        f"- failure_ids_found_in_full_trace: {summary['failure_ids_found_in_full_trace']}",
        f"- failure_ids_still_clean_candidate: {summary['failure_ids_still_clean_candidate']}",
        f"- failure_ids_downgraded_to_uncertain_or_removed: {summary['failure_ids_downgraded_to_uncertain_or_removed']}",
        "",
        "## New Bucket Distribution",
        "",
        *table_lines(bucket_counter),
        "",
        f"can_accept_v1_4b: {summary['can_accept_v1_4b']}",
    ]
    write_md(DOC_DIR / "v1_5c_failure_regression_report_v1_4b.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
