#!/usr/bin/env python
"""Replay v3.3-style manual audit rules on manual40 and Round2."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from round2_v0_5_utils import (
    DOCS_DIR,
    MANUAL40_PATH,
    OUTPUT_DIR,
    ROUND2_DRAFT_PATH,
    duplicate_ids,
    ensure_dirs,
    fieldnames_union,
    find_round2_human_final,
    load_standardized,
    now_str,
    parse_count,
    pct,
    rate,
    rows_to_markdown_table,
    write_csv,
    write_json,
)


MANUAL40_TRACE_CSV = OUTPUT_DIR / "manual40_rule_replay_trace.csv"
ROUND2_TRACE_CSV = OUTPUT_DIR / "round2_rule_replay_trace.csv"
SUMMARY_JSON = OUTPUT_DIR / "rule_replay_summary.json"
REPLAY_REPORT_MD = DOCS_DIR / "manual40_round2_rule_replay_report_v0_5.md"
V4_CANDIDATE_MD = DOCS_DIR / "manual_audit_rule_v4_candidate.md"
GO_NO_GO_MD = DOCS_DIR / "round2_v0_5_go_no_go_report.md"


def to_int(raw: str) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(float(str(raw).strip()))
    except ValueError:
        return None


def get_counts(std: Dict[str, str]) -> Dict[str, int | None]:
    raw = std.get("_raw", {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "candidate_service_count": to_int(std.get("candidate_service_count", ""))
        or parse_count(raw, "candidate_service_count", "candidate_services_json"),
        "gold_service_count": to_int(std.get("gold_service_count", ""))
        or parse_count(raw, "gold_service_count", "gold_services_json"),
        "candidate_api_count": to_int(std.get("candidate_api_count", ""))
        or parse_count(raw, "candidate_api_count", "candidate_apis_json"),
        "gold_api_count": to_int(std.get("gold_api_count", ""))
        or parse_count(raw, "gold_api_count", "gold_apis_json"),
    }


def replay_rule(std: Dict[str, str]) -> Tuple[str, List[str]]:
    rules: List[str] = []
    leak = std.get("leakage_bucket", "")
    semantic = std.get("semantic_alignment_bucket", "")
    candidate = std.get("candidate_validity_bucket", "")
    task_check = std.get("task_type_check_bucket", "")
    task_type = (std.get("task_type") or "").lower()
    task_family = std.get("task_family", "")
    is_service_level = "service" in task_family or "service_discovery" in task_type
    is_api_level = "api" in task_family or "api_recommendation" in task_type
    counts = get_counts(std)

    if leak == "api_leak_blocking":
        return "remove", ["strong_api_leak_remove"]

    if semantic == "mismatch":
        return "remove", ["semantic_mismatch_remove"]

    if candidate in {"invalid", "insufficient_choice_space"}:
        return "remove", [f"candidate_validity_{candidate}_remove"]

    if task_check == "invalid":
        return "remove", ["task_type_invalid_remove"]

    if is_service_level:
        cand_s = counts["candidate_service_count"]
        gold_s = counts["gold_service_count"]
        if cand_s is not None and gold_s is not None and cand_s <= gold_s:
            return "uncertain", ["service_level_insufficient_choice_space_uncertain"]

    if is_api_level:
        cand_api = counts["candidate_api_count"]
        gold_api = counts["gold_api_count"]
        if cand_api is not None and gold_api is not None and cand_api <= gold_api:
            return "uncertain", ["api_level_insufficient_choice_space_uncertain"]

    if leak == "service_leak_only":
        return "uncertain", ["service_leak_only_uncertain"]

    if leak == "ambiguous":
        return "uncertain", ["leakage_ambiguous_uncertain"]

    if semantic == "uncertain":
        return "uncertain", ["semantic_uncertain_uncertain"]

    if candidate in {"uncertain", "other"}:
        return "uncertain", [f"candidate_validity_{candidate}_uncertain"]

    task_valid = (
        task_check == "valid"
        or (is_service_level and task_check == "valid_multi_service")
        or (is_api_level and task_check == "valid_multi_api")
    )
    if task_check in {"uncertain", "other"} or not task_valid:
        return "uncertain", [f"task_type_check_{task_check or 'missing'}_uncertain"]

    if leak == "no_blocking" and semantic == "ok" and candidate == "valid" and task_valid:
        rules.append("clean_candidate_all_gates_pass")
        return "keep_for_cleaning_candidate", rules

    return "uncertain", ["otherwise_uncertain"]


def replay_dataset(name: str, path: Path) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    _, rows, mapping = load_standardized(path)
    dupes = duplicate_ids(rows)
    trace: List[Dict[str, object]] = []
    for std in rows:
        decision, triggered = replay_rule(std)
        raw = std.get("_raw", {})
        if not isinstance(raw, dict):
            raw = {}
        human = std.get("manual_final_decision_norm", "")
        counts = get_counts(std)
        trace.append(
            {
                "dataset": name,
                "sample_id": std.get("sample_id", ""),
                "task_id": std.get("task_id", ""),
                "task_type": std.get("task_type", ""),
                "task_family": std.get("task_family", ""),
                "review_bucket": std.get("review_bucket_norm", ""),
                "source_group": std.get("source_group", ""),
                "leakage_check": std.get("leakage_check", ""),
                "leakage_bucket": std.get("leakage_bucket", ""),
                "semantic_alignment_check": std.get("semantic_alignment_check", ""),
                "semantic_alignment_bucket": std.get("semantic_alignment_bucket", ""),
                "candidate_validity_check": std.get("candidate_validity_check", ""),
                "candidate_validity_bucket": std.get("candidate_validity_bucket", ""),
                "task_type_check": std.get("task_type_check", ""),
                "task_type_check_bucket": std.get("task_type_check_bucket", ""),
                "candidate_service_count": counts["candidate_service_count"],
                "gold_service_count": counts["gold_service_count"],
                "candidate_api_count": counts["candidate_api_count"],
                "gold_api_count": counts["gold_api_count"],
                "rule_decision": decision,
                "human_final_decision": human,
                "rule_human_match": "yes" if decision == human else "no",
                "triggered_rules": ";".join(triggered),
                "human_final_source": raw.get("human_final_source", ""),
                "query_text": std.get("query_text", ""),
                "human_decision_reason": raw.get("manual_decision_reason", ""),
            }
        )

    summary = summarize_trace(name, trace)
    summary["row_count"] = len(rows)
    summary["duplicate_sample_ids"] = dupes
    summary["missing_mapped_columns"] = [
        key
        for key in [
            "sample_id",
            "task_type",
            "manual_final_decision",
            "semantic_alignment_check",
            "leakage_check",
            "candidate_validity_check",
            "task_type_check",
        ]
        if mapping.get(key) is None
    ]
    return trace, summary


def summarize_trace(name: str, trace: List[Dict[str, object]]) -> Dict[str, object]:
    total = len(trace)
    matches = sum(1 for row in trace if row["rule_human_match"] == "yes")
    rule_dist = Counter(str(row["rule_decision"]) for row in trace)
    human_dist = Counter(str(row["human_final_decision"]) for row in trace)
    rule_keep = [row for row in trace if row["rule_decision"] == "keep_for_cleaning_candidate"]
    rule_remove = [row for row in trace if row["rule_decision"] == "remove"]
    rule_uncertain = [row for row in trace if row["rule_decision"] == "uncertain"]
    high_conf = [row for row in trace if row["review_bucket"] == "high_confidence_candidate"]
    high_risk = [row for row in trace if row["review_bucket"] == "high_risk_review"]
    api_leak = [row for row in trace if row["leakage_bucket"] == "api_leak_blocking"]
    semantic_mismatch = [row for row in trace if row["semantic_alignment_bucket"] == "mismatch"]
    semantic_uncertain = [row for row in trace if row["semantic_alignment_bucket"] == "uncertain"]

    return {
        "dataset": name,
        "agreement_count": matches,
        "agreement_rate": rate(matches, total),
        "rule_decision_distribution": dict(rule_dist),
        "human_final_decision_distribution": dict(human_dist),
        "rule_keep_human_keep_count": sum(
            1 for row in rule_keep if row["human_final_decision"] == "keep_for_cleaning_candidate"
        ),
        "rule_keep_count": len(rule_keep),
        "rule_keep_human_keep_rate": rate(
            sum(
                1
                for row in rule_keep
                if row["human_final_decision"] == "keep_for_cleaning_candidate"
            ),
            len(rule_keep),
        ),
        "rule_remove_human_remove_count": sum(
            1 for row in rule_remove if row["human_final_decision"] == "remove"
        ),
        "rule_remove_count": len(rule_remove),
        "rule_remove_human_remove_rate": rate(
            sum(1 for row in rule_remove if row["human_final_decision"] == "remove"),
            len(rule_remove),
        ),
        "rule_uncertain_human_distribution": dict(
            Counter(str(row["human_final_decision"]) for row in rule_uncertain)
        ),
        "high_confidence_count": len(high_conf),
        "high_confidence_human_keep_rate": rate(
            sum(
                1
                for row in high_conf
                if row["human_final_decision"] == "keep_for_cleaning_candidate"
            ),
            len(high_conf),
        ),
        "high_confidence_rule_keep_human_keep_rate": rate(
            sum(
                1
                for row in high_conf
                if row["rule_decision"] == "keep_for_cleaning_candidate"
                and row["human_final_decision"] == "keep_for_cleaning_candidate"
            ),
            sum(1 for row in high_conf if row["rule_decision"] == "keep_for_cleaning_candidate"),
        ),
        "high_risk_count": len(high_risk),
        "high_risk_human_remove_or_uncertain_rate": rate(
            sum(1 for row in high_risk if row["human_final_decision"] in {"remove", "uncertain"}),
            len(high_risk),
        ),
        "high_risk_rule_remove_or_uncertain_coverage": rate(
            sum(1 for row in high_risk if row["rule_decision"] in {"remove", "uncertain"}),
            len(high_risk),
        ),
        "api_leak_blocking_count": len(api_leak),
        "api_leak_captured_by_rule_remove_or_uncertain_rate": rate(
            sum(1 for row in api_leak if row["rule_decision"] in {"remove", "uncertain"}),
            len(api_leak),
        ),
        "semantic_mismatch_count": len(semantic_mismatch),
        "semantic_mismatch_rule_keep_count": sum(
            1 for row in semantic_mismatch if row["rule_decision"] == "keep_for_cleaning_candidate"
        ),
        "semantic_uncertain_count": len(semantic_uncertain),
        "semantic_uncertain_rule_keep_count": sum(
            1 for row in semantic_uncertain if row["rule_decision"] == "keep_for_cleaning_candidate"
        ),
    }


def write_replay_report(
    manual40_trace: List[Dict[str, object]],
    round2_trace: List[Dict[str, object]],
    summary: Dict[str, object],
    manual40_path: Path,
    round2_path: Path,
) -> None:
    m40 = summary["manual40"]
    r2 = summary["round2"]
    lines = [
        "# Manual40 + Round2 Rule Replay Report v0.5",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        f"- manual40 human approved: `{manual40_path}`",
        f"- Round2 human final: `{round2_path}`",
        f"- frozen rule reference: `docs/phase1/manual_audit_rule_v3_3.md`",
        "",
        "## 样本数量",
        "",
        f"- manual40 rows: `{m40['row_count']}`",
        f"- Round2 rows: `{r2['row_count']}`",
        "",
        "## Rule vs Human Agreement",
        "",
        "| dataset | agreement_count | row_count | agreement_rate |",
        "|---|---:|---:|---:|",
        f"| manual40 | {m40['agreement_count']} | {m40['row_count']} | {pct(m40['agreement_count'], m40['row_count'])} |",
        f"| Round2 | {r2['agreement_count']} | {r2['row_count']} | {pct(r2['agreement_count'], r2['row_count'])} |",
        "",
        "## Precision-like 统计",
        "",
        "| dataset | rule_keep human_keep | rule_remove human_remove | rule_uncertain human distribution |",
        "|---|---:|---:|---|",
        f"| manual40 | {pct(m40['rule_keep_human_keep_count'], m40['rule_keep_count'])} | {pct(m40['rule_remove_human_remove_count'], m40['rule_remove_count'])} | `{m40['rule_uncertain_human_distribution']}` |",
        f"| Round2 | {pct(r2['rule_keep_human_keep_count'], r2['rule_keep_count'])} | {pct(r2['rule_remove_human_remove_count'], r2['rule_remove_count'])} | `{r2['rule_uncertain_human_distribution']}` |",
        "",
        "## Round2 Bucket 子集",
        "",
        f"- high_confidence_candidate human keep rate: `{r2['high_confidence_human_keep_rate']:.4f}`",
        f"- high_confidence_candidate rule keep reliability: `{r2['high_confidence_rule_keep_human_keep_rate']:.4f}`",
        f"- high_risk_review human remove/uncertain rate: `{r2['high_risk_human_remove_or_uncertain_rate']:.4f}`",
        f"- high_risk_review rule remove/uncertain coverage: `{r2['high_risk_rule_remove_or_uncertain_coverage']:.4f}`",
        "",
        "## Fatal / Mapping / Duplicate Checks",
        "",
        f"- fatal_error: `{summary['fatal_error']}`",
        f"- manual40 missing mapped columns: `{m40['missing_mapped_columns']}`",
        f"- Round2 missing mapped columns: `{r2['missing_mapped_columns']}`",
        f"- manual40 duplicate sample_id: `{m40['duplicate_sample_ids']}`",
        f"- Round2 duplicate sample_id: `{r2['duplicate_sample_ids']}`",
        "",
        "## 错误样例",
        "",
        "### rule keep 但 human remove/uncertain",
        "",
    ]
    cols = [
        "dataset",
        "sample_id",
        "task_id",
        "task_type",
        "review_bucket",
        "rule_decision",
        "human_final_decision",
        "triggered_rules",
        "human_decision_reason",
    ]
    combined = manual40_trace + round2_trace
    lines.extend(
        rows_to_markdown_table(
            [
                row
                for row in combined
                if row["rule_decision"] == "keep_for_cleaning_candidate"
                and row["human_final_decision"] in {"remove", "uncertain"}
            ],
            cols,
            max_rows=15,
        )
    )
    lines.extend(["", "### rule remove 但 human keep", ""])
    lines.extend(
        rows_to_markdown_table(
            [
                row
                for row in combined
                if row["rule_decision"] == "remove"
                and row["human_final_decision"] == "keep_for_cleaning_candidate"
            ],
            cols,
            max_rows=15,
        )
    )
    lines.extend(["", "### rule uncertain 但 human keep", ""])
    lines.extend(
        rows_to_markdown_table(
            [
                row
                for row in combined
                if row["rule_decision"] == "uncertain"
                and row["human_final_decision"] == "keep_for_cleaning_candidate"
            ],
            cols,
            max_rows=15,
        )
    )
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "- 没有执行 full cleaning。",
            "- 没有生成 split。",
            "- 没有运行 baseline。",
            "- 没有训练模型。",
        ]
    )
    REPLAY_REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_v4_and_go_reports(summary: Dict[str, object], round2_path: Path) -> None:
    r2 = summary["round2"]
    no_fatal = not summary["fatal_error"]
    high_conf_ok = r2["high_confidence_human_keep_rate"] >= 0.70
    high_risk_ok = r2["high_risk_human_remove_or_uncertain_rate"] >= 0.70
    rule_keep_ok = r2["rule_keep_human_keep_rate"] >= 0.70
    api_leak_ok = r2["api_leak_captured_by_rule_remove_or_uncertain_rate"] >= 0.95
    semantic_ok = r2["semantic_mismatch_rule_keep_count"] == 0
    can_write_cleaning_script = all(
        [no_fatal, high_conf_ok, high_risk_ok, rule_keep_ok, api_leak_ok, semantic_ok]
    )
    can_run_full_cleaning_now = False
    can_create_split_now = False
    can_run_paper_baseline_now = False

    common_inputs = [
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        f"- Round2 human final: `{round2_path}`",
        f"- manual40: `{MANUAL40_PATH}`",
        f"- rule reference: `docs/phase1/manual_audit_rule_v3_3.md`",
        "",
        "## 样本数量",
        "",
        f"- manual40 rows: `{summary['manual40']['row_count']}`",
        f"- Round2 rows: `{r2['row_count']}`",
        "",
    ]

    if can_write_cleaning_script:
        v4_next_step = (
            "可以开始写 cleaning script，但只能先做 replay validation 和 another small sample，"
            "不应直接 full cleaning。"
        )
        go_next_step = (
            "写 cleaning pipeline，但只在 manual40 + Round2 + another small sample 上验证；"
            "不要运行 full cleaning。"
        )
    else:
        v4_next_step = (
            "暂不建议写正式 cleaning script。应先补强 high-risk / 能力覆盖规则，"
            "再做 another small sample 验证。"
        )
        go_next_step = (
            "先修订 v4 candidate 中的 high-risk 与能力覆盖失败规则，"
            "再做 another small sample 验证；通过后再写正式 cleaning script。"
        )

    v4_lines = [
        "# Manual Audit Rule v4 Candidate",
        "",
        *common_inputs,
        "## v3.3 规则回顾",
        "",
        "- strong API leak 优先删除。",
        "- semantic mismatch / uncertain 不得直接进入 clean-ready。",
        "- service leak only 不进入 clean service discovery。",
        "- service-level 必须有真实 service 选择空间。",
        "- API-level 可以只有一个 service，但必须有 API 选择空间。",
        "- G3/composable 不能自动视为 strong composable。",
        "",
        "## manual40 + Round2 验证结果",
        "",
        f"- manual40 rule agreement: `{summary['manual40']['agreement_rate']:.4f}`",
        f"- Round2 rule agreement: `{r2['agreement_rate']:.4f}`",
        f"- Round2 high_confidence human keep rate: `{r2['high_confidence_human_keep_rate']:.4f}`",
        f"- Round2 high_risk human remove/uncertain rate: `{r2['high_risk_human_remove_or_uncertain_rate']:.4f}`",
        f"- Round2 rule_keep human keep rate: `{r2['rule_keep_human_keep_rate']:.4f}`",
        f"- API leak captured by rule remove/uncertain rate: `{r2['api_leak_captured_by_rule_remove_or_uncertain_rate']:.4f}`",
        "",
        "## 稳定规则",
        "",
        "- blocking API leak 作为最高优先级 remove/uncertain gate 是稳定的。",
        "- semantic mismatch/uncertain 不能进入 keep，是稳定的 fail-closed 原则。",
        "- API-level 单 service 不自动失败，但 API candidate space 必须存在。",
        "- no_obvious_leak 不等于 semantic alignment ok。",
        "",
        "## 不稳定规则",
        "",
        "- generic tracking / container tracking / package tracking 仍容易误判。",
        "- restaurant、zoo、concert、coordinate、gas station 这类能力覆盖需要更强语义检查。",
        "- assistant draft 与 human final 的差异说明自动规则不能替代人审。",
        "",
        "## 是否可以进入正式 cleaning pipeline",
        "",
        f"- can_write_cleaning_script: `{str(can_write_cleaning_script).lower()}`",
        "- can_run_full_cleaning_now: `false`",
        "- required_human_review_before_full_cleaning: `true`",
        "",
        v4_next_step,
    ]
    V4_CANDIDATE_MD.write_text("\n".join(v4_lines), encoding="utf-8")

    go_lines = [
        "# Round2 v0.5 Go / No-Go Report",
        "",
        *common_inputs,
        "## 问题回答",
        "",
        f"1. Round2 人审结果是否支持当前规则：`{'yes_for_writing_script' if can_write_cleaning_script else 'not_enough_for_full_pipeline'}`。",
        "2. v3.3 规则是否需要升级到 v4：`yes`，至少需要把 Round2 用户反馈中的能力覆盖错误写进 v4 candidate。",
        f"3. 当前是否可以写正式 cleaning script：`{str(can_write_cleaning_script).lower()}`。",
        "4. 当前是否可以 full cleaning：`false`。",
        "5. 当前是否可以 split：`false`。",
        "6. 当前是否可以 baseline：`false`。",
        f"7. 下一步推荐动作：{go_next_step}",
        "",
        "```text",
        "Go / No-Go Decision",
        "",
        f"can_write_cleaning_script: {str(can_write_cleaning_script).lower()}",
        f"can_run_full_cleaning_now: {str(can_run_full_cleaning_now).lower()}",
        f"can_create_split_now: {str(can_create_split_now).lower()}",
        f"can_run_paper_baseline_now: {str(can_run_paper_baseline_now).lower()}",
        "",
        "recommended_next_step:",
        go_next_step,
        "```",
        "",
        "## 判定依据",
        "",
        f"- no fatal error: `{no_fatal}`",
        f"- high_confidence_candidate human keep rate >= 70%: `{high_conf_ok}` ({r2['high_confidence_human_keep_rate']:.4f})",
        f"- high_risk_review human remove/uncertain rate >= 70%: `{high_risk_ok}` ({r2['high_risk_human_remove_or_uncertain_rate']:.4f})",
        f"- rule_keep human keep rate >= 70%: `{rule_keep_ok}` ({r2['rule_keep_human_keep_rate']:.4f})",
        f"- blocking API leak captured: `{api_leak_ok}` ({r2['api_leak_captured_by_rule_remove_or_uncertain_rate']:.4f})",
        f"- semantic mismatch not leaking into keep: `{semantic_ok}`",
        "",
        "## Scope",
        "",
        "- 没有执行 full cleaning。",
        "- 没有生成 split。",
        "- 没有运行 baseline。",
        "- 没有训练模型。",
    ]
    GO_NO_GO_MD.write_text("\n".join(go_lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay manual audit rules for v0.5.")
    parser.add_argument("--manual40", type=Path, default=MANUAL40_PATH)
    parser.add_argument("--round2-draft", type=Path, default=ROUND2_DRAFT_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dirs()
    round2_path, human_resolution = find_round2_human_final(allow_overlay=True)
    if round2_path is None:
        print("ERROR: Round2 human final is missing; cannot replay rules.")
        return 2

    manual40_trace, manual40_summary = replay_dataset("manual40", args.manual40)
    round2_trace, round2_summary = replay_dataset("round2", round2_path)
    fatal_error = bool(
        manual40_summary["duplicate_sample_ids"]
        or round2_summary["duplicate_sample_ids"]
        or manual40_summary["missing_mapped_columns"]
        or round2_summary["missing_mapped_columns"]
    )
    summary = {
        "generated_at": now_str(),
        "inputs": {
            "manual40": str(args.manual40),
            "round2_human_final": str(round2_path),
            "round2_human_final_resolution": human_resolution,
        },
        "fatal_error": fatal_error,
        "manual40": manual40_summary,
        "round2": round2_summary,
        "scope": {
            "full_cleaning": False,
            "split": False,
            "baseline": False,
            "training": False,
        },
    }
    write_csv(MANUAL40_TRACE_CSV, manual40_trace, fieldnames_union(manual40_trace))
    write_csv(ROUND2_TRACE_CSV, round2_trace, fieldnames_union(round2_trace))
    write_json(SUMMARY_JSON, summary)
    write_replay_report(manual40_trace, round2_trace, summary, args.manual40, round2_path)
    write_v4_and_go_reports(summary, round2_path)
    print(f"manual40_rule_replay_trace={MANUAL40_TRACE_CSV}")
    print(f"round2_rule_replay_trace={ROUND2_TRACE_CSV}")
    print(f"rule_replay_summary={SUMMARY_JSON}")
    print(f"manual40_round2_rule_replay_report={REPLAY_REPORT_MD}")
    print(f"manual_audit_rule_v4_candidate={V4_CANDIDATE_MD}")
    print(f"round2_v0_5_go_no_go_report={GO_NO_GO_MD}")
    if fatal_error:
        print("ERROR: fatal validation issue found. See rule_replay_summary.json.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
