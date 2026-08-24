from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


TARGET_COUNTS = {
    "single_service_discovery": 20667,
    "single_api_recommendation": 49570,
    "multi_service_discovery": 36128,
    "multi_api_recommendation": 36128,
    "composable_service_discovery": 12520,
    "composable_api_recommendation": 12520,
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_md(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def table(rows: list[tuple[str, Any, Any, Any]]) -> list[str]:
    lines = ["| item | actual | target | delta |", "|---|---:|---:|---:|"]
    for name, actual, target, delta in rows:
        lines.append(f"| {name} | {actual} | {target} | {delta} |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate route rebuild v0.1 counts and source coverage.")
    parser.add_argument("--rebuild-dir", type=Path, default=Path("outputs/service_discovery_route_rebuild_v0_1"))
    args = parser.parse_args()

    summary_path = args.rebuild_dir / "route_rebuild_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing rebuild summary: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    six_dir = args.rebuild_dir / "service_discovery_six_tasks"
    six_counts: dict[str, int] = {}
    six_sources: dict[str, dict[str, int]] = {}
    for path in sorted(six_dir.glob("*.csv")):
        rows = read_csv(path)
        name = path.stem
        six_counts[name] = len(rows)
        six_sources[name] = dict(Counter(row.get("source_dataset", "") for row in rows))

    count_rows = []
    for name, target in TARGET_COUNTS.items():
        actual = six_counts.get(name, 0)
        count_rows.append((name, actual, target, actual - target))

    source_presence = {
        "ToolBench_included": summary["raw_counts"].get("ToolBench", 0) > 0,
        "StableToolBench_included": summary["raw_counts"].get("StableToolBench", 0) > 0,
        "MetaTool_included": summary["raw_counts"].get("MetaTool", 0) > 0,
        "ShortcutsBenchStrict_included": summary["raw_counts"].get("ShortcutsBenchStrict", 0) > 0,
    }

    validation = {
        "generated_time": now_text(),
        "rebuild_dir": str(args.rebuild_dir),
        "source_presence": source_presence,
        "raw_counts": summary.get("raw_counts", {}),
        "six_task_counts": six_counts,
        "six_task_source_distribution": six_sources,
        "teacher_target_counts": TARGET_COUNTS,
        "count_deltas": {name: six_counts.get(name, 0) - target for name, target in TARGET_COUNTS.items()},
        "matches_teacher_targets": all(six_counts.get(name, 0) == target for name, target in TARGET_COUNTS.items()),
        "interpretation": [
            "The rebuild now includes ToolBench, StableToolBench, MetaTool, and conservative ShortcutsBench strict samples.",
            "The actual counts do not exactly match the teacher document, so this should be treated as a reproducible local-route rebuild, not final ServiceDiscoveryBench-v0.1.",
            "Count gaps should be resolved before split or baseline by checking source versions, ToolBench test_instruction inclusion rules, ShortcutsBench strict filtering, and single_api_recommendation construction rules.",
        ],
    }
    out_json = args.rebuild_dir / "route_rebuild_validation_summary.json"
    write_json(out_json, validation)

    lines = [
        "# ServiceDiscoveryBench Route Rebuild v0.1 Count Gap Analysis",
        "",
        f"Generated time: {now_text()}",
        f"Rebuild directory: `{args.rebuild_dir}`",
        "",
        "## Source Coverage",
        "",
        f"- ToolBench included: {str(source_presence['ToolBench_included']).lower()} ({summary['raw_counts'].get('ToolBench', 0)} tasks)",
        f"- StableToolBench included: {str(source_presence['StableToolBench_included']).lower()} ({summary['raw_counts'].get('StableToolBench', 0)} tasks)",
        f"- MetaTool included: {str(source_presence['MetaTool_included']).lower()} ({summary['raw_counts'].get('MetaTool', 0)} tasks)",
        f"- ShortcutsBenchStrict included: {str(source_presence['ShortcutsBenchStrict_included']).lower()} ({summary['raw_counts'].get('ShortcutsBenchStrict', 0)} tasks before service-leak filtering)",
        "",
        "## Six Task Counts vs Teacher Route Targets",
        "",
        *table(count_rows),
        "",
        "## Six Task Source Distributions",
        "",
    ]
    for name in sorted(six_sources):
        lines.append(f"- `{name}`: `{six_sources[name]}`")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This fixes the previous ToolBench-only issue: MetaTool and StableToolBench are now actually included.",
            "- Counts still do not match the teacher document exactly, so this is not yet a final benchmark package.",
            "- The biggest open gap is rule/source-version alignment: local ToolBench+Stable internal raw is 202,104 tasks, while the teacher document reports 202,604.",
            "- ShortcutsBench strict filtering is conservative and needs manual confirmation before finalizing single_service_discovery.",
            "- Do not run split, baseline, or training from this rebuild until these gaps are resolved.",
        ]
    )
    write_md(Path("docs/phase1/service_discovery_route_rebuild_v0_1_count_gap_analysis.md"), lines)

    print(json.dumps(validation["source_presence"], ensure_ascii=False, indent=2))
    print(json.dumps(validation["six_task_counts"], ensure_ascii=False, indent=2))
    print(f"matches_teacher_targets: {validation['matches_teacher_targets']}")
    print(f"validation_json: {out_json}")
    print("report: docs/phase1/service_discovery_route_rebuild_v0_1_count_gap_analysis.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
