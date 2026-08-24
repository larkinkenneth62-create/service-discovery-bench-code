#!/usr/bin/env python
"""Check ShortcutsBench source availability and JSON shape.

This script intentionally performs source-format preflight only. It does not
build benchmark rows, clean data, split data, run baselines, or train models.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


SHORTCUTS_FILES = [
    "generated_success_queries.json",
    "generated_success_queries.json.extracted",
    "1_final_detailed_records_filter_apis_leq_30.json",
    "1_final_detailed_records_filter_apis_leq_30.json.extracted",
    "4_api_json_filter.json",
    "4_api_json_filter.json.extracted",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def first_non_ws(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        while True:
            chunk = f.read(4096)
            if not chunk:
                return ""
            for ch in chunk:
                if not ch.isspace():
                    return ch


def count_top_level_array_stream(path: Path) -> int:
    """Count top-level array elements without loading the whole JSON file."""
    count = 0
    depth = 0
    in_string = False
    escape = False
    opened_array = False
    in_element = False

    with path.open("r", encoding="utf-8", errors="replace") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            for ch in chunk:
                if not opened_array:
                    if ch.isspace():
                        continue
                    if ch != "[":
                        raise ValueError(f"Expected top-level array in {path}, got {ch!r}")
                    opened_array = True
                    continue

                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue

                if ch == '"':
                    if depth == 0 and not in_element:
                        count += 1
                        in_element = True
                    in_string = True
                    continue

                if ch in "{[":
                    if depth == 0 and not in_element:
                        count += 1
                        in_element = True
                    depth += 1
                    continue

                if ch in "}]":
                    if ch == "]" and depth == 0:
                        return count
                    depth -= 1
                    if depth < 0:
                        raise ValueError(f"Unexpected closing bracket while counting {path}")
                    continue

                if depth == 0:
                    if ch == ",":
                        in_element = False
                    elif ch.isspace():
                        continue
                    elif ch == "]":
                        return count
                    elif not in_element:
                        count += 1
                        in_element = True
    return count


def inspect_json(path: Path, stream_threshold_bytes: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "is_file": path.is_file() if path.exists() else False,
        "size_bytes": path.stat().st_size if path.exists() else None,
        "readable": False,
        "parse_status": "missing",
        "top_level_type": None,
        "item_count": None,
        "error": "",
    }
    if not result["exists"] or not result["is_file"]:
        return result

    try:
        marker = first_non_ws(path)
        result["readable"] = True
        if marker == "[" and result["size_bytes"] and result["size_bytes"] > stream_threshold_bytes:
            result["top_level_type"] = "list"
            result["item_count"] = count_top_level_array_stream(path)
            result["parse_status"] = "stream_count_ok"
            return result

        data = json.loads(path.read_text(encoding="utf-8"))
        result["parse_status"] = "json_load_ok"
        if isinstance(data, list):
            result["top_level_type"] = "list"
            result["item_count"] = len(data)
        elif isinstance(data, dict):
            result["top_level_type"] = "dict"
            result["item_count"] = len(data)
        else:
            result["top_level_type"] = type(data).__name__
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic script.
        result["parse_status"] = "parse_failed"
        result["error"] = str(exc)
    return result


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# ShortcutsBench Source Check v0.1",
        "",
        f"Generated time: {summary['generated_at']}",
        f"Input directory: `{summary['source_dir']}`",
        "",
        "## File Checks",
        "",
        "| file | exists | readable | parse_status | top_level_type | item_count | size_bytes |",
        "|---|---:|---:|---|---|---:|---:|",
    ]
    for item in summary["files"]:
        lines.append(
            f"| `{Path(item['path']).name}` | {item['exists']} | {item['readable']} | "
            f"{item['parse_status']} | {item['top_level_type'] or ''} | "
            f"{item['item_count'] if item['item_count'] is not None else ''} | "
            f"{item['size_bytes'] if item['size_bytes'] is not None else ''} |"
        )

    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This step checks ShortcutsBench source existence and JSON shape only.",
            "- It does not construct benchmark rows.",
            "- It does not run full cleaning, final dataset generation, split, baseline, or training.",
            "",
            "## Conclusion",
            "",
            f"- source_present: `{summary['source_present']}`",
            f"- extracted_files_parseable: `{summary['extracted_files_parseable']}`",
            f"- can_build_shortcutsbench_adapter_now: `{summary['can_build_shortcutsbench_adapter_now']}`",
            "- For this preflight, ShortcutsBench remains source-checked only and should not be merged into the benchmark.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check ShortcutsBench source files and JSON shape.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument(
        "--source-dir",
        default="external_sources/ShortcutsBench",
        help="ShortcutsBench source directory.",
    )
    parser.add_argument(
        "--output-json",
        default="outputs/external_sources_adapters_v0_1/shortcutsbench/shortcutsbench_source_check.json",
        help="Output JSON summary.",
    )
    parser.add_argument(
        "--report",
        default="docs/phase1/shortcutsbench_source_check_v0_1.md",
        help="Output Markdown report.",
    )
    parser.add_argument(
        "--stream-threshold-mb",
        type=int,
        default=120,
        help="Use streaming count for top-level arrays larger than this many MB.",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    source_dir = project_root / args.source_dir
    threshold = args.stream_threshold_mb * 1024 * 1024

    files = [inspect_json(source_dir / name, threshold) for name in SHORTCUTS_FILES]
    extracted = [item for item in files if str(item["path"]).endswith(".extracted")]
    summary = {
        "generated_at": now_iso(),
        "project_root": str(project_root),
        "source_dir": str(source_dir),
        "files": files,
        "source_present": source_dir.exists(),
        "extracted_files_parseable": all(item["parse_status"] in {"json_load_ok", "stream_count_ok"} for item in extracted),
        "can_build_shortcutsbench_adapter_now": False,
        "reason": "This preflight only checks source shape; ShortcutsBench strict construction requires separate route-specific filtering.",
        "forbidden_actions_observed": {
            "full_cleaning": False,
            "final_dataset": False,
            "split": False,
            "baseline": False,
            "training": False,
        },
    }

    output_json = project_root / args.output_json
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(project_root / args.report, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
