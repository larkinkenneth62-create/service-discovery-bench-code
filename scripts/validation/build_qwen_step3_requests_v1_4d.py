from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from build_qwen_semcap_requests_v1_4d import request_from_row
from qwen_semcap_v1_4d_common import read_csv, read_jsonl, table_lines, write_jsonl, write_md
from qwen_semcap_v1_4d_common_step3 import DOC_DIR, OUTPUT_DIR, PROMPT_DOC, REQUEST_DIR, SCHEMA_PATH, QWEN_SCHEMA, build_prompt_text, now_text, write_json


HUMAN_LABEL_FIELDS = {
    "manual_final_decision",
    "semantic_alignment_check",
    "capability_coverage_check",
    "human_notes",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Qwen Step3 sample20 and calibration180 request JSONL files.")
    parser.add_argument(
        "--old-sample20",
        type=Path,
        default=Path("outputs/qwen_semcap_judge_v1_4d/requests/qwen_semcap_request_sample_20.jsonl"),
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=Path("outputs/semcap_detector_v1_implementation_v1_1/combined_semcap_calibration_180.csv"),
    )
    parser.add_argument(
        "--sample20-output",
        type=Path,
        default=REQUEST_DIR / "qwen_step3_request_sample_20.jsonl",
    )
    parser.add_argument(
        "--calibration-output",
        type=Path,
        default=REQUEST_DIR / "qwen_step3_requests_calibration_180.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DOC_DIR / "qwen_step3_request_build_report_v1_4d.md",
    )
    args = parser.parse_args()

    if not args.old_sample20.exists():
        raise FileNotFoundError(f"Missing old sample20 request JSONL: {args.old_sample20}")
    if not args.calibration.exists():
        raise FileNotFoundError(f"Missing calibration CSV: {args.calibration}")

    sample20_requests = read_jsonl(args.old_sample20)
    calibration_rows = read_csv(args.calibration)
    calibration_requests = [request_from_row(row, "step3_calibration_180") for row in calibration_rows]

    for req in calibration_requests:
        req["calibration_source"] = next((row.get("calibration_source", "") for row in calibration_rows if row.get("task_id") == req.get("task_id")), "")

    human_leaks: list[str] = []
    for req in sample20_requests + calibration_requests:
        leaked = sorted(HUMAN_LABEL_FIELDS.intersection(req.keys()))
        if leaked:
            human_leaks.append(f"{req.get('custom_id', '')}: {','.join(leaked)}")

    write_jsonl(args.sample20_output, sample20_requests)
    write_jsonl(args.calibration_output, calibration_requests)
    write_json(SCHEMA_PATH, QWEN_SCHEMA)
    write_md(
        PROMPT_DOC,
        [
            "# Qwen SemCap Judge Prompt v1.4d Step3",
            "",
            f"Generated time: {now_text()}",
            "",
            "This prompt adds gold-only evidence and conservative guard fields.",
            "It does not include exact dangerous false keep rows, task IDs, or few-shot examples.",
            "",
            "```text",
            build_prompt_text(),
            "```",
        ],
    )

    source_counts = Counter(req.get("calibration_source", "") or "<blank>" for req in calibration_requests)
    task_type_counts = Counter(req.get("task_type", "") or "<blank>" for req in calibration_requests)
    sample20_ok = len(sample20_requests) == 20
    calibration_ok = len(calibration_requests) == 180
    labels_excluded = not human_leaks

    report_lines = [
        "# Qwen Step3 Request Build Report v1.4d",
        "",
        f"Generated time: {now_text()}",
        f"Input old sample20: `{args.old_sample20}`",
        f"Input calibration: `{args.calibration}`",
        "",
        "## Output Files",
        "",
        f"- sample20 request: `{args.sample20_output}`",
        f"- calibration180 request: `{args.calibration_output}`",
        f"- schema: `{SCHEMA_PATH}`",
        f"- prompt: `{PROMPT_DOC}`",
        "",
        "## Checks",
        "",
        f"- sample20 request rows: {len(sample20_requests)}",
        f"- calibration request rows: {len(calibration_requests)}",
        f"- sample20_ok: {str(sample20_ok).lower()}",
        f"- calibration_ok: {str(calibration_ok).lower()}",
        f"- human labels excluded from prompt payload: {str(labels_excluded).lower()}",
        f"- exact dangerous false keep rows used as few-shot examples: false",
        "",
        "Step3 request payloads retain metadata for local output alignment, but Step3 request_messages removes custom_id/task_id/record_id before sending the user prompt to Qwen.",
        "",
        "## Calibration Source Distribution",
        "",
        *table_lines(source_counts),
        "",
        "## Task Type Distribution",
        "",
        *table_lines(task_type_counts),
        "",
        "No API key is read or written by this script. No full2168, full cleaning, split, baseline, or training is performed.",
    ]
    if human_leaks:
        report_lines.extend(["", "## Human Label Leaks", ""])
        report_lines.extend(f"- {item}" for item in human_leaks[:50])
    write_md(args.report, report_lines)

    print(f"sample20 requests: {len(sample20_requests)}")
    print(f"calibration requests: {len(calibration_requests)}")
    print(f"human labels excluded: {labels_excluded}")
    print(f"sample20_output: {args.sample20_output}")
    print(f"calibration_output: {args.calibration_output}")
    return 0 if sample20_ok and calibration_ok and labels_excluded else 2


if __name__ == "__main__":
    raise SystemExit(main())
