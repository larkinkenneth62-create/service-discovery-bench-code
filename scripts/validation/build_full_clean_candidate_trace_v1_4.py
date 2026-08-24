from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4_common import (
    DOC_DIR,
    OUTPUT_DIR,
    RAW_CANDIDATE,
    append_csv_row,
    ensure_dir,
    now_text,
    open_csv_writer,
    table_lines,
    truthy_yes,
    write_json,
    write_md,
)


TASK_JOIN_FIELDS = [
    "dryrun_decision",
    "dryrun_bucket",
    "blocking_reasons",
    "warning_reasons",
    "triggered_rules",
    "is_dryrun_clean_candidate",
    "is_dryrun_removed",
    "is_dryrun_uncertain",
    "is_dryrun_service_leak_only",
    "clean_confidence_bucket",
    "requires_final_qa",
    "prediction_level",
    "candidate_space_status",
    "task_type_eligibility_status",
    "api_leak_detector_status",
    "api_leak_strength",
    "service_leak_detector_status",
    "v1_semantic_alignment_pred",
    "v1_semantic_alignment_confidence",
    "v1_capability_coverage_pred",
    "v1_capability_coverage_confidence",
]


def load_task_decisions(path: Path) -> dict[str, dict[str, str]]:
    decisions: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            task_id = row.get("task_id", "")
            if not task_id:
                continue
            decisions[task_id] = {field: row.get(field, "") for field in TASK_JOIN_FIELDS}
    return decisions


def main() -> int:
    parser = argparse.ArgumentParser(description="Build candidate-level dry-run trace by joining task decisions.")
    parser.add_argument("--raw-candidate", type=Path, default=RAW_CANDIDATE)
    parser.add_argument("--task-trace", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "full_clean_candidate_trace_v1_4.csv")
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "full_clean_candidate_trace_summary_v1_4.json")
    parser.add_argument("--progress-every", type=int, default=100000)
    args = parser.parse_args()

    if not args.raw_candidate.exists():
        raise FileNotFoundError(f"Missing raw candidate CSV: {args.raw_candidate}")
    if not args.task_trace.exists():
        raise FileNotFoundError(f"Missing task trace CSV: {args.task_trace}")
    decisions = load_task_decisions(args.task_trace)
    counters = {
        "dryrun_decision": Counter(),
        "dryrun_bucket": Counter(),
        "gold_candidate_by_decision": Counter(),
        "join_status": Counter(),
        "source_group": Counter(),
    }
    row_count = 0
    with args.raw_candidate.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for field in TASK_JOIN_FIELDS + ["task_trace_found"]:
            if field not in fieldnames:
                fieldnames.append(field)
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                joined = decisions.get(row.get("task_id", ""))
                out = dict(row)
                if joined:
                    out.update(joined)
                    out["task_trace_found"] = "yes"
                else:
                    out["task_trace_found"] = "no"
                append_csv_row(writer, out, fieldnames)
                row_count += 1
                counters["join_status"][out["task_trace_found"]] += 1
                counters["dryrun_decision"][out.get("dryrun_decision", "") or "<blank>"] += 1
                counters["dryrun_bucket"][out.get("dryrun_bucket", "") or "<blank>"] += 1
                counters["source_group"][out.get("source_group", "") or "<blank>"] += 1
                if truthy_yes(out.get("is_gold_service")) or truthy_yes(out.get("is_gold_api")):
                    counters["gold_candidate_by_decision"][out.get("dryrun_decision", "") or "<blank>"] += 1
                if args.progress_every and row_count % args.progress_every == 0:
                    print(f"[v1.4 candidate trace] processed {row_count} candidate rows")
        finally:
            out_f.close()

    summary = {
        "generated_time": now_text(),
        "raw_candidate_file": str(args.raw_candidate),
        "task_trace_file": str(args.task_trace),
        "output_file": str(args.output),
        "candidate_row_count": row_count,
        "task_decision_count": len(decisions),
        "missing_task_join_count": counters["join_status"].get("no", 0),
        "join_completeness": "complete" if counters["join_status"].get("no", 0) == 0 else "incomplete",
        "counters": {key: dict(value) for key, value in counters.items()},
        "is_final_clean_dataset": False,
    }
    write_json(args.summary, summary)
    lines = [
        "# Full Clean Candidate Trace Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Raw candidate input: `{args.raw_candidate}`",
        f"Task trace input: `{args.task_trace}`",
        f"Output file: `{args.output}`",
        f"Candidate rows: {row_count}",
        f"Task decisions loaded: {len(decisions)}",
        f"Missing task joins: {summary['missing_task_join_count']}",
        "",
        "This candidate trace is diagnostic and is not a final clean dataset.",
        "",
        "## Join Status",
        "",
        *table_lines(summary["counters"]["join_status"]),
        "",
        "## Candidate Rows By Task Decision",
        "",
        *table_lines(summary["counters"]["dryrun_decision"]),
        "",
        "## Gold Candidate Rows By Task Decision",
        "",
        *table_lines(summary["counters"]["gold_candidate_by_decision"]),
    ]
    write_md(DOC_DIR / "full_clean_candidate_trace_report_v1_4.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
