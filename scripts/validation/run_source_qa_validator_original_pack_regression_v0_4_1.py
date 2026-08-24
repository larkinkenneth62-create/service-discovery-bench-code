#!/usr/bin/env python
"""Run read-only v0.4.1 validation on the four original source-QA packs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from source_qa_review_validator_v0_4_1 import (
    HUMAN_FIELDS,
    ISSUE_FIELDS,
    read_csv,
    validate_rows,
    write_csv,
    write_json,
)


PACK_SPECS = [
    (
        "metatool",
        "outputs/source_qa_adjudication_v0_3/metatool/metatool_disagreement_adjudication_items_v0_3.csv",
        50,
    ),
    (
        "stabletoolbench",
        "outputs/source_qa_adjudication_v0_3/stabletoolbench/stabletoolbench_supplemental_adjudication_items_v0_3.csv",
        136,
    ),
    (
        "toolbench",
        "outputs/source_qa_adjudication_v0_3/toolbench/toolbench_v1_5f_final_targeted_qa_items_v0_3.csv",
        110,
    ),
    (
        "shortcutsbench",
        "outputs/shortcutsbench_strict_adapter_v0_1/shortcutsbench_strict_qa_items_100_or_all_v0_1.csv",
        55,
    ),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only regression of v0.4.1 validator on four original packs."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--output-dir", default="outputs/validator_patch_v0_4_1"
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    output_dir = (root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    all_issues: list[dict[str, str]] = []
    source_results: dict[str, dict[str, Any]] = {}
    source_hashes: dict[str, dict[str, str]] = {}
    files: dict[str, Path] = {}

    for source, relative, expected in PACK_SPECS:
        path = (root / relative).resolve()
        if not path.exists():
            raise SystemExit(f"Required original pack does not exist: {path}")
        files[source] = path
        before_hash = sha256_file(path)
        _, rows = read_csv(path)
        summary, issues, _ = validate_rows(
            rows,
            filename=path.name,
            category=source,
            source_hint=source,
            expected_rows=expected,
        )
        for item in issues:
            item["category"] = source
        all_issues.extend(issues)
        after_hash = sha256_file(path)
        source_hashes[source] = {
            "path": str(path),
            "sha256_before": before_hash,
            "sha256_after": after_hash,
        }
        summary["source_path"] = str(path)
        summary["source_sha256_before"] = before_hash
        summary["source_sha256_after"] = after_hash
        summary["source_unchanged"] = before_hash == after_hash
        source_results[source] = summary

    immutable_modified = sum(
        values["sha256_before"] != values["sha256_after"]
        for values in source_hashes.values()
    )
    pending_by_source = {
        source: result["pending_count"] for source, result in source_results.items()
    }
    total_pending = sum(pending_by_source.values())
    fatal_count = sum(item["severity"] == "fatal" for item in all_issues)
    shortcuts_false_api_fatal = source_results["shortcutsbench"][
        "shortcuts_api_equals_gold_fatal_count_after_patch"
    ]
    expected_pending = {
        "metatool": 50,
        "stabletoolbench": 136,
        "toolbench": 110,
        "shortcutsbench": 55,
    }
    regression_pass = bool(
        pending_by_source == expected_pending
        and total_pending == 351
        and fatal_count == 0
        and shortcuts_false_api_fatal == 0
        and immutable_modified == 0
    )
    summary = {
        "generated_at": now_iso(),
        "project_root": str(root),
        "input_files": [str(files[source]) for source, _, _ in PACK_SPECS],
        "source_results": source_results,
        "pending_counts": pending_by_source,
        "total_pending_human_rows": total_pending,
        "bundle_status": "PENDING_HUMAN_ADJUDICATION",
        "invalid_reviewed_input": False,
        "validation_fatal_count": fatal_count,
        "validation_warning_count": sum(item["severity"] == "warning" for item in all_issues),
        "shortcuts_api_equals_gold_fatal_count_after_patch": shortcuts_false_api_fatal,
        "immutable_source_files_modified_count": immutable_modified,
        "review_fields_autofilled_count": 0,
        "source_hashes": source_hashes,
        "regression_pass": regression_pass,
        "can_freeze_sources_now": False,
        "can_start_six_task_assembly_now": False,
        "can_generate_final_dataset_now": False,
    }
    write_json(output_dir / "original_pack_regression_summary.json", summary)
    write_csv(
        output_dir / "original_pack_validation_issues.csv",
        ISSUE_FIELDS,
        all_issues,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if regression_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
