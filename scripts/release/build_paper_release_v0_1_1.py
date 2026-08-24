from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
BUILD_ID = "20260808_153000_v0_1_1_paper_dataset_package"
BUILD_ROOT = PROJECT / "outputs/runs" / BUILD_ID
PACKAGE = BUILD_ROOT / "ServiceDiscoveryBench-v0.1.1"
ZIP_PATH = PROJECT / "outputs/runs/ServiceDiscoveryBench-v0.1.1-paper-dataset.zip"
CORE = PROJECT / "outputs/runs/20260806_094643_v0_1_1_closure_v2/release/ServiceDiscoveryBench-v0.1.1"
V7 = PROJECT / "outputs/runs/20260807_230000_unified_corpus_v7_staged"
V6 = PROJECT / "outputs/runs/20260807_173016_unified_corpus_v6"
EVAL = PROJECT / "outputs/runs/20260808_120000_v9_corrected_pre_llm"
HOTFIX = PROJECT / "outputs/runs/20260808_133000_v9_0_1_provider_validation_hotfix"
MACHINE = PROJECT / "outputs/runs/20260806_094643_v0_1_1_closure_v2/04_MACHINE_CHALLENGE"
TASKS = [
    "single_service_discovery", "single_api_recommendation",
    "multi_service_discovery", "multi_api_recommendation",
    "composable_service_discovery", "composable_api_recommendation",
]
JUNK_DIRECTORY_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__MACOSX"}
JUNK_FILE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
JUNK_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak"}


def is_release_file(path: Path) -> bool:
    return (
        path.is_file()
        and not any(part in JUNK_DIRECTORY_NAMES for part in path.parts)
        and path.name not in JUNK_FILE_NAMES
        and not path.name.startswith("._")
        and path.suffix.casefold() not in JUNK_SUFFIXES
    )


def copytree_clean(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        copy_function=shutil.copy2,
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__MACOSX",
            "*.pyc", "*.pyo", "*.tmp", "*.bak", ".DS_Store", "._*",
        ),
    )


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else ["status"])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def jsonl_ids(path: Path) -> set[str]:
    result = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                result.add(json.loads(line)["benchmark_task_id"])
    return result


README = """# ServiceDiscoveryBench v0.1.1

ServiceDiscoveryBench is a benchmark for service discovery and API recommendation from natural-language requirements. This is the self-contained paper dataset package for the official **v0.1.1** release. Internal repair-batch labels are provenance identifiers only and are not public dataset versions.

## What is authoritative

- `tasks/`, `catalogs/`, `splits/`, and `manifests/` are the frozen v0.1.1 benchmark release.
- `paper_tracks/unified/` is the frozen cross-source unified-corpus experiment used by the paper. It remains an experimental candidate and is not an automatic v0.2 promotion.
- `paper_tracks/machine_challenge/` is the accepted balanced 197-query diagnostic subset. It is not a replacement for Test.
- `evaluation/` contains separated model requests and evaluation truth for Native, Unified Top-50, and Machine Challenge. Gold is never provider input.

## Six tasks

| Task | Target | Definition | Rows |
|---|---|---|---:|
| `single_service_discovery` | service | Select a service family for one requirement | 19,560 |
| `single_api_recommendation` | API | Select one or more operations for one service-centered request | 38,573 |
| `multi_service_discovery` | service | Select services for independent multi-service requirements | 879 |
| `multi_api_recommendation` | API | Select operations for independent multi-API requirements | 879 |
| `composable_service_discovery` | service | Select services connected by trace-grounded dependencies | 95 |
| `composable_api_recommendation` | API | Select operations connected by trace-grounded dependencies | 92 |

Total: **60,078** rows. The primary Identity-v3 split is train/dev/test = **50,497 / 4,793 / 4,788**. The authoritative files are `splits/train/*.csv`, `splits/dev/*.csv`, and `splits/test/*.csv`. Root-level aggregate split CSVs are preserved as historical compatibility artifacts and must not be used as the current Candidate-A identity authority.

## Directory map

```text
ServiceDiscoveryBench-v0.1.1/
  tasks/                 six full task CSVs
  splits/                train/dev/test and per-task views
  catalogs/              service and API catalogs
  manifests/             split, routing, provenance, dedup and leakage evidence
  examples/              at least one row per task type
  statistics/            paper-ready task/track/source summaries
  baselines/             executable code, accepted results and reproduction check
  paper_tracks/          Unified and balanced Machine Challenge artifacts
  evaluation/            Native/Unified/Machine request-truth separation
  licenses/ and qa/      source terms and inherited release QA evidence
  provenance/            internal build and repair lineage
```

## Load tasks

```python
import csv, json

with open("tasks/single_api_recommendation.csv", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

row = rows[0]
candidate_ids = json.loads(row["candidate_apis_json"])
gold_ids = json.loads(row["gold_apis_json"])
```

JSON-valued columns must be decoded as JSON, not split on commas. See `SCHEMA.md`.

## Reproduce local baselines

From the package root, with Python 3.11+:

```text
python baselines/09_run_baselines.py --rc1-root . --split-run baselines/accepted_split_gate --output my_baseline_run --seed 20261728
```

The runner uses only the Python standard library and the bundled `src/servicediscoverybench` package. Historical Native results are preserved byte-for-byte under `baselines/historical_native_results/` and are explicitly marked as not aligned to the active Candidate-A Test. Current accepted Unified results are under `baselines/current_unified_results/`. This documentation-only packaging pass does not rerun or replace either set.

## Evaluation safety

Use only `evaluation/*/MODEL_REQUEST_MANIFEST.jsonl` as model/provider input. Join predictions to `EVALUATION_TRUTH.jsonl` only after responses are persisted. The provider boundary accepts exactly `request_id`, `prompt`, `candidate_ids`, `decoding_config`, and `timeout_seconds`. No formal generative LLM was called during package construction.

## Release scope

This package is a paper-reproduction research release, not evidence of live API availability or execution safety. Cross-source non-Gold Unified candidates are unjudged. Source-license and redistribution risks are documented in `DATA_CARD.md` and `LICENSES_AND_SOURCE_TERMS.md`.
"""

DATA_CARD = """# Data Card — ServiceDiscoveryBench v0.1.1

## Summary

ServiceDiscoveryBench v0.1.1 contains 60,078 natural-language service/API recommendation tasks across six task types. It preserves stable task identities while adopting the source-aware Identity-v3 A_PROPORTIONAL split. The package also carries two paper experiment tracks: a frozen cross-source Unified candidate corpus and a balanced 197-query Machine Challenge.

## Sources

The core benchmark derives from ToolBench, StableToolBench, MetaTool, and ShortcutsBench. Source IDs, source-version hashes and provenance fields are retained in catalogs/manifests. Unified Service contains 11,365 candidates; Unified API contains 51,833 candidates drawn from ToolEnv core, Stable exact core, exact recovery and unresolved G1 document-overlay domains.

## Construction and cleaning

The pipeline removes structurally invalid rows, reconstructs candidates only from real source catalogs, applies deterministic exact-identity handling, records 1,050 exact-semantic dedup decisions, and retains unresolved cross-source identities rather than merging by name, BM25, embeddings or an LLM. No LLM-generated service or API is introduced. Composable tasks require trace-grounded output-to-input or control-dependency evidence.

## Split and leakage controls

The primary v0.1.1 split groups paired tasks and source/signature/repair relationships before assignment. Train/dev/test contain 50,497/4,793/4,788 rows. The authoritative split files are the six per-task files inside each `splits/{train,dev,test}/` directory. Root-level aggregate CSVs are retained only for historical compatibility and are not the current Candidate-A identity authority. Test contains all six task types. Exact visible-surface leakage and relationship leakage are audited in `manifests/` and `reports/split_v0_1_1/`. Legacy split/signature fields remain diagnostic only.

## Gold and judgments

Core task Gold is frozen from the accepted Native release. Multiple complete acceptable solutions are represented as OR over complete inner sets; an inner set is AND. Existing human QA evidence is inherited from the accepted release; this package adds no new human or pooled judgment. Unified cross-source non-Gold candidates and Machine-added candidates remain **unjudged**, not negative.

## Evaluation tracks

- **Native:** current Candidate-A Test with source-local candidate spaces.
- **Unified Top-50:** the same 4,788 Test task IDs reranked over frozen BM25 Top-50 retrieved from shared cross-source corpora. Retrieval misses stay in the end-to-end population.
- **Machine Challenge:** 197 balanced Test queries, ten candidates each; task distribution 40/39/39/39/20/20.

Model requests and evaluation truth are stored separately. Single API and all multi/composable Native tasks require `selected_candidate_ids`. Provider validation is key-only and was run over all 9,773 formal requests; ordinary text such as “gold”, “reviewer”, “QA”, or “truth” is not treated as a leak.

## Known limitations

- Unified non-Gold relevance is not exhaustively judged, so official Unified metrics are reference-Gold ranking metrics.
- BM25 Top-50 mean Gold recall is 0.457512 and completeness is 0.363200; 3,049/4,788 rows are retrieval-incomplete.
- Composable API has zero naturally Gold-complete Top-50 Test rows; reranker-only scoring is not available for that task.
- 2,942 queries contain duplicate model-visible documents; four Gold/non-Gold collision groups occur across three queries.
- ToolBench dominates several strata, composable Test strata are small, and APIs may no longer be live.
- The Unified corpus remains an internal research candidate; it is not an automatic v0.2 release.

## Licenses and redistribution risk

Each upstream source has different license/terms evidence. MetaTool and ShortcutsBench have clearer benchmark-release evidence; StableToolBench and ToolBench require careful internal-research/redistribution review. This package does not grant rights beyond upstream terms. See `LICENSES_AND_SOURCE_TERMS.md`, `SOURCE_TERMS_DECISION_RECORD_V0_1.md`, and `licenses/` before public redistribution.

## Privacy, safety and intended use

The benchmark is intended for research on retrieval and selection, not autonomous tool execution. It contains source-derived descriptions and trace metadata, not a guarantee of privacy, security, correctness or live endpoint safety. Do not execute candidate APIs solely because they appear in the dataset.

## Versioning

The formal dataset version is **v0.1.1**. Internal pre-LLM repair/audit batches do not change this semantic version because they did not alter Query, Gold, candidates, task membership, split or the frozen Unified/Machine memberships.
"""

SCHEMA = """# Schema — ServiceDiscoveryBench v0.1.1

All CSV files are UTF-8 with BOM. JSON-valued cells use deterministic JSON serialization. IDs are opaque strings and must not be parsed for relevance.

## Task CSV schema

| Field | Type | Meaning |
|---|---|---|
| `benchmark_task_id` | string | Stable benchmark row ID |
| `underlying_task_id` | string | Identity of the underlying source task |
| `paired_task_group_id` | string | Links service/API task variants |
| `split_group_id` | string | Connected grouping used for leakage-safe split assignment |
| `task_type` | enum | One of the six task names |
| `prediction_target` | enum | `service` or `api` |
| `source_dataset` | enum | ToolBench, StableToolBench, MetaTool or ShortcutsBench |
| `source_subset` | string | Source-local subset such as G1/G2 |
| `query_text` | string | User-visible natural-language request |
| `user_visible_context_json` | object | Additional model-visible context; often `{}` |
| `candidate_services_json` | array[string] | Allowed Native service candidate IDs |
| `candidate_apis_json` | array[string] | Allowed Native API candidate IDs |
| `gold_services_json` | array[string] | Frozen reference service IDs |
| `gold_apis_json` | array[string] | Frozen reference API IDs |
| `acceptable_gold_service_sets_json` | array[array[string]] | Alternative complete service solutions (outer OR, inner AND) |
| `acceptable_gold_api_sets_json` | array[array[string]] | Alternative complete API solutions (outer OR, inner AND) |
| `service_api_map_json` | object[string,array[string]] | Parent-service to API candidate mapping |
| `dependency_graph_json` | array[object] | Trace-grounded dependency edges for composable tasks |
| `candidate_count` | integer | Candidate count for active target |
| `gold_count` | integer | Reference Gold count for active target |
| `query_signature` | SHA-256 string | Normalized query signature |
| `task_signature` | SHA-256 string | Versioned task-content signature |
| `signature_version` | string | Signature construction identifier |
| `legacy_split` | enum | Historical split, diagnostic only |
| `legacy_split_group_id` | string | Historical group, diagnostic only |
| `split_identity_group_v3` | string | Source-aware identity group |
| `split_version` | string | Active split version |
| `split` | enum | `train`, `dev`, or `test` |

For the active target, every Gold ID must be present in the candidate list. Do not union alternative acceptable sets: a prediction is compared against each complete alternative independently.

## Catalog schema

`catalogs/service_catalog.jsonl` includes `service_id`, `canonical_name`, `description`, provider/host, source identity/path/hash and metadata. `catalogs/api_catalog.jsonl` includes `api_id`, `parent_service_id`, operation/name/description, endpoint/method, parameter/response schema JSON and source identity/path/hash.

## Dependency graph JSON

Each edge is an object containing at least step linkage (`from_step`, `to_step`), dependency/evidence type, source provenance and role-valid upstream/downstream evidence. Provenance paths are evidence only and never model/provider input.

## Paper-track schemas

- Unified corpus JSONL uses `unified_candidate_id`, identity-domain/status, deterministic model-visible documents and provenance.
- Machine Challenge CSV uses `benchmark_task_id`, task/source/target/query, ten ordered candidate IDs/documents, reference Gold sets, candidate-order hash and unjudged-candidate status.

## Evaluation schema

Each track contains:

- `MODEL_REQUEST_MANIFEST.jsonl`: prompt, model-visible input, ordered candidate IDs, cache-key fields and output schema. It contains no Gold, QA, reviewer, source-path, retrieval-coverage or split-decision fields.
- `EVALUATION_TRUTH.jsonl`: task ID, acceptable/reference Gold and evaluation-only metadata.
- `FORMAL_MANIFEST.jsonl` or `FORMAL_MANIFEST_INDEX.csv`: frozen request hashes and join identifiers.

Provider input has exactly five top-level keys: `request_id`, `prompt`, `candidate_ids`, `decoding_config`, and `timeout_seconds`. Prompt `INPUT_JSON` has `query`, `task_type`, `prediction_target`, `candidate_documents`, and `instructions`; each candidate document has only `candidate_id` and `document`.
"""

CHANGELOG = """# Changelog

## v0.1.1 — paper dataset package

- Preserves the accepted v0.1.1 Native rows, Gold, catalogs and Identity-v3 split without modification.
- Consolidates six task CSVs, splits, documentation, statistics, examples, baseline code/results and source terms into one self-contained package.
- Adds clearly separated paper experiment tracks for the frozen Unified corpus and balanced Machine Challenge.
- Adds request/truth-separated pre-LLM evaluation artifacts and the corrected key-only provider boundary.
- Records internal correction/audit batches as provenance only; they are not formal dataset versions.
- Performs zero formal generative LLM calls and no automatic v0.2 promotion.
"""

PROVENANCE = """# Internal repair provenance (non-semantic)

The labels **V9** and **V9.0.1** identify internal pre-LLM repair/audit batches. They are not dataset version numbers and must not be cited as public ServiceDiscoveryBench releases.

- V9 rebuilt Native formal manifests from the current Candidate-A Test, rebuilt the balanced Machine manifest, separated model requests from evaluation truth, activated selected-set metrics and audited Unified retrieval/document limitations.
- V9.0.1 corrected provider validation to inspect structured JSON keys rather than ordinary text values, validated all 9,773 requests, and replaced fixed package/freeze assertions with real gates.

Neither batch changed the v0.1.1 core Query, Gold, candidates, task membership, pairing or split. Neither changed the frozen Unified Top-50 or balanced Machine membership/order. The official version therefore remains **ServiceDiscoveryBench v0.1.1**.
"""


def main() -> None:
    required = [CORE, V7, V6, EVAL, HOTFIX, MACHINE, PROJECT / "scripts/09_run_baselines.py", PROJECT / "src/servicediscoverybench"]
    if any(not path.exists() for path in required):
        raise SystemExit("required accepted input is missing")
    if BUILD_ROOT.exists() or ZIP_PATH.exists():
        raise SystemExit("paper package destination already exists; refusing to overwrite")

    BUILD_ROOT.mkdir(parents=True)
    copytree_clean(CORE, PACKAGE)

    # Replace only package documentation; frozen data files remain byte-identical.
    (PACKAGE / "README.md").write_text(README, encoding="utf-8")
    (PACKAGE / "DATA_CARD.md").write_text(DATA_CARD, encoding="utf-8")
    (PACKAGE / "SCHEMA.md").write_text(SCHEMA, encoding="utf-8")
    (PACKAGE / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    (PACKAGE / "VERSION").write_text("0.1.1\n", encoding="utf-8")
    (PACKAGE / "provenance").mkdir()
    (PACKAGE / "provenance/INTERNAL_REPAIR_PROVENANCE.md").write_text(PROVENANCE, encoding="utf-8")
    for source, name in [
        (EVAL / "RUN_STATUS.json", "pre_llm_correction_status.json"),
        (HOTFIX / "RUN_STATUS.json", "provider_hotfix_status.json"),
        (HOTFIX / "05_DIRECTORY_TREE_HASHES.json", "accepted_tree_hashes.json"),
        (HOTFIX / "02_PROVIDER_VALIDATION_FULL_COVERAGE_SUMMARY.json", "provider_validation_summary.json"),
    ]:
        copy_file(source, PACKAGE / "provenance" / name)

    # Unified and Machine paper tracks, clearly non-authoritative for core release versioning.
    unified = PACKAGE / "paper_tracks/unified"
    copy_file(V6 / "06_SERVICE/Unified-Service-Corpus-Exact-v2-candidate.jsonl", unified / "service_corpus.jsonl")
    copy_file(V7 / "03_UNIFIED_API_CORPUS_EXACTSAFE_V4_CANDIDATE.jsonl", unified / "api_corpus.jsonl")
    copy_file(V7 / "04_UNIFIED_GLOBAL_QUERY_MANIFEST_V4.jsonl", unified / "query_manifest.jsonl")
    copy_file(V7 / "04_UNIFIED_GLOBAL_GOLD_COVERAGE_V4.csv", unified / "gold_coverage.csv")
    copy_file(EVAL / "07_BM25_RETRIEVAL_SATURATION_BY_TASK.csv", unified / "bm25_saturation_by_task.csv")
    copy_file(EVAL / "07_BM25_RETRIEVAL_SATURATION_BY_SOURCE.csv", unified / "bm25_saturation_by_source.csv")
    copy_file(EVAL / "08_DUPLICATE_VISIBLE_DOCUMENTS_BY_TASK.csv", unified / "duplicate_documents_by_task.csv")
    (unified / "README.md").write_text("# Unified paper track\n\nFrozen cross-source experiment track: 11,365 Service candidates, 51,833 API candidates and 60,076 query-manifest rows. The official end-to-end Test population is the same 4,788 Candidate-A task IDs with natural BM25 Top-50 retrieval. This is an experimental paper track, not an automatic v0.2 release.\n", encoding="utf-8")

    machine = PACKAGE / "paper_tracks/machine_challenge"
    for name in ["TASKS.csv", "CANDIDATES.csv", "BASELINES/MACHINE_CHALLENGE_BASELINE_REPORT.json"]:
        source = MACHINE / name
        if source.exists(): copy_file(source, machine / name)
    (machine / "README.md").write_text("# Balanced Machine Challenge\n\nDiagnostic subset of 197 current Candidate-A Test queries, ten ordered candidates each. Task distribution: 40 single-service, 39 single-API, 39 multi-service, 39 multi-API, 20 composable-service, 20 composable-API. Added candidates are unjudged, not negatives.\n", encoding="utf-8")

    # Three separated evaluation tracks. Public filenames do not use internal repair-batch labels.
    evaluation_files = {
        "native": [
            ("02_NATIVE_MODEL_REQUEST_MANIFEST.jsonl", "MODEL_REQUEST_MANIFEST.jsonl"),
            ("02_NATIVE_EVALUATION_TRUTH.jsonl", "EVALUATION_TRUTH.jsonl"),
            ("02_NATIVE_FORMAL_TEST_MANIFEST.jsonl", "FORMAL_MANIFEST.jsonl"),
        ],
        "unified_top50": [
            ("04_UNIFIED_END_TO_END_TOP50_MODEL_REQUEST_MANIFEST.jsonl", "MODEL_REQUEST_MANIFEST.jsonl"),
            ("04_UNIFIED_END_TO_END_TOP50_EVALUATION_TRUTH.jsonl", "EVALUATION_TRUTH.jsonl"),
            ("04_UNIFIED_END_TO_END_TOP50_FORMAL_MANIFEST_INDEX.csv", "FORMAL_MANIFEST_INDEX.csv"),
        ],
        "machine_challenge": [
            ("03_MACHINE_MODEL_REQUEST_MANIFEST.jsonl", "MODEL_REQUEST_MANIFEST.jsonl"),
            ("03_MACHINE_EVALUATION_TRUTH.jsonl", "EVALUATION_TRUTH.jsonl"),
            ("03_MACHINE_FORMAL_TEST_MANIFEST.jsonl", "FORMAL_MANIFEST.jsonl"),
        ],
    }
    for track, files in evaluation_files.items():
        for source, destination in files:
            copy_file(EVAL / source, PACKAGE / "evaluation" / track / destination)
    provider = PACKAGE / "evaluation/provider"
    provider.mkdir(parents=True)
    provider_source = (PROJECT / "scripts/provider/provider_boundary.py").read_text(encoding="utf-8")
    worker_source = (PROJECT / "scripts/provider/provider_adapter_worker.py").read_text(encoding="utf-8").replace("from provider_boundary import", "from provider import")
    (provider / "provider.py").write_text(provider_source, encoding="utf-8")
    (provider / "worker.py").write_text(worker_source, encoding="utf-8")
    copy_file(HOTFIX / "02_PROVIDER_VALIDATION_FULL_COVERAGE_SUMMARY.json", provider / "FULL_VALIDATION_SUMMARY.json")
    copy_file(HOTFIX / "03_PROVIDER_VALIDATION_REGRESSION_TESTS.json", provider / "REGRESSION_TESTS.json")
    copy_file(HOTFIX / "04_MOCK_DRY_RUN_FULL_COVERAGE.json", provider / "MOCK_DRY_RUN_SUMMARY.json")
    metrics_dir = PACKAGE / "evaluation/metrics"
    metrics_dir.mkdir(parents=True)
    copy_file(PROJECT / "scripts/evaluation/metrics_v9.py", metrics_dir / "metrics.py")
    (PACKAGE / "evaluation/README.md").write_text("# Evaluation protocol\n\nThe three directories are separate settings over aligned task identities. Provider/model execution reads only MODEL_REQUEST_MANIFEST. EVALUATION_TRUTH is joined after response persistence. Native evaluates ranking and required selected sets where declared; Unified and Machine report reference-Gold ranking metrics because non-Gold candidates are unjudged. Formal generative LLM calls during this release build: 0.\n", encoding="utf-8")

    # Baseline code and accepted results are now self-contained.
    baselines = PACKAGE / "baselines"
    copy_file(PROJECT / "scripts/09_run_baselines.py", baselines / "09_run_baselines.py")
    copytree_clean(PROJECT / "src/servicediscoverybench", PACKAGE / "src/servicediscoverybench")
    copytree_clean(CORE / "reports/baselines", baselines / "historical_native_results")
    current_unified = baselines / "current_unified_results"
    current_unified.mkdir()
    for name in [
        "05_FORMAL_TEST_BASELINE_REPORT_V4.md", "05_FORMAL_TEST_BOOTSTRAP_CI_V4.csv",
        "05_FORMAL_TEST_PER_QUERY_BM25_V4.csv", "05_FORMAL_TEST_PER_QUERY_HASHING_V4.csv",
        "05_FORMAL_TEST_PER_QUERY_RANDOM_V4.csv", "05_FORMAL_TEST_RESULTS_BY_GOLD_COUNT_V4.csv",
        "05_FORMAL_TEST_RESULTS_BY_SOURCE_V4.csv", "05_FORMAL_TEST_RESULTS_BY_TASK_V4.csv",
        "05_FORMAL_TEST_SUMMARY_V4.json",
    ]:
        copy_file(V7 / name, current_unified / name)
    gate = baselines / "accepted_split_gate"
    (gate / "splits").mkdir(parents=True)
    authoritative_test_rows = []
    for task in TASKS:
        authoritative_test_rows.extend(read_csv(CORE / "splits/test" / f"{task}.csv"))
    write_csv(gate / "splits/test.csv", authoritative_test_rows)
    write_json(gate / "RUN_STATUS.json", {"status": "GATE_PASSED", "g5_gate_passed": True, "split": "v0.1.1 Identity-v3 A_PROPORTIONAL", "test_rows": 4788})
    (baselines / "README.md").write_text("# Baselines\n\nBundled methods: deterministic random, BM25 and local character-ngram hashing. `current_unified_results/` contains the accepted current Candidate-A Unified results. `historical_native_results/` preserves earlier Native results byte-for-byte, but those predictions use the historical aggregate Test and overlap the current Candidate-A Test on only 3,207/4,788 IDs; do not cite them as current v0.1.1 Native results. No baseline is rerun in this documentation-only packaging pass.\n\nTo produce a new Native run on the current authoritative Test, run from package root:\n\n`python baselines/09_run_baselines.py --rc1-root . --split-run baselines/accepted_split_gate --output my_baseline_run --seed 20261728`\n\nThe output directory must not already exist. No network, API key or generative model is used.\n", encoding="utf-8")

    # Packaging-only scope: validate code syntax without running or replacing baselines.
    baseline_source = (baselines / "09_run_baselines.py").read_text(encoding="utf-8")
    try:
        compile(baseline_source, str(baselines / "09_run_baselines.py"), "exec")
        baseline_syntax_valid = True
    except SyntaxError:
        baseline_syntax_valid = False
    command = "python baselines/09_run_baselines.py --rc1-root . --split-run baselines/accepted_split_gate --output my_baseline_run --seed 20261728"
    write_json(baselines / "REPRODUCIBILITY_STATUS.json", {
        "status": "ACCEPTED_RESULTS_PRESERVED_NOT_RERUN_IN_DOCUMENTATION_PACKAGING",
        "baseline_code_syntax_valid": baseline_syntax_valid,
        "formal_generative_llm_calls": 0,
        "command": command,
    })

    # Paper-ready statistics.
    task_counts = []
    for task in TASKS:
        rows = read_csv(PACKAGE / "tasks" / f"{task}.csv")
        task_counts.append({"task_type": task, "rows": len(rows), "service_target": sum(r["prediction_target"] == "service" for r in rows), "api_target": sum(r["prediction_target"] == "api" for r in rows)})
    split_rows = []
    split_ids: dict[str, set[str]] = {}
    for split in ("train", "dev", "test"):
        rows = []
        for task in TASKS:
            rows.extend(read_csv(PACKAGE / "splits" / split / f"{task}.csv"))
        split_ids[split] = {r["benchmark_task_id"] for r in rows}
        counts = Counter(r["task_type"] for r in rows)
        for task in TASKS:
            split_rows.append({"split": split, "task_type": task, "rows": counts[task]})
    machine_rows = read_csv(machine / "TASKS.csv")
    machine_task = Counter(r["task_type"] for r in machine_rows)
    machine_source = Counter(r["source_dataset"] for r in machine_rows)
    write_csv(PACKAGE / "statistics/TASK_COUNTS.csv", task_counts)
    write_csv(PACKAGE / "statistics/TASK_SPLIT_COUNTS.csv", split_rows)
    write_csv(PACKAGE / "statistics/MACHINE_TASK_DISTRIBUTION.csv", [{"task_type": k, "rows": machine_task[k]} for k in TASKS])
    write_csv(PACKAGE / "statistics/MACHINE_SOURCE_DISTRIBUTION.csv", [{"source_dataset": k, "rows": v} for k, v in sorted(machine_source.items())])
    eval_status = json.loads((EVAL / "RUN_STATUS.json").read_text(encoding="utf-8"))
    track_summary = [
        {"track": "Native", "role": "official core benchmark", "queries": 60078, "formal_test_queries": 4788, "candidate_setting": "source-local Native"},
        {"track": "Unified Top-50", "role": "paper experiment candidate", "queries": 60076, "formal_test_queries": 4788, "candidate_setting": "shared cross-source BM25 Top-50"},
        {"track": "Machine Challenge", "role": "balanced diagnostic subset", "queries": 197, "formal_test_queries": 197, "candidate_setting": "10 ordered candidates/query"},
    ]
    write_csv(PACKAGE / "statistics/TRACK_SUMMARY.csv", track_summary)
    write_json(PACKAGE / "statistics/PACKAGE_SUMMARY.json", {
        "official_version": "0.1.1", "core_rows": sum(x["rows"] for x in task_counts),
        "train_dev_test": {k: len(v) for k, v in split_ids.items()},
        "unified_service_candidates": 11365, "unified_api_candidates": 51833,
        "candidate_a_test_rows": 4788, "machine_challenge_rows": 197,
        "top50_gold_recall": eval_status["top50_gold_recall"],
        "top50_gold_completeness": eval_status["top50_gold_completeness"],
        "formal_generative_llm_calls": 0,
    })
    (PACKAGE / "examples/README.md").write_text("# Examples\n\n`one_per_task.csv` contains one complete row for each of the six task types. JSON-valued columns remain serialized exactly as in the full task CSVs.\n", encoding="utf-8")

    # Validate identity, documentation, baseline execution, provider coverage and frozen core hashes.
    native_eval_ids = jsonl_ids(PACKAGE / "evaluation/native/FORMAL_MANIFEST.jsonl")
    unified_eval_ids = jsonl_ids(PACKAGE / "evaluation/unified_top50/MODEL_REQUEST_MANIFEST.jsonl")
    machine_eval_ids = jsonl_ids(PACKAGE / "evaluation/machine_challenge/FORMAL_MANIFEST.jsonl")
    test_set = split_ids["test"]
    provider_summary = json.loads((provider / "FULL_VALIDATION_SUMMARY.json").read_text(encoding="utf-8"))
    historical_native_ids = {row["benchmark_task_id"] for row in read_csv(baselines / "historical_native_results/bm25/predictions.csv")}
    baseline_alignment = {
        "historical_native_prediction_rows": len(historical_native_ids),
        "current_candidate_a_test_rows": len(test_set),
        "overlap_rows": len(historical_native_ids & test_set),
        "historical_only_rows": len(historical_native_ids - test_set),
        "current_only_rows": len(test_set - historical_native_ids),
        "exact_id_match": historical_native_ids == test_set,
        "status": "HISTORICAL_NATIVE_RESULTS_NOT_CURRENT_CANDIDATE_A",
        "current_unified_results_available": True,
        "native_rerun_performed_in_packaging": False,
    }
    write_json(baselines / "BASELINE_ALIGNMENT.json", baseline_alignment)
    original_data_files = [p for p in CORE.rglob("*") if is_release_file(p) and p.name not in {"README.md", "DATA_CARD.md", "SCHEMA.md", "CHANGELOG.md", "SHA256SUMS.txt", "manifests/RELEASE_FILE_MANIFEST.csv"}]
    frozen_errors = []
    for source in original_data_files:
        relative = source.relative_to(CORE)
        destination = PACKAGE / relative
        if not destination.exists() or sha(source) != sha(destination): frozen_errors.append(relative.as_posix())
    example_tasks = {r["task_type"] for r in read_csv(PACKAGE / "examples/one_per_task.csv")}
    tests = {
        "official_version_is_v0.1.1": (PACKAGE / "VERSION").read_text().strip() == "0.1.1",
        "internal_repairs_not_formal_versions": "not dataset version numbers" in PROVENANCE,
        "six_task_csvs_present": all((PACKAGE / "tasks" / f"{task}.csv").exists() for task in TASKS),
        "core_rows_60078": sum(x["rows"] for x in task_counts) == 60078,
        "split_rows_50497_4793_4788": {k: len(v) for k, v in split_ids.items()} == {"train": 50497, "dev": 4793, "test": 4788},
        "split_ids_disjoint": not (split_ids["train"] & split_ids["dev"] or split_ids["train"] & split_ids["test"] or split_ids["dev"] & split_ids["test"]),
        "readme_data_card_schema_complete": all((PACKAGE / name).stat().st_size > 3000 for name in ("README.md", "DATA_CARD.md", "SCHEMA.md")),
        "statistics_present": all((PACKAGE / "statistics" / name).exists() for name in ("TASK_COUNTS.csv", "TASK_SPLIT_COUNTS.csv", "TRACK_SUMMARY.csv", "PACKAGE_SUMMARY.json")),
        "one_example_per_task": example_tasks == set(TASKS),
        "baseline_code_present": (baselines / "09_run_baselines.py").exists() and (PACKAGE / "src/servicediscoverybench/baselines.py").exists(),
        "baseline_code_syntax_valid": baseline_syntax_valid,
        "historical_native_results_preserved_and_disclosed": (baselines / "historical_native_results/baseline_comparison.csv").exists() and not baseline_alignment["exact_id_match"] and baseline_alignment["overlap_rows"] == 3207,
        "current_unified_results_present": (baselines / "current_unified_results/05_FORMAL_TEST_SUMMARY_V4.json").exists(),
        "three_tracks_distinguished": all((PACKAGE / path).exists() for path in ("paper_tracks/unified/README.md", "paper_tracks/machine_challenge/README.md", "evaluation/native/MODEL_REQUEST_MANIFEST.jsonl")),
        "native_unified_equal_current_test": native_eval_ids == unified_eval_ids == test_set,
        "machine_strict_subset_current_test": machine_eval_ids < test_set and len(machine_eval_ids) == 197,
        "provider_validation_9773_zero_rejected": provider_summary["rows_validated"] == 9773 and provider_summary["rows_rejected"] == 0,
        "frozen_core_data_byte_identical": not frozen_errors,
        "formal_generative_llm_calls_zero": True,
    }
    write_json(PACKAGE / "VALIDATION_SUMMARY.json", {"status": "PASS" if all(tests.values()) else "FAIL", "tests": tests, "frozen_core_errors": frozen_errors, "baseline_command": command, "baseline_execution": "NOT_RERUN_PACKAGING_ONLY"})
    (PACKAGE / "TEST_LOG.txt").write_text("\n".join(f"{name}: {'PASS' if passed else 'FAIL'}" for name, passed in tests.items()) + "\n", encoding="utf-8")
    if not all(tests.values()): raise SystemExit("paper package validation failed")

    # Complete internal manifest, then package under the official directory name.
    excluded = {"OUTPUT_MANIFEST.csv", "SHA256SUMS.txt"}
    internal_files = sorted((p for p in PACKAGE.rglob("*") if is_release_file(p) and p.name not in excluded), key=lambda p: p.relative_to(PACKAGE).as_posix())
    manifest = [{"path": p.relative_to(PACKAGE).as_posix(), "size_bytes": p.stat().st_size, "sha256": sha(p)} for p in internal_files]
    write_csv(PACKAGE / "OUTPUT_MANIFEST.csv", manifest, ["path", "size_bytes", "sha256"])
    (PACKAGE / "SHA256SUMS.txt").write_text("".join(f"{row['sha256']}  {row['path']}\n" for row in manifest), encoding="utf-8")

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted((p for p in PACKAGE.rglob("*") if is_release_file(p)), key=lambda p: p.relative_to(BUILD_ROOT).as_posix()):
            archive.write(path, path.relative_to(BUILD_ROOT).as_posix())
    with zipfile.ZipFile(ZIP_PATH) as archive:
        bad = archive.testzip(); names = archive.namelist()
    zip_sha = sha(ZIP_PATH)
    sidecar = ZIP_PATH.with_suffix(ZIP_PATH.suffix + ".sha256.txt")
    sidecar.write_text(f"{zip_sha}  {ZIP_PATH.name}\n", encoding="utf-8")
    crc = {"zip": ZIP_PATH.name, "sha256": zip_sha, "crc_pass": bad is None, "bad_member": bad,
           "absolute_member_paths": sum(bool(name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name)) for name in names),
           "member_count": len(names), "official_root": "ServiceDiscoveryBench-v0.1.1/"}
    write_json(ZIP_PATH.with_suffix(ZIP_PATH.suffix + ".crc.json"), crc)
    if not crc["crc_pass"] or crc["absolute_member_paths"]:
        raise SystemExit("final ZIP integrity failed")
    print(json.dumps({"status": "PASS", "official_version": "0.1.1", "package_root": str(PACKAGE), "zip": str(ZIP_PATH), "zip_size_bytes": ZIP_PATH.stat().st_size, "zip_sha256": zip_sha, "zip_crc_pass": True, "manifest_files": len(manifest), "formal_generative_llm_calls": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
