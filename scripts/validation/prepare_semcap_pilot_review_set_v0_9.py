"""Prepare v0.9 semcap pilot human review set and Go/No-Go report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from semcap_detector_v0_9_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    archive_v0_9,
    count_by,
    distribution_rows,
    ensure_dirs,
    html_review_app,
    markdown_table,
    now_str,
    read_csv,
    write_csv,
)


MANUAL_FIELDS = [
    "manual_final_decision",
    "semantic_alignment_check",
    "capability_coverage_check",
    "leakage_check",
    "candidate_validity_check",
    "task_type_check",
    "human_notes",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare v0.9 semcap pilot review set of 80 items.")
    parser.add_argument("--pilot-trace", type=Path, default=OUTPUT_DIR / "v0_8_sample_policy_trace_with_semcap_pilot.csv")
    parser.add_argument("--eval-summary", type=Path, default=OUTPUT_DIR / "semcap_round3_eval_summary.json")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    parser.add_argument("--max-items", type=int, default=80)
    return parser


def add_rows(selected: List[Dict[str, object]], keys: set[str], rows: List[Dict[str, str]], bucket: str, limit: int) -> None:
    for row in rows:
        if len([r for r in selected if r.get("review_bucket") == bucket]) >= limit:
            break
        key = row.get("task_id", "") + "|" + row.get("v0_8_sample_id", "")
        if key in keys:
            continue
        selected.append({**row, "review_bucket": bucket})
        keys.add(key)


def build_review(rows: List[Dict[str, str]], max_items: int) -> List[Dict[str, object]]:
    selected: List[Dict[str, object]] = []
    keys: set[str] = set()
    high_ok = [
        row
        for row in rows
        if row.get("pilot_semantic_alignment_pred") == "ok"
        and row.get("pilot_semantic_alignment_confidence") == "high"
        and row.get("pilot_capability_coverage_pred") == "coverage_ok"
        and row.get("pilot_capability_coverage_confidence") == "high"
    ]
    mismatch = [row for row in rows if row.get("pilot_capability_coverage_pred") == "coverage_mismatch"]
    uncertain = [row for row in rows if row.get("pilot_capability_coverage_pred") == "coverage_uncertain"]
    risk = [
        row
        for row in rows
        if row.get("api_leak_detector_status") == "api_leak_weak_or_generic"
        or row.get("service_leak_detector_status") == "service_leak_only"
        or row.get("policy_decision_pilot") == "pilot_keep_candidate"
    ]
    add_rows(selected, keys, high_ok, "high_confidence_ok", 20)
    add_rows(selected, keys, mismatch, "coverage_mismatch", 20)
    add_rows(selected, keys, uncertain, "coverage_uncertain", 20)
    add_rows(selected, keys, risk, "disagreement_or_boundary_risk", 20)
    for row in rows:
        if len(selected) >= max_items:
            break
        key = row.get("task_id", "") + "|" + row.get("v0_8_sample_id", "")
        if key not in keys:
            selected.append({**row, "review_bucket": "backfill"})
            keys.add(key)
    selected = selected[:max_items]
    for idx, row in enumerate(selected, start=1):
        row["review_item_id"] = f"SCV09-{idx:03d}"
        for field in MANUAL_FIELDS:
            row[field] = ""
    return selected


def write_sampling_report(path: Path, rows: List[Dict[str, object]]) -> None:
    lines = [
        "# SemCap Pilot Review Sampling Report v0.9",
        "",
        f"Generated time: {now_str()}",
        f"Review item count: {len(rows)}",
        "",
        "Scope: human review package only. Detector predictions are not human final labels.",
        "",
        "## Review Bucket Distribution",
        "",
    ]
    lines.extend(markdown_table(distribution_rows(rows, "review_bucket"), ["value", "count"], max_rows=20))
    lines.extend(["", "## Pilot Decision Distribution", ""])
    lines.extend(markdown_table(distribution_rows(rows, "policy_decision_pilot"), ["value", "count"], max_rows=20))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_go_no_go(path: Path, summary: Dict[str, object], review_rows: List[Dict[str, object]]) -> None:
    dangerous = int(summary.get("dangerous_false_keep_count", 999))
    passed = bool(summary.get("minimum_safety_passed", False))
    lines = [
        "# Semantic Capability Detector Pilot v0.9 Go / No-Go Report",
        "",
        f"Generated time: {now_str()}",
        f"Round3 eval rows: {summary.get('row_count', 'not_available')}",
        f"Review pack rows: {len(review_rows)}",
        "",
        "Scope: v0.9 is a detector pilot. It does not permit full cleaning, split, baseline, or model training.",
        "",
        "## Answers",
        "",
        f"1. Can heuristic semcap detector safely catch coverage_mismatch on Round3: `{str(passed).lower()}`",
        f"2. Dangerous false keep count: `{dangerous}`",
        "3. Can detector be directly used for full cleaning: `false`",
        "4. Can project enter split: `false`",
        "5. Can project enter baseline: `false`",
        "6. Need semcap pilot review 80: `true`",
        "7. Next step: manually review `semcap_pilot_review_items_80.csv`, then revise semcap detector v1.",
        "",
        "## Go / No-Go Decision v0.9",
        "",
        "can_use_semcap_detector_for_full_cleaning_now: false",
        "can_run_full_cleaning_now: false",
        "can_create_split_now: false",
        "can_run_paper_baseline_now: false",
        "can_prepare_semcap_pilot_review_80: true",
        "",
        "recommended_next_step:",
        "Complete semcap pilot review 80, compare human labels with heuristic predictions, then revise detector v1.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    if not args.pilot_trace.exists() or not args.eval_summary.exists():
        print("ERROR: missing pilot trace or eval summary input.")
        return 1
    _, rows = read_csv(args.pilot_trace)
    summary = json.loads(args.eval_summary.read_text(encoding="utf-8"))
    review_rows = build_review(rows, args.max_items)
    csv_path = args.output_dir / "semcap_pilot_review_items_80.csv"
    html_path = args.output_dir / "semcap_pilot_review_app_80.html"
    report_path = args.output_dir / "semcap_pilot_review_sampling_report.md"
    write_csv(csv_path, review_rows)
    html_path.write_text(html_review_app(review_rows), encoding="utf-8")
    write_sampling_report(report_path, review_rows)
    go_path = args.docs_dir / "semantic_capability_detector_pilot_v0_9_go_no_go_report.md"
    write_go_no_go(go_path, summary, review_rows)
    manifest = archive_v0_9()
    print(f"Wrote {csv_path} ({len(review_rows)} rows)")
    print(f"Wrote {html_path}")
    print(f"Wrote {report_path}")
    print(f"Review bucket distribution: {count_by(review_rows, 'review_bucket')}")
    print(f"Wrote {go_path}")
    print(f"Wrote archive manifest: {manifest}")
    print("Go / No-Go Decision v0.9:")
    print("can_use_semcap_detector_for_full_cleaning_now: false")
    print("can_run_full_cleaning_now: false")
    print("can_create_split_now: false")
    print("can_run_paper_baseline_now: false")
    print("can_prepare_semcap_pilot_review_80: true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
