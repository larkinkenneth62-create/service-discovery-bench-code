from __future__ import annotations

import argparse
from pathlib import Path

from toolbench_v1_3_common import (
    OUTPUT_DIR,
    answer_paths,
    count_json_array_stream,
    ensure_dir,
    instruction_paths,
    locate_toolbench_root,
    now_text,
    table_lines,
    write_json,
    write_md,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check ToolBench inputs for full raw streaming v1.3.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    toolbench_root = locate_toolbench_root(root)
    missing: list[str] = []
    summary = {"generated_time": now_text(), "toolbench_root": str(toolbench_root or ""), "groups": {}, "missing": []}

    if toolbench_root is None:
        missing.append("ToolBench root with data/instruction/G1_query.json")
    else:
        tool_root = toolbench_root / "data" / "toolenv" / "tools"
        if not tool_root.exists():
            missing.append(str(tool_root))
        answers = answer_paths(toolbench_root)
        for group, answer_dir in answers.items():
            if not answer_dir.exists():
                missing.append(str(answer_dir))
        for group, path in instruction_paths(toolbench_root).items():
            if not path.exists():
                missing.append(str(path))
                continue
            count, first = count_json_array_stream(path)
            checks = {
                "query_text_available": bool(str(first.get("query", "")).strip()),
                "candidate_apis_available": bool(first.get("api_list", [])),
                "gold_apis_available": bool(first.get("relevant APIs", [])),
                "source_group_available": True,
            }
            summary["groups"][group] = {
                "instruction_path": str(path),
                "top_level_count": count,
                "first_record_keys": sorted(first.keys()),
                "first_query_id": first.get("query_id", ""),
                "first_api_list_len": len(first.get("api_list", []) or []),
                "first_relevant_api_len": len(first.get("relevant APIs", []) or []),
                "checks": checks,
                "answer_dir": str(answers.get(group, "")),
                "answer_dir_exists": answers.get(group, Path()).exists(),
            }
            for name, ok in checks.items():
                if not ok:
                    missing.append(f"{group}:{name}")

    summary["missing"] = missing
    summary["all_required_inputs_available"] = not missing
    write_json(output_dir / "input_schema_summary.json", summary)

    if missing:
        write_md(
            output_dir / "MISSING_TOOLBENCH_INPUTS.md",
            [
                "# Missing ToolBench Inputs v1.3",
                "",
                f"Generated time: {now_text()}",
                "",
                "Stopped without guessing or fabricating data.",
                "",
                *[f"- `{item}`" for item in missing],
            ],
        )

    lines = [
        "# ToolBench Full Streaming v1.3 Input Check Report",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Project root: `{root}`",
        f"ToolBench root: `{toolbench_root}`",
        "",
        "Scope: input check only. No raw dataset, cleaning, split, baseline, model training, or human review was generated.",
        "",
        "## Group instruction files",
        "",
        "| group | path | top-level count | first api count | first gold api count | answer dir exists |",
        "|---|---|---:|---:|---:|---|",
    ]
    for group, info in summary["groups"].items():
        lines.append(
            f"| {group} | `{info['instruction_path']}` | {info['top_level_count']} | {info['first_api_list_len']} | "
            f"{info['first_relevant_api_len']} | {info['answer_dir_exists']} |"
        )
    lines.extend(
        [
            "",
            "## Parse checks",
            "",
            f"- query text / candidate APIs / gold APIs parseable: `{str(not missing).lower()}`",
            f"- missing inputs: {missing if missing else 'none'}",
        ]
    )
    write_md(output_dir / "input_check_report.md", lines)

    print("Input check complete.")
    print("missing:", missing if missing else "none")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
