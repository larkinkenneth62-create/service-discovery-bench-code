#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Validate fail-closed rules on the user-approved manual40 validation set.

This script only validates rules on manual40. It does not run full cleaning,
baseline, model training, train/dev/test split, top200 expansion, full G3
search, or automatic manual-label modification.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "outputs" / "main_four_tasks_rule_validation_v0_3"
DOCS_DIR = PROJECT_ROOT / "docs" / "phase1"
ARCHIVE_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "run_archives"
    / "2026-06-26_manual40_rule_validation_v0_3"
)

MANUAL_GOLD_CSV = (
    PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_manual_check_v0_2"
    / "main_four_tasks_manual_gold_validation_set_40.csv"
)
APPROVED_CSV = (
    PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_manual_check_v0_2"
    / "main_four_tasks_manual_decisions_40_user_approved_round1.csv"
)
DRYRUN_SERVICE_CSV = (
    PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_dryrun_v0_2"
    / "multi_service_discovery_task_level.csv"
)
DRYRUN_API_CSV = (
    PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_dryrun_v0_2"
    / "multi_api_recommendation_task_level.csv"
)

REQUIRED_INPUTS = [
    MANUAL_GOLD_CSV,
    PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "main_four_tasks_manual_gold_validation_set_40.json",
    APPROVED_CSV,
    PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "main_four_tasks_manual40_rule_disagreements.csv",
    PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2" / "manual40_user_approved_validation_errors.csv",
    DOCS_DIR / "main_four_tasks_manual40_user_approved_analysis_report_v0_2.md",
    DOCS_DIR / "main_four_tasks_manual40_rule_disagreement_report_v0_2.md",
    DOCS_DIR / "main_four_tasks_fail_closed_rule_update_after_manual40_v0_2.md",
    DOCS_DIR / "main_four_tasks_cleaning_script_validation_plan_after_manual40_v0_2.md",
    DOCS_DIR / "main_four_tasks_next_step_after_user_approved_manual40_v0_2.md",
    DRYRUN_SERVICE_CSV,
    DRYRUN_API_CSV,
    DOCS_DIR / "manual_audit_rule_v3_3.md",
    DOCS_DIR / "service_discovery_bench_v0_2_schema_draft.md",
]

PREDICTIONS_CSV = OUT_DIR / "manual40_rule_validation_predictions.csv"
COMPARISON_CSV = OUT_DIR / "manual40_rule_validation_comparison.csv"
FATAL_ERRORS_CSV = OUT_DIR / "manual40_rule_validation_fatal_errors.csv"
WARNINGS_CSV = OUT_DIR / "manual40_rule_validation_warnings.csv"
UNMATCHED_CSV = OUT_DIR / "manual40_rule_validation_unmatched.csv"
SUMMARY_JSON = OUT_DIR / "manual40_rule_validation_summary.json"
REPORT_MD = DOCS_DIR / "main_four_tasks_rule_validation_manual40_v0_3_report.md"
NEXT_STEP_MD = DOCS_DIR / "main_four_tasks_rule_validation_manual40_v0_3_next_step.md"

PREDICTION_FIELDS = [
    "review_id",
    "task_id",
    "task_type",
    "source_group",
    "original_leak_status",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "manual_semantic_alignment",
    "manual_leak_check",
    "manual_candidate_gold_validity",
    "manual_task_type_check",
    "manual_final_decision",
    "predicted_rule_decision",
    "predicted_cleaning_bucket",
    "predicted_reason",
    "is_fatal_error",
    "is_warning",
    "validation_status",
]

COMPARISON_FIELDS = PREDICTION_FIELDS + [
    "manual_vs_rule_match",
    "manual_expected_rule_decision",
    "comparison_note",
]

ISSUE_FIELDS = [
    "review_id",
    "task_id",
    "task_type",
    "issue_type",
    "message",
    "manual_final_decision",
    "predicted_rule_decision",
]


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_json_value(text: str, field_name: str, review_id: str, warnings: list[dict[str, Any]]) -> tuple[Any, bool]:
    if text is None or str(text).strip() == "":
        warnings.append(
            {
                "review_id": review_id,
                "task_id": "",
                "task_type": "",
                "issue_type": "json_empty",
                "message": f"{field_name} is empty.",
                "manual_final_decision": "",
                "predicted_rule_decision": "",
            }
        )
        return None, False
    try:
        return json.loads(text), True
    except Exception as exc:
        warnings.append(
            {
                "review_id": review_id,
                "task_id": "",
                "task_type": "",
                "issue_type": "json_parse_failed",
                "message": f"{field_name} parse failed: {exc}",
                "manual_final_decision": "",
                "predicted_rule_decision": "",
            }
        )
        return None, False


def safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def count_json_items(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


def counts_from_row(row: dict[str, str], review_id: str) -> tuple[dict[str, int], bool, list[dict[str, Any]]]:
    local_warnings: list[dict[str, Any]] = []
    parse_ok = True

    metadata, metadata_ok = parse_json_value(row.get("metadata_json", ""), "metadata_json", review_id, local_warnings)
    if not metadata_ok:
        parse_ok = False
        metadata = {}

    parsed_fields: dict[str, Any] = {}
    for field in [
        "candidate_services_json",
        "gold_services_json",
        "candidate_apis_json",
        "gold_apis_json",
    ]:
        value, ok = parse_json_value(row.get(field, ""), field, review_id, local_warnings)
        parsed_fields[field] = value
        parse_ok = parse_ok and ok

    counts = {
        "candidate_service_count": safe_int(metadata.get("candidate_service_count")),
        "gold_service_count": safe_int(metadata.get("gold_service_count")),
        "candidate_api_count": safe_int(metadata.get("candidate_api_count")),
        "gold_api_count": safe_int(metadata.get("gold_api_count")),
    }
    fallback_map = {
        "candidate_service_count": "candidate_services_json",
        "gold_service_count": "gold_services_json",
        "candidate_api_count": "candidate_apis_json",
        "gold_api_count": "gold_apis_json",
    }
    for count_name, field_name in fallback_map.items():
        if counts[count_name] is None:
            counts[count_name] = count_json_items(parsed_fields[field_name])

    for field_name, parsed in parsed_fields.items():
        if count_json_items(parsed) <= 0:
            parse_ok = False
            local_warnings.append(
                {
                    "review_id": review_id,
                    "task_id": "",
                    "task_type": "",
                    "issue_type": "json_empty_or_non_list",
                    "message": f"{field_name} is empty or not countable.",
                    "manual_final_decision": "",
                    "predicted_rule_decision": "",
                }
            )

    return {k: int(v or 0) for k, v in counts.items()}, parse_ok, local_warnings


def is_service_level(task_type: str) -> bool:
    return task_type == "multi_service_discovery"


def is_api_level(task_type: str) -> bool:
    return task_type == "multi_api_recommendation"


def predict_rule(row: dict[str, str], counts: dict[str, int], parse_ok: bool) -> tuple[str, str, str]:
    semantic = row.get("manual_semantic_alignment", "")
    leak = row.get("manual_leak_check", "")
    validity = row.get("manual_candidate_gold_validity", "")
    task_check = row.get("manual_task_type_check", "")
    task_type = row.get("task_type", "")
    reasons: list[str] = []

    if validity == "gold_wrong":
        reasons.append("manual_candidate_gold_validity=gold_wrong hard block")
        return "rule_remove", "remove", "; ".join(reasons)
    if task_check == "not_eligible":
        reasons.append("manual_task_type_check=not_eligible hard block")
        return "rule_remove", "remove", "; ".join(reasons)
    if leak == "api_leak_blocking":
        reasons.append("manual_leak_check=api_leak_blocking hard block")
        return "rule_remove", "remove", "; ".join(reasons)
    if semantic == "semantic_mismatch_uncertain" and validity == "gold_wrong":
        reasons.append("semantic mismatch plus gold_wrong hard block")
        return "rule_remove", "remove", "; ".join(reasons)

    if not parse_ok:
        reasons.append("JSON parse or nonempty candidate/gold validation failed")
        return "rule_uncertain", "uncertain", "; ".join(reasons)
    if semantic == "semantic_mismatch_uncertain":
        reasons.append("semantic_mismatch_uncertain fail-closed")
        return "rule_uncertain", "uncertain", "; ".join(reasons)
    if semantic == "semantic_alignment_uncertain":
        reasons.append("semantic_alignment_uncertain fail-closed")
        return "rule_uncertain", "uncertain", "; ".join(reasons)
    if leak == "service_leak_only":
        reasons.append("service_leak_only routes to uncertain")
        return "rule_uncertain", "uncertain", "; ".join(reasons)
    if leak == "leak_uncertain":
        reasons.append("leak_uncertain routes to uncertain")
        return "rule_uncertain", "uncertain", "; ".join(reasons)
    if validity in {"candidate_set_too_small", "gold_incomplete", "uncertain"}:
        reasons.append(f"manual_candidate_gold_validity={validity} routes to uncertain")
        return "rule_uncertain", "uncertain", "; ".join(reasons)
    if task_check == "ordinary_or_unclear":
        reasons.append("manual_task_type_check=ordinary_or_unclear routes to uncertain")
        return "rule_uncertain", "uncertain", "; ".join(reasons)
    if is_service_level(task_type) and counts["candidate_service_count"] <= counts["gold_service_count"]:
        reasons.append("service-level candidate_service_count <= gold_service_count")
        return "rule_uncertain", "uncertain", "; ".join(reasons)
    if is_api_level(task_type) and counts["candidate_api_count"] <= counts["gold_api_count"]:
        reasons.append("API-level candidate_api_count <= gold_api_count")
        return "rule_uncertain", "uncertain", "; ".join(reasons)

    if (
        semantic == "semantic_alignment_ok"
        and leak == "no_blocking_leak"
        and validity == "valid"
        and task_check in {"valid_multi_service_discovery", "valid_multi_api_recommendation"}
        and (not is_service_level(task_type) or counts["candidate_service_count"] > counts["gold_service_count"])
        and (not is_api_level(task_type) or counts["candidate_api_count"] > counts["gold_api_count"])
        and parse_ok
    ):
        reasons.append("all keep-candidate gates passed")
        return "rule_keep_candidate", "clean_candidate", "; ".join(reasons)

    reasons.append("no keep rule matched; fail closed to uncertain")
    return "rule_uncertain", "uncertain", "; ".join(reasons)


def manual_expected_rule_decision(manual_final_decision: str) -> str:
    if manual_final_decision == "keep_for_cleaning_candidate":
        return "rule_keep_candidate"
    if manual_final_decision == "remove":
        return "rule_remove"
    return "rule_uncertain"


def issue_row(row: dict[str, Any], issue_type: str, message: str) -> dict[str, Any]:
    return {
        "review_id": row.get("review_id", ""),
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "issue_type": issue_type,
        "message": message,
        "manual_final_decision": row.get("manual_final_decision", ""),
        "predicted_rule_decision": row.get("predicted_rule_decision", ""),
    }


def detect_fatal_errors(row: dict[str, Any], parse_ok: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    predicted = row["predicted_rule_decision"]
    manual_final = row["manual_final_decision"]
    if manual_final == "remove" and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "manual_remove_predicted_keep", "manual remove sample predicted as clean"))
    if manual_final == "uncertain" and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "manual_uncertain_predicted_keep", "manual uncertain sample predicted as clean"))
    if row["manual_leak_check"] == "api_leak_blocking" and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "api_leak_blocking_predicted_keep", "API leak hard block predicted as clean"))
    if row["manual_semantic_alignment"] == "semantic_mismatch_uncertain" and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "semantic_mismatch_predicted_keep", "semantic mismatch predicted as clean"))
    if row["manual_candidate_gold_validity"] == "gold_wrong" and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "gold_wrong_predicted_keep", "gold_wrong predicted as clean"))
    if row["manual_task_type_check"] == "not_eligible" and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "not_eligible_predicted_keep", "not_eligible predicted as clean"))
    if is_service_level(row["task_type"]) and int(row["candidate_service_count"]) <= int(row["gold_service_count"]) and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "service_choice_space_violation", "service-level choice-space violation predicted as clean"))
    if is_api_level(row["task_type"]) and int(row["candidate_api_count"]) <= int(row["gold_api_count"]) and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "api_choice_space_violation", "API-level choice-space violation predicted as clean"))
    if not parse_ok and predicted == "rule_keep_candidate":
        out.append(issue_row(row, "json_parse_failed_predicted_keep", "JSON parse failure predicted as clean"))
    return out


def detect_warnings(row: dict[str, Any]) -> list[dict[str, Any]]:
    predicted = row["predicted_rule_decision"]
    manual_final = row["manual_final_decision"]
    out: list[dict[str, Any]] = []
    if manual_final == "keep_for_cleaning_candidate" and predicted == "rule_uncertain":
        out.append(issue_row(row, "manual_keep_predicted_uncertain", "manual keep sample predicted uncertain"))
    if manual_final == "keep_for_cleaning_candidate" and predicted == "rule_remove":
        out.append(issue_row(row, "manual_keep_predicted_remove", "manual keep sample predicted remove"))
    if manual_final == "uncertain" and predicted == "rule_remove":
        out.append(issue_row(row, "manual_uncertain_predicted_remove", "manual uncertain sample predicted remove"))
    if manual_final == "remove" and predicted == "rule_uncertain":
        out.append(issue_row(row, "manual_remove_predicted_uncertain", "manual remove sample predicted uncertain"))
    return out


def md_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def pct(n: int, d: int) -> str:
    return f"{n / d * 100:.1f}%" if d else "0.0%"


def crosstab(rows: list[dict[str, Any]], row_key: str, col_key: str) -> dict[str, Counter[str]]:
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        table[str(row.get(row_key, ""))][str(row.get(col_key, ""))] += 1
    return dict(table)


def write_report(
    predictions: list[dict[str, Any]],
    comparison: list[dict[str, Any]],
    fatal_errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    unmatched: list[dict[str, Any]],
    merge_notes: dict[str, Any],
) -> None:
    final_dist = Counter(row["manual_final_decision"] for row in predictions)
    pred_dist = Counter(row["predicted_rule_decision"] for row in predictions)
    fatal_dist = Counter(row["issue_type"] for row in fatal_errors)
    warning_dist = Counter(row["issue_type"] for row in warnings)
    table = crosstab(comparison, "manual_final_decision", "predicted_rule_decision")
    cross_rows = []
    for manual, counts in sorted(table.items()):
        cross_rows.append(
            [
                manual,
                counts.get("rule_keep_candidate", 0),
                counts.get("rule_uncertain", 0),
                counts.get("rule_remove", 0),
            ]
        )

    fatal_detail = "None" if not fatal_errors else md_table(
        ["review_id", "task_id", "issue_type", "message"],
        [[r["review_id"], r["task_id"], r["issue_type"], r["message"]] for r in fatal_errors],
    )
    warning_detail = "None" if not warnings else md_table(
        ["issue_type", "count"],
        [[k, v] for k, v in sorted(warning_dist.items())],
    )
    passed = len(fatal_errors) == 0
    lines = [
        "# Main Four Tasks Rule Validation Manual40 v0.3 Report",
        "",
        "## 本阶段做了什么",
        "",
        "实现并运行一个验证优先的 fail-closed rule validator，只在 user-approved manual40 validation set 上测试规则是否安全。",
        "",
        "## 为什么这是 rule validation，不是 full cleaning",
        "",
        "本阶段只验证规则是否会错误地把人工 uncertain/remove 或 hard-block 样本放入 clean，不生成正式清洗数据，不做 baseline，不训练模型，不 split。",
        "",
        "## 输入文件",
        "",
        f"- `{MANUAL_GOLD_CSV}`",
        f"- `{APPROVED_CSV}`",
        f"- `{DRYRUN_SERVICE_CSV}`",
        f"- `{DRYRUN_API_CSV}`",
        "",
        "## 输出文件",
        "",
        f"- `{PREDICTIONS_CSV}`",
        f"- `{COMPARISON_CSV}`",
        f"- `{FATAL_ERRORS_CSV}`",
        f"- `{WARNINGS_CSV}`",
        f"- `{UNMATCHED_CSV}`",
        "",
        "## 样本数量",
        "",
        f"- merged manual40 rows: {len(predictions)}",
        f"- unmatched rows: {len(unmatched)}",
        "",
        "## manual final decision 分布",
        "",
        md_table(["manual_final_decision", "count", "rate"], [[k, v, pct(v, len(predictions))] for k, v in sorted(final_dist.items())]),
        "",
        "## predicted rule decision 分布",
        "",
        md_table(["predicted_rule_decision", "count", "rate"], [[k, v, pct(v, len(predictions))] for k, v in sorted(pred_dist.items())]),
        "",
        "## fatal error 数量",
        "",
        f"- fatal_error_count: {len(fatal_errors)}",
        "",
        "## warning 数量",
        "",
        f"- warning_count: {len(warnings)}",
        "",
        "## 规则通过标准",
        "",
        "- fatal error = 0",
        "- 不能把人工 uncertain/remove 样本预测为 clean",
        "- 不能把 hard block 样本预测为 clean",
        "- warning 可以存在，但必须解释",
        "",
        "## fatal errors 详情",
        "",
        fatal_detail,
        "",
        "## warnings 详情",
        "",
        warning_detail,
        "",
        "## manual vs rule 交叉统计",
        "",
        md_table(["manual_final_decision", "rule_keep_candidate", "rule_uncertain", "rule_remove"], cross_rows),
        "",
        "## 合并策略说明",
        "",
        "- 人工记录唯一键使用 `review_id`。",
        "- 合并 dry-run 原始数据使用 `task_id + task_type`。",
        "- 允许同一 `task_id` 同时出现在 service-level 和 API-level 样本中。",
        f"- duplicate task_id count: {merge_notes['duplicate_task_id_count']}",
        f"- merged row count: {merge_notes['merged_row_count']}",
        f"- unmatched row count: {merge_notes['unmatched_row_count']}",
        "",
        "## 结论",
        "",
        f"- 是否通过 manual40 rule validation：{'是' if passed else '否'}",
        f"- 是否可以进入下一轮 small dry-run：{'可以' if passed else '不可以，需先修规则'}",
        "- 是否可以 full cleaning：不可以",
        "- 是否可以 baseline：不可以",
    ]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_next_step(fatal_errors: list[dict[str, Any]], warning_rows: list[dict[str, Any]]) -> None:
    fatal_count = len(fatal_errors)
    lines = [
        "# Main Four Tasks Rule Validation Manual40 v0.3 Next Step",
        "",
        "## 1. 如果 fatal error = 0，下一步怎么做",
        "",
        "可以进入第二轮 small dry-run 设计，但仍然不能 full cleaning。第二轮输出仍需人工审核。",
        "",
        "## 2. 如果 fatal error > 0，下一步怎么修规则",
        "",
        "必须先定位 fatal error 对应 hard block，修正规则后重新运行 manual40 validation，直到 fatal error 为 0。",
        "",
        "## 3. 是否可以进入第二轮 small dry-run",
        "",
        f"{'可以进入第二轮 small dry-run。' if fatal_count == 0 else '不可以，需先修复 fatal errors。'}",
        "",
        "## 4. 第二轮 small dry-run 应该抽多少",
        "",
        "- multi_service_discovery 40 条",
        "- multi_api_recommendation 40 条",
        "",
        "## 5. 第二轮样本应该如何抽",
        "",
        "- 排除 manual40 中已有 `review_id` 对应的 `task_id + task_type`。",
        "- 优先抽取规则认为 high-confidence keep candidate 的样本。",
        "- 同时抽取一部分 `rule_uncertain` 样本作为边界样本。",
        "- 覆盖不同 `source_group`、`leak_status`、candidate/gold 数量组合。",
        "- 覆盖 generic tracking/address 高风险样本。",
        "- 覆盖 `service_leak_only` 样本。",
        "- 覆盖 `candidate_service_count` 接近 `gold_service_count` 的边界样本。",
        "",
        "## 6. 第二轮是否需要继续使用交互式 HTML 审核页面",
        "",
        "需要。HTML 页面应继续保留中英对照、Service/API hierarchy view、rule hints、导出 CSV 功能。",
        "",
        "## 7. 是否可以 full cleaning",
        "",
        "不可以。至少要完成第二轮 small dry-run 和人工审核后再讨论。",
        "",
        "## 8. 是否可以 baseline",
        "",
        "不可以。benchmark 数据和清洗规则尚未稳定。",
        "",
        "## 9. single_service 和 single_api 是否继续暂缓",
        "",
        "建议继续暂缓 single_service；single_api 可作为后续补充，但要先验证 API-level choice space。",
        "",
        "## 10. 哪些内容可以汇报给导师",
        "",
        "- 已完成 manual40 user-approved validation set。",
        "- 已实现 fail-closed rule validator。",
        f"- manual40 rule validation fatal error 数量：{fatal_count}。",
        f"- warning 数量：{len(warning_rows)}。",
        "- 当前仍不建议 full cleaning 或 baseline。",
    ]
    NEXT_STEP_MD.write_text("\n".join(lines), encoding="utf-8")


def archive_outputs() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    paths = [
        Path(__file__),
        PREDICTIONS_CSV,
        COMPARISON_CSV,
        FATAL_ERRORS_CSV,
        WARNINGS_CSV,
        UNMATCHED_CSV,
        REPORT_MD,
        NEXT_STEP_MD,
        SUMMARY_JSON,
    ]
    for path in paths:
        if not path.exists():
            continue
        rel = path.relative_to(PROJECT_ROOT)
        target = ARCHIVE_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main() -> None:
    missing = [str(path) for path in REQUIRED_INPUTS if not path.exists()]
    if missing:
        print(json.dumps({"missing_required_inputs": missing}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    manual_rows, _ = read_csv(MANUAL_GOLD_CSV)
    approved_rows, _ = read_csv(APPROVED_CSV)
    service_rows, _ = read_csv(DRYRUN_SERVICE_CSV)
    api_rows, _ = read_csv(DRYRUN_API_CSV)

    approved_by_review = {row["review_id"]: row for row in approved_rows}
    dryrun_rows = service_rows + api_rows
    dryrun_by_key: dict[tuple[str, str], dict[str, str]] = {}
    duplicate_keys: list[dict[str, str]] = []
    for row in dryrun_rows:
        key = (row.get("task_id", ""), row.get("task_type", ""))
        if key in dryrun_by_key:
            duplicate_keys.append({"task_id": key[0], "task_type": key[1]})
        else:
            dryrun_by_key[key] = row

    task_id_counts = Counter(row.get("task_id", "") for row in manual_rows)
    duplicate_task_ids = {task_id: count for task_id, count in task_id_counts.items() if count > 1 and task_id}

    predictions: list[dict[str, Any]] = []
    comparison: list[dict[str, Any]] = []
    fatal_errors: list[dict[str, Any]] = []
    warning_rows: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for manual in manual_rows:
        review_id = manual.get("review_id", "")
        task_id = manual.get("task_id", "")
        task_type = manual.get("task_type", "")
        approved = approved_by_review.get(review_id, {})
        dryrun = dryrun_by_key.get((task_id, task_type), {})
        if not approved or not dryrun:
            unmatched.append(
                {
                    "review_id": review_id,
                    "task_id": task_id,
                    "task_type": task_type,
                    "missing_approved_by_review_id": "yes" if not approved else "no",
                    "missing_dryrun_by_task_id_task_type": "yes" if not dryrun else "no",
                }
            )
            continue

        counts, parse_ok, parse_warnings = counts_from_row(dryrun, review_id)
        row = {
            **manual,
            "source_group": approved.get("source_group", dryrun.get("source_group", "")),
            "original_leak_status": dryrun.get("leak_status", approved.get("leak_status", "")),
            "candidate_service_count": counts["candidate_service_count"],
            "gold_service_count": counts["gold_service_count"],
            "candidate_api_count": counts["candidate_api_count"],
            "gold_api_count": counts["gold_api_count"],
        }
        predicted_rule_decision, predicted_cleaning_bucket, predicted_reason = predict_rule(row, counts, parse_ok)
        row.update(
            {
                "predicted_rule_decision": predicted_rule_decision,
                "predicted_cleaning_bucket": predicted_cleaning_bucket,
                "predicted_reason": predicted_reason,
            }
        )
        row_fatal_errors = detect_fatal_errors(row, parse_ok)
        row_warnings = detect_warnings(row)
        for warning in parse_warnings:
            warning.update(
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "manual_final_decision": manual.get("manual_final_decision", ""),
                    "predicted_rule_decision": predicted_rule_decision,
                }
            )
        row_warnings.extend(parse_warnings)
        row.update(
            {
                "is_fatal_error": "yes" if row_fatal_errors else "no",
                "is_warning": "yes" if row_warnings else "no",
                "validation_status": "fatal_error" if row_fatal_errors else ("warning" if row_warnings else "pass"),
            }
        )
        predictions.append({field: row.get(field, "") for field in PREDICTION_FIELDS})
        expected = manual_expected_rule_decision(manual.get("manual_final_decision", ""))
        comparison_row = {field: row.get(field, "") for field in COMPARISON_FIELDS}
        comparison_row["manual_expected_rule_decision"] = expected
        comparison_row["manual_vs_rule_match"] = "yes" if predicted_rule_decision == expected else "no"
        comparison_row["comparison_note"] = (
            "matches expected fail-closed mapping"
            if comparison_row["manual_vs_rule_match"] == "yes"
            else "manual and rule differ; inspect warning/fatal status"
        )
        comparison.append(comparison_row)
        fatal_errors.extend(row_fatal_errors)
        warning_rows.extend(row_warnings)

    write_csv(UNMATCHED_CSV, unmatched, ["review_id", "task_id", "task_type", "missing_approved_by_review_id", "missing_dryrun_by_task_id_task_type"])
    if unmatched or len(predictions) != 40:
        write_csv(PREDICTIONS_CSV, predictions, PREDICTION_FIELDS)
        print(
            json.dumps(
                {
                    "error": "Merged manual40 row count is not 40 or unmatched rows exist; stopping validation.",
                    "merged_rows": len(predictions),
                    "unmatched_rows": len(unmatched),
                    "unmatched_csv": str(UNMATCHED_CSV),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)

    write_csv(PREDICTIONS_CSV, predictions, PREDICTION_FIELDS)
    write_csv(COMPARISON_CSV, comparison, COMPARISON_FIELDS)
    write_csv(FATAL_ERRORS_CSV, fatal_errors, ISSUE_FIELDS)
    write_csv(WARNINGS_CSV, warning_rows, ISSUE_FIELDS)

    merge_notes = {
        "merged_row_count": len(predictions),
        "unmatched_row_count": len(unmatched),
        "duplicate_task_id_count": len(duplicate_task_ids),
        "duplicate_task_ids": duplicate_task_ids,
        "duplicate_dryrun_key_count": len(duplicate_keys),
        "used_manual_key": "review_id",
        "used_dryrun_merge_key": "task_id + task_type",
    }
    write_report(predictions, comparison, fatal_errors, warning_rows, unmatched, merge_notes)
    write_next_step(fatal_errors, warning_rows)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "found_all_required_inputs": True,
        "manual40_rows": len(manual_rows),
        "merged_rows": len(predictions),
        "unmatched_rows": len(unmatched),
        "manual_final_decision_distribution": dict(sorted(Counter(r["manual_final_decision"] for r in predictions).items())),
        "predicted_rule_decision_distribution": dict(sorted(Counter(r["predicted_rule_decision"] for r in predictions).items())),
        "predicted_cleaning_bucket_distribution": dict(sorted(Counter(r["predicted_cleaning_bucket"] for r in predictions).items())),
        "fatal_error_count": len(fatal_errors),
        "warning_count": len(warning_rows),
        "passed_manual40_rule_validation": len(fatal_errors) == 0,
        "can_enter_second_small_dryrun": len(fatal_errors) == 0,
        "can_full_cleaning_now": False,
        "can_baseline_now": False,
        "merge_notes": merge_notes,
        "outputs": {
            "predictions_csv": str(PREDICTIONS_CSV),
            "comparison_csv": str(COMPARISON_CSV),
            "fatal_errors_csv": str(FATAL_ERRORS_CSV),
            "warnings_csv": str(WARNINGS_CSV),
            "unmatched_csv": str(UNMATCHED_CSV),
            "report_md": str(REPORT_MD),
            "next_step_md": str(NEXT_STEP_MD),
            "archive_dir": str(ARCHIVE_DIR),
        },
        "guardrails": {
            "full_cleaning": False,
            "baseline": False,
            "training": False,
            "split": False,
            "top200": False,
            "full_g3_search": False,
            "auto_modify_manual_labels": False,
            "force_uncertain_to_clean": False,
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_outputs()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
