#!/usr/bin/env python3
"""Finalize only the metadata authorized by the frozen Composable resolution."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


EXPECTED_INPUT_SHA256 = "e2d1f2b0f6e16f8fdd3d5b462085d50428b02a18a39f876f6ccb06c53a949f94"
ALLOWED_CHANGED_FIELDS = {"adjudicator_id", "review_status"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        return fields, list(reader)


def validate_frozen_input(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    actual_sha = sha256(path)
    if actual_sha != EXPECTED_INPUT_SHA256:
        raise ValueError(f"reviewed CSV hash mismatch: expected {EXPECTED_INPUT_SHA256}, got {actual_sha}")
    if len(rows) != 103:
        raise ValueError(f"expected 103 rows, got {len(rows)}")
    required = {"underlying_task_id", "review_content_hash", "adjudicator_id", "adjudicator_type", "adjudicated_at", "review_status"}
    missing = required.difference(fields)
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    if len({row["underlying_task_id"] for row in rows}) != 103:
        raise ValueError("underlying_task_id is not unique")
    if len({row["review_content_hash"] for row in rows}) != 103:
        raise ValueError("review_content_hash is not unique")
    if any(row["adjudicator_type"].strip() != "human_confirmed" for row in rows):
        raise ValueError("not all adjudicator_type values are human_confirmed")
    if any(not row["adjudicated_at"].strip() for row in rows):
        raise ValueError("adjudicated_at is incomplete")


def repair(rows: list[dict[str, str]], reviewer_id: str) -> list[dict[str, str]]:
    reviewer_id = reviewer_id.strip()
    if not reviewer_id:
        raise ValueError("reviewer_id must be non-empty")
    output = []
    for row in rows:
        updated = dict(row)
        updated["adjudicator_id"] = reviewer_id
        updated["review_status"] = "HUMAN_REVIEW_COMPLETED"
        output.append(updated)
    return output


def assert_only_metadata_changed(before: list[dict[str, str]], after: list[dict[str, str]]) -> None:
    if len(before) != len(after):
        raise ValueError("row count changed")
    for index, (left, right) in enumerate(zip(before, after), start=1):
        changed = {key for key in left if left.get(key, "") != right.get(key, "")}
        if not changed.issubset(ALLOWED_CHANGED_FIELDS):
            raise ValueError(f"row {index} changed forbidden fields: {sorted(changed - ALLOWED_CHANGED_FIELDS)}")
        if changed != ALLOWED_CHANGED_FIELDS:
            raise ValueError(f"row {index} did not change exactly the authorized fields: {sorted(changed)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--reviewer-id", required=True)
    args = parser.parse_args()

    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    manifest = Path(args.manifest).resolve()
    if source == output:
        raise ValueError("refusing to overwrite the frozen reviewed input")
    if output.exists() or manifest.exists():
        raise FileExistsError("output or manifest already exists")

    fields, before = read_csv(source)
    validate_frozen_input(source, fields, before)
    after = repair(before, args.reviewer_id)
    assert_only_metadata_changed(before, after)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(after)
    _, reread = read_csv(output)
    assert_only_metadata_changed(before, reread)

    payload = {
        "status": "FROZEN_FOR_V0_1",
        "source_path": str(source),
        "source_sha256": sha256(source),
        "output_path": str(output),
        "output_sha256": sha256(output),
        "row_count": len(after),
        "reviewer_id": args.reviewer_id,
        "adjudicator_id_populated": sum(row["adjudicator_id"] == args.reviewer_id for row in after),
        "review_status_completed": sum(row["review_status"] == "HUMAN_REVIEW_COMPLETED" for row in after),
        "review_content_hash_unchanged": all(a["review_content_hash"] == b["review_content_hash"] for a, b in zip(before, after)),
        "allowed_changed_fields": sorted(ALLOWED_CHANGED_FIELDS),
        "semantic_rereview_performed": False,
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
