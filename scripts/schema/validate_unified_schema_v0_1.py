from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from unified_schema_v0_1_common import (
    CANONICAL_FIELDS,
    ENUM_FIELD_TO_VALUES,
    INT_FIELDS,
    JSON_FIELDS,
    count_json,
    ensure_dirs,
    now_iso,
    read_csv_rows,
    try_json,
    write_csv,
    write_json,
)


REQUIRED_FIELDS = [
    "schema_version",
    "candidate_row_id",
    "canonical_task_id",
    "source_dataset",
    "source_branch",
    "task_type",
    "query_text",
    "final_release_status",
]


def add_issue(issues: List[Dict[str, Any]], severity: str, row_index: int, field: str, message: str, value: str = "") -> None:
    issues.append({"severity": severity, "row_index": row_index, "field": field, "message": message, "value": str(value)[:500]})


def validate_row(row: Dict[str, str], idx: int, issues: List[Dict[str, Any]]) -> None:
    for field in REQUIRED_FIELDS:
        if not row.get(field):
            add_issue(issues, "fatal", idx, field, "required canonical field missing")
    for field, values in ENUM_FIELD_TO_VALUES.items():
        val = row.get(field, "")
        if val and val not in values:
            add_issue(issues, "error", idx, field, "invalid enum value", val)
    for field in JSON_FIELDS:
        val = row.get(field, "")
        if val:
            ok, parsed = try_json(val)
            if not ok:
                add_issue(issues, "fatal" if field in {"candidate_services_json", "gold_services_json", "candidate_apis_json", "gold_apis_json"} else "error", idx, field, "JSON parse failure", val)
            elif field.endswith("_json") and not isinstance(parsed, (list, dict)):
                add_issue(issues, "warning", idx, field, "JSON value is scalar, expected array/object", val)
    for field in INT_FIELDS:
        val = row.get(field, "")
        if val:
            try:
                int(val)
            except Exception:
                add_issue(issues, "error", idx, field, "integer field is not parseable", val)
    count_pairs = [
        ("candidate_service_count", "candidate_services_json"),
        ("gold_service_count", "gold_services_json"),
        ("candidate_api_count", "candidate_apis_json"),
        ("gold_api_count", "gold_apis_json"),
    ]
    for count_field, json_field in count_pairs:
        if row.get(count_field) and row.get(json_field):
            try:
                expected = int(row[count_field])
                actual = count_json(row[json_field])
                if expected != actual:
                    add_issue(issues, "warning", idx, count_field, f"count mismatch with {json_field}: expected {expected}, actual {actual}", row[count_field])
            except Exception:
                pass
    task_type = row.get("task_type", "")
    if "service" in task_type:
        if not row.get("candidate_services_json") or count_json(row.get("candidate_services_json", "")) == 0:
            add_issue(issues, "fatal", idx, "candidate_services_json", "service-level task lacks candidate services")
        if not row.get("gold_services_json") or count_json(row.get("gold_services_json", "")) == 0:
            add_issue(issues, "fatal", idx, "gold_services_json", "service-level task lacks gold services")
    if "api" in task_type:
        if not row.get("candidate_apis_json") or count_json(row.get("candidate_apis_json", "")) == 0:
            add_issue(issues, "fatal", idx, "candidate_apis_json", "API-level task lacks candidate APIs")
        if not row.get("gold_apis_json") or count_json(row.get("gold_apis_json", "")) == 0:
            add_issue(issues, "fatal", idx, "gold_apis_json", "API-level task lacks gold APIs")
    if row.get("final_release_status") == "final_keep":
        add_issue(issues, "fatal", idx, "final_release_status", "final_keep is forbidden in schema preview")
    if row.get("source_branch") == "StableToolBench-solvable" and row.get("source_policy_decision") == "":
        add_issue(issues, "fatal", idx, "source_policy_decision", "StableToolBench row missing exclusive source policy decision")
    if row.get("qa_final_decision") in {"uncertain", "remove"} and not row.get("qa_error_type"):
        add_issue(issues, "warning", idx, "qa_error_type", "qa_final_decision uncertain/remove should have qa_error_type")
    if row.get("qa_severity") and row.get("qa_severity") not in {"none", "not_applicable"} and not row.get("qa_error_type"):
        add_issue(issues, "warning", idx, "qa_error_type", "qa_severity is non-none but qa_error_type is empty")
    if not row.get("reviewer_type"):
        add_issue(issues, "warning", idx, "reviewer_type", "reviewer_type missing")
    if not row.get("query_text_zh"):
        add_issue(issues, "warning", idx, "query_text_zh", "missing optional Chinese query translation")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a CSV against ServiceDiscoveryBench unified schema v0.1.")
    parser.add_argument("--input", required=True, help="Input CSV to validate.")
    parser.add_argument("--project-root", default=".", help="Project root path.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    ensure_dirs(project_root)
    input_path = (project_root / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV does not exist: {input_path}")

    rows = read_csv_rows(input_path)
    header = []
    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        header = list(csv.DictReader(f).fieldnames or [])
    issues: List[Dict[str, Any]] = []
    for field in CANONICAL_FIELDS:
        if field not in header:
            add_issue(issues, "fatal", 0, field, "canonical field missing from header")
    for idx, row in enumerate(rows, 1):
        validate_row(row, idx, issues)

    severity_counts = Counter(i["severity"] for i in issues)
    stem = input_path.stem
    out_dir = project_root / "outputs" / "unified_schema_v0_1" / "validation"
    docs_dir = project_root / "docs" / "schema"
    summary = {
        "generated_at": now_iso(),
        "input": str(input_path),
        "row_count": len(rows),
        "issue_count": len(issues),
        "severity_counts": dict(severity_counts),
        "fatal_count": severity_counts.get("fatal", 0),
        "error_count": severity_counts.get("error", 0),
        "warning_count": severity_counts.get("warning", 0),
        "valid_for_schema_preview": severity_counts.get("fatal", 0) == 0 and severity_counts.get("error", 0) == 0,
        "can_call_any_preview_final_dataset": False,
    }
    write_json(out_dir / f"{stem}_schema_validation.json", summary)
    write_csv(out_dir / f"{stem}_schema_validation_errors.csv", issues, ["severity", "row_index", "field", "message", "value"])
    report = [
        "# Unified Schema Validation Report V0.1",
        "",
        f"Generated at: {now_iso()}",
        f"Input: `{input_path}`",
        f"Rows: {len(rows)}",
        "",
        "## Result",
        f"- fatal: {severity_counts.get('fatal', 0)}",
        f"- error: {severity_counts.get('error', 0)}",
        f"- warning: {severity_counts.get('warning', 0)}",
        f"- valid_for_schema_preview: {summary['valid_for_schema_preview']}",
        "",
        "This validation does not make the input a final dataset.",
    ]
    (docs_dir / "UNIFIED_SCHEMA_VALIDATION_REPORT_V0_1.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

