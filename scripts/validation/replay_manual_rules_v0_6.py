#!/usr/bin/env python
"""Replay v4.1 candidate decision policy on audited fields.

This is not an automatic raw-data detector validation. It replays decisions on
available audited fields and reports automatic detector status separately.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from rule_revision_v0_6_utils import (
    DOCS_DIR,
    MANUAL40_PATH,
    OUTPUT_DIR,
    as_int,
    candidate_bucket,
    ensure_dirs,
    get_count,
    leakage_bucket,
    markdown_table,
    missing_required_inputs,
    norm_decision,
    now_str,
    pct,
    semantic_bucket,
    task_family,
    task_type_bucket,
    write_csv,
    write_json,
    write_missing_inputs,
    read_csv,
)


MANUAL40_TRACE = OUTPUT_DIR / "manual40_rule_replay_v0_6_trace.csv"
ROUND2_TRACE = OUTPUT_DIR / "round2_rule_replay_v0_6_trace.csv"
SUMMARY_JSON = OUTPUT_DIR / "rule_replay_v0_6_summary.json"
REPORT_MD = DOCS_DIR / "manual40_round2_rule_replay_report_v0_6.md"

ROUND2_FINAL = OUTPUT_DIR.parent / "main_four_tasks_round2_rule_validation_v0_5" / "round2_manual_decisions_80_user_approved.normalized_from_user_overlay.csv"


def standardize(row: Dict[str, str], dataset: str) -> Dict[str, object]:
    sample_id = row.get("round2_review_id") or row.get("review_id") or row.get("sample_id") or row.get("task_id", "")
    human_source = row.get("human_final_source", "manual40_user_approved" if dataset == "manual40" else "")
    task = row.get("task_type", "")
    return {
        **row,
        "dataset": dataset,
        "sample_id": sample_id,
        "task_family": task_family(task),
        "decision_norm": norm_decision(row.get("manual_final_decision", "")),
        "leakage_norm": leakage_bucket(row.get("manual_leak_check", "") or row.get("leak_status", "")),
        "semantic_norm": semantic_bucket(row.get("manual_semantic_alignment", "") or row.get("semantic_alignment_status", "")),
        "candidate_norm": candidate_bucket(row.get("manual_candidate_gold_validity", "")),
        "task_check_norm": task_type_bucket(row.get("manual_task_type_check", "")),
        "candidate_service_count_int": get_count(row, "candidate_service_count", "candidate_services_json"),
        "gold_service_count_int": get_count(row, "gold_service_count", "gold_services_json"),
        "candidate_api_count_int": get_count(row, "candidate_api_count", "candidate_apis_json"),
        "gold_api_count_int": get_count(row, "gold_api_count", "gold_apis_json"),
        "human_final_source": human_source,
        "is_overlay": human_source == "user_feedback_overlay" or row.get("human_final_overlay_applied") == "yes",
        "user_feedback_category": row.get("user_feedback_category", ""),
    }


def valid_task_check(task: str, task_check: str) -> bool:
    if task_check == "valid":
        return True
    if "service" in task and task_check == "valid_multi_service":
        return True
    if "api" in task and task_check == "valid_multi_api":
        return True
    return False


def replay_decision(row: Dict[str, object]) -> Tuple[str, List[str], str]:
    leak = str(row.get("leakage_norm", ""))
    semantic = str(row.get("semantic_norm", ""))
    candidate = str(row.get("candidate_norm", ""))
    task_check = str(row.get("task_check_norm", ""))
    task = str(row.get("task_family", ""))
    csc = row.get("candidate_service_count_int")
    gsc = row.get("gold_service_count_int")
    capi = row.get("candidate_api_count_int")
    gapi = row.get("gold_api_count_int")
    rules: List[str] = []

    if leak == "api_leak_blocking":
        return "remove", ["strong_api_leak_remove"], "blocking_api_leak"
    if semantic == "mismatch":
        return "remove", ["semantic_mismatch_remove"], "coverage_or_semantic_mismatch"
    if candidate in {"invalid", "insufficient_choice_space"}:
        return "remove", [f"candidate_{candidate}_remove"], "candidate_space_invalid"
    if task_check == "invalid":
        return "remove", ["task_type_invalid_remove"], "task_type_invalid"

    if semantic == "uncertain":
        return "uncertain", ["semantic_uncertain"], "semantic_uncertain"
    if leak == "service_leak_only":
        return "uncertain", ["service_leak_only_policy_uncertain"], "service_leak_policy"
    if leak == "ambiguous":
        return "uncertain", ["ambiguous_leak_uncertain"], "weak_or_ambiguous_leak"
    if candidate in {"uncertain", "other"}:
        return "uncertain", [f"candidate_{candidate}_uncertain"], "coverage_or_candidate_uncertain"
    if task_check in {"uncertain", "other"}:
        return "uncertain", [f"task_type_{task_check}_uncertain"], "task_type_boundary"

    if "service" in task and isinstance(csc, int) and isinstance(gsc, int) and csc <= gsc:
        return "uncertain", ["service_level_candidate_service_count_not_greater_than_gold"], "candidate_space_boundary"

    if "api" in task and isinstance(capi, int) and isinstance(gapi, int) and capi <= gapi:
        return "uncertain", ["api_level_candidate_api_count_not_greater_than_gold"], "candidate_space_boundary"

    if (
        leak == "no_blocking"
        and semantic == "ok"
        and candidate == "valid"
        and valid_task_check(task, task_check)
    ):
        rules.append("clean_ready_all_audited_gates_pass")
        if "api" in task and csc == 1:
            rules.append("api_level_single_service_not_fatal")
        return "keep_for_cleaning_candidate", rules, "clean_ready"

    return "uncertain", ["otherwise_uncertain"], "unresolved"


def replay_file(path: Path, dataset: str) -> tuple[list[dict], dict]:
    cols, rows = read_csv(path)
    std_rows = [standardize(row, dataset) for row in rows]
    trace: list[dict] = []
    for row in std_rows:
        decision, rules, status = replay_decision(row)
        human = str(row.get("decision_norm", ""))
        trace.append(
            {
                "dataset": dataset,
                "sample_id": row.get("sample_id", ""),
                "task_id": row.get("task_id", ""),
                "task_type": row.get("task_type", ""),
                "task_family": row.get("task_family", ""),
                "source_group": row.get("source_group", ""),
                "review_bucket": row.get("mechanical_screening_bucket", ""),
                "human_final_source": row.get("human_final_source", ""),
                "is_overlay": "yes" if row.get("is_overlay") else "no",
                "user_feedback_category": row.get("user_feedback_category", ""),
                "leakage_norm": row.get("leakage_norm", ""),
                "semantic_norm": row.get("semantic_norm", ""),
                "candidate_norm": row.get("candidate_norm", ""),
                "task_check_norm": row.get("task_check_norm", ""),
                "candidate_service_count": row.get("candidate_service_count_int", ""),
                "gold_service_count": row.get("gold_service_count_int", ""),
                "candidate_api_count": row.get("candidate_api_count_int", ""),
                "gold_api_count": row.get("gold_api_count_int", ""),
                "decision_policy_replay_on_audited_fields": decision,
                "human_final_decision": human,
                "rule_human_match": "yes" if decision == human else "no",
                "triggered_rules": ";".join(rules),
                "policy_status": status,
                "automatic_detector_status": "not_validated_for_raw_auto_detection",
                "query_text": row.get("query_text", ""),
                "human_reason": row.get("manual_decision_reason", "") or row.get("calibration_reason", ""),
            }
        )
    return trace, summarize(dataset, trace)


def summarize(dataset: str, trace: list[dict]) -> dict:
    total = len(trace)
    matches = sum(1 for row in trace if row["rule_human_match"] == "yes")
    rule_keep = [r for r in trace if r["decision_policy_replay_on_audited_fields"] == "keep_for_cleaning_candidate"]
    rule_remove = [r for r in trace if r["decision_policy_replay_on_audited_fields"] == "remove"]
    rule_uncertain = [r for r in trace if r["decision_policy_replay_on_audited_fields"] == "uncertain"]
    overlay = [r for r in trace if r["is_overlay"] == "yes"]
    retained = [r for r in trace if r["is_overlay"] == "no"]
    api_single_legal = [
        r for r in trace
        if "api" in str(r["task_family"])
        and str(r.get("candidate_service_count")) == "1"
        and r["human_final_decision"] == "keep_for_cleaning_candidate"
    ]
    generic_false_positive_rows = [
        r for r in trace if r.get("user_feedback_category") == "leak_false_positive"
    ]
    strong_api_leaks = [r for r in trace if r["leakage_norm"] == "api_leak_blocking"]
    capability_mismatch = [
        r for r in trace
        if r.get("user_feedback_category") in {
            "gold_api_cannot_satisfy_query",
            "gold_service_cannot_satisfy_query",
            "missing_required_service",
            "package_vs_container_mismatch",
            "semantic_mismatch_despite_no_leak",
        }
    ]
    return {
        "dataset": dataset,
        "row_count": total,
        "agreement_count": matches,
        "agreement_rate": matches / total if total else 0,
        "rule_decision_distribution": dict(Counter(r["decision_policy_replay_on_audited_fields"] for r in trace)),
        "human_decision_distribution": dict(Counter(r["human_final_decision"] for r in trace)),
        "overlay_subset_row_count": len(overlay),
        "overlay_subset_agreement_count": sum(1 for r in overlay if r["rule_human_match"] == "yes"),
        "overlay_subset_agreement_rate": sum(1 for r in overlay if r["rule_human_match"] == "yes") / len(overlay) if overlay else 0,
        "draft_retained_row_count": len(retained),
        "draft_retained_agreement_count": sum(1 for r in retained if r["rule_human_match"] == "yes"),
        "draft_retained_agreement_rate": sum(1 for r in retained if r["rule_human_match"] == "yes") / len(retained) if retained else 0,
        "rule_keep_count": len(rule_keep),
        "rule_keep_human_keep_count": sum(1 for r in rule_keep if r["human_final_decision"] == "keep_for_cleaning_candidate"),
        "rule_keep_precision_like": sum(1 for r in rule_keep if r["human_final_decision"] == "keep_for_cleaning_candidate") / len(rule_keep) if rule_keep else 0,
        "rule_remove_count": len(rule_remove),
        "rule_remove_human_remove_count": sum(1 for r in rule_remove if r["human_final_decision"] == "remove"),
        "rule_remove_precision_like": sum(1 for r in rule_remove if r["human_final_decision"] == "remove") / len(rule_remove) if rule_remove else 0,
        "rule_uncertain_human_distribution": dict(Counter(r["human_final_decision"] for r in rule_uncertain)),
        "strong_api_leak_keep_count": sum(1 for r in strong_api_leaks if r["decision_policy_replay_on_audited_fields"] == "keep_for_cleaning_candidate"),
        "capability_mismatch_keep_count": sum(1 for r in capability_mismatch if r["decision_policy_replay_on_audited_fields"] == "keep_for_cleaning_candidate"),
        "api_level_single_service_legal_count": len(api_single_legal),
        "api_level_single_service_legal_rule_remove_count": sum(1 for r in api_single_legal if r["decision_policy_replay_on_audited_fields"] == "remove"),
        "generic_weak_leak_false_positive_count": len(generic_false_positive_rows),
        "generic_weak_leak_false_positive_rule_remove_count": sum(1 for r in generic_false_positive_rows if r["decision_policy_replay_on_audited_fields"] == "remove"),
    }


def write_report(manual_trace: list[dict], round2_trace: list[dict], summary: dict) -> None:
    m = summary["manual40"]
    r = summary["round2"]
    lines = [
        "# Manual40 + Round2 Rule Replay Report v0.6",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        f"- manual40: `{MANUAL40_PATH}`",
        f"- Round2 normalized final: `{ROUND2_FINAL}`",
        "- v4.1 candidate rules: `docs/phase1/manual_audit_rule_v4_1_candidate.md`",
        "",
        "## 样本数量",
        "",
        f"- manual40 rows: `{m['row_count']}`",
        f"- Round2 rows: `{r['row_count']}`",
        f"- Round2 overlay subset rows: `{r['overlay_subset_row_count']}`",
        f"- Round2 draft-retained subset rows: `{r['draft_retained_row_count']}`",
        "",
        "## Agreement",
        "",
        "| dataset | agreement | total | rate |",
        "|---|---:|---:|---:|",
        f"| manual40 | {m['agreement_count']} | {m['row_count']} | {pct(m['agreement_count'], m['row_count'])} |",
        f"| Round2 all | {r['agreement_count']} | {r['row_count']} | {pct(r['agreement_count'], r['row_count'])} |",
        f"| Round2 overlay subset | {r['overlay_subset_agreement_count']} | {r['overlay_subset_row_count']} | {pct(r['overlay_subset_agreement_count'], r['overlay_subset_row_count'])} |",
        f"| Round2 draft-retained subset | {r['draft_retained_agreement_count']} | {r['draft_retained_row_count']} | {pct(r['draft_retained_agreement_count'], r['draft_retained_row_count'])} |",
        "",
        "## Precision-like 统计",
        "",
        f"- rule_keep precision-like: `{r['rule_keep_precision_like']:.4f}`",
        f"- rule_remove precision-like: `{r['rule_remove_precision_like']:.4f}`",
        f"- rule_uncertain human distribution: `{r['rule_uncertain_human_distribution']}`",
        "",
        "## v4.1 关键检查",
        "",
        f"- strong API leak 漏入 keep: `{r['strong_api_leak_keep_count']}`",
        f"- capability coverage mismatch 漏入 keep: `{r['capability_mismatch_keep_count']}`",
        f"- API-level single-service legal 样本数: `{r['api_level_single_service_legal_count']}`",
        f"- API-level single-service legal 被 rule remove: `{r['api_level_single_service_legal_rule_remove_count']}`",
        f"- generic weak leak false positive 样本数: `{r['generic_weak_leak_false_positive_count']}`",
        f"- generic weak leak false positive 被 rule remove: `{r['generic_weak_leak_false_positive_rule_remove_count']}`",
        "",
        "## 自动检测器状态",
        "",
        "本报告是 `decision_policy_replay_on_audited_fields`，不是 raw 数据自动检测器验证。`automatic_detector_status` 统一为 `not_validated_for_raw_auto_detection`。",
        "",
        "## 误差样例",
        "",
        "### rule keep 但 human remove/uncertain",
        "",
    ]
    cols = [
        "dataset",
        "sample_id",
        "task_id",
        "task_type",
        "is_overlay",
        "user_feedback_category",
        "decision_policy_replay_on_audited_fields",
        "human_final_decision",
        "triggered_rules",
        "human_reason",
    ]
    combined = manual_trace + round2_trace
    lines.extend(markdown_table([r for r in combined if r["decision_policy_replay_on_audited_fields"] == "keep_for_cleaning_candidate" and r["human_final_decision"] in {"remove", "uncertain"}], cols, max_rows=12))
    lines.extend(["", "### rule remove 但 human keep", ""])
    lines.extend(markdown_table([r for r in combined if r["decision_policy_replay_on_audited_fields"] == "remove" and r["human_final_decision"] == "keep_for_cleaning_candidate"], cols, max_rows=12))
    lines.extend(["", "### rule uncertain 但 human keep", ""])
    lines.extend(markdown_table([r for r in combined if r["decision_policy_replay_on_audited_fields"] == "uncertain" and r["human_final_decision"] == "keep_for_cleaning_candidate"], cols, max_rows=12))
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "- 没有 full cleaning。",
            "- 没有 split。",
            "- 没有 baseline。",
            "- 没有训练模型。",
        ]
    )
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay v4.1 candidate rules for v0.6.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def main() -> int:
    parse_args()
    ensure_dirs()
    missing = missing_required_inputs()
    if missing:
        out = write_missing_inputs(missing)
        print(f"ERROR: missing required inputs. See {out}")
        return 2
    manual_trace, manual_summary = replay_file(MANUAL40_PATH, "manual40")
    round2_trace, round2_summary = replay_file(ROUND2_FINAL, "round2")
    write_csv(MANUAL40_TRACE, manual_trace)
    write_csv(ROUND2_TRACE, round2_trace)
    summary = {
        "generated_at": now_str(),
        "inputs": {
            "manual40": str(MANUAL40_PATH),
            "round2": str(ROUND2_FINAL),
        },
        "manual40": manual_summary,
        "round2": round2_summary,
        "decision_policy_replay_on_audited_fields": True,
        "automatic_detector_status": "not_validated_for_raw_auto_detection",
        "scope": {
            "full_cleaning": False,
            "split": False,
            "baseline": False,
            "training": False,
        },
    }
    write_json(SUMMARY_JSON, summary)
    write_report(manual_trace, round2_trace, summary)
    print(f"manual40_rule_replay_v0_6_trace={MANUAL40_TRACE}")
    print(f"round2_rule_replay_v0_6_trace={ROUND2_TRACE}")
    print(f"rule_replay_v0_6_summary={SUMMARY_JSON}")
    print(f"manual40_round2_rule_replay_report_v0_6={REPORT_MD}")
    print(
        "round2_replay="
        f"agreement:{round2_summary['agreement_count']}/{round2_summary['row_count']};"
        f"overlay:{round2_summary['overlay_subset_agreement_count']}/{round2_summary['overlay_subset_row_count']};"
        f"rule_keep_precision:{round2_summary['rule_keep_precision_like']:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
