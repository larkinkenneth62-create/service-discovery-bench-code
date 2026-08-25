from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


QUOTED_STRING = re.compile(r'"((?:\\.|[^"\\])*)"')


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def extract_input_json(request: dict[str, Any]) -> dict[str, Any]:
    messages = request["payload"]["messages"]
    user_content = next(message["content"] for message in messages if message["role"] == "user")
    marker = "INPUT_JSON="
    if marker not in user_content:
        raise ValueError("request user message has no INPUT_JSON marker")
    encoded = user_content.split(marker, 1)[1].splitlines()[0]
    return json.loads(encoded)


def extract_candidate_like_strings(content: str, pool: set[str]) -> list[str]:
    prefixes = {candidate.split("::", 1)[0] + "::" for candidate in pool if "::" in candidate}
    values: list[str] = []
    for match in QUOTED_STRING.finditer(content):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if value in pool or any(value.startswith(prefix) for prefix in prefixes):
            values.append(value)
    return values


def classify_candidate_stream(content: str, candidates: list[str]) -> dict[str, Any]:
    pool = set(candidates)
    emitted = extract_candidate_like_strings(content, pool)
    seen: set[str] = set()
    first_duplicate_position: int | None = None
    first_duplicate_id: str | None = None
    first_out_of_pool_position: int | None = None
    first_out_of_pool_id: str | None = None
    for position, candidate in enumerate(emitted, 1):
        if candidate not in pool and first_out_of_pool_position is None:
            first_out_of_pool_position = position
            first_out_of_pool_id = candidate
        if candidate in seen and first_duplicate_position is None:
            first_duplicate_position = position
            first_duplicate_id = candidate
        seen.add(candidate)
    schema_violation_before_cutoff = (
        first_duplicate_position is not None or first_out_of_pool_position is not None
    )
    return {
        "classification": (
            "MODEL_FORMAT_FAILURE_BEFORE_LENGTH"
            if schema_violation_before_cutoff
            else "POTENTIAL_CLEAN_PREFIX_BUDGET_TRUNCATION"
        ),
        "schema_violation_before_cutoff": schema_violation_before_cutoff,
        "candidate_count": len(candidates),
        "emitted_candidate_like_count": len(emitted),
        "unique_emitted_count": len(set(emitted)),
        "first_duplicate_position": first_duplicate_position,
        "first_duplicate_id": first_duplicate_id,
        "first_out_of_pool_position": first_out_of_pool_position,
        "first_out_of_pool_id": first_out_of_pool_id,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def artifact_directory(run_dir: Path, status: dict[str, Any]) -> Path:
    attempts = status.get("attempts") or []
    if not attempts:
        raise ValueError(f"status has no attempts: {status.get('request_id')}")
    raw_path = attempts[-1].get("raw_sse_events_path")
    if not raw_path:
        raise ValueError(f"terminal attempt has no raw path: {status.get('request_id')}")
    return (run_dir / raw_path).resolve().parent


def classify_length_row(run_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    directory = artifact_directory(run_dir, status)
    request_path = directory / "request.json"
    response_path = directory / "final_response.json"
    request = load_json(request_path)
    response = load_json(response_path)
    input_json = extract_input_json(request)
    candidates = [row["candidate_id"] for row in input_json["candidate_documents"]]
    content = response["response"]["choices"][0]["message"].get("content") or ""
    result = classify_candidate_stream(content, candidates)
    result.update(
        {
            "request_id": status["request_id"],
            "finish_reason": status.get("finish_reason"),
            "parse_status": status.get("parse_status"),
            "model_status": status.get("status"),
            "terminal_event_received": status.get("terminal_event_received"),
            "done_received": status.get("done_received"),
            "http_status": status.get("http_status"),
            "response_model": status.get("response_model"),
            "frozen_max_tokens": status.get("frozen_max_tokens"),
            "retry_count": status.get("retry_count"),
            "heartbeat_count": status.get("heartbeat_count"),
            "request_artifact": str(request_path),
            "request_artifact_sha256": sha256_file(request_path),
            "response_artifact": str(response_path),
            "response_artifact_sha256": sha256_file(response_path),
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--strict-gate", type=Path, required=True)
    parser.add_argument("--budget-freeze", type=Path, required=True)
    parser.add_argument("--budget-validation", type=Path, required=True)
    parser.add_argument("--governance-plan", type=Path, required=True)
    parser.add_argument("--governance-protocol", type=Path, required=True)
    parser.add_argument("--luna-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    strict_gate = load_json(args.strict_gate)
    budget_freeze = load_json(args.budget_freeze)
    budget_validation = load_json(args.budget_validation)
    applied_max_tokens = int(budget_validation["applied_native_frozen_max_tokens"])
    required_max_tokens = int(budget_validation["smoke_required_with_10_percent_margin"])
    worst_legal_json_tokens = int(budget_validation["worst_legal_output_tokens"])
    statuses = [
        json.loads(line)
        for line in (args.run_dir / "REQUEST_STATUS.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    length_rows = [row for row in statuses if row.get("finish_reason") == "length"]
    classifications = [classify_length_row(args.run_dir, row) for row in length_rows]

    strict_checks = strict_gate.get("checks", {})
    strict_other_checks = {
        name: value for name, value in strict_checks.items() if name != "no_output_length_finish"
    }
    checks = {
        "strict_gate_failed_only_on_absolute_length_rule": (
            strict_gate.get("status") == "FAIL"
            and strict_checks.get("no_output_length_finish") is False
            and bool(strict_other_checks)
            and all(value is True for value in strict_other_checks.values())
        ),
        "all_60_status_rows_present": len(statuses) == 60,
        "length_rows_present": bool(length_rows),
        "all_length_rows_terminal_model_failures": all(
            row.get("status") == "parse_failure"
            and row.get("parse_status") == "invalid"
            and row.get("terminal_event_received") is True
            and row.get("done_received") is True
            and row.get("http_status") == 200
            for row in length_rows
        ),
        "all_length_rows_have_prior_schema_violation": all(
            row["schema_violation_before_cutoff"] is True for row in classifications
        ),
        "no_clean_valid_prefix_budget_truncation": not any(
            row["classification"] == "POTENTIAL_CLEAN_PREFIX_BUDGET_TRUNCATION"
            for row in classifications
        ),
        "budget_validation_pass": budget_validation.get("status") == "PASS",
        "budget_covers_worst_legal_json_with_margin": (
            applied_max_tokens >= required_max_tokens > worst_legal_json_tokens
        ),
        "budget_freeze_matches_status_rows": all(
            row.get("budget_freeze_sha256") == sha256_file(args.budget_freeze)
            and row.get("frozen_max_tokens") == applied_max_tokens
            for row in statuses
        ),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    output = {
        "schema_version": 1,
        "gate": "Q1_GOVERNANCE_ADJUDICATED",
        "status": status,
        "decision": (
            "PROCEED_Q2_MODEL_FORMAT_FAILURES_NONBLOCKING"
            if status == "PASS"
            else "BLOCK_Q2_UNRESOLVED_LENGTH_OR_BINDING_FAILURE"
        ),
        "checks": checks,
        "rationale": {
            "governance_rule": "Model format failures may pass smoke; widespread infrastructure/budget truncation may not.",
            "classification_rule": "A length response is nonblocking only when duplicate or out-of-pool candidate output proves the schema was already violated before cutoff.",
            "non_repair_statement": "No response is repaired, completed, deduplicated, or rescored by this adjudication.",
        },
        "counts": {
            "request_status_rows": len(statuses),
            "length_rows": len(length_rows),
            "model_format_failure_before_length": sum(
                row["classification"] == "MODEL_FORMAT_FAILURE_BEFORE_LENGTH"
                for row in classifications
            ),
            "potential_clean_prefix_budget_truncation": sum(
                row["classification"] == "POTENTIAL_CLEAN_PREFIX_BUDGET_TRUNCATION"
                for row in classifications
            ),
        },
        "length_classifications": classifications,
        "bindings": {
            "strict_gate": {"path": str(args.strict_gate), "sha256": sha256_file(args.strict_gate)},
            "budget_freeze": {"path": str(args.budget_freeze), "sha256": sha256_file(args.budget_freeze)},
            "budget_validation": {"path": str(args.budget_validation), "sha256": sha256_file(args.budget_validation)},
            "governance_plan": {"path": str(args.governance_plan), "sha256": sha256_file(args.governance_plan)},
            "governance_protocol": {"path": str(args.governance_protocol), "sha256": sha256_file(args.governance_protocol)},
            "luna_review": {"path": str(args.luna_review), "sha256": sha256_file(args.luna_review)},
        },
        "budget_evidence": {
            "budget_freeze_schema_version": budget_freeze.get("schema_version"),
            "applied_max_tokens": applied_max_tokens,
            "required_max_tokens": required_max_tokens,
            "worst_legal_json_tokens": worst_legal_json_tokens,
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(args.output, output)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if status == "PASS" else 2)


if __name__ == "__main__":
    main()
