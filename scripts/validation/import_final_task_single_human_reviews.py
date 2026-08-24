#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


TASK_TYPES = (
    "single_service_discovery",
    "single_api_recommendation",
    "multi_service_discovery",
    "multi_api_recommendation",
    "composable_service_discovery",
    "composable_api_recommendation",
)
REVIEW_FIELDS = [
    "review_id", "benchmark_task_id", "review_round", "reviewer_id", "blind_pack_id",
    "content_fingerprint", "semantic_alignment_check", "gold_validity_check", "candidate_validity_check",
    "service_catalog_check", "task_type_check", "leakage_check", "dependency_check", "final_decision",
    "error_type", "severity", "notes", "reviewed_at",
]
ATTESTATION_FIELDS = [
    "reviewer_id", "human_reviewer_confirmed", "reviewed_independently",
    "did_not_see_other_reviewer_decisions", "did_not_use_ai_as_final_judge", "attested_at", "notes",
]
CHECK_FIELDS = [
    "semantic_alignment_check", "gold_validity_check", "candidate_validity_check", "service_catalog_check",
    "task_type_check", "leakage_check", "dependency_check",
]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_export(
    task_type: str,
    export_path: Path,
    template_rows: list[dict[str, str]],
    reviewer_id: str,
) -> list[dict[str, str]]:
    fields, exported = read_csv(export_path)
    if fields != REVIEW_FIELDS:
        raise ValueError(f"{export_path.name}: unexpected schema {fields}")
    template_by_id = {row["benchmark_task_id"]: row for row in template_rows}
    export_by_id = {row["benchmark_task_id"]: row for row in exported}
    if len(template_by_id) != len(template_rows) or len(export_by_id) != len(exported):
        raise ValueError(f"{task_type}: duplicate benchmark_task_id")
    if template_by_id.keys() != export_by_id.keys():
        missing = sorted(template_by_id.keys() - export_by_id.keys())
        extra = sorted(export_by_id.keys() - template_by_id.keys())
        raise ValueError(f"{task_type}: export/template ID mismatch; missing={missing}; extra={extra}")

    imported: list[dict[str, str]] = []
    for template in template_rows:
        task_id = template["benchmark_task_id"]
        row = dict(export_by_id[task_id])
        for field in ("benchmark_task_id", "review_round", "blind_pack_id", "content_fingerprint"):
            if row[field] != template[field]:
                raise ValueError(f"{task_id}: immutable field mismatch for {field}")
        if row["review_round"] != "primary":
            raise ValueError(f"{task_id}: expected primary review round")
        if row["final_decision"] not in {"keep", "remove", "uncertain"}:
            raise ValueError(f"{task_id}: invalid final_decision={row['final_decision']!r}")
        missing_checks = [field for field in CHECK_FIELDS if not row[field].strip()]
        if missing_checks:
            raise ValueError(f"{task_id}: missing review checks {missing_checks}")
        if not row["reviewed_at"].strip():
            raise ValueError(f"{task_id}: reviewed_at is blank")
        if row["reviewer_id"].strip() not in {"", reviewer_id}:
            raise ValueError(f"{task_id}: export already belongs to another reviewer")
        row["reviewer_id"] = reviewer_id
        row["review_id"] = f"human::{reviewer_id}::primary::{task_id}"
        imported.append(row)
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(description="Import authoritative primary HTML exports under the single-human-review G4 policy.")
    parser.add_argument("--exports-dir", required=True)
    parser.add_argument("--qa-root", required=True)
    parser.add_argument("--reviewer-id", default="human_reviewer_01")
    args = parser.parse_args()

    exports_dir = Path(args.exports_dir).resolve()
    qa_root = Path(args.qa_root).resolve()
    reviewer_id = args.reviewer_id.strip()
    if not reviewer_id:
        raise ValueError("reviewer_id must not be blank")

    policy_path = qa_root / "review_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if policy.get("review_mode") != "single_human_review" or policy.get("authoritative_round") != "primary":
        raise ValueError("review_policy.json does not authorize authoritative single primary review")

    manifest_files: list[dict[str, object]] = []
    total_imported = 0
    decisions: dict[str, int] = {"keep": 0, "remove": 0, "uncertain": 0}
    for task_type in TASK_TYPES:
        template_path = qa_root / "review_templates" / task_type / "primary_reviews.csv"
        fields, template_rows = read_csv(template_path)
        if fields != REVIEW_FIELDS:
            raise ValueError(f"{template_path}: unexpected template schema")
        export_path = exports_dir / f"{task_type}_primary_reviews_draft.csv"
        if not template_rows:
            if export_path.exists() and read_csv(export_path)[1]:
                raise ValueError(f"{export_path.name}: no primary rows were expected")
            continue
        if not export_path.is_file():
            raise FileNotFoundError(f"missing primary export: {export_path}")
        imported = validate_export(task_type, export_path, template_rows, reviewer_id)
        write_csv(template_path, imported, REVIEW_FIELDS)
        total_imported += len(imported)
        for row in imported:
            decisions[row["final_decision"]] += 1
        manifest_files.append({
            "role": "authoritative_primary_import",
            "task_type": task_type,
            "source_filename": export_path.name,
            "source_size_bytes": export_path.stat().st_size,
            "source_sha256": sha256(export_path),
            "row_count": len(imported),
            "destination": template_path.relative_to(qa_root).as_posix(),
        })

    for task_type in TASK_TYPES:
        supplemental_path = exports_dir / f"{task_type}_secondary_reviews_draft.csv"
        if supplemental_path.is_file():
            fields, rows = read_csv(supplemental_path)
            if fields != REVIEW_FIELDS:
                raise ValueError(f"{supplemental_path.name}: unexpected schema")
            manifest_files.append({
                "role": "supplemental_secondary_not_imported",
                "task_type": task_type,
                "source_filename": supplemental_path.name,
                "source_size_bytes": supplemental_path.stat().st_size,
                "source_sha256": sha256(supplemental_path),
                "row_count": len(rows),
                "destination": "",
            })

    attested_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    write_csv(qa_root / "reviewer_attestations.csv", [{
        "reviewer_id": reviewer_id,
        "human_reviewer_confirmed": "true",
        "reviewed_independently": "not_applicable_single_review",
        "did_not_see_other_reviewer_decisions": "not_applicable_single_review",
        "did_not_use_ai_as_final_judge": "true",
        "attested_at": attested_at,
        "notes": "Pseudonymous ID assigned per dataset-owner instruction; primary decisions were exported by the human user from the blind HTML review app under the single-review policy.",
    }], ATTESTATION_FIELDS)

    import_manifest = {
        "schema_version": "1.0",
        "review_policy_id": policy["policy_id"],
        "reviewer_id": reviewer_id,
        "imported_at": attested_at,
        "authoritative_primary_rows_imported": total_imported,
        "decision_counts": decisions,
        "secondary_exports_disposition": "supplemental_non_gating_not_imported",
        "files": manifest_files,
    }
    (qa_root / "review_import_manifest.json").write_text(
        json.dumps(import_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(import_manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
