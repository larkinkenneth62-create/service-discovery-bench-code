from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path("outputs/final_qa_v1_5c")
DOC_DIR = Path("docs/phase1")
INPUT_REVIEW_SET = Path("outputs/final_qa_v1_5/final_qa_review_items_v1_5.csv")
INPUT_QA_PROTOCOL = Path("docs/phase1/final_qa_review_protocol_v1_5.md")
INPUT_TASK_TRACE = Path("outputs/full_clean_dryrun_v1_4/full_clean_task_trace_v1_4.csv")
INPUT_V4_2_RULE = Path("docs/phase1/manual_audit_rule_v4_2_candidate.md")
INPUT_SEMCAP_RULE = Path("docs/phase1/semantic_capability_detector_v1_rule_candidate.md")

FAILURE_ITEMS = {
    "FQA-1.5-002": {
        "severity": "critical",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "nearby_place_or_restaurant_requirement_not_covered;wrong_gold_set_for_service_level",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query asks for intimate anniversary restaurants with scenery and romantic movie recommendations; gold services do not cover restaurant recommendation, scenery, and movie recommendation.",
    },
    "FQA-1.5-005": {
        "severity": "critical",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "domain_specific_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query requires TTS plus translation; gold covers ChatGPT detection and TTS but lacks translation coverage.",
    },
    "FQA-1.5-008": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "nearby_place_or_restaurant_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query needs niche destination recommendations in Japan; gold does not clearly cover the destination recommendation requirement.",
    },
    "FQA-1.5-009": {
        "severity": "critical",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "current_weather_not_covered;domain_specific_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query needs Alps weather, sunrise/sunset, and hiking trail recommendation; Wavebase is surf/ocean oriented and WeatherAPI does not cover trail recommendation.",
    },
    "FQA-1.5-010": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "domain_specific_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query needs file storage option details and related images; gold does not fully cover the file storage comparison/feature requirement.",
    },
    "FQA-1.5-011": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "translation_direction_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query needs Greek phrases; gold does not clearly cover the needed Greek phrase/translation requirement.",
    },
    "FQA-1.5-013": {
        "severity": "major",
        "primary_failure_type": "domain_specific_requirement_not_covered",
        "secondary_failure_types": "wrong_gold_set_for_service_level",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Flight fare is incorrectly covered by an IRCTC rail fare capability; domain-specific flight fare coverage is missing.",
    },
    "FQA-1.5-017": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "domain_specific_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query asks what vehicle is required for a trip; gold does not correctly cover vehicle requirement reasoning.",
    },
    "FQA-1.5-022": {
        "severity": "major",
        "primary_failure_type": "domain_specific_requirement_not_covered",
        "secondary_failure_types": "wrong_gold_set_for_service_level",
        "qa_error_type": "capability_mismatch",
        "human_reason": "IP geocode/provider query is not matched by a census address geocoder capability.",
    },
    "FQA-1.5-029": {
        "severity": "major",
        "primary_failure_type": "translation_direction_not_covered",
        "secondary_failure_types": "domain_specific_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query needs Japanese map labels but gold appears to cover French labels instead.",
    },
    "FQA-1.5-035": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "generic_search_overtrusted",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Road trip destination recommendation is missing; playlist/news/spellcheck coverage is not enough.",
    },
    "FQA-1.5-037": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "current_weather_not_covered;nearby_place_or_restaurant_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query needs hiking trails, 3-hour weather forecast, and camping sites; gold mainly covers weather/climate and misses trail/camping recommendation.",
    },
    "FQA-1.5-039": {
        "severity": "major",
        "primary_failure_type": "wrong_extra_gold_service",
        "secondary_failure_types": "wrong_gold_set_for_service_level",
        "qa_error_type": "wrong_bucket",
        "human_reason": "Gold includes unrelated service `13`; service-level gold set contains unnecessary/unrelated service.",
    },
    "FQA-1.5-042": {
        "severity": "major",
        "primary_failure_type": "domain_specific_requirement_not_covered",
        "secondary_failure_types": "nearby_place_or_restaurant_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Catering restaurant capability is unclear and should not be high-confidence clean.",
    },
    "FQA-1.5-048": {
        "severity": "major",
        "primary_failure_type": "current_weather_not_covered",
        "secondary_failure_types": "wrong_gold_set_for_service_level",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Query requires current weather but gold API is Astronomy API, not current weather/forecast.",
    },
    "FQA-1.5-050": {
        "severity": "major",
        "primary_failure_type": "wrong_extra_gold_service",
        "secondary_failure_types": "wrong_gold_set_for_service_level",
        "qa_error_type": "wrong_bucket",
        "human_reason": "Currency Quake appears excessive or unnecessary in the gold set.",
    },
    "FQA-1.5-051": {
        "severity": "critical",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "generic_search_overtrusted",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Historical events and birth-year facts are missing from the gold coverage.",
    },
    "FQA-1.5-053": {
        "severity": "major",
        "primary_failure_type": "wrong_extra_gold_service",
        "secondary_failure_types": "wrong_gold_set_for_service_level",
        "qa_error_type": "wrong_bucket",
        "human_reason": "Query needs QR code and random password; gold also includes unrelated QuickMocker service.",
    },
    "FQA-1.5-059": {
        "severity": "major",
        "primary_failure_type": "domain_specific_requirement_not_covered",
        "secondary_failure_types": "wrong_gold_set_for_service_level",
        "qa_error_type": "capability_mismatch",
        "human_reason": "School-related image tile is incorrectly covered by Helioviewer solar tile.",
    },
    "FQA-1.5-060": {
        "severity": "major",
        "primary_failure_type": "domain_specific_requirement_not_covered",
        "secondary_failure_types": "wrong_gold_set_for_service_level",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Flights/hotels/cars booking is not sufficiently covered by Sagenda list bookable items.",
    },
    "FQA-1.5-073": {
        "severity": "critical",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "generic_search_overtrusted",
        "qa_error_type": "capability_mismatch",
        "human_reason": "City historical events requirement is missing.",
    },
    "FQA-1.5-075": {
        "severity": "major",
        "primary_failure_type": "domain_specific_requirement_not_covered",
        "secondary_failure_types": "missing_core_requirement",
        "qa_error_type": "capability_mismatch",
        "human_reason": "NBA all teams/details coverage is insufficient.",
    },
    "FQA-1.5-079": {
        "severity": "critical",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "generic_search_overtrusted",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Motivational quotes and entrepreneur stories are missing.",
    },
    "FQA-1.5-082": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "generic_search_overtrusted",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Inspiring quote requirement is missing; email and screenshot services only partially cover the query.",
    },
    "FQA-1.5-084": {
        "severity": "major",
        "primary_failure_type": "domain_specific_requirement_not_covered",
        "secondary_failure_types": "missing_core_requirement",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Current USD-to-GBP exchange rate is not clearly covered.",
    },
    "FQA-1.5-085": {
        "severity": "major",
        "primary_failure_type": "domain_availability_vs_domain_list",
        "secondary_failure_types": "domain_specific_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Domain availability check is not equivalent to a domain list endpoint.",
    },
    "FQA-1.5-088": {
        "severity": "critical",
        "primary_failure_type": "translation_direction_not_covered",
        "secondary_failure_types": "domain_specific_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "English-to-French sentence translation is not covered; Indic translation/dictionary services do not satisfy the direction and sentence-level requirement.",
    },
    "FQA-1.5-091": {
        "severity": "major",
        "primary_failure_type": "nearby_place_or_restaurant_requirement_not_covered",
        "secondary_failure_types": "missing_core_requirement",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Nearby grocery address/contact requirement is insufficiently covered.",
    },
    "FQA-1.5-095": {
        "severity": "major",
        "primary_failure_type": "domain_specific_requirement_not_covered",
        "secondary_failure_types": "missing_core_requirement",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Country income levels are not correctly covered by the gold API.",
    },
    "FQA-1.5-098": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "nearby_place_or_restaurant_requirement_not_covered",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Cat availability requirement is missing.",
    },
    "FQA-1.5-099": {
        "severity": "critical",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "generic_search_overtrusted;wrong_gold_set_for_service_level",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Baby naming and parenting news query is basically mismatched with the gold services.",
    },
    "FQA-1.5-100": {
        "severity": "major",
        "primary_failure_type": "missing_core_requirement",
        "secondary_failure_types": "nearby_place_or_restaurant_requirement_not_covered;generic_search_overtrusted",
        "qa_error_type": "capability_mismatch",
        "human_reason": "Paris attractions requirement is missing.",
    },
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_md(path: Path, lines: list[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def write_json(path: Path, payload: object) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def table(counter: Counter) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    for key, value in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {value} |")
    return lines


def split_types(primary: str, secondary: str) -> list[str]:
    return [primary] + [item for item in secondary.split(";") if item]


def archive(paths: list[Path]) -> Path:
    archive_dir = Path("outputs/run_archives") / f"{datetime.now().strftime('%Y-%m-%d')}_final_qa_failure_analysis_v1_5c"
    ensure_dir(archive_dir)
    copied: list[str] = []
    for src in paths:
        if not src.exists():
            continue
        dest = archive_dir / src
        ensure_dir(dest.parent)
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        copied.append(str(dest))
    write_md(
        archive_dir / "ARCHIVE_MANIFEST.md",
        [
            "# v1.5c Final QA Failure Analysis Archive",
            "",
            f"Generated time: {now_text()}",
            "",
            "No final clean dataset, split, baseline, model training, or large-scale human review was generated.",
            "",
            "## Archived Files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )
    return archive_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Build v1.5c final QA failure taxonomy and policy tightening artifacts.")
    parser.add_argument("--review-set", type=Path, default=INPUT_REVIEW_SET)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    required = [args.review_set, INPUT_QA_PROTOCOL, INPUT_TASK_TRACE, INPUT_V4_2_RULE, INPUT_SEMCAP_RULE]
    missing = [str(path) for path in required if not path.exists()]
    ensure_dir(args.output_dir)
    if missing:
        write_md(
            args.output_dir / "MISSING_INPUTS.md",
            [
                "# Missing Inputs For v1.5c",
                "",
                f"Generated time: {now_text()}",
                "",
                *[f"- `{path}`" for path in missing],
            ],
        )
        return 1

    rows = {row["qa_item_id"]: row for row in read_csv(args.review_set)}
    patch_rows: list[dict[str, Any]] = []
    missing_ids: list[str] = []
    for qa_id, meta in FAILURE_ITEMS.items():
        row = rows.get(qa_id)
        if row is None:
            missing_ids.append(qa_id)
            continue
        all_types = split_types(meta["primary_failure_type"], meta["secondary_failure_types"])
        patch = {
            "qa_item_id": qa_id,
            "task_id": row.get("task_id", ""),
            "source_group": row.get("source_group", ""),
            "task_type": row.get("task_type", ""),
            "prediction_level": row.get("prediction_level", ""),
            "qa_bucket": row.get("qa_bucket", ""),
            "qa_subbucket": row.get("qa_subbucket", ""),
            "query_text": row.get("query_text", ""),
            "gold_services_json": row.get("gold_services_json", ""),
            "gold_apis_json": row.get("gold_apis_json", ""),
            "candidate_services_json": row.get("candidate_services_json", ""),
            "candidate_apis_json": row.get("candidate_apis_json", ""),
            "dryrun_decision": row.get("dryrun_decision", ""),
            "dryrun_bucket": row.get("dryrun_bucket", ""),
            "v1_semantic_alignment_pred": row.get("v1_semantic_alignment_pred", ""),
            "v1_capability_coverage_pred": row.get("v1_capability_coverage_pred", ""),
            "v1_capability_coverage_confidence": row.get("v1_capability_coverage_confidence", ""),
            "qa_final_decision": "fail",
            "qa_semantic_alignment_check": "mismatch" if meta["severity"] == "critical" else "uncertain",
            "qa_capability_coverage_check": "coverage_mismatch",
            "qa_leakage_check": "no_blocking",
            "qa_candidate_validity_check": "valid",
            "qa_task_type_check": "valid",
            "qa_dedup_check": "unique",
            "qa_error_type": meta["qa_error_type"],
            "qa_severity": meta["severity"],
            "primary_failure_type": meta["primary_failure_type"],
            "secondary_failure_types": meta["secondary_failure_types"],
            "failure_types_all": ";".join(all_types),
            "manual_failure_reason": meta["human_reason"],
            "recommended_policy_action": "tighten_semcap_and_policy;rerun_full_clean_dryrun_v1_4b;qa_clean_candidate_subset_only",
        }
        patch_rows.append(patch)

    severity_counter = Counter(row["qa_severity"] for row in patch_rows)
    primary_counter = Counter(row["primary_failure_type"] for row in patch_rows)
    type_counter = Counter()
    for row in patch_rows:
        for kind in row["failure_types_all"].split(";"):
            type_counter[kind] += 1
    source_counter = Counter(row["source_group"] for row in patch_rows)
    task_type_counter = Counter(row["task_type"] for row in patch_rows)

    patch_path = args.output_dir / "final_qa_clean_candidate_failure_patch_v1_5c.csv"
    patch_fields = [
        "qa_item_id",
        "task_id",
        "source_group",
        "task_type",
        "prediction_level",
        "qa_bucket",
        "qa_subbucket",
        "query_text",
        "gold_services_json",
        "gold_apis_json",
        "candidate_services_json",
        "candidate_apis_json",
        "dryrun_decision",
        "dryrun_bucket",
        "v1_semantic_alignment_pred",
        "v1_capability_coverage_pred",
        "v1_capability_coverage_confidence",
        "qa_final_decision",
        "qa_semantic_alignment_check",
        "qa_capability_coverage_check",
        "qa_leakage_check",
        "qa_candidate_validity_check",
        "qa_task_type_check",
        "qa_dedup_check",
        "qa_error_type",
        "qa_severity",
        "primary_failure_type",
        "secondary_failure_types",
        "failure_types_all",
        "manual_failure_reason",
        "recommended_policy_action",
    ]
    write_csv(patch_path, patch_rows, patch_fields)

    taxonomy_lines = [
        "# Final QA Clean-Candidate Failure Taxonomy v1.5c",
        "",
        f"Generated time: {now_text()}",
        f"Input review set: `{args.review_set}`",
        f"Failure patch CSV: `{patch_path}`",
        f"Analyzed clean-candidate failure count: {len(patch_rows)}",
        "",
        "This analysis uses the v1.5 final QA failure list supplied by the user. It is a release-blocking QA analysis, not a new large-scale annotation round.",
        "",
        "## Executive Conclusion",
        "",
        "- v1.5 final QA does not pass for clean candidates.",
        "- At least 32/100 clean-candidate QA samples have major or critical capability/gold-set issues.",
        "- The observed major+critical clean-candidate error rate is at least 32%, far above the <=5% release threshold.",
        "- v1.6 final clean dataset generation must remain blocked.",
        "",
        "## Severity Distribution",
        "",
        *table(severity_counter),
        "",
        "## Primary Failure Type Distribution",
        "",
        *table(primary_counter),
        "",
        "## All Failure Type Distribution",
        "",
        *table(type_counter),
        "",
        "## Source Group Distribution",
        "",
        *table(source_counter),
        "",
        "## Task Type Distribution",
        "",
        *table(task_type_counter),
        "",
        "## Failure Taxonomy",
        "",
        "### missing_core_requirement",
        "",
        "A core user requirement is not covered by the gold service/API set. This is the dominant failure mode and includes queries requiring recommendations, facts, places, quotes, attractions, or domain-specific outputs that the gold set only partially covers.",
        "",
        "### wrong_extra_gold_service",
        "",
        "The gold service set includes an unrelated or unnecessary service. In service-level discovery this is harmful because a model selecting only relevant services would be penalized.",
        "",
        "### generic_search_overtrusted",
        "",
        "Generic search, autosuggest, image search, or entity lookup is treated as if it fully satisfies a domain-specific requirement. These tools should not automatically cover recommendations, travel planning, baby names, inspirational content, or factual event lookup unless the API description clearly supports the exact requirement.",
        "",
        "### domain_specific_requirement_not_covered",
        "",
        "A domain-specific request is matched to a neighboring but wrong domain capability, such as flight fare versus rail fare, IP geocode versus address geocode, solar image tile versus school image tile, or booking search versus generic bookable-item listing.",
        "",
        "### current_weather_not_covered",
        "",
        "The query asks for current weather or forecast, but the gold API is astronomy, surf/ocean, climate, or another weather-adjacent capability.",
        "",
        "### translation_direction_not_covered",
        "",
        "The query specifies target language or sentence-level translation, but the gold service covers another language direction, dictionary lookup, or partial translation.",
        "",
        "### nearby_place_or_restaurant_requirement_not_covered",
        "",
        "The query asks for nearby places, restaurants, attractions, grocery locations, camping sites, or travel destinations, but the gold set lacks a clear place/recommendation capability.",
        "",
        "### domain_availability_vs_domain_list",
        "",
        "Domain availability checking is not equivalent to listing existing domains. Availability requires a specific check/lookup capability.",
        "",
        "### wrong_gold_set_for_service_level",
        "",
        "The issue is not merely one bad API; the service-level gold set itself is wrong, incomplete, or overcomplete.",
        "",
        "## Patch Items",
        "",
        "| qa_item_id | severity | primary_failure_type | reason |",
        "|---|---|---|---|",
        *[
            f"| {row['qa_item_id']} | {row['qa_severity']} | {row['primary_failure_type']} | {row['manual_failure_reason']} |"
            for row in patch_rows
        ],
    ]
    taxonomy_doc = DOC_DIR / "final_qa_clean_candidate_failure_taxonomy_v1_5c.md"
    write_md(taxonomy_doc, taxonomy_lines)
    shutil.copy2(taxonomy_doc, args.output_dir / taxonomy_doc.name)

    semcap_lines = [
        "# SemCap v1.2 Tightening Rules Candidate",
        "",
        f"Generated time: {now_text()}",
        "",
        "Purpose: prevent SemCap v1.1 from over-crediting partial or generic coverage as high-confidence `coverage_ok`.",
        "",
        "## Rule 1: Every Core Requirement Must Be Covered",
        "",
        "If a query contains multiple core requirements, every requirement must be explicitly covered by the gold service/API set. Partial coverage must produce `coverage_uncertain` or `coverage_mismatch`, never high-confidence `coverage_ok`.",
        "",
        "Examples from v1.5c: weather + hiking trails; restaurant + movie recommendation; news + quote; domain availability + ICD code.",
        "",
        "## Rule 2: Extra Gold Service Penalty",
        "",
        "For service-level tasks, an unrelated extra gold service should block clean-ready status. It should be classified as `wrong_gold_set_for_service_level` and routed to removed or uncertain depending on confidence.",
        "",
        "## Rule 3: Generic Search Is Not A Universal Cover",
        "",
        "Web search, autosuggest, image search, entity search, and similar broad tools cannot automatically satisfy travel recommendations, restaurant recommendations, gift ideas, baby names, motivational content, or factual historical event lookup unless the API description directly supports that use case.",
        "",
        "## Rule 4: Domain-Specific Capability Must Match Domain-Specific Need",
        "",
        "- current weather requires current weather or forecast, not astronomy/surf/climate-adjacent APIs.",
        "- translation requires the requested direction and granularity, not neighboring dictionary or unrelated language support.",
        "- hotel/flight fare requires hotel/flight fare APIs, not rail fare or generic booking list APIs.",
        "- domain availability requires availability checking, not domain list retrieval.",
        "",
        "## Rule 5: High-Confidence Coverage Requires Full Gold-Set Integrity",
        "",
        "`coverage_ok` with high confidence is allowed only when gold is complete, non-extra, semantically aligned, and covers each core requirement. Otherwise downgrade to `coverage_uncertain` or `coverage_mismatch`.",
    ]
    semcap_doc = DOC_DIR / "semcap_v1_2_tightening_rules_candidate.md"
    write_md(semcap_doc, semcap_lines)
    shutil.copy2(semcap_doc, args.output_dir / semcap_doc.name)

    plan_lines = [
        "# Policy Tightening Plan v1.5c",
        "",
        f"Generated time: {now_text()}",
        "",
        "## Why v1.5c Is Needed",
        "",
        "v1.4 passed engineering checks, but v1.5 final QA showed that clean candidates still contain too many semantic/capability failures. The issue is not API leak or join integrity; the issue is over-permissive SemCap coverage and insufficient gold-set integrity checks.",
        "",
        "## Rules To Tighten",
        "",
        "1. Add a core-requirement coverage gate before allowing high-confidence clean.",
        "2. Add service-level extra-gold penalty.",
        "3. Downgrade generic search/autosuggest/entity/image coverage unless exact domain support is explicit.",
        "4. Add domain-specific guards for weather, translation, travel/places, flight/hotel fare, domain availability, and factual/recommendation requirements.",
        "5. Route partial coverage to `uncertain_semcap` or `removed_capability_mismatch`, not `dryrun_clean_candidate`.",
        "",
        "## Implementation Plan",
        "",
        "- Implement SemCap v1.2 tightening in a new script/module; do not overwrite v1.1.",
        "- Rerun full clean dry-run as v1.4b, preserving v1.4 outputs.",
        "- Regenerate only the impacted clean_candidate QA subset; do not reopen large-scale manual review.",
        "- Keep v1.6 final dataset generation blocked until clean_candidate QA passes release thresholds.",
        "",
        "## Non-Goals",
        "",
        "- No final clean dataset generation.",
        "- No split.",
        "- No baseline.",
        "- No model training.",
        "- No new large-scale human annotation round.",
    ]
    plan_doc = DOC_DIR / "policy_tightening_plan_v1_5c.md"
    write_md(plan_doc, plan_lines)
    shutil.copy2(plan_doc, args.output_dir / plan_doc.name)

    go_lines = [
        "# v1.5c Go / No-Go Report",
        "",
        f"Generated time: {now_text()}",
        "",
        "## Decision",
        "",
        "```text",
        "can_accept_final_qa: false",
        "can_generate_final_clean_dataset_now: false",
        "can_generate_service_level_final_clean_dataset_v1_6: false",
        "can_generate_api_level_final_clean_dataset_v1_6: false",
        "can_create_split_now: false",
        "can_run_baseline_now: false",
        "can_train_model_now: false",
        "```",
        "",
        "## Evidence",
        "",
        f"- Clean-candidate QA failures analyzed: {len(patch_rows)}",
        f"- Critical failures: {severity_counter.get('critical', 0)}",
        f"- Major failures: {severity_counter.get('major', 0)}",
        "- Major+critical clean-candidate error rate is at least 32/100 = 32%.",
        "- Release threshold was major+critical error rate <= 5%.",
        "- v1.5 contains only service-level QA samples; API-level final clean readiness remains unverified.",
        "",
        "## Recommended Next Step",
        "",
        "Implement SemCap v1.2 tightening and rerun full clean dry-run as v1.4b, then regenerate final QA only for the impacted clean_candidate subset.",
        "",
        "No final clean dataset, split, baseline, or training should be run now.",
    ]
    go_doc = DOC_DIR / "v1_5c_go_no_go_report.md"
    write_md(go_doc, go_lines)
    shutil.copy2(go_doc, args.output_dir / go_doc.name)

    summary = {
        "generated_time": now_text(),
        "input_review_set": str(args.review_set),
        "failure_count": len(patch_rows),
        "missing_failure_ids": missing_ids,
        "severity_distribution": dict(severity_counter),
        "primary_failure_type_distribution": dict(primary_counter),
        "all_failure_type_distribution": dict(type_counter),
        "can_accept_final_qa": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "recommended_next_step": "implement SemCap v1.2 tightening and rerun full clean dry-run as v1.4b, then regenerate final QA clean_candidate subset only",
    }
    summary_path = args.output_dir / "v1_5c_failure_analysis_summary.json"
    write_json(summary_path, summary)

    archive_dir = archive([
        Path("scripts/validation/build_v1_5c_failure_analysis.py"),
        args.output_dir,
        taxonomy_doc,
        semcap_doc,
        plan_doc,
        go_doc,
    ])

    print(f"Generated: {taxonomy_doc}")
    print(f"Generated: {patch_path}")
    print(f"Generated: {semcap_doc}")
    print(f"Generated: {plan_doc}")
    print(f"Generated: {go_doc}")
    print(f"Generated: {summary_path}")
    print(f"Archive: {archive_dir}")
    print(f"failure_count: {len(patch_rows)}")
    print(f"severity_distribution: {dict(severity_counter)}")
    print("Go/No-Go: NO_GO_TO_V1_6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
