#!/usr/bin/env python
"""Validate reviewed CSVs for external policy v0.2 QA packs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


QA_ALLOWED_VALUES = {
    "qa_final_decision": {"keep_for_cleaning_candidate", "uncertain", "remove", "critical"},
    "qa_semantic_alignment_check": {
        "semantic_alignment_ok",
        "semantic_alignment_uncertain",
        "semantic_mismatch",
        "semantic_mismatch_uncertain",
    },
    "qa_capability_coverage_check": {"coverage_ok", "coverage_uncertain", "coverage_mismatch"},
    "qa_candidate_validity_check": {"candidate_validity_ok", "candidate_validity_uncertain", "candidate_invalid"},
    "qa_service_catalog_check": {"service_catalog_ok", "service_catalog_uncertain", "service_catalog_invalid"},
    "qa_task_type_check": {
        "task_type_ok",
        "task_type_uncertain",
        "task_type_invalid",
        "composable_not_strong_dependency",
    },
    "qa_leakage_check": {"no_obvious_leak", "leak_uncertain", "service_leak_blocking", "api_leak_blocking"},
    "qa_severity": {"none", "minor", "major", "critical"},
}


REQUIRED_QA_COLUMNS = [
    "qa_final_decision",
    "qa_semantic_alignment_check",
    "qa_capability_coverage_check",
    "qa_candidate_validity_check",
    "qa_service_catalog_check",
    "qa_task_type_check",
    "qa_leakage_check",
    "qa_error_type",
    "qa_severity",
    "qa_notes",
    "reviewer_id",
    "reviewed_at",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_json_cell(value: str) -> None:
    if (value or "").strip():
        json.loads(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an external policy v0.2 reviewed CSV.")
    parser.add_argument("--input", required=True, help="Reviewed CSV path.")
    parser.add_argument("--source", choices=["metatool", "stabletoolbench"], required=True)
    parser.add_argument(
        "--output-dir",
        default="outputs/external_policy_v0_2_reviewed_csv_analysis",
        help="Output directory for validation JSON and report.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not input_path.exists():
        raise SystemExit(f"Input CSV does not exist: {input_path}")

    rows = read_csv(input_path)
    header = list(rows[0].keys()) if rows else []
    required = {"review_item_id", "task_id", "query_text", *REQUIRED_QA_COLUMNS}
    source_policy_col = "metatool_policy_decision" if args.source == "metatool" else "stable_policy_decision"
    required.add(source_policy_col)

    missing_columns = sorted(required - set(header))
    invalid_values: dict[str, dict[str, int]] = {}
    for col, allowed in QA_ALLOWED_VALUES.items():
        if col not in header:
            continue
        bad_counter: Counter[str] = Counter()
        for row in rows:
            value = (row.get(col, "") or "").strip()
            if value and value not in allowed:
                bad_counter[value] += 1
        if bad_counter:
            invalid_values[col] = dict(bad_counter)

    json_bad_counts: dict[str, int] = {}
    for col in [c for c in header if c.endswith("_json")]:
        bad = 0
        for row in rows:
            try:
                parse_json_cell(row.get(col, ""))
            except Exception:
                bad += 1
        json_bad_counts[col] = bad

    pending_review_count = sum(1 for row in rows if not (row.get("qa_final_decision", "") or "").strip())
    qa_error_type_missing_when_needed = 0
    for row in rows:
        decision = (row.get("qa_final_decision", "") or "").strip()
        if decision in {"remove", "critical", "uncertain"} and not (row.get("qa_error_type", "") or "").strip():
            qa_error_type_missing_when_needed += 1

    result = {
        "generated_at": now_iso(),
        "input": str(input_path),
        "source": args.source,
        "row_count": len(rows),
        "missing_columns": missing_columns,
        "invalid_values": invalid_values,
        "json_bad_counts": json_bad_counts,
        "pending_review_count": pending_review_count,
        "qa_error_type_missing_when_needed": qa_error_type_missing_when_needed,
        "validation_pass": bool(
            not missing_columns
            and not invalid_values
            and sum(json_bad_counts.values()) == 0
            and pending_review_count == 0
            and qa_error_type_missing_when_needed == 0
        ),
    }
    stem = input_path.stem
    write_json(out_dir / f"{stem}_validation_summary.json", result)
    report = f"""# External Policy v0.2 Reviewed CSV Validation

Generated at: {result['generated_at']}

Input: `{input_path}`

Source: `{args.source}`

- row_count: `{len(rows)}`
- validation_pass: `{str(result['validation_pass']).lower()}`
- pending_review_count: `{pending_review_count}`
- missing_columns: `{missing_columns}`
- invalid_values: `{invalid_values}`
- json_bad_total: `{sum(json_bad_counts.values())}`
- qa_error_type_missing_when_needed: `{qa_error_type_missing_when_needed}`
"""
    (out_dir / f"{stem}_validation_report.md").write_text(report, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["validation_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
