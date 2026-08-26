from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import statistics
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OFFICIAL_MODEL_ID = "Qwen/Qwen3.8-27B-FP8"
MODEL = "qwen3.8-27b-fp8"
TOKENIZER_REVISION = "RUNTIME_REPORTED_OR_UNAVAILABLE"
TOKEN_COUNTER_REVISION = "UTF8_BYTE_UPPER_BOUND_PLUS_REASONING_4096_V1"
REVISION = "QWEN38_SSE_THINKING_SELECTION_V1_7"
EXPECTED_FORMAL_ROWS = {"smoke": 60, "machine": 197, "native": 4798}
ALLOWED_COMPLETE = {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def status_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "REQUEST_STATUS.jsonl"
    if not path.is_file():
        raise ValueError(f"missing request status: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [row.get("request_id") for row in rows]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError(f"invalid request ID in {path}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate request ID in {path}")
    return rows


def validate_q0(path: Path) -> dict[str, Any]:
    report = read_json(path)
    if report.get("status") != "PASS" or report.get("experiment_revision") != REVISION:
        raise ValueError("Q0 report is not a passing V1.7 report")
    if report.get("benchmark_rows_transmitted") != 0 or report.get("q0_requests") != 8:
        raise ValueError("Q0 must contain eight synthetic requests and zero benchmark rows")
    results = report.get("results")
    if not isinstance(results, list) or len(results) != 8:
        raise ValueError("Q0 result count mismatch")
    expected_pairs = {
        (slot, contract)
        for slot in range(1, 5)
        for contract in ("TOP5_RANKING_V1", "SELECTED_SET_V1")
    }
    actual_pairs = {(row.get("key_slot"), row.get("output_contract")) for row in results if isinstance(row, dict)}
    if actual_pairs != expected_pairs:
        raise ValueError("Q0 does not cover four slots and both output contracts exactly once")
    for row in results:
        required = (
            row.get("http_status") == 200
            and int(row.get("heartbeat_count", 0)) >= 1
            and row.get("terminal_event_received") is True
            and row.get("done_received") is True
            and row.get("response_model") == MODEL
            and row.get("final_content_present") is True
            and row.get("reasoning_content_present") is True
            and int(row.get("reasoning_char_count", 0)) > 0
            and row.get("parse_valid") is True
            and row.get("finish_reason") == "stop"
            and row.get("error_code") is None
        )
        if not required:
            raise ValueError(f"Q0 gate evidence is incomplete for slot/case {row.get('key_slot')}/{row.get('case')}")
    return report


def validate_track(run_dir: Path, label: str, expected_rows: int, *, formal: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary_path = run_dir / "RUN_SUMMARY.json"
    if not summary_path.is_file():
        raise ValueError(f"missing run summary: {summary_path}")
    summary = read_json(summary_path)
    rows = status_rows(run_dir)
    if summary.get("status") not in ALLOWED_COMPLETE:
        raise ValueError(f"{label} is not complete: {summary.get('status')}")
    expected = expected_rows if formal else len(rows)
    if len(rows) != expected:
        raise ValueError(f"{label} row mismatch: {len(rows)} != {expected}")
    if int(summary.get("terminal_rows", len(rows))) != len(rows):
        raise ValueError(f"{label} summary terminal count mismatch")
    if formal and int(summary.get("requested_rows", expected)) != expected:
        raise ValueError(f"{label} summary requested count mismatch")
    blocking = Counter(row.get("status") for row in rows if row.get("status") in {"infra_error", "api_error"})
    if blocking:
        raise ValueError(f"{label} contains unresolved blocking statuses: {dict(blocking)}")
    revisions = {row.get("experiment_revision") for row in rows}
    if formal and revisions != {REVISION}:
        raise ValueError(f"{label} mixes experiment revisions: {sorted(map(str, revisions))}")
    if formal and summary.get("experiment_revision") != REVISION:
        raise ValueError(f"{label} summary has the wrong experiment revision")
    if formal:
        provenance_fields = {
            "runner_sha256", "parser_sha256", "prompt_contract_sha256",
            "output_contract_registry_sha256", "runtime_freeze_sha256",
            "budget_freeze_sha256", "source_manifest_sha256",
        }
        for row in rows:
            if not provenance_fields.issubset(row):
                raise ValueError(f"{label} row lacks frozen provenance: {row.get('request_id')}")
            if row.get("response_model") != MODEL or row.get("terminal_event_received") is not True or row.get("done_received") is not True:
                raise ValueError(f"{label} row violates terminal/model contract: {row.get('request_id')}")
            if row.get("reasoning_content_present") is not True or int(row.get("reasoning_char_count", 0)) <= 0:
                raise ValueError(f"{label} row lacks separate preserved reasoning: {row.get('request_id')}")
            if not isinstance(row.get("reasoning_sha256"), str) or not isinstance(row.get("content_sha256"), str):
                raise ValueError(f"{label} row lacks content/reasoning hashes: {row.get('request_id')}")
    return summary, rows


def copy_parsed(run_dir: Path, target: Path, label: str, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        relative = row.get("parsed_prediction_path")
        if not isinstance(relative, str):
            continue
        source = (run_dir / relative).resolve()
        root = run_dir.resolve()
        if source != root and root not in source.parents:
            raise ValueError(f"parsed prediction path escapes {label} run root")
        if not source.is_file():
            raise ValueError(f"missing parsed prediction: {source}")
        destination = target / "parsed_predictions" / label / f"{hashlib.sha256(row['request_id'].encode()).hexdigest()[:24]}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        count += 1
    return count


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row["end_to_end_latency_ms"]) for row in rows if row.get("end_to_end_latency_ms") is not None]
    reasoning_chars = [float(row["reasoning_char_count"]) for row in rows if row.get("reasoning_char_count") is not None]
    return {
        "rows": len(rows),
        "status_counts": dict(Counter(row.get("status") for row in rows)),
        "parse_failure_taxonomy": dict(Counter(row.get("error_code") for row in rows if row.get("status") == "parse_failure")),
        "heartbeat_total": sum(int(row.get("heartbeat_count", 0)) for row in rows),
        "retry_total": sum(int(row.get("retry_count", 0)) for row in rows),
        "reasoning_content_present_rows": sum(bool(row.get("reasoning_content_present")) for row in rows),
        "reasoning_char_count": {
            "mean": statistics.fmean(reasoning_chars) if reasoning_chars else None,
            "p50": percentile(reasoning_chars, 0.50),
            "p95": percentile(reasoning_chars, 0.95),
            "max": max(reasoning_chars) if reasoning_chars else None,
        },
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
    }


def ensure_empty_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"output directory is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build private Qwen3.8 SSE Thinking Selection V1.7 result bundle")
    parser.add_argument("--mode", choices=("formal", "synthetic"), default="formal")
    parser.add_argument("--smoke-dir", type=Path, required=True)
    parser.add_argument("--machine-dir", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--scores-dir", type=Path, required=True)
    parser.add_argument("--prompt-contract", type=Path, required=True)
    parser.add_argument("--output-contract-registry", type=Path, required=True)
    parser.add_argument("--token-budget-freeze", type=Path, required=True)
    parser.add_argument("--q0-report", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--execution-plan", type=Path)
    parser.add_argument("--model-registry", type=Path)
    parser.add_argument("--runtime-freeze", type=Path)
    parser.add_argument("--prompt-registry", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    args = parser.parse_args()

    formal = args.mode == "formal"
    review_inputs = {
        "Q0_PREFLIGHT_REPORT.json": args.q0_report,
        "SDB_RETRIEVER_AND_LLM_EXECUTION_PROTOCOL_V1_4_FROZEN.md": args.protocol,
        "SDB_LLM_RETRIEVER_RAPID_EXECUTION_PLAN_V1_6_FINAL.md": args.execution_plan,
        "MODEL_REGISTRY.json": args.model_registry,
        "QWEN38_SSE_THINKING_RUNTIME_FREEZE_V1_7.json": args.runtime_freeze,
        "PROMPT_REGISTRY.json": args.prompt_registry,
    }
    if formal:
        missing_review = [name for name, path in review_inputs.items() if path is None or not path.is_file()]
        if missing_review:
            raise ValueError(f"formal bundle lacks required review inputs: {missing_review}")
        q0_report = validate_q0(args.q0_report)
    else:
        q0_report = None
    target = args.output_dir
    ensure_empty_output(target)

    run_inputs = {
        "smoke": args.smoke_dir,
        "machine": args.machine_dir,
        "native": args.native_dir,
    }
    summaries: dict[str, dict[str, Any]] = {}
    rows_by_track: dict[str, list[dict[str, Any]]] = {}
    for label, directory in run_inputs.items():
        expected = EXPECTED_FORMAL_ROWS[label]
        summary, rows = validate_track(directory, label, expected, formal=formal)
        summaries[label] = summary
        rows_by_track[label] = rows
        shutil.copy2(directory / "RUN_SUMMARY.json", target / f"{label.upper()}_RUN_SUMMARY.json")

    all_ids: set[str] = set()
    combined = target / "REQUEST_STATUS.jsonl"
    with combined.open("w", encoding="utf-8", newline="\n") as handle:
        for label in ("smoke", "machine", "native"):
            for row in rows_by_track[label]:
                namespaced = f"{label}:{row['request_id']}"
                if namespaced in all_ids:
                    raise ValueError(f"duplicate namespaced request ID: {namespaced}")
                all_ids.add(namespaced)
                handle.write(json.dumps({"bundle_track": label, **row}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    parsed_counts = {
        label: copy_parsed(directory, target, label, rows_by_track[label])
        for label, directory in run_inputs.items()
    }
    if not args.scores_dir.is_dir():
        raise ValueError("scores directory is missing")
    score_summary_path = args.scores_dir / "SCORE_SUMMARY.json"
    if not score_summary_path.is_file():
        raise ValueError("scores directory lacks SCORE_SUMMARY.json")
    score_summary = read_json(score_summary_path)
    expected_scored = len(rows_by_track["machine"]) + len(rows_by_track["native"])
    if int(score_summary.get("rows", -1)) != expected_scored:
        raise ValueError(f"scored row mismatch: {score_summary.get('rows')} != {expected_scored}")
    shutil.copytree(args.scores_dir, target / "score_tables", dirs_exist_ok=True)

    shutil.copy2(args.output_contract_registry, target / "OUTPUT_CONTRACT_REGISTRY.json")
    shutil.copy2(args.token_budget_freeze, target / "TOKEN_BUDGET_FREEZE.json")
    for destination_name, source in review_inputs.items():
        if source is not None:
            if not source.is_file():
                raise ValueError(f"review input is missing: {source}")
            shutil.copy2(source, target / destination_name)
    if args.prompt_registry is None:
        write_json(target / "PROMPT_REGISTRY.json", {
            "revision": REVISION,
            "prompt_contract_sha256": sha256_file(args.prompt_contract),
            "instantiated_prompts_in_bundle": False,
        })
    if args.model_registry is None:
        write_json(target / "MODEL_REGISTRY.json", {
            "official_model_id": OFFICIAL_MODEL_ID,
            "served_model_id": MODEL,
            "tokenizer_revision": TOKENIZER_REVISION,
            "token_counter_revision": TOKEN_COUNTER_REVISION,
            "thinking_mode": "enabled_preserved_separate",
            "reasoning_channel_policy": "saved_not_scored_content_must_remain_strict_json",
            "weights_in_bundle": False,
            "live_endpoint_in_bundle": False,
        })
    write_json(target / "RUN_PROVENANCE.json", {
        "experiment_revision": REVISION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bundle_mode": args.mode,
        "q0_status": None if q0_report is None else q0_report["status"],
        "q0_report_sha256": None if args.q0_report is None else sha256_file(args.q0_report),
        "run_summaries": summaries,
    })

    all_rows = [row for label in ("smoke", "machine", "native") for row in rows_by_track[label]]
    diagnostic = diagnostics(all_rows)
    write_json(target / "LATENCY_HEARTBEAT_RETRY_STATISTICS.json", diagnostic)
    write_json(target / "PARSE_FAILURE_TAXONOMY.json", diagnostic["parse_failure_taxonomy"])

    validation = {
        "status": "PASS",
        "bundle_mode": args.mode,
        "exact_track_counts": {label: len(rows) for label, rows in rows_by_track.items()},
        "parsed_prediction_counts": parsed_counts,
        "request_rows": len(all_rows),
        "score_rows": expected_scored,
        "blocking_status_rows": 0,
        "old_v1_4_rows_reused": 0,
        "old_qwen36_rows_reused": 0,
        "old_qwen38_v1_6_rows_reused": 0,
        "q0_validated": not formal or q0_report is not None,
        "q0_synthetic_requests": None if q0_report is None else q0_report["q0_requests"],
        "q0_benchmark_rows_transmitted": None if q0_report is None else q0_report["benchmark_rows_transmitted"],
    }
    write_json(target / "VALIDATION_SUMMARY.json", validation)
    (target / "LATEST_RESULT.md").write_text(
        "# Qwen3.8 SSE Thinking Selection V1.7 result\n\n"
        f"- validation: `{validation['status']}`\n"
        f"- smoke/machine/native rows: `{len(rows_by_track['smoke'])} / {len(rows_by_track['machine'])} / {len(rows_by_track['native'])}`\n"
        "- output contract: Single/Machine Top-5; Multi/Composable selected set\n"
        "- unresolved infrastructure/API errors: `0`\n",
        encoding="utf-8",
    )

    result_index = {
        "experiment_revision": REVISION,
        "official_model_id": OFFICIAL_MODEL_ID,
        "served_model_id": MODEL,
        "thinking_mode": "enabled_preserved_separate",
        "reasoning_channel_policy": "saved_not_scored_content_must_remain_strict_json",
        "tracks": {
            label: {
                "rows": len(rows_by_track[label]),
                "run_summary": f"{label.upper()}_RUN_SUMMARY.json",
                "parsed_prediction_directory": f"parsed_predictions/{label}",
            }
            for label in ("smoke", "machine", "native")
        },
        "score_summary": "score_tables/SCORE_SUMMARY.json",
        "combined_status": "REQUEST_STATUS.jsonl",
        "validation": "VALIDATION_SUMMARY.json",
        "integrity": ["OUTPUT_MANIFEST.csv", "SHA256SUMS.txt"],
    }
    write_json(target / "RESULT_SET_INDEX.json", result_index)

    files = sorted(path for path in target.rglob("*") if path.is_file() and path.name not in {"OUTPUT_MANIFEST.csv", "SHA256SUMS.txt"})
    with (target / "OUTPUT_MANIFEST.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("path", "bytes", "sha256"), lineterminator="\n")
        writer.writeheader()
        for path in files:
            writer.writerow({"path": path.relative_to(target).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    files = sorted(path for path in target.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
    (target / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(target).as_posix()}\n" for path in files),
        encoding="utf-8",
    )

    args.zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(target).as_posix())
    with zipfile.ZipFile(args.zip) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError(f"ZIP CRC failed at {bad}")
    zip_sha = sha256_file(args.zip)
    sidecar = args.zip.with_suffix(args.zip.suffix + ".sha256")
    sidecar.write_text(f"{zip_sha}  {args.zip.name}\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "zip": str(args.zip),
        "bytes": args.zip.stat().st_size,
        "sha256": zip_sha,
        "sidecar": str(sidecar),
    }, indent=2))


if __name__ == "__main__":
    main()
