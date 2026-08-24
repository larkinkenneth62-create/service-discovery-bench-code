from __future__ import annotations

import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


OUTPUT_DIR = Path("outputs/final_qa_v1_5")
DOC_DIR = Path("docs/phase1")
V1_4_DIR = Path("outputs/full_clean_dryrun_v1_4")
V1_4_SUMMARY = V1_4_DIR / "full_clean_dryrun_summary_v1_4.json"
V1_4_SUMMARY_REPORT = DOC_DIR / "full_clean_dryrun_summary_report_v1_4.md"
V1_4_GO_NO_GO_REPORT = DOC_DIR / "full_clean_dryrun_v1_4_go_no_go_report.md"
V1_4_TASK_TRACE = V1_4_DIR / "full_clean_task_trace_v1_4.csv"
V1_4_QA_FRAME = V1_4_DIR / "final_qa_sampling_frame_v1_4.csv"
V1_4_DEDUP_TRACE = V1_4_DIR / "dedup_precheck/dryrun_clean_candidate_dedup_trace_v1_4.csv"
V1_4_DEDUP_SUMMARY = V1_4_DIR / "dedup_precheck/dedup_precheck_summary_v1_4.json"

BUCKET_ALIASES = {
    "dryrun_clean_candidate_task_level_v1_4.csv": [
        V1_4_DIR / "task_buckets/dryrun_clean_candidate_task_level_v1_4.csv",
        V1_4_DIR / "task_buckets/dryrun_clean_candidate_all.csv",
    ],
    "dryrun_clean_candidate_high_conf_task_level_v1_4.csv": [
        V1_4_DIR / "task_buckets/dryrun_clean_candidate_high_conf_task_level_v1_4.csv",
        V1_4_DIR / "task_buckets/dryrun_clean_candidate_high_conf.csv",
    ],
    "dryrun_removed_task_level_v1_4.csv": [
        V1_4_DIR / "task_buckets/dryrun_removed_task_level_v1_4.csv",
        V1_4_DIR / "task_buckets/dryrun_removed_all.csv",
    ],
    "dryrun_service_leak_only_task_level_v1_4.csv": [
        V1_4_DIR / "task_buckets/dryrun_service_leak_only_task_level_v1_4.csv",
        V1_4_DIR / "task_buckets/dryrun_service_leak_only.csv",
    ],
    "dryrun_uncertain_task_level_v1_4.csv": [
        V1_4_DIR / "task_buckets/dryrun_uncertain_task_level_v1_4.csv",
        V1_4_DIR / "task_buckets/dryrun_uncertain_all.csv",
    ],
}

QA_HUMAN_FIELDS = [
    "qa_final_decision",
    "qa_semantic_alignment_check",
    "qa_capability_coverage_check",
    "qa_leakage_check",
    "qa_candidate_validity_check",
    "qa_task_type_check",
    "qa_dedup_check",
    "qa_error_type",
    "qa_severity",
    "qa_notes",
]

QA_BASE_FIELDS = [
    "qa_item_id",
    "qa_bucket",
    "qa_subbucket",
    "qa_group_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "prediction_level",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "dryrun_decision",
    "dryrun_bucket",
    "clean_confidence_bucket",
    "blocking_reasons",
    "warning_reasons",
    "triggered_rules",
    "api_leak_detector_status",
    "service_leak_detector_status",
    "v1_semantic_alignment_pred",
    "v1_semantic_alignment_confidence",
    "v1_capability_coverage_pred",
    "v1_capability_coverage_confidence",
    "v1_capability_coverage_reason",
    "dedup_group_id",
    "dedup_group_size",
    "is_representative_candidate",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "candidate_space_status",
    "task_type_eligibility_status",
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "qa_priority_reason",
]

QA_OUTPUT_FIELDS = QA_BASE_FIELDS + QA_HUMAN_FIELDS

QA_FIELD_OPTIONS = {
    "qa_final_decision": ["", "pass", "fail", "uncertain"],
    "qa_semantic_alignment_check": ["", "ok", "uncertain", "mismatch", "not_applicable"],
    "qa_capability_coverage_check": ["", "coverage_ok", "coverage_uncertain", "coverage_mismatch", "not_applicable"],
    "qa_leakage_check": ["", "no_blocking", "api_leak_blocking", "service_leak_only", "ambiguous"],
    "qa_candidate_validity_check": ["", "valid", "insufficient_choice_space", "uncertain", "not_applicable"],
    "qa_task_type_check": ["", "valid", "invalid", "uncertain", "not_applicable"],
    "qa_dedup_check": ["", "unique", "duplicate_ok", "duplicate_should_remove", "uncertain"],
    "qa_error_type": [
        "",
        "none",
        "api_leak",
        "service_leak",
        "choice_space_invalid",
        "gold_missing",
        "semantic_mismatch",
        "capability_mismatch",
        "task_type_invalid",
        "duplicate_issue",
        "wrong_bucket",
        "unclear",
    ],
    "qa_severity": ["", "none", "minor", "major", "critical"],
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_md(path: Path, lines: Sequence[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def write_json(path: Path, payload: object) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: Sequence[str]) -> int:
    ensure_dir(path.parent)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
            count += 1
    return count


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return sum(1 for _ in reader)


def stable_score(*parts: str) -> int:
    text = "||".join(str(part or "") for part in parts)
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)


def table_lines(counter: dict[str, int] | Counter) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    for key, value in sorted(dict(counter).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {value} |")
    return lines


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []


def json_preview(value: Any, limit: int = 500) -> str:
    data = parse_jsonish(value)
    if data:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "\n..."


def resolve_bucket_aliases() -> dict[str, dict[str, str]]:
    resolved: dict[str, dict[str, str]] = {}
    for expected, candidates in BUCKET_ALIASES.items():
        found = next((path for path in candidates if path.exists()), None)
        resolved[expected] = {
            "expected": str(candidates[0]),
            "resolved": str(found) if found else "",
            "exists": str(bool(found)).lower(),
            "used_alias": str(bool(found and found != candidates[0])).lower(),
        }
    return resolved


def archive_v1_5(root: Path) -> list[str]:
    archive_dir = root / "outputs" / "run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_final_qa_v1_5"
    ensure_dir(archive_dir)
    paths = [
        Path("scripts/validation/final_qa_v1_5_common.py"),
        Path("scripts/validation/check_final_qa_v1_5_inputs.py"),
        Path("scripts/validation/build_final_qa_review_set_v1_5.py"),
        Path("scripts/validation/build_final_qa_review_app_v1_5.py"),
        Path("scripts/validation/merge_and_analyze_final_qa_v1_5.py"),
        OUTPUT_DIR,
        DOC_DIR / "final_qa_review_protocol_v1_5.md",
        DOC_DIR / "final_qa_v1_5_go_no_go_report.md",
    ]
    copied: list[str] = []
    for rel in paths:
        src = root / rel
        if not src.exists():
            continue
        dest = archive_dir / rel
        ensure_dir(dest.parent)
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        copied.append(str(dest))
    write_md(
        archive_dir / "ARCHIVE_MANIFEST.md",
        [
            "# Final QA v1.5 Archive Manifest",
            "",
            f"Generated time: {now_text()}",
            f"Archive directory: `{archive_dir}`",
            "",
            "This archive contains the final QA sampling package only.",
            "No final clean dataset, split, baseline, model training, raw overwrite, or automatic QA labels were produced.",
            "",
            "## Archived Files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )
    return copied
