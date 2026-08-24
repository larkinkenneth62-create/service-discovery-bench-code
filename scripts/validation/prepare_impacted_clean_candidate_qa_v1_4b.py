from __future__ import annotations

import argparse
import csv
import hashlib
from collections import Counter
from pathlib import Path

from full_clean_v1_4b_common import DOC_DIR, OUTPUT_DIR, QA_DIR, V14_TASK_TRACE, V15C_PATCH, now_text, open_csv_writer, table_lines, write_json, write_md


QA_FIELDS = ["qa_final_decision", "qa_error_type", "qa_severity", "qa_notes"]


def score(*parts: str) -> int:
    return int(hashlib.md5("||".join(parts).encode("utf-8")).hexdigest(), 16)


def load_failures(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return {row["task_id"]: row for row in csv.DictReader(f)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare impacted clean-candidate QA frame for v1.5d.")
    parser.add_argument("--v14b", type=Path, default=OUTPUT_DIR / "full_clean_task_trace_v1_4b.csv")
    parser.add_argument("--v14", type=Path, default=V14_TASK_TRACE)
    parser.add_argument("--patch", type=Path, default=V15C_PATCH)
    parser.add_argument("--output", type=Path, default=QA_DIR / "impacted_clean_candidate_qa_frame_v1_4b.csv")
    args = parser.parse_args()
    if not args.v14b.exists() or not args.patch.exists():
        raise FileNotFoundError("Missing v1.4b trace or v1.5c patch")
    QA_DIR.mkdir(parents=True, exist_ok=True)
    failures = load_failures(args.patch)
    failure_task_ids = set(failures)
    failure_rows = []
    clean_candidates = []
    with args.v14b.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        base_fields = list(reader.fieldnames or [])
        for row in reader:
            tid = row.get("task_id", "")
            if tid in failure_task_ids:
                out = dict(row)
                out["qa_frame_bucket"] = "previous_failed_clean_candidate_regression"
                out["expected_status"] = "not_clean_candidate"
                out["previous_qa_item_id"] = failures[tid].get("qa_item_id", "")
                failure_rows.append(out)
            if row.get("dryrun_decision_v1_4b") == "dryrun_clean_candidate":
                clean_candidates.append(dict(row))
    clean_candidates.sort(key=lambda r: score("v14b-clean", r.get("task_id", ""), r.get("query_text", "")))
    selected_clean = []
    high_risk_terms = ["search", "image", "news", "travel", "place", "hotel", "restaurant", "weather", "translation", "domain", "finance", "exchange", "composable"]
    high_risk = [r for r in clean_candidates if any(t in (r.get("query_text", "") + " " + r.get("v12_tightening_triggered_rules_json", "")).lower() for t in high_risk_terms)]
    low_risk = [r for r in clean_candidates if r not in high_risk]
    for row in high_risk[:70] + low_risk[:30]:
        out = dict(row)
        out["qa_frame_bucket"] = "new_or_surviving_v14b_clean_candidate"
        out["expected_status"] = "manual_check_clean_quality"
        out["previous_qa_item_id"] = ""
        selected_clean.append(out)
    rows = failure_rows + selected_clean[:100]
    fields = ["qa_frame_id", "qa_frame_bucket", "expected_status", "previous_qa_item_id"] + base_fields + [f for f in QA_FIELDS if f not in base_fields]
    out_f, writer = open_csv_writer(args.output, fields)
    try:
        for i, row in enumerate(rows, start=1):
            out = dict(row)
            out["qa_frame_id"] = f"V14B-QA-{i:03d}"
            for field in QA_FIELDS:
                out[field] = ""
            writer.writerow({field: out.get(field, "") for field in fields})
    finally:
        out_f.close()
    counts = Counter(row.get("qa_frame_bucket", "") for row in rows)
    summary = {"generated_time": now_text(), "row_count": len(rows), "bucket_distribution": dict(counts), "previous_failure_rows": len(failure_rows), "new_clean_candidate_rows": len(selected_clean[:100])}
    write_json(QA_DIR / "impacted_clean_candidate_qa_frame_summary_v1_4b.json", summary)
    write_md(QA_DIR / "impacted_clean_candidate_qa_plan_v1_4b.md", ["# Impacted Clean-Candidate QA Plan v1.4b", "", f"Generated time: {now_text()}", "This frame prepares v1.5d QA only. It does not perform human review.", "", *table_lines(counts)])
    write_md(DOC_DIR / "impacted_clean_candidate_qa_frame_report_v1_4b.md", ["# Impacted Clean-Candidate QA Frame Report v1.4b", "", f"Generated time: {now_text()}", f"Output: `{args.output}`", f"Rows: {len(rows)}", "", *table_lines(counts)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
