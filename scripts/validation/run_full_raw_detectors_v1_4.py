from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4_common import (
    DETECTOR_FIELDS_EXTRA,
    DOC_DIR,
    OUTPUT_DIR,
    RAW_TASK,
    append_csv_row,
    deterministic_detect,
    ensure_dir,
    now_text,
    open_csv_writer,
    table_lines,
    write_json,
    write_md,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic full raw detectors for v1.4 dry-run.")
    parser.add_argument("--raw-task", type=Path, default=RAW_TASK)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "full_raw_detector_trace_v1_4.csv")
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "full_raw_detector_summary_v1_4.json")
    parser.add_argument("--progress-every", type=int, default=10000)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not args.raw_task.exists():
        raise FileNotFoundError(f"Missing raw task CSV: {args.raw_task}")
    ensure_dir(args.output.parent)
    counters: dict[str, Counter] = {
        "source_group": Counter(),
        "task_type": Counter(),
        "prediction_level": Counter(),
        "candidate_space_status": Counter(),
        "task_type_eligibility_status": Counter(),
        "api_leak_detector_status": Counter(),
        "api_leak_strength": Counter(),
        "service_leak_detector_status": Counter(),
        "gold_in_candidate_services": Counter(),
        "gold_in_candidate_apis": Counter(),
        "detector_parse_error": Counter(),
    }
    error_rows: list[dict[str, str]] = []
    started = now_text()
    row_count = 0
    with args.raw_task.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for field in DETECTOR_FIELDS_EXTRA:
            if field not in fieldnames:
                fieldnames.append(field)
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                if args.limit and row_count >= args.limit:
                    break
                try:
                    out = deterministic_detect(row)
                except Exception as exc:
                    out = dict(row)
                    out["detector_parse_error"] = f"{type(exc).__name__}: {exc}"
                    error_rows.append({"task_id": row.get("task_id", ""), "error": out["detector_parse_error"]})
                append_csv_row(writer, out, fieldnames)
                row_count += 1
                for key in counters:
                    value = out.get(key, "")
                    counters[key][value if value else "<blank>"] += 1
                if args.progress_every and row_count % args.progress_every == 0:
                    print(f"[v1.4 detector] processed {row_count} tasks")
        finally:
            out_f.close()

    summary = {
        "generated_time": now_text(),
        "started_time": started,
        "input_file": str(args.raw_task),
        "output_file": str(args.output),
        "row_count": row_count,
        "error_count": len(error_rows),
        "counters": {key: dict(value) for key, value in counters.items()},
        "no_full_cleaning_no_split_no_baseline_no_training": True,
    }
    write_json(args.summary, summary)
    lines = [
        "# Full Raw Detector Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{args.raw_task}`",
        f"Output file: `{args.output}`",
        f"Sample count: {row_count}",
        "",
        "No full cleaning, split, baseline, or training was run.",
        "",
        "## Prediction Level",
        "",
        *table_lines(summary["counters"]["prediction_level"]),
        "",
        "## Candidate Space Status",
        "",
        *table_lines(summary["counters"]["candidate_space_status"]),
        "",
        "## API Leak Detector Status",
        "",
        *table_lines(summary["counters"]["api_leak_detector_status"]),
        "",
        "## Service Leak Detector Status",
        "",
        *table_lines(summary["counters"]["service_leak_detector_status"]),
        "",
        "## Gold In Candidate",
        "",
        "### Services",
        "",
        *table_lines(summary["counters"]["gold_in_candidate_services"]),
        "",
        "### APIs",
        "",
        *table_lines(summary["counters"]["gold_in_candidate_apis"]),
    ]
    write_md(DOC_DIR / "full_raw_detector_report_v1_4.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
