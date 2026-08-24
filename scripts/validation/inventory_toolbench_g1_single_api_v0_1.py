#!/usr/bin/env python
"""Read-only inventory for ToolBench G1 single-API recommendation feasibility."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_FIELDS = [
    "task_id",
    "source_query_id",
    "query_parse_ok",
    "service_mapping_available",
    "api_mapping_available",
    "candidate_service_count",
    "gold_service_count",
    "candidate_api_count",
    "gold_api_count",
    "gold_services_subset",
    "gold_apis_subset",
    "candidate_api_equals_gold",
    "negative_api_distractor_count",
    "same_service_hard_negative_count",
    "cross_service_hard_negative_count",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service",
    "api_leak_risk",
    "service_leak_risk",
    "estimated_valid_api_recommendation_candidate",
    "hard_negative_feasibility",
    "feasibility_reasons",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value or "").strip()


def truthy(value: Any) -> bool:
    return text(value).lower() in {"1", "true", "yes"}


def parse_list(value: Any) -> tuple[bool, list[Any]]:
    try:
        parsed = json.loads(text(value))
    except Exception:
        return False, []
    return (True, parsed) if isinstance(parsed, list) else (False, [])


def service_id(item: Any) -> str:
    if isinstance(item, str):
        return item.strip().casefold()
    if isinstance(item, dict):
        for field in ["service_name", "tool_name", "name", "service_id"]:
            if text(item.get(field)):
                return text(item.get(field)).casefold()
    return json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()


def api_id(item: Any) -> tuple[str, str]:
    if isinstance(item, str):
        return "", item.strip().casefold()
    if isinstance(item, dict):
        service = text(item.get("service_name") or item.get("tool_name")).casefold()
        api = text(item.get("api_name") or item.get("endpoint_name") or item.get("name")).casefold()
        return service, api
    return "", json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ToolBench G1 single-API inventory.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--input",
        default="outputs/toolbench_full_raw_streaming_v1_3/full/G1_task_level_raw.csv",
    )
    parser.add_argument(
        "--output-dir", default="outputs/toolbench_g1_single_api_inventory_v0_1"
    )
    parser.add_argument(
        "--report", default="docs/phase1/toolbench_g1_single_api_inventory_v0_1.md"
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    input_path = (root / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    output_dir = (root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    report_path = (root / args.report).resolve() if not Path(args.report).is_absolute() else Path(args.report)
    if not input_path.exists():
        raise SystemExit(f"ToolBench G1 input does not exist: {input_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    counters: Counter[str] = Counter()
    candidate_api_distribution: Counter[int] = Counter()
    gold_api_distribution: Counter[int] = Counter()
    negative_distribution: Counter[int] = Counter()
    output_path = output_dir / "candidate_construction_feasibility.csv"
    with input_path.open("r", encoding="utf-8-sig", newline="") as source, output_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as target:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(target, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in reader:
            counters["raw_task_count"] += 1
            query_ok = bool(text(row.get("query_text")))
            counters["query_parseable_count"] += int(query_ok)

            candidate_services_ok, candidate_services = parse_list(row.get("candidate_services_json"))
            gold_services_ok, gold_services = parse_list(row.get("gold_services_json"))
            candidate_apis_ok, candidate_apis = parse_list(row.get("candidate_apis_json"))
            gold_apis_ok, gold_apis = parse_list(row.get("gold_apis_json"))

            candidate_service_ids = {service_id(item) for item in candidate_services} if candidate_services_ok else set()
            gold_service_ids = {service_id(item) for item in gold_services} if gold_services_ok else set()
            candidate_api_ids = {api_id(item) for item in candidate_apis} if candidate_apis_ok else set()
            gold_api_ids = {api_id(item) for item in gold_apis} if gold_apis_ok else set()
            service_subset = bool(gold_service_ids and gold_service_ids.issubset(candidate_service_ids))
            api_subset = bool(gold_api_ids and gold_api_ids.issubset(candidate_api_ids))
            service_mapping = bool(candidate_services_ok and gold_services_ok and service_subset)
            api_mapping = bool(
                candidate_apis_ok
                and gold_apis_ok
                and api_subset
                and all(service and api for service, api in candidate_api_ids | gold_api_ids)
            )
            counters["service_mapping_available_count"] += int(service_mapping)
            counters["api_mapping_available_count"] += int(api_mapping)

            candidate_api_count = len(candidate_api_ids)
            gold_api_count = len(gold_api_ids)
            candidate_service_count = len(candidate_service_ids)
            gold_service_count = len(gold_service_ids)
            negative_count = len(candidate_api_ids - gold_api_ids) if candidate_apis_ok and gold_apis_ok else 0
            equals_gold = bool(candidate_apis_ok and gold_apis_ok and candidate_api_ids == gold_api_ids)
            candidate_api_distribution[candidate_api_count] += 1
            gold_api_distribution[gold_api_count] += 1
            negative_distribution[negative_count] += 1
            counters["candidate_equals_gold_count"] += int(equals_gold)
            counters["no_negative_distractor_count"] += int(negative_count == 0)

            gold_services_for_api = {service for service, _ in gold_api_ids}
            same_service_negatives = sum(
                1
                for service, api in candidate_api_ids - gold_api_ids
                if service in gold_services_for_api
            )
            cross_service_negatives = negative_count - same_service_negatives
            counters["same_service_hard_negative_available_count"] += int(same_service_negatives > 0)
            counters["cross_service_hard_negative_available_count"] += int(cross_service_negatives > 0)

            api_leak = truthy(row.get("query_mentions_any_gold_api"))
            service_leak = truthy(row.get("query_mentions_any_gold_service"))
            counters["api_leak_risk_count"] += int(api_leak)
            counters["service_leak_risk_count"] += int(service_leak)
            estimated_valid = bool(
                query_ok
                and api_mapping
                and candidate_api_count > gold_api_count
                and negative_count > 0
                and not api_leak
            )
            counters["estimated_valid_api_recommendation_candidate_count"] += int(estimated_valid)

            reasons = []
            if not query_ok:
                reasons.append("query_missing")
            if not api_mapping:
                reasons.append("api_mapping_unavailable")
            if equals_gold:
                reasons.append("candidate_api_equals_gold")
            if negative_count == 0:
                reasons.append("no_api_distractor")
            if api_leak:
                reasons.append("query_mentions_gold_api")
            if service_leak:
                reasons.append("service_name_prior_evidence_only_for_api_task")
            if estimated_valid:
                reasons.append("estimated_candidate_space_feasible_requires_human_qa")

            hard_negative = (
                "same_service_available"
                if same_service_negatives > 0
                else "cross_service_only"
                if cross_service_negatives > 0
                else "not_available_in_current_row"
            )
            writer.writerow(
                {
                    "task_id": row.get("task_id", ""),
                    "source_query_id": row.get("source_query_id", ""),
                    "query_parse_ok": str(query_ok).lower(),
                    "service_mapping_available": str(service_mapping).lower(),
                    "api_mapping_available": str(api_mapping).lower(),
                    "candidate_service_count": candidate_service_count,
                    "gold_service_count": gold_service_count,
                    "candidate_api_count": candidate_api_count,
                    "gold_api_count": gold_api_count,
                    "gold_services_subset": str(service_subset).lower(),
                    "gold_apis_subset": str(api_subset).lower(),
                    "candidate_api_equals_gold": str(equals_gold).lower(),
                    "negative_api_distractor_count": negative_count,
                    "same_service_hard_negative_count": same_service_negatives,
                    "cross_service_hard_negative_count": cross_service_negatives,
                    "query_mentions_any_gold_api": str(api_leak).lower(),
                    "query_mentions_any_gold_service": str(service_leak).lower(),
                    "api_leak_risk": "blocking_candidate" if api_leak else "not_flagged",
                    "service_leak_risk": "prior_evidence" if service_leak else "not_flagged",
                    "estimated_valid_api_recommendation_candidate": str(estimated_valid).lower(),
                    "hard_negative_feasibility": hard_negative,
                    "feasibility_reasons": ";".join(reasons),
                }
            )

    raw_count = counters["raw_task_count"]
    summary = {
        "generated_at": now_iso(),
        "scope": "read_only_inventory_not_final_pool_not_automatic_cleaning",
        "input_file": str(input_path),
        "output_file": str(output_path),
        "raw_g1_task_count": raw_count,
        "query_parseable_count": counters["query_parseable_count"],
        "service_mapping_available_count": counters["service_mapping_available_count"],
        "api_mapping_available_count": counters["api_mapping_available_count"],
        "candidate_api_count_distribution": {str(k): v for k, v in sorted(candidate_api_distribution.items())},
        "gold_api_count_distribution": {str(k): v for k, v in sorted(gold_api_distribution.items())},
        "negative_api_distractor_count_distribution": {str(k): v for k, v in sorted(negative_distribution.items())},
        "candidate_equals_gold_count": counters["candidate_equals_gold_count"],
        "no_negative_distractor_count": counters["no_negative_distractor_count"],
        "api_leak_risk_count": counters["api_leak_risk_count"],
        "service_leak_risk_count": counters["service_leak_risk_count"],
        "estimated_valid_api_recommendation_candidate_count": counters[
            "estimated_valid_api_recommendation_candidate_count"
        ],
        "same_service_hard_negative_available_count": counters[
            "same_service_hard_negative_available_count"
        ],
        "cross_service_hard_negative_available_count": counters[
            "cross_service_hard_negative_available_count"
        ],
        "final_clean_pool_generated": False,
        "gold_modified_or_inferred": False,
        "real_api_called": False,
    }
    (output_dir / "inventory.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    estimate = summary["estimated_valid_api_recommendation_candidate_count"]
    estimate_rate = estimate / raw_count if raw_count else 0.0
    report = f"""# ToolBench G1 Single-API Inventory v0.1

Generated at: {summary['generated_at']}

Input: `{input_path}`

## Scope

This is a read-only feasibility inventory. It does not create a final G1 clean pool, infer missing gold APIs, declare every G1 row a single-API benchmark example, or call any real API.

## Counts

- raw G1 task count: {raw_count}
- parseable/non-empty query count: {summary['query_parseable_count']}
- usable service mapping count: {summary['service_mapping_available_count']}
- usable API mapping count: {summary['api_mapping_available_count']}
- candidate API equals gold API count: {summary['candidate_equals_gold_count']}
- no API negative distractor count: {summary['no_negative_distractor_count']}
- query gold-API mention risk count: {summary['api_leak_risk_count']}
- query gold-service mention evidence count: {summary['service_leak_risk_count']}
- estimated API-recommendation candidate-space feasible count: {estimate} ({estimate_rate:.2%})

The estimate is a structural candidate-space estimate only. It is not a clean count and still requires semantic, capability, leakage, and gold-integrity human QA.

## Candidate API count distribution

`{json.dumps(summary['candidate_api_count_distribution'], ensure_ascii=False)}`

## Gold API count distribution

`{json.dumps(summary['gold_api_count_distribution'], ensure_ascii=False)}`

## Negative API distractor distribution

`{json.dumps(summary['negative_api_distractor_count_distribution'], ensure_ascii=False)}`

## Hard-negative feasibility

- rows with same-service non-gold API candidates: {summary['same_service_hard_negative_available_count']}
- rows with only/also cross-service non-gold API candidates: {summary['cross_service_hard_negative_available_count']}

Recommended hard-negative sources, subject to later QA:

1. Non-gold sibling endpoints from the same service, preferred because they preserve domain and provider while changing operation.
2. APIs with overlapping descriptions/parameters but different action semantics.
3. Cross-service APIs from the same category when same-service siblings are unavailable.
4. Leakage-uncertain and candidate-equals-gold rows should form explicit QA strata, not be silently repaired.

## Recommended QA sample design

Use a stratified sample across:

- candidate-equals-gold versus real API choice space;
- 1, 2-3, 4-10, and more than 10 API candidates;
- gold API mention flagged/unflagged;
- gold service mention flagged/unflagged;
- same-service hard negatives versus cross-service only;
- single versus multiple gold APIs;
- mapping failures and parse failures.

The human reviewer must verify query-gold semantics, capability coverage, true API leak, gold-set integrity, and whether the row genuinely defines a single-API recommendation target.

## Remaining blockers

- G1 raw task type currently comes from the original ToolBench grouping and is not itself proof of a valid single-API benchmark target.
- Candidate-space feasibility does not prove semantic correctness or gold completeness.
- Direct endpoint names in queries can create blocking API leakage.
- Rows with multiple gold APIs may require a multi-API label or source-only handling.
- A dedicated human QA branch is still required before any G1 source freeze or six-task assembly.
"""
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
