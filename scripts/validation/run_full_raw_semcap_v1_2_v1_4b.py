from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import (
    DOC_DIR,
    OUTPUT_DIR,
    V14_DETECTOR_TRACE,
    V14_SEMCAP_TRACE,
    append_csv_row,
    ensure_dir,
    ensure_v12_fields,
    now_text,
    open_csv_writer,
    table_lines,
    write_json,
    write_md,
)
from semcap_v1_2_tightening_utils import run_semcap_v12


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SemCap v1.2 tightening on full raw trace for v1.4b.")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "full_raw_semcap_v1_2_trace_v1_4b.csv")
    parser.add_argument("--progress-every", type=int, default=10000)
    args = parser.parse_args()

    input_path = args.input or (V14_SEMCAP_TRACE if V14_SEMCAP_TRACE.exists() else V14_DETECTOR_TRACE)
    if not input_path.exists():
        raise FileNotFoundError(f"Missing v1.4 SemCap/detector trace: {input_path}")
    counters = {
        "v12_semantic_alignment_pred": Counter(),
        "v12_capability_coverage_pred": Counter(),
        "v12_capability_coverage_confidence": Counter(),
        "v12_gold_set_integrity_status": Counter(),
        "v12_generic_search_overtrust_flag": Counter(),
        "v12_requires_human_review": Counter(),
        "source_group": Counter(),
    }
    domain_flag_counter = Counter()
    row_count = 0
    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = ensure_v12_fields(list(reader.fieldnames or []))
        out_f, writer = open_csv_writer(args.output, fieldnames)
        try:
            for row in reader:
                out = run_semcap_v12(row)
                append_csv_row(writer, out, fieldnames)
                row_count += 1
                for field, counter in counters.items():
                    counter[out.get(field, "") or "<blank>"] += 1
                for flag in out.get("v12_domain_specific_guard_flags_json", "[]").strip("[]").split("},"):
                    if flag.strip():
                        domain_flag_counter[flag[:120]] += 1
                if args.progress_every and row_count % args.progress_every == 0:
                    print(f"[v1.4b semcap v1.2] processed {row_count} rows")
        finally:
            out_f.close()
    summary = {
        "generated_time": now_text(),
        "input_file": str(input_path),
        "output_file": str(args.output),
        "row_count": row_count,
        "counters": {key: dict(value) for key, value in counters.items()},
        "domain_specific_guard_flags_preview": dict(domain_flag_counter.most_common(50)),
    }
    write_json(OUTPUT_DIR / "full_raw_semcap_v1_2_summary_v1_4b.json", summary)
    lines = [
        "# Full Raw SemCap v1.2 Report v1.4b",
        "",
        f"Generated time: {now_text()}",
        f"Input file: `{input_path}`",
        f"Output file: `{args.output}`",
        f"Sample count: {row_count}",
        "",
        "No external LLM/API was called. No human labels were used as prediction input.",
        "",
        "## v12 Semantic Alignment",
        "",
        *table_lines(counters["v12_semantic_alignment_pred"]),
        "",
        "## v12 Capability Coverage",
        "",
        *table_lines(counters["v12_capability_coverage_pred"]),
        "",
        "## v12 Capability Confidence",
        "",
        *table_lines(counters["v12_capability_coverage_confidence"]),
        "",
        "## v12 Gold Set Integrity",
        "",
        *table_lines(counters["v12_gold_set_integrity_status"]),
        "",
        "## Generic Search Overtrust Flag",
        "",
        *table_lines(counters["v12_generic_search_overtrust_flag"]),
        "",
        "## Requires Human Review",
        "",
        *table_lines(counters["v12_requires_human_review"]),
    ]
    write_md(DOC_DIR / "full_raw_semcap_v1_2_report_v1_4b.md", lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
