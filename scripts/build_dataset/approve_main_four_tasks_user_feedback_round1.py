#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Freeze the approved round1 manual audit decisions for the 40-row review set.

This only records the user's approval of the existing round1 manual-review
draft. It does not run full cleaning, baseline, training, splitting, top200
expansion, or full G3 search.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVIEW_DIR = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2"
DOCS_DIR = PROJECT_ROOT / "docs" / "phase1"
INPUT_CSV = REVIEW_DIR / "main_four_tasks_manual_decisions_40_user_feedback_round1.csv"
ROUND1_CHANGES_CSV = REVIEW_DIR / "main_four_tasks_user_feedback_round1_changes.csv"
ROUND1_SUMMARY_JSON = REVIEW_DIR / "main_four_tasks_user_feedback_round1_summary.json"

APPROVED_CSV = REVIEW_DIR / "main_four_tasks_manual_decisions_40_user_approved_round1.csv"
APPROVED_SUMMARY_JSON = REVIEW_DIR / "main_four_tasks_user_approved_round1_summary.json"
APPROVED_REPORT_MD = DOCS_DIR / "main_four_tasks_user_approved_round1_report.md"
ARCHIVE_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "run_archives"
    / "2026-06-26_main_four_tasks_user_approved_round1_v0_2"
)


APPROVAL_NOTE = "user approved round1 after reviewing assistant draft and round1 revisions"


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def distribution(rows: list[dict[str, str]], column: str) -> dict[str, int]:
    return dict(sorted(Counter(row.get(column, "") for row in rows).items()))


def write_report(summary: dict[str, object], path: Path) -> None:
    lines = [
        "# Main Four Tasks User Approved Round1 Report",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Project root: `{PROJECT_ROOT}`",
        f"- Approved CSV: `{APPROVED_CSV}`",
        f"- Archive directory: `{ARCHIVE_DIR}`",
        "",
        "## 本次做了什么",
        "",
        "将 40 条 main-four-tasks manual check 的 round1 修订结果固化为 user-approved 版本。",
        "本步骤只记录人工审批结果，不执行 full cleaning、baseline、训练、split、top200 扩展或 full G3 重新搜索。",
        "",
        "## 人工审批状态",
        "",
        "- 用户审批：已批准 round1",
        f"- 审批说明：{APPROVAL_NOTE}",
        f"- 总行数：{summary['row_count']}",
        "",
        "## final decision 分布",
        "",
        "```json",
        json.dumps(summary["manual_final_decision_distribution"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## semantic alignment 分布",
        "",
        "```json",
        json.dumps(summary["manual_semantic_alignment_distribution"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 已批准的关键修订方向",
        "",
        "- 对泛化邮编/地址查询但 gold 绑定 CEP Brazil 的样本，保守降为 uncertain。",
        "- 对泛化包裹/邮件追踪但 gold 绑定特定国家、地区、邮政或 container tracking 的样本，保守降为 uncertain。",
        "- 对只有一个 candidate service 的 G1 样本，不作为 service discovery，但保留为 API-level recommendation 候选。",
        "- 对 R040，按人工反馈从 uncertain 调整为 keep_for_cleaning_candidate。",
        "",
        "## 当前边界",
        "",
        "- 没有跑 full cleaning。",
        "- 没有做 baseline。",
        "- 没有训练模型。",
        "- 没有 split。",
        "- 没有继续 top200。",
        "- 没有重新搜索 full G3。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    rows, fieldnames = read_csv(INPUT_CSV)
    approved_at = datetime.now().isoformat(timespec="seconds")

    extra_fields = [
        "user_approval_status",
        "user_approval_round",
        "user_approval_note",
        "user_approval_time",
    ]
    for field in extra_fields:
        if field not in fieldnames:
            fieldnames.append(field)

    for row in rows:
        row["user_approval_status"] = "approved"
        row["user_approval_round"] = "round1"
        row["user_approval_note"] = APPROVAL_NOTE
        row["user_approval_time"] = approved_at

    write_csv(APPROVED_CSV, rows, fieldnames)

    summary: dict[str, object] = {
        "generated_at": approved_at,
        "input_csv": str(INPUT_CSV),
        "approved_csv": str(APPROVED_CSV),
        "approved_report_md": str(APPROVED_REPORT_MD),
        "archive_dir": str(ARCHIVE_DIR),
        "row_count": len(rows),
        "user_approval_status": "approved",
        "user_approval_round": "round1",
        "user_approval_note": APPROVAL_NOTE,
        "manual_final_decision_distribution": distribution(rows, "manual_final_decision"),
        "manual_semantic_alignment_distribution": distribution(rows, "manual_semantic_alignment"),
        "manual_leak_check_distribution": distribution(rows, "manual_leak_check"),
        "review_source_distribution": distribution(rows, "review_source"),
        "guardrails": {
            "full_cleaning": False,
            "baseline": False,
            "training": False,
            "split": False,
            "top200": False,
            "full_g3_search": False,
        },
    }

    APPROVED_SUMMARY_JSON.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(summary, APPROVED_REPORT_MD)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in [
        INPUT_CSV,
        ROUND1_CHANGES_CSV,
        ROUND1_SUMMARY_JSON,
        APPROVED_CSV,
        APPROVED_SUMMARY_JSON,
        APPROVED_REPORT_MD,
        Path(__file__),
    ]:
        if path.exists():
            shutil.copy2(path, ARCHIVE_DIR / path.name)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
