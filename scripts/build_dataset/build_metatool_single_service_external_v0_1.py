from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path("outputs/external_sources_adapters_v0_1/metatool")
QA_DIR = Path("outputs/external_qa_v0_1/metatool")
DOC_DIR = Path("docs/phase1")

HUMAN_FIELDS = [
    "qa_final_decision",
    "qa_semantic_alignment_check",
    "qa_candidate_validity_check",
    "qa_service_catalog_check",
    "qa_leakage_check",
    "qa_error_type",
    "qa_severity",
    "qa_notes",
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def task_sig(*parts: Any) -> str:
    return hashlib.sha1("\n".join(norm(part) for part in parts).encode("utf-8")).hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md(path: Path, lines: list[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def query_mentions(query: str, name: str) -> bool:
    q = norm(query)
    n = norm(name)
    return bool(n and len(n) >= 3 and n in q)


def load_plugin_catalog(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    services = []
    for index, (name, desc) in enumerate(sorted(data.items(), key=lambda kv: norm(kv[0])), start=1):
        services.append(
            {
                "service_id": f"MetaToolService_{index:03d}",
                "service_name": name,
                "service_description": str(desc or ""),
            }
        )
    return services, {norm(item["service_name"]): item for item in services}


def query_len_bucket(query: str) -> str:
    length = len(str(query).split())
    if length <= 6:
        return "short"
    if length <= 15:
        return "medium"
    return "long"


def build_rows(data_path: Path, plugin_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    services, service_by_norm = load_plugin_catalog(plugin_path)
    candidate_services_json = json_dumps(
        [
            {
                "service_id": item["service_id"],
                "service_name": item["service_name"],
                "service_description": item["service_description"],
            }
            for item in services
        ]
    )
    service_catalog_rows = [
        {
            "service_id": item["service_id"],
            "service_name": item["service_name"],
            "service_description": item["service_description"],
        }
        for item in services
    ]

    task_rows: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    leakage_scan: list[dict[str, Any]] = []
    with data_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for idx, src in enumerate(reader, start=1):
            query = src.get("Query", "")
            tool = src.get("Tool", "")
            matched = service_by_norm.get(norm(tool))
            warnings = []
            leakage = "no_service_name_leak"
            if not matched:
                warnings.append("gold_service_unmatched")
                gold_obj = {"service_name": tool, "service_description": ""}
                unmatched.append({"source_row_id": idx, "source_tool_or_plugin_name": tool, "query_text": query})
            else:
                gold_obj = {
                    "service_id": matched["service_id"],
                    "service_name": matched["service_name"],
                    "service_description": matched["service_description"],
                }
            if query_mentions(query, tool):
                leakage = "gold_service_name_mentioned_in_query"
                warnings.append("service_leakage_risk")
            row = {
                "task_id": f"MetaTool_{idx}",
                "source_dataset": "MetaTool",
                "task_type": "single_service_discovery_external",
                "query_text": query,
                "gold_services_json": json_dumps([gold_obj]),
                "candidate_services_json": candidate_services_json,
                "gold_service_count": 1,
                "candidate_service_count": len(services),
                "service_catalog_size": len(services),
                "source_row_id": idx,
                "source_tool_or_plugin_name": tool,
                "leakage_check_status": leakage,
                "adapter_notes": ";".join(warnings),
                "query_signature": task_sig(query),
                "task_signature": task_sig(query, tool),
                "query_length_bucket": query_len_bucket(query),
            }
            task_rows.append(row)
            leakage_scan.append(
                {
                    "task_id": row["task_id"],
                    "source_row_id": idx,
                    "source_tool_or_plugin_name": tool,
                    "leakage_check_status": leakage,
                    "query_text": query,
                    "adapter_notes": row["adapter_notes"],
                }
            )
    summary = {
        "generated_time": now_text(),
        "input_all_clean_data": str(data_path),
        "input_plugin_des": str(plugin_path),
        "task_rows": len(task_rows),
        "service_catalog_size": len(services),
        "unmatched_gold_service_count": len(unmatched),
        "service_leakage_risk_count": sum(1 for row in leakage_scan if row["leakage_check_status"] != "no_service_name_leak"),
        "query_length_bucket_distribution": dict(Counter(row["query_length_bucket"] for row in task_rows)),
        "source_tool_distribution_top20": dict(Counter(row["source_tool_or_plugin_name"] for row in task_rows).most_common(20)),
        "expected_rows_note": "Route document expects approximately 20,614 rows and 199 services; warn but do not fail if local counts differ.",
    }
    return task_rows, service_catalog_rows, unmatched, leakage_scan, summary


def build_review_pack(rows: list[dict[str, Any]], output_path: Path) -> list[dict[str, Any]]:
    rng = random.Random(42)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(row: dict[str, Any]) -> None:
        if row["task_id"] in seen or len(selected) >= 100:
            return
        selected.append(row)
        seen.add(row["task_id"])

    for row in rows:
        if "gold_service_unmatched" in row.get("adapter_notes", ""):
            add(row)
    for row in rows:
        if row.get("leakage_check_status") != "no_service_name_leak":
            add(row)
    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_tool[str(row.get("source_tool_or_plugin_name", ""))].append(row)
    for tool in sorted(by_tool):
        add(rng.choice(by_tool[tool]))
    for bucket in ["short", "medium", "long"]:
        pool = [row for row in rows if row.get("query_length_bucket") == bucket and row["task_id"] not in seen]
        rng.shuffle(pool)
        for row in pool[:20]:
            add(row)
    pool = [row for row in rows if row["task_id"] not in seen]
    rng.shuffle(pool)
    for row in pool:
        add(row)
        if len(selected) >= 100:
            break

    review_rows = []
    for idx, row in enumerate(selected, start=1):
        review = {
            "review_item_id": f"MT-QA-{idx:03d}",
            "task_id": row["task_id"],
            "source_dataset": row["source_dataset"],
            "task_type": row["task_type"],
            "query_text": row["query_text"],
            "candidate_services_json": row["candidate_services_json"],
            "gold_services_json": row["gold_services_json"],
            "source_tool_or_plugin_name": row["source_tool_or_plugin_name"],
            "adapter_warnings": row["adapter_notes"],
        }
        for field in HUMAN_FIELDS:
            review[field] = ""
        review_rows.append(review)
    fieldnames = [
        "review_item_id",
        "task_id",
        "source_dataset",
        "task_type",
        "query_text",
        "candidate_services_json",
        "gold_services_json",
        "source_tool_or_plugin_name",
        "adapter_warnings",
        *HUMAN_FIELDS,
    ]
    write_csv(output_path, review_rows, fieldnames)
    return review_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build MetaTool single-service external task-level adapter.")
    parser.add_argument("--all-clean-data", type=Path, default=Path("external_sources/MetaTool/dataset/data/all_clean_data.csv"))
    parser.add_argument("--plugin-des", type=Path, default=Path("external_sources/MetaTool/dataset/plugin_des.json"))
    args = parser.parse_args()
    if not args.all_clean_data.exists():
        raise FileNotFoundError(f"Missing MetaTool all_clean_data.csv: {args.all_clean_data}")
    if not args.plugin_des.exists():
        raise FileNotFoundError(f"Missing MetaTool plugin_des.json: {args.plugin_des}")

    rows, catalog, unmatched, leakage, summary = build_rows(args.all_clean_data, args.plugin_des)
    task_fields = [
        "task_id",
        "source_dataset",
        "task_type",
        "query_text",
        "gold_services_json",
        "candidate_services_json",
        "gold_service_count",
        "candidate_service_count",
        "service_catalog_size",
        "source_row_id",
        "source_tool_or_plugin_name",
        "leakage_check_status",
        "adapter_notes",
        "query_signature",
        "task_signature",
        "query_length_bucket",
    ]
    write_csv(OUTPUT_DIR / "metatool_single_service_task_level_raw.csv", rows, task_fields)
    write_csv(OUTPUT_DIR / "metatool_plugin_service_catalog.csv", catalog, ["service_id", "service_name", "service_description"])
    write_csv(OUTPUT_DIR / "metatool_unmatched_gold_services.csv", unmatched, ["source_row_id", "source_tool_or_plugin_name", "query_text"])
    write_csv(OUTPUT_DIR / "metatool_query_leakage_scan.csv", leakage, ["task_id", "source_row_id", "source_tool_or_plugin_name", "leakage_check_status", "query_text", "adapter_notes"])
    review_rows = build_review_pack(rows, QA_DIR / "metatool_single_service_review_items_100.csv")
    summary["review_pack_rows"] = len(review_rows)
    summary["outputs"] = {
        "task_level_raw": str(OUTPUT_DIR / "metatool_single_service_task_level_raw.csv"),
        "service_catalog": str(OUTPUT_DIR / "metatool_plugin_service_catalog.csv"),
        "unmatched": str(OUTPUT_DIR / "metatool_unmatched_gold_services.csv"),
        "leakage_scan": str(OUTPUT_DIR / "metatool_query_leakage_scan.csv"),
        "review_pack": str(QA_DIR / "metatool_single_service_review_items_100.csv"),
    }
    write_json(OUTPUT_DIR / "metatool_adapter_summary.json", summary)

    report = [
        "# MetaTool Single-Service Adapter Report v0.1",
        "",
        f"Generated time: {summary['generated_time']}",
        f"Input all_clean_data: `{args.all_clean_data}`",
        f"Input plugin_des: `{args.plugin_des}`",
        "",
        f"- task_rows: {summary['task_rows']}",
        f"- service_catalog_size: {summary['service_catalog_size']}",
        f"- unmatched_gold_service_count: {summary['unmatched_gold_service_count']}",
        f"- service_leakage_risk_count: {summary['service_leakage_risk_count']}",
        f"- review_pack_rows: {summary['review_pack_rows']}",
        "",
        "This adapter creates task-level raw rows only. It does not expand to candidate-level rows, does not perform final cleaning, and does not merge into ToolBench-core outputs.",
    ]
    write_md(DOC_DIR / "metatool_single_service_adapter_report_v0_1.md", report)

    plan = [
        "# MetaTool Single-Service Review Plan v0.1",
        "",
        f"Generated time: {now_text()}",
        f"Review CSV: `{QA_DIR / 'metatool_single_service_review_items_100.csv'}`",
        "",
        "## Sampling",
        "",
        "- 100 rows.",
        "- Covers different plugins/services where possible.",
        "- Includes leakage-risk and unmatched-gold rows if present.",
        "- Includes different query length buckets.",
        "",
        "## Human Review Focus",
        "",
        "- Whether the query semantically matches the gold service.",
        "- Whether 199-service candidate catalog is appropriate.",
        "- Whether query directly leaks the service/plugin name.",
        "- Whether any adapter warning should block future integration.",
    ]
    write_md(DOC_DIR / "metatool_single_service_review_plan_v0_1.md", plan)

    print(json.dumps({k: summary[k] for k in ["task_rows", "service_catalog_size", "unmatched_gold_service_count", "service_leakage_risk_count", "review_pack_rows"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
