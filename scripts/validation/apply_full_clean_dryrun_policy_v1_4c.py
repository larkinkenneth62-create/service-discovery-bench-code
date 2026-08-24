from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from full_clean_v1_4c_common import (
    OUTPUT_DIR,
    REGRESSION_DIR,
    V14B_TASK_TRACE,
    V14C_POLICY_FIELDS,
    load_v15d_failed_task_ids,
    now_text,
    open_csv_writer,
    policy_decide_v14c,
    table_lines,
    write_json,
    write_md,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply v1.4c targeted dry-run tightening policy.")
    parser.add_argument("--input", type=Path, default=V14B_TASK_TRACE)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4c.csv")
    parser.add_argument("--progress-every", type=int, default=10000)
    args = parser.parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Missing v1.4b task trace: {args.input}")

    failed_task_to_qa = load_v15d_failed_task_ids()
    failed_seen: dict[str, dict[str, str]] = {}
    counters = {
        "decision": Counter(),
        "bucket": Counter(),
        "change": Counter(),
        "source_group": Counter(),
        "task_type": Counter(),
        "triggered_rules": Counter(),
    }
    cross = defaultdict(Counter)
    row_count = 0
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for field in V14C_POLICY_FIELDS + ["v1_4c_domain_specific_guard_flags_json", "v1_4c_wrong_gold_set_flags_json"]:
            if field not in fieldnames:
                fieldnames.append(field)
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                out = policy_decide_v14c(row)
                writer.writerow({field: out.get(field, "") for field in fieldnames})
                row_count += 1
                decision = out.get("dryrun_decision_v1_4c", "")
                bucket = out.get("dryrun_bucket_v1_4c", "")
                counters["decision"][decision] += 1
                counters["bucket"][bucket] += 1
                counters["change"][out.get("v1_4c_change_from_v1_4b", "")] += 1
                counters["source_group"][out.get("source_group", "")] += 1
                counters["task_type"][out.get("task_type", "")] += 1
                for rule in str(out.get("triggered_rules_v1_4c", "")).split(";"):
                    if rule:
                        counters["triggered_rules"][rule] += 1
                cross[f"{out.get('source_group','')}|{out.get('task_type','')}"][decision] += 1
                task_id = out.get("task_id", "")
                if task_id in failed_task_to_qa:
                    failed_seen[task_id] = {
                        "qa_item_id": failed_task_to_qa[task_id],
                        "task_id": task_id,
                        "source_group": out.get("source_group", ""),
                        "task_type": out.get("task_type", ""),
                        "v1_4b_decision": out.get("dryrun_decision_v1_4b", ""),
                        "v1_4b_bucket": out.get("dryrun_bucket_v1_4b", ""),
                        "v1_4c_decision": decision,
                        "v1_4c_bucket": bucket,
                        "v1_4c_blocking_reasons": out.get("blocking_reasons_v1_4c", ""),
                        "v1_4c_warning_reasons": out.get("warning_reasons_v1_4c", ""),
                        "v1_4c_triggered_rules": out.get("triggered_rules_v1_4c", ""),
                    }
                if args.progress_every and row_count % args.progress_every == 0:
                    print(f"[v1.4c policy] processed {row_count} rows")
        finally:
            out_f.close()

    failed_rows = list(failed_seen.values())
    failed_still_clean = [row for row in failed_rows if row.get("v1_4c_decision") == "dryrun_clean_candidate"]
    failed_missing = [
        {"qa_item_id": qa_id, "task_id": task_id}
        for task_id, qa_id in failed_task_to_qa.items()
        if task_id not in failed_seen
    ]
    regression_csv = REGRESSION_DIR / "v1_5d_failed_clean_candidate_regression_v1_4c.csv"
    regression_fields = [
        "qa_item_id",
        "task_id",
        "source_group",
        "task_type",
        "v1_4b_decision",
        "v1_4b_bucket",
        "v1_4c_decision",
        "v1_4c_bucket",
        "v1_4c_blocking_reasons",
        "v1_4c_warning_reasons",
        "v1_4c_triggered_rules",
    ]
    out_f, writer = open_csv_writer(regression_csv, regression_fields)
    try:
        for row in sorted(failed_rows, key=lambda item: item.get("qa_item_id", "")):
            writer.writerow({field: row.get(field, "") for field in regression_fields})
    finally:
        out_f.close()

    summary = {
        "generated_time": now_text(),
        "input_file": str(args.input),
        "output_file": str(args.output),
        "row_count": row_count,
        "counters": {key: dict(value) for key, value in counters.items()},
        "source_group_task_type_decision": {key: dict(value) for key, value in cross.items()},
        "v1_5d_failed_label_count": len(failed_task_to_qa),
        "v1_5d_failed_rows_found": len(failed_rows),
        "v1_5d_failed_still_clean_count": len(failed_still_clean),
        "v1_5d_failed_moved_out_count": len([row for row in failed_rows if row.get("v1_4c_decision") != "dryrun_clean_candidate"]),
        "v1_5d_failed_missing": failed_missing,
        "regression_csv": str(regression_csv),
    }
    write_json(OUTPUT_DIR / "full_clean_task_trace_summary_v1_4c.json", summary)
    write_json(REGRESSION_DIR / "v1_5d_failure_regression_summary_v1_4c.json", summary)
    write_md(
        OUTPUT_DIR / "full_clean_task_trace_report_v1_4c.md",
        [
            "# Full Clean Task Trace Report v1.4c",
            "",
            f"Generated time: {now_text()}",
            f"Input: `{args.input}`",
            f"Output: `{args.output}`",
            f"Rows: {row_count}",
            "",
            "This is a dry-run trace, not a final clean dataset.",
            "",
            "## Decision Distribution",
            "",
            *table_lines(counters["decision"]),
            "",
            "## Bucket Distribution",
            "",
            *table_lines(counters["bucket"]),
            "",
            "## Triggered Rules",
            "",
            *table_lines(counters["triggered_rules"]),
            "",
            "## v1.5d Failed Clean Candidate Regression",
            "",
            f"- labels: {len(failed_task_to_qa)}",
            f"- found: {len(failed_rows)}",
            f"- still clean: {len(failed_still_clean)}",
            f"- moved out: {summary['v1_5d_failed_moved_out_count']}",
        ],
    )
    print(f"row_count={row_count}")
    print(f"v1_4c_clean_candidate_count={counters['decision'].get('dryrun_clean_candidate', 0)}")
    print(f"v1_5d_failed_still_clean_count={len(failed_still_clean)}")
    print(f"v1_5d_failed_moved_out_count={summary['v1_5d_failed_moved_out_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
