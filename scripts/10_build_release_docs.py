#!/usr/bin/env python3
"""Build the final release only when RC1, G5, G6, and source terms are evidenced."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.manifests import sha256_file, write_csv, write_json  # noqa: E402
from servicediscoverybench.release import (  # noqa: E402
    SOURCE_TERMS_CLEARED,
    source_terms_are_cleared,
    source_terms_status,
)
from servicediscoverybench.signatures import task_signature  # noqa: E402
from servicediscoverybench.splits import candidate_bucket  # noqa: E402

TASK_TYPES = (
    "single_service_discovery", "single_api_recommendation", "multi_service_discovery",
    "multi_api_recommendation", "composable_service_discovery", "composable_api_recommendation",
)

SOURCE_TERMS_SUPPORT_FILES = (
    "SOURCE_FIELD_PROVENANCE_AND_TERMS_V0_1.md",
    "SOURCE_TERMS_DECISION_RECORD_V0_1.md",
)
SOURCE_LICENSE_FILES = (
    "ToolBench_LICENSE.txt",
    "StableToolBench_LICENSE.txt",
    "MetaTool_LICENSE.txt",
    "ShortcutsBench_LICENSE.txt",
)


def read_status(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def public_logical_path(value: str) -> str:
    """Remove workstation-identifying absolute paths from public provenance."""
    if not value:
        return value
    candidate = Path(value)
    if not candidate.is_absolute():
        return value.replace("\\", "/")
    try:
        return candidate.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return f"external_input/{candidate.name}"


def sanitize_path_metadata(value):
    if isinstance(value, list):
        return [sanitize_path_metadata(item) for item in value]
    if isinstance(value, dict):
        return {
            key: public_logical_path(item) if isinstance(item, str) and key.lower().endswith("path")
            else sanitize_path_metadata(item)
            for key, item in value.items()
        }
    return value


def sanitize_public_catalogs(catalog_dir: Path) -> None:
    for name in ("service_catalog.jsonl", "api_catalog.jsonl"):
        path = catalog_dir / name
        sanitized = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                row["source_path"] = public_logical_path(row.get("source_path", ""))
                metadata = json.loads(row.get("metadata_json") or "{}")
                row["metadata_json"] = json.dumps(
                    sanitize_path_metadata(metadata), ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"),
                )
                sanitized.append(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        path.write_text("\n".join(sanitized) + "\n", encoding="utf-8")


PUBLIC_TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt", ".html", ".log"}


def encoded_path_variants(value: str) -> set[str]:
    variants = {value, value.replace("\\", "/")}
    escaped = value
    for _ in range(3):
        escaped = json.dumps(escaped, ensure_ascii=False)[1:-1]
        variants.add(escaped)
    return {item for item in variants if item}


def scrub_private_paths(release: Path) -> None:
    replacements = []
    for prefix, replacement in (
        (str(ROOT.resolve()), "PROJECT_ROOT"),
        (str(Path.home().resolve()), "USER_HOME"),
    ):
        replacements.extend((variant, replacement) for variant in encoded_path_variants(prefix))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    for path in release.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in PUBLIC_TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8-sig")
        updated = text
        for source, replacement in replacements:
            updated = updated.replace(source, replacement)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def refresh_public_task_signatures(release: Path) -> None:
    signature_by_id: dict[str, str] = {}
    task_csvs = list((release / "tasks").glob("*.csv"))
    split_task_csvs = [
        path for path in (release / "splits").rglob("*.csv")
        if path.name != "split_manifest.csv"
    ]
    example_csvs = list((release / "examples").glob("*.csv"))
    for path in [*task_csvs, *split_task_csvs, *example_csvs]:
        fields, rows = read_csv(path)
        if "benchmark_task_id" not in fields or "task_signature" not in fields:
            continue
        for row in rows:
            row["task_signature"] = task_signature(row)
            signature_by_id[row["benchmark_task_id"]] = row["task_signature"]
        write_csv(path, rows, fields)

    manifest = release / "splits" / "split_manifest.csv"
    fields, rows = read_csv(manifest)
    for row in rows:
        row["task_signature"] = signature_by_id[row["benchmark_task_id"]]
    write_csv(manifest, rows, fields)


def assert_no_private_paths(release: Path) -> None:
    forbidden = {str(Path.home().resolve()).casefold(), Path.home().name.casefold()}
    hits = []
    for path in release.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in PUBLIC_TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8-sig").casefold()
        if any(marker and marker in text for marker in forbidden):
            hits.append(path.relative_to(release).as_posix())
    if hits:
        raise ValueError(f"private workstation path markers remain: {hits[:10]}")


def blocked(output: Path, blockers: list[dict]) -> int:
    output.mkdir(parents=True, exist_ok=False)
    status = {"stage": "FINAL_RELEASE", "status": "BLOCKED", "release_ready": False, "blockers": blockers}
    write_json(output / "RUN_STATUS.json", status)
    write_csv(output / "RELEASE_BLOCKERS.csv", blockers, ["code", "detail"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rc1-run", required=True)
    parser.add_argument("--split-run", required=True)
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--source-terms", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rc1_run, split_run, baseline_run = map(lambda value: Path(value).resolve(), (args.rc1_run, args.split_run, args.baseline_run))
    source_terms, output = Path(args.source_terms).resolve(), Path(args.output).resolve()
    rc1_status = read_status(rc1_run / "RUN_STATUS.json")
    split_status = read_status(split_run / "RUN_STATUS.json")
    baseline_status = read_status(baseline_run / "RUN_STATUS.json")
    blockers = []
    if rc1_status.get("status") != "FROZEN_RC1" or rc1_status.get("rc1_frozen") is not True:
        blockers.append({"code": "RC1_NOT_FROZEN", "detail": rc1_status.get("status", "MISSING")})
    if split_status.get("status") != "GATE_PASSED" or split_status.get("g5_gate_passed") is not True:
        blockers.append({"code": "G5_NOT_PASSED", "detail": split_status.get("status", "MISSING")})
    if baseline_status.get("g6_local_gate_passed") is not True:
        blockers.append({"code": "G6_LOCAL_BASELINES_NOT_PASSED", "detail": baseline_status.get("status", "MISSING")})
    terms_text = source_terms.read_text(encoding="utf-8") if source_terms.exists() else ""
    terms_status = source_terms_status(terms_text)
    if not source_terms_are_cleared(terms_text):
        blockers.append({
            "code": "SOURCE_TERMS_NOT_CLEARED",
            "detail": (
                f"release_terms_status={terms_status or 'MISSING_OR_AMBIGUOUS'}; "
                f"required={SOURCE_TERMS_CLEARED}"
            ),
        })
    support_paths = [source_terms.parent / name for name in SOURCE_TERMS_SUPPORT_FILES]
    license_dir = source_terms.parent / "source_terms_licenses"
    license_paths = [license_dir / name for name in SOURCE_LICENSE_FILES]
    for path in [*support_paths, license_dir / "README.md", *license_paths]:
        if not path.is_file():
            blockers.append({"code": "SOURCE_TERMS_EVIDENCE_MISSING", "detail": str(path)})
    if blockers:
        return blocked(output, blockers)

    output.mkdir(parents=True, exist_ok=False)
    release = output / "ServiceDiscoveryBench-v0.1"
    rc1 = rc1_run / "ServiceDiscoveryBench-v0.1-rc1"
    release.mkdir()
    for name in ("catalogs", "tasks", "manifests"):
        shutil.copytree(rc1 / name, release / name)
    shutil.copytree(
        rc1 / "qa", release / "qa",
        ignore=shutil.ignore_patterns("blind_packs", "review_templates", "html_review_apps", "adjudication"),
    )
    sanitize_public_catalogs(release / "catalogs")
    shutil.copytree(split_run / "splits", release / "splits")
    (release / "reports").mkdir()
    shutil.copytree(rc1 / "reports", release / "reports" / "assembly")
    shutil.copytree(baseline_run / "baseline" / "results", release / "reports" / "baselines")
    (release / "examples").mkdir()
    shutil.copy2(source_terms, release / "LICENSES_AND_SOURCE_TERMS.md")
    for path in support_paths:
        shutil.copy2(path, release / path.name)
    shutil.copytree(license_dir, release / "licenses")

    rows = []
    by_task, by_source, by_bucket, gold_counts = Counter(), Counter(), Counter(), Counter()
    public_fields = None
    for task_type in TASK_TYPES:
        fields, task_rows = read_csv(release / "tasks" / f"{task_type}.csv")
        public_fields = fields if public_fields is None else public_fields
        if fields != public_fields:
            raise ValueError(f"task schema mismatch: {task_type}")
        rows.extend(task_rows)
        for row in task_rows:
            by_task[task_type] += 1
            by_source[(task_type, row["source_dataset"])] += 1
            by_bucket[(task_type, candidate_bucket(row["candidate_count"]))] += 1
            gold_counts[(task_type, row["gold_count"])] += 1
    if set(by_task) != set(TASK_TYPES):
        raise ValueError("all six tasks are required")

    first_examples = []
    for task_type in TASK_TYPES:
        example = next(row for row in rows if row["task_type"] == task_type)
        first_examples.append(example)
    write_csv(release / "examples" / "one_per_task.csv", first_examples, public_fields or [])
    write_csv(release / "reports" / "rows_by_task.csv", [{"task_type": key, "row_count": value} for key, value in sorted(by_task.items())], ["task_type", "row_count"])
    write_csv(release / "reports" / "rows_by_source.csv", [{"task_type": key[0], "source_dataset": key[1], "row_count": value} for key, value in sorted(by_source.items())], ["task_type", "source_dataset", "row_count"])
    write_csv(release / "reports" / "candidate_count_distribution.csv", [{"task_type": key[0], "candidate_count_bucket": key[1], "row_count": value} for key, value in sorted(by_bucket.items())], ["task_type", "candidate_count_bucket", "row_count"])
    write_csv(release / "reports" / "gold_count_distribution.csv", [{"task_type": key[0], "gold_count": key[1], "row_count": value} for key, value in sorted(gold_counts.items())], ["task_type", "gold_count", "row_count"])

    counts_text = "\n".join(f"- `{task}`: {count}" for task, count in sorted(by_task.items()))
    (release / "README.md").write_text(
        "# ServiceDiscoveryBench v0.1\n\n"
        "A benchmark-only dataset for LLM service discovery and API recommendation across six task types. "
        "Rows expose a user query, allowed context, a natural canonical candidate space, and one or more acceptable Gold sets.\n\n"
        "## Tasks\n\n" + counts_text + "\n\n"
        "Service targets select tools/services; API targets select concrete operations under their parent services. "
        "Single tasks require one service family, multi tasks contain independent cross-service requirements, and composable tasks require trace-grounded cross-service output-to-input or control dependency confirmed by humans.\n\n"
        "## Construction and quality\n\nSources are adapted into canonical service/API catalogs, candidates are reconstructed only from real catalog entries, exact visible-surface leakage is blocked, and task-level rows pass human-only blind QA. "
        "Composable v0.1 contains the accepted 95 service / 92 API release scale rather than an artificial rounded target.\n\n"
        "## Split and baselines\n\nTrain/dev/test use connected relationship groups, so paired, source-linked, signature-linked, and repair-family rows never cross splits. "
        "See `splits/split_report.md` and `reports/baselines/BASELINE_COMPARISON.md`. Reproduction commands are recorded in run manifests and `COMMANDS.log` files of the release build.\n",
        encoding="utf-8",
    )
    (release / "DATA_CARD.md").write_text(
        "# Data Card\n\n"
        "Sources: ToolBench, StableToolBench, MetaTool, and ShortcutsBench, with source-version hashes retained in manifests and catalogs. "
        "Filtering removes structural invalidity, exact blocking leakage, duplicates, and rows rejected by human QA. Candidate spaces contain real catalog items; no LLM-generated tools or APIs are used.\n\n"
        "Final task-level QA uses one authoritative natural-person primary review under the recorded single-human-review policy, with content fingerprints and auditable exclusion of rejected rows. Any secondary exports are supplemental and non-gating. Reviewer identities are pseudonymous internal provenance and are not model inputs. "
        "Potential biases include source/domain imbalance, very large candidate catalogs, ToolBench dominance in composable tasks, and dependence on historical execution traces. This benchmark does not claim live API availability, execution safety, or suitability for autonomous deployment.\n",
        encoding="utf-8",
    )
    (release / "SCHEMA.md").write_text(
        "# Schema\n\n"
        "Each task CSV follows the machine-readable public task schema. IDs are canonical and stable. JSON-valued columns store arrays or objects using stable serialization. "
        "For the active prediction target, Gold must be a strict subset of candidates and at least one non-Gold candidate must remain. API rows include API-to-parent-service mapping. "
        "Alternative acceptable Gold sets are evaluated independently; exact-set and ranking metrics take the best valid alternative rather than unioning incompatible solutions. Signatures and fingerprints use the versions recorded in the config and manifests.\n",
        encoding="utf-8",
    )
    (release / "CHANGELOG.md").write_text("# Changelog\n\n## v0.1\n\n- First six-task release.\n- Human-only task-level QA and group-aware splits.\n- Trace-grounded composable branch frozen at 95 service and 92 API rows.\n", encoding="utf-8")
    (release / "PUBLIC_PACKAGING_NOTES.md").write_text(
        "# Public packaging notes\n\n"
        "Workstation-absolute provenance paths are replaced by `PROJECT_ROOT` or `USER_HOME` logical prefixes. "
        "Because dependency-graph provenance is part of the task-signature payload, task signatures are recomputed after this non-semantic redaction and propagated to every task/split copy and the split manifest. "
        "Stable benchmark task IDs remain the frozen RC1 identities. Human-review fingerprints remain audit references to the pre-publication RC1 content.\n\n"
        "Internal review interfaces, blind packs, blank review templates, and adjudication workspaces are not public release data and are excluded. Final QA reports, policy, attestations, decisions, and review summaries remain under `qa/`.\n",
        encoding="utf-8",
    )

    scrub_private_paths(release)
    refresh_public_task_signatures(release)
    assert_no_private_paths(release)

    sums = []
    for path in sorted((path for path in release.rglob("*") if path.is_file() and path.name != "SHA256SUMS.txt"), key=lambda p: p.as_posix()):
        sums.append(f"{sha256_file(path)}  {path.relative_to(release).as_posix()}")
    (release / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    status = {
        "stage": "FINAL_RELEASE", "status": "RELEASE_READY", "release_ready": True,
        "g0_to_g6_verified": True, "task_counts": dict(sorted(by_task.items())), "total_rows": len(rows),
        "source_terms_status": SOURCE_TERMS_CLEARED,
    }
    write_json(output / "RUN_STATUS.json", status)
    inputs = [
        rc1_run / "RUN_STATUS.json", split_run / "RUN_STATUS.json",
        baseline_run / "RUN_STATUS.json", source_terms,
        *support_paths, license_dir / "README.md", *license_paths,
    ]
    write_csv(output / "INPUT_MANIFEST.csv", [{"resolved_path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in inputs], ["resolved_path", "size_bytes", "sha256"])
    (output / "COMMANDS.log").write_text(" ".join(sys.argv) + "\n", encoding="utf-8")
    files = [path for path in output.rglob("*") if path.is_file() and path.name != "OUTPUT_MANIFEST.csv"]
    write_csv(output / "OUTPUT_MANIFEST.csv", [{"relative_path": path.relative_to(output).as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in sorted(files)], ["relative_path", "size_bytes", "sha256"])
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
