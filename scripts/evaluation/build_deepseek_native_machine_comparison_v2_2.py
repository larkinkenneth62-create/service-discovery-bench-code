from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


METRICS = ("hit_at_1", "mrr_at_5", "recall_at_5", "ndcg_at_5", "parse_failure")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(value, dict) and isinstance(value.get("pairs"), list):
        value = value["pairs"]
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"expected a JSON array or JSONL objects: {path}")
    return value


def _unique(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not request_id or request_id in result:
            raise ValueError(f"invalid or duplicate request_id: {request_id!r}")
        result[request_id] = row
    return result


def build_comparison(native_rows: list[dict[str, Any]], machine_rows: list[dict[str, Any]], pairing_rows: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if pairing_rows is None:
        return [], {"status": "PAIRING_NOT_AVAILABLE", "matched_pairs": 0, "finding": "No frozen explicit pairing artifact was supplied; no delta was fabricated."}
    native = _unique(native_rows)
    machine = _unique(machine_rows)
    seen_pairing_ids: set[str] = set()
    seen_native: set[str] = set()
    seen_machine: set[str] = set()
    result: list[dict[str, Any]] = []
    for pair in pairing_rows:
        pairing_id = pair.get("pairing_id")
        native_id = pair.get("native_request_id")
        machine_id = pair.get("machine_request_id")
        if not all(isinstance(value, str) and value for value in (pairing_id, native_id, machine_id)):
            raise ValueError("pairing rows require pairing_id, native_request_id, and machine_request_id")
        if pairing_id in seen_pairing_ids or native_id in seen_native or machine_id in seen_machine:
            raise ValueError("pairing artifact contains duplicate IDs")
        seen_pairing_ids.add(pairing_id)
        seen_native.add(native_id)
        seen_machine.add(machine_id)
        if native_id not in native or machine_id not in machine:
            raise ValueError(f"pairing references an unknown request: {pairing_id}")
        native_row = native[native_id]
        machine_row = machine[machine_id]
        if native_row.get("task_type") != machine_row.get("task_type") or native_row.get("prediction_target") != machine_row.get("prediction_target"):
            raise ValueError(f"pairing semantics differ: {pairing_id}")
        if native_row.get("ranking_metrics") is None or machine_row.get("ranking_metrics") is None:
            raise ValueError(f"ranking metrics are not defined for both sides: {pairing_id}")
        output: dict[str, Any] = {
            "pairing_id": pairing_id,
            "native_request_id": native_id,
            "machine_request_id": machine_id,
            "task_type": native_row.get("task_type"),
            "prediction_target": native_row.get("prediction_target"),
        }
        for metric in METRICS:
            if metric == "parse_failure":
                native_value = float(native_row[metric])
                machine_value = float(machine_row[metric])
            else:
                native_value = float(native_row["ranking_metrics"][metric])
                machine_value = float(machine_row["ranking_metrics"][metric])
            output[f"native_{metric}"] = native_value
            output[f"machine_{metric}"] = machine_value
            output[f"delta_{metric}"] = native_value - machine_value
        result.append(output)
    return result, {"status": "PASS", "matched_pairs": len(result), "pairing_policy": "EXPLICIT_FROZEN_PAIRING_IDS_ONLY"}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["pairing_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an explicit DeepSeek Native/Machine paired comparison")
    parser.add_argument("--native-scores", type=Path, required=True)
    parser.add_argument("--machine-scores", type=Path, required=True)
    parser.add_argument("--pairing-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    pairing_rows = read_rows(args.pairing_manifest) if args.pairing_manifest is not None else None
    rows, validation = build_comparison(read_rows(args.native_scores), read_rows(args.machine_scores), pairing_rows)
    validation.update({
        "native_scores_sha256": sha256_file(args.native_scores),
        "machine_scores_sha256": sha256_file(args.machine_scores),
        "pairing_manifest_sha256": sha256_file(args.pairing_manifest) if args.pairing_manifest is not None else None,
    })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "NATIVE_MACHINE_MATCHED_DELTA.csv", rows)
    (args.output_dir / "PAIRING_VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
