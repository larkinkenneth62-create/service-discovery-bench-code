from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import DOC_DIR, OUTPUT_DIR, RAW_CANDIDATE, V14B_POLICY_FIELDS, append_csv_row, now_text, open_csv_writer, table_lines, truthy, write_json, write_md


JOIN_FIELDS = V14B_POLICY_FIELDS + ["v12_capability_coverage_pred", "v12_capability_coverage_confidence", "v12_gold_set_integrity_status", "v12_generic_search_overtrust_flag"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build candidate-level v1.4b trace.")
    parser.add_argument("--raw-candidate", type=Path, default=RAW_CANDIDATE)
    parser.add_argument("--task-trace", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4b.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "full_clean_candidate_trace_v1_4b.csv")
    parser.add_argument("--progress-every", type=int, default=100000)
    args = parser.parse_args()
    if not args.raw_candidate.exists() or not args.task_trace.exists():
        raise FileNotFoundError("Missing raw candidate or v1.4b task trace")
    decisions = {}
    with args.task_trace.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            decisions[row.get("task_id", "")] = {field: row.get(field, "") for field in JOIN_FIELDS}
    counters = {"join": Counter(), "decision": Counter(), "bucket": Counter(), "gold_candidate_by_bucket": Counter()}
    row_count = 0
    with args.raw_candidate.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for field in JOIN_FIELDS + ["task_trace_found_v1_4b"]:
            if field not in fieldnames:
                fieldnames.append(field)
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                joined = decisions.get(row.get("task_id", ""))
                out = dict(row)
                out["task_trace_found_v1_4b"] = "yes" if joined else "no"
                if joined:
                    out.update(joined)
                append_csv_row(writer, out, fieldnames)
                row_count += 1
                counters["join"][out["task_trace_found_v1_4b"]] += 1
                counters["decision"][out.get("dryrun_decision_v1_4b", "")] += 1
                counters["bucket"][out.get("dryrun_bucket_v1_4b", "")] += 1
                if truthy(out.get("is_gold_service")) or truthy(out.get("is_gold_api")):
                    counters["gold_candidate_by_bucket"][out.get("dryrun_bucket_v1_4b", "")] += 1
                if args.progress_every and row_count % args.progress_every == 0:
                    print(f"[v1.4b candidate trace] processed {row_count}")
        finally:
            out_f.close()
    summary = {
        "generated_time": now_text(),
        "candidate_row_count": row_count,
        "task_decision_count": len(decisions),
        "missing_task_join_count": counters["join"].get("no", 0),
        "join_completeness": "complete" if counters["join"].get("no", 0) == 0 else "incomplete",
        "counters": {k: dict(v) for k, v in counters.items()},
    }
    write_json(OUTPUT_DIR / "full_clean_candidate_trace_summary_v1_4b.json", summary)
    write_md(DOC_DIR / "full_clean_candidate_trace_report_v1_4b.md", ["# Full Clean Candidate Trace Report v1.4b", "", f"Generated time: {now_text()}", f"Candidate rows: {row_count}", f"Join completeness: {summary['join_completeness']}", "", *table_lines(counters["bucket"])])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
