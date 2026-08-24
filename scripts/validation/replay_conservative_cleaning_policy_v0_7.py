"""Replay conservative v4.2 cleaning policy on audited data only.

This script compares policy trace decisions with human final decisions for
manual40, Round2, and Round3. It never runs full cleaning and never creates a
final clean dataset.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from cleaning_policy_v0_7_utils import (
    AUDIT_EVIDENCE_PATH,
    DOCS_DIR,
    EVIDENCE_COLUMNS,
    OUTPUT_DIR,
    apply_v42_policy,
    ensure_dirs,
    markdown_table,
    now_str,
    pct,
    read_csv,
    summarize_policy_trace,
    write_csv,
    write_json,
)


TRACE_COLUMNS = [
    *EVIDENCE_COLUMNS,
    "cleaning_decision",
    "cleaning_bucket",
    "blocking_reasons",
    "warning_reasons",
    "triggered_rules",
    "detector_status",
    "requires_human_or_llm_review",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay v4.2 conservative cleaning policy on audited evidence table only."
    )
    parser.add_argument("--input", type=Path, default=AUDIT_EVIDENCE_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("outputs/run_archives/2026-06-28_cleaning_policy_validation_v0_7"),
    )
    return parser


def as_int(value: object) -> int | None:
    try:
        text = str(value or "").strip()
        return int(float(text)) if text else None
    except ValueError:
        return None


def decision_counts(rows: Sequence[Dict[str, object]], key: str) -> Dict[str, int]:
    return dict(sorted(Counter(str(row.get(key, "not_available")) for row in rows).items()))


def policy_summary(rows: Sequence[Dict[str, object]], group_key: str) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(group_key, "not_available"))].append(row)
    out: List[Dict[str, object]] = []
    for group, subset in sorted(grouped.items()):
        total = len(subset)
        keep_rows = [r for r in subset if r.get("cleaning_decision") == "keep_for_cleaning_candidate"]
        remove_rows = [r for r in subset if r.get("cleaning_decision") == "remove"]
        uncertain_rows = [r for r in subset if r.get("cleaning_decision") == "uncertain"]
        agreement = sum(1 for r in subset if r.get("cleaning_decision") == r.get("manual_final_decision"))
        keep_human_keep = sum(1 for r in keep_rows if r.get("manual_final_decision") == "keep_for_cleaning_candidate")
        remove_human_remove = sum(1 for r in remove_rows if r.get("manual_final_decision") == "remove")
        out.append(
            {
                group_key: group,
                "rows": total,
                "policy_keep": len(keep_rows),
                "policy_remove": len(remove_rows),
                "policy_uncertain": len(uncertain_rows),
                "human_keep": sum(1 for r in subset if r.get("manual_final_decision") == "keep_for_cleaning_candidate"),
                "human_remove": sum(1 for r in subset if r.get("manual_final_decision") == "remove"),
                "human_uncertain": sum(1 for r in subset if r.get("manual_final_decision") == "uncertain"),
                "agreement_count": agreement,
                "agreement_rate": pct(agreement, total),
                "policy_keep_human_keep_rate": pct(keep_human_keep, len(keep_rows)),
                "policy_remove_human_remove_rate": pct(remove_human_remove, len(remove_rows)),
            }
        )
    return out


def metric_bundle(trace_rows: Sequence[Dict[str, object]]) -> Dict[str, object]:
    total = len(trace_rows)
    agree = sum(1 for r in trace_rows if r.get("cleaning_decision") == r.get("manual_final_decision"))
    keep_rows = [r for r in trace_rows if r.get("cleaning_decision") == "keep_for_cleaning_candidate"]
    remove_rows = [r for r in trace_rows if r.get("cleaning_decision") == "remove"]
    uncertain_rows = [r for r in trace_rows if r.get("cleaning_decision") == "uncertain"]
    policy_keep_human_keep = sum(1 for r in keep_rows if r.get("manual_final_decision") == "keep_for_cleaning_candidate")
    policy_remove_human_remove = sum(1 for r in remove_rows if r.get("manual_final_decision") == "remove")
    strong_api_leak_kept = [
        r for r in trace_rows if r.get("leakage_check") == "api_leak_blocking" and r.get("cleaning_decision") == "keep_for_cleaning_candidate"
    ]
    coverage_mismatch_kept = [
        r for r in trace_rows if r.get("capability_coverage_check") == "coverage_mismatch" and r.get("cleaning_decision") == "keep_for_cleaning_candidate"
    ]
    semantic_mismatch_kept = [
        r for r in trace_rows if r.get("semantic_alignment_check") == "mismatch" and r.get("cleaning_decision") == "keep_for_cleaning_candidate"
    ]
    api_single_service_legal_false_remove = [
        r
        for r in trace_rows
        if "api" in str(r.get("task_type", "")).lower()
        and as_int(r.get("candidate_service_count")) == 1
        and r.get("manual_final_decision") == "keep_for_cleaning_candidate"
        and r.get("cleaning_decision") == "remove"
    ]
    generic_weak_over_remove = [
        r
        for r in trace_rows
        if r.get("risk_category") == "generic_weak_leak_false_positive_risk"
        and r.get("manual_final_decision") == "keep_for_cleaning_candidate"
        and r.get("cleaning_decision") == "remove"
    ]
    rule_keep_blocked = [
        r
        for r in trace_rows
        if r.get("risk_category") == "rule_keep_candidate"
        and r.get("manual_final_decision") in {"remove", "uncertain"}
        and r.get("cleaning_decision") != "keep_for_cleaning_candidate"
    ]
    return {
        "total_rows": total,
        "overall_agreement_count": agree,
        "overall_agreement_rate": pct(agree, total),
        "policy_decision_distribution": decision_counts(trace_rows, "cleaning_decision"),
        "human_decision_distribution": decision_counts(trace_rows, "manual_final_decision"),
        "policy_keep_rows": len(keep_rows),
        "policy_keep_human_keep_count": policy_keep_human_keep,
        "policy_keep_human_keep_rate": pct(policy_keep_human_keep, len(keep_rows)),
        "policy_remove_rows": len(remove_rows),
        "policy_remove_human_remove_count": policy_remove_human_remove,
        "policy_remove_human_remove_rate": pct(policy_remove_human_remove, len(remove_rows)),
        "policy_uncertain_rows": len(uncertain_rows),
        "policy_uncertain_human_distribution": decision_counts(uncertain_rows, "manual_final_decision"),
        "strong_api_leak_kept_count": len(strong_api_leak_kept),
        "coverage_mismatch_kept_count": len(coverage_mismatch_kept),
        "semantic_mismatch_kept_count": len(semantic_mismatch_kept),
        "api_level_single_service_legal_false_remove_count": len(api_single_service_legal_false_remove),
        "generic_weak_leak_over_remove_count": len(generic_weak_over_remove),
        "rule_keep_candidate_remove_uncertain_blocked_count": len(rule_keep_blocked),
        "thresholds": {
            "policy_keep_human_keep_rate_target": ">=90%",
            "policy_remove_human_remove_rate_target": ">=85%",
            "strong_api_leak_kept_target": 0,
            "coverage_mismatch_kept_target": 0,
            "semantic_mismatch_kept_target": 0,
        },
    }


def passes_keep_threshold(metrics: Dict[str, object]) -> bool:
    keep_rows = int(metrics["policy_keep_rows"])
    if keep_rows == 0:
        return False
    return int(metrics["policy_keep_human_keep_count"]) / keep_rows >= 0.9


def passes_remove_threshold(metrics: Dict[str, object]) -> bool:
    remove_rows = int(metrics["policy_remove_rows"])
    if remove_rows == 0:
        return False
    return int(metrics["policy_remove_human_remove_count"]) / remove_rows >= 0.85


def write_v42_policy_doc(path: Path, metrics: Dict[str, object]) -> None:
    lines = [
        "# Manual Audit Rule v4.2 Candidate",
        "",
        f"Generated time: {now_str()}",
        "Input evidence: `outputs/cleaning_policy_validation_v0_7/audit_evidence_table_manual40_round2_round3.csv`",
        f"Sample count: {metrics['total_rows']}",
        "",
        "Scope: v4.2 is a cleaning policy candidate validated only on manual40 + Round2 + Round3 audited data. It is not a full-cleaning result.",
        "",
        "## Clean-Ready Gates",
        "",
        "A sample can be clean-ready only when all of the following hold:",
        "",
        "1. no blocking API leak",
        "2. `semantic_alignment_check = ok`",
        "3. `capability_coverage_check = coverage_ok`",
        "4. candidate choice space is valid for the prediction level",
        "5. `task_type_check` is valid for the target task",
        "6. gold services/APIs are present in the candidate sets",
        "",
        "## Remove Conditions",
        "",
        "1. strong API leak / exact endpoint identity leak",
        "2. `capability_coverage_check = coverage_mismatch`",
        "3. `semantic_alignment_check = mismatch`",
        "4. fatal candidate space invalid",
        "5. gold service/API not in candidates",
        "6. service-level task with `candidate_service_count <= gold_service_count`",
        "7. API-level task with `candidate_api_count <= gold_api_count`",
        "",
        "## Uncertain Conditions",
        "",
        "1. weak or ambiguous API leak",
        "2. service leak only for service-level clean discovery",
        "3. `capability_coverage_check = coverage_uncertain` or missing",
        "4. `semantic_alignment_check = uncertain` or missing",
        "5. unresolved task type boundary",
        "6. detector cannot decide automatically",
        "",
        "## API-Level Single-Service Policy",
        "",
        "For API-level tasks, `candidate_service_count = 1` is not fatal. The task may be clean-ready if `candidate_api_count > gold_api_count`, capability coverage is ok, semantic alignment is ok, and gold APIs are in the candidate set.",
        "",
        "For service-level discovery, `candidate_service_count <= gold_service_count` is not clean-ready because there is no real service choice space.",
        "",
        "## API Leak Policy",
        "",
        "Strong endpoint leak is blocking when an exact endpoint path, carrier-specific endpoint identity, or task-flow endpoint identity appears in the query.",
        "",
        "Weak/generic mentions such as `search`, `status`, `summary`, `detail`, `count`, `latest`, `health`, `places`, `image`, `news`, or `subtitle format` are not automatically blocking; they should enter uncertain/review unless there is endpoint-specific evidence.",
        "",
        "Service name mention is treated as `service_leak_only`: it is not clean service-level discovery, but it may still be usable for API-level recommendation when API choice space is valid.",
        "",
        "## Capability Coverage Policy",
        "",
        "No leak is not enough. Gold services/APIs must cover the core user request.",
        "",
        "Round3 evidence: `capability_coverage_risk` had 11 remove out of 20; `rule_keep_candidate` had 5 remove and 1 uncertain out of 20, mostly because capability coverage was not guaranteed.",
        "",
        "## Detector Status",
        "",
        "| Detector | Status |",
        "|---|---|",
        "| candidate_space_validator | deterministic |",
        "| task_type_eligibility_validator | deterministic |",
        "| gold_in_candidate_validator | deterministic |",
        "| api_leak_detector | heuristic + needs review for weak/generic cases |",
        "| service_leak_detector | heuristic |",
        "| semantic_alignment_detector | not fully automatic |",
        "| capability_coverage_detector | not fully automatic |",
        "",
        "## v0.7 Replay Notes",
        "",
        f"- policy_keep human keep rate: {metrics['policy_keep_human_keep_rate']}",
        f"- policy_remove human remove rate: {metrics['policy_remove_human_remove_rate']}",
        f"- strong API leak kept count: {metrics['strong_api_leak_kept_count']}",
        f"- coverage mismatch kept count: {metrics['coverage_mismatch_kept_count']}",
        f"- semantic mismatch kept count: {metrics['semantic_mismatch_kept_count']}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_replay_report(
    path: Path,
    input_path: Path,
    trace_rows: Sequence[Dict[str, object]],
    by_round: List[Dict[str, object]],
    by_risk: List[Dict[str, object]],
    metrics: Dict[str, object],
) -> None:
    lines: List[str] = [
        "# Conservative Cleaning Policy Replay Report v0.7",
        "",
        f"Generated time: {now_str()}",
        f"Input file: `{input_path}`",
        f"Sample count: {len(trace_rows)}",
        "",
        "Scope: policy replay was run only on manual40 + Round2 + Round3 audited rows. No full cleaning, split, baseline, or model training was run.",
        "",
        "Manual final decisions are used only as evaluation labels, not as policy-rule inputs.",
        "",
        "## Overall Metrics",
        "",
        f"- overall agreement with human final: {metrics['overall_agreement_count']}/{metrics['total_rows']} ({metrics['overall_agreement_rate']})",
        f"- policy_keep human keep rate: {metrics['policy_keep_human_keep_count']}/{metrics['policy_keep_rows']} ({metrics['policy_keep_human_keep_rate']})",
        f"- policy_remove human remove rate: {metrics['policy_remove_human_remove_count']}/{metrics['policy_remove_rows']} ({metrics['policy_remove_human_remove_rate']})",
        f"- policy_uncertain rows: {metrics['policy_uncertain_rows']}",
        "",
        "## Safety Leakage Checks",
        "",
        f"- strong API leak leaking into keep: {metrics['strong_api_leak_kept_count']}",
        f"- coverage mismatch leaking into keep: {metrics['coverage_mismatch_kept_count']}",
        f"- semantic mismatch leaking into keep: {metrics['semantic_mismatch_kept_count']}",
        f"- API-level single-service legal false removals: {metrics['api_level_single_service_legal_false_remove_count']}",
        f"- generic weak leak over-removal count: {metrics['generic_weak_leak_over_remove_count']}",
        f"- rule_keep_candidate remove/uncertain blocked by v4.2: {metrics['rule_keep_candidate_remove_uncertain_blocked_count']}",
        "",
        "## Summary By Audit Round",
        "",
    ]
    lines.extend(
        markdown_table(
            by_round,
            [
                "audit_round",
                "rows",
                "policy_keep",
                "policy_remove",
                "policy_uncertain",
                "human_keep",
                "human_remove",
                "human_uncertain",
                "agreement_rate",
                "policy_keep_human_keep_rate",
                "policy_remove_human_remove_rate",
            ],
            max_rows=20,
        )
    )
    lines.extend(["", "## Summary By Risk Category", ""])
    lines.extend(
        markdown_table(
            by_risk,
            [
                "risk_category",
                "rows",
                "policy_keep",
                "policy_remove",
                "policy_uncertain",
                "human_keep",
                "human_remove",
                "human_uncertain",
                "agreement_rate",
                "policy_keep_human_keep_rate",
                "policy_remove_human_remove_rate",
            ],
            max_rows=80,
        )
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The conservative replay intentionally fails closed when semantic or capability coverage fields are missing. This is why older rounds may be routed to uncertain rather than keep.",
            "",
            "The key pass/fail checks for moving beyond v0.7 are not simple overall agreement. The important safety checks are whether strong API leak, coverage mismatch, or semantic mismatch can leak into keep, and whether API-level single-service samples are mistakenly removed by service-count logic.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_detector_matrix(path: Path, input_path: Path, row_count: int) -> None:
    rows = [
        {
            "Detector": "candidate_space_validator",
            "Input": "candidate/gold service and API counts",
            "Output": "choice-space pass/fail",
            "Type": "deterministic",
            "Currently automatic": "yes",
            "Risk": "count parsing can fail if JSON schema changes",
            "v0.7 conclusion": "usable as a gate",
        },
        {
            "Detector": "gold_in_candidate_validator",
            "Input": "candidate_services_json/candidate_apis_json/gold JSON",
            "Output": "gold present yes/no/unknown",
            "Type": "deterministic parser",
            "Currently automatic": "partial",
            "Risk": "name normalization can cause false no",
            "v0.7 conclusion": "use, but inspect no/unknown rates",
        },
        {
            "Detector": "task_type_eligibility_validator",
            "Input": "task_type and task_type_check",
            "Output": "service/API eligibility",
            "Type": "deterministic with audited labels",
            "Currently automatic": "partial",
            "Risk": "raw task_type alone is not enough",
            "v0.7 conclusion": "needs audited or generated task_type_check",
        },
        {
            "Detector": "api_leak_detector",
            "Input": "query and gold API names",
            "Output": "blocking/ambiguous/no leak",
            "Type": "heuristic",
            "Currently automatic": "partial",
            "Risk": "generic words can be false positives",
            "v0.7 conclusion": "strong leaks remove; weak/generic -> review",
        },
        {
            "Detector": "service_leak_detector",
            "Input": "query and gold service names",
            "Output": "service_leak_only/no leak",
            "Type": "heuristic",
            "Currently automatic": "partial",
            "Risk": "brand vs generic service names",
            "v0.7 conclusion": "service-level not clean; API-level may remain",
        },
        {
            "Detector": "semantic_alignment_detector",
            "Input": "query, gold services/APIs, candidates",
            "Output": "ok/uncertain/mismatch",
            "Type": "semantic judgment",
            "Currently automatic": "no",
            "Risk": "cannot pretend full reliability",
            "v0.7 conclusion": "not fully automatic",
        },
        {
            "Detector": "capability_coverage_detector",
            "Input": "query and gold capability descriptions",
            "Output": "coverage_ok/uncertain/mismatch",
            "Type": "semantic/capability judgment",
            "Currently automatic": "no",
            "Risk": "major false keep source if absent",
            "v0.7 conclusion": "must be a gate; not fully automatic",
        },
        {
            "Detector": "dedup_detector",
            "Input": "task_id/query/gold/candidate signatures",
            "Output": "duplicate groups",
            "Type": "deterministic + fuzzy optional",
            "Currently automatic": "not validated in v0.7",
            "Risk": "near-duplicate queries",
            "v0.7 conclusion": "defer to later dataset QA",
        },
        {
            "Detector": "composable_dependency_detector",
            "Input": "query and service/API chain",
            "Output": "strong_composable/ordinary_multi/ambiguous",
            "Type": "semantic dependency judgment",
            "Currently automatic": "no",
            "Risk": "G3 raw group overstates composability",
            "v0.7 conclusion": "requires dependency screening + review",
        },
    ]
    lines = [
        "# Automatic Detector Readiness Matrix v0.7",
        "",
        f"Generated time: {now_str()}",
        f"Input file: `{input_path}`",
        f"Sample count: {row_count}",
        "",
        "Scope: detector readiness is assessed for conservative policy validation only. No full cleaning, split, baseline, or model training was run.",
        "",
    ]
    lines.extend(
        markdown_table(
            rows,
            ["Detector", "Input", "Output", "Type", "Currently automatic", "Risk", "v0.7 conclusion"],
            max_rows=20,
        )
    )
    lines.extend(
        [
            "",
            "Important: `semantic_alignment_detector` and `capability_coverage_detector` currently cannot be treated as fully automatic.",
            "",
            "The v0.7 conservative cleaning skeleton can process already-audited fields. For full raw data, semantic/capability detectors must be implemented or connected first; otherwise many samples should go to uncertain/review.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_go_no_go(path: Path, input_path: Path, metrics: Dict[str, object], can_freeze: bool) -> None:
    lines = [
        "# Cleaning Policy v0.7 Go / No-Go Report",
        "",
        f"Generated time: {now_str()}",
        f"Input file: `{input_path}`",
        f"Sample count: {metrics['total_rows']}",
        "",
        "Scope: this decision is based only on manual40 + Round2 + Round3 audited policy replay. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Answers",
        "",
        "1. Round3 does not support old `rule_keep` directly entering clean-ready.",
        "2. Round3 supports the API-level single-service policy: `candidate_service_count = 1` is not fatal for API-level tasks.",
        "3. Round3 proves a capability coverage gate is necessary: no leak is not enough.",
        "4. It is appropriate to write a conservative cleaning script skeleton.",
        "5. It is not appropriate to run full cleaning now.",
        "6. It is not appropriate to create train/dev/test split now.",
        "7. It is not appropriate to run a paper baseline now.",
        "8. Next step should be a small full-pipeline dry-run with conservative policy trace only, after reviewing v0.7 replay results.",
        "",
        "## Replay Metrics Used",
        "",
        f"- policy_keep human keep rate: {metrics['policy_keep_human_keep_rate']}",
        f"- policy_remove human remove rate: {metrics['policy_remove_human_remove_rate']}",
        f"- strong API leak kept count: {metrics['strong_api_leak_kept_count']}",
        f"- coverage mismatch kept count: {metrics['coverage_mismatch_kept_count']}",
        f"- semantic mismatch kept count: {metrics['semantic_mismatch_kept_count']}",
        f"- API-level single-service legal false removals: {metrics['api_level_single_service_legal_false_remove_count']}",
        "",
        "## Go / No-Go Decision v0.7",
        "",
        f"can_freeze_v4_2_policy_candidate: {str(can_freeze).lower()}",
        "can_write_conservative_cleaning_script_skeleton: true",
        "can_run_policy_replay_on_audited_data: true",
        "can_run_full_cleaning_now: false",
        "can_create_split_now: false",
        "can_run_paper_baseline_now: false",
        "",
        "recommended_next_step:",
        "run small full-pipeline dry-run with conservative policy trace only; do not emit a final clean dataset yet.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def archive_results(archive_dir: Path, output_dir: Path, docs_dir: Path) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    script_paths = [
        Path("scripts/validation/check_cleaning_policy_v0_7_inputs.py"),
        Path("scripts/validation/analyze_round3_reviewed_v0_7.py"),
        Path("scripts/validation/build_audit_evidence_table_v0_7.py"),
        Path("scripts/cleaning/apply_conservative_cleaning_policy_v0_7.py"),
        Path("scripts/validation/replay_conservative_cleaning_policy_v0_7.py"),
    ]
    for src in script_paths:
        dst = archive_dir / src
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(str(dst))

    outputs_dest = archive_dir / "outputs_cleaning_policy_validation_v0_7"
    shutil.copytree(output_dir, outputs_dest, dirs_exist_ok=True)
    copied.append(str(outputs_dest))

    docs_to_copy = [
        docs_dir / "round3_reviewed_deep_analysis_v0_7.md",
        docs_dir / "audit_evidence_table_report_v0_7.md",
        docs_dir / "manual_audit_rule_v4_2_candidate.md",
        docs_dir / "conservative_cleaning_policy_replay_report_v0_7.md",
        docs_dir / "automatic_detector_readiness_matrix_v0_7.md",
        docs_dir / "cleaning_policy_v0_7_go_no_go_report.md",
    ]
    docs_dest = archive_dir / "docs_phase1"
    for src in docs_to_copy:
        if src.exists():
            dst = docs_dest / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(dst))

    manifest = archive_dir / "ARCHIVE_MANIFEST.md"
    lines = [
        "# Archive Manifest: cleaning_policy_validation_v0_7",
        "",
        f"Generated time: {now_str()}",
        f"Archive directory: `{archive_dir}`",
        "",
        "Scope: archived v0.7 policy-validation scripts, reports, and trace outputs only.",
        "",
        "No full cleaning, split, baseline, or model training was run.",
        "",
        "## Files",
        "",
    ]
    for item in copied:
        lines.append(f"- `{item}`")
    manifest.write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.docs_dir.mkdir(parents=True, exist_ok=True)
    if not args.input.exists():
        print(f"ERROR: evidence table is missing: {args.input}")
        print("Run scripts/validation/build_audit_evidence_table_v0_7.py first.")
        return 1

    _, evidence_rows = read_csv(args.input)
    trace_rows = [apply_v42_policy(row) for row in evidence_rows]

    trace_path = args.output_dir / "policy_replay_trace_all_rounds.csv"
    write_csv(trace_path, trace_rows, TRACE_COLUMNS)
    for audit_round in ["manual40", "round2", "round3"]:
        subset = [row for row in trace_rows if row.get("audit_round") == audit_round]
        write_csv(args.output_dir / f"conservative_policy_trace_{audit_round}.csv", subset, TRACE_COLUMNS)

    by_round = policy_summary(trace_rows, "audit_round")
    by_risk = policy_summary(trace_rows, "risk_category")
    write_csv(args.output_dir / "policy_replay_summary_by_round.csv", by_round)
    write_csv(args.output_dir / "policy_replay_summary_by_risk_category.csv", by_risk)

    metrics = metric_bundle(trace_rows)
    metrics_path = args.output_dir / "policy_replay_metrics.json"
    write_json(metrics_path, metrics)

    can_freeze = (
        passes_keep_threshold(metrics)
        and passes_remove_threshold(metrics)
        and metrics["strong_api_leak_kept_count"] == 0
        and metrics["coverage_mismatch_kept_count"] == 0
        and metrics["semantic_mismatch_kept_count"] == 0
    )
    write_v42_policy_doc(args.docs_dir / "manual_audit_rule_v4_2_candidate.md", metrics)
    write_replay_report(
        args.docs_dir / "conservative_cleaning_policy_replay_report_v0_7.md",
        args.input,
        trace_rows,
        by_round,
        by_risk,
        metrics,
    )
    write_detector_matrix(args.docs_dir / "automatic_detector_readiness_matrix_v0_7.md", args.input, len(trace_rows))
    write_go_no_go(args.docs_dir / "cleaning_policy_v0_7_go_no_go_report.md", args.input, metrics, can_freeze)
    manifest = archive_results(args.archive_dir, args.output_dir, args.docs_dir)

    print(f"Rows replayed: {len(trace_rows)}")
    print(f"Wrote {trace_path}")
    print(f"Wrote {args.output_dir / 'policy_replay_summary_by_round.csv'}")
    print(f"Wrote {args.output_dir / 'policy_replay_summary_by_risk_category.csv'}")
    print(f"Wrote {metrics_path}")
    print(f"Wrote {args.docs_dir / 'manual_audit_rule_v4_2_candidate.md'}")
    print(f"Wrote {args.docs_dir / 'conservative_cleaning_policy_replay_report_v0_7.md'}")
    print(f"Wrote {args.docs_dir / 'automatic_detector_readiness_matrix_v0_7.md'}")
    print(f"Wrote {args.docs_dir / 'cleaning_policy_v0_7_go_no_go_report.md'}")
    print(f"Wrote archive manifest: {manifest}")
    print("Go / No-Go Decision v0.7:")
    print(f"can_freeze_v4_2_policy_candidate: {str(can_freeze).lower()}")
    print("can_write_conservative_cleaning_script_skeleton: true")
    print("can_run_policy_replay_on_audited_data: true")
    print("can_run_full_cleaning_now: false")
    print("can_create_split_now: false")
    print("can_run_paper_baseline_now: false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
