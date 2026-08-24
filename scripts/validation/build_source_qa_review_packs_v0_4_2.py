#!/usr/bin/env python
"""Version the four source-QA review packs to v0.4.2 without filling reviews."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from source_qa_review_validator_v0_4_2 import (
        HUMAN_FIELDS,
        NEW_HUMAN_FIELDS,
        V041_HUMAN_FIELDS,
    )
except ImportError:
    from scripts.validation.source_qa_review_validator_v0_4_2 import (
        HUMAN_FIELDS,
        NEW_HUMAN_FIELDS,
        V041_HUMAN_FIELDS,
    )


PACK_SPECS = [
    {
        "source": "metatool",
        "input": "outputs/source_qa_adjudication_v0_3/metatool/metatool_disagreement_adjudication_items_v0_3.csv",
        "output": "metatool/metatool_disagreement_adjudication_items_v0_4_2.csv",
        "expected_rows": 50,
    },
    {
        "source": "stabletoolbench",
        "input": "outputs/source_qa_adjudication_v0_3/stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_3.csv",
        "output": "stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_4_2.csv",
        "expected_rows": 136,
    },
    {
        "source": "toolbench",
        "input": "outputs/source_qa_adjudication_v0_3/toolbench/toolbench_v1_5f_final_targeted_qa_items_v0_3.csv",
        "output": "toolbench/toolbench_v1_5f_final_targeted_qa_items_v0_4_2.csv",
        "expected_rows": 110,
    },
    {
        "source": "shortcutsbench",
        "input": "outputs/shortcutsbench_strict_adapter_v0_1/shortcutsbench_strict_qa_items_100_or_all_v0_1.csv",
        "output": "shortcutsbench/shortcutsbench_strict_qa_items_v0_4_2.csv",
        "expected_rows": 55,
    },
]


COMPARISON_FIELDS = [
    "source",
    "original_path",
    "versioned_path",
    "expected_rows",
    "original_rows",
    "versioned_rows",
    "original_column_count",
    "versioned_column_count",
    "original_fields_preserved_in_order",
    "new_fields_appended_rightmost",
    "immutable_cell_change_count",
    "existing_human_nonempty_count",
    "new_human_nonempty_count",
    "json_multiline_cell_count",
    "json_parse_failure_count",
    "source_unchanged_after_generation",
    "validation_pass",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_versioned_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="raise",
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            output = dict(row)
            for field in NEW_HUMAN_FIELDS:
                output[field] = ""
            writer.writerow(output)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def compare_pack(
    source: str,
    original_path: Path,
    output_path: Path,
    expected_rows: int,
    before_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    original_fields, original_rows = read_csv(original_path)
    output_fields, output_rows = read_csv(output_path)
    immutable_changes = 0
    for original, versioned in zip(original_rows, output_rows):
        for field in original_fields:
            if str(original.get(field, "") or "") != str(versioned.get(field, "") or ""):
                immutable_changes += 1

    existing_human_nonempty = sum(
        bool(str(row.get(field, "") or "").strip())
        for row in output_rows
        for field in V041_HUMAN_FIELDS
    )
    new_human_nonempty = sum(
        bool(str(row.get(field, "") or "").strip())
        for row in output_rows
        for field in NEW_HUMAN_FIELDS
    )
    json_multiline = 0
    json_parse_failures = 0
    json_fields = [field for field in original_fields if field.lower().endswith("_json")]
    for row in output_rows:
        for field in json_fields:
            value = str(row.get(field, "") or "")
            if "\n" in value or "\r" in value:
                json_multiline += 1
            if value.strip():
                try:
                    json.loads(value)
                except Exception:
                    json_parse_failures += 1

    original_order_ok = output_fields[: len(original_fields)] == original_fields
    new_rightmost_ok = output_fields[-len(NEW_HUMAN_FIELDS) :] == NEW_HUMAN_FIELDS
    after_hash = sha256_file(original_path)
    validation_pass = bool(
        len(original_rows) == expected_rows
        and len(output_rows) == expected_rows
        and original_order_ok
        and new_rightmost_ok
        and immutable_changes == 0
        and existing_human_nonempty == 0
        and new_human_nonempty == 0
        and json_multiline == 0
        and json_parse_failures == 0
        and before_hash == after_hash
    )
    comparison = {
        "source": source,
        "original_path": str(original_path),
        "versioned_path": str(output_path),
        "expected_rows": expected_rows,
        "original_rows": len(original_rows),
        "versioned_rows": len(output_rows),
        "original_column_count": len(original_fields),
        "versioned_column_count": len(output_fields),
        "original_fields_preserved_in_order": original_order_ok,
        "new_fields_appended_rightmost": new_rightmost_ok,
        "immutable_cell_change_count": immutable_changes,
        "existing_human_nonempty_count": existing_human_nonempty,
        "new_human_nonempty_count": new_human_nonempty,
        "json_multiline_cell_count": json_multiline,
        "json_parse_failure_count": json_parse_failures,
        "source_unchanged_after_generation": before_hash == after_hash,
        "validation_pass": validation_pass,
    }
    manifest = {
        **comparison,
        "original_sha256": before_hash,
        "original_sha256_after": after_hash,
        "versioned_sha256": sha256_file(output_path),
        "new_fields": NEW_HUMAN_FIELDS,
        "encoding": "utf-8-sig",
        "csv_quoting": "QUOTE_ALL",
    }
    return comparison, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build immutable v0.4.2 source-QA review packs.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--output-dir", default="outputs/source_qa_adjudication_v0_4_2"
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    output_dir = (root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    if output_dir.exists():
        raise SystemExit(
            f"Refusing to overwrite existing v0.4.2 review-pack directory: {output_dir}"
        )

    comparisons: list[dict[str, Any]] = []
    pack_manifest: list[dict[str, Any]] = []
    for spec in PACK_SPECS:
        original_path = (root / spec["input"]).resolve()
        if not original_path.exists():
            raise SystemExit(f"Required original pack is missing: {original_path}")
        original_fields, original_rows = read_csv(original_path)
        if len(original_rows) != spec["expected_rows"]:
            raise SystemExit(
                f"Unexpected row count for {original_path}: {len(original_rows)} != {spec['expected_rows']}"
            )
        if any(field in original_fields for field in NEW_HUMAN_FIELDS):
            raise SystemExit(f"Original pack already contains v0.4.2 fields: {original_path}")
        nonempty_existing = [
            (index + 1, field)
            for index, row in enumerate(original_rows)
            for field in V041_HUMAN_FIELDS
            if str(row.get(field, "") or "").strip()
        ]
        if nonempty_existing:
            raise SystemExit(
                f"Original pack contains non-empty v0.3 human fields; refusing to version automatically: {original_path}"
            )

        before_hash = sha256_file(original_path)
        output_path = output_dir / spec["output"]
        write_versioned_csv(output_path, original_fields + NEW_HUMAN_FIELDS, original_rows)
        comparison, manifest = compare_pack(
            spec["source"],
            original_path,
            output_path,
            spec["expected_rows"],
            before_hash,
        )
        comparisons.append(comparison)
        pack_manifest.append(manifest)

    all_pass = all(row["validation_pass"] for row in comparisons)
    total_rows = sum(row["versioned_rows"] for row in comparisons)
    manifest_payload = {
        "generated_at": now_iso(),
        "version": "v0.4.2",
        "project_root": str(root),
        "output_dir": str(output_dir),
        "new_fields": NEW_HUMAN_FIELDS,
        "packs": pack_manifest,
        "total_rows": total_rows,
        "pending_human_rows": total_rows,
        "immutable_cell_change_total": sum(
            row["immutable_cell_change_count"] for row in comparisons
        ),
        "review_field_nonempty_total": sum(
            row["existing_human_nonempty_count"] + row["new_human_nonempty_count"]
            for row in comparisons
        ),
        "all_validation_pass": all_pass,
        "ready_for_human_adjudication": all_pass and total_rows == 351,
        "source_freeze_authorized": False,
        "assembly_authorized": False,
    }
    write_csv(
        output_dir / "original_vs_v0_4_2_immutable_comparison.csv",
        COMPARISON_FIELDS,
        comparisons,
    )
    write_json(output_dir / "review_pack_manifest.json", manifest_payload)
    print(json.dumps(manifest_payload, ensure_ascii=False, indent=2))
    return 0 if manifest_payload["ready_for_human_adjudication"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
