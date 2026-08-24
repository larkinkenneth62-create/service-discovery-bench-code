from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from semcap_v1_2_tightening_utils import (
    V12_FIELDS,
    has_domain_guard_violation,
    has_extra_gold_service,
    has_missing_core_requirement,
    parse_jsonish,
)


OUTPUT_DIR = Path("outputs/full_clean_dryrun_v1_4b")
TASK_BUCKET_DIR = OUTPUT_DIR / "task_buckets"
REGRESSION_DIR = OUTPUT_DIR / "regression"
DEDUP_DIR = OUTPUT_DIR / "dedup_precheck"
DIFF_DIR = OUTPUT_DIR / "diff"
QA_DIR = OUTPUT_DIR / "qa"
DOC_DIR = Path("docs/phase1")

V14_DIR = Path("outputs/full_clean_dryrun_v1_4")
V14_TASK_TRACE = V14_DIR / "full_clean_task_trace_v1_4.csv"
V14_CANDIDATE_TRACE = V14_DIR / "full_clean_candidate_trace_v1_4.csv"
V14_SUMMARY = V14_DIR / "full_clean_dryrun_summary_v1_4.json"
V14_DETECTOR_TRACE = V14_DIR / "full_raw_detector_trace_v1_4.csv"
V14_SEMCAP_TRACE = V14_DIR / "full_raw_semcap_trace_v1_4.csv"
RAW_TASK = Path("outputs/toolbench_full_raw_streaming_v1_3/full/toolbench_full_task_level_raw.csv")
RAW_CANDIDATE = Path("outputs/toolbench_full_raw_streaming_v1_3/full/toolbench_full_candidate_level_raw.csv")

V15C_TAXONOMY = DOC_DIR / "final_qa_clean_candidate_failure_taxonomy_v1_5c.md"
V15C_PATCH = Path("outputs/final_qa_v1_5c/final_qa_clean_candidate_failure_patch_v1_5c.csv")
V12_RULE_DOC = DOC_DIR / "semcap_v1_2_tightening_rules_candidate.md"
V15C_PLAN = DOC_DIR / "policy_tightening_plan_v1_5c.md"
V15C_SUMMARY = Path("outputs/final_qa_v1_5c/v1_5c_failure_analysis_summary.json")
V15C_GO_NO_GO = DOC_DIR / "v1_5c_go_no_go_report.md"
V42_POLICY = DOC_DIR / "manual_audit_rule_v4_2_candidate.md"
SEMCAP_V1_SCRIPT = Path("scripts/validation/run_semcap_heuristic_detector_v1_1.py")
SEMCAP_V1_RULE = DOC_DIR / "semantic_capability_detector_v1_rule_candidate.md"
SEMCAP_V1_EVAL = DOC_DIR / "semcap_detector_v1_1_eval_report.md"
CALIBRATION_180 = Path("outputs/semcap_detector_v1_implementation_v1_1/combined_semcap_calibration_180.csv")
PREDICTIONS_180 = Path("outputs/semcap_detector_v1_implementation_v1_1/semcap_predictions_combined_180_v1.csv")

V14B_POLICY_FIELDS = [
    "dryrun_decision_v1_4b",
    "dryrun_bucket_v1_4b",
    "blocking_reasons_v1_4b",
    "warning_reasons_v1_4b",
    "triggered_rules_v1_4b",
    "is_dryrun_clean_candidate_v1_4b",
    "is_dryrun_removed_v1_4b",
    "is_dryrun_uncertain_v1_4b",
    "is_dryrun_service_leak_only_v1_4b",
    "clean_confidence_bucket_v1_4b",
    "requires_final_qa_v1_4b",
    "v1_4b_change_from_v1_4",
]


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


def open_csv_writer(path: Path, fieldnames: Sequence[str]) -> tuple[Any, csv.DictWriter]:
    ensure_dir(path.parent)
    f = path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
    writer.writeheader()
    return f, writer


def append_csv_row(writer: csv.DictWriter, row: dict[str, Any], fieldnames: Sequence[str]) -> None:
    writer.writerow({field: row.get(field, "") for field in fieldnames})


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return sum(1 for _ in reader)


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def truthy(value: Any) -> bool:
    return norm(value) in {"true", "1", "yes"}


def table_lines(counter: dict[str, int] | Counter) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    for key, count in sorted(dict(counter).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {count} |")
    return lines


def policy_decide_v14b(row: dict[str, Any]) -> dict[str, Any]:
    blocking: list[str] = []
    warnings: list[str] = []
    rules: list[str] = []
    level = norm(row.get("prediction_level"))

    if norm(row.get("api_leak_detector_status")) == "api_leak_blocking" or norm(row.get("api_leak_strength")) == "strong":
        blocking.append("api_leak")
        rules.append("remove_blocking_api_leak")
    if row.get("gold_in_candidate_services") not in {"yes"} or row.get("gold_in_candidate_apis") not in {"yes", "yes_api_name_only"}:
        blocking.append("gold_missing")
        rules.append("remove_gold_missing")
    if norm(row.get("candidate_space_status")).startswith("invalid"):
        blocking.append("choice_space_invalid")
        rules.append("remove_choice_space_invalid")
    if norm(row.get("task_type_eligibility_status")).startswith("invalid"):
        blocking.append("task_type_invalid")
        rules.append("remove_task_type_invalid")
    if norm(row.get("v12_semantic_alignment_pred")) == "mismatch":
        blocking.append("semantic_mismatch")
        rules.append("remove_v12_semantic_mismatch")
    if norm(row.get("v12_capability_coverage_pred")) == "coverage_mismatch":
        blocking.append("capability_mismatch")
        rules.append("remove_v12_capability_mismatch")
    if has_extra_gold_service(row):
        blocking.append("wrong_gold_set")
        rules.append("remove_wrong_gold_set")
    if has_domain_guard_violation(row):
        blocking.append("domain_guard_violation")
        rules.append("remove_domain_specific_guard_violation")
    if has_missing_core_requirement(row) and norm(row.get("v12_capability_coverage_pred")) == "coverage_mismatch":
        blocking.append("missing_core_requirement")
        rules.append("remove_missing_core_requirement")

    if level == "service" and norm(row.get("service_leak_detector_status")) == "service_leak_only":
        if not blocking:
            decision = "dryrun_service_leak_only"
            bucket = "dryrun_service_leak_only"
            rules.append("separate_service_level_service_leak")
        else:
            decision = ""
            bucket = ""
    else:
        decision = ""
        bucket = ""

    if not decision:
        if blocking:
            priority = [
                "api_leak",
                "gold_missing",
                "choice_space_invalid",
                "task_type_invalid",
                "semantic_mismatch",
                "wrong_gold_set",
                "domain_guard_violation",
                "missing_core_requirement",
                "capability_mismatch",
            ]
            primary = next((item for item in priority if item in blocking), blocking[0])
            bucket = {
                "api_leak": "removed_api_leak",
                "gold_missing": "removed_gold_missing",
                "choice_space_invalid": "removed_choice_space_invalid",
                "task_type_invalid": "removed_task_type_invalid",
                "semantic_mismatch": "removed_semantic_mismatch",
                "wrong_gold_set": "removed_wrong_gold_set",
                "domain_guard_violation": "removed_capability_mismatch",
                "missing_core_requirement": "removed_capability_mismatch",
                "capability_mismatch": "removed_capability_mismatch",
            }[primary]
            decision = "dryrun_removed"
        else:
            if norm(row.get("v12_semantic_alignment_pred")) == "uncertain":
                warnings.append("semantic_uncertain")
                rules.append("uncertain_v12_semantic")
            if norm(row.get("v12_capability_coverage_pred")) == "coverage_uncertain":
                warnings.append("coverage_uncertain")
                rules.append("uncertain_v12_semcap")
            if norm(row.get("api_leak_detector_status")) == "api_leak_weak_or_generic":
                warnings.append("weak_or_generic_api_leak")
                rules.append("uncertain_weak_api_leak")
            if norm(row.get("service_leak_detector_status")) == "ambiguous_service_leak":
                warnings.append("ambiguous_service_leak")
                rules.append("uncertain_ambiguous_service_leak")
            if truthy(row.get("v12_generic_search_overtrust_flag")):
                warnings.append("generic_search_overtrusted")
                rules.append("uncertain_generic_search_overtrusted")
            if norm(row.get("v12_gold_set_integrity_status")) not in {"ok", "not_applicable"}:
                warnings.append("wrong_gold_set_uncertain")
                rules.append("uncertain_wrong_gold_set")

            if warnings:
                decision = "dryrun_uncertain"
                if "generic_search_overtrusted" in warnings:
                    bucket = "uncertain_generic_search_overtrusted"
                elif "wrong_gold_set_uncertain" in warnings:
                    bucket = "uncertain_wrong_gold_set"
                elif "coverage_uncertain" in warnings or "semantic_uncertain" in warnings:
                    bucket = "uncertain_semcap"
                elif "weak_or_generic_api_leak" in warnings:
                    bucket = "uncertain_weak_api_leak"
                elif "ambiguous_service_leak" in warnings:
                    bucket = "uncertain_ambiguous_service_leak"
                else:
                    bucket = "dryrun_uncertain"
            elif (
                norm(row.get("v12_semantic_alignment_pred")) == "ok"
                and norm(row.get("v12_capability_coverage_pred")) == "coverage_ok"
                and norm(row.get("v12_capability_coverage_confidence")) == "high"
                and norm(row.get("v12_gold_set_integrity_status")) in {"ok", "not_applicable"}
                and not truthy(row.get("v12_generic_search_overtrust_flag"))
                and not has_domain_guard_violation(row)
                and not has_missing_core_requirement(row)
            ):
                decision = "dryrun_clean_candidate"
                bucket = "dryrun_clean_candidate"
                rules.append("keep_v14b_clean_candidate")
            else:
                decision = "dryrun_uncertain"
                bucket = "uncertain_semcap"
                warnings.append("v12_not_high_confidence_clean")

    old_decision = row.get("dryrun_decision", "")
    old_bucket = row.get("dryrun_bucket", "")
    if not old_decision:
        change = "no_v1_4_decision_available"
    elif old_decision == decision and old_bucket == bucket:
        change = "unchanged"
    else:
        change = f"{old_decision}:{old_bucket}->" + f"{decision}:{bucket}"
    out = dict(row)
    out.update(
        {
            "dryrun_decision_v1_4b": decision,
            "dryrun_bucket_v1_4b": bucket,
            "blocking_reasons_v1_4b": ";".join(sorted(set(blocking))),
            "warning_reasons_v1_4b": ";".join(sorted(set(warnings))),
            "triggered_rules_v1_4b": ";".join(sorted(set(rules))),
            "is_dryrun_clean_candidate_v1_4b": str(decision == "dryrun_clean_candidate").lower(),
            "is_dryrun_removed_v1_4b": str(decision == "dryrun_removed").lower(),
            "is_dryrun_uncertain_v1_4b": str(decision == "dryrun_uncertain").lower(),
            "is_dryrun_service_leak_only_v1_4b": str(decision == "dryrun_service_leak_only").lower(),
            "clean_confidence_bucket_v1_4b": "clean_candidate_high_conf" if decision == "dryrun_clean_candidate" else "",
            "requires_final_qa_v1_4b": "true",
            "v1_4b_change_from_v1_4": change,
        }
    )
    return out


def dangerous_flags_v14b(row: dict[str, Any]) -> list[str]:
    if row.get("dryrun_decision_v1_4b") != "dryrun_clean_candidate":
        return []
    flags: list[str] = []
    if norm(row.get("api_leak_detector_status")) == "api_leak_blocking" or norm(row.get("api_leak_strength")) == "strong":
        flags.append("blocking_api_leak_into_clean")
    if row.get("gold_in_candidate_services") != "yes" or row.get("gold_in_candidate_apis") not in {"yes", "yes_api_name_only"}:
        flags.append("gold_missing_into_clean")
    if norm(row.get("candidate_space_status")).startswith("invalid"):
        flags.append("no_choice_space_into_clean")
    if norm(row.get("v12_semantic_alignment_pred")) in {"mismatch", "uncertain"}:
        flags.append(f"semantic_{row.get('v12_semantic_alignment_pred')}_into_clean")
    if norm(row.get("v12_capability_coverage_pred")) in {"coverage_mismatch", "coverage_uncertain"}:
        flags.append(f"{row.get('v12_capability_coverage_pred')}_into_clean")
    if norm(row.get("service_leak_detector_status")) == "service_leak_only" and norm(row.get("prediction_level")) == "service":
        flags.append("service_level_service_leak_into_clean")
    if has_extra_gold_service(row):
        flags.append("wrong_gold_set_into_clean")
    if truthy(row.get("v12_generic_search_overtrust_flag")):
        flags.append("generic_search_overtrust_into_clean")
    if has_domain_guard_violation(row):
        flags.append("domain_specific_guard_violation_into_clean")
    if has_missing_core_requirement(row):
        flags.append("missing_core_requirement_into_clean")
    return sorted(set(flags))


def archive_v14b(root: Path) -> list[str]:
    archive_dir = root / "outputs" / "run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_full_clean_dryrun_v1_4b"
    ensure_dir(archive_dir)
    paths = [
        Path("scripts/validation/semcap_v1_2_tightening_utils.py"),
        Path("scripts/validation/full_clean_v1_4b_common.py"),
        Path("scripts/validation/check_v1_4b_inputs.py"),
        Path("scripts/validation/run_full_raw_semcap_v1_2_v1_4b.py"),
        Path("scripts/validation/check_v1_5c_failure_regression_v1_4b.py"),
        Path("scripts/validation/evaluate_semcap_v1_2_on_calibration_v1_4b.py"),
        Path("scripts/validation/apply_full_clean_dryrun_policy_v1_4b.py"),
        Path("scripts/validation/export_full_clean_dryrun_buckets_v1_4b.py"),
        Path("scripts/validation/build_full_clean_candidate_trace_v1_4b.py"),
        Path("scripts/validation/check_full_clean_dryrun_dangerous_errors_v1_4b.py"),
        Path("scripts/validation/run_dedup_precheck_v1_4b.py"),
        Path("scripts/validation/compare_v1_4_vs_v1_4b_clean_dryrun.py"),
        Path("scripts/validation/prepare_impacted_clean_candidate_qa_v1_4b.py"),
        Path("scripts/validation/summarize_full_clean_dryrun_v1_4b.py"),
        OUTPUT_DIR,
        DOC_DIR / "full_raw_semcap_v1_2_report_v1_4b.md",
        DOC_DIR / "v1_5c_failure_regression_report_v1_4b.md",
        DOC_DIR / "semcap_v1_2_calibration_eval_report_v1_4b.md",
        DOC_DIR / "full_clean_task_trace_report_v1_4b.md",
        DOC_DIR / "full_clean_candidate_trace_report_v1_4b.md",
        DOC_DIR / "full_clean_dryrun_bucket_report_v1_4b.md",
        DOC_DIR / "full_clean_dryrun_dangerous_error_report_v1_4b.md",
        DOC_DIR / "dedup_precheck_report_v1_4b.md",
        DOC_DIR / "v1_4_vs_v1_4b_diff_report.md",
        DOC_DIR / "impacted_clean_candidate_qa_frame_report_v1_4b.md",
        DOC_DIR / "full_clean_dryrun_summary_report_v1_4b.md",
        DOC_DIR / "full_clean_dryrun_v1_4b_go_no_go_report.md",
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
            "# Full Clean Dry-Run v1.4b Archive Manifest",
            "",
            f"Generated time: {now_text()}",
            "",
            "No final clean dataset, split, baseline, model training, or large-scale human review was generated.",
            "",
            "## Archived Files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )
    return copied


def ensure_v12_fields(fieldnames: list[str]) -> list[str]:
    out = list(fieldnames)
    for field in V12_FIELDS:
        if field not in out:
            out.append(field)
    return out
