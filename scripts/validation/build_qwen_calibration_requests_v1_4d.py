from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from build_qwen_semcap_requests_v1_4d import request_from_row
from qwen_semcap_v1_4d_common import (
    CALIBRATION_180,
    DOC_DIR,
    REQUEST_DIR,
    ensure_dir,
    now_text,
    read_csv,
    table_lines,
    write_jsonl,
    write_md,
)


HUMAN_LABEL_FIELDS = {
    "manual_final_decision",
    "semantic_alignment_check",
    "capability_coverage_check",
    "human_notes",
}


REQUIRED_REQUEST_FIELDS = [
    "custom_id",
    "calibration_source",
    "record_id",
    "task_id",
    "query_text",
    "task_type",
    "prediction_level",
    "candidate_services",
    "candidate_apis_brief",
    "gold_services",
    "gold_apis",
    "existing_policy_signals",
]


def build_request(row: dict[str, str]) -> dict[str, Any]:
    payload = request_from_row(row, "calibration_180")
    payload["calibration_source"] = row.get("calibration_source", "")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Qwen calibration180 request JSONL without human labels in prompts.")
    parser.add_argument("--calibration", type=Path, default=CALIBRATION_180)
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=REQUEST_DIR / "qwen_semcap_requests_calibration_180.jsonl",
    )
    parser.add_argument(
        "--sample10-jsonl",
        type=Path,
        default=REQUEST_DIR / "qwen_semcap_requests_calibration_sample_10.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DOC_DIR / "qwen_semcap_calibration_request_build_report_v1_4d.md",
    )
    args = parser.parse_args()

    if not args.calibration.exists():
        raise FileNotFoundError(f"Missing calibration180 CSV: {args.calibration}")

    rows = read_csv(args.calibration)
    requests = [build_request(row) for row in rows]
    missing_required: list[dict[str, str]] = []
    human_label_leaks: list[dict[str, str]] = []
    for req in requests:
        missing = [field for field in REQUIRED_REQUEST_FIELDS if field not in req]
        if missing:
            missing_required.append({"custom_id": str(req.get("custom_id", "")), "missing_fields": ",".join(missing)})
        leaked = sorted(HUMAN_LABEL_FIELDS.intersection(req.keys()))
        if leaked:
            human_label_leaks.append({"custom_id": str(req.get("custom_id", "")), "leaked_fields": ",".join(leaked)})

    ensure_dir(args.output_jsonl.parent)
    write_jsonl(args.output_jsonl, requests)
    write_jsonl(args.sample10_jsonl, requests[:10])

    source_counts = Counter(req.get("calibration_source", "") or "<blank>" for req in requests)
    task_type_counts = Counter(req.get("task_type", "") or "<blank>" for req in requests)
    truncation_count = sum(1 for req in requests if req.get("truncation_applied"))
    human_labels_excluded = not human_label_leaks
    build_ok = len(requests) == 180 and not missing_required and human_labels_excluded

    lines = [
        "# Qwen SemCap Calibration Request Build Report v1.4d",
        "",
        f"Generated time: {now_text()}",
        f"Input calibration: `{args.calibration}`",
        f"Sample count: {len(rows)}",
        "",
        "## Output Files",
        "",
        f"- calibration 180 requests: `{args.output_jsonl}`",
        f"- calibration sample10 requests: `{args.sample10_jsonl}`",
        "",
        "## Build Checks",
        "",
        f"- request_count: {len(requests)}",
        f"- build_ok: {str(build_ok).lower()}",
        f"- human labels excluded from prompt: {str(human_labels_excluded).lower()}",
        f"- missing_required_request_rows: {len(missing_required)}",
        f"- human_label_leak_rows: {len(human_label_leaks)}",
        f"- api_truncation_applied_rows: {truncation_count}",
        "",
        "Human label fields excluded from request payload:",
        "",
        *[f"- `{field}`" for field in sorted(HUMAN_LABEL_FIELDS)],
        "",
        "## Calibration Source Distribution",
        "",
        *table_lines(source_counts),
        "",
        "## Task Type Distribution",
        "",
        *table_lines(task_type_counts),
        "",
        "No API key is read or written by this script. No full cleaning, split, baseline, or training is performed.",
    ]
    if missing_required:
        lines.extend(["", "## Missing Required Request Fields", ""])
        lines.extend(f"- {row['custom_id']}: {row['missing_fields']}" for row in missing_required[:20])
    if human_label_leaks:
        lines.extend(["", "## Human Label Leakage Rows", ""])
        lines.extend(f"- {row['custom_id']}: {row['leaked_fields']}" for row in human_label_leaks[:20])
    write_md(args.report, lines)

    print(f"calibration rows: {len(rows)}")
    print(f"request rows: {len(requests)}")
    print(f"human labels excluded from prompt: {human_labels_excluded}")
    print(f"output_jsonl: {args.output_jsonl}")
    print(f"sample10_jsonl: {args.sample10_jsonl}")
    return 0 if build_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
