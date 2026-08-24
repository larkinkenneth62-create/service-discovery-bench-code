#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Analyze user-approved manual40 labels and generate calibration artifacts.

This script only performs analysis, rule calibration, validation-set export,
and archiving. It does not run full cleaning, baseline, model training,
train/dev/test split, top200 expansion, or full G3 search.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCS = PROJECT_ROOT / "docs" / "phase1"
OUT = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2"
DRYRUN = PROJECT_ROOT / "outputs" / "main_four_tasks_dryrun_v0_2"
ARCHIVE = (
    PROJECT_ROOT
    / "outputs"
    / "run_archives"
    / "2026-06-26_manual40_user_approved_analysis_v0_2"
)

REQUIRED_INPUTS = [
    OUT / "main_four_tasks_manual_decisions_40_user_approved_round1.csv",
    DOCS / "main_four_tasks_user_approved_round1_report.md",
    DRYRUN / "multi_service_discovery_task_level.csv",
    DRYRUN / "multi_api_recommendation_task_level.csv",
    DRYRUN / "main_four_tasks_dryrun_summary.json",
    DOCS / "manual_audit_rule_v3_3.md",
    DOCS / "service_discovery_bench_v0_2_schema_draft.md",
    DOCS / "main_four_tasks_dryrun_v0_2_report.md",
    DOCS / "main_four_tasks_source_viability_report.md",
]

APPROVED_CSV = REQUIRED_INPUTS[0]
VALIDATION_ERRORS_CSV = OUT / "manual40_user_approved_validation_errors.csv"
ANALYSIS_REPORT = DOCS / "main_four_tasks_manual40_user_approved_analysis_report_v0_2.md"
DISAGREEMENT_REPORT = DOCS / "main_four_tasks_manual40_rule_disagreement_report_v0_2.md"
DISAGREEMENT_CSV = OUT / "main_four_tasks_manual40_rule_disagreements.csv"
FAIL_CLOSED_DOC = DOCS / "main_four_tasks_fail_closed_rule_update_after_manual40_v0_2.md"
GOLD_VALIDATION_CSV = OUT / "main_four_tasks_manual_gold_validation_set_40.csv"
GOLD_VALIDATION_JSON = OUT / "main_four_tasks_manual_gold_validation_set_40.json"
VALIDATION_PLAN = DOCS / "main_four_tasks_cleaning_script_validation_plan_after_manual40_v0_2.md"
NEXT_STEP_DOC = DOCS / "main_four_tasks_next_step_after_user_approved_manual40_v0_2.md"
SUMMARY_JSON = OUT / "main_four_tasks_manual40_user_approved_analysis_summary_v0_2.json"


REQUIRED_COLUMNS = [
    "review_id",
    "task_id",
    "task_type",
    "source_group",
    "leak_status",
    "manual_semantic_alignment",
    "manual_leak_check",
    "manual_candidate_gold_validity",
    "manual_task_type_check",
    "manual_final_decision",
    "manual_decision_reason",
    "review_completed",
    "user_approval_status",
    "user_approval_round",
    "user_approval_note",
    "user_approval_time",
]

VALID_FINAL_DECISION = {"keep_for_cleaning_candidate", "uncertain", "remove"}
VALID_SEMANTIC = {
    "semantic_alignment_ok",
    "semantic_alignment_uncertain",
    "semantic_mismatch_uncertain",
}
VALID_LEAK = {
    "no_blocking_leak",
    "api_leak_blocking",
    "service_leak_only",
    "leak_uncertain",
}
VALID_CANDIDATE_GOLD = {
    "valid",
    "candidate_set_too_small",
    "gold_incomplete",
    "gold_wrong",
    "uncertain",
}
VALID_TASK_TYPE_CHECK = {
    "valid_multi_service_discovery",
    "valid_multi_api_recommendation",
    "should_be_multi_api",
    "should_be_multi_service",
    "should_be_single_service",
    "should_be_single_api",
    "ordinary_or_unclear",
    "not_eligible",
}

VALIDATION_FIELDS = [
    "review_id",
    "task_id",
    "task_type",
    "manual_semantic_alignment",
    "manual_leak_check",
    "manual_candidate_gold_validity",
    "manual_task_type_check",
    "manual_final_decision",
    "manual_decision_reason",
    "user_approval_status",
    "user_approval_round",
]


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def pct(n: int, d: int) -> str:
    return f"{(n / d * 100):.1f}%" if d else "0.0%"


def dist(rows: list[dict[str, str]], col: str) -> Counter[str]:
    return Counter(row.get(col, "") for row in rows)


def crosstab(rows: list[dict[str, str]], row_col: str, col_col: str) -> dict[str, Counter[str]]:
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        table[row.get(row_col, "")][row.get(col_col, "")] += 1
    return dict(table)


def md_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def decision_rows_for_task_type(rows: list[dict[str, str]]) -> list[list[object]]:
    out: list[list[object]] = []
    for task_type in ["multi_service_discovery", "multi_api_recommendation"]:
        subset = [r for r in rows if r.get("task_type") == task_type]
        total = len(subset)
        counts = dist(subset, "manual_final_decision")
        out.append(
            [
                task_type,
                total,
                counts.get("keep_for_cleaning_candidate", 0),
                counts.get("uncertain", 0),
                counts.get("remove", 0),
                pct(counts.get("keep_for_cleaning_candidate", 0), total),
                pct(counts.get("uncertain", 0), total),
                pct(counts.get("remove", 0), total),
            ]
        )
    return out


def crosstab_markdown(rows: list[dict[str, str]], label_col: str) -> str:
    table = crosstab(rows, label_col, "manual_final_decision")
    ordered_decisions = ["keep_for_cleaning_candidate", "uncertain", "remove"]
    md_rows = []
    for label, counts in sorted(table.items()):
        total = sum(counts.values())
        md_rows.append([label, total] + [counts.get(d, 0) for d in ordered_decisions])
    return md_table(["label", "total"] + ordered_decisions, md_rows)


def validate_manual40(rows: list[dict[str, str]], fieldnames: list[str]) -> tuple[list[dict[str, str]], dict[str, object]]:
    errors: list[dict[str, str]] = []

    def add_error(error_type: str, message: str, row: dict[str, str] | None = None) -> None:
        errors.append(
            {
                "severity": "error",
                "error_type": error_type,
                "review_id": row.get("review_id", "") if row else "",
                "task_id": row.get("task_id", "") if row else "",
                "task_type": row.get("task_type", "") if row else "",
                "message": message,
            }
        )

    for col in REQUIRED_COLUMNS:
        if col not in fieldnames:
            add_error("missing_column", f"Missing required column: {col}")

    if len(rows) != 40:
        add_error("row_count", f"Expected 40 rows, got {len(rows)}")

    review_counts = dist(rows, "review_id")
    for review_id, count in review_counts.items():
        if count > 1:
            add_error("duplicate_review_id", f"Duplicate review_id {review_id}: {count} rows")

    by_task = defaultdict(list)
    for row in rows:
        by_task[row.get("task_id", "")].append(row.get("task_type", ""))
    duplicate_task_ids = {
        task_id: task_types
        for task_id, task_types in by_task.items()
        if task_id and len(task_types) > 1
    }

    for row in rows:
        if row.get("review_completed") != "yes":
            add_error("review_completed_not_yes", "review_completed is not yes", row)
        if row.get("user_approval_status") != "approved":
            add_error("approval_not_approved", "user_approval_status is not approved", row)
        if row.get("user_approval_round") != "round1":
            add_error("approval_round_not_round1", "user_approval_round is not round1", row)
        if not row.get("manual_final_decision", "").strip():
            add_error("empty_final_decision", "manual_final_decision is empty", row)
        if not row.get("manual_decision_reason", "").strip():
            add_error("empty_decision_reason", "manual_decision_reason is empty", row)
        if row.get("manual_final_decision") not in VALID_FINAL_DECISION:
            add_error("invalid_final_decision", f"Invalid manual_final_decision: {row.get('manual_final_decision')}", row)
        if row.get("manual_semantic_alignment") not in VALID_SEMANTIC:
            add_error("invalid_semantic_alignment", f"Invalid manual_semantic_alignment: {row.get('manual_semantic_alignment')}", row)
        if row.get("manual_leak_check") not in VALID_LEAK:
            add_error("invalid_leak_check", f"Invalid manual_leak_check: {row.get('manual_leak_check')}", row)
        if row.get("manual_candidate_gold_validity") not in VALID_CANDIDATE_GOLD:
            add_error(
                "invalid_candidate_gold_validity",
                f"Invalid manual_candidate_gold_validity: {row.get('manual_candidate_gold_validity')}",
                row,
            )
        if row.get("manual_task_type_check") not in VALID_TASK_TYPE_CHECK:
            add_error("invalid_task_type_check", f"Invalid manual_task_type_check: {row.get('manual_task_type_check')}", row)

    validation_summary = {
        "row_count": len(rows),
        "error_count": len(errors),
        "duplicate_task_ids": duplicate_task_ids,
        "all_review_completed_yes": all(r.get("review_completed") == "yes" for r in rows),
        "all_user_approved": all(r.get("user_approval_status") == "approved" for r in rows),
        "all_round1": all(r.get("user_approval_round") == "round1" for r in rows),
    }
    return errors, validation_summary


def is_dryrun_candidate_like(row: dict[str, str]) -> bool:
    text = " ".join(
        [
            row.get("cleaning_status", ""),
            row.get("task_eligibility", ""),
            row.get("task_bucket", ""),
        ]
    ).lower()
    return "candidate" in text or "clean" in text or "dryrun" in text


def disagreement_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def add(row: dict[str, str], disagreement_type: str, fix: str) -> None:
        out.append(
            {
                "review_id": row.get("review_id", ""),
                "task_id": row.get("task_id", ""),
                "task_type": row.get("task_type", ""),
                "query_text": row.get("query_text", ""),
                "leak_status": row.get("leak_status", ""),
                "cleaning_status": row.get("cleaning_status", ""),
                "task_eligibility": row.get("task_eligibility", ""),
                "task_bucket": row.get("task_bucket", ""),
                "manual_semantic_alignment": row.get("manual_semantic_alignment", ""),
                "manual_leak_check": row.get("manual_leak_check", ""),
                "manual_candidate_gold_validity": row.get("manual_candidate_gold_validity", ""),
                "manual_task_type_check": row.get("manual_task_type_check", ""),
                "manual_final_decision": row.get("manual_final_decision", ""),
                "manual_decision_reason": row.get("manual_decision_reason", ""),
                "disagreement_type": disagreement_type,
                "suggested_rule_fix": fix,
            }
        )

    for row in rows:
        if is_dryrun_candidate_like(row) and row.get("manual_final_decision") in {"uncertain", "remove"}:
            add(
                row,
                "dryrun_candidate_but_manual_not_clean",
                "Fail closed: dry-run candidate signals must be gated by manual semantic/leak/candidate-gold checks.",
            )
        if row.get("leak_status") == "no_obvious_leak" and row.get("manual_semantic_alignment") in {
            "semantic_alignment_uncertain",
            "semantic_mismatch_uncertain",
        }:
            add(
                row,
                "no_obvious_leak_but_semantic_uncertain_or_mismatch",
                "Do not use no-leak as a proxy for semantic validity; add semantic alignment gate.",
            )
        if row.get("leak_status") == "no_obvious_leak" and row.get("manual_final_decision") in {"uncertain", "remove"}:
            add(
                row,
                "no_obvious_leak_but_manual_uncertain_or_remove",
                "No obvious leak is insufficient for clean; keep fail-closed checks for geography/carrier/object mismatch.",
            )
        if row.get("task_type") == "multi_service_discovery" and row.get("manual_task_type_check") != "valid_multi_service_discovery":
            add(
                row,
                "multi_service_task_type_not_valid_multi_service",
                "Service-level samples need real service choice space and semantic alignment; otherwise route to audit/uncertain.",
            )
        if row.get("task_type") == "multi_api_recommendation" and row.get("manual_task_type_check") != "valid_multi_api_recommendation":
            add(
                row,
                "multi_api_task_type_not_valid_multi_api",
                "API-level samples need valid API choice space and gold operation alignment; otherwise route to audit/uncertain.",
            )
        if row.get("manual_leak_check") == "service_leak_only" and is_dryrun_candidate_like(row):
            add(
                row,
                "service_leak_only_dryrun_candidate",
                "Service leak only must not enter clean service discovery; API-level service leak should default to audit/uncertain.",
            )
        if row.get("manual_candidate_gold_validity") == "gold_wrong" and is_dryrun_candidate_like(row):
            add(
                row,
                "gold_wrong_dryrun_candidate",
                "Gold-wrong samples must be hard-blocked from clean outputs.",
            )
    return out


def write_analysis_report(rows: list[dict[str, str]], validation_summary: dict[str, object]) -> None:
    total = len(rows)
    final_counts = dist(rows, "manual_final_decision")
    approval_times = sorted({r.get("user_approval_time", "") for r in rows if r.get("user_approval_time", "")})

    lines = [
        "# Main Four Tasks Manual40 User-Approved Analysis Report v0.2",
        "",
        "## 基本信息",
        "",
        f"- 总样本数：{total}",
        f"- 审批状态：{validation_summary['all_user_approved'] and '全部 approved' or '存在异常'}",
        f"- 审批轮次：round1",
        f"- 审批时间：{', '.join(approval_times)}",
        f"- keep：{final_counts.get('keep_for_cleaning_candidate', 0)} ({pct(final_counts.get('keep_for_cleaning_candidate', 0), total)})",
        f"- uncertain：{final_counts.get('uncertain', 0)} ({pct(final_counts.get('uncertain', 0), total)})",
        f"- remove：{final_counts.get('remove', 0)} ({pct(final_counts.get('remove', 0), total)})",
        "",
        "## 按任务类型统计",
        "",
        md_table(
            ["task_type", "total", "keep", "uncertain", "remove", "keep rate", "uncertain rate", "remove rate"],
            decision_rows_for_task_type(rows),
        ),
        "",
        "结论：当前 manual40 中，multi_service_discovery 的 keep rate 高于 multi_api_recommendation；multi_api 更容易受到 service leak、单一 service 候选、generic tracking 绑定具体服务等问题影响。",
        "",
        "## 按 semantic alignment 统计",
        "",
        crosstab_markdown(rows, "manual_semantic_alignment"),
        "",
        "## 按 leak check 统计",
        "",
        crosstab_markdown(rows, "manual_leak_check"),
        "",
        "## 按 candidate/gold validity 统计",
        "",
        crosstab_markdown(rows, "manual_candidate_gold_validity"),
        "",
        "## 按 task type check 统计",
        "",
        crosstab_markdown(rows, "manual_task_type_check"),
        "",
        "## 主要 keep 特征",
        "",
        "- query 能明确支持 gold service/API，且不存在明显 geography/carrier/object-type gap。",
        "- 没有 strong API leak；service 名没有直接暴露，或该行不是 clean service discovery 任务。",
        "- candidate/gold 结构有效，gold 在 candidates 中，且当前层级存在真实选择空间。",
        "- API-level 中即使只有一个 candidate service，只要同一 service 下有多个 candidate APIs，仍可保留为 API recommendation 候选。",
        "",
        "## 主要 uncertain 原因",
        "",
        "- generic package tracking 被绑定到具体物流/邮政服务，但 query 没有给出国家、地区、承运商或追踪号格式证据。",
        "- generic postal code/address lookup 被绑定到具体国家或地区服务，例如 CEP Brazil，但 query 没有明确巴西/CEP/Correios 语境。",
        "- query 不能唯一推出 gold service/API，只能说明某类能力。",
        "- service_leak_only 样本不能直接进入 clean service discovery；API-level 也应先进入 audit/uncertain。",
        "- API-level 中 gold APIs 虽相关，但不够唯一，或者 gold service 绑定过强。",
        "- package/mail tracking 与 container tracking 存在对象类型差异。",
        "",
        "## 主要 remove 原因",
        "",
        "- semantic mismatch：query 的需求域与 gold services/APIs 明显不同。",
        "- gold_wrong：gold 无法覆盖 query，或国家/任务类型明显错配。",
        "- not_eligible：样本不适合进入 benchmark 主任务。",
    ]
    ANALYSIS_REPORT.write_text("\n".join(lines), encoding="utf-8")


def write_disagreement_report(disagreements: list[dict[str, str]], rows: list[dict[str, str]]) -> None:
    type_counts = Counter(d["disagreement_type"] for d in disagreements)
    affected_samples = len({d["review_id"] for d in disagreements})
    report_rows = [[k, v] for k, v in sorted(type_counts.items())]
    lines = [
        "# Main Four Tasks Manual40 Rule Disagreement Report v0.2",
        "",
        "## 本次做了什么",
        "",
        "对 dry-run 初始字段和 user-approved manual40 判断进行对比，找出 dry-run candidate 信号与人工结论之间的不一致。",
        "",
        "## 总览",
        "",
        f"- manual40 样本数：{len(rows)}",
        f"- disagreement 记录数：{len(disagreements)}",
        f"- 受影响样本数：{affected_samples}",
        "",
        "## disagreement type 分布",
        "",
        md_table(["disagreement_type", "count"], report_rows),
        "",
        "## 主要发现",
        "",
        "- dry-run 的 `no_obvious_leak` 不能证明样本 clean；manual40 中大量 uncertain/remove 来自 semantic alignment 或 gold validity 问题。",
        "- dry-run candidate-like 状态容易高估 generic tracking/address 样本质量。",
        "- service_leak_only 需要单独 gate，尤其不能进入 clean service discovery。",
        "- gold_wrong/not_eligible 必须作为 hard block。",
        "",
        "## 规则修正方向",
        "",
        "- 增加 semantic alignment gate。",
        "- 增加 query-to-gold uniqueness / evidence gate。",
        "- 对 generic package tracking、generic postal/address lookup 采用 fail-closed 默认 uncertain。",
        "- service-level 要求 `candidate_service_count > gold_service_count`。",
        "- API-level 要求 `candidate_api_count > gold_api_count`。",
    ]
    DISAGREEMENT_REPORT.write_text("\n".join(lines), encoding="utf-8")


def write_fail_closed_doc() -> None:
    lines = [
        "# Fail-Closed Rule Update After Manual40 v0.2",
        "",
        "本文件基于 user-approved manual40 结果生成，用于后续正式清洗脚本的规则校准。它不是 full cleaning，不是 baseline，也不是训练规则。",
        "",
        "## 强规则",
        "",
        "这些规则后续脚本必须执行：",
        "",
        "1. `manual_final_decision=remove` 的模式不能进入 clean。",
        "2. `semantic_mismatch_uncertain` 不能进入 clean。",
        "3. `gold_wrong` 不能进入 clean。",
        "4. `not_eligible` 不能进入 clean。",
        "5. `service_leak_only` 不进入 clean service discovery。",
        "6. 人工 `uncertain` 样本不能被脚本自动升级成 clean。",
        "7. query 不能唯一支持 gold service/API 时，默认 `uncertain`。",
        "8. candidate/gold 为空、JSON parse 失败、字段缺失时，默认 `uncertain` 或 `remove`。",
        "9. service-level task 必须满足 `candidate_service_count > gold_service_count`。",
        "10. API-level task 必须满足 `candidate_api_count > gold_api_count`。",
        "",
        "## 趋势规则",
        "",
        "这些规则来自 manual40，但后续需要更多样本验证：",
        "",
        "1. generic package tracking 绑定具体物流/邮政服务，倾向 `uncertain`。",
        "2. generic postal code/address lookup 绑定具体国家/地区服务，倾向 `uncertain`。",
        "3. API-level 中 `service_leak_only` 倾向 `uncertain`，不直接 `remove`。",
        "4. manual40 中 multi_service 比 multi_api 更容易形成 clean candidate，但该结论需要第二轮样本验证。",
        "5. G1 的单 candidate service 样本不适合 service discovery，但可能适合 API-level recommendation。",
        "",
        "## 不能过拟合的地方",
        "",
        "manual40 只是小样本，不允许把所有人工理由机械化为最终规则。尤其不能把某个服务名、国家名、追踪号格式的个案写死成全局清洗规则。",
        "",
        "后续应把这些规则作为 fail-closed gate 和 audit routing 规则，而不是直接当作最终 benchmark 数据生成规则。",
    ]
    FAIL_CLOSED_DOC.write_text("\n".join(lines), encoding="utf-8")


def write_gold_validation_set(rows: list[dict[str, str]]) -> None:
    validation_rows = [{field: row.get(field, "") for field in VALIDATION_FIELDS} for row in rows]
    write_csv(GOLD_VALIDATION_CSV, validation_rows, VALIDATION_FIELDS)
    GOLD_VALIDATION_JSON.write_text(
        json.dumps(
            {
                "description": "User-approved manual40 validation set for future cleaning-script regression tests.",
                "row_count": len(validation_rows),
                "records": validation_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_validation_plan() -> None:
    lines = [
        "# Cleaning Script Validation Plan After Manual40 v0.2",
        "",
        "本计划定义正式清洗脚本在 full cleaning 前必须通过的验证流程。当前阶段不允许直接 full cleaning。",
        "",
        "## Step 1: Schema Validation",
        "",
        "- 字段完整性。",
        "- JSON parse。",
        "- candidate/gold 非空。",
        "- task_type 合法。",
        "- metadata 字段可读。",
        "",
        "## Step 2: Rule Invariant Validation",
        "",
        "- service-level: `candidate_service_count > gold_service_count`。",
        "- API-level: `candidate_api_count > gold_api_count`。",
        "- `semantic_mismatch_uncertain` 不允许进入 clean。",
        "- `gold_wrong` 不允许进入 clean。",
        "- `not_eligible` 不允许进入 clean。",
        "- `service_leak_only` 不允许进入 clean service discovery。",
        "- `service_leak_only` 在 API-level 只能进入 audit/uncertain，不能直接 clean。",
        "- query 不能唯一支持 gold 的样本默认 `uncertain`。",
        "",
        "## Step 3: Manual Regression Validation",
        "",
        "- 用 `main_four_tasks_manual_gold_validation_set_40.csv` 测试脚本输出。",
        "- 如果脚本把人工 `remove` 样本放进 clean，必须报错。",
        "- 如果脚本把人工 `uncertain` 样本放进 clean，必须报错。",
        "- 如果脚本把人工 `keep` 样本 remove，输出 warning 并解释原因。",
        "- 所有不一致都要输出 comparison report。",
        "",
        "## Step 4: Small Dry-run Before Full Cleaning",
        "",
        "- 通过 manual40 验证后，只能先做小范围 dry-run。",
        "- 建议下一轮抽取：multi_service_discovery 40 条，multi_api_recommendation 40 条。",
        "- 不允许直接 full cleaning。",
        "",
        "## Step 5: Human Review Loop",
        "",
        "- 第二轮 dry-run 仍要人工审核。",
        "- 如果 keep/uncertain/remove 分布稳定，才考虑扩大。",
        "- 如果 uncertain 仍接近 50%，说明规则或数据源仍需调整。",
    ]
    VALIDATION_PLAN.write_text("\n".join(lines), encoding="utf-8")


def write_next_step_doc(rows: list[dict[str, str]]) -> None:
    task_rows = decision_rows_for_task_type(rows)
    lines = [
        "# Next Step After User-Approved Manual40 v0.2",
        "",
        "## 1. 当前 manual40 结果说明了什么",
        "",
        "manual40 显示：main four tasks 的候选构造方向可行，但当前规则仍偏乐观。40 条中 only 19 条 keep，19 条 uncertain，2 条 remove，说明必须继续 fail-closed。",
        "",
        "## 2. multi_service 是否比 multi_api 更适合优先推进",
        "",
        md_table(["task_type", "total", "keep", "uncertain", "remove", "keep rate", "uncertain rate", "remove rate"], task_rows),
        "",
        "manual40 中 multi_service 的 keep rate 更高，建议优先推进 multi_service_discovery 的第二轮 dry-run；multi_api 仍可并行抽样，但需要更强的 API-level gold validation。",
        "",
        "## 3. 为什么现在仍不建议 full cleaning",
        "",
        "uncertain 占 19/40，接近一半。generic tracking/address、container tracking、service leak、candidate choice space 等问题还没有完全自动化验证。",
        "",
        "## 4. 为什么现在仍不建议 baseline",
        "",
        "benchmark 数据还没稳定，baseline 会把数据构造问题和模型能力混在一起，无法解释结果。",
        "",
        "## 5. 下一步是否应该再审一轮 40 条",
        "",
        "建议再审一轮 80 条小样本：multi_service_discovery 40 条，multi_api_recommendation 40 条。不要直接 full cleaning。",
        "",
        "## 6. 第二轮 40/80 条应该怎么抽",
        "",
        "- 覆盖 generic tracking/address 高风险样本。",
        "- 覆盖 candidate_service_count 接近 gold_service_count 的边界样本。",
        "- 覆盖 service_leak_only 和 query-to-gold 不唯一样本。",
        "- 保留一部分高置信 keep 样本，用于检查规则是否过度保守。",
        "",
        "## 7. single_service 和 single_api 是否继续暂缓",
        "",
        "建议 single_service 继续暂缓，因为 G1 单 candidate service 不适合 service discovery。single_api 可以后续考虑，但要先保证 API-level candidate choice space。",
        "",
        "## 8. 是否需要引入 MetaTool / ShortcutsBench 补强 single_service",
        "",
        "需要作为后续方向。ToolBench 当前更适合 multi-service/multi-api 复现与校准，single_service 可能需要 MetaTool / ShortcutsBench 等来源补强。",
        "",
        "## 9. 正式清洗脚本什么时候可以写",
        "",
        "可以开始写验证优先的清洗脚本骨架，但只能跑 manual40 regression 和小范围 dry-run，不能直接 full cleaning。",
        "",
        "## 10. full cleaning 什么时候可以开始",
        "",
        "至少需要通过 manual40 regression、第二轮人工审核、rule disagreement 收敛后，才能考虑 full cleaning。",
        "",
        "## 11. 可以向导师汇报什么",
        "",
        "- 已完成 40 条 user-approved manual audit。",
        "- 已发现 dry-run 规则偏乐观，尤其在 logistics/address/tracking 类样本上。",
        "- 已提出 fail-closed 规则和 manual gold validation set。",
        "- 下一步建议是第二轮 dry-run + 人工审核，而不是 baseline 或 full cleaning。",
    ]
    NEXT_STEP_DOC.write_text("\n".join(lines), encoding="utf-8")


def archive_outputs(paths: list[Path]) -> None:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if not path.exists():
            continue
        rel = path.relative_to(PROJECT_ROOT)
        target = ARCHIVE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main() -> None:
    missing_inputs = [str(path) for path in REQUIRED_INPUTS if not path.exists()]
    if missing_inputs:
        print(json.dumps({"missing_required_inputs": missing_inputs}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    rows, fieldnames = read_csv(APPROVED_CSV)
    validation_errors, validation_summary = validate_manual40(rows, fieldnames)
    write_csv(
        VALIDATION_ERRORS_CSV,
        validation_errors,
        ["severity", "error_type", "review_id", "task_id", "task_type", "message"],
    )

    disagreements = disagreement_rows(rows)
    write_csv(
        DISAGREEMENT_CSV,
        disagreements,
        [
            "review_id",
            "task_id",
            "task_type",
            "query_text",
            "leak_status",
            "cleaning_status",
            "task_eligibility",
            "task_bucket",
            "manual_semantic_alignment",
            "manual_leak_check",
            "manual_candidate_gold_validity",
            "manual_task_type_check",
            "manual_final_decision",
            "manual_decision_reason",
            "disagreement_type",
            "suggested_rule_fix",
        ],
    )

    write_analysis_report(rows, validation_summary)
    write_disagreement_report(disagreements, rows)
    write_fail_closed_doc()
    write_gold_validation_set(rows)
    write_validation_plan()
    write_next_step_doc(rows)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "manual40_complete": validation_summary["error_count"] == 0,
        "validation_error_count": validation_summary["error_count"],
        "row_count": len(rows),
        "final_decision_distribution": dict(sorted(dist(rows, "manual_final_decision").items())),
        "task_type_distribution": dict(sorted(dist(rows, "task_type").items())),
        "task_type_final_decision": {
            task_type: dict(sorted(counts.items()))
            for task_type, counts in crosstab(rows, "task_type", "manual_final_decision").items()
        },
        "disagreement_count": len(disagreements),
        "disagreement_type_distribution": dict(sorted(Counter(d["disagreement_type"] for d in disagreements).items())),
        "outputs": {
            "validation_errors_csv": str(VALIDATION_ERRORS_CSV),
            "analysis_report": str(ANALYSIS_REPORT),
            "disagreement_report": str(DISAGREEMENT_REPORT),
            "disagreement_csv": str(DISAGREEMENT_CSV),
            "fail_closed_doc": str(FAIL_CLOSED_DOC),
            "gold_validation_csv": str(GOLD_VALIDATION_CSV),
            "gold_validation_json": str(GOLD_VALIDATION_JSON),
            "validation_plan": str(VALIDATION_PLAN),
            "next_step_doc": str(NEXT_STEP_DOC),
            "archive": str(ARCHIVE),
        },
        "guardrails": {
            "full_cleaning": False,
            "baseline": False,
            "training": False,
            "split": False,
            "top200": False,
            "full_g3_search": False,
            "auto_modify_manual_labels": False,
        },
        "duplicate_task_ids": validation_summary["duplicate_task_ids"],
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    archive_outputs(
        [
            APPROVED_CSV,
            GOLD_VALIDATION_CSV,
            GOLD_VALIDATION_JSON,
            ANALYSIS_REPORT,
            DISAGREEMENT_REPORT,
            FAIL_CLOSED_DOC,
            VALIDATION_PLAN,
            NEXT_STEP_DOC,
            DISAGREEMENT_CSV,
            VALIDATION_ERRORS_CSV,
            SUMMARY_JSON,
            Path(__file__),
        ]
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
