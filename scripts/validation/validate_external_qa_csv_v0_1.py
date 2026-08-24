#!/usr/bin/env python
"""Validate external QA CSV files.

Empty qa_* fields are treated as pending review, not fatal errors.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED = {
    "qa_final_decision": {"", "keep_for_cleaning_candidate", "uncertain", "remove"},
    "qa_semantic_alignment_check": {"", "ok", "uncertain", "mismatch"},
    "qa_capability_coverage_check": {"", "coverage_ok", "coverage_uncertain", "coverage_mismatch", "not_applicable"},
    "qa_candidate_validity_check": {"", "valid", "uncertain", "invalid"},
    "qa_service_catalog_check": {"", "valid_catalog", "catalog_uncertain", "invalid_catalog", "not_applicable"},
    "qa_task_type_check": {"", "task_type_ok", "task_type_uncertain", "task_type_invalid", "composable_not_strong_dependency", "not_applicable"},
    "qa_leakage_check": {"", "no_obvious_leak", "service_leak_blocking", "api_leak_blocking", "leak_uncertain"},
    "qa_severity": {"", "none", "low", "medium", "high", "critical"},
}

REQUIRED_BY_SOURCE = {
    "MetaTool": [
        "review_item_id",
        "task_id",
        "source_dataset",
        "task_type",
        "query_text",
        "candidate_services_json",
        "gold_services_json",
        "source_tool_or_plugin_name",
        "adapter_warnings",
        "qa_final_decision",
        "qa_semantic_alignment_check",
        "qa_candidate_validity_check",
        "qa_service_catalog_check",
        "qa_leakage_check",
        "qa_error_type",
        "qa_severity",
        "qa_notes",
        "reviewer_id",
        "reviewed_at",
    ],
    "StableToolBench": [
        "review_item_id",
        "task_id",
        "source_dataset",
        "stable_group",
        "task_type_guess",
        "query_text",
        "available_tools_or_apis_json",
        "gold_tools_or_apis_json",
        "adapter_warnings",
        "qa_final_decision",
        "qa_semantic_alignment_check",
        "qa_capability_coverage_check",
        "qa_candidate_validity_check",
        "qa_task_type_check",
        "qa_leakage_check",
        "qa_error_type",
        "qa_severity",
        "qa_notes",
        "reviewer_id",
        "reviewed_at",
    ],
}

JSON_FIELDS = [
    "candidate_services_json",
    "gold_services_json",
    "candidate_apis_json",
    "gold_apis_json",
    "available_tools_or_apis_json",
    "gold_tools_or_apis_json",
]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def identify_source(rows: list[dict[str, str]]) -> str:
    values = {row.get("source_dataset", "") for row in rows}
    if "MetaTool" in values:
        return "MetaTool"
    if "StableToolBench" in values:
        return "StableToolBench"
    return sorted(values)[0] if values else "unknown"


def validate(path: Path) -> dict[str, Any]:
    fieldnames, rows = read_rows(path)
    source = identify_source(rows)
    required = REQUIRED_BY_SOURCE.get(source, [])
    missing_cols = [col for col in required if col not in fieldnames]
    invalid_values: list[dict[str, Any]] = []
    json_errors: list[dict[str, Any]] = []
    required_value_errors: list[dict[str, Any]] = []

    pending = 0
    reviewed = 0
    for idx, row in enumerate(rows, start=2):
        decision = row.get("qa_final_decision", "").strip()
        if decision:
            reviewed += 1
        else:
            pending += 1

        for field, allowed in ALLOWED.items():
            if field in fieldnames and row.get(field, "").strip() not in allowed:
                invalid_values.append({"line": idx, "field": field, "value": row.get(field, "")})

        severity = row.get("qa_severity", "").strip()
        error_type = row.get("qa_error_type", "").strip()
        if decision in {"remove", "uncertain"} and not error_type:
            required_value_errors.append({"line": idx, "field": "qa_error_type", "reason": "decision_remove_or_uncertain_requires_error_type"})
        if severity and severity != "none" and not error_type:
            required_value_errors.append({"line": idx, "field": "qa_error_type", "reason": "non_none_severity_requires_error_type"})

        for id_field in ["review_item_id", "task_id"]:
            if id_field in fieldnames and not row.get(id_field, "").strip():
                required_value_errors.append({"line": idx, "field": id_field, "reason": "id_field_empty"})

        for field in JSON_FIELDS:
            if field in fieldnames and row.get(field, "").strip():
                try:
                    json.loads(row[field])
                except Exception as exc:
                    json_errors.append({"line": idx, "field": field, "error": str(exc)})

    return {
        "generated_time": now(),
        "input_csv": str(path),
        "source_dataset": source,
        "rows": len(rows),
        "columns": fieldnames,
        "missing_required_columns": missing_cols,
        "pending_review_count": pending,
        "reviewed_count": reviewed,
        "invalid_values": invalid_values,
        "json_errors": json_errors,
        "required_value_errors": required_value_errors,
        "is_fatal": bool(missing_cols or invalid_values or json_errors or required_value_errors),
        "qa_final_decision_distribution": dict(Counter(row.get("qa_final_decision", "") for row in rows)),
    }


def write_md(path: Path, reports: list[dict[str, Any]]) -> None:
    lines = ["# External QA CSV Validation Report v0.1", "", f"Generated time: {now()}", ""]
    for report in reports:
        lines.extend(
            [
                f"## {report['source_dataset']}",
                "",
                f"- input_csv: `{report['input_csv']}`",
                f"- rows: {report['rows']}",
                f"- pending_review_count: {report['pending_review_count']}",
                f"- reviewed_count: {report['reviewed_count']}",
                f"- missing_required_columns: `{report['missing_required_columns']}`",
                f"- invalid_values_count: {len(report['invalid_values'])}",
                f"- json_errors_count: {len(report['json_errors'])}",
                f"- required_value_errors_count: {len(report['required_value_errors'])}",
                f"- is_fatal: `{report['is_fatal']}`",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def default_output_for(path: Path, source: str) -> Path:
    name = "metatool_csv_validation_report.json" if source == "MetaTool" else "stabletoolbench_csv_validation_report.json"
    return path.parent / name


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate external QA CSV files.")
    parser.add_argument("csv_files", nargs="+", help="One or more external QA CSV files.")
    parser.add_argument("--markdown-report", default="docs/phase1/external_qa_csv_validation_report_v0_1.md")
    args = parser.parse_args()

    reports = []
    for csv_file in args.csv_files:
        path = Path(csv_file)
        if not path.exists():
            raise SystemExit(f"Input CSV does not exist: {path}")
        report = validate(path)
        out = default_output_for(path, report["source_dataset"])
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
        print(json.dumps({"input_csv": str(path), "output_json": str(out), "is_fatal": report["is_fatal"]}, ensure_ascii=False))

    write_md(Path(args.markdown_report), reports)


if __name__ == "__main__":
    main()
