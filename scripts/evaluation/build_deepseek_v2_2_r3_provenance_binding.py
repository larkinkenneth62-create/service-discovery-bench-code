from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROVIDER = "deepseek"
EXPERIMENT_REVISION = "DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2"
IMPLEMENTATION_REVISION = "DEEPSEEK_V4_FLASH_V2_2_R3_NONSTREAM_GATEWAY"
TRANSPORT_PROTOCOL = "openai_chat_completions_json_nonstream"
INFERENCE_PUBLIC_COMMIT = "3657a53b3ac3c98adc66ee3475111ba2115b83a3"
RUNNER_REL = "experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/run_deepseek_v4_flash_v2_2_r3_nonstream.py"
PARSER_REL = "experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/output_contracts_v2_2.py"
SIZE_UTIL_REL = "experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/contract_size_utils_v2_2.py"
RUNTIME_REL = "experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/schemas/DEEPSEEK_V4_FLASH_RUNTIME_FREEZE_V2_2_R3_NONSTREAM.json"
BUDGET_BUILDER_REL = "experiments/llm_v0_2_deepseek_v4_flash_structured_selection_v2_2/code/freeze_output_budgets_v2_2_r3_nonstream.py"
EXPECTED_ROWS = {"machine": 197, "native": 4798}
ALLOWED_RESULT_STATUS = {"succeeded", "parse_failure"}
ALLOWED_RESPONSE_MODELS = {"DeepSeek-V4-Flash", "DeepSeek-V4-Flash-0731"}


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob_bytes(repo_root: Path, commit: str, relative: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(repo_root), "show", f"{commit}:{relative}"],
        stderr=subprocess.STDOUT,
    )


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"non-object row at {path}:{number}")
        rows.append(row)
    return rows


def _unique(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value or value in result:
            raise ValueError(f"BLOCKED_DUPLICATE_OR_INVALID_ID: {label}:{value!r}")
        result[value] = row
    return result


def _single(rows: list[dict[str, Any]], field: str) -> Any:
    values = {stable_json(row.get(field)) for row in rows}
    if len(values) != 1:
        raise ValueError(f"BLOCKED_MIXED_PROVENANCE: {field}")
    return rows[0].get(field)


def _inside(root: Path, relative: str) -> Path:
    base = root.resolve()
    path = (root / relative).resolve()
    if path != base and base not in path.parents:
        raise ValueError("BLOCKED_PATH_ESCAPE")
    return path


def validate_run_track(*, track: str, run_root: Path, manifest: Path) -> dict[str, Any]:
    if track not in EXPECTED_ROWS:
        raise ValueError(f"unsupported track: {track}")
    expected_rows = EXPECTED_ROWS[track]
    summary_path = run_root / "RUN_SUMMARY.json"
    status_path = run_root / "REQUEST_STATUS.jsonl"
    ledger_path = run_root / "ATTEMPT_LEDGER.jsonl"
    artifacts = run_root / "artifacts"
    if not all(path.is_file() for path in (summary_path, status_path, ledger_path)) or not artifacts.is_dir():
        raise ValueError(f"BLOCKED_RESULT_INTEGRITY: missing {track} run artifact")
    summary = read_json(summary_path)
    expected_summary = {
        "provider": PROVIDER,
        "experiment_revision": EXPERIMENT_REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "mode": "formal",
        "track": track,
        "requested_rows": expected_rows,
        "terminal_rows": expected_rows,
    }
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        raise ValueError(f"BLOCKED_RESULT_INTEGRITY: {track} summary identity")
    if summary.get("status") not in {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES"}:
        raise ValueError(f"BLOCKED_RESULT_INTEGRITY: {track} summary status")
    counts = summary.get("status_counts", {})
    if int(counts.get("infra_error", 0)) or int(counts.get("api_error", 0)):
        raise ValueError(f"BLOCKED_RESULT_INTEGRITY: unresolved {track} provider rows")

    statuses = read_jsonl(status_path)
    manifests = read_jsonl(manifest)
    if len(statuses) != expected_rows or len(manifests) != expected_rows:
        raise ValueError(f"BLOCKED_RESULT_INTEGRITY: {track} row count")
    status_by_id = _unique(statuses, "request_id", f"{track}-status")
    manifest_by_id = _unique(
        [{**row, "_binding_id": row.get("benchmark_task_id", row.get("request_id"))} for row in manifests],
        "_binding_id",
        f"{track}-manifest",
    )
    if set(status_by_id) != set(manifest_by_id):
        raise ValueError(f"BLOCKED_RESULT_INTEGRITY: {track} manifest/status IDs")
    manifest_hash = sha256_file(manifest)
    source_hash = _single(statuses, "source_manifest_sha256")
    if source_hash != manifest_hash:
        raise ValueError(f"BLOCKED_RESULT_INTEGRITY: {track} manifest hash")

    exact_fields = {
        "provider": PROVIDER,
        "experiment_revision": EXPERIMENT_REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "track": track,
        "requested_model": "DeepSeek-V4-Flash",
        "requested_model_version_mapping": "DeepSeek-V4-Flash-0731",
        "http_status": 200,
        "response_complete_received": True,
        "terminal_event_received": None,
        "done_received": None,
        "sse_event_count": 0,
        "response_object_count": 1,
        "finish_reason": "stop",
    }
    for request_id, row in status_by_id.items():
        if row.get("status") not in ALLOWED_RESULT_STATUS:
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: non-capability status {request_id}")
        if any(row.get(field) != value for field, value in exact_fields.items()):
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: R3 row contract {request_id}")
        if row.get("response_model") not in ALLOWED_RESPONSE_MODELS:
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: response model {request_id}")
        source = dict(manifest_by_id[request_id])
        source.pop("_binding_id", None)
        if row.get("source_row_sha256") != sha256_bytes(stable_json(source).encode("utf-8")):
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: source row hash {request_id}")
        if row["status"] == "succeeded":
            if row.get("parse_status") != "valid" or not isinstance(row.get("parsed_prediction_path"), str):
                raise ValueError(f"BLOCKED_RESULT_INTEGRITY: parsed prediction metadata {request_id}")
            prediction = _inside(run_root, row["parsed_prediction_path"])
            if not prediction.is_file() or not isinstance(json.loads(prediction.read_text(encoding="utf-8")), dict):
                raise ValueError(f"BLOCKED_RESULT_INTEGRITY: parsed prediction {request_id}")
        elif row.get("parse_status") != "invalid":
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: parse-failure metadata {request_id}")

    return {
        "track": track,
        "run_root": str(run_root.resolve()),
        "manifest_path": str(manifest.resolve()),
        "rows": expected_rows,
        "status_counts": dict(sorted(Counter(row["status"] for row in statuses).items())),
        "manifest_sha256": manifest_hash,
        "request_status_sha256": sha256_file(status_path),
        "run_summary_sha256": sha256_file(summary_path),
        "attempt_ledger_sha256": sha256_file(ledger_path),
        "runner_sha256": _single(statuses, "runner_sha256"),
        "parser_sha256": _single(statuses, "parser_sha256"),
        "runtime_freeze_sha256": _single(statuses, "runtime_freeze_sha256"),
        "budget_freeze_sha256": _single(statuses, "budget_freeze_sha256"),
        "endpoint_sha256": _single(statuses, "endpoint_sha256"),
        "git_commit_values": sorted({_single(statuses, "git_commit_sha")}),
        "statuses": statuses,
        "status_by_id": status_by_id,
    }


def validate_attempt_ledger(*, run_root: Path, statuses_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = read_jsonl(run_root / "ATTEMPT_LEDGER.jsonl")
    grouped: dict[tuple[str, int], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    retry_taxonomy: Counter[str] = Counter()
    for index, row in enumerate(rows):
        request_id = row.get("request_id")
        attempt = row.get("attempt")
        if not isinstance(request_id, str) or request_id not in statuses_by_id or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("BLOCKED_RESULT_INTEGRITY: invalid ledger identity")
        grouped[(request_id, attempt)].append((index, row))
        if row.get("event") == "attempt_finished" and row.get("will_retry"):
            retry_taxonomy[str(row.get("error_code"))] += 1
    started = finished = 0
    attempts_by_request: dict[str, list[int]] = defaultdict(list)
    for (request_id, attempt), events in grouped.items():
        starts = [(idx, row) for idx, row in events if row.get("event") == "attempt_started"]
        finishes = [(idx, row) for idx, row in events if row.get("event") == "attempt_finished"]
        if len(starts) != 1 or len(finishes) != 1 or starts[0][0] >= finishes[0][0]:
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: unclosed ledger attempt {request_id}/{attempt}")
        started += 1
        finished += 1
        attempts_by_request[request_id].append(attempt)
    if set(attempts_by_request) != set(statuses_by_id):
        raise ValueError("BLOCKED_RESULT_INTEGRITY: ledger/status request IDs")
    for request_id, status in statuses_by_id.items():
        attempts = sorted(attempts_by_request[request_id])
        if attempts != list(range(1, len(attempts) + 1)):
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: non-contiguous attempts {request_id}")
        if status.get("attempt_count") != len(attempts) or status.get("retry_count") != len(attempts) - 1:
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: attempt accounting {request_id}")
        final = next(row for _, row in grouped[(request_id, attempts[-1])] if row.get("event") == "attempt_finished")
        if final.get("raw_response_path") != status.get("raw_response_path") or final.get("raw_response_sha256") != status.get("raw_response_sha256"):
            raise ValueError(f"BLOCKED_RESULT_INTEGRITY: final raw binding {request_id}")
        for path_field, hash_field in (("raw_response_path", "raw_response_sha256"), ("response_attempt_path", "response_attempt_sha256")):
            relative = final.get(path_field)
            expected_hash = final.get(hash_field)
            if relative is None:
                if expected_hash is not None:
                    raise ValueError(f"BLOCKED_RESULT_INTEGRITY: partial artifact binding {request_id}")
                continue
            path = _inside(run_root, relative)
            if not path.is_file() or sha256_file(path) != expected_hash:
                raise ValueError(f"BLOCKED_RESULT_INTEGRITY: attempt artifact hash {request_id}")
    return {
        "attempt_started_count": started,
        "attempt_finished_count": finished,
        "retried_request_count": sum(len(values) > 1 for values in attempts_by_request.values()),
        "max_attempt_count": max((len(values) for values in attempts_by_request.values()), default=0),
        "retry_reason_taxonomy": dict(sorted(retry_taxonomy.items())),
    }


def validate_commit_binding(*, repo_root: Path, statuses: list[dict[str, Any]], runtime_freeze: Path, budget_freeze: Path) -> dict[str, Any]:
    runner_blob = sha256_bytes(git_blob_bytes(repo_root, INFERENCE_PUBLIC_COMMIT, RUNNER_REL))
    parser_blob = sha256_bytes(git_blob_bytes(repo_root, INFERENCE_PUBLIC_COMMIT, PARSER_REL))
    runtime_blob = sha256_bytes(git_blob_bytes(repo_root, INFERENCE_PUBLIC_COMMIT, RUNTIME_REL))
    size_blob = sha256_bytes(git_blob_bytes(repo_root, INFERENCE_PUBLIC_COMMIT, SIZE_UTIL_REL))
    budget_builder_blob = sha256_bytes(git_blob_bytes(repo_root, INFERENCE_PUBLIC_COMMIT, BUDGET_BUILDER_REL))
    runner_value = _single(statuses, "runner_sha256")
    parser_value = _single(statuses, "parser_sha256")
    runtime_value = _single(statuses, "runtime_freeze_sha256")
    budget_value = _single(statuses, "budget_freeze_sha256")
    runtime_file_hash = sha256_file(runtime_freeze)
    budget_file_hash = sha256_file(budget_freeze)
    if runner_value != runner_blob:
        raise ValueError("BLOCKED_INFERENCE_PROVENANCE_BINDING: runner")
    if parser_value != parser_blob:
        raise ValueError("BLOCKED_INFERENCE_PROVENANCE_BINDING: parser")
    if runtime_value != runtime_blob or runtime_value != runtime_file_hash:
        raise ValueError("BLOCKED_INFERENCE_PROVENANCE_BINDING: runtime")
    if budget_value != budget_file_hash:
        raise ValueError("BLOCKED_INFERENCE_PROVENANCE_BINDING: budget")
    git_values = sorted({str(row.get("git_commit_sha")) for row in statuses})
    if git_values not in (["UNKNOWN"], [INFERENCE_PUBLIC_COMMIT]):
        raise ValueError("BLOCKED_INFERENCE_PROVENANCE_BINDING: git commit values")
    return {
        "runner_sha256": runner_blob,
        "parser_sha256": parser_blob,
        "runtime_freeze_sha256": runtime_blob,
        "budget_freeze_sha256": budget_file_hash,
        "size_util_sha256": size_blob,
        "budget_builder_sha256": budget_builder_blob,
        "runner_hash_match": True,
        "parser_hash_match": True,
        "runtime_hash_match": True,
        "budget_hash_match": True,
        "original_git_commit_values": git_values,
    }


def build_binding(*, repo_root: Path, machine_root: Path, native_root: Path, machine_manifest: Path, native_manifest: Path, runtime_freeze: Path, budget_freeze: Path) -> dict[str, Any]:
    machine = validate_run_track(track="machine", run_root=machine_root, manifest=machine_manifest)
    native = validate_run_track(track="native", run_root=native_root, manifest=native_manifest)
    machine_ledger = validate_attempt_ledger(run_root=machine_root, statuses_by_id=machine.pop("status_by_id"))
    native_ledger = validate_attempt_ledger(run_root=native_root, statuses_by_id=native.pop("status_by_id"))
    statuses = machine.pop("statuses") + native.pop("statuses")
    code = validate_commit_binding(repo_root=repo_root, statuses=statuses, runtime_freeze=runtime_freeze, budget_freeze=budget_freeze)
    if machine["runner_sha256"] != native["runner_sha256"] or machine["parser_sha256"] != native["parser_sha256"]:
        raise ValueError("BLOCKED_INFERENCE_PROVENANCE_BINDING: mixed tracks")
    if machine["runtime_freeze_sha256"] != native["runtime_freeze_sha256"] or machine["budget_freeze_sha256"] != native["budget_freeze_sha256"]:
        raise ValueError("BLOCKED_INFERENCE_PROVENANCE_BINDING: mixed freezes")
    machine["attempt_ledger_validation"] = machine_ledger
    native["attempt_ledger_validation"] = native_ledger
    for track in (machine, native):
        track.pop("git_commit_values", None)
    return {
        "schema_version": 1,
        "status": "PASS",
        "provider": PROVIDER,
        "experiment_revision": EXPERIMENT_REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "inference_public_commit": INFERENCE_PUBLIC_COMMIT,
        "original_git_commit_values": code["original_git_commit_values"],
        "effective_inference_git_commit": INFERENCE_PUBLIC_COMMIT,
        "binding_method": "EXACT_RUNNER_PARSER_RUNTIME_HASH_MATCH_TO_PUBLIC_COMMIT",
        "source_snapshot_match": True,
        "original_result_files_modified": False,
        "inference_rerun": False,
        "paid_api_calls": 0,
        "code_hashes": {key: value for key, value in code.items() if key.endswith("sha256") or key.endswith("match")},
        "private_freeze_hashes": {
            "runtime_freeze_sha256": sha256_file(runtime_freeze),
            "budget_freeze_sha256": sha256_file(budget_freeze),
        },
        "tracks": {"machine": machine, "native": native},
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bind completed DeepSeek V2.2 R3 non-stream results to public inference code")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--machine-root", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--machine-manifest", type=Path, required=True)
    parser.add_argument("--native-manifest", type=Path, required=True)
    parser.add_argument("--runtime-freeze", type=Path, required=True)
    parser.add_argument("--budget-freeze", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    binding = build_binding(
        repo_root=args.repo_root,
        machine_root=args.machine_root,
        native_root=args.native_root,
        machine_manifest=args.machine_manifest,
        native_manifest=args.native_manifest,
        runtime_freeze=args.runtime_freeze,
        budget_freeze=args.budget_freeze,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "DEEPSEEK_V2_2_R3_RUN_PROVENANCE_BINDING.json"
    path.write_text(json.dumps(binding, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    digest = sha256_file(path)
    (args.output_dir / f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    report = (
        "# DeepSeek V2.2 R3 provenance binding\n\n"
        f"- Status: `{binding['status']}`\n"
        f"- Inference public commit: `{INFERENCE_PUBLIC_COMMIT}`\n"
        f"- Original Git values: `{binding['original_git_commit_values']}`\n"
        "- Binding method: exact runner/parser/runtime SHA-256 match to the public commit.\n"
        "- Original result rows were not modified and inference was not rerun.\n"
    )
    (args.output_dir / "PROVENANCE_BINDING_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps({"status": "PASS", "binding": str(path), "sha256": digest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
