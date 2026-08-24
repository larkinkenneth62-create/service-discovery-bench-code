from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


OUTPUT_DIR = Path("outputs/small_cleaning_dryrun_v1_2")
DOC_DIR = Path("docs/phase1")
V1_1_DIR = Path("outputs/semcap_detector_v1_implementation_v1_1")
PRIMARY_POLICY_TRACE = V1_1_DIR / "v0_8_sample_policy_trace_with_semcap_v1.csv"
ALT_POLICY_TRACE = V1_1_DIR / "v0_8_sample_policy_trace_with_semcap_v1_1.csv"
V1_1_PREDICTIONS = V1_1_DIR / "semcap_predictions_v0_8_sample_v1.csv"
V1_1_EVAL_SUMMARY = V1_1_DIR / "semcap_v1_eval_summary.json"
V4_2_POLICY = Path("docs/phase1/manual_audit_rule_v4_2_candidate.md")

V1_1_REPORTS = [
    Path("docs/phase1/semcap_detector_v1_1_eval_report.md"),
    Path("docs/phase1/v0_8_sample_policy_trace_with_semcap_v1_1_report.md"),
    Path("docs/phase1/semcap_detector_v1_1_go_no_go_report.md"),
]

FIELD_ALIASES = {
    "semantic_alignment_pred": ["semantic_alignment_pred", "v1_semantic_alignment_pred"],
    "capability_coverage_pred": ["capability_coverage_pred", "v1_capability_coverage_pred"],
    "semantic_alignment_confidence": ["semantic_alignment_confidence", "v1_semantic_alignment_confidence"],
    "capability_coverage_confidence": ["capability_coverage_confidence", "v1_capability_coverage_confidence"],
}

REQUIRED_CANONICAL_FIELDS = [
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "prediction_level",
    "candidate_space_status",
    "task_type_eligibility_status",
    "api_leak_detector_status",
    "api_leak_strength",
    "service_leak_detector_status",
    "semantic_alignment_pred",
    "capability_coverage_pred",
    "semantic_alignment_confidence",
    "capability_coverage_confidence",
    "policy_decision_v1",
    "policy_bucket_v1",
    "blocking_reasons_v1",
    "warning_reasons_v1",
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required input not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_csv_with_fields(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required input not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: Sequence[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_md(path: Path, lines: Sequence[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def write_json(path: Path, payload: object) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def norm(value: object) -> str:
    return str(value or "").strip().lower()


def truthy_yes(value: object) -> bool:
    return norm(value) in {"yes", "true", "1"}


def to_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def value_counter(rows: Iterable[Dict[str, object]], field: str) -> Dict[str, int]:
    return dict(Counter((str(row.get(field, "")) or "<blank>").strip() or "<blank>" for row in rows))


def table_lines(counter: Dict[str, int]) -> List[str]:
    lines = ["| value | count |", "|---|---|"]
    for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {count} |")
    return lines


def resolve_policy_trace(root: Path) -> Path | None:
    primary = root / PRIMARY_POLICY_TRACE
    if primary.exists():
        return primary
    alt = root / ALT_POLICY_TRACE
    if alt.exists():
        return alt
    candidates = [
        path
        for path in (root / V1_1_DIR).glob("*")
        if path.is_file()
        and all(token in path.name.lower() for token in ["v0_8", "sample", "policy", "trace", "semcap", "v1"])
    ]
    return candidates[0] if candidates else None


def build_column_mapping(fields: Sequence[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    field_set = set(fields)
    for canonical in REQUIRED_CANONICAL_FIELDS:
        candidates = FIELD_ALIASES.get(canonical, [canonical])
        for candidate in candidates:
            if candidate in field_set:
                mapping[canonical] = candidate
                break
    return mapping


def canonical_value(row: Dict[str, str], canonical_field: str) -> str:
    for field in FIELD_ALIASES.get(canonical_field, [canonical_field]):
        if field in row:
            return row.get(field, "")
    return row.get(canonical_field, "")


def add_canonical_aliases(row: Dict[str, str]) -> Dict[str, str]:
    out = dict(row)
    for canonical in FIELD_ALIASES:
        out[canonical] = canonical_value(row, canonical)
    return out


def prediction_level(row: Dict[str, str]) -> str:
    level = norm(row.get("prediction_level") or row.get("task_type"))
    if "api" in level:
        return "api"
    return "service"


def has_strong_or_blocking_api_leak(row: Dict[str, str]) -> bool:
    status = norm(row.get("api_leak_detector_status"))
    strength = norm(row.get("api_leak_strength"))
    return "blocking" in status or "strong" in status or "strong" in strength


def has_weak_or_ambiguous_api_leak(row: Dict[str, str]) -> bool:
    status = norm(row.get("api_leak_detector_status"))
    strength = norm(row.get("api_leak_strength"))
    return any(token in status + " " + strength for token in ["weak", "generic", "ambiguous"])


def is_service_leak_only(row: Dict[str, str]) -> bool:
    return norm(row.get("service_leak_detector_status")) == "service_leak_only"


def candidate_choice_valid(row: Dict[str, str]) -> bool:
    status = norm(row.get("candidate_space_status"))
    if status.startswith("invalid"):
        return False
    level = prediction_level(row)
    if level == "service":
        return to_int(row.get("candidate_service_count")) > max(1, to_int(row.get("gold_service_count")))
    return to_int(row.get("candidate_api_count")) > max(1, to_int(row.get("gold_api_count")))


def task_type_valid(row: Dict[str, str]) -> bool:
    status = norm(row.get("task_type_eligibility_status"))
    check = norm(row.get("task_type_check"))
    return not status.startswith("invalid") and check != "invalid"


def gold_in_candidates(row: Dict[str, str]) -> bool:
    return truthy_yes(row.get("gold_in_candidate_services")) and truthy_yes(row.get("gold_in_candidate_apis"))


def semcap_ok(row: Dict[str, str]) -> bool:
    return norm(row.get("semantic_alignment_pred")) == "ok" and norm(row.get("capability_coverage_pred")) == "coverage_ok"


def clean_confidence_bucket(row: Dict[str, str]) -> str:
    sem_conf = norm(row.get("semantic_alignment_confidence"))
    cap_conf = norm(row.get("capability_coverage_confidence"))
    if sem_conf == "high" and cap_conf == "high":
        return "clean_candidate_high_conf"
    return "clean_candidate_medium_conf"


def blocking_flags(row: Dict[str, str]) -> List[str]:
    flags: List[str] = []
    level = prediction_level(row)
    if has_strong_or_blocking_api_leak(row):
        flags.append("strong_or_blocking_api_leak")
    if not gold_in_candidates(row):
        flags.append("gold_missing_from_candidate")
    if level == "service" and not candidate_choice_valid(row):
        flags.append("service_level_no_choice_space")
    if level == "api" and not candidate_choice_valid(row):
        flags.append("api_level_no_api_choice_space")
    if norm(row.get("semantic_alignment_pred")) == "mismatch":
        flags.append("semantic_mismatch")
    if norm(row.get("capability_coverage_pred")) == "coverage_mismatch":
        flags.append("capability_mismatch")
    if not task_type_valid(row):
        flags.append("task_type_invalid")
    if level == "service" and is_service_leak_only(row):
        flags.append("service_level_service_leak_only")
    if not row.get("semantic_alignment_pred") or not row.get("capability_coverage_pred"):
        flags.append("missing_semantic_or_capability_output")
    return sorted(set(flags))


def warning_flags(row: Dict[str, str]) -> List[str]:
    flags: List[str] = []
    level = prediction_level(row)
    if norm(row.get("semantic_alignment_pred")) == "uncertain":
        flags.append("semantic_uncertain")
    if norm(row.get("capability_coverage_pred")) == "coverage_uncertain":
        flags.append("coverage_uncertain")
    if has_weak_or_ambiguous_api_leak(row):
        flags.append("weak_or_generic_api_leak_unresolved")
    if level == "api" and is_service_leak_only(row):
        flags.append("api_level_service_leak_warning")
    if norm(row.get("semantic_alignment_confidence")) == "medium" or norm(row.get("capability_coverage_confidence")) == "medium":
        flags.append("medium_confidence")
    if truthy_yes(row.get("requires_human_review_v1")):
        flags.append("policy_requires_review")
    return sorted(set(flags))


def assign_dryrun_bucket(row: Dict[str, str]) -> Dict[str, str]:
    row = add_canonical_aliases(row)
    bflags = blocking_flags(row)
    wflags = warning_flags(row)
    level = prediction_level(row)

    if level == "service" and is_service_leak_only(row) and "strong_or_blocking_api_leak" not in bflags:
        bucket = "dryrun_service_leak_only"
        subbucket = "service_leak_only"
        decision_reason = "service-level sample has service_leak_only, so it is separated from clean candidates"
    elif bflags:
        bucket = "dryrun_removed"
        subbucket = "removed_policy_blocked"
        decision_reason = "blocking flags: " + ";".join(bflags)
    elif semcap_ok(row):
        bucket = "dryrun_clean_candidate"
        subbucket = clean_confidence_bucket(row)
        decision_reason = "semantic ok, coverage ok, choice space/gold/task gates pass"
    else:
        bucket = "dryrun_uncertain"
        subbucket = "uncertain_semcap_or_warning"
        decision_reason = "uncertain flags: " + ";".join(wflags or ["semcap_not_clean_ok"])

    out = dict(row)
    out.update(
        {
            "dryrun_bucket_v1_2": bucket,
            "dryrun_subbucket_v1_2": subbucket,
            "dryrun_decision_reason_v1_2": decision_reason,
            "blocking_flags_v1_2": ";".join(bflags),
            "warning_flags_v1_2": ";".join(wflags),
            "keep_confidence_bucket": subbucket if bucket == "dryrun_clean_candidate" else "",
            "can_be_final_clean_release_without_more_qa": "false",
        }
    )
    return out


def why_kept(row: Dict[str, str]) -> str:
    return (
        "Kept for dry-run because semantic_alignment_pred=ok, capability_coverage_pred=coverage_ok, "
        "candidate/gold/task gates pass, and no blocking leak/no-choice/mismatch flag was detected."
    )


def why_risky(row: Dict[str, str]) -> str:
    warnings = row.get("warning_flags_v1_2") or ""
    if warnings:
        return "Warnings: " + warnings
    if row.get("keep_confidence_bucket") == "clean_candidate_medium_conf":
        return "Medium confidence dry-run candidate; not allowed as final clean release without more QA."
    return ""


def dangerous_flags_for_clean(row: Dict[str, str]) -> List[str]:
    if row.get("dryrun_bucket_v1_2") != "dryrun_clean_candidate":
        return []
    flags: List[str] = []
    if has_strong_or_blocking_api_leak(row):
        flags.append("strong_or_blocking_api_leak_into_clean")
    if not gold_in_candidates(row):
        flags.append("gold_missing_from_candidate_into_clean")
    level = prediction_level(row)
    if level == "service" and not candidate_choice_valid(row):
        flags.append("service_level_no_choice_space_into_clean")
    if level == "api" and not candidate_choice_valid(row):
        flags.append("api_level_no_api_choice_space_into_clean")
    if norm(row.get("semantic_alignment_pred")) == "mismatch":
        flags.append("semantic_mismatch_into_clean")
    if norm(row.get("capability_coverage_pred")) == "coverage_mismatch":
        flags.append("capability_mismatch_into_clean")
    if norm(row.get("semantic_alignment_pred")) == "uncertain":
        flags.append("semantic_uncertain_into_clean")
    if norm(row.get("capability_coverage_pred")) == "coverage_uncertain":
        flags.append("coverage_uncertain_into_clean")
    if level == "service" and is_service_leak_only(row):
        flags.append("service_level_service_leak_only_into_clean")
    if not task_type_valid(row):
        flags.append("task_type_invalid_into_clean")
    if not row.get("semantic_alignment_pred") or not row.get("capability_coverage_pred"):
        flags.append("missing_semantic_or_capability_into_clean")
    return sorted(set(flags))


def archive_v1_2(root: Path) -> List[str]:
    archive_dir = root / "outputs/run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_small_cleaning_dryrun_v1_2"
    ensure_dir(archive_dir)
    paths = [
        Path("scripts/validation/check_small_cleaning_dryrun_v1_2_inputs.py"),
        Path("scripts/validation/build_small_cleaning_dryrun_buckets_v1_2.py"),
        Path("scripts/validation/inspect_policy_keep_candidates_v1_2.py"),
        Path("scripts/validation/check_small_cleaning_dryrun_dangerous_errors_v1_2.py"),
        Path("scripts/validation/export_keep_candidate_readable_table_v1_2.py"),
        Path("scripts/validation/small_cleaning_v1_2_common.py"),
        OUTPUT_DIR,
        DOC_DIR / "small_cleaning_dryrun_bucket_report_v1_2.md",
        DOC_DIR / "policy_keep_candidate_inspection_report_v1_2.md",
        DOC_DIR / "small_cleaning_dryrun_dangerous_error_report_v1_2.md",
        DOC_DIR / "keep_candidate_readable_46_v1_2.md",
        DOC_DIR / "small_cleaning_dryrun_v1_2_go_no_go_report.md",
    ]
    copied: List[str] = []
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
    manifest = [
        "# Small Cleaning Dry-Run v1.2 Archive Manifest",
        "",
        f"Generated time: {now_text()}",
        f"Archive directory: `{archive_dir}`",
        "",
        "No full cleaning, split, baseline, model training, final clean dataset, or new human review was run.",
        "",
        "## Archived files",
        "",
        *[f"- `{path}`" for path in copied],
    ]
    write_md(archive_dir / "ARCHIVE_MANIFEST.md", manifest)
    return copied
