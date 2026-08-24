#!/usr/bin/env python
"""Analyze the filled strong-composable manual confirmation table."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


VALID_VALUES = {
    "strong_composable_final_label": {
        "strong_composable",
        "ordinary_multi",
        "ambiguous",
        "not_eligible",
    },
    "semantic_alignment_manual_check": {
        "semantic_alignment_ok",
        "semantic_alignment_uncertain",
        "semantic_mismatch_uncertain",
    },
    "leakage_manual_check": {
        "no_blocking_leak",
        "api_leak_blocking",
        "service_leak_only",
        "leak_uncertain",
    },
}


def read_rows(path: Path) -> tuple[List[Dict[str, str]], List[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader), list(reader.fieldnames or [])


def distribution(rows: List[Dict[str, str]], column: str) -> Dict[str, int]:
    return dict(Counter((row.get(column, "") or "").strip() or "<blank>" for row in rows))


def task_id(row: Dict[str, str]) -> str:
    return row.get("task_id") or row.get("original_task_id") or row.get("raw_record_id") or ""


def one_line(value: str | None, limit: int = 180) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ")[:limit]


def analyze(input_path: Path, summary_path: Path, report_path: Path) -> Dict[str, Any]:
    rows, fieldnames = read_rows(input_path)
    missing_by_col = {
        column: sum(1 for row in rows if not (row.get(column, "") or "").strip())
        for column in VALID_VALUES
    }
    missing_by_col["strong_composable_decision_reason"] = sum(
        1 for row in rows if not (row.get("strong_composable_decision_reason", "") or "").strip()
    )

    invalid_values: Dict[str, List[Dict[str, Any]]] = {}
    for column, allowed_values in VALID_VALUES.items():
        invalid_values[column] = []
        for row_number, row in enumerate(rows, start=1):
            value = (row.get(column, "") or "").strip()
            if value and value not in allowed_values:
                invalid_values[column].append(
                    {
                        "row_number": row_number,
                        "task_id": task_id(row),
                        "value": value,
                    }
                )

    ordinary_rows = [
        row
        for row in rows
        if (row.get("strong_composable_final_label", "") or "").strip() == "ordinary_multi"
    ]
    not_eligible_rows = [
        row
        for row in rows
        if (row.get("strong_composable_final_label", "") or "").strip() == "not_eligible"
    ]
    strong_rows = [
        row
        for row in rows
        if (row.get("strong_composable_final_label", "") or "").strip() == "strong_composable"
    ]
    issue_rows = [
        row
        for row in rows
        if (row.get("semantic_alignment_manual_check", "") or "").strip() != "semantic_alignment_ok"
        or (row.get("leakage_manual_check", "") or "").strip() != "no_blocking_leak"
    ]

    summary: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_file": str(input_path),
        "row_count": len(rows),
        "columns": fieldnames,
        "completion": {
            "missing_by_col": missing_by_col,
            "all_required_filled": all(value == 0 for value in missing_by_col.values()),
        },
        "invalid_values": invalid_values,
        "strong_composable_final_label_distribution": distribution(
            rows, "strong_composable_final_label"
        ),
        "semantic_alignment_manual_check_distribution": distribution(
            rows, "semantic_alignment_manual_check"
        ),
        "leakage_manual_check_distribution": distribution(rows, "leakage_manual_check"),
        "confirmed_strong_composable_count": len(strong_rows),
        "ordinary_multi_task_ids": [task_id(row) for row in ordinary_rows],
        "not_eligible_task_ids": [task_id(row) for row in not_eligible_rows],
        "leakage_or_semantic_issue_task_ids": [task_id(row) for row in issue_rows],
        "conclusion": (
            "当前 dry-run audit 样本不足以构建 composable 主任务，需要从原始 ToolBench full G3 扩大搜索。"
            if not strong_rows
            else "dry-run audit 中已有 strong_composable 正例，但仍需扩大验证。"
        ),
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    ordinary_lines = [
        f"- `{task_id(row)}`: {one_line(row.get('query_text'))}" for row in ordinary_rows[:8]
    ]
    not_eligible_lines = []
    for row in not_eligible_rows:
        reason = one_line(
            row.get("strong_composable_decision_reason") or row.get("final_decision_reason"),
            180,
        )
        not_eligible_lines.append(
            f"- `{task_id(row)}`: {one_line(row.get('query_text'))}; 原因：{reason}"
        )

    issue_lines = [
        (
            f"- `{task_id(row)}`: semantic=`{row.get('semantic_alignment_manual_check', '')}`, "
            f"leakage=`{row.get('leakage_manual_check', '')}`, "
            f"{one_line(row.get('query_text'), 140)}"
        )
        for row in issue_rows[:12]
    ]

    invalid_count = {column: len(values) for column, values in invalid_values.items()}
    report = f"""# Strong Composable 候选 16 条人审结果分析

## 【本次做了什么】
读取人工填写文件 `{input_path.name}`，统计 16 条 dry-run strong composable 候选的人审标签、语义对齐标签和泄露标签，并判断 dry-run audit 是否足以支撑 composable 主任务。

## 【16 条是否全部填写完整】
- 样本数：{len(rows)}
- 必填人工列空值统计：`{json.dumps(missing_by_col, ensure_ascii=False)}`
- 结论：{'全部填写完整。' if all(value == 0 for value in missing_by_col.values()) else '仍存在未填写字段，需要回填后再作为最终证据。'}

## 【是否存在非法取值】
- 非法取值统计：`{json.dumps(invalid_count, ensure_ascii=False)}`
- 结论：{'未发现非法取值。' if all(value == 0 for value in invalid_count.values()) else '发现非法取值，需要先修正。'}

## 【strong_composable_final_label 分布】
`{json.dumps(summary['strong_composable_final_label_distribution'], ensure_ascii=False)}`

## 【semantic_alignment_manual_check 分布】
`{json.dumps(summary['semantic_alignment_manual_check_distribution'], ensure_ascii=False)}`

## 【leakage_manual_check 分布】
`{json.dumps(summary['leakage_manual_check_distribution'], ensure_ascii=False)}`

## 【最终是否确认 strong_composable】
确认 strong_composable 数量：{len(strong_rows)}。

{'当前 dry-run audit 样本不足以构建 composable 主任务，需要从原始 ToolBench full G3 扩大搜索。' if not strong_rows else 'dry-run audit 中存在 strong_composable 正例，但数量仍不足，需要扩大验证。'}

## 【为什么 dry-run audit 不足以支撑 composable 主任务】
这 16 条候选全部来自 dry-run audit 的小样本范围，人工标签显示没有 strong_composable 正例：多数样本只是多个需求并列完成，或者存在 API leak、service leak、语义不匹配等不可直接进入主任务的问题。因此，dry-run 只能验证规则方向，不能证明 ToolBench G3 中有足够可用的强组合任务。

## 【哪些样本是 ordinary_multi】
{chr(10).join(ordinary_lines) if ordinary_lines else '- 无'}

## 【哪些样本是 not_eligible】
{chr(10).join(not_eligible_lines) if not_eligible_lines else '- 无'}

## 【哪些样本存在 leakage 或 semantic mismatch】
{chr(10).join(issue_lines) if issue_lines else '- 无'}

## 【是否需要扩大 full G3 搜索】
需要。下一步应从原始 ToolBench full G3 中按依赖信号扩大搜索，优先找“后一步需要前一步结果”的候选，再交给人工确认 strong_composable / ordinary_multi / ambiguous / not_eligible。
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default=(
            "outputs/toolbench_full_raw_v0_1_streaming_dryrun_audit_v3_2_"
            "strong_composable_search/strong_composable_candidate_search_filled.csv"
        ),
    )
    parser.add_argument(
        "--summary-output",
        default=(
            "outputs/toolbench_full_raw_v0_1_streaming_dryrun_audit_v3_2_"
            "strong_composable_search/strong_composable_candidate_search_filled_summary.json"
        ),
    )
    parser.add_argument(
        "--report-output",
        default="docs/phase1/strong_composable_candidate_search_filled_analysis_report.md",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = analyze(Path(args.input), Path(args.summary_output), Path(args.report_output))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
