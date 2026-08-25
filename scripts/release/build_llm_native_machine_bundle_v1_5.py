from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODEL = "Qwen3.6-35B-A3B-APEX-I-Compact.gguf"
TOKENIZER_REVISION = "995ad96eacd98c81ed38be0c5b274b04031597b0"


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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def copy_parsed(run_dir: Path, target: Path, label: str) -> int:
    count = 0
    for row in status_rows(run_dir):
        relative = row.get("parsed_prediction_path")
        if not isinstance(relative, str):
            continue
        source = run_dir / relative
        destination = target / "parsed_predictions" / label / f"{hashlib.sha256(row['request_id'].encode()).hexdigest()[:24]}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        count += 1
    return count


def diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row["end_to_end_latency_ms"]) for row in rows if row.get("end_to_end_latency_ms") is not None]
    return {
        "rows": len(rows),
        "status_counts": dict(Counter(row.get("status") for row in rows)),
        "parse_failure_taxonomy": dict(Counter(row.get("error_code") for row in rows if row.get("status") == "parse_failure")),
        "heartbeat_total": sum(int(row.get("heartbeat_count", 0)) for row in rows),
        "retry_total": sum(int(row.get("retry_count", 0)) for row in rows),
        "latency_ms": {
            "mean": sum(latencies) / len(latencies) if latencies else None,
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build private Qwen Selection V1.5 result bundle")
    parser.add_argument("--smoke-dir", type=Path, required=True)
    parser.add_argument("--machine-dir", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--scores-dir", type=Path, required=True)
    parser.add_argument("--prompt-contract", type=Path, required=True)
    parser.add_argument("--output-contract-registry", type=Path, required=True)
    parser.add_argument("--token-budget-freeze", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    args = parser.parse_args()
    target = args.output_dir
    target.mkdir(parents=True, exist_ok=True)
    summaries = {
        "SMOKE_RUN_SUMMARY.json": args.smoke_dir / "RUN_SUMMARY.json",
        "MACHINE_RUN_SUMMARY.json": args.machine_dir / "RUN_SUMMARY.json",
        "NATIVE_RUN_SUMMARY.json": args.native_dir / "RUN_SUMMARY.json",
    }
    for name, source in summaries.items():
        shutil.copy2(source, target / name)
    all_rows: list[dict[str, Any]] = []
    combined = target / "REQUEST_STATUS.jsonl"
    with combined.open("w", encoding="utf-8", newline="\n") as handle:
        for label, directory in (("smoke", args.smoke_dir), ("machine", args.machine_dir), ("native", args.native_dir)):
            rows = status_rows(directory)
            all_rows.extend(rows)
            for row in rows:
                handle.write(json.dumps({"bundle_track": label, **row}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    parsed_counts = {
        label: copy_parsed(directory, target, label)
        for label, directory in (("smoke", args.smoke_dir), ("machine", args.machine_dir), ("native", args.native_dir))
    }
    shutil.copytree(args.scores_dir, target / "score_tables", dirs_exist_ok=True)
    shutil.copy2(args.output_contract_registry, target / "OUTPUT_CONTRACT_REGISTRY.json")
    shutil.copy2(args.token_budget_freeze, target / "TOKEN_BUDGET_FREEZE.json")
    prompt_registry = {
        "revision": "QWEN_SSE_SELECTION_V1_5",
        "prompt_contract_sha256": sha256_file(args.prompt_contract),
        "instantiated_prompts_in_bundle": False,
    }
    write_json(target / "PROMPT_REGISTRY.json", prompt_registry)
    write_json(target / "MODEL_REGISTRY.json", {
        "model": MODEL, "tokenizer_revision": TOKENIZER_REVISION,
        "weights_in_bundle": False, "live_endpoint_in_bundle": False,
    })
    provenance = {
        "experiment_revision": "QWEN_SSE_SELECTION_V1_5",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_summaries": {name: read_json(path) for name, path in summaries.items()},
    }
    write_json(target / "RUN_PROVENANCE.json", provenance)
    diagnostic = diagnostics(all_rows)
    write_json(target / "LATENCY_HEARTBEAT_RETRY_STATISTICS.json", diagnostic)
    write_json(target / "PARSE_FAILURE_TAXONOMY.json", diagnostic["parse_failure_taxonomy"])
    validation = {
        "status": "PASS" if all(read_json(path).get("status") in {"COMPLETE_ALL_PARSED", "COMPLETE_WITH_MODEL_FAILURES"} for path in summaries.values()) else "FAIL",
        "parsed_prediction_counts": parsed_counts,
        "request_rows": len(all_rows),
    }
    write_json(target / "VALIDATION_SUMMARY.json", validation)
    latest = (
        "# Qwen SSE Selection V1.5 result\n\n"
        f"- validation: `{validation['status']}`\n"
        f"- request rows: `{len(all_rows)}`\n"
        "- output contract: Single/Machine Top-5; Multi/Composable selected set\n"
    )
    (target / "LATEST_RESULT.md").write_text(latest, encoding="utf-8")
    files = sorted(path for path in target.rglob("*") if path.is_file() and path.name not in {"OUTPUT_MANIFEST.csv", "SHA256SUMS.txt"})
    with (target / "OUTPUT_MANIFEST.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("path", "bytes", "sha256"))
        writer.writeheader()
        for path in files:
            writer.writerow({"path": path.relative_to(target).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    files = sorted(path for path in target.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt")
    (target / "SHA256SUMS.txt").write_text("".join(f"{sha256_file(path)}  {path.relative_to(target).as_posix()}\n" for path in files), encoding="utf-8")
    args.zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(target).as_posix())
    print(json.dumps({"status": validation["status"], "zip": str(args.zip), "bytes": args.zip.stat().st_size, "sha256": sha256_file(args.zip)}, indent=2))


if __name__ == "__main__":
    main()
