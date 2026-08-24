from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, List

from semcap_v1_1_common import (
    CALIBRATION_FIELDS,
    compact_json,
    ensure_dir,
    now_text,
    read_csv,
    table_lines,
    value_counter,
    write_csv,
    write_md,
)


DEFAULT_OUTPUT_DIR = Path("outputs/semcap_detector_v1_implementation_v1_1")
ROUND3_CALIBRATION = Path("outputs/semantic_capability_detector_pilot_v0_9/semcap_calibration_round3_100.csv")
ROUND3_PREDICTIONS = Path("outputs/semantic_capability_detector_pilot_v0_9/semcap_predictions_round3_heuristic.csv")
SEMCAP80 = Path("outputs/semcap_detector_v1_validation_v1_0/semcap_pilot_review_items_80_user_reviewed.csv")


TAXONOMY = {
    "coverage_ok_but_policy_blocked": {
        "keywords": ["coverage_ok", "no-choice", "service leak", "api leak", "invalid"],
        "root_cause": "SemCap judgment was mixed with final cleaning policy.",
        "v1_fix": "Add coverage_ok_but_policy_blocked as an explanation path, not a final decision.",
        "fix_type": "policy-separation",
    },
    "candidate_no_choice_misread_as_capability_mismatch": {
        "keywords": ["one candidate", "no choice", "choice space", "single candidate"],
        "root_cause": "Candidate-space invalidity was treated as missing capability.",
        "v1_fix": "Keep candidate validity outside semantic/capability coverage.",
        "fix_type": "policy-separation",
    },
    "service_leak_misread_as_capability_mismatch": {
        "keywords": ["service leak", "service name", "explicitly named"],
        "root_cause": "Service leakage was conflated with coverage.",
        "v1_fix": "Route service leak through policy warning/block buckets after SemCap.",
        "fix_type": "policy-separation",
    },
    "strong_api_leak_mixed_into_semcap": {
        "keywords": ["api leak", "endpoint", "exact endpoint", "strong"],
        "root_cause": "Endpoint disclosure is a benchmark-cleanliness issue, not coverage.",
        "v1_fix": "Detect strong API leak separately; do not change SemCap coverage solely because of leakage.",
        "fix_type": "policy-separation",
    },
    "multilingual_news_misread_as_translation_requirement": {
        "keywords": ["news", "french", "german", "spanish", "language"],
        "root_cause": "Language-filtered news search was mistaken for translation.",
        "v1_fix": "Treat news in a language as news search unless translation is explicitly requested.",
        "fix_type": "heuristic",
    },
    "fake_user_query_not_extracted": {
        "keywords": ["fake user", "gender", "random user"],
        "root_cause": "Fake-user domain terms were not extracted as a capability.",
        "v1_fix": "Add fake user and gender-filter capability rules.",
        "fix_type": "heuristic",
    },
    "obvious_news_search_coverage_underestimated": {
        "keywords": ["news", "article", "headline", "currents"],
        "root_cause": "Generic news/search endpoints were under-credited.",
        "v1_fix": "Credit news/search endpoints when query requests news retrieval.",
        "fix_type": "heuristic",
    },
    "obvious_web_image_search_coverage_underestimated": {
        "keywords": ["image", "images", "web search", "imagesearch"],
        "root_cause": "Web/image search capability was underestimated.",
        "v1_fix": "Credit imageSearch/newsSearch/webSearch based on query modality.",
        "fix_type": "heuristic",
    },
    "obvious_trustpilot_endpoint_coverage_underestimated": {
        "keywords": ["trustpilot", "review", "star"],
        "root_cause": "Endpoint-like TrustPilot tasks were treated as unclear coverage.",
        "v1_fix": "Mark matching TrustPilot review/star/detail APIs as coverage_ok while leaving leakage to policy.",
        "fix_type": "heuristic",
    },
    "logistics_company_list_specialization_uncertain": {
        "keywords": ["company", "shipping", "courier", "same-day", "remote", "custom packaging"],
        "root_cause": "Company list APIs may cover generic lists but not specialized attributes.",
        "v1_fix": "Use coverage_uncertain for specialization unless explicitly supported.",
        "fix_type": "heuristic",
    },
    "hotel_or_venue_requirement_missing": {
        "keywords": ["hotel", "venue", "concert", "restaurant", "zoo", "gas station"],
        "root_cause": "Gold APIs did not cover place/venue/event requirements.",
        "v1_fix": "Keep as coverage_mismatch unless venue/place capability is visible.",
        "fix_type": "heuristic",
    },
    "company_images_or_additional_data_missing": {
        "keywords": ["company images", "images", "additional data", "logo"],
        "root_cause": "Company-list APIs were over-read as richer company-data APIs.",
        "v1_fix": "Mark missing images/additional data as mismatch or uncertain depending on evidence.",
        "fix_type": "heuristic",
    },
    "geography_or_carrier_scope_mismatch": {
        "keywords": ["brazil", "argentina", "turkey", "istanbul", "new caledonia", "pridnestrovie"],
        "root_cause": "Country/carrier-specific service scope may not match the query.",
        "v1_fix": "Treat unclear geography/carrier scope as uncertain; clear wrong scope as mismatch.",
        "fix_type": "heuristic",
    },
    "package_tracking_vs_country_specific_tracking": {
        "keywords": ["package", "parcel", "mail", "container"],
        "root_cause": "Package/mail tracking was confused with container tracking or country-specific tracking.",
        "v1_fix": "Separate package/mail, postal, courier, and container tracking scopes.",
        "fix_type": "heuristic",
    },
    "generic_api_words_false_positive": {
        "keywords": ["latest", "all", "count", "list", "search"],
        "root_cause": "Generic API words can be natural query words.",
        "v1_fix": "Do not treat generic API words as strong leak without endpoint or proper-name evidence.",
        "fix_type": "heuristic",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build combined SemCap calibration set v1.1.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current working directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    return parser.parse_args()


def normalize_round3(row: Dict[str, str], pred_by_record: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    record_id = row.get("round3_review_id", "")
    pred = pred_by_record.get(record_id, {})
    return {
        "calibration_source": "round3",
        "record_id": record_id,
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_dataset": row.get("source_dataset", "ToolBench"),
        "source_group": row.get("source_group", ""),
        "query_text": row.get("query_text", ""),
        "candidate_services_json": row.get("candidate_services_json", ""),
        "candidate_apis_json": row.get("candidate_apis_json", ""),
        "gold_services_json": row.get("gold_services_json", ""),
        "gold_apis_json": row.get("gold_apis_json", ""),
        "manual_final_decision": row.get("manual_final_decision", ""),
        "semantic_alignment_check": row.get("semantic_alignment_check", ""),
        "capability_coverage_check": row.get("capability_coverage_check", ""),
        "leakage_check": row.get("leakage_check", ""),
        "candidate_validity_check": row.get("candidate_validity_check", ""),
        "task_type_check": row.get("task_type_check", ""),
        "human_notes": row.get("human_notes", ""),
        "pilot_semantic_alignment_pred": pred.get("semantic_alignment_pred", ""),
        "pilot_semantic_alignment_confidence": pred.get("semantic_alignment_confidence", ""),
        "pilot_semantic_alignment_reason": pred.get("semantic_alignment_reason", ""),
        "pilot_semantic_mismatch_type": pred.get("semantic_mismatch_type", ""),
        "pilot_capability_coverage_pred": pred.get("capability_coverage_pred", ""),
        "pilot_capability_coverage_confidence": pred.get("capability_coverage_confidence", ""),
        "pilot_core_requirements_json": pred.get("core_requirements_json", ""),
        "pilot_covered_requirements_json": pred.get("covered_requirements_json", ""),
        "pilot_missing_requirements_json": pred.get("missing_requirements_json", ""),
        "pilot_capability_mismatch_type": pred.get("capability_mismatch_type", ""),
        "pilot_capability_coverage_reason": pred.get("capability_coverage_reason", ""),
        "review_bucket": "",
        "risk_category": row.get("risk_category", ""),
        "risk_subtype": row.get("risk_subtype", ""),
    }


def normalize_semcap80(row: Dict[str, str]) -> Dict[str, str]:
    return {
        "calibration_source": "semcap80",
        "record_id": row.get("review_item_id", ""),
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_dataset": row.get("source_dataset", "ToolBench"),
        "source_group": row.get("source_group", ""),
        "query_text": row.get("query_text", ""),
        "candidate_services_json": row.get("candidate_services_json", ""),
        "candidate_apis_json": row.get("candidate_apis_json", ""),
        "gold_services_json": row.get("gold_services_json", ""),
        "gold_apis_json": row.get("gold_apis_json", ""),
        "manual_final_decision": row.get("manual_final_decision", ""),
        "semantic_alignment_check": row.get("semantic_alignment_check", ""),
        "capability_coverage_check": row.get("capability_coverage_check", ""),
        "leakage_check": row.get("leakage_check", ""),
        "candidate_validity_check": row.get("candidate_validity_check", ""),
        "task_type_check": row.get("task_type_check", ""),
        "human_notes": row.get("human_notes", ""),
        "pilot_semantic_alignment_pred": row.get("pilot_semantic_alignment_pred", ""),
        "pilot_semantic_alignment_confidence": row.get("pilot_semantic_alignment_confidence", ""),
        "pilot_semantic_alignment_reason": row.get("pilot_semantic_alignment_reason", ""),
        "pilot_semantic_mismatch_type": row.get("pilot_semantic_mismatch_type", ""),
        "pilot_capability_coverage_pred": row.get("pilot_capability_coverage_pred", ""),
        "pilot_capability_coverage_confidence": row.get("pilot_capability_coverage_confidence", ""),
        "pilot_core_requirements_json": row.get("pilot_core_requirements_json", ""),
        "pilot_covered_requirements_json": row.get("pilot_covered_requirements_json", ""),
        "pilot_missing_requirements_json": row.get("pilot_missing_requirements_json", ""),
        "pilot_capability_mismatch_type": row.get("pilot_capability_mismatch_type", ""),
        "pilot_capability_coverage_reason": row.get("pilot_capability_coverage_reason", ""),
        "review_bucket": row.get("review_bucket", ""),
        "risk_category": "",
        "risk_subtype": "",
    }


def make_report(rows: List[Dict[str, str]], output_path: Path, input_paths: List[Path]) -> None:
    coverage_policy_blocked = [
        row
        for row in rows
        if row.get("capability_coverage_check") == "coverage_ok"
        and row.get("manual_final_decision") in {"remove", "uncertain"}
    ]
    lines = [
        "# Combined SemCap Calibration Report v1.1",
        "",
        f"Generated time: {now_text()}",
        "",
        "Input files:",
        *[f"- `{path}`" for path in input_paths],
        "",
        f"Sample count: {len(rows)}",
        "",
        "Scope: calibration construction only. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Source Distribution",
        "",
        *table_lines(value_counter(rows, "calibration_source")),
        "",
        "## Semantic Label Distribution",
        "",
        *table_lines(value_counter(rows, "semantic_alignment_check")),
        "",
        "## Capability Label Distribution",
        "",
        *table_lines(value_counter(rows, "capability_coverage_check")),
        "",
        "## Final Decision Distribution",
        "",
        *table_lines(value_counter(rows, "manual_final_decision")),
        "",
        "## coverage_ok but final remove/uncertain",
        "",
        f"Count: {len(coverage_policy_blocked)}",
        "",
        "This is not a detector error by itself. It shows that SemCap judgment and final cleaning policy must stay separate.",
        "",
        "| record_id | task_id | final | candidate_validity | leakage | task_type | notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in coverage_policy_blocked[:12]:
        lines.append(
            f"| {row.get('record_id')} | {row.get('task_id')} | {row.get('manual_final_decision')} | "
            f"{row.get('candidate_validity_check')} | {row.get('leakage_check')} | {row.get('task_type_check')} | "
            f"{row.get('human_notes', '')[:160]} |"
        )
    for label in ["coverage_mismatch", "coverage_uncertain"]:
        lines.extend(["", f"## {label} examples", "", "| record_id | task_id | final | notes |", "|---|---|---|---|"])
        for row in [item for item in rows if item.get("capability_coverage_check") == label][:12]:
            lines.append(f"| {row.get('record_id')} | {row.get('task_id')} | {row.get('manual_final_decision')} | {row.get('human_notes', '')[:180]} |")
    write_md(output_path, lines)


def make_taxonomy(rows: List[Dict[str, str]], output_path: Path, input_paths: List[Path]) -> None:
    lines = [
        "# SemCap Detector v1 Failure Taxonomy",
        "",
        f"Generated time: {now_text()}",
        "",
        "Input files:",
        *[f"- `{path}`" for path in input_paths],
        "",
        f"Sample count: {len(rows)}",
        "",
        "Scope: taxonomy for detector revision. No full cleaning, split, baseline, or model training was run.",
        "",
    ]
    for name, spec in TAXONOMY.items():
        matches = []
        keywords = [kw.lower() for kw in spec["keywords"]]
        for row in rows:
            blob = " ".join(
                [
                    row.get("query_text", ""),
                    row.get("human_notes", ""),
                    row.get("gold_services_json", ""),
                    row.get("gold_apis_json", ""),
                    row.get("pilot_capability_coverage_reason", ""),
                ]
            ).lower()
            if any(keyword in blob for keyword in keywords):
                matches.append(row)
        lines.extend(
            [
                f"## {name}",
                "",
                f"- count: {len(matches)}",
                f"- root_cause: {spec['root_cause']}",
                f"- v1_fix: {spec['v1_fix']}",
                f"- fix_type: {spec['fix_type']}",
                "",
                "| record_id | task_id | human capability | human final | notes |",
                "|---|---|---|---|---|",
            ]
        )
        for row in matches[:8]:
            lines.append(
                f"| {row.get('record_id')} | {row.get('task_id')} | {row.get('capability_coverage_check')} | "
                f"{row.get('manual_final_decision')} | {row.get('human_notes', '')[:180]} |"
            )
        lines.append("")
    write_md(output_path, lines)


def make_rule_candidate(output_path: Path, input_paths: List[Path], rows: List[Dict[str, str]]) -> None:
    lines = [
        "# Semantic Capability Detector v1 Rule Candidate",
        "",
        f"Generated time: {now_text()}",
        "",
        "Input files:",
        *[f"- `{path}`" for path in input_paths],
        "",
        f"Sample count used for rule design: {len(rows)}",
        "",
        "Scope: rule candidate only. This document does not authorize full cleaning, split, baseline, or model training.",
        "",
        "## Principle 1: SemCap and final cleaning are separate",
        "",
        "SemCap detector only predicts semantic alignment and capability coverage. It must not decide final clean-ready status, removal due to candidate no-choice, removal due to API leak, or removal due to invalid task type.",
        "",
        "Add explanation path: `coverage_ok_but_policy_blocked`.",
        "",
        "Meaning: gold can cover the query, but the item is not clean-ready due to no-choice, leak, or invalid task type.",
        "",
        "## v1 coverage_ok conditions",
        "",
        "Output `coverage_ok` only when core requirements are identifiable, every core requirement is covered by gold service/API names or descriptions, no obvious major requirement is missing, and gold is in the same domain and intent as the query.",
        "",
        "## v1 coverage_uncertain conditions",
        "",
        "Use `coverage_uncertain` when gold partially covers the query, the description is insufficient, specialized filtering may be unsupported, generic search/news/image APIs may cover the need but evidence is weak, or geographic/carrier/domain scope is unclear.",
        "",
        "## v1 coverage_mismatch conditions",
        "",
        "Use `coverage_mismatch` when a core capability is clearly missing, the domain is wrong, gold covers an unrelated entity/geography/carrier, hotel/venue/place search is mapped to postal/logistics/health APIs, company images/additional data are missing, or weather/traffic/translation/ASCII generation requirements are absent.",
        "",
        "## Special v1 fixes",
        "",
        "- Multilingual news: news in French/German/Spanish is news search with language filtering, not translation, unless translation is explicit.",
        "- Fake user: fake user with gender maps to fake-user APIs and gender-filtered APIs.",
        "- TrustPilot: reviews/detail/star/web-link endpoints are `coverage_ok` for exactly matching requests, but final policy may remove due to strong API leak.",
        "- SQUAKE: Projects + Checkhealth are `coverage_ok` for project list and API health, while leak/no-choice remains policy-level.",
        "- Transitaires: customs agency/transitaire list/contact in New Caledonia is `coverage_ok`, while no-choice/service leak blocks clean-ready.",
        "- Currents News: latest/search news APIs cover dated multilingual news requests if language/date filtering is described or reasonably implied.",
        "- Web Search: imageSearch covers image retrieval; newsSearch covers news retrieval; webSearch covers web page search.",
        "- Logistics: company list API covers generic courier/shipping company lists, but same-day, packaging, remote-area, images, or extra data are uncertain unless supported.",
        "- Hotel/venue/place: hotel, venue, restaurant, attraction, gas station, or concert queries are not covered by postal-code, package-tracking, or API-health services.",
        "",
        "## Safety rule",
        "",
        "Prefer conservative `coverage_uncertain` over high-confidence `coverage_ok` when evidence is incomplete. Dangerous false keep must remain zero.",
    ]
    write_md(output_path, lines)


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    round3 = read_csv(root / ROUND3_CALIBRATION)
    round3_pred = read_csv(root / ROUND3_PREDICTIONS)
    semcap80 = read_csv(root / SEMCAP80)
    pred_by_record = {row.get("record_id", ""): row for row in round3_pred}

    combined = [normalize_round3(row, pred_by_record) for row in round3]
    combined.extend(normalize_semcap80(row) for row in semcap80)

    combined_path = output_dir / "combined_semcap_calibration_180.csv"
    write_csv(combined_path, combined, CALIBRATION_FIELDS)

    input_paths = [ROUND3_CALIBRATION, ROUND3_PREDICTIONS, SEMCAP80]
    make_report(combined, root / "docs/phase1/combined_semcap_calibration_report_v1_1.md", input_paths)
    make_taxonomy(combined, root / "docs/phase1/semcap_detector_v1_failure_taxonomy.md", input_paths)
    make_rule_candidate(root / "docs/phase1/semantic_capability_detector_v1_rule_candidate.md", input_paths, combined)

    print(f"Wrote {combined_path}")
    print("combined rows:", len(combined))
    print("source distribution:", dict(Counter(row.get("calibration_source", "") for row in combined)))
    print("capability distribution:", value_counter(combined, "capability_coverage_check"))
    print("No full cleaning, split, baseline, or model training was run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
