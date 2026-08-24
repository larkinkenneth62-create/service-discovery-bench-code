from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path.cwd()
VALIDATION_DIR = ROOT / "scripts" / "validation"
BUILD_DIR = ROOT / "scripts" / "build_dataset"
for path in [VALIDATION_DIR, BUILD_DIR]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import convert_toolbench_to_service_candidates as legacy  # noqa: E402
from toolbench_v1_3_common import build_task_raw_row  # noqa: E402


TASK_FIELDS = [
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "source_query_id",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "gold_in_candidate_services",
    "gold_in_candidate_apis",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "task_signature",
    "query_signature",
    "metadata_json",
]

TASK_TYPES = [
    "single_service_discovery_raw",
    "multi_service_discovery_raw",
    "composable_service_discovery_raw",
]

STRICT_SHORTCUT_KEYWORDS = [
    "chatgpt",
    "openai",
    "bing",
    "jira",
    "todoist",
    "toggl",
    "mastodon",
    "home assistant",
    "homeassistant",
    "carrot weather",
    "notion",
    "omnifocus",
    "goodreads",
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def sig(*parts: Any) -> str:
    text = "\n".join(norm(part) for part in parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def parse_json(value: Any, default: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str] = TASK_FIELDS) -> int:
    ensure_dir(path.parent)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
            count += 1
    return count


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md(path: Path, lines: list[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def table(counter: Counter[str] | dict[str, int]) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    for key, value in sorted(dict(counter).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {value} |")
    if len(lines) == 2:
        lines.append("| <empty> | 0 |")
    return lines


def names_from_gold_services(value: Any) -> list[str]:
    data = parse_json(value, [])
    out = []
    for item in data:
        if isinstance(item, dict):
            out.append(str(item.get("service_name", "") or item.get("tool_name", "") or item.get("name", "")))
        else:
            out.append(str(item))
    return [x for x in out if x]


def names_from_gold_apis(value: Any) -> list[dict[str, str]]:
    data = parse_json(value, [])
    out = []
    for item in data:
        if isinstance(item, dict):
            out.append({"service_name": str(item.get("service_name", "")), "api_name": str(item.get("api_name", ""))})
    return out


def recompute_route_signatures(row: dict[str, Any]) -> dict[str, Any]:
    gold_services = sorted(norm(x) for x in names_from_gold_services(row.get("gold_services_json")) if x)
    gold_apis = sorted(
        f"{norm(item.get('service_name'))}::{norm(item.get('api_name'))}"
        for item in names_from_gold_apis(row.get("gold_apis_json"))
    )
    row = dict(row)
    row["task_signature"] = sig(row.get("query_text", ""), json_dumps(gold_services), json_dumps(gold_apis))
    row["query_signature"] = sig(row.get("query_text", ""))
    return row


def as_int(value: Any) -> int:
    try:
        return int(str(value))
    except Exception:
        return 0


def sort_key_for_dedup(row: dict[str, Any]) -> tuple[int, int, str]:
    priority = {
        "StableToolBench": 0,
        "ToolBench": 1,
        "ToolBenchTest": 2,
        "MetaTool": 3,
        "ShortcutsBenchStrict": 4,
    }.get(str(row.get("source_dataset", "")), 9)
    source_id = str(row.get("source_query_id", ""))
    numeric = int(source_id) if source_id.isdigit() else 10**12
    return (priority, numeric, source_id)


def load_toolbench_existing(path: Path) -> list[dict[str, Any]]:
    rows = []
    for row in read_csv(path):
        rows.append(recompute_route_signatures(row))
    return rows


def stable_group_paths(root: Path) -> dict[str, Path]:
    base = root / "external_sources" / "StableToolBench" / "solvable_queries" / "test_instruction"
    return {
        "G1": base / "G1_instruction.json",
        "G2": base / "G2_instruction.json",
        "G3": base / "G3_instruction.json",
    }


def build_stable_rows(root: Path) -> list[dict[str, Any]]:
    all_tasks: list[dict[str, Any]] = []
    service_metadata: dict[Any, Any] = {}
    for group, path in stable_group_paths(root).items():
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for task in data:
            candidate_rows = list(legacy.iter_candidate_rows("StableToolBench", group, task, service_metadata))
            if not candidate_rows:
                continue
            raw = build_task_raw_row(candidate_rows, path)
            raw["metadata_json"] = json_dumps(
                {
                    **parse_json(raw.get("metadata_json"), {}),
                    "route_rebuild_source_file": str(path),
                    "route_rebuild_stage": "stabletoolbench_task_level",
                }
            )
            all_tasks.append(recompute_route_signatures(raw))
    return all_tasks


def candidate_service_names(row: dict[str, Any]) -> set[str]:
    services = parse_json(row.get("candidate_services_json"), [])
    names = set()
    for item in services:
        if isinstance(item, dict):
            names.add(norm(item.get("service_name") or item.get("tool_name") or item.get("name")))
        else:
            names.add(norm(item))
    return {name for name in names if name}


def gold_service_names(row: dict[str, Any]) -> set[str]:
    return {norm(name) for name in names_from_gold_services(row.get("gold_services_json")) if name}


def gold_api_keys(row: dict[str, Any]) -> set[tuple[str, str]]:
    return {(norm(item.get("service_name")), norm(item.get("api_name"))) for item in names_from_gold_apis(row.get("gold_apis_json"))}


def candidate_api_keys(row: dict[str, Any]) -> set[tuple[str, str]]:
    apis = parse_json(row.get("candidate_apis_json"), [])
    out = set()
    for item in apis:
        if isinstance(item, dict):
            out.add((norm(item.get("service_name")), norm(item.get("api_name"))))
    return out


def finalize_integrity_flags(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["gold_in_candidate_services"] = int(gold_service_names(row).issubset(candidate_service_names(row)))
    row["gold_in_candidate_apis"] = int(gold_api_keys(row).issubset(candidate_api_keys(row)))
    return row


def clean_internal(rows: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    by_type: dict[str, dict[str, list[dict[str, Any]]]] = {
        task_type: {"api_leak_removed": [], "deduped": [], "clean_ready": [], "service_leak_only": []}
        for task_type in TASK_TYPES
    }
    summary: dict[str, Any] = {"by_task_type": {}}
    for task_type in TASK_TYPES:
        subset = [finalize_integrity_flags(row) for row in rows if row.get("task_type") == task_type]
        api_clean = [row for row in subset if as_int(row.get("query_mentions_any_gold_api")) == 0]
        api_leak = [row for row in subset if as_int(row.get("query_mentions_any_gold_api")) == 1]
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in api_clean:
            groups[str(row.get("task_signature", ""))].append(row)
        deduped = [sorted(items, key=sort_key_for_dedup)[0] for items in groups.values()]
        service_leak = [row for row in deduped if as_int(row.get("query_mentions_any_gold_service")) == 1]
        clean_ready = [row for row in deduped if as_int(row.get("query_mentions_any_gold_service")) == 0]
        by_type[task_type] = {
            "api_leak_removed": api_leak,
            "deduped": deduped,
            "clean_ready": clean_ready,
            "service_leak_only": service_leak,
        }
        summary["by_task_type"][task_type] = {
            "raw_tasks": len(subset),
            "api_leak_removed_tasks": len(api_leak),
            "after_api_leak_removal": len(api_clean),
            "after_task_signature_dedup": len(deduped),
            "clean_ready_tasks": len(clean_ready),
            "service_leak_only_tasks": len(service_leak),
            "source_dataset_raw": dict(Counter(str(row.get("source_dataset", "")) for row in subset)),
            "source_dataset_clean_ready": dict(Counter(str(row.get("source_dataset", "")) for row in clean_ready)),
        }
    summary["totals"] = {
        "raw_tasks": sum(v["raw_tasks"] for v in summary["by_task_type"].values()),
        "api_leak_removed_tasks": sum(v["api_leak_removed_tasks"] for v in summary["by_task_type"].values()),
        "after_task_signature_dedup": sum(v["after_task_signature_dedup"] for v in summary["by_task_type"].values()),
        "clean_ready_tasks": sum(v["clean_ready_tasks"] for v in summary["by_task_type"].values()),
        "service_leak_only_tasks": sum(v["service_leak_only_tasks"] for v in summary["by_task_type"].values()),
    }
    return by_type, summary


def load_metatool(root: Path) -> list[dict[str, Any]]:
    plugin_path = root / "external_sources" / "MetaTool" / "dataset" / "plugin_des.json"
    data_path = root / "external_sources" / "MetaTool" / "dataset" / "data" / "all_clean_data.csv"
    plugin_des = json.loads(plugin_path.read_text(encoding="utf-8"))
    candidates = [
        {"service_name": name, "service_description": str(desc), "is_gold_service": 0}
        for name, desc in sorted(plugin_des.items(), key=lambda kv: norm(kv[0]))
    ]
    by_norm = {norm(item["service_name"]): item for item in candidates}
    rows: list[dict[str, Any]] = []
    with data_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            query = row.get("Query", "")
            tool = row.get("Tool", "")
            gold = by_norm.get(norm(tool), {"service_name": tool, "service_description": "", "is_gold_service": 1})
            candidate_services = []
            for item in candidates:
                obj = dict(item)
                obj["is_gold_service"] = int(norm(obj["service_name"]) == norm(gold["service_name"]) or norm(obj["service_name"]) == norm(tool))
                candidate_services.append(obj)
            if not any(item["is_gold_service"] for item in candidate_services):
                candidate_services.append({"service_name": tool, "service_description": "", "is_gold_service": 1})
            gold_services = [next((item["service_name"] for item in candidate_services if item["is_gold_service"]), tool)]
            task_id = f"MetaTool_single_{idx}"
            meta = {"original_tool": tool, "route_rebuild_stage": "metatool_external_single"}
            task = {
                "task_id": task_id,
                "task_type": "single_service_discovery",
                "source_dataset": "MetaTool",
                "source_group": "single",
                "source_query_id": str(idx),
                "query_text": query,
                "candidate_services_json": json_dumps(candidate_services),
                "candidate_apis_json": "[]",
                "gold_services_json": json_dumps(gold_services),
                "gold_apis_json": "[]",
                "candidate_service_count": len(candidate_services),
                "gold_service_count": 1,
                "candidate_api_count": 0,
                "gold_api_count": 0,
                "gold_in_candidate_services": 1,
                "gold_in_candidate_apis": 1,
                "query_mentions_any_gold_api": 0,
                "query_mentions_any_gold_service": int(legacy.query_mentions_any(query, gold_services)),
                "metadata_json": json_dumps(meta),
            }
            rows.append(recompute_route_signatures(task))
    return rows


def shortcut_text(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False).lower()


def build_shortcuts_strict(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = root / "external_sources" / "ShortcutsBench" / "generated_success_queries.json.extracted"
    if not path.exists():
        return [], {"status": "missing_generated_success_queries"}
    data = json.loads(path.read_text(encoding="utf-8"))
    service_names = [
        "ChatGPT",
        "Bing",
        "Jira",
        "Todoist",
        "Toggl",
        "Mastodon",
        "Home Assistant",
        "CARROT Weather",
        "Notion",
        "OmniFocus",
        "Goodreads",
        "OpenAI",
    ]
    candidate_services = [
        {"service_name": name, "service_description": f"Strict ShortcutsBench app/service candidate: {name}", "is_gold_service": 0}
        for name in service_names
    ]
    rows: list[dict[str, Any]] = []
    matched_by_service: Counter[str] = Counter()
    for idx, (_url, rec) in enumerate(data.items(), start=1):
        text = shortcut_text(rec)
        matched = [svc for svc in service_names if norm(svc) in text]
        if not matched:
            continue
        # Keep only records with exactly one strict app signal to avoid noisy multi-app shortcuts.
        if len(set(norm(x) for x in matched)) != 1:
            continue
        gold_service = matched[0]
        generated = rec.get("GeneratedQuery") or {}
        query = generated.get("query") if isinstance(generated, dict) else ""
        if not query:
            continue
        services = []
        for item in candidate_services:
            obj = dict(item)
            obj["is_gold_service"] = int(norm(obj["service_name"]) == norm(gold_service))
            services.append(obj)
        task_id = f"ShortcutsBenchStrict_single_{idx}"
        task = {
            "task_id": task_id,
            "task_type": "single_service_discovery",
            "source_dataset": "ShortcutsBenchStrict",
            "source_group": "strict_single",
            "source_query_id": str(idx),
            "query_text": query,
            "candidate_services_json": json_dumps(services),
            "candidate_apis_json": "[]",
            "gold_services_json": json_dumps([gold_service]),
            "gold_apis_json": "[]",
            "candidate_service_count": len(services),
            "gold_service_count": 1,
            "candidate_api_count": 0,
            "gold_api_count": 0,
            "gold_in_candidate_services": 1,
            "gold_in_candidate_apis": 1,
            "query_mentions_any_gold_api": 0,
            "query_mentions_any_gold_service": int(legacy.query_mentions_any(query, [gold_service])),
            "metadata_json": json_dumps(
                {
                    "record_name": rec.get("RecordName"),
                    "strict_match_service": gold_service,
                    "route_rebuild_stage": "shortcutsbench_external_strict_single",
                }
            ),
        }
        rows.append(recompute_route_signatures(task))
        matched_by_service[gold_service] += 1
    return rows, {"strict_keywords": STRICT_SHORTCUT_KEYWORDS, "matched_by_service": dict(matched_by_service)}


def build_six_tasks(cleaned: dict[str, list[dict[str, Any]]], external_single: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    internal_single = cleaned["single_service_discovery_raw"]["clean_ready"]
    single_discovery_internal = [
        row for row in internal_single if as_int(row.get("candidate_service_count")) > as_int(row.get("gold_service_count"))
    ]
    single_api_internal = [
        row for row in internal_single if as_int(row.get("candidate_service_count")) <= as_int(row.get("gold_service_count"))
    ]
    # Route: single service discovery is primarily MetaTool + strict Shortcuts, with any valid internal single-choice-space samples appended.
    return {
        "single_service_discovery": external_single + single_discovery_internal,
        "single_api_recommendation": single_api_internal,
        "multi_service_discovery": cleaned["multi_service_discovery_raw"]["clean_ready"],
        "multi_api_recommendation": cleaned["multi_service_discovery_raw"]["clean_ready"],
        "composable_service_discovery": cleaned["composable_service_discovery_raw"]["clean_ready"],
        "composable_api_recommendation": cleaned["composable_service_discovery_raw"]["clean_ready"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild ServiceDiscoveryBench route v0.1 from ToolBench + StableToolBench + MetaTool + ShortcutsBench.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/service_discovery_route_rebuild_v0_1"))
    parser.add_argument("--toolbench-task-level", type=Path, default=Path("outputs/toolbench_full_raw_streaming_v1_3/full/toolbench_full_task_level_raw.csv"))
    args = parser.parse_args()

    out = args.output_dir
    ensure_dir(out)
    if not args.toolbench_task_level.exists():
        raise FileNotFoundError(f"Missing ToolBench full task-level raw: {args.toolbench_task_level}")

    toolbench_rows = load_toolbench_existing(args.toolbench_task_level)
    stable_rows = build_stable_rows(ROOT)
    internal_rows = toolbench_rows + stable_rows
    internal_raw_dir = out / "service_discovery_raw"
    write_csv(internal_raw_dir / "internal_toolbench_stable_task_level_raw.csv", internal_rows)
    for task_type in TASK_TYPES:
        write_csv(internal_raw_dir / f"{task_type}.csv", [row for row in internal_rows if row.get("task_type") == task_type])

    cleaned, clean_summary = clean_internal(internal_rows)
    cleaned_dir = out / "service_discovery_cleaned"
    for task_type, buckets in cleaned.items():
        stem = task_type.replace("_discovery_raw", "")
        write_csv(cleaned_dir / f"{stem}_clean_ready.csv", buckets["clean_ready"])
        write_csv(cleaned_dir / f"{stem}_service_leak_only.csv", buckets["service_leak_only"])
        write_csv(cleaned_dir / f"{stem}_api_leak_removed.csv", buckets["api_leak_removed"])
    write_json(cleaned_dir / "cleaning_summary.json", clean_summary)

    metatool_rows = load_metatool(ROOT)
    shortcuts_rows, shortcuts_summary = build_shortcuts_strict(ROOT)
    external_dir = out / "service_discovery_external"
    write_csv(external_dir / "metatool_single_service_discovery_task_level.csv", metatool_rows)
    write_csv(external_dir / "shortcutsbench_strict_single_service_discovery_task_level.csv", shortcuts_rows)
    external_clean = [
        row
        for row in metatool_rows + shortcuts_rows
        if as_int(row.get("query_mentions_any_gold_service")) == 0 and as_int(row.get("query_mentions_any_gold_api")) == 0
    ]
    write_csv(external_dir / "external_single_clean_ready_task_level.csv", external_clean)

    six = build_six_tasks(cleaned, external_clean)
    six_dir = out / "service_discovery_six_tasks"
    six_counts = {}
    for name, rows in six.items():
        six_counts[name] = write_csv(six_dir / f"{name}.csv", rows)

    summary = {
        "generated_time": now_text(),
        "route_doc": "SERVICE_DISCOVERY_REPRODUCE_FROM_DOWNLOAD.md",
        "output_dir": str(out),
        "inputs": {
            "toolbench_task_level": str(args.toolbench_task_level),
            "stabletoolbench_paths": {k: str(v) for k, v in stable_group_paths(ROOT).items()},
            "metatool_all_clean_data": "external_sources/MetaTool/dataset/data/all_clean_data.csv",
            "metatool_plugin_des": "external_sources/MetaTool/dataset/plugin_des.json",
            "shortcutsbench_generated_success_queries": "external_sources/ShortcutsBench/generated_success_queries.json.extracted",
        },
        "raw_counts": {
            "ToolBench": len(toolbench_rows),
            "StableToolBench": len(stable_rows),
            "internal_total": len(internal_rows),
            "MetaTool": len(metatool_rows),
            "ShortcutsBenchStrict": len(shortcuts_rows),
            "external_single_clean_ready": len(external_clean),
        },
        "cleaning_summary": clean_summary,
        "shortcuts_summary": shortcuts_summary,
        "six_task_counts": six_counts,
        "notes": [
            "This rebuild is independent from the older ToolBench-only v1.4/v1.5 pipeline and does not overwrite it.",
            "Internal cleaning applies: remove API leak tasks, deduplicate by source-independent task_signature with StableToolBench priority, split service leak into service_leak_only.",
            "MetaTool is included as single_service_discovery external data with 199 service candidates per query.",
            "ShortcutsBench strict uses conservative keyword matching and should be manually audited before being considered final.",
            "Counts may differ from the teacher route document if local source versions differ.",
        ],
    }
    write_json(out / "route_rebuild_summary.json", summary)

    report = [
        "# ServiceDiscoveryBench Route Rebuild v0.1 Report",
        "",
        f"Generated time: {now_text()}",
        f"Output directory: `{out}`",
        "",
        "## What Changed",
        "",
        "This rebuild follows the provided route document instead of the earlier ToolBench-only pipeline.",
        "It includes ToolBench, StableToolBench, MetaTool, and a conservative ShortcutsBench strict extraction.",
        "",
        "## Raw Source Counts",
        "",
        *table(Counter(summary["raw_counts"])),
        "",
        "## Internal Cleaning Counts",
        "",
    ]
    for task_type, item in clean_summary["by_task_type"].items():
        report.extend(
            [
                f"### {task_type}",
                "",
                f"- raw_tasks: {item['raw_tasks']}",
                f"- api_leak_removed_tasks: {item['api_leak_removed_tasks']}",
                f"- after_task_signature_dedup: {item['after_task_signature_dedup']}",
                f"- clean_ready_tasks: {item['clean_ready_tasks']}",
                f"- service_leak_only_tasks: {item['service_leak_only_tasks']}",
                f"- source_dataset_clean_ready: `{item['source_dataset_clean_ready']}`",
                "",
            ]
        )
    report.extend(
        [
            "## Six Task Counts",
            "",
            *table(Counter(six_counts)),
            "",
            "## Important Caveats",
            "",
            "- This does not run split, baseline, or model training.",
            "- ShortcutsBench strict extraction is conservative and needs manual audit.",
            "- If counts differ from the route document, inspect source version and ShortcutsBench extraction/filtering before experiments.",
        ]
    )
    write_md(Path("docs/phase1/service_discovery_route_rebuild_v0_1_report.md"), report)

    print("route rebuild complete")
    print(json.dumps(summary["raw_counts"], ensure_ascii=False, indent=2))
    print(json.dumps(six_counts, ensure_ascii=False, indent=2))
    print(f"summary: {out / 'route_rebuild_summary.json'}")
    print("report: docs/phase1/service_discovery_route_rebuild_v0_1_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
