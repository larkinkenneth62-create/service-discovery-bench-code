from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import statistics
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


PROVIDER = "deepseek"
REVISION = "DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2"
IMPLEMENTATION_REVISION = "DEEPSEEK_V4_FLASH_V2_2_R2_GATE_ACCOUNTING"
ZIP_NAME = "SDB_DEEPSEEK_V4_FLASH_NATIVE_MACHINE_RESULT_V2_2.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _counts(summary: dict[str, Any]) -> dict[str, int]:
    return {name: int(summary.get("status_counts", {}).get(name, 0)) for name in ("succeeded", "parse_failure", "infra_error", "api_error")}


def _validate_summary(summary: dict[str, Any], *, mode: str, track: str, rows: int) -> None:
    expected = {
        "provider": PROVIDER, "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "mode": mode, "track": track, "requested_rows": rows, "terminal_rows": rows,
    }
    if any(summary.get(field) != value for field, value in expected.items()):
        raise ValueError(f"BLOCKED_RESULT_SUMMARY: {track}")
    if summary.get("status") not in {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES"}:
        raise ValueError(f"BLOCKED_RESULT_STATUS: {track}")
    counts = _counts(summary)
    if counts["infra_error"] or counts["api_error"]:
        raise ValueError(f"BLOCKED_UNRESOLVED_PROVIDER_ROWS: {track}")


def validate_inputs(q0_report: Path, smoke_root: Path, machine_root: Path, native_root: Path, machine_score_dir: Path, native_score_dir: Path) -> dict[str, Any]:
    q0 = read_json(q0_report)
    smoke_path = smoke_root / "RUN_SUMMARY.json"
    machine_path = machine_root / "RUN_SUMMARY.json"
    native_path = native_root / "RUN_SUMMARY.json"
    smoke = read_json(smoke_path)
    machine = read_json(machine_path)
    native = read_json(native_path)
    if q0.get("status") != "PASS" or q0.get("provider") != PROVIDER or q0.get("experiment_revision") != REVISION or q0.get("implementation_revision") != IMPLEMENTATION_REVISION:
        raise ValueError("BLOCKED_Q0_REPORT")
    _validate_summary(smoke, mode="smoke", track="smoke", rows=60)
    if smoke.get("gate_passed") is not True:
        raise ValueError("BLOCKED_SMOKE_GATE")
    _validate_summary(machine, mode="formal", track="machine", rows=197)
    _validate_summary(native, mode="formal", track="native", rows=4798)
    q0_hash = sha256_file(q0_report)
    smoke_hash = sha256_file(smoke_path)
    machine_hash = sha256_file(machine_path)
    if smoke.get("prerequisite_q0_report_sha256") != q0_hash:
        raise ValueError("BLOCKED_PREREQUISITE_HASH_CHAIN: smoke->q0")
    if machine.get("prerequisite_q0_report_sha256") != q0_hash or machine.get("prerequisite_smoke_summary_sha256") != smoke_hash:
        raise ValueError("BLOCKED_PREREQUISITE_HASH_CHAIN: machine")
    if native.get("prerequisite_q0_report_sha256") != q0_hash or native.get("prerequisite_smoke_summary_sha256") != smoke_hash or native.get("prerequisite_machine_summary_sha256") != machine_hash:
        raise ValueError("BLOCKED_PREREQUISITE_HASH_CHAIN: native")
    for field in ("runtime_freeze_sha256", "budget_freeze_sha256"):
        if len({q0.get(field), smoke.get(field), machine.get(field), native.get(field)}) != 1:
            raise ValueError(f"BLOCKED_PREREQUISITE_HASH_CHAIN: {field}")
    smoke_rows = read_jsonl(smoke_root / "REQUEST_STATUS.jsonl")
    machine_rows = read_jsonl(machine_root / "REQUEST_STATUS.jsonl")
    native_rows = read_jsonl(native_root / "REQUEST_STATUS.jsonl")
    if (len(smoke_rows), len(machine_rows), len(native_rows)) != (60, 197, 4798):
        raise ValueError("BLOCKED_STATUS_ROW_COUNT")
    all_rows = smoke_rows + machine_rows + native_rows
    if any(row.get("provider") != PROVIDER or row.get("experiment_revision") != REVISION or row.get("implementation_revision") != IMPLEMENTATION_REVISION for row in all_rows):
        raise ValueError("BLOCKED_FOREIGN_OR_MIXED_RESULT_ROWS")
    machine_scores = read_json(machine_score_dir / "PER_REQUEST_SCORES.json")
    native_scores = read_json(native_score_dir / "PER_REQUEST_SCORES.json")
    if not isinstance(machine_scores, list) or len(machine_scores) != 197:
        raise ValueError("BLOCKED_MACHINE_SCORE_ROW_COUNT")
    if not isinstance(native_scores, list) or len(native_scores) != 4798:
        raise ValueError("BLOCKED_NATIVE_SCORE_ROW_COUNT")
    for score_dir in (machine_score_dir, native_score_dir):
        summary = read_json(score_dir / "SCORE_SUMMARY.json")
        if summary.get("provider") != PROVIDER or summary.get("experiment_revision") != REVISION or summary.get("implementation_revision") != IMPLEMENTATION_REVISION or summary.get("old_qwen_rows_reused") != 0:
            raise ValueError("BLOCKED_SCORE_PROVENANCE")
    return {"q0": q0, "smoke": smoke, "machine": machine, "native": native, "status_rows": {"smoke": smoke_rows, "machine": machine_rows, "native": native_rows}}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def summarize(validated: dict[str, Any]) -> dict[str, Any]:
    rows_by_track = validated["status_rows"]
    all_rows = [row for rows in rows_by_track.values() for row in rows]
    latencies = [float(row["latency_seconds"]) for row in all_rows if isinstance(row.get("latency_seconds"), (int, float))]
    usage_fields = ("prompt_tokens", "prompt_cache_hit_tokens", "reasoning_tokens", "completion_tokens")
    return {
        "status": "PASS",
        "provider": PROVIDER,
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "qwen_rows_reused": 0,
        "status_counts": {track: dict(sorted(Counter(row.get("status") for row in rows).items())) for track, rows in rows_by_track.items()},
        "parse_failure_taxonomy": dict(sorted(Counter(row.get("error_code") for row in all_rows if row.get("status") == "parse_failure").items())),
        "finish_reason_taxonomy": dict(sorted(Counter(row.get("finish_reason") for row in all_rows).items(), key=lambda item: str(item[0]))),
        "retry_reason_taxonomy": dict(sorted(Counter(attempt.get("error_code") for row in all_rows for attempt in row.get("attempts", []) if attempt.get("will_retry")).items(), key=lambda item: str(item[0]))),
        "latency_seconds": {"mean": statistics.fmean(latencies) if latencies else None, "p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95), "min": min(latencies) if latencies else None, "max": max(latencies) if latencies else None},
        "usage_token_totals": {field: sum(int(row.get("usage", {}).get(field, 0)) for row in all_rows if isinstance(row.get("usage"), dict)) for field in usage_fields},
        "system_fingerprint_distribution": dict(sorted(Counter(row.get("system_fingerprint") for row in all_rows).items(), key=lambda item: str(item[0]))),
        "response_model_distribution": dict(sorted(Counter(row.get("response_model") for row in all_rows).items(), key=lambda item: str(item[0]))),
        "source_distribution": dict(sorted(Counter(row.get("source_dataset") for row in all_rows).items(), key=lambda item: str(item[0]))),
        "task_distribution": dict(sorted(Counter(row.get("task_type") for row in all_rows).items())),
        "contract_distribution": dict(sorted(Counter(row.get("output_contract") for row in all_rows).items())),
    }


def _copy_file(source: Path, staging: Path, relative: Path, manifest: list[dict[str, Any]]) -> None:
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest.append({"path": relative.as_posix(), "bytes": target.stat().st_size, "sha256": sha256_file(target)})


def build_bundle(q0_report: Path, smoke_root: Path, machine_root: Path, native_root: Path, machine_score_dir: Path, native_score_dir: Path, output_dir: Path, comparison_dir: Path | None = None) -> Path:
    validated = validate_inputs(q0_report, smoke_root, machine_root, native_root, machine_score_dir, native_score_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / ZIP_NAME
    sidecar = output_dir / f"{ZIP_NAME}.sha256"
    if zip_path.exists() or sidecar.exists():
        raise FileExistsError("result ZIP or sidecar already exists")
    with tempfile.TemporaryDirectory(prefix="sdb-deepseek-v2-2-") as temporary:
        staging = Path(temporary) / "SDB_DEEPSEEK_V4_FLASH_NATIVE_MACHINE_RESULT_V2_2"
        staging.mkdir()
        manifest: list[dict[str, Any]] = []
        _copy_file(q0_report, staging, Path("Q0/Q0_REPORT.json"), manifest)
        for label, root in (("SMOKE", smoke_root), ("MACHINE", machine_root), ("NATIVE", native_root)):
            for filename in ("RUN_SUMMARY.json", "REQUEST_STATUS.jsonl", "ATTEMPT_LEDGER.jsonl"):
                path = root / filename
                if path.is_file():
                    _copy_file(path, staging, Path(label) / filename, manifest)
        for label, root in (("MACHINE_SCORING", machine_score_dir), ("NATIVE_SCORING", native_score_dir)):
            for path in sorted(root.iterdir()):
                if path.is_file():
                    _copy_file(path, staging, Path(label) / path.name, manifest)
        if comparison_dir is not None:
            for path in sorted(comparison_dir.iterdir()):
                if path.is_file():
                    _copy_file(path, staging, Path("PAIRED_COMPARISON") / path.name, manifest)
        validation = summarize(validated)
        (staging / "VALIDATION_SUMMARY.json").write_text(json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        index = {"status": "PASS", "provider": PROVIDER, "experiment_revision": REVISION, "implementation_revision": IMPLEMENTATION_REVISION, "tracks": {"smoke": 60, "machine": 197, "native": 4798}, "qwen_rows_reused": 0}
        (staging / "RESULT_SET_INDEX.json").write_text(json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (staging / "LATEST_RESULT.md").write_text("# DeepSeek V4 Flash V2.2 result\n\nValidation status: PASS. See `VALIDATION_SUMMARY.json` and the frozen scoring tables.\n", encoding="utf-8")
        with (staging / "OUTPUT_MANIFEST.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("path", "bytes", "sha256"), lineterminator="\n")
            writer.writeheader()
            writer.writerows(manifest)
        checksum_targets = sorted(path for path in staging.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
        (staging / "SHA256SUMS.txt").write_text("".join(f"{sha256_file(path)}  {path.relative_to(staging).as_posix()}\n" for path in checksum_targets), encoding="utf-8")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, (Path(staging.name) / path.relative_to(staging)).as_posix())
    with zipfile.ZipFile(zip_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC validation failed")
        checksum_name = next((name for name in archive.namelist() if name.endswith("/SHA256SUMS.txt")), None)
        if checksum_name is None:
            raise ValueError("ZIP lacks internal SHA256SUMS.txt")
        root_prefix = checksum_name[: -len("SHA256SUMS.txt")]
        for line in archive.read(checksum_name).decode("utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            actual = hashlib.sha256(archive.read(root_prefix + relative)).hexdigest()
            if actual != expected:
                raise ValueError(f"internal checksum mismatch: {relative}")
    zip_sha = sha256_file(zip_path)
    sidecar.write_text(f"{zip_sha}  {ZIP_NAME}\n", encoding="utf-8")
    if sha256_file(zip_path) != zip_sha:
        raise ValueError("ZIP sidecar verification failed")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the private DeepSeek V4 Flash V2.2 paper-ready result bundle")
    parser.add_argument("--q0-report", type=Path, required=True)
    parser.add_argument("--smoke-root", type=Path, required=True)
    parser.add_argument("--machine-root", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--machine-score-dir", type=Path, required=True)
    parser.add_argument("--native-score-dir", type=Path, required=True)
    parser.add_argument("--comparison-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    path = build_bundle(args.q0_report, args.smoke_root, args.machine_root, args.native_root, args.machine_score_dir, args.native_score_dir, args.output_dir, args.comparison_dir)
    print(json.dumps({"status": "PASS", "zip_path": str(path), "zip_bytes": path.stat().st_size, "zip_sha256": sha256_file(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
