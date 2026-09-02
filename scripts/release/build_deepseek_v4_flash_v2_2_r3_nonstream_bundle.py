from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


PROVIDER = "deepseek"
REVISION = "DEEPSEEK_V4_FLASH_FULL_SIX_TASK_V2_2"
IMPLEMENTATION_REVISION = "DEEPSEEK_V4_FLASH_V2_2_R3_NONSTREAM_GATEWAY"
TRANSPORT_PROTOCOL = "openai_chat_completions_json_nonstream"
INFERENCE_PUBLIC_COMMIT = "3657a53b3ac3c98adc66ee3475111ba2115b83a3"
ZIP_NAME = "SDB_DEEPSEEK_V4_FLASH_R3_NONSTREAM_NATIVE_MACHINE_RESULT_V2_2.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _validate_summary(summary: dict[str, Any], *, mode: str, track: str, rows: int) -> None:
    expected = {
        "provider": PROVIDER,
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "mode": mode,
        "track": track,
        "requested_rows": rows,
        "terminal_rows": rows,
    }
    if any(summary.get(field) != value for field, value in expected.items()):
        raise ValueError(f"BLOCKED_RESULT_SUMMARY: {track}")
    if summary.get("status") not in {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES"}:
        raise ValueError(f"BLOCKED_RESULT_STATUS: {track}")
    counts = summary.get("status_counts", {})
    if int(counts.get("infra_error", 0)) or int(counts.get("api_error", 0)):
        raise ValueError(f"BLOCKED_UNRESOLVED_PROVIDER_ROWS: {track}")


def validate_inputs(*, provenance_binding: Path, q0_report: Path, smoke_root: Path, machine_root: Path, native_root: Path, machine_score_dir: Path, native_score_dir: Path) -> dict[str, Any]:
    binding = read_json(provenance_binding)
    expected_binding = {
        "status": "PASS",
        "provider": PROVIDER,
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "inference_public_commit": INFERENCE_PUBLIC_COMMIT,
        "source_snapshot_match": True,
        "original_result_files_modified": False,
        "inference_rerun": False,
    }
    if any(binding.get(field) != value for field, value in expected_binding.items()):
        raise ValueError("BLOCKED_R3_PROVENANCE_BINDING")
    q0 = read_json(q0_report)
    if any(q0.get(field) != value for field, value in {
        "status": "PASS", "provider": PROVIDER, "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
    }.items()):
        raise ValueError("BLOCKED_Q0_REPORT")
    q0_summary_path = q0_report.parent / "RUN_SUMMARY.json"
    if not q0_summary_path.is_file() or q0.get("diagnostic_run_summary_sha256") != sha256_file(q0_summary_path):
        raise ValueError("BLOCKED_Q0_SUMMARY_BINDING")
    q0_summary = read_json(q0_summary_path)
    expected_q0_summary = {
        "status": "DIAGNOSTIC_COMPLETE",
        "provider": PROVIDER,
        "experiment_revision": REVISION,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "transport_protocol": TRANSPORT_PROTOCOL,
        "mode": "diagnostic",
        "track": "smoke",
        "requested_rows": 6,
        "terminal_rows": 6,
    }
    if any(q0_summary.get(field) != value for field, value in expected_q0_summary.items()):
        raise ValueError("BLOCKED_Q0_SUMMARY_IDENTITY")
    q0_counts = q0_summary.get("status_counts", {})
    if int(q0_counts.get("infra_error", 0)) or int(q0_counts.get("api_error", 0)):
        raise ValueError("BLOCKED_Q0_SUMMARY_PROVIDER_ROWS")
    smoke_path = smoke_root / "RUN_SUMMARY.json"
    machine_path = machine_root / "RUN_SUMMARY.json"
    native_path = native_root / "RUN_SUMMARY.json"
    smoke, machine, native = map(read_json, (smoke_path, machine_path, native_path))
    _validate_summary(smoke, mode="smoke", track="smoke", rows=60)
    if smoke.get("gate_passed") is not True:
        raise ValueError("BLOCKED_SMOKE_GATE")
    _validate_summary(machine, mode="formal", track="machine", rows=197)
    _validate_summary(native, mode="formal", track="native", rows=4798)
    q0_hash, smoke_hash, machine_hash = sha256_file(q0_report), sha256_file(smoke_path), sha256_file(machine_path)
    if smoke.get("prerequisite_q0_report_sha256") != q0_hash:
        raise ValueError("BLOCKED_PREREQUISITE_HASH_CHAIN: smoke")
    if machine.get("prerequisite_q0_report_sha256") != q0_hash or machine.get("prerequisite_smoke_summary_sha256") != smoke_hash:
        raise ValueError("BLOCKED_PREREQUISITE_HASH_CHAIN: machine")
    if native.get("prerequisite_q0_report_sha256") != q0_hash or native.get("prerequisite_smoke_summary_sha256") != smoke_hash or native.get("prerequisite_machine_summary_sha256") != machine_hash:
        raise ValueError("BLOCKED_PREREQUISITE_HASH_CHAIN: native")
    for field in ("runtime_freeze_sha256", "budget_freeze_sha256"):
        if len({q0.get(field), smoke.get(field), machine.get(field), native.get(field)}) != 1:
            raise ValueError(f"BLOCKED_PREREQUISITE_HASH_CHAIN: {field}")
    status_rows: dict[str, list[dict[str, Any]]] = {}
    for track, root, count in (("smoke", smoke_root, 60), ("machine", machine_root, 197), ("native", native_root, 4798)):
        rows = read_jsonl(root / "REQUEST_STATUS.jsonl")
        if len(rows) != count:
            raise ValueError(f"BLOCKED_STATUS_ROW_COUNT: {track}")
        if any(row.get("provider") != PROVIDER or row.get("experiment_revision") != REVISION or row.get("implementation_revision") != IMPLEMENTATION_REVISION or row.get("transport_protocol") != TRANSPORT_PROTOCOL for row in rows):
            raise ValueError("BLOCKED_FOREIGN_OR_MIXED_RESULT_ROWS")
        status_rows[track] = rows
    binding_hash = sha256_file(provenance_binding)
    scores: dict[str, dict[str, Any]] = {}
    for track, score_dir, count in (("machine", machine_score_dir, 197), ("native", native_score_dir, 4798)):
        per_request = json.loads((score_dir / "PER_REQUEST_SCORES.json").read_text(encoding="utf-8"))
        summary = read_json(score_dir / "SCORE_SUMMARY.json")
        if not isinstance(per_request, list) or len(per_request) != count or summary.get("rows") != count:
            raise ValueError(f"BLOCKED_{track.upper()}_SCORE_ROW_COUNT")
        expected_score = {
            "status": "PASS", "provider": PROVIDER, "experiment_revision": REVISION,
            "implementation_revision": IMPLEMENTATION_REVISION, "transport_protocol": TRANSPORT_PROTOCOL,
            "inference_provenance_binding_sha256": binding_hash, "old_qwen_rows_reused": 0,
        }
        if any(summary.get(field) != value for field, value in expected_score.items()):
            raise ValueError("BLOCKED_SCORE_PROVENANCE")
        scores[track] = summary
    for track, root, summary_path in (("machine", machine_root, machine_path), ("native", native_root, native_path)):
        details = binding.get("tracks", {}).get(track, {})
        if details.get("request_status_sha256") != sha256_file(root / "REQUEST_STATUS.jsonl") or details.get("run_summary_sha256") != sha256_file(summary_path) or details.get("attempt_ledger_sha256") != sha256_file(root / "ATTEMPT_LEDGER.jsonl"):
            raise ValueError("BLOCKED_ORIGINAL_RESULT_HASH_MISMATCH")
    return {"binding": binding, "q0": q0, "q0_summary": q0_summary, "smoke": smoke, "machine": machine, "native": native, "status_rows": status_rows, "scores": scores}


def _copy(source: Path, staging: Path, relative: Path) -> None:
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def build_bundle(*, provenance_binding: Path, q0_report: Path, smoke_root: Path, machine_root: Path, native_root: Path, machine_score_dir: Path, native_score_dir: Path, output_dir: Path, comparison_dir: Path | None = None) -> Path:
    validated = validate_inputs(
        provenance_binding=provenance_binding, q0_report=q0_report, smoke_root=smoke_root,
        machine_root=machine_root, native_root=native_root, machine_score_dir=machine_score_dir,
        native_score_dir=native_score_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / ZIP_NAME
    sidecar = output_dir / f"{ZIP_NAME}.sha256"
    with tempfile.TemporaryDirectory(prefix="sdb-deepseek-r3-bundle-") as temp:
        staging = Path(temp) / ZIP_NAME.removesuffix(".zip")
        staging.mkdir()
        _copy(provenance_binding, staging, Path("PROVENANCE") / provenance_binding.name)
        report = provenance_binding.parent / "PROVENANCE_BINDING_REPORT.md"
        if report.is_file():
            _copy(report, staging, Path("PROVENANCE") / report.name)
        _copy(q0_report, staging, Path("Q0/Q0_REPORT.json"))
        _copy(q0_report.parent / "RUN_SUMMARY.json", staging, Path("Q0/RUN_SUMMARY.json"))
        for label, root in (("SMOKE", smoke_root), ("MACHINE", machine_root), ("NATIVE", native_root)):
            for filename in ("RUN_SUMMARY.json", "REQUEST_STATUS.jsonl", "ATTEMPT_LEDGER.jsonl"):
                path = root / filename
                if path.is_file():
                    _copy(path, staging, Path(label) / filename)
        for label, root in (("MACHINE_SCORING", machine_score_dir), ("NATIVE_SCORING", native_score_dir)):
            for path in sorted(root.iterdir()):
                if path.is_file():
                    _copy(path, staging, Path(label) / path.name)
        if comparison_dir is not None and comparison_dir.is_dir():
            for path in sorted(comparison_dir.iterdir()):
                if path.is_file():
                    _copy(path, staging, Path("PAIRED_COMPARISON") / path.name)
        validation = {
            "status": "PASS", "provider": PROVIDER, "experiment_revision": REVISION,
            "implementation_revision": IMPLEMENTATION_REVISION, "transport_protocol": TRANSPORT_PROTOCOL,
            "tracks": {name: value["status_counts"] for name, value in (("smoke", validated["smoke"]), ("machine", validated["machine"]), ("native", validated["native"]))},
            "provenance_binding_sha256": sha256_file(provenance_binding), "qwen_rows_reused": 0,
            "inference_rerun": False, "model_inference_calls": 0, "paid_api_calls": 0,
        }
        (staging / "VALIDATION_SUMMARY.json").write_text(json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        index = {"status": "PASS", "provider": PROVIDER, "experiment_revision": REVISION, "implementation_revision": IMPLEMENTATION_REVISION, "tracks": {"smoke": 60, "machine": 197, "native": 4798}, "qwen_rows_reused": 0}
        (staging / "RESULT_SET_INDEX.json").write_text(json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (staging / "LATEST_RESULT.md").write_text("# DeepSeek V4 Flash V2.2 R3 non-stream result\n\nProvenance, Machine scoring, and Native scoring: PASS. See the score summaries and validation index.\n", encoding="utf-8")
        manifest_rows = []
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.name not in {"OUTPUT_MANIFEST.csv", "SHA256SUMS.txt"}:
                manifest_rows.append({"path": path.relative_to(staging).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
        with (staging / "OUTPUT_MANIFEST.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("path", "bytes", "sha256"), lineterminator="\n")
            writer.writeheader(); writer.writerows(manifest_rows)
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
            raise ValueError("ZIP lacks SHA256SUMS.txt")
        prefix = checksum_name[: -len("SHA256SUMS.txt")]
        for line in archive.read(checksum_name).decode("utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            if hashlib.sha256(archive.read(prefix + relative)).hexdigest() != expected:
                raise ValueError(f"internal checksum mismatch: {relative}")
    digest = sha256_file(zip_path)
    sidecar.write_text(f"{digest}  {ZIP_NAME}\n", encoding="utf-8")
    if sha256_file(zip_path) != digest:
        raise ValueError("ZIP sidecar verification failed")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the provenance-bound DeepSeek V2.2 R3 non-stream result bundle")
    parser.add_argument("--provenance-binding", type=Path, required=True)
    parser.add_argument("--q0-report", type=Path, required=True)
    parser.add_argument("--smoke-root", type=Path, required=True)
    parser.add_argument("--machine-root", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--machine-score-dir", type=Path, required=True)
    parser.add_argument("--native-score-dir", type=Path, required=True)
    parser.add_argument("--comparison-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    path = build_bundle(**vars(args))
    print(json.dumps({"status": "PASS", "zip_path": str(path), "zip_bytes": path.stat().st_size, "zip_sha256": sha256_file(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
