from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from semcap_v1_1_common import archive_paths, ensure_dir, now_text, read_csv, value_counter, write_csv, write_json, write_md


DEFAULT_OUTPUT_DIR = Path("outputs/semcap_detector_v1_implementation_v1_1")
V0_8_POLICY = Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_policy_trace.csv")
V0_8_PREDICTIONS = DEFAULT_OUTPUT_DIR / "semcap_predictions_v0_8_sample_v1.csv"
EVAL_SUMMARY = DEFAULT_OUTPUT_DIR / "semcap_v1_eval_summary.json"
V4_2_POLICY = Path("docs/phase1/manual_audit_rule_v4_2_candidate.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply SemCap v1.1 predictions to v0.8 policy trace without full cleaning.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current working directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--v0-8-policy", type=Path, default=V0_8_POLICY, help="v0.8 policy trace CSV.")
    parser.add_argument("--v0-8-predictions", type=Path, default=V0_8_PREDICTIONS, help="SemCap v1 v0.8 predictions CSV.")
    parser.add_argument("--eval-summary", type=Path, default=EVAL_SUMMARY, help="SemCap v1 eval summary JSON.")
    parser.add_argument("--v4-2-policy", type=Path, default=V4_2_POLICY, help="v4.2 policy document.")
    parser.add_argument("--archive-dir", type=Path, default=None, help="Optional archive directory. Default uses current date.")
    return parser.parse_args()


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def decide_policy(row: Dict[str, str], pred: Dict[str, str]) -> Dict[str, str]:
    blocking: List[str] = []
    warnings: List[str] = []
    bucket = ""
    decision = ""

    sem_pred = pred.get("semantic_alignment_pred", "")
    cap_pred = pred.get("capability_coverage_pred", "")
    cap_conf = pred.get("capability_coverage_confidence", "")
    pred_level = (row.get("prediction_level") or row.get("task_type") or "").lower()

    if sem_pred == "mismatch":
        blocking.append("semantic_mismatch")
    if cap_pred == "coverage_mismatch":
        blocking.append("capability_coverage_mismatch")
    if cap_pred == "coverage_uncertain":
        warnings.append("capability_coverage_uncertain")
    if sem_pred == "uncertain":
        warnings.append("semantic_alignment_uncertain")

    if (row.get("candidate_space_status") or "").startswith("invalid_service_no_choice_space") or (
        "service" in pred_level and str(row.get("candidate_service_count")) == "1"
    ):
        blocking.append("service_level_no_real_choice_space")
    if "strong" in (row.get("api_leak_strength") or "").lower() or "strong" in (row.get("api_leak_detector_status") or "").lower():
        blocking.append("strong_api_leak")
    if (row.get("service_leak_detector_status") or "").lower() == "service_leak_only" and "service" in pred_level:
        warnings.append("service_leak_only_service_level")
    if (row.get("task_type_eligibility_status") or "").lower().startswith("invalid"):
        blocking.append("task_type_invalid")

    blocking = sorted(set(blocking))
    warnings = sorted(set(warnings))

    if blocking:
        decision = "policy_remove"
        if "capability_coverage_mismatch" in blocking:
            bucket = "remove_semcap_coverage_mismatch"
        elif "semantic_mismatch" in blocking:
            bucket = "remove_semcap_semantic_mismatch"
        elif "strong_api_leak" in blocking:
            bucket = "remove_api_leak"
        elif "service_level_no_real_choice_space" in blocking:
            bucket = "remove_service_choice_space_invalid"
        else:
            bucket = "remove_policy_blocked"
    elif warnings:
        decision = "policy_uncertain"
        if "capability_coverage_uncertain" in warnings or "semantic_alignment_uncertain" in warnings:
            bucket = "uncertain_semcap_needs_review"
        elif "service_leak_only_service_level" in warnings:
            bucket = "service_leak_only_needs_separate_bucket"
        else:
            bucket = "uncertain_policy_warning"
    else:
        decision = "policy_keep_candidate"
        bucket = "semcap_and_policy_pass_candidate"

    semcap_summary = (
        f"semantic={sem_pred}:{pred.get('semantic_alignment_confidence', '')}; "
        f"capability={cap_pred}:{cap_conf}; "
        f"coverage_ok_but_policy_blocked_candidate={pred.get('coverage_ok_but_policy_blocked_candidate', '')}"
    )
    return {
        "policy_decision_v1": decision,
        "policy_bucket_v1": bucket,
        "blocking_reasons_v1": ";".join(blocking),
        "warning_reasons_v1": ";".join(warnings),
        "semcap_decision_summary": semcap_summary,
        "can_enter_clean_ready_candidate": "true" if decision == "policy_keep_candidate" else "false",
        "requires_human_review_v1": "true" if decision != "policy_keep_candidate" or pred.get("requires_human_review") == "true" else "false",
    }


def parse_percent(value: object) -> float:
    text = str(value or "").strip()
    if text == "n/a" or not text:
        return 0.0
    if text.endswith("%"):
        try:
            return float(text[:-1]) / 100.0
        except Exception:
            return 0.0
    try:
        return float(text)
    except Exception:
        return 0.0


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    import json

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def make_trace_report(output_path: Path, trace_rows: List[Dict[str, str]], input_paths: List[Path]) -> None:
    keep = sum(1 for row in trace_rows if row.get("policy_decision_v1") == "policy_keep_candidate")
    remove = sum(1 for row in trace_rows if row.get("policy_decision_v1") == "policy_remove")
    uncertain = sum(1 for row in trace_rows if row.get("policy_decision_v1") == "policy_uncertain")
    coverage_ok_policy_blocked = sum(1 for row in trace_rows if "coverage_ok_but_policy_blocked_candidate=true" in row.get("semcap_decision_summary", "") and row.get("policy_decision_v1") != "policy_keep_candidate")
    coverage_mismatch_into_keep = sum(1 for row in trace_rows if "capability=coverage_mismatch" in row.get("semcap_decision_summary", "") and row.get("policy_decision_v1") == "policy_keep_candidate")
    strong_api_leak_into_keep = sum(1 for row in trace_rows if "strong_api_leak" in row.get("blocking_reasons_v1", "") and row.get("policy_decision_v1") == "policy_keep_candidate")
    service_no_choice_into_keep = sum(1 for row in trace_rows if "service_level_no_real_choice_space" in row.get("blocking_reasons_v1", "") and row.get("policy_decision_v1") == "policy_keep_candidate")

    lines = [
        "# v0.8 Sample Policy Trace With SemCap v1.1 Report",
        "",
        f"Generated time: {now_text()}",
        "",
        "Input files:",
        *[f"- `{path}`" for path in input_paths],
        "",
        f"Sample count: {len(trace_rows)}",
        "",
        "Scope: policy trace only. This is not a final clean dataset. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Policy decision distribution",
        "",
        f"- policy_keep count: {keep}",
        f"- policy_remove count: {remove}",
        f"- policy_uncertain count: {uncertain}",
        "",
        "## Safety checks",
        "",
        f"- coverage_ok_but_policy_blocked count: {coverage_ok_policy_blocked}",
        f"- dangerous false keep count proxy: {coverage_mismatch_into_keep}",
        f"- strong API leak into keep count: {strong_api_leak_into_keep}",
        f"- coverage mismatch into keep count: {coverage_mismatch_into_keep}",
        f"- service-level no-choice into keep count: {service_no_choice_into_keep}",
        "",
        "## Bucket distribution",
        "",
    ]
    for key, count in value_counter(trace_rows, "policy_bucket_v1").items():
        lines.append(f"- {key}: {count}")
    lines.extend(
        [
            "",
            "## Important boundary",
            "",
            "coverage_ok + semantic ok only means SemCap passed. v4.2 policy gates still block no-choice, strong API leak, service leak only, and invalid task-type cases.",
        ]
    )
    write_md(output_path, lines)


def make_go_no_go(output_path: Path, eval_summary: dict, trace_rows: List[Dict[str, str]], input_paths: List[Path]) -> dict:
    combined = eval_summary.get("combined", {})
    dangerous = int(combined.get("dangerous_false_keep") or 0)
    mismatch_capture = parse_percent(combined.get("coverage_mismatch_capture"))
    high_ok_precision = parse_percent(combined.get("high_confidence_coverage_ok_precision_like"))
    coverage_ok_recall = parse_percent(combined.get("coverage_ok_recall"))
    v1_vs_v09 = eval_summary.get("v1_vs_v0_9", {})
    v1_cap = parse_percent(v1_vs_v09.get("v1_capability_agreement"))
    v09_cap = parse_percent(v1_vs_v09.get("v0_9_capability_agreement"))
    can_small = dangerous == 0 and mismatch_capture >= 0.9 and high_ok_precision >= 0.85 and v1_cap >= v09_cap
    result = {
        "can_use_semcap_v1_for_full_cleaning_now": False,
        "can_run_small_cleaning_dryrun_with_semcap_v1": bool(can_small),
        "can_run_full_cleaning_now": False,
        "can_create_split_now": False,
        "can_run_paper_baseline_now": False,
        "recommended_next_step": "Run a small cleaning dry-run only if user approves; otherwise inspect v1 disagreement cases and improve v1.2.",
    }
    lines = [
        "# SemCap Detector v1.1 Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        "",
        "Input files:",
        *[f"- `{path}`" for path in input_paths],
        "",
        f"Calibration sample count: {eval_summary.get('input_rows', 'unknown')}",
        f"v0.8 trace sample count: {len(trace_rows)}",
        "",
        "## What SemCap80 human labels show",
        "",
        "SemCap80 shows that capability coverage and final cleaning policy must be separated. Many coverage_ok samples are still removed or uncertain because of no-choice candidate space, leakage, or invalid task type.",
        "",
        "## v0.9 detector main problem",
        "",
        "v0.9 was safe as a review router but too conservative and weak at recognizing coverage_ok.",
        "",
        "## v1.1 metric signals",
        "",
        f"- dangerous_false_keep: {dangerous}",
        f"- coverage_mismatch_capture: {combined.get('coverage_mismatch_capture')}",
        f"- high_confidence_coverage_ok_precision_like: {combined.get('high_confidence_coverage_ok_precision_like')}",
        f"- coverage_ok_recall: {combined.get('coverage_ok_recall')}",
        f"- v0.9 capability agreement: {v1_vs_v09.get('v0_9_capability_agreement')}",
        f"- v1.1 capability agreement: {v1_vs_v09.get('v1_capability_agreement')}",
        "",
        "## Go / No-Go Decision v1.1",
        "",
    ]
    for key, value in result.items():
        lines.append(f"{key}: {str(value).lower() if isinstance(value, bool) else value}")
    lines.extend(
        [
            "",
            "## Answers",
            "",
            "1. SemCap80 labels show coverage_ok does not imply clean-ready.",
            "2. v0.9's largest problem is over-conservative capability coverage recognition.",
            "3. v1.1 separates SemCap from final policy using coverage_ok_but_policy_blocked_candidate.",
            "4. v1.1 should reduce some coverage_ok underestimation, but evaluation metrics decide whether it is sufficient.",
            f"5. dangerous false keep count is {dangerous}.",
            "6. v1.1 cannot be used directly for full cleaning.",
            "7. Current full cleaning status: false.",
            "8. Current split status: false.",
            "9. Current baseline status: false.",
            "10. Next step is user-approved small cleaning dry-run or v1.2 disagreement analysis.",
        ]
    )
    write_md(output_path, lines)
    return result


def archive_run(root: Path, output_dir: Path, archive_dir: Path) -> List[str]:
    paths = [
        Path("scripts/validation/check_semcap_v1_1_inputs.py"),
        Path("scripts/validation/build_combined_semcap_calibration_v1_1.py"),
        Path("scripts/validation/run_semcap_heuristic_detector_v1_1.py"),
        Path("scripts/validation/evaluate_semcap_detector_v1_1.py"),
        Path("scripts/validation/apply_semcap_v1_to_v0_8_policy_trace_v1_1.py"),
        Path("scripts/validation/semcap_v1_1_common.py"),
        output_dir,
        Path("docs/phase1/combined_semcap_calibration_report_v1_1.md"),
        Path("docs/phase1/semcap_detector_v1_failure_taxonomy.md"),
        Path("docs/phase1/semantic_capability_detector_v1_rule_candidate.md"),
        Path("docs/phase1/semcap_heuristic_detector_v1_1_report.md"),
        Path("docs/phase1/semcap_detector_v1_1_eval_report.md"),
        Path("docs/phase1/v0_8_sample_policy_trace_with_semcap_v1_1_report.md"),
        Path("docs/phase1/semcap_detector_v1_1_go_no_go_report.md"),
    ]
    copied = archive_paths(archive_dir, [root / p if not p.is_absolute() else p for p in paths], root)
    manifest = [
        "# SemCap Detector v1.1 Archive Manifest",
        "",
        f"Generated time: {now_text()}",
        f"Archive directory: `{archive_dir}`",
        "",
        "No full cleaning, split, baseline, or model training was run.",
        "",
        "## Archived files",
        "",
    ]
    manifest.extend(f"- `{item}`" for item in copied)
    write_md(archive_dir / "ARCHIVE_MANIFEST.md", manifest)
    return copied


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    policy_rows = read_csv(root / args.v0_8_policy)
    pred_rows = read_csv(root / args.v0_8_predictions)
    pred_by_record = {row.get("record_id", ""): row for row in pred_rows}

    trace_rows: List[Dict[str, str]] = []
    for row in policy_rows:
        pred = pred_by_record.get(row.get("v0_8_sample_id", ""), {})
        merged = dict(row)
        merged.update(
            {
                "v1_semantic_alignment_pred": pred.get("semantic_alignment_pred", ""),
                "v1_semantic_alignment_confidence": pred.get("semantic_alignment_confidence", ""),
                "v1_capability_coverage_pred": pred.get("capability_coverage_pred", ""),
                "v1_capability_coverage_confidence": pred.get("capability_coverage_confidence", ""),
                "v1_capability_coverage_reason": pred.get("capability_coverage_reason", ""),
                "v1_coverage_ok_but_policy_blocked_candidate": pred.get("coverage_ok_but_policy_blocked_candidate", ""),
            }
        )
        merged.update(decide_policy(row, pred))
        trace_rows.append(merged)

    trace_path = output_dir / "v0_8_sample_policy_trace_with_semcap_v1.csv"
    write_csv(trace_path, trace_rows, list(trace_rows[0].keys()) if trace_rows else [])

    make_trace_report(
        root / "docs/phase1/v0_8_sample_policy_trace_with_semcap_v1_1_report.md",
        trace_rows,
        [args.v0_8_policy, args.v0_8_predictions, args.v4_2_policy],
    )

    eval_summary = load_json(root / args.eval_summary)
    go_no_go = make_go_no_go(
        root / "docs/phase1/semcap_detector_v1_1_go_no_go_report.md",
        eval_summary,
        trace_rows,
        [args.eval_summary, args.v0_8_policy, args.v0_8_predictions, args.v4_2_policy],
    )

    summary = {
        "generated_time": now_text(),
        "v0_8_rows": len(trace_rows),
        "policy_decision_v1_distribution": value_counter(trace_rows, "policy_decision_v1"),
        "policy_bucket_v1_distribution": value_counter(trace_rows, "policy_bucket_v1"),
        "go_no_go": go_no_go,
    }
    write_json(output_dir / "v0_8_sample_policy_trace_with_semcap_v1_summary.json", summary)

    archive_dir = args.archive_dir
    if archive_dir is None:
        archive_dir = Path("outputs/run_archives") / f"{datetime.now().strftime('%Y-%m-%d')}_semcap_detector_v1_implementation_v1_1"
    copied = archive_run(root, output_dir, root / archive_dir)

    print(f"Wrote {trace_path}")
    print("policy decision distribution:", value_counter(trace_rows, "policy_decision_v1"))
    print("Go / No-Go:", go_no_go)
    print(f"Archived files: {len(copied)}")
    print("No full cleaning, split, baseline, or model training was run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
