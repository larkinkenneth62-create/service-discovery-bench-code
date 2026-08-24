"""Check inputs for v0.8 small full-pipeline trace-only dry-run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

from small_full_pipeline_v0_8_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    REQUIRED_BASE_FIELDS,
    SOURCE_CANDIDATES,
    V07_DETECTOR_MATRIX,
    V07_POLICY_SCRIPT,
    V07_REPLAY_REPORT,
    V42_POLICY_DOC,
    candidate_input_files,
    ensure_dirs,
    now_str,
    read_csv,
    resolve_count,
    write_json,
)


COUNT_FIELDS = [
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check v0.8 policy docs, script skeleton, and small raw inputs.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser


def missing_policy_report(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Missing Cleaning Policy Script",
                "",
                f"Generated time: {now_str()}",
                "",
                f"Required script not found: `{V07_POLICY_SCRIPT}`",
                "",
                "v0.8 stops here. No full cleaning, split, baseline, or model training was run.",
            ]
        ),
        encoding="utf-8",
    )


def missing_raw_input_report(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Missing Small Raw Input",
        "",
        f"Generated time: {now_str()}",
        "",
        "No usable task-level raw/dry-run input CSV was found.",
        "",
        "Checked candidate paths:",
        "",
    ]
    for candidate in SOURCE_CANDIDATES:
        lines.append(f"- `{candidate}`")
    lines.extend(["", "v0.8 stops here. No full cleaning, split, baseline, or model training was run."])
    path.write_text("\n".join(lines), encoding="utf-8")


def summarize_source(path: Path) -> Dict[str, object]:
    columns, rows = read_csv(path)
    missing_required = [field for field in REQUIRED_BASE_FIELDS if field not in columns]
    mapped_counts = {}
    empty_counts = {}
    for field in REQUIRED_BASE_FIELDS:
        empty_counts[field] = sum(1 for row in rows if not (row.get(field) or "").strip()) if field in columns else "missing"
    for count_col in COUNT_FIELDS:
        if count_col in columns:
            mapped_counts[count_col] = count_col
        elif "metadata_json" in columns or count_col.replace("_count", "s_json") in columns:
            mapped_counts[count_col] = "derived_from_metadata_or_json"
        else:
            mapped_counts[count_col] = "not_available"
    return {
        "path": str(path),
        "exists": path.exists(),
        "row_count": len(rows),
        "columns": columns,
        "missing_required_fields": missing_required,
        "count_field_mapping": mapped_counts,
        "empty_counts": empty_counts,
    }


def write_report(path: Path, summaries: List[Dict[str, object]], policy_status: Dict[str, object]) -> None:
    lines = [
        "# v0.8 Input Check Report",
        "",
        f"Generated time: {now_str()}",
        f"Project root: `{Path.cwd()}`",
        "",
        "Scope: this stage checks trace-only inputs. No full cleaning, final clean dataset, split, baseline, or model training was run.",
        "",
        "## Required v0.7/v4.2 Inputs",
        "",
        "| file | exists |",
        "|---|---:|",
    ]
    for item, exists in policy_status.items():
        lines.append(f"| `{item}` | {exists} |")
    lines.extend(
        [
            "",
            "## Candidate Raw/Task-Level Sources",
            "",
            "| path | rows | missing required fields |",
            "|---|---:|---|",
        ]
    )
    for summary in summaries:
        lines.append(
            f"| `{summary['path']}` | {summary['row_count']} | {', '.join(summary['missing_required_fields']) or 'none'} |"
        )
    lines.extend(["", "## Column Mapping Notes", ""])
    for summary in summaries:
        lines.extend(
            [
                f"### `{summary['path']}`",
                "",
                "| target field | mapping |",
                "|---|---|",
            ]
        )
        for field in REQUIRED_BASE_FIELDS:
            mapping = field if field in summary["columns"] else "missing"
            lines.append(f"| {field} | {mapping} |")
        for field, mapping in summary["count_field_mapping"].items():
            lines.append(f"| {field} | {mapping} |")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if not V07_POLICY_SCRIPT.exists():
        missing_policy_report(args.output_dir / "MISSING_CLEANING_POLICY_SCRIPT.md")
        print(f"ERROR: missing required cleaning policy script: {V07_POLICY_SCRIPT}")
        return 1

    sources = candidate_input_files()
    if not sources:
        missing_raw_input_report(args.output_dir / "MISSING_SMALL_RAW_INPUT.md")
        print("ERROR: no usable small raw/task-level input found.")
        return 2

    summaries = [summarize_source(path) for path in sources]
    policy_status = {
        str(V42_POLICY_DOC): V42_POLICY_DOC.exists(),
        str(V07_REPLAY_REPORT): V07_REPLAY_REPORT.exists(),
        str(V07_DETECTOR_MATRIX): V07_DETECTOR_MATRIX.exists(),
        str(V07_POLICY_SCRIPT): V07_POLICY_SCRIPT.exists(),
    }
    mapping = {
        "generated_time": now_str(),
        "source_files": [str(path) for path in sources],
        "required_base_fields": REQUIRED_BASE_FIELDS,
        "count_fields": COUNT_FIELDS,
        "source_summaries": summaries,
        "policy_status": policy_status,
        "scope_guardrails": {
            "full_cleaning": False,
            "final_clean_dataset": False,
            "split": False,
            "baseline": False,
            "training": False,
        },
    }
    write_json(args.output_dir / "input_column_mapping.json", mapping)
    write_report(args.output_dir / "input_check_report.md", summaries, policy_status)
    print(f"Usable source files: {len(sources)}")
    for source in sources:
        print(f"  - {source}")
    print(f"Wrote {args.output_dir / 'input_check_report.md'}")
    print(f"Wrote {args.output_dir / 'input_column_mapping.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
