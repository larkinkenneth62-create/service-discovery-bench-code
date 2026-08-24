"""Conservative cleaning policy trace generator v0.7.

This is a skeleton, not a full-cleaning execution script. Its default behavior
is dry-run trace generation only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

VALIDATION_DIR = Path(__file__).resolve().parents[1] / "validation"
if str(VALIDATION_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATION_DIR))

from cleaning_policy_v0_7_utils import (  # noqa: E402
    AUDIT_EVIDENCE_PATH,
    EVIDENCE_COLUMNS,
    OUTPUT_DIR,
    apply_v42_policy,
    as_boolish,
    ensure_dirs,
    gold_in_candidates,
    norm_candidate,
    norm_coverage,
    norm_decision,
    norm_leakage,
    norm_semantic,
    norm_task_check,
    now_str,
    read_csv,
    resolve_count,
    write_csv,
)


TRACE_COLUMNS = [
    *EVIDENCE_COLUMNS,
    "cleaning_decision",
    "cleaning_bucket",
    "blocking_reasons",
    "warning_reasons",
    "triggered_rules",
    "detector_status",
    "requires_human_or_llm_review",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply v4.2 conservative policy to a task/evidence CSV and write trace only. "
            "Default mode is dry-run; this script is not allowed to run full cleaning in v0.7."
        )
    )
    parser.add_argument("--input", type=Path, default=AUDIT_EVIDENCE_PATH, help="Task-level or evidence CSV input.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--trace-output", type=Path, default=None, help="Optional all-rows trace CSV path.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Dry-run trace mode; enabled by default.")
    parser.add_argument(
        "--allow-write-clean-output",
        action="store_true",
        help="Explicitly allow writing a clean-ready subset. Not used in v0.7 validation commands.",
    )
    parser.add_argument("--clean-output", type=Path, default=None, help="Optional clean-ready output path.")
    return parser


def coerce_policy_input(row: Dict[str, str], fallback_round: str = "task_level_input") -> Dict[str, object]:
    service_in, api_in = gold_in_candidates(row)
    return {
        "audit_round": row.get("audit_round") or fallback_round,
        "review_id": row.get("review_id") or row.get("round3_review_id") or row.get("round2_review_id") or "",
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_dataset": row.get("source_dataset", "not_available") or "not_available",
        "source_group": row.get("source_group", "not_available") or "not_available",
        "query_text": row.get("query_text", ""),
        "risk_category": row.get("risk_category", "not_available") or "not_available",
        "risk_subtype": row.get("risk_subtype", "not_available") or "not_available",
        "manual_final_decision": norm_decision(row.get("manual_final_decision", "not_available")),
        "semantic_alignment_check": norm_semantic(row.get("semantic_alignment_check", "not_available")),
        "capability_coverage_check": norm_coverage(row.get("capability_coverage_check", "not_available")),
        "leakage_check": norm_leakage(row.get("leakage_check", "not_available")),
        "candidate_validity_check": norm_candidate(row.get("candidate_validity_check", "not_available")),
        "task_type_check": norm_task_check(row.get("task_type_check", "not_available")),
        "candidate_service_count": row.get("candidate_service_count_resolved")
        or resolve_count(row, "candidate_service_count", "candidate_services_json"),
        "gold_service_count": row.get("gold_service_count_resolved")
        or resolve_count(row, "gold_service_count", "gold_services_json"),
        "candidate_api_count": row.get("candidate_api_count_resolved")
        or resolve_count(row, "candidate_api_count", "candidate_apis_json"),
        "gold_api_count": row.get("gold_api_count_resolved") or resolve_count(row, "gold_api_count", "gold_apis_json"),
        "query_mentions_any_gold_api": as_boolish(row.get("query_mentions_any_gold_api", "not_available")),
        "query_mentions_any_gold_service": as_boolish(row.get("query_mentions_any_gold_service", "not_available")),
        "gold_in_candidate_services": row.get("gold_in_candidate_services") or service_in,
        "gold_in_candidate_apis": row.get("gold_in_candidate_apis") or api_in,
        "human_notes": row.get("human_notes") or row.get("manual_decision_reason") or "",
    }


def write_split_traces(output_dir: Path, trace_rows: List[Dict[str, object]]) -> List[Path]:
    paths: List[Path] = []
    by_round: Dict[str, List[Dict[str, object]]] = {}
    for row in trace_rows:
        by_round.setdefault(str(row.get("audit_round", "unknown")), []).append(row)
    for audit_round, subset in sorted(by_round.items()):
        if audit_round in {"manual40", "round2", "round3"}:
            path = output_dir / f"conservative_policy_trace_{audit_round}.csv"
            write_csv(path, subset, TRACE_COLUMNS)
            paths.append(path)
    return paths


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_dirs()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.input.exists():
        print(f"ERROR: input CSV does not exist: {args.input}")
        return 1

    _, raw_rows = read_csv(args.input)
    trace_rows = [apply_v42_policy(coerce_policy_input(row)) for row in raw_rows]
    trace_path = args.trace_output or (args.output_dir / "conservative_policy_trace_all_rounds.csv")
    write_csv(trace_path, trace_rows, TRACE_COLUMNS)
    split_paths = write_split_traces(args.output_dir, trace_rows)

    print(f"Generated time: {now_str()}")
    print(f"Input: {args.input}")
    print(f"Rows traced: {len(trace_rows)}")
    print(f"Wrote trace: {trace_path}")
    for path in split_paths:
        print(f"Wrote split trace: {path}")

    if args.clean_output and not args.allow_write_clean_output:
        print("ERROR: refusing to write clean output without --allow-write-clean-output.")
        return 2
    if args.clean_output and args.allow_write_clean_output:
        clean_rows = [row for row in trace_rows if row.get("cleaning_decision") == "keep_for_cleaning_candidate"]
        write_csv(args.clean_output, clean_rows, TRACE_COLUMNS)
        print(f"Wrote explicit clean-ready subset: {args.clean_output} ({len(clean_rows)} rows)")
        print("WARNING: v0.7 validation does not treat this as a final clean dataset.")
    else:
        print("No clean dataset was written; dry-run trace mode only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
