from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from qwen_semcap_v1_4d_common_step3 import request_messages


DOC_DIR = Path("docs/phase1")
FINALQA_DIR = Path("outputs/qwen_semcap_judge_v1_4d_step3/finalqa100")
ARCHIVE_DIR = Path("outputs/run_archives/2026-07-01_qwen_step3_finalqa100_reliability_audit")

LABEL_FIELD_PREFIXES = ("qa_",)
LABEL_FIELD_NAMES = {
    "manual_final_decision",
    "human_label",
    "human_final",
    "failure_label",
    "manual_label",
}
PROMPT_FORBIDDEN_STRINGS = {
    "qa_final_decision",
    "qa_notes",
    "qa_error_type",
    "qa_severity",
    "manual_final_decision",
    "human_label",
    "human_final",
    "failure_label",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing JSONL: {path}")
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl_no_overwrite(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists():
        existing = read_jsonl(path)
        if existing == rows:
            return
        raise FileExistsError(f"Refusing to overwrite existing file with different content: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_md(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def dist(rows: list[dict[str, str]], field: str) -> Counter[str]:
    return Counter(row.get(field, "") or "<blank>" for row in rows)


def table(counter: Counter[str] | dict[str, int]) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    if not counter:
        lines.append("| <empty> | 0 |")
        return lines
    for key, value in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {value} |")
    return lines


def has_label_key(item: dict[str, Any]) -> list[str]:
    hits = []
    for key in item:
        if key.startswith(LABEL_FIELD_PREFIXES) or key in LABEL_FIELD_NAMES:
            hits.append(key)
    return sorted(hits)


def prompt_leak_check(items: list[dict[str, Any]]) -> dict[str, Any]:
    prompt_hits: list[dict[str, str]] = []
    id_hits: list[dict[str, str]] = []
    for item in items:
        content = "\n".join(message.get("content", "") for message in request_messages(item))
        for marker in PROMPT_FORBIDDEN_STRINGS:
            if marker in content:
                prompt_hits.append({"custom_id": item.get("custom_id", ""), "marker": marker})
        for id_field in ["custom_id", "task_id", "record_id"]:
            value = str(item.get(id_field, "") or "")
            if value and value in content:
                id_hits.append({"custom_id": item.get("custom_id", ""), "id_field": id_field})
    return {
        "prompt_forbidden_field_hits": prompt_hits,
        "id_hits_in_prompt": id_hits,
        "prompt_content_clean": not prompt_hits and not id_hits,
    }


def perturb_requests(items: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    out = []
    for item in items:
        copy = json.loads(json.dumps(item, ensure_ascii=False))
        custom_id = str(copy.get("custom_id", ""))
        for field in ["candidate_services", "candidate_apis_brief"]:
            values = list(copy.get(field) or [])
            rng = random.Random(f"{seed}:{custom_id}:{field}")
            rng.shuffle(values)
            copy[field] = values
        copy["custom_id"] = f"{custom_id}::perturbed_seed{seed}"
        out.append(copy)
    return out


def infer_sampling_status(rows: list[dict[str, str]]) -> str:
    reasons = Counter()
    for field in ["qa_sampling_reason", "qa_subbucket", "risk_keywords_matched"]:
        if field in (rows[0].keys() if rows else []):
            reasons.update(value for value in (row.get(field, "") for row in rows) if value)
    text = " ".join(reasons.keys()).lower()
    if "risk" in text or "keyword" in text or "subbucket" in text:
        return "risk-targeted_or_stratified_sample"
    if "random" in text:
        return "random_sample"
    return "unknown_sampling_method"


def write_sampling_audit(reviewed_path: Path, rows: list[dict[str, str]], request_rows: list[dict[str, Any]]) -> Path:
    status = infer_sampling_status(rows)
    lines = [
        "# Final QA v1.5e Sampling Provenance Audit",
        "",
        f"Generated time: {now_text()}",
        f"Input reviewed CSV: `{reviewed_path}`",
        f"Input request count: {len(request_rows)}",
        f"Reviewed row count: {len(rows)}",
        "",
        f"sampling_provenance_status: `{status}`",
        "",
        "Important: the observed 23% remove rate in this 100-row final QA set must not be extrapolated to all 2168 candidates unless the sampling design is proven representative.",
        "",
        "## QA Bucket Distribution",
        "",
        *table(dist(rows, "qa_bucket")),
        "",
        "## Task Type Distribution",
        "",
        *table(dist(rows, "task_type")),
        "",
        "## QA Sampling Reason Distribution",
        "",
        *table(dist(rows, "qa_sampling_reason")),
        "",
        "## QA Subbucket Distribution",
        "",
        *table(dist(rows, "qa_subbucket")),
        "",
        "## Risk Keywords Matched Distribution",
        "",
        *table(dist(rows, "risk_keywords_matched")),
        "",
        "## v1.4c Dryrun Bucket Distribution",
        "",
        *table(dist(rows, "v1_4c_dryrun_bucket")),
        "",
        "This audit does not run full cleaning, split, baseline, or training.",
    ]
    path = DOC_DIR / "final_qa_v1_5e_sampling_provenance_audit.md"
    write_md(path, lines)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight finalQA100 and build deterministic perturbation request JSONL.")
    parser.add_argument("--reviewed-csv", type=Path, default=Path("outputs/final_qa_v1_5e/final_qa_review_items_v1_5e_gpt_manual_reviewed.csv"))
    parser.add_argument("--requests-jsonl", type=Path, default=FINALQA_DIR / "requests/qwen_step3_requests_finalqa100.jsonl")
    parser.add_argument("--perturbed-output", type=Path, default=FINALQA_DIR / "requests/qwen_step3_requests_finalqa100_perturbed_seed42.jsonl")
    parser.add_argument("--summary-output", type=Path, default=FINALQA_DIR / "qwen_step3_finalqa100_preflight_summary.json")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    reviewed_rows = read_csv(args.reviewed_csv)
    request_rows = read_jsonl(args.requests_jsonl)
    label_hits = {item.get("custom_id", ""): has_label_key(item) for item in request_rows if has_label_key(item)}
    prompt_check = prompt_leak_check(request_rows)
    perturbed_rows = perturb_requests(request_rows, args.seed)
    write_jsonl_no_overwrite(args.perturbed_output, perturbed_rows)
    sampling_audit_path = write_sampling_audit(args.reviewed_csv, reviewed_rows, request_rows)

    summary = {
        "generated_time": now_text(),
        "reviewed_csv": str(args.reviewed_csv),
        "requests_jsonl": str(args.requests_jsonl),
        "perturbed_output": str(args.perturbed_output),
        "reviewed_human_rows": len(reviewed_rows),
        "finalQA100_request_count": len(request_rows),
        "request_count_matches_reviewed_rows": len(reviewed_rows) == len(request_rows) == 100,
        "top_level_human_label_hits": label_hits,
        "human_label_fields_in_request_payload": bool(label_hits),
        **prompt_check,
        "sampling_provenance_audit": str(sampling_audit_path),
        "sample20_report_exists": Path("docs/phase1/qwen_step3_sample20_report_v1_4d.md").exists(),
        "calibration_eval_report_exists": Path("docs/phase1/qwen_step3_calibration_eval_report_v1_4d.md").exists(),
        "calibration_go_no_go_report_exists": Path("docs/phase1/qwen_step3_calibration_go_no_go_v1_4d.md").exists(),
        "full2168_outputs_generated_in_this_preflight": False,
        "perturbation_qwen_called": False,
    }
    write_json(args.summary_output, summary)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in [args.perturbed_output, args.summary_output, sampling_audit_path]:
        shutil.copy2(path, ARCHIVE_DIR / path.name)

    print(f"reviewed_human_rows: {len(reviewed_rows)}")
    print(f"finalQA100_request_count: {len(request_rows)}")
    print(f"human_label_fields_in_request_payload: {bool(label_hits)}")
    print(f"prompt_content_clean: {prompt_check['prompt_content_clean']}")
    print(f"perturbed_output: {args.perturbed_output}")
    print(f"sampling_audit: {sampling_audit_path}")
    return 0 if len(reviewed_rows) == len(request_rows) == 100 and not label_hits and prompt_check["prompt_content_clean"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
