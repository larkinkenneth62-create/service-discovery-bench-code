#!/usr/bin/env python
"""Check inputs for Round2 v0.5 validation.

Run from project root:
    python scripts/validation/check_round2_inputs_v0_5.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from round2_v0_5_utils import (
    MANUAL40_PATH,
    OUTPUT_DIR,
    ROUND2_DRAFT_PATH,
    ROUND2_EXPECTED_HUMAN_PATH,
    column_mapping,
    ensure_dirs,
    find_round2_human_final,
    now_str,
    null_summary,
    read_csv,
    write_json,
)


def inspect_file(label: str, path: Path) -> dict:
    item = {
        "label": label,
        "path": str(path),
        "exists": path.exists(),
        "row_count": None,
        "columns": [],
        "null_summary": {},
        "column_mapping": {},
        "missing_required_semantic_columns": [],
        "read_error": "",
    }
    if not path.exists():
        return item
    try:
        columns, rows = read_csv(path)
    except Exception as exc:  # pragma: no cover - CLI error path
        item["read_error"] = str(exc)
        return item
    mapping = column_mapping(columns)
    required = [
        "sample_id",
        "task_type",
        "review_bucket",
        "source_group",
        "manual_final_decision",
        "semantic_alignment_check",
        "leakage_check",
        "candidate_validity_check",
        "task_type_check",
    ]
    item.update(
        {
            "row_count": len(rows),
            "columns": columns,
            "null_summary": null_summary(columns, rows),
            "column_mapping": mapping,
            "missing_required_semantic_columns": [
                key for key in required if mapping.get(key) is None
            ],
        }
    )
    return item


def write_report(output_dir: Path, items: list[dict], human_resolution: dict) -> Path:
    path = output_dir / "input_file_check_report.md"
    lines = [
        "# Round2 v0.5 Input File Check Report",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件路径",
        "",
    ]
    for item in items:
        lines.append(f"- {item['label']}: `{item['path']}`")
    lines.extend(
        [
            "",
            "## Round2 Human Final Resolution",
            "",
            f"- source: `{human_resolution.get('source')}`",
            f"- path: `{human_resolution.get('path')}`",
            f"- user_declared_overlay_as_human_final: `{human_resolution.get('user_declared_overlay_as_human_final', False)}`",
            f"- overlay_applied_rows: `{human_resolution.get('overlay_applied_rows', '')}`",
            f"- retained_draft_rows: `{human_resolution.get('retained_draft_rows', '')}`",
            "",
            "说明：用户已明确说明此前的 Round2 correction overlay 就是本轮人审 final。"
            "因此脚本在不覆盖原始 CSV 的前提下，生成标准化 80 行 final CSV 到 v0.5 输出目录。",
            "",
            "## 文件检查结果",
            "",
        ]
    )

    for item in items:
        lines.extend(
            [
                f"### {item['label']}",
                "",
                f"- exists: `{item['exists']}`",
                f"- sample_count: `{item['row_count']}`",
                f"- read_error: `{item.get('read_error', '')}`",
                f"- missing semantic columns: `{', '.join(item['missing_required_semantic_columns']) or 'none'}`",
                f"- columns: `{', '.join(item['columns'])}`",
                "",
                "| column | empty_count |",
                "|---|---:|",
            ]
        )
        nulls = item.get("null_summary") or {}
        if nulls:
            for col, count in nulls.items():
                lines.append(f"| `{col}` | {count} |")
        else:
            lines.append("| - | - |")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Round2 v0.5 validation inputs.")
    parser.add_argument("--manual40", type=Path, default=MANUAL40_PATH)
    parser.add_argument("--round2-draft", type=Path, default=ROUND2_DRAFT_PATH)
    parser.add_argument("--round2-human-final", type=Path, default=ROUND2_EXPECTED_HUMAN_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dirs()

    human_path, human_resolution = find_round2_human_final(allow_overlay=True)
    if human_path is None:
        missing_path = args.output_dir / "MISSING_ROUND2_HUMAN_FINAL.md"
        missing_path.write_text(
            "\n".join(
                [
                    "# Missing Round2 Human Final CSV",
                    "",
                    f"生成时间：{now_str()}",
                    "",
                    "未找到 Round2 human final，也没有可用 correction overlay。停止。",
                ]
            ),
            encoding="utf-8",
        )
        print(f"ERROR: missing Round2 human final. See {missing_path}")
        return 2

    items = [
        inspect_file("manual40_user_approved", args.manual40),
        inspect_file("round2_assistant_draft", args.round2_draft),
        inspect_file("round2_human_final", human_path),
    ]
    mapping_payload = {
        "generated_at": now_str(),
        "round2_human_final_resolution": human_resolution,
        "files": {
            item["label"]: {
                "path": item["path"],
                "exists": item["exists"],
                "row_count": item["row_count"],
                "column_mapping": item["column_mapping"],
                "missing_required_semantic_columns": item[
                    "missing_required_semantic_columns"
                ],
            }
            for item in items
        },
    }
    mapping_path = args.output_dir / "column_mapping.json"
    write_json(mapping_path, mapping_payload)
    report_path = write_report(args.output_dir, items, human_resolution)

    fatal = []
    for item in items:
        if not item["exists"] or item["row_count"] is None:
            fatal.append(f"{item['label']} missing or unreadable")
        required_missing = [
            col
            for col in item["missing_required_semantic_columns"]
            if col not in {"review_bucket"}
        ]
        if required_missing:
            fatal.append(
                f"{item['label']} missing required semantic columns: {required_missing}"
            )

    print(f"input_file_check_report={report_path}")
    print(f"column_mapping={mapping_path}")
    print(f"round2_human_final={human_path}")
    if fatal:
        print("ERROR: " + "; ".join(fatal))
        return 1
    print("OK: Round2 v0.5 inputs are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
