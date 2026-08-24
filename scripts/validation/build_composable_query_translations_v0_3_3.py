#!/usr/bin/env python3
"""Merge retained v0.3.2 translations with direct v0.3.3 replacement translations."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


DEFAULT_INPUT = Path(
    "outputs/composable_paired_task_preparation_v0_3_3/"
    "composable_paired_task_review_items_v0_3_3.csv"
)
DEFAULT_OLD_INPUT = Path(
    "outputs/composable_paired_task_preparation_v0_3_2/"
    "composable_paired_task_review_items_v0_3_2.csv"
)
DEFAULT_OLD_TRANSLATIONS = Path(
    "outputs/composable_paired_task_preparation_v0_3_2/"
    "composable_query_translations_zh_v0_3_2.json"
)
DEFAULT_NEW_TRANSLATIONS = Path(
    "outputs/composable_paired_task_preparation_v0_3_3/"
    "composable_query_translations_new_zh_v0_3_3.json"
)
DEFAULT_OUTPUT = Path(
    "outputs/composable_paired_task_preparation_v0_3_3/"
    "composable_query_translations_zh_v0_3_3.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build complete direct Chinese query translations for v0.3.3."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--old-input", type=Path, default=DEFAULT_OLD_INPUT)
    parser.add_argument("--old-translations", type=Path, default=DEFAULT_OLD_TRANSLATIONS)
    parser.add_argument("--new-translations", type=Path, default=DEFAULT_NEW_TRANSLATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_mapping(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Translation file must contain an object: {path}")
    return {str(key): str(text).strip() for key, text in value.items()}


def main() -> int:
    args = parse_args()
    for path in (
        args.input,
        args.old_input,
        args.old_translations,
        args.new_translations,
    ):
        if not path.exists():
            raise FileNotFoundError(f"Required translation input does not exist: {path}")
    rows = read_rows(args.input)
    old_rows = read_rows(args.old_input)
    old_translations = read_mapping(args.old_translations)
    new_translations = read_mapping(args.new_translations)
    old_by_source = {
        row["source_task_id"]: old_translations.get(row["review_item_id"], "")
        for row in old_rows
    }
    output = {}
    source_counts = {"retained_v0_3_2": 0, "new_direct_v0_3_3": 0}
    missing = []
    for row in rows:
        source_id = row["source_task_id"]
        translation = old_by_source.get(source_id, "")
        if translation:
            source_counts["retained_v0_3_2"] += 1
        else:
            translation = new_translations.get(source_id, "")
            if translation:
                source_counts["new_direct_v0_3_3"] += 1
        if not translation:
            missing.append(source_id)
            continue
        if "�" in translation or not any("\u4e00" <= char <= "\u9fff" for char in translation):
            raise ValueError(f"Invalid Chinese translation for {source_id}")
        output[row["review_item_id"]] = translation
    if missing:
        raise ValueError(f"Missing direct Chinese translations for {len(missing)} rows: {missing}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "rows": len(rows),
                "translations": len(output),
                **source_counts,
                "missing": 0,
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
