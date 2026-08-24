from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_CSV = (
    PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_manual_check_v0_2"
    / "main_four_tasks_manual_decisions_40_assistant_prefilled.csv"
)
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2"
DOCS_DIR = PROJECT_ROOT / "docs" / "phase1"
OUTPUT_MD = DOCS_DIR / "main_four_tasks_assistant_prefill_reasons.md"
OUTPUT_CSV = OUTPUT_DIR / "main_four_tasks_assistant_prefill_reasons.csv"
OUTPUT_SUMMARY = OUTPUT_DIR / "main_four_tasks_assistant_prefill_reasons_summary.json"
ARCHIVE_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "run_archives"
    / "2026-06-26_main_four_tasks_assistant_prefill_reasons_v0_2"
)


REASON_FIELDS = [
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
    "query_text_zh",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing input CSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def strip_assistant_prefix(reason: str) -> str:
    prefix = "assistant draft:"
    if reason.lower().startswith(prefix):
        return reason[len(prefix) :].strip()
    return reason.strip()


def count_missing(rows: list[dict[str, str]], columns: list[str]) -> dict[str, int]:
    missing: dict[str, int] = {}
    for col in columns:
        missing[col] = sum(1 for row in rows if not row.get(col, "").strip())
    return missing


def compact_json(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def write_reasons_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REASON_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in REASON_FIELDS})


def write_markdown(rows: list[dict[str, str]], summary: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Main Four Tasks Assistant Prefill Reasons")
    lines.append("")
    lines.append(f"- Generated at: {summary['generated_at']}")
    lines.append(f"- Project root: `{PROJECT_ROOT}`")
    lines.append(f"- Source CSV: `{INPUT_CSV}`")
    lines.append("- Scope: assistant draft review reasons for the 40-row dry-run manual check set.")
    lines.append("- Important: these are assistant-draft labels and reasons, not final human-confirmed labels.")
    lines.append(
        "- Guardrails: no full cleaning, no baseline, no model training, no split, no top200 expansion, and no full G3 re-search were run."
    )
    lines.append("")
    lines.append("## Overall Distribution")
    lines.append("")
    lines.append("```json")
    lines.append(
        json.dumps(
            {
                "row_count": summary["row_count"],
                "manual_final_decision_distribution": summary[
                    "manual_final_decision_distribution"
                ],
                "manual_semantic_alignment_distribution": summary[
                    "manual_semantic_alignment_distribution"
                ],
                "manual_leak_check_distribution": summary["manual_leak_check_distribution"],
                "missing_required_fields": summary["missing_required_fields"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    lines.append("```")
    lines.append("")
    lines.append("## Per-row Reasons")
    lines.append("")

    for row in rows:
        reason = strip_assistant_prefix(row.get("manual_decision_reason", ""))
        lines.append(
            f"### {row.get('review_id', '')} | {row.get('task_id', '')} | {row.get('task_type', '')}"
        )
        lines.append("")
        lines.append(f"- Source group: `{row.get('source_group', '')}`")
        lines.append(f"- Final decision: `{row.get('manual_final_decision', '')}`")
        lines.append(f"- Semantic alignment: `{row.get('manual_semantic_alignment', '')}`")
        lines.append(f"- Leak check: `{row.get('manual_leak_check', '')}`")
        lines.append(
            f"- Candidate/gold validity: `{row.get('manual_candidate_gold_validity', '')}`"
        )
        lines.append(f"- Task type check: `{row.get('manual_task_type_check', '')}`")
        lines.append(f"- Query 中文: {row.get('query_text_zh', '')}")
        lines.append(f"- 填写理由: {reason}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def copy_to_archive(paths: list[Path], archive_dir: Path) -> list[str]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in paths:
        if path.exists():
            target = archive_dir / path.name
            shutil.copy2(path, target)
            copied.append(str(target))
    return copied


def main() -> None:
    rows = read_rows(INPUT_CSV)

    required = [
        "review_id",
        "task_id",
        "task_type",
        "source_group",
        "manual_semantic_alignment",
        "manual_leak_check",
        "manual_candidate_gold_validity",
        "manual_task_type_check",
        "manual_final_decision",
        "manual_decision_reason",
        "review_completed",
    ]
    missing_required_fields = count_missing(rows, required)

    summary: dict[str, object] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_csv": str(INPUT_CSV),
        "row_count": len(rows),
        "manual_final_decision_distribution": compact_json(
            Counter(row.get("manual_final_decision", "") for row in rows)
        ),
        "manual_semantic_alignment_distribution": compact_json(
            Counter(row.get("manual_semantic_alignment", "") for row in rows)
        ),
        "manual_leak_check_distribution": compact_json(
            Counter(row.get("manual_leak_check", "") for row in rows)
        ),
        "review_completed_distribution": compact_json(
            Counter(row.get("review_completed", "") for row in rows)
        ),
        "missing_required_fields": missing_required_fields,
        "outputs": {
            "markdown": str(OUTPUT_MD),
            "csv": str(OUTPUT_CSV),
            "summary_json": str(OUTPUT_SUMMARY),
            "archive_dir": str(ARCHIVE_DIR),
        },
        "scope_guardrails": {
            "full_cleaning": False,
            "baseline": False,
            "training": False,
            "split": False,
            "top200": False,
            "full_g3_research": False,
        },
    }

    write_reasons_csv(rows, OUTPUT_CSV)
    write_markdown(rows, summary, OUTPUT_MD)

    OUTPUT_SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    prefill_report = DOCS_DIR / "main_four_tasks_assistant_prefill_report.md"
    prefill_script = (
        PROJECT_ROOT
        / "scripts"
        / "build_dataset"
        / "prefill_main_four_tasks_review_app_assistant_draft.py"
    )
    copied = copy_to_archive(
        [INPUT_CSV, OUTPUT_CSV, OUTPUT_MD, OUTPUT_SUMMARY, prefill_report, prefill_script, Path(__file__)],
        ARCHIVE_DIR,
    )
    summary["archived_files"] = copied
    OUTPUT_SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    shutil.copy2(OUTPUT_SUMMARY, ARCHIVE_DIR / OUTPUT_SUMMARY.name)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
