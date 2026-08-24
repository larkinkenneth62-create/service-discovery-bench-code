from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path("outputs/external_source_recovery_v1_5f_pre")
DOC_DIR = Path("docs/phase1")


EXPECTED_FILES = {
    "ToolBench": [
        "external_sources/ToolBench/data/instruction/G1_query.json",
        "external_sources/ToolBench/data/instruction/G2_query.json",
        "external_sources/ToolBench/data/instruction/G3_query.json",
        "external_sources/ToolBench/data/test_instruction/G1_instruction.json",
        "external_sources/ToolBench/data/test_instruction/G2_instruction.json",
        "external_sources/ToolBench/data/test_instruction/G3_instruction.json",
        "external_sources/ToolBench/data/answer",
        "external_sources/ToolBench/reproduction_data",
    ],
    "StableToolBench": [
        "external_sources/StableToolBench/solvable_queries/test_instruction/G1_instruction.json",
        "external_sources/StableToolBench/solvable_queries/test_instruction/G2_instruction.json",
        "external_sources/StableToolBench/solvable_queries/test_instruction/G3_instruction.json",
    ],
    "MetaTool": [
        "external_sources/MetaTool/dataset/data/all_clean_data.csv",
        "external_sources/MetaTool/dataset/plugin_des.json",
    ],
    "ShortcutsBench": [
        "external_sources/ShortcutsBench/generated_success_queries.json",
        "external_sources/ShortcutsBench/1_final_detailed_records_filter_apis_leq_30.json",
        "external_sources/ShortcutsBench/4_api_json_filter.json",
        "external_sources/ShortcutsBench/generated_success_queries.json.extracted",
        "external_sources/ShortcutsBench/1_final_detailed_records_filter_apis_leq_30.json.extracted",
        "external_sources/ShortcutsBench/4_api_json_filter.json.extracted",
    ],
}

ALT_NAMES = [
    "all_clean_data.csv",
    "plugin_des.json",
    "G1_instruction.json",
    "G2_instruction.json",
    "G3_instruction.json",
    "solvable_queries",
    "StableToolBench",
    "MetaTool",
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def fast_count(path: Path) -> tuple[Any, str]:
    if path.is_dir():
        try:
            return sum(1 for _ in path.iterdir()), "directory_child_count"
        except Exception as exc:
            return "", f"unreadable_directory: {exc}"
    if path.suffix.lower() == ".csv":
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                next(reader, None)
                return sum(1 for _ in reader), "csv_data_rows"
        except Exception as exc:
            return "", f"unreadable_csv: {exc}"
    if path.suffix.lower() == ".json" or ".json." in path.name:
        if path.stat().st_size > 120_000_000:
            return "", "json_count_skipped_large_file"
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, (list, dict)):
                return len(data), f"json_{type(data).__name__}_count"
            return "", f"json_type_{type(data).__name__}"
        except Exception as exc:
            return "", f"unreadable_json: {type(exc).__name__}: {str(exc)[:120]}"
    return "", "count_not_applicable"


def find_alternate(root: Path, expected: str) -> Path | None:
    name = Path(expected).name
    if name not in ALT_NAMES and name.replace(".extracted", "") not in ALT_NAMES:
        return None
    matches = []
    for path in root.rglob(name):
        if path.is_file() or path.is_dir():
            matches.append(path)
            if len(matches) >= 5:
                break
    return matches[0] if matches else None


def inventory(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, expected_files in EXPECTED_FILES.items():
        for expected in expected_files:
            path = root / expected
            actual = path
            status = "present" if path.exists() else "missing"
            notes = ""
            if not path.exists():
                alt = find_alternate(root, expected)
                if alt:
                    actual = alt
                    status = "found_alternate"
                    notes = "expected path missing; alternate with same name found"
            exists = actual.exists()
            count, count_note = fast_count(actual) if exists else ("", "")
            if exists and count_note.startswith("unreadable"):
                status = "unreadable"
                notes = count_note
            elif count_note:
                notes = (notes + "; " if notes else "") + count_note
            rows.append(
                {
                    "source_name": source,
                    "expected_file": expected,
                    "exists": str(exists).lower(),
                    "actual_path": str(actual) if exists else "",
                    "file_size_bytes": actual.stat().st_size if exists and actual.is_file() else "",
                    "row_count_or_json_count_if_fast": count,
                    "status": status,
                    "notes": notes,
                }
            )
    return rows


def source_present(rows: list[dict[str, Any]], source: str, required: list[str]) -> bool:
    by_expected = {row["expected_file"]: row for row in rows if row["source_name"] == source}
    return all(by_expected.get(path, {}).get("status") in {"present", "found_alternate"} for path in required)


def write_outputs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_dir(OUTPUT_DIR)
    json_path = OUTPUT_DIR / "external_source_inventory.json"
    csv_path = OUTPUT_DIR / "external_source_inventory.csv"
    fieldnames = [
        "source_name",
        "expected_file",
        "exists",
        "actual_path",
        "file_size_bytes",
        "row_count_or_json_count_if_fast",
        "status",
        "notes",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    metatool_present = source_present(rows, "MetaTool", EXPECTED_FILES["MetaTool"])
    stable_present = source_present(rows, "StableToolBench", EXPECTED_FILES["StableToolBench"])
    shortcuts_present = all(
        any(
            row["source_name"] == "ShortcutsBench"
            and row["expected_file"].endswith(name)
            and row["status"] in {"present", "found_alternate", "unreadable"}
            for row in rows
        )
        for name in [
            "generated_success_queries.json",
            "1_final_detailed_records_filter_apis_leq_30.json",
            "4_api_json_filter.json",
        ]
    )
    toolbench_present = source_present(rows, "ToolBench", EXPECTED_FILES["ToolBench"][:6])
    summary = {
        "generated_time": now_text(),
        "inventory_csv": str(csv_path),
        "ToolBench_present": toolbench_present,
        "StableToolBench_present": stable_present,
        "MetaTool_present": metatool_present,
        "ShortcutsBench_present": shortcuts_present,
        "can_build_metatool_adapter": metatool_present,
        "can_build_stabletoolbench_adapter": stable_present,
        "can_build_shortcutsbench_adapter": False,
        "can_generate_final_dataset_now": False,
        "rows": rows,
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# External Source Recovery Inventory v1.5f-pre",
        "",
        f"Generated time: {summary['generated_time']}",
        "",
        "## Source Status",
        "",
        f"- ToolBench present: {str(toolbench_present).lower()}",
        f"- StableToolBench present: {str(stable_present).lower()}",
        f"- MetaTool present: {str(metatool_present).lower()}",
        f"- ShortcutsBench present: {str(shortcuts_present).lower()}",
        f"- can_build_metatool_adapter: {str(metatool_present).lower()}",
        f"- can_build_stabletoolbench_adapter: {str(stable_present).lower()}",
        "- can_build_shortcutsbench_adapter: false",
        "- can_generate_final_dataset_now: false",
        "",
        "## File Inventory",
        "",
        "| source | expected_file | status | count | notes |",
        "|---|---|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['source_name']} | `{row['expected_file']}` | {row['status']} | "
            f"{row['row_count_or_json_count_if_fast'] or ''} | {row['notes']} |"
        )
    (DOC_DIR / "external_source_recovery_inventory_v1_5f_pre.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Check external source inventory for v1.5f-pre recovery.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    rows = inventory(args.project_root)
    summary = write_outputs(rows)
    print(json.dumps({k: summary[k] for k in [
        "ToolBench_present",
        "StableToolBench_present",
        "MetaTool_present",
        "ShortcutsBench_present",
        "can_build_metatool_adapter",
        "can_build_stabletoolbench_adapter",
        "can_build_shortcutsbench_adapter",
        "can_generate_final_dataset_now",
    ]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
