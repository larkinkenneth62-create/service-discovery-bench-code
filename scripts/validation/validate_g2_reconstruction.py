#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
from servicediscoverybench.reconstruction import validate_candidate_space  # noqa: E402
from servicediscoverybench.signatures import review_content_fingerprint  # noqa: E402

csv.field_size_limit(2_147_483_647)


def load_ids(path: Path, field: str) -> tuple[set[str], dict[str, dict]]:
    ids, rows = set(), {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                ids.add(row[field])
                rows[row[field]] = row
    return ids, rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--service-catalog", required=True)
    parser.add_argument("--api-catalog", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    paths = {key: Path(value).resolve() for key, value in vars(args).items() if key != "output"}
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)

    service_ids, _ = load_ids(paths["service_catalog"], "service_id")
    api_ids, api_rows = load_ids(paths["api_catalog"], "api_id")
    ledger_ids = set()
    with paths["ledger"].open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            ledger_ids.add(row["new_row_id"])

    issues, seen = [], set()
    counts = Counter()
    with paths["candidates"].open("r", encoding="utf-8-sig", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            row_id = row["g2_row_id"]
            if row_id in seen:
                issues.append({"severity": "ERROR", "code": "DUPLICATE_G2_ROW_ID", "row_id": row_id, "detail": str(line_number)})
            seen.add(row_id)
            candidate_services = json.loads(row["candidate_services_json"])
            candidate_apis = json.loads(row["candidate_apis_json"])
            gold_services = json.loads(row["gold_services_json"])
            gold_apis = json.loads(row["gold_apis_json"])
            candidates = candidate_services if row["prediction_target"] == "service" else candidate_apis
            gold = gold_services if row["prediction_target"] == "service" else gold_apis
            valid, reason = validate_candidate_space(candidates, gold)
            if not valid:
                issues.append({"severity": "ERROR", "code": "INVALID_CANDIDATE_SPACE", "row_id": row_id, "detail": reason})
            if int(row["candidate_count"]) != len(candidates) or int(row["gold_count"]) != len(gold) or int(row["non_gold_candidate_count"]) != len(candidates) - len(gold):
                issues.append({"severity": "ERROR", "code": "COUNT_MISMATCH", "row_id": row_id, "detail": "stored counts differ"})
            missing_services = (set(candidate_services) | set(gold_services)) - service_ids
            missing_apis = (set(candidate_apis) | set(gold_apis)) - api_ids
            if missing_services:
                issues.append({"severity": "ERROR", "code": "UNKNOWN_SERVICE_ID", "row_id": row_id, "detail": json.dumps(sorted(missing_services))})
            if missing_apis:
                issues.append({"severity": "ERROR", "code": "UNKNOWN_API_ID", "row_id": row_id, "detail": json.dumps(sorted(missing_apis))})
            parent_services = {api_rows[aid]["parent_service_id"] for aid in candidate_apis if aid in api_rows}
            if candidate_apis and not parent_services.issubset(candidate_services):
                issues.append({"severity": "ERROR", "code": "API_PARENT_NOT_IN_CANDIDATE_SERVICES", "row_id": row_id, "detail": json.dumps(sorted(parent_services - set(candidate_services)))})
            if row["repair_status"] == "reconstructed" and row_id not in ledger_ids:
                issues.append({"severity": "ERROR", "code": "MISSING_RECONSTRUCTION_LEDGER", "row_id": row_id, "detail": ""})
            if review_content_fingerprint(row) != row["review_content_fingerprint"]:
                issues.append({"severity": "ERROR", "code": "FINGERPRINT_MISMATCH", "row_id": row_id, "detail": ""})
            counts[(row["task_type"], row["source_dataset"], row["repair_status"])] += 1

    orphan_ledger = ledger_ids - seen
    for row_id in sorted(orphan_ledger):
        issues.append({"severity": "ERROR", "code": "ORPHAN_RECONSTRUCTION_LEDGER", "row_id": row_id, "detail": ""})
    summary = {
        "stage": "G2_independent_validation",
        "status": "GATE_PASSED" if not issues else "BLOCKED",
        "validated_rows": len(seen),
        "ledger_rows": len(ledger_ids),
        "errors": len(issues),
        "counts": [{"task_type": key[0], "source_dataset": key[1], "repair_status": key[2], "count": value} for key, value in sorted(counts.items())],
    }
    write_json(output / "VALIDATION_SUMMARY.json", summary)
    write_csv(output / "VALIDATION_ISSUES.csv", issues, ["severity", "code", "row_id", "detail"])
    write_csv(output / "INPUT_MANIFEST.csv", [{"logical_name": key, "resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for key, path in sorted(paths.items())], ["logical_name", "resolved_path", "size_bytes", "sha256"])
    manifest = []
    for path in sorted((p for p in output.iterdir() if p.is_file() and p.name != "OUTPUT_MANIFEST.csv"), key=lambda p: p.name):
        manifest.append({"relative_path": path.name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
    write_csv(output / "OUTPUT_MANIFEST.csv", manifest, ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
