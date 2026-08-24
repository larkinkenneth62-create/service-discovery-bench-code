from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4_common import (
    DOC_DIR,
    OUTPUT_DIR,
    SEMCAP_FIELDS_EXTRA,
    append_csv_row,
    ensure_dir,
    now_text,
    open_csv_writer,
    semcap_predict,
    table_lines,
    write_json,
    write_md,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SemCap v1.1 on full raw detector trace for v1.4 dry-run.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "full_raw_detector_trace_v1_4.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "full_raw_semcap_trace_v1_4.csv")
    parser.add_argument("--summary", type=Path, default=OUTPUT_DIR / "full_raw_semcap_summary_v1_4.json")
    parser.add_argument("--progress-every", type=int, default=10000)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Missing detector trace: {args.input}")
    ensure_dir(args.output.parent)
    counters = {
        "v1_semantic_alignment_pred": Counter(),
        "v1_semantic_alignment_confidence": Counter(),
        "v1_capability_coverage_pred": Counter(),
        "v1_capability_coverage_confidence": Counter(),
        "requires_human_review_v1": Counter(),
        "source_group": Counter(),
    }
    row_count = 0
    error_count = 0
    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for field in SEMCAP_FIELDS_EXTRA:
            if field not in fieldnames:
                fieldnames.append(field)
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                if args.limit and row_count >= args.limit:
                    break
                try:
                    out = semcap_predict(row)
                except Exception as exc:
                    error_count += 1
                    out = dict(row)
                    out.update(
                        {
                            "v1_semantic_alignment_pred": "uncertain",
                            "v1_semantic_alignment_confidence": "low",
                            "v1_semantic_alignment_reason": f"semcap_exception: {type(exc).__name__}: {exc}",
                            "v1_capability_coverage_pred": "coverage_uncertain",
                            "v1_capability_coverage_confidence": "low",
                            "v1_capability_coverage_reason": f"semcap_exception: {type(exc).__name__}: {exc}",
                            "requires_human_review_v1": "true",
                        }
                    )
                append_csv_row(writer, out, fieldnames)
                row_count += 1
                for key in counters:
                    counters[key][out.get(key, "") or "<blank>"] += 1
                if args.progress_every and row_count % args.progress_every == 0:
                    print(f"[v1.4 semcap] processed {row_count} tasks")
        finally:
            out_f.close()

    summary = {
        "generated_time": now_text(),
        "input_file": str(args.input),
        "output_file": str(args.output),
        "row_count": row_count,
        "error_count": error_count,
        "counters": {key: dict(value) for key, value in counters.items()},
        "no_full_cleaning_no_split_no_baseline_no_training": True,
    }
    write_json(args.summary, summary)
    lines = [
        "# Full Raw SemCap Report v1.4",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{args.input}`",
        f"Output file: `{args.output}`",
        f"Sample count: {row_count}",
        f"SemCap exception count: {error_count}",
        "",
        "No full cleaning, split, baseline, or training was run.",
        "",
        "## Semantic Alignment Prediction",
        "",
        *table_lines(summary["counters"]["v1_semantic_alignment_pred"]),
        "",
        "## Capability Coverage Prediction",
        "",
        *table_lines(summary["counters"]["v1_capability_coverage_pred"]),
        "",
        "## Requires Human Review",
        "",
        *table_lines(summary["counters"]["requires_human_review_v1"]),
    ]
    write_md(DOC_DIR / "full_raw_semcap_report_v1_4.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
