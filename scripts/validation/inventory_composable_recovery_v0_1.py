#!/usr/bin/env python
"""Read-only inventory of objective composable evidence availability."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


OUTPUT_FIELDS = [
    "inventory_id",
    "task_id",
    "source_dataset",
    "source_group",
    "source_query_id",
    "query_text",
    "current_policy_status",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "trace_or_trajectory_available",
    "answer_record_available",
    "trace_model_count",
    "trace_models_json",
    "step_count",
    "tool_api_sequence_available",
    "argument_flow_available",
    "repeated_entity_available",
    "output_input_evidence_available",
    "control_conditional_evidence_available",
    "observed_tool_sequence_json",
    "source_dependency_evidence_available",
    "source_dependency_evidence_json",
    "evidence_notes",
]

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{4,}")
CONTROL_PATTERN = re.compile(
    r"\b(if|then|depending on|based on (?:the )?(?:result|response|output)|using (?:the )?(?:result|response|output)|after (?:finding|retrieving|getting))\b",
    re.IGNORECASE,
)
STOP_TOKENS = {
    "true",
    "false",
    "null",
    "none",
    "string",
    "number",
    "query",
    "search",
    "result",
    "response",
    "error",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    return str(value or "").strip()


def truthy(value: Any) -> bool:
    return text(value).lower() in {"1", "true", "yes"}


def tokens(value: Any) -> set[str]:
    return {
        token.casefold()
        for token in TOKEN_PATTERN.findall(str(value or ""))
        if token.casefold() not in STOP_TOKENS
    }


def collect_tool_calls(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    calls: list[dict[str, str]] = []
    text_fragments: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            role = text(node.get("role")).lower()
            message = node.get("message")
            if role == "tool" and isinstance(message, dict):
                name = text(message.get("name"))
                if name and name.casefold() != "finish":
                    calls.append(
                        {
                            "name": name,
                            "arguments": text(message.get("arguments")),
                            "response": text(message.get("response")),
                        }
                    )
            elif isinstance(message, str) and message:
                text_fragments.append(message)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, str) and node:
            text_fragments.append(node)

    visit(value)
    return calls, text_fragments


def analyze_answer_record(record: dict[str, Any]) -> dict[str, Any]:
    answer = record.get("answer")
    if not isinstance(answer, dict):
        return {
            "answer_available": False,
            "step_count": 0,
            "tool_calls": [],
            "tool_sequence": False,
            "argument_flow": False,
            "repeated_entity": False,
            "output_input": False,
            "control_conditional": False,
        }
    calls, trace_text = collect_tool_calls(answer.get("answer_details", []))
    step_count = 0
    try:
        step_count = int(answer.get("total_steps", 0) or 0)
    except (TypeError, ValueError):
        step_count = len(calls)
    step_count = max(step_count, len(calls))
    argument_token_sets = [tokens(call["arguments"]) for call in calls]
    repeated_entity = any(
        argument_token_sets[left] & argument_token_sets[right]
        for left in range(len(argument_token_sets))
        for right in range(left + 1, len(argument_token_sets))
    )
    output_input = False
    for later_index in range(1, len(calls)):
        later_args = tokens(calls[later_index]["arguments"])
        prior_output = set().union(
            *(tokens(calls[index]["response"]) for index in range(later_index))
        )
        if later_args & prior_output:
            output_input = True
            break
    control_conditional = bool(CONTROL_PATTERN.search(" ".join(trace_text)))
    return {
        "answer_available": True,
        "step_count": step_count,
        "tool_calls": calls,
        "tool_sequence": len(calls) > 0,
        "argument_flow": len(calls) >= 2 and all(call["arguments"] for call in calls[:2]),
        "repeated_entity": repeated_entity,
        "output_input": output_input,
        "control_conditional": control_conditional,
    }


def build_prediction_index(converted_root: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    files: list[str] = []
    if not converted_root.exists():
        return index, files
    for model_dir in sorted(path for path in converted_root.iterdir() if path.is_dir()):
        path = model_dir / "G3_instruction.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        files.append(str(path))
        for query_id, record in payload.items():
            if isinstance(record, dict):
                index[str(query_id)].append(
                    {
                        "model": model_dir.name,
                        "path": str(path),
                        "record": record,
                    }
                )
    return index, files


def aggregate_trace_evidence(records: list[dict[str, Any]]) -> dict[str, Any]:
    analyses: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in records:
        analyses.append((item, analyze_answer_record(item["record"])))
    if not analyses:
        return {
            "trace_available": False,
            "answer_available": False,
            "models": [],
            "step_count": 0,
            "tool_sequence": False,
            "argument_flow": False,
            "repeated_entity": False,
            "output_input": False,
            "control_conditional": False,
            "observed_sequence": [],
        }
    best_item, best = max(
        analyses,
        key=lambda pair: (
            len(pair[1]["tool_calls"]),
            pair[1]["step_count"],
            pair[0]["model"],
        ),
    )
    return {
        "trace_available": True,
        "answer_available": any(analysis["answer_available"] for _, analysis in analyses),
        "models": sorted(item["model"] for item, _ in analyses),
        "step_count": max(analysis["step_count"] for _, analysis in analyses),
        "tool_sequence": any(analysis["tool_sequence"] for _, analysis in analyses),
        "argument_flow": any(analysis["argument_flow"] for _, analysis in analyses),
        "repeated_entity": any(analysis["repeated_entity"] for _, analysis in analyses),
        "output_input": any(analysis["output_input"] for _, analysis in analyses),
        "control_conditional": any(analysis["control_conditional"] for _, analysis in analyses),
        "observed_sequence": [call["name"] for call in best["tool_calls"]],
        "best_trace_model": best_item["model"],
    }


def parse_json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(text(value))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only composable recovery evidence inventory.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--toolbench-policy",
        default="outputs/policy_v1_5f_tightening_dryrun/clean_candidates_v1_4c_with_v1_5f_annotations.csv",
    )
    parser.add_argument(
        "--stable-pack",
        default="outputs/source_qa_adjudication_v0_3/stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_3.csv",
    )
    parser.add_argument(
        "--converted-traces",
        default="external_sources/ToolBench/reproduction_data/model_predictions_converted",
    )
    parser.add_argument(
        "--output-dir", default="outputs/composable_recovery_inventory_v0_1"
    )
    parser.add_argument(
        "--report", default="docs/phase1/composable_recovery_inventory_v0_1.md"
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    tool_path = root / args.toolbench_policy
    stable_path = root / args.stable_pack
    trace_root = root / args.converted_traces
    output_dir = root / args.output_dir
    report_path = root / args.report
    for required in [tool_path, stable_path]:
        if not required.exists():
            raise SystemExit(f"Required inventory input is missing: {required}")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

    prediction_index, trace_files = build_prediction_index(trace_root)
    inventory_rows: list[dict[str, Any]] = []

    with tool_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if "composable" not in text(row.get("task_type")).lower():
                continue
            policy = text(row.get("v1_5f_dryrun_decision"))
            if policy not in {"still_clean_candidate", "downgrade_to_uncertain"}:
                continue
            query_id = text(row.get("source_query_id"))
            evidence = aggregate_trace_evidence(prediction_index.get(query_id, []))
            inventory_rows.append(
                {
                    "inventory_id": f"TB-COMP-{len(inventory_rows)+1:04d}",
                    "task_id": row.get("task_id", ""),
                    "source_dataset": "ToolBench",
                    "source_group": row.get("source_group", "G3"),
                    "source_query_id": query_id,
                    "query_text": row.get("query_text", ""),
                    "current_policy_status": policy,
                    "candidate_services_json": row.get("candidate_services_json", ""),
                    "candidate_apis_json": row.get("candidate_apis_json", ""),
                    "gold_services_json": row.get("gold_services_json", ""),
                    "gold_apis_json": row.get("gold_apis_json", ""),
                    "trace_or_trajectory_available": str(evidence["trace_available"]).lower(),
                    "answer_record_available": str(evidence["answer_available"]).lower(),
                    "trace_model_count": len(evidence["models"]),
                    "trace_models_json": json.dumps(evidence["models"], ensure_ascii=False),
                    "step_count": evidence["step_count"],
                    "tool_api_sequence_available": str(evidence["tool_sequence"]).lower(),
                    "argument_flow_available": str(evidence["argument_flow"]).lower(),
                    "repeated_entity_available": str(evidence["repeated_entity"]).lower(),
                    "output_input_evidence_available": str(evidence["output_input"]).lower(),
                    "control_conditional_evidence_available": str(evidence["control_conditional"]).lower(),
                    "observed_tool_sequence_json": json.dumps(evidence["observed_sequence"], ensure_ascii=False),
                    "source_dependency_evidence_available": "false",
                    "source_dependency_evidence_json": "[]",
                    "evidence_notes": "Model trajectory availability only; not a strong-composable judgment.",
                }
            )

    tool_count = len(inventory_rows)
    with stable_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if text(row.get("stable_group") or row.get("source_group")).upper() != "G3":
                continue
            source_evidence = parse_json_list(row.get("dependency_chain_evidence_json"))
            source_available = truthy(row.get("dependency_chain_evidence_available")) or bool(source_evidence)
            inventory_rows.append(
                {
                    "inventory_id": f"STB-COMP-{len(inventory_rows)-tool_count+1:04d}",
                    "task_id": row.get("task_id", ""),
                    "source_dataset": "StableToolBench",
                    "source_group": row.get("stable_group") or row.get("source_group", "G3"),
                    "source_query_id": row.get("source_query_id", ""),
                    "query_text": row.get("query_text", ""),
                    "current_policy_status": row.get("stable_policy_primary_decision_derived")
                    or row.get("stable_policy_decision", ""),
                    "candidate_services_json": row.get("candidate_services_json", ""),
                    "candidate_apis_json": row.get("candidate_apis_json", ""),
                    "gold_services_json": row.get("gold_services_json", ""),
                    "gold_apis_json": row.get("gold_apis_json", ""),
                    "trace_or_trajectory_available": "false",
                    "answer_record_available": "false",
                    "trace_model_count": 0,
                    "trace_models_json": "[]",
                    "step_count": 0,
                    "tool_api_sequence_available": "false",
                    "argument_flow_available": "false",
                    "repeated_entity_available": "false",
                    "output_input_evidence_available": "false",
                    "control_conditional_evidence_available": "false",
                    "observed_tool_sequence_json": "[]",
                    "source_dependency_evidence_available": str(source_available).lower(),
                    "source_dependency_evidence_json": json.dumps(source_evidence, ensure_ascii=False),
                    "evidence_notes": "StableToolBench source metadata only; no ToolBench trajectory join attempted.",
                }
            )

    output_csv = output_dir / "candidate_inventory.csv"
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(inventory_rows)

    source_counts = Counter(row["source_dataset"] for row in inventory_rows)
    policy_counts = Counter(
        (row["source_dataset"], row["current_policy_status"]) for row in inventory_rows
    )
    availability_fields = [
        "trace_or_trajectory_available",
        "answer_record_available",
        "tool_api_sequence_available",
        "argument_flow_available",
        "repeated_entity_available",
        "output_input_evidence_available",
        "control_conditional_evidence_available",
        "source_dependency_evidence_available",
    ]
    availability_counts = {
        field: sum(row[field] == "true" for row in inventory_rows)
        for field in availability_fields
    }
    summary = {
        "generated_at": now_iso(),
        "scope": "read_only_objective_evidence_inventory_no_strong_composable_label",
        "inputs": {
            "toolbench_policy": str(tool_path),
            "stabletoolbench_pack": str(stable_path),
            "converted_trace_root": str(trace_root),
        },
        "output_file": str(output_csv),
        "candidate_count": len(inventory_rows),
        "source_counts": dict(source_counts),
        "toolbench_still_clean_composable_count": sum(
            row["source_dataset"] == "ToolBench"
            and row["current_policy_status"] == "still_clean_candidate"
            for row in inventory_rows
        ),
        "toolbench_uncertain_composable_count": sum(
            row["source_dataset"] == "ToolBench"
            and row["current_policy_status"] == "downgrade_to_uncertain"
            for row in inventory_rows
        ),
        "stabletoolbench_g3_count": source_counts.get("StableToolBench", 0),
        "policy_status_distribution": {
            f"{source}::{status}": count
            for (source, status), count in sorted(policy_counts.items())
        },
        "availability_counts": availability_counts,
        "converted_trace_file_count": len(trace_files),
        "converted_trace_files": trace_files,
        "strong_composable_labels_generated": 0,
        "dependency_edges_generated": 0,
        "llm_judge_used": False,
    }
    (output_dir / "evidence_availability_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report = f"""# Composable Recovery Inventory v0.1

Generated at: {summary['generated_at']}

## Scope

This is a read-only inventory of evidence availability. It does not confirm strong composability, generate dependency edges, call an LLM judge, or create a final composable pool.

## Inputs

- ToolBench policy table: `{tool_path}`
- StableToolBench supplemental pack: `{stable_path}`
- ToolBench converted trajectory root: `{trace_root}`

## Candidate counts

- ToolBench still-clean composable: {summary['toolbench_still_clean_composable_count']}
- ToolBench uncertain composable: {summary['toolbench_uncertain_composable_count']}
- StableToolBench G3: {summary['stabletoolbench_g3_count']}
- Total inventory rows: {summary['candidate_count']}

## Objective evidence availability

`{json.dumps(summary['availability_counts'], ensure_ascii=False)}`

- Converted ToolBench G3 trace files inspected: {summary['converted_trace_file_count']}
- `step_count` is the maximum observed step count across locally available converted model trajectories for the query ID.
- Tool/API sequence availability records whether at least one tool call exists.
- Argument-flow availability records whether multiple calls expose arguments; it does not prove dependency.
- Repeated-entity availability records token reuse across call arguments.
- Output-input evidence availability records token overlap from an earlier response into later arguments.
- Control/conditional evidence availability records explicit conditional/result-use language in trajectory text.

These are observable evidence signals, not a composable decision. Model trajectories can be failed, hallucinated, or behaviorally unrelated to the benchmark gold set.

## Policy status distribution

`{json.dumps(summary['policy_status_distribution'], ensure_ascii=False)}`

## Remaining blockers

1. Raw G3 membership does not establish cross-service dependency.
2. Most source rows may lack locally available successful trajectories.
3. A model-generated trajectory is not human-confirmed gold evidence.
4. Output-input token overlap is only a retrieval aid and requires human interpretation.
5. StableToolBench G3 rows require separate target adjudication and dependency review.
6. Strong composable positives still require human confirmation of how an earlier output changes a later input, selection, or control decision.

## Recommended next review

Stratify human review by output-input evidence, repeated entity, multi-call sequence, current policy status, and source. Include evidence-absent rows as controls. Do not auto-promote any row to strong composable.
"""
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(inventory_rows) == 322 else 1


if __name__ == "__main__":
    raise SystemExit(main())
