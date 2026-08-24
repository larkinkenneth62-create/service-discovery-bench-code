#!/usr/bin/env python
"""Analyze v0.5 failure modes and produce v4.1 rule candidate docs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from rule_revision_v0_6_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    ensure_dirs,
    identify_failure_modes,
    load_draft_vs_human_trace,
    load_round2_final,
    markdown_table,
    missing_required_inputs,
    mode_implication,
    now_str,
    pct,
    write_csv,
    write_missing_inputs,
)


TRACE_CSV = OUTPUT_DIR / "failure_mode_trace_v0_6.csv"
SUMMARY_CSV = OUTPUT_DIR / "failure_mode_summary_v0_6.csv"
TAXONOMY_MD = DOCS_DIR / "round2_failure_mode_taxonomy_v0_6.md"
V41_MD = DOCS_DIR / "manual_audit_rule_v4_1_candidate.md"
DETECTOR_MD = DOCS_DIR / "automatic_detector_spec_v0_6.md"


REQUIRED_MODES = [
    "strong_api_leak_missed_by_draft",
    "endpoint_identity_exposed_in_query",
    "generic_weak_leak_false_positive",
    "api_level_single_service_ok",
    "service_level_single_service_invalid",
    "gold_api_cannot_satisfy_query",
    "gold_service_cannot_satisfy_query",
    "semantic_coverage_mismatch",
    "candidate_space_invalid",
    "service_leak_only_policy_unclear",
    "high_risk_bucket_false_positive",
    "high_risk_bucket_not_predictive",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze v0.5 failure modes for v0.6.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def build_trace() -> list[dict]:
    final_rows = load_round2_final()
    trace_rows = load_draft_vs_human_trace()
    trace_by_id = {str(row.get("sample_id", "")): row for row in trace_rows}
    out: list[dict] = []
    for row in final_rows:
        sample_id = str(row.get("sample_id", ""))
        trace = trace_by_id.get(sample_id)
        modes = identify_failure_modes(row, trace)
        if not modes:
            modes = ["no_failure_mode_detected"]
        out.append(
            {
                "sample_id": sample_id,
                "task_id": row.get("task_id", ""),
                "task_type": row.get("task_type", ""),
                "source_group": row.get("source_group", ""),
                "review_bucket": row.get("mechanical_screening_bucket", ""),
                "human_final_source": row.get("human_final_source", ""),
                "is_overlay": "yes" if row.get("is_overlay") else "no",
                "user_feedback_category": row.get("user_feedback_category", ""),
                "manual_final_decision": row.get("manual_final_decision", ""),
                "decision_norm": row.get("decision_norm", ""),
                "assistant_draft_manual_final_decision": row.get(
                    "assistant_draft_manual_final_decision", ""
                ),
                "assistant_decision_norm": row.get("assistant_decision_norm", ""),
                "manual_leak_check": row.get("manual_leak_check", ""),
                "assistant_draft_manual_leak_check": row.get(
                    "assistant_draft_manual_leak_check", ""
                ),
                "manual_semantic_alignment": row.get("manual_semantic_alignment", ""),
                "assistant_draft_manual_semantic_alignment": row.get(
                    "assistant_draft_manual_semantic_alignment", ""
                ),
                "manual_candidate_gold_validity": row.get("manual_candidate_gold_validity", ""),
                "candidate_service_count": row.get("candidate_service_count", ""),
                "gold_service_count": row.get("gold_service_count", ""),
                "candidate_api_count": row.get("candidate_api_count", ""),
                "gold_api_count": row.get("gold_api_count", ""),
                "failure_modes": ";".join(modes),
                "mode_count": len(modes),
                "query_text": row.get("query_text", ""),
                "gold_services_json": row.get("gold_services_json", ""),
                "gold_apis_json": row.get("gold_apis_json", ""),
                "calibration_reason": row.get("calibration_reason", ""),
            }
        )
    return out


def build_summary(trace: list[dict]) -> list[dict]:
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for row in trace:
        for mode in str(row["failure_modes"]).split(";"):
            by_mode[mode].append(row)

    summary: list[dict] = []
    all_count = len(trace)
    overlay_count = sum(1 for row in trace if row["is_overlay"] == "yes")
    for mode in sorted(set(REQUIRED_MODES) | set(by_mode.keys())):
        rows = by_mode.get(mode, [])
        overlay_rows = [row for row in rows if row["is_overlay"] == "yes"]
        draft_rows = [row for row in rows if row["is_overlay"] == "no"]
        summary.append(
            {
                "failure_mode": mode,
                "count_all_80": len(rows),
                "rate_all_80": pct(len(rows), all_count),
                "count_overlay_subset": len(overlay_rows),
                "rate_overlay_subset": pct(len(overlay_rows), overlay_count),
                "count_draft_retained_subset": len(draft_rows),
                "rule_implication": mode_implication(mode),
                "representative_sample_ids": ";".join(str(row["sample_id"]) for row in rows[:10]),
            }
        )
    return summary


def write_taxonomy(trace: list[dict], summary: list[dict]) -> None:
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for row in trace:
        for mode in str(row["failure_modes"]).split(";"):
            by_mode[mode].append(row)

    overlay = [row for row in trace if row["is_overlay"] == "yes"]
    lines = [
        "# Round2 Failure Mode Taxonomy v0.6",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        "- `outputs/main_four_tasks_round2_rule_validation_v0_5/round2_manual_decisions_80_user_approved.normalized_from_user_overlay.csv`",
        "- `outputs/main_four_tasks_round2_rule_validation_v0_5/round2_draft_vs_human_trace.csv`",
        "- `outputs/main_four_tasks_round2_rule_validation_v0_5/round2_rule_replay_trace.csv`",
        "",
        "## 样本数量",
        "",
        f"- all Round2 final rows: `{len(trace)}`",
        f"- user_feedback_overlay rows: `{len(overlay)}`",
        "",
        "## Failure Mode Summary",
        "",
        "| failure mode | all count | overlay count | rule implication |",
        "|---|---:|---:|---|",
    ]
    for item in summary:
        if item["failure_mode"] == "no_failure_mode_detected":
            continue
        lines.append(
            f"| `{item['failure_mode']}` | {item['count_all_80']} | "
            f"{item['count_overlay_subset']} | {item['rule_implication']} |"
        )

    lines.extend(
        [
            "",
            "## 24 条 user_feedback_overlay 的主要模式",
            "",
        ]
    )
    overlay_mode_counts = Counter()
    for row in overlay:
        for mode in str(row["failure_modes"]).split(";"):
            overlay_mode_counts[mode] += 1
    lines.extend(["| mode | overlay count |", "|---|---:|"])
    for mode, count in overlay_mode_counts.most_common():
        lines.append(f"| `{mode}` | {count} |")

    lines.extend(["", "## Representative Examples", ""])
    example_cols = [
        "sample_id",
        "task_id",
        "task_type",
        "is_overlay",
        "user_feedback_category",
        "decision_norm",
        "assistant_decision_norm",
        "calibration_reason",
        "query_text",
    ]
    for mode in REQUIRED_MODES:
        lines.extend([f"### {mode}", "", f"Rule implication: {mode_implication(mode)}", ""])
        lines.extend(markdown_table(by_mode.get(mode, []), example_cols, max_rows=10))
        lines.append("")

    lines.extend(
        [
            "## 特别分析",
            "",
            "- `draft_keep_but_human_remove_or_uncertain`：说明 assistant draft 容易过度 keep，需要 capability coverage gate。",
            "- `draft_remove_or_uncertain_but_human_keep`：说明 generic weak leak 和 API-level single-service 边界会导致误删。",
            "- `draft_no_blocking_but_human_api_leak_blocking`：说明 carrier/endpoint identity leak detector 不够。",
            "- `draft_ok_but_human_mismatch_or_uncertain`：说明 semantic alignment 和 capability coverage 不能依赖 leak detector。",
            "",
            "## Scope",
            "",
            "- 没有 full cleaning。",
            "- 没有 split。",
            "- 没有 baseline。",
            "- 没有训练模型。",
        ]
    )
    TAXONOMY_MD.write_text("\n".join(lines), encoding="utf-8")


def write_v41_doc(summary: list[dict]) -> None:
    lines = [
        "# Manual Audit Rule v4.1 Candidate",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        "- `outputs/main_four_tasks_rule_revision_v0_6/failure_mode_summary_v0_6.csv`",
        "- `docs/phase1/manual_audit_rule_v4_candidate.md`",
        "",
        "## 样本数量",
        "",
        "- Round2 normalized final: `80`",
        "- user_feedback_overlay subset: `24`",
        "",
        "## v4.1 相比 v4 candidate 的核心变化",
        "",
        "v4.1 不再把 high-risk bucket 当作直接 reject/uncertain 预测器，而是把它拆成具体风险子类型；同时新增 capability coverage gate，修正 API-level single-service 边界和 generic weak leak false positive。",
        "",
        "## 1. Strong API Leak 规则",
        "",
        "### 类型",
        "",
        "| leak type | decision | explanation |",
        "|---|---|---|",
        "| endpoint-specific leak | remove | query 暴露 endpoint/path/API 专名 |",
        "| carrier-specific leak | remove | query 直接出现与 API path 强绑定的承运商/服务专名，例如 Correo Argentino/OCA |",
        "| task-flow endpoint identity leak | remove | query 暴露 create_task/result_task 等 API 调用流程身份 |",
        "| generic weak term | weak_leak_or_nonblocking | Latest/All/Count/List/Search 等自然语言通用词不直接 remove |",
        "| ambiguous API name mention | uncertain | API 名和普通词边界不清时进入 uncertain |",
        "",
        "## 2. API-level Single-Service 规则",
        "",
        "`candidate_service_count = 1` 对 API-level task 不是 fatal。",
        "",
        "API-level 可以 keep 的条件：",
        "",
        "- `prediction_level = api`",
        "- `candidate_api_count > gold_api_count`",
        "- no blocking API leak",
        "- semantic / capability coverage ok",
        "- task_type_check valid",
        "",
        "但对 service-level task：",
        "",
        "- `candidate_service_count <= gold_service_count` 仍不适合 clean service discovery。",
        "",
        "## 3. Capability Coverage 规则",
        "",
        "新增字段概念：",
        "",
        "```text",
        "capability_coverage_check:",
        "- coverage_ok",
        "- coverage_uncertain",
        "- coverage_mismatch",
        "```",
        "",
        "规则：",
        "",
        "- `coverage_mismatch -> remove`",
        "- `coverage_uncertain -> uncertain`",
        "- `coverage_ok -> 进入后续判断`",
        "",
        "特别关注：",
        "",
        "- gold API/service 不能实现 query",
        "- generic address/postal vs country-specific CEP",
        "- generic package/mail vs carrier-specific tracking",
        "- package/mail tracking vs container tracking",
        "- venue/bookstore/zoo/concert query vs unrelated gold services",
        "",
        "## 4. High-risk Bucket 规则",
        "",
        "`review_bucket = high_risk_review` 只能表示 `needs_review_priority`，不能直接等价于 remove/uncertain。",
        "",
        "v4.1 high-risk 子类型：",
        "",
        "- endpoint_leak_risk",
        "- generic_false_positive_risk",
        "- capability_coverage_risk",
        "- candidate_space_risk",
        "- service_leak_policy_risk",
        "- task_type_boundary_risk",
        "",
        "## 5. Final Decision Policy",
        "",
        "### clean_ready",
        "",
        "必须同时满足：",
        "",
        "- no blocking API leak",
        "- semantic alignment ok",
        "- capability coverage ok",
        "- candidate choice space valid for the prediction level",
        "- task type eligibility valid",
        "",
        "### remove",
        "",
        "- strong API leak",
        "- capability coverage mismatch",
        "- fatal candidate space invalid",
        "- gold not in candidates",
        "- clear semantic mismatch",
        "",
        "### uncertain",
        "",
        "- weak/ambiguous leak",
        "- coverage uncertain",
        "- semantic uncertain",
        "- high-risk unresolved",
        "- task type boundary",
        "",
        "## 6. Failure Mode Evidence",
        "",
        "| failure mode | count all | overlay count | implication |",
        "|---|---:|---:|---|",
    ]
    for row in summary:
        if row["failure_mode"] == "no_failure_mode_detected":
            continue
        lines.append(
            f"| `{row['failure_mode']}` | {row['count_all_80']} | {row['count_overlay_subset']} | {row['rule_implication']} |"
        )
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "- 这是 candidate，不是最终冻结版。",
            "- 没有 full cleaning。",
            "- 没有 split。",
            "- 没有 baseline。",
            "- 没有训练模型。",
        ]
    )
    V41_MD.write_text("\n".join(lines), encoding="utf-8")


def write_detector_spec() -> None:
    rows = [
        ("api_leak_detector", "query, gold/candidate API names, endpoint paths", "endpoint-specific / carrier-specific / generic weak / ambiguous", "heuristic + needs_human_review", "generic terms false positive; carrier identity missed", "no"),
        ("service_leak_detector", "query, gold/candidate service names", "service_leak_only / no_service_leak / ambiguous", "heuristic", "brand names vs generic service terms", "partial"),
        ("candidate_space_validator", "candidate/gold services/APIs counts", "valid / insufficient_choice_space / uncertain", "deterministic", "requires correct task prediction level", "yes_for_counts_only"),
        ("task_type_eligibility_validator", "task_type, prediction level, candidate/gold counts", "valid_multi_service / valid_multi_api / invalid / uncertain", "heuristic", "boundary between service-level and API-level", "partial"),
        ("semantic_alignment_detector", "query, gold service/API descriptions", "semantic_alignment_ok / uncertain / mismatch", "needs_llm_assist + needs_human_review", "cannot be treated as fully automatic now", "no"),
        ("capability_coverage_detector", "query, candidate/gold service/API capability text", "coverage_ok / coverage_uncertain / coverage_mismatch", "needs_llm_assist + needs_human_review", "core current blocker; no leak does not imply coverage", "no"),
        ("dedup_detector", "query signature, task_id, normalized query", "duplicate / unique / uncertain", "deterministic + heuristic", "paraphrase duplicates", "partial"),
        ("composable_dependency_detector", "query steps, service outputs/inputs", "strong_composable / ordinary_multi / ambiguous", "heuristic + needs_human_review", "G3 raw not sufficient; dependency chain required", "no"),
    ]
    lines = [
        "# Automatic Detector Spec v0.6",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        "- `docs/phase1/round2_failure_mode_taxonomy_v0_6.md`",
        "- `docs/phase1/manual_audit_rule_v4_1_candidate.md`",
        "",
        "## 样本数量",
        "",
        "- Based on Round2 normalized final: `80`",
        "- User overlay subset: `24`",
        "",
        "## Detector Table",
        "",
        "| Detector | 输入 | 输出 | 当前状态 | 风险 | 是否可全自动 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(f"`{cell}`" for cell in row) + " |")
    lines.extend(
        [
            "",
            "## 关键说明",
            "",
            "`semantic_alignment_detector` 和 `capability_coverage_detector` 当前不能假装已经全自动可靠。v0.5/v0.6 的 rule replay 依赖 audited fields，不能直接证明 raw full cleaning 自动检测可用。",
            "",
            "## Scope",
            "",
            "- 没有 full cleaning。",
            "- 没有 split。",
            "- 没有 baseline。",
            "- 没有训练模型。",
        ]
    )
    DETECTOR_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parse_args()
    ensure_dirs()
    missing = missing_required_inputs()
    if missing:
        out = write_missing_inputs(missing)
        print(f"ERROR: missing required inputs. See {out}")
        return 2
    trace = build_trace()
    summary = build_summary(trace)
    write_csv(TRACE_CSV, trace)
    write_csv(SUMMARY_CSV, summary)
    write_taxonomy(trace, summary)
    write_v41_doc(summary)
    write_detector_spec()

    overlay_modes = Counter()
    for row in trace:
        if row["is_overlay"] == "yes":
            for mode in str(row["failure_modes"]).split(";"):
                overlay_modes[mode] += 1
    print(f"failure_mode_trace={TRACE_CSV}")
    print(f"failure_mode_summary={SUMMARY_CSV}")
    print(f"failure_mode_taxonomy={TAXONOMY_MD}")
    print(f"manual_audit_rule_v4_1_candidate={V41_MD}")
    print(f"automatic_detector_spec={DETECTOR_MD}")
    print("overlay_top_failure_modes=" + ";".join(f"{k}:{v}" for k, v in overlay_modes.most_common(8)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
