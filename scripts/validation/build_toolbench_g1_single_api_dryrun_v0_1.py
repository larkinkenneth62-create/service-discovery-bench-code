#!/usr/bin/env python
"""Build a source-specific ToolBench G1 single-API candidate dry-run.

The script only restructures APIs already present in each raw task. It does not
invent APIs, infer missing gold labels, execute APIs, freeze a source, or create
a final benchmark dataset.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from source_qa_review_validator_v0_4_2 import HUMAN_FIELDS
except ImportError:
    from scripts.validation.source_qa_review_validator_v0_4_2 import HUMAN_FIELDS


OUTPUT_FIELDS = [
    "source_task_id",
    "query_text",
    "source_service",
    "candidate_apis_json",
    "gold_apis_json",
    "service_api_map_json",
    "candidate_api_count",
    "gold_api_count",
    "negative_distractor_count",
    "hard_negative_type_distribution_json",
    "api_leak_status",
    "mapping_status",
    "candidate_space_status",
    "g1_dryrun_decision",
    "g1_dryrun_reasons_json",
    "requires_human_review",
    "source_provenance",
]

QA_FIELDS = ["review_item_id", "sampling_stratum"] + OUTPUT_FIELDS + HUMAN_FIELDS

DECISIONS = [
    "candidate_ready_for_qa",
    "reconstruction_needed",
    "leakage_hold",
    "mapping_uncertain",
    "excluded",
]

STRATUM_TARGETS = [
    ("candidate_count_2", 10),
    ("candidate_count_3_to_5", 10),
    ("candidate_count_6_to_9", 10),
    ("candidate_count_10_plus", 10),
    ("same_service_sibling_hard_negative", 20),
    ("same_domain_hard_negative", 10),
    ("leak_risk", 20),
    ("mapping_uncertain", 10),
    ("normal_ready_for_qa", 25),
    ("candidate_equals_gold_or_no_negative", 25),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_json(value: str, expected: type) -> Any:
    parsed = json.loads(value)
    if not isinstance(parsed, expected):
        raise ValueError(f"expected {expected.__name__}, got {type(parsed).__name__}")
    return parsed


def text(value: Any) -> str:
    return str(value or "").strip()


def pair(item: dict[str, Any]) -> tuple[str, str]:
    return text(item.get("service_name")), text(item.get("api_name"))


def stable_score(task_id: str, stratum: str) -> int:
    digest = hashlib.sha256(f"{stratum}\0{task_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def push_reservoir(
    reservoirs: dict[str, list[tuple[int, str, dict[str, str]]]],
    stratum: str,
    row: dict[str, str],
    limit: int,
) -> None:
    score = stable_score(row["source_task_id"], stratum)
    entry = (-score, row["source_task_id"], row)
    heap = reservoirs[stratum]
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif entry > heap[0]:
        heapq.heapreplace(heap, entry)


def candidate_count_stratum(count: int) -> str:
    if count <= 2:
        return "candidate_count_2"
    if count <= 5:
        return "candidate_count_3_to_5"
    if count <= 9:
        return "candidate_count_6_to_9"
    return "candidate_count_10_plus"


def row_strata(row: dict[str, str], hard_counts: Counter[str]) -> list[str]:
    strata = [candidate_count_stratum(int(row["candidate_api_count"]))]
    if hard_counts["same_service_sibling"]:
        strata.append("same_service_sibling_hard_negative")
    if hard_counts["same_domain_different_service"]:
        strata.append("same_domain_hard_negative")
    if row["api_leak_status"] == "blocking_candidate":
        strata.append("leak_risk")
    if row["mapping_status"] != "traceable":
        strata.append("mapping_uncertain")
    if row["g1_dryrun_decision"] == "candidate_ready_for_qa":
        strata.append("normal_ready_for_qa")
    if row["candidate_space_status"] in {
        "candidate_equals_gold",
        "no_negative_distractor",
        "candidate_count_not_greater_than_gold",
    }:
        strata.append("candidate_equals_gold_or_no_negative")
    return strata


def annotate(raw: dict[str, str]) -> tuple[dict[str, str], Counter[str]]:
    reasons: list[str] = []
    mapping_reasons: list[str] = []
    parse_failed = False
    try:
        candidate_services = parse_json(raw.get("candidate_services_json", ""), list)
        candidate_apis = parse_json(raw.get("candidate_apis_json", ""), list)
        gold_services = parse_json(raw.get("gold_services_json", ""), list)
        gold_apis = parse_json(raw.get("gold_apis_json", ""), list)
        metadata = parse_json(raw.get("metadata_json", ""), dict)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        candidate_services, candidate_apis, gold_services, gold_apis, metadata = [], [], [], [], {}
        parse_failed = True
        mapping_reasons.append(f"json_parse_error:{type(exc).__name__}")

    candidate_service_names = {
        text(item.get("service_name"))
        for item in candidate_services
        if isinstance(item, dict) and text(item.get("service_name"))
    }
    gold_service_names = {text(item) for item in gold_services if text(item)}
    candidate_pairs = {
        pair(item) for item in candidate_apis if isinstance(item, dict) and all(pair(item))
    }
    gold_pairs = {
        pair(item) for item in gold_apis if isinstance(item, dict) and all(pair(item))
    }
    malformed_candidate_api = any(
        not isinstance(item, dict) or not all(pair(item)) for item in candidate_apis
    )
    if malformed_candidate_api:
        mapping_reasons.append("candidate_api_missing_service_or_api_name")
    if not gold_service_names.issubset(candidate_service_names):
        mapping_reasons.append("gold_service_not_in_candidate_services")
    if not gold_pairs.issubset(candidate_pairs):
        mapping_reasons.append("gold_api_not_in_candidate_apis")
    orphan_services = sorted(
        {service for service, _api in candidate_pairs if service not in candidate_service_names}
    )
    if orphan_services:
        mapping_reasons.append("candidate_api_parent_service_missing")

    service_api_map: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in candidate_apis:
        if not isinstance(item, dict):
            continue
        service, api = pair(item)
        if not service or not api:
            continue
        service_api_map[service].append(
            {
                "api_name": api,
                "api_description": text(item.get("api_description")),
                "category_name": text(item.get("category_name")),
                "is_gold_api": text(item.get("is_gold_api")),
            }
        )

    gold_service = next(iter(sorted(gold_service_names)), "")
    gold_categories = {
        text(item.get("category_name"))
        for item in candidate_apis
        if isinstance(item, dict) and pair(item) in gold_pairs and text(item.get("category_name"))
    }
    hard_counts: Counter[str] = Counter()
    for item in candidate_apis:
        if not isinstance(item, dict) or pair(item) in gold_pairs:
            continue
        service, _api = pair(item)
        category = text(item.get("category_name"))
        if service and service == gold_service:
            hard_counts["same_service_sibling"] += 1
        elif category and category in gold_categories:
            hard_counts["same_domain_different_service"] += 1
        else:
            hard_counts["cross_domain_easy_negative"] += 1

    candidate_count = len(candidate_apis)
    gold_count = len(gold_apis)
    negative_count = sum(pair(item) not in gold_pairs for item in candidate_apis if isinstance(item, dict))
    candidate_equals_gold = candidate_pairs == gold_pairs and candidate_count == gold_count
    if candidate_count <= gold_count:
        candidate_space_status = "candidate_count_not_greater_than_gold"
    elif candidate_equals_gold:
        candidate_space_status = "candidate_equals_gold"
    elif negative_count < 1:
        candidate_space_status = "no_negative_distractor"
    else:
        candidate_space_status = "valid_choice_space"

    provenance_complete = all(
        text(raw.get(field))
        for field in ["task_id", "source_dataset", "source_group", "source_query_id"]
    ) and bool(text(metadata.get("input_file")))
    query_ok = bool(text(raw.get("query_text")))
    mapping_status = "traceable" if not parse_failed and not mapping_reasons else "uncertain"
    api_leak = text(raw.get("query_mentions_any_gold_api")) in {"1", "true", "yes"}

    if not query_ok:
        reasons.append("query_empty_or_unparseable")
    if len(gold_services) != 1:
        reasons.append("gold_service_count_not_one")
    if not gold_apis:
        reasons.append("gold_api_empty")
    if not provenance_complete:
        reasons.append("source_provenance_incomplete")

    if reasons:
        decision = "excluded"
    elif mapping_status != "traceable":
        decision = "mapping_uncertain"
        reasons.extend(mapping_reasons)
    elif api_leak:
        decision = "leakage_hold"
        reasons.append("query_mentions_gold_api")
    elif candidate_space_status != "valid_choice_space":
        decision = "reconstruction_needed"
        reasons.append(candidate_space_status)
    else:
        decision = "candidate_ready_for_qa"
        reasons.append("structurally_valid_existing_candidate_space")

    provenance = {
        "source_dataset": text(raw.get("source_dataset")),
        "source_group": text(raw.get("source_group")),
        "source_query_id": text(raw.get("source_query_id")),
        "input_file": text(metadata.get("input_file")),
        "raw_stage": text(metadata.get("script_stage")),
        "raw_task_id": text(raw.get("task_id")),
    }
    annotated = {
        "source_task_id": text(raw.get("task_id")),
        "query_text": text(raw.get("query_text")),
        "source_service": gold_service,
        "candidate_apis_json": json.dumps(candidate_apis, ensure_ascii=False, sort_keys=True),
        "gold_apis_json": json.dumps(gold_apis, ensure_ascii=False, sort_keys=True),
        "service_api_map_json": json.dumps(service_api_map, ensure_ascii=False, sort_keys=True),
        "candidate_api_count": str(candidate_count),
        "gold_api_count": str(gold_count),
        "negative_distractor_count": str(negative_count),
        "hard_negative_type_distribution_json": json.dumps(hard_counts, ensure_ascii=False, sort_keys=True),
        "api_leak_status": "blocking_candidate" if api_leak else "no_obvious_leak",
        "mapping_status": mapping_status,
        "candidate_space_status": candidate_space_status,
        "g1_dryrun_decision": decision,
        "g1_dryrun_reasons_json": json.dumps(reasons, ensure_ascii=False),
        "requires_human_review": "true",
        "source_provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
    }
    return annotated, hard_counts


def sorted_reservoir(heap: Iterable[tuple[int, str, dict[str, str]]]) -> list[dict[str, str]]:
    return [entry[2] for entry in sorted(heap, key=lambda item: (-item[0], item[1]))]


def write_report(path: Path, summary: dict[str, Any]) -> None:
    decision_counts = summary["decision_distribution"]
    lines = [
        "# ToolBench G1 Single-API Candidate Dry-run v0.1",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Raw input: `{summary['raw_input']}`",
        f"- Inventory input: `{summary['inventory_input']}`",
        f"- Feasibility input: `{summary['feasibility_input']}`",
        f"- Raw rows recomputed: **{summary['raw_rows']}**",
        "- Scope: source-specific dry-run and blank human QA pack; not source freeze or final data.",
        "",
        "## Decision Distribution",
        "",
        "| Decision | Rows |",
        "|---|---:|",
    ]
    for decision in DECISIONS:
        lines.append(f"| {decision} | {decision_counts.get(decision, 0)} |")
    lines.extend(
        [
            "",
            "## QA Pack",
            "",
            f"- Rows: **{summary['qa_pack_rows']}**",
            f"- Human fields populated: **{summary['human_fields_populated']}**",
            f"- Sampling strata: `{json.dumps(summary['qa_sampling_distribution'], ensure_ascii=False)}`",
            f"- Missing requested strata: `{json.dumps(summary['missing_sampling_strata'], ensure_ascii=False)}`",
            "",
            "## Hard Negatives",
            "",
            f"`{json.dumps(summary['hard_negative_distribution'], ensure_ascii=False)}`",
            "",
            "All negatives are existing APIs from the same raw candidate list. No API, API description, "
            "or gold label was invented. Same-service siblings are used first; current G1 rows expose "
            "no verified cross-service candidate pool, so same-domain and cross-domain coverage may be absent.",
            "",
            "## Cross-check",
            "",
            f"- Inventory raw rows: `{summary['inventory_raw_rows']}`",
            f"- Inventory estimated ready rows: `{summary['inventory_estimated_ready_rows']}`",
            f"- Recomputed ready rows: `{decision_counts.get('candidate_ready_for_qa', 0)}`",
            f"- Inventory-ready rows conservatively downgraded by the dry-run: `{summary['inventory_ready_not_recomputed_ready']}`",
            f"- Duplicate-gold/count regression rows among those downgrades: `{summary['duplicate_gold_count_regression_rows']}`",
            f"- Feasibility task IDs matched: `{summary['feasibility_task_ids_matched']}`",
            f"- Feasibility task ID mismatches: `{summary['feasibility_task_id_mismatches']}`",
            "",
            "The dry-run decision is structural and remains subject to row-level human semantic QA. "
            "It does not automatically establish single-API benchmark eligibility.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ToolBench G1 single-API candidate dry-run v0.1.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--raw-input", default="outputs/toolbench_full_raw_streaming_v1_3/full/G1_task_level_raw.csv"
    )
    parser.add_argument(
        "--inventory", default="outputs/toolbench_g1_single_api_inventory_v0_1/inventory.json"
    )
    parser.add_argument(
        "--feasibility",
        default="outputs/toolbench_g1_single_api_inventory_v0_1/candidate_construction_feasibility.csv",
    )
    parser.add_argument("--output-dir", default="outputs/toolbench_g1_single_api_dryrun_v0_1")
    parser.add_argument(
        "--report", default="docs/phase1/toolbench_g1_single_api_candidate_dryrun_report_v0_1.md"
    )
    parser.add_argument("--qa-rows", type=int, default=150)
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    raw_path = root / args.raw_input
    inventory_path = root / args.inventory
    feasibility_path = root / args.feasibility
    for required in [raw_path, inventory_path, feasibility_path]:
        if not required.exists():
            parser.error(f"Required input does not exist: {required}")
    if not 100 <= args.qa_rows <= 150:
        parser.error("--qa-rows must be between 100 and 150")

    inventory = json.loads(inventory_path.read_text(encoding="utf-8-sig"))
    with feasibility_path.open("r", encoding="utf-8-sig", newline="") as handle:
        feasibility_rows = list(csv.DictReader(handle))
    feasibility_ids = {text(row.get("task_id")) for row in feasibility_rows}
    inventory_ready_ids = {
        text(row.get("task_id"))
        for row in feasibility_rows
        if text(row.get("estimated_valid_api_recommendation_candidate")).lower() == "true"
    }

    output_dir = root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "annotated": output_dir / "toolbench_g1_single_api_candidates_annotated.csv",
        "candidate_ready_for_qa": output_dir / "toolbench_g1_single_api_ready_for_qa.csv",
        "reconstruction_needed": output_dir / "toolbench_g1_single_api_reconstruction_needed.csv",
        "leakage_hold": output_dir / "toolbench_g1_single_api_leakage_hold.csv",
    }
    handles: dict[str, Any] = {}
    writers: dict[str, csv.DictWriter] = {}
    try:
        for name, path in paths.items():
            handle = path.open("w", encoding="utf-8-sig", newline="")
            handles[name] = handle
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writers[name] = writer

        decision_counts: Counter[str] = Counter()
        hard_total: Counter[str] = Counter()
        reservoirs: dict[str, list[tuple[int, str, dict[str, str]]]] = defaultdict(list)
        raw_rows = 0
        matched = 0
        mismatched = 0
        inventory_ready_not_recomputed_ready = 0
        duplicate_gold_count_regression_rows = 0
        with raw_path.open("r", encoding="utf-8-sig", newline="") as raw_handle:
            for raw in csv.DictReader(raw_handle):
                raw_rows += 1
                annotated, hard_counts = annotate(raw)
                writers["annotated"].writerow(annotated)
                decision = annotated["g1_dryrun_decision"]
                decision_counts[decision] += 1
                if annotated["source_task_id"] in inventory_ready_ids and decision != "candidate_ready_for_qa":
                    inventory_ready_not_recomputed_ready += 1
                    if (
                        annotated["candidate_space_status"] == "candidate_count_not_greater_than_gold"
                        and int(annotated["negative_distractor_count"]) > 0
                    ):
                        duplicate_gold_count_regression_rows += 1
                hard_total.update(hard_counts)
                if decision in writers and decision != "annotated":
                    writers[decision].writerow(annotated)
                if annotated["source_task_id"] in feasibility_ids:
                    matched += 1
                else:
                    mismatched += 1
                for stratum in row_strata(annotated, hard_counts):
                    push_reservoir(reservoirs, stratum, annotated, max(args.qa_rows, 200))
                push_reservoir(reservoirs, "all_rows_fallback", annotated, args.qa_rows * 4)
    finally:
        for handle in handles.values():
            handle.close()

    selected: list[tuple[str, dict[str, str]]] = []
    selected_ids: set[str] = set()
    requested = dict(STRATUM_TARGETS)
    for stratum, target in STRATUM_TARGETS:
        for row in sorted_reservoir(reservoirs.get(stratum, [])):
            if len([1 for label, _ in selected if label == stratum]) >= target:
                break
            if row["source_task_id"] in selected_ids:
                continue
            selected.append((stratum, row))
            selected_ids.add(row["source_task_id"])
    for row in sorted_reservoir(reservoirs.get("all_rows_fallback", [])):
        if len(selected) >= args.qa_rows:
            break
        if row["source_task_id"] in selected_ids:
            continue
        selected.append(("fallback_fill", row))
        selected_ids.add(row["source_task_id"])

    qa_path = output_dir / "toolbench_g1_single_api_qa_items_v0_1.csv"
    with qa_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QA_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for index, (stratum, row) in enumerate(selected, start=1):
            out = {**row, "review_item_id": f"G1-API-QA-{index:03d}", "sampling_stratum": stratum}
            out.update({field: "" for field in HUMAN_FIELDS})
            writer.writerow(out)

    hard_stats_path = output_dir / "hard_negative_statistics.csv"
    with hard_stats_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["hard_negative_type", "count"])
        writer.writeheader()
        for label in [
            "same_service_sibling",
            "same_domain_different_service",
            "capability_similar_unverified",
            "cross_domain_easy_negative",
        ]:
            writer.writerow({"hard_negative_type": label, "count": hard_total.get(label, 0)})

    sampling_distribution = Counter(label for label, _row in selected)
    summary = {
        "generated_at": now_iso(),
        "scope": "source_specific_dryrun_not_source_freeze_not_final_dataset",
        "raw_input": str(raw_path.resolve()),
        "inventory_input": str(inventory_path.resolve()),
        "feasibility_input": str(feasibility_path.resolve()),
        "raw_rows": raw_rows,
        "inventory_raw_rows": inventory.get("raw_g1_task_count"),
        "inventory_estimated_ready_rows": inventory.get("estimated_valid_api_recommendation_candidate_count"),
        "inventory_ready_not_recomputed_ready": inventory_ready_not_recomputed_ready,
        "duplicate_gold_count_regression_rows": duplicate_gold_count_regression_rows,
        "decision_distribution": dict(decision_counts),
        "hard_negative_distribution": dict(hard_total),
        "qa_pack_rows": len(selected),
        "qa_sampling_distribution": dict(sampling_distribution),
        "missing_sampling_strata": [
            label for label, target in STRATUM_TARGETS if sampling_distribution.get(label, 0) < target
        ],
        "human_fields_populated": 0,
        "feasibility_task_ids_matched": matched,
        "feasibility_task_id_mismatches": mismatched,
        "outputs": {name: str(path.resolve()) for name, path in paths.items()},
        "qa_output": str(qa_path.resolve()),
        "hard_negative_statistics": str(hard_stats_path.resolve()),
        "gold_modified_or_inferred": False,
        "invented_api_count": 0,
        "real_api_called": False,
        "source_freeze_executed": False,
        "final_dataset_generated": False,
    }
    summary_path = output_dir / "toolbench_g1_single_api_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(root / args.report, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
