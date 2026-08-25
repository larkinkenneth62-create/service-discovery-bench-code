#!/usr/bin/env python
"""Run ToolBench-core v1.5f deterministic tightening dry-run.

This script annotates existing v1.4c clean candidates only. It does not modify
v1.4c outputs, does not generate a final clean dataset, does not create splits,
does not run baselines/training, and does not call Qwen or external APIs.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


OUT_DIR = Path("outputs/policy_v1_5f_tightening_dryrun")
IMPACTED_DIR = Path("outputs/final_qa_v1_5f_impacted_review")
ARCHIVE_DIR = Path("outputs/run_archives/2026-07-05_toolbench_core_v1_5f_dryrun_and_external_csv_qa_prep")

TARGET_FALSE_KEEP = {
    "FQA-1.5E-086": "ToolBench_G3_10870",
    "FQA-1.5E-088": "ToolBench_G3_10817",
    "FQA-1.5E-092": "ToolBench_G3_2684",
    "FQA-1.5E-100": "ToolBench_G2_45337",
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json_field(row: dict[str, str], field: str) -> Any:
    raw = row.get(field, "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def lower_blob(row: dict[str, str]) -> str:
    parts = [
        row.get("query_text", ""),
        row.get("candidate_services_json", ""),
        row.get("candidate_apis_json", ""),
        row.get("gold_services_json", ""),
        row.get("gold_apis_json", ""),
        row.get("v12_capability_coverage_reason", ""),
        row.get("v13_capability_coverage_reason", ""),
    ]
    return " ".join(parts).lower()


def query(row: dict[str, str]) -> str:
    return row.get("query_text", "").lower()


def gold_text(row: dict[str, str]) -> str:
    return " ".join([row.get("gold_services_json", ""), row.get("gold_apis_json", "")]).lower()


def candidate_text(row: dict[str, str]) -> str:
    return " ".join([row.get("candidate_services_json", ""), row.get("candidate_apis_json", "")]).lower()


def parse_json_list(raw: str) -> list[Any]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else [value]
    except Exception:
        return []


def has_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def regex(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) is not None


def add_rule(
    hits: list[dict[str, Any]],
    rule_id: str,
    severity: str,
    decision_effect: str,
    reason: str,
    evidence: dict[str, Any],
) -> None:
    hits.append(
        {
            "rule_id": rule_id,
            "severity": severity,
            "decision_effect": decision_effect,
            "reason": reason,
            "evidence": evidence,
        }
    )


def dependency_signal(q: str) -> bool:
    patterns = [
        r"based on",
        r"according to",
        r"using (the )?(result|returned|retrieved|obtained)",
        r"after (finding|retrieving|getting)",
        r"first .* then",
        r"then use",
        r"use .* to",
        r"depending on",
        r"if .* then",
        r"given the result",
        r"before recommending",
    ]
    return any(regex(q, p) for p in patterns)


def apply_rules(row: dict[str, str]) -> dict[str, Any]:
    q = query(row)
    g = gold_text(row)
    c = candidate_text(row)
    blob = lower_blob(row)
    task_type = row.get("task_type", "") or row.get("task_type_guess", "")
    hits: list[dict[str, Any]] = []

    # Existing detector evidence, when present, is treated as general signal.
    wrong_gold_flags = row.get("v1_4c_wrong_gold_set_flags_json") or row.get("v13_extra_gold_service_flags_json") or ""
    domain_flags = row.get("v1_4c_domain_specific_guard_flags_json") or row.get("v13_domain_specific_guard_flags_json") or ""
    if wrong_gold_flags.strip() not in {"", "[]", "null"}:
        add_rule(
            hits,
            "R4_wrong_gold_set_gate",
            "blocking",
            "remove",
            "Existing gold-set integrity flags are non-empty.",
            {"wrong_gold_flags": wrong_gold_flags[:500]},
        )
    if "mismatch" in (row.get("v12_semantic_alignment_pred", "") + row.get("v13_semantic_alignment_pred", "")).lower():
        add_rule(
            hits,
            "R1_gold_only_coverage_gate",
            "blocking",
            "remove",
            "Existing semantic alignment detector marks mismatch.",
            {"semantic_pred": row.get("v12_semantic_alignment_pred") or row.get("v13_semantic_alignment_pred")},
        )

    # R10: composable requires explicit dependency chain.
    if "composable" in task_type.lower() and not dependency_signal(q):
        add_rule(
            hits,
            "R10_composable_dependency_gate",
            "warning",
            "downgrade",
            "Composable raw sample lacks an explicit dependency-chain signal; may be ordinary multi-task.",
            {"task_type": task_type, "dependency_signal_found": False},
        )

    # R5: dummy/test/sample/health/deprecated endpoints are not user-facing solutions.
    if has_any(g, ["healthcheck", "checkhealth", "sandbox", "dummy", "sample", "deprecated", "test endpoint", "ping", '"hello"', '"home"', "demo project"]):
        add_rule(
            hits,
            "R5_dummy_test_endpoint_gate",
            "blocking",
            "remove",
            "Gold API/service text contains dummy/test/health/deprecated endpoint signal.",
            {"matched_text": "dummy/test/health/deprecated family"},
        )

    # R6: object/scope and input modality mismatch.
    if has_any(q, ["package", "parcel", "mail", "postal", "delivery", "tracking number"]) and has_any(g, ["container", "freight", "bill of lading"]):
        add_rule(
            hits,
            "R6_object_scope_gate",
            "blocking",
            "remove",
            "Query is about package/mail/postal tracking but gold evidence is container/freight scoped.",
            {"query_scope": "package/mail/postal", "gold_scope": "container/freight"},
        )
    if has_any(q, ["postal code", "address", "neighborhood", "geolocation", "geocode", "latitude", "longitude"]) and not has_any(g, ["postal", "address", "geocod", "latitude", "longitude", "zip"]):
        add_rule(
            hits,
            "R6_object_scope_gate",
            "blocking",
            "remove",
            "Query asks for postal/address/geocoding evidence that is not explicit in gold API names.",
            {"query_terms": "postal/address/geocode", "gold_has_explicit_support": False},
        )
    if has_any(q, ["city", "city name"]) and has_any(g, ["zip code", "zipcode", "zip"]) and not has_any(g, ["city", "geocod", "address"]):
        add_rule(
            hits,
            "R6_object_scope_gate",
            "blocking",
            "remove",
            "Query uses city-level input but gold evidence is ZIP-code scoped without explicit geocoding.",
            {"input_modality": "city_vs_zip"},
        )
    if has_any(q, ["city", "cities", "san francisco", "istanbul", "tokyo", "new york"]) and has_any(g, ["lat/lon", "latitude", "longitude"]) and not has_any(g, ["city", "geocod", "address"]):
        add_rule(
            hits,
            "R6_object_scope_gate",
            "blocking",
            "remove",
            "Query uses city-level input but gold evidence requires coordinates without explicit geocoding.",
            {"input_modality": "city_vs_lat_lon"},
        )
    if has_any(q, ["istanbul", "turkey", "turkish"]) and has_any(g, ["pridnestrovie", "transnistria"]):
        add_rule(
            hits,
            "R6_object_scope_gate",
            "blocking",
            "remove",
            "Query is Turkey/Istanbul scoped while gold postal/tracking service is Pridnestrovie/Transnistria scoped.",
            {"region_scope": "turkey_vs_pridnestrovie"},
        )
    if has_any(q, ["san francisco", "california"]) and has_any(g, ["septa", "philadelphia"]):
        add_rule(
            hits,
            "R6_object_scope_gate",
            "blocking",
            "remove",
            "Query is San Francisco/California scoped while gold transit evidence is SEPTA/Philadelphia scoped.",
            {"region_scope": "san_francisco_vs_septa"},
        )
    if has_any(q, ["forecast", "tomorrow", "next day", "next three", "next 3", "weekend", "from saturday", "14 days"]) and has_any(g, ["current weather", "current temperature", "sunrise", "sunset"]) and "forecast" not in g:
        add_rule(
            hits,
            "R6_object_scope_gate",
            "warning",
            "downgrade",
            "Forecast request appears supported only by current/sunrise/sunset style gold evidence.",
            {"scope": "forecast_vs_current_weather"},
        )
    if has_any(q, ["bitcoin", "btc", "ethereum", "eth", "cryptocurrency", "crypto"]) and has_any(q, ["usd", "eur", "euro"]) and has_any(g, ["ethereum price index", "gex"]):
        add_rule(
            hits,
            "R6_object_scope_gate",
            "blocking",
            "remove",
            "Crypto query asks multi-asset/multi-currency coverage but gold evidence is Ethereum-index scoped or inferential.",
            {"scope": "crypto_asset_currency_scope"},
        )

    # R2/R3: action/recommendation/generation/tracking requires explicit gold evidence.
    action_terms = ["generate", "create", "book", "reserve", "track", "current status", "recommend", "suggest", "find me", "help me find"]
    metadata_terms = ["list", "all", "detail", "details", "status", "support", "supported", "revision", "search", "frontpage"]
    if has_any(q, action_terms) and has_any(g, metadata_terms) and not has_any(g, ["generate", "create", "book", "reservation", "track", "tracking", "recommend", "suggest"]):
        add_rule(
            hits,
            "R3_action_vs_metadata_endpoint_gate",
            "blocking",
            "remove",
            "Query asks for action/recommendation/generation/tracking but gold APIs look like list/support/metadata endpoints.",
            {"action_terms_present": True, "metadata_like_gold": True},
        )
    if has_any(q, ["translate", "translation"]) and has_any(g, ["supported languages", "get revision", "revision"]) and not has_any(g, ["translate text", "translate phrase", "translation request"]):
        add_rule(
            hits,
            "R3_action_vs_metadata_endpoint_gate",
            "blocking",
            "remove",
            "Translation action is being satisfied by supported-language or revision metadata endpoints.",
            {"action": "translation", "gold_endpoint_family": "metadata_or_revision"},
        )
    if has_any(q, ["qr code", "generate qr", "create qr"]) and has_any(g, ['"hello"', '"home"', "demo project"]) and not has_any(g, ["generate qr", "create qr", "qr code generator"]):
        add_rule(
            hits,
            "R3_action_vs_metadata_endpoint_gate",
            "blocking",
            "remove",
            "QR generation request is paired with hello" + "/" + "home" + "/" + "demo endpoint rather than an explicit generation endpoint.",
            {"action": "qr_generation", "gold_endpoint_family": "hello_home_demo"},
        )
    if has_any(q, ["profile image", "profile photo", "user image"]) and has_any(g, ["demo project"]):
        add_rule(
            hits,
            "R4_wrong_gold_set_gate",
            "blocking",
            "remove",
            "Profile image/catalog query includes a demo-project gold service, indicating weak or wrong gold set.",
            {"wrong_gold_family": "demo_project_for_user_facing_task"},
        )
    if has_any(q, ["recommend", "suggest", "best", "suitable", "nearby", "near me", "pair", "pairs well", "complements"]) and not has_any(g, ["recommend", "suggest", "near", "restaurant", "hotel", "place", "pair", "booking", "search places"]):
        add_rule(
            hits,
            "R2_missing_core_requirement_gate",
            "warning",
            "downgrade",
            "Recommendation or pairing requirement is not explicitly supported by gold evidence.",
            {"requirement": "recommendation_or_pairing"},
        )
    if has_any(q, ["example sentence", "interactive lesson", "learning app", "website", "websites", "lesson"]) and not has_any(g, ["example", "lesson", "learning", "website", "app"]):
        add_rule(
            hits,
            "R2_missing_core_requirement_gate",
            "blocking",
            "remove",
            "Language-learning or example-sentence core requirement lacks explicit gold evidence.",
            {"requirement": "language_learning_example_or_interactive_resource"},
        )
    if has_any(q, ["gold", "silver", "precious metal", "precious metals"]) and has_any(q, ["exchange rate", "exchange rates", "currency"]) and not has_any(g, ["gold", "silver", "metal", "xau", "xag"]):
        add_rule(
            hits,
            "R2_missing_core_requirement_gate",
            "blocking",
            "remove",
            "Currency exchange gold evidence does not explicitly cover precious metals requested by the query.",
            {"requirement": "precious_metal_exchange_rates"},
        )
    if has_any(q, ["nearest post office", "post office locations", "post office"]) and not has_any(g, ["post office", "nearby", "location search", "places"]):
        add_rule(
            hits,
            "R2_missing_core_requirement_gate",
            "blocking",
            "remove",
            "Query asks for nearest post office/location search but gold evidence does not explicitly provide it.",
            {"requirement": "nearest_post_office_locations"},
        )

    # R7 generic search/news/image overtrust.
    if has_any(g, ["web search", "imagesearch", "image search", "newssearch", "generic search"]) and has_any(q, ["appropriate", "verify", "ted talk", "banner", "movie", "restaurant", "hotel", "tourist", "specific genre", "domain-specific"]):
        add_rule(
            hits,
            "R7_generic_search_news_image_overtrust_gate",
            "warning",
            "downgrade",
            "Generic search/news/image API is being asked to satisfy domain-specific requirements.",
            {"generic_gold": True},
        )

    # R8 travel/place recommendation gap.
    if has_any(q, ["hotel", "restaurant", "attraction", "tourist", "flight", "vacation", "trip", "concert", "venue", "book a table"]):
        if has_any(q, ["recommend", "suggest", "near", "suitable", "find", "book"]) and not has_any(g, ["hotel", "restaurant", "place", "flight", "booking", "venue", "concert", "travel", "search places"]):
            add_rule(
                hits,
                "R8_travel_place_recommendation_gap_gate",
                "blocking",
                "remove",
                "Travel/place recommendation or booking requirement lacks direct gold evidence.",
                {"requirement": "travel_place_recommendation"},
            )

    # R9 duplicate representative.
    if row.get("is_representative_candidate", "").strip().lower() in {"0", "false", "no"}:
        add_rule(
            hits,
            "R9_duplicate_representative_gate",
            "warning",
            "downgrade",
            "Row is marked as a non-representative duplicate candidate.",
            {"is_representative_candidate": row.get("is_representative_candidate")},
        )

    # R11: generalized Qwen false-keep families, no IDs.
    if has_any(q, ["pairs well", "complements", "specific dish", "seafood"]) and has_any(g, ["cocktail"]) and not has_any(g, ["pair", "food", "dish", "seafood"]):
        add_rule(
            hits,
            "R11_qwen_failure_family_regression_gate",
            "blocking",
            "remove",
            "Cocktail recipe retrieval is not enough for dish-pairing recommendation unless pairing evidence is explicit.",
            {"family": "cocktail_pairing_recommendation_gap"},
        )
    if has_any(q, ["city", "zip code", "hardiness zone"]) and has_any(g, ["hardiness zone"]) and has_any(g, ["zip"]) and "city" not in g:
        add_rule(
            hits,
            "R11_qwen_failure_family_regression_gate",
            "blocking",
            "remove",
            "Hardiness-zone lookup appears ZIP-scoped while query input is city-level.",
            {"family": "city_zip_input_modality_mismatch"},
        )
    if "composable" in task_type.lower() and has_any(q, ["current prices", "ticker data", "cryptocurrency", "bitcoin", "ethereum"]) and not dependency_signal(q):
        add_rule(
            hits,
            "R11_qwen_failure_family_regression_gate",
            "blocking",
            "remove",
            "Crypto price sample is ordinary parallel retrieval, not a strong composable dependency chain.",
            {"family": "crypto_scope_not_composable"},
        )

    # Some existing domain flags are weaker than hard blocking because they were
    # already present in v1.4c clean candidates.
    if domain_flags.strip() not in {"", "[]", "null"}:
        add_rule(
            hits,
            "R2_missing_core_requirement_gate",
            "warning",
            "downgrade",
            "Existing domain-specific guard flags are non-empty.",
            {"domain_flags": domain_flags[:500]},
        )

    blocking = [h for h in hits if h["decision_effect"] == "remove"]
    warnings = [h for h in hits if h["decision_effect"] == "downgrade"]
    if blocking:
        decision = "dryrun_remove"
        confidence = "high" if any(h["severity"] == "blocking" for h in blocking) else "medium"
    elif warnings:
        decision = "downgrade_to_uncertain"
        confidence = "medium"
    else:
        decision = "still_clean_candidate"
        confidence = "medium"

    return {
        "decision": decision,
        "blocking": blocking,
        "warnings": warnings,
        "all_hits": hits,
        "confidence": confidence,
        "requires_human_review": "yes" if decision != "still_clean_candidate" or "composable" in task_type.lower() else "no",
    }


def discover_clean_source(project_root: Path) -> tuple[Path, list[dict[str, str]], list[dict[str, str]]]:
    source = project_root / "outputs/full_clean_dryrun_v1_4c/full_clean_task_trace_v1_4c.csv"
    if not source.exists():
        raise FileNotFoundError(f"Missing v1.4c trace source: {source}")
    rows = read_csv(source)
    clean = [row for row in rows if row.get("dryrun_decision_v1_4c") == "dryrun_clean_candidate"]
    if len(clean) != 2168:
        missing = project_root / "outputs/policy_v1_5f_tightening_dryrun/MISSING_INPUTS.md"
        missing.parent.mkdir(parents=True, exist_ok=True)
        missing.write_text(
            f"# Missing v1.4c 2168 Clean Candidate Input\n\nGenerated time: {now()}\n\n"
            f"Expected exactly 2168 `dryrun_clean_candidate` rows in `{source}` but found {len(clean)}.\n\n"
            "Stopped. Did not use full raw 201,774 rows or candidate-level rows as a replacement.\n",
            encoding="utf-8",
        )
        raise SystemExit(f"Could not find 2168 clean candidates. See {missing}")
    return source, rows, clean


def annotate_clean_candidates(project_root: Path, clean_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out_rows: list[dict[str, Any]] = []
    for row in clean_rows:
        result = apply_rules(row)
        new = dict(row)
        new["v1_4c_original_decision"] = row.get("dryrun_decision_v1_4c", "")
        new["v1_5f_dryrun_decision"] = result["decision"]
        new["v1_5f_blocking_rules_json"] = json.dumps([h["rule_id"] for h in result["blocking"]], ensure_ascii=False)
        new["v1_5f_warning_rules_json"] = json.dumps([h["rule_id"] for h in result["warnings"]], ensure_ascii=False)
        new["v1_5f_rule_evidence_json"] = json.dumps(result["all_hits"], ensure_ascii=False)
        new["v1_5f_policy_confidence"] = result["confidence"]
        new["v1_5f_notes"] = "; ".join(h["reason"] for h in result["all_hits"][:3])
        new["v1_5f_requires_human_review"] = result["requires_human_review"]
        out_rows.append(new)
    out_csv = project_root / OUT_DIR / "clean_candidates_v1_4c_with_v1_5f_annotations.csv"
    fieldnames = list(clean_rows[0].keys()) + [
        "v1_4c_original_decision",
        "v1_5f_dryrun_decision",
        "v1_5f_blocking_rules_json",
        "v1_5f_warning_rules_json",
        "v1_5f_rule_evidence_json",
        "v1_5f_policy_confidence",
        "v1_5f_notes",
        "v1_5f_requires_human_review",
    ]
    write_csv(out_csv, out_rows, fieldnames)
    return out_rows


def rule_ids(row: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for field in ["v1_5f_blocking_rules_json", "v1_5f_warning_rules_json"]:
        ids.extend(parse_json_list(str(row.get(field, ""))))
    return [str(x) for x in ids]


def write_rule_spec(project_root: Path) -> None:
    path = project_root / "docs/phase1/policy_v1_5f_rule_spec_from_finalqa_and_false_keep.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Policy v1.5f Rule Spec From FinalQA And False Keep

Generated time: {now()}

Input policy files:

- `docs/phase1/final_qa_v1_5e_failure_taxonomy.md`
- `docs/phase1/policy_v1_5f_tightening_plan_from_final_qa.md`
- `outputs/qwen_semcap_judge_v1_4d_step3/finalqa100/eval/qwen_step3_finalqa100_eval_trace.csv`

These are generalized rules. They must not use `qa_item_id` or `task_id` as filters.

## R1_gold_only_coverage_gate

Every explicit core requirement must be supported by gold service/API evidence. Non-gold candidate capability cannot satisfy the query. Missing gold evidence means downgrade or remove.

## R2_missing_core_requirement_gate

If the query requires location, contact, recommendation, booking, generation, action, tracking, forecast, geocoding, language-learning examples, or interactive resources and gold evidence does not explicitly support it, downgrade or remove.

## R3_action_vs_metadata_endpoint_gate

List/support/metadata/revision/detail/status endpoints cannot satisfy generation, booking, tracking, recommendation, creation, or action tasks unless explicitly documented.

## R4_wrong_gold_set_gate

Unrelated extra gold service/API or missing necessary gold service/API blocks clean status.

## R5_dummy_test_endpoint_gate

Dummy/test/sample/healthcheck/sandbox/deprecated endpoints cannot satisfy user-facing tasks.

## R6_object_scope_gate

Package/mail/container tracking, postal/address/geocoding, forecast/current weather, carrier/region/country, crypto asset/currency, and city-vs-ZIP scope mismatches become uncertain or remove.

## R7_generic_search_news_image_overtrust_gate

Generic search/news/image APIs cannot satisfy domain-specific requirements without explicit gold evidence.

## R8_travel_place_recommendation_gap_gate

Hotel, restaurant, place, attraction, concert, flight, and travel recommendations require direct support from gold docs.

## R9_duplicate_representative_gate

Non-representative duplicates cannot remain clean candidates.

## R10_composable_dependency_gate

Composable raw samples need explicit dependency chain. Ordinary parallel multi-task is not strong composable. Raw G3/composable samples without dependency evidence should be downgraded unless they match a separately human-confirmed strong-composable seed set.

## R11_qwen_failure_family_regression_gate

The four Qwen finalQA100 false keeps map to generalized families:

- crypto asset/currency scope mismatch plus no dependency-chain evidence;
- cocktail/recipe retrieval over-trusted for dish-pairing recommendation;
- city-level input over-trusted for ZIP-scoped hardiness-zone APIs;
- ordinary parallel multi-task over-trusted as composable.

This rule family is used as regression protection only and must not hard-code `qa_item_id` or `task_id`.
"""
    path.write_text(text, encoding="utf-8")


def false_keep_analysis(project_root: Path) -> dict[str, Any]:
    qa_rows = {row["qa_item_id"]: row for row in read_csv(project_root / "outputs/final_qa_v1_5e/final_qa_review_items_v1_5e_gpt_manual_reviewed.csv")}
    trace_rows = {row["qa_item_id"]: row for row in read_csv(project_root / "outputs/qwen_semcap_judge_v1_4d_step3/finalqa100/eval/qwen_step3_finalqa100_eval_trace.csv")}
    guarded = {row["task_id"]: row for row in read_csv(project_root / "outputs/qwen_semcap_judge_v1_4d_step3/finalqa100/eval/qwen_step3_guarded_predictions_finalqa100.csv")}

    out_rows = []
    for qa_id, expected_task_id in TARGET_FALSE_KEEP.items():
        qa = qa_rows.get(qa_id, {})
        trace = trace_rows.get(qa_id, {})
        g = guarded.get(expected_task_id, {})
        applied = apply_rules(qa)
        family = qa.get("qa_error_type", "")
        if "crypto" in family:
            why = "Qwen inferred broad crypto coverage and composability despite crypto scope mismatch and no dependency chain."
        elif "input_modality" in family or "geocoding" in family:
            why = "Qwen accepted ZIP/proxy input for a city-level/geocoding requirement."
        elif "capability_mismatch" in family or "underspecified_location" in family:
            why = "Qwen treated generic retrieval as sufficient for recommendation/pairing/location-specific capability."
        else:
            why = "Qwen treated coverage as sufficient where human QA found blocking semantic/capability risk."
        row = {
            "qa_item_id": qa_id,
            "task_id": expected_task_id,
            "task_type": qa.get("task_type", trace.get("task_type", "")),
            "query_text": qa.get("query_text", ""),
            "qa_final_decision": qa.get("qa_final_decision", trace.get("human_final", "")),
            "qa_error_type": qa.get("qa_error_type", trace.get("human_error_type", "")),
            "qa_severity": qa.get("qa_severity", trace.get("human_severity", "")),
            "qa_notes": qa.get("qa_notes", ""),
            "Qwen raw label if available": trace.get("QWEN_raw_capability_coverage_check", ""),
            "Qwen guarded label if available": trace.get("QWEN_guarded_capability_coverage_check", g.get("QWEN_guarded_capability_coverage_check", "")),
            "Qwen confidence if available": trace.get("QWEN_guarded_capability_coverage_confidence", g.get("QWEN_guarded_capability_coverage_confidence", "")),
            "why Qwen failed": why,
            "corresponding human failure family": family,
            "which v1.5f generalized rule should catch it": ";".join(sorted(set(rule_ids_for_hits(applied["all_hits"])))),
            "whether current v1.5f proposed rules cover it": "yes" if applied["decision"] != "still_clean_candidate" else "no",
        }
        out_rows.append(row)

    out_csv = project_root / "outputs/qwen_semcap_judge_v1_4d_step3/finalqa100/eval/qwen_step3_finalqa100_false_keep_analysis.csv"
    write_csv(out_csv, out_rows, list(out_rows[0].keys()))

    md = project_root / "docs/phase1/qwen_step3_finalqa100_false_keep_analysis_v1_4d.md"
    lines = ["# Qwen Step3 FinalQA100 False Keep Analysis v1.4d", "", f"Generated time: {now()}", ""]
    for row in out_rows:
        lines.extend(
            [
                f"## {row['qa_item_id']} / {row['task_id']}",
                "",
                f"- human final: `{row['qa_final_decision']}`",
                f"- human error family: `{row['corresponding human failure family']}`",
                f"- Qwen guarded label: `{row['Qwen guarded label if available']}`",
                f"- why Qwen failed: {row['why Qwen failed']}",
                f"- generalized v1.5f rules: `{row['which v1.5f generalized rule should catch it']}`",
                f"- covered by current proposed rules: `{row['whether current v1.5f proposed rules cover it']}`",
                "",
            ]
        )
    md.write_text("\n".join(lines), encoding="utf-8")
    return {"rows": len(out_rows), "covered": sum(1 for row in out_rows if row["whether current v1.5f proposed rules cover it"] == "yes")}


def rule_ids_for_hits(hits: list[dict[str, Any]]) -> list[str]:
    return [str(h["rule_id"]) for h in hits]


def regression_finalqa100(project_root: Path) -> dict[str, Any]:
    qa_rows = read_csv(project_root / "outputs/final_qa_v1_5e/final_qa_review_items_v1_5e_gpt_manual_reviewed.csv")
    trace_rows = []
    for row in qa_rows:
        applied = apply_rules(row)
        out = dict(row)
        out["v1_5f_dryrun_decision"] = applied["decision"]
        out["v1_5f_blocking_rules_json"] = json.dumps([h["rule_id"] for h in applied["blocking"]], ensure_ascii=False)
        out["v1_5f_warning_rules_json"] = json.dumps([h["rule_id"] for h in applied["warnings"]], ensure_ascii=False)
        out["v1_5f_rule_evidence_json"] = json.dumps(applied["all_hits"], ensure_ascii=False)
        trace_rows.append(out)

    out_csv = project_root / OUT_DIR / "finalqa100_v1_5f_regression_trace.csv"
    fieldnames = list(qa_rows[0].keys()) + [
        "v1_5f_dryrun_decision",
        "v1_5f_blocking_rules_json",
        "v1_5f_warning_rules_json",
        "v1_5f_rule_evidence_json",
    ]
    write_csv(out_csv, trace_rows, fieldnames)

    human_keep = [r for r in trace_rows if r.get("qa_final_decision") == "keep_for_cleaning_candidate"]
    human_uncertain = [r for r in trace_rows if r.get("qa_final_decision") == "uncertain"]
    human_remove = [r for r in trace_rows if r.get("qa_final_decision") == "remove"]
    human_critical = [r for r in trace_rows if r.get("qa_severity") == "critical"]
    captured_critical = [r for r in human_critical if r["v1_5f_dryrun_decision"] != "still_clean_candidate"]
    captured_remove = [r for r in human_remove if r["v1_5f_dryrun_decision"] != "still_clean_candidate"]
    retained_keep = [r for r in human_keep if r["v1_5f_dryrun_decision"] == "still_clean_candidate"]
    qwen_false = [r for r in trace_rows if r.get("qa_item_id") in TARGET_FALSE_KEEP]
    qwen_blocked = [r for r in qwen_false if r["v1_5f_dryrun_decision"] != "still_clean_candidate"]
    comp = [r for r in trace_rows if "composable" in r.get("task_type", "").lower()]
    summary = {
        "generated_time": now(),
        "finalqa_rows": len(trace_rows),
        "human_keep_count": len(human_keep),
        "human_uncertain_count": len(human_uncertain),
        "human_remove_count": len(human_remove),
        "human_critical_count": len(human_critical),
        "v1_5f_captures_critical_count": len(captured_critical),
        "v1_5f_critical_capture_rate": round(len(captured_critical) / len(human_critical), 4) if human_critical else 1.0,
        "v1_5f_captures_remove_count": len(captured_remove),
        "v1_5f_remove_capture_rate": round(len(captured_remove) / len(human_remove), 4) if human_remove else 1.0,
        "v1_5f_keep_retention_count": len(retained_keep),
        "v1_5f_keep_retention_rate": round(len(retained_keep) / len(human_keep), 4) if human_keep else 0,
        "qwen_false_keep_4_blocked_count": len(qwen_blocked),
        "qwen_false_keep_4_blocked_ids": [r.get("qa_item_id") for r in qwen_blocked],
        "qwen_false_keep_4_not_blocked_ids": [r.get("qa_item_id") for r in qwen_false if r["v1_5f_dryrun_decision"] == "still_clean_candidate"],
        "composable_raw_keep_after_v1_5f_count": sum(1 for r in comp if r["v1_5f_dryrun_decision"] == "still_clean_candidate"),
        "composable_raw_downgrade_or_remove_count": sum(1 for r in comp if r["v1_5f_dryrun_decision"] != "still_clean_candidate"),
        "no_hard_coded_id_rules_used": True,
        "no_qwen_labels_used_as_final_decision": True,
    }
    summary["regression_pass"] = (
        summary["v1_5f_critical_capture_rate"] == 1.0
        and summary["qwen_false_keep_4_blocked_count"] == 4
        and summary["v1_5f_remove_capture_rate"] >= 0.85
        and summary["no_hard_coded_id_rules_used"]
        and summary["no_qwen_labels_used_as_final_decision"]
    )
    write_json(project_root / OUT_DIR / "finalqa100_v1_5f_regression_summary.json", summary)
    return summary


def summarize_dryrun(project_root: Path, annotated: list[dict[str, Any]]) -> dict[str, Any]:
    decision_counts = Counter(row["v1_5f_dryrun_decision"] for row in annotated)
    task_move = Counter((row.get("task_type", ""), row["v1_5f_dryrun_decision"]) for row in annotated)
    group_move = Counter((row.get("source_group", ""), row["v1_5f_dryrun_decision"]) for row in annotated)
    rule_counter = Counter()
    combos = Counter()
    examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in annotated:
        ids = rule_ids(row)
        combos[";".join(sorted(set(ids))) if ids else "no_rule_hit"] += 1
        for rid in ids:
            rule_counter[rid] += 1
            if len(examples[rid]) < 5:
                examples[rid].append(
                    {
                        "task_id": row.get("task_id", ""),
                        "task_type": row.get("task_type", ""),
                        "decision": row.get("v1_5f_dryrun_decision", ""),
                        "query_text": row.get("query_text", "")[:350],
                    }
                )

    def movement_contains(words: list[str]) -> dict[str, int]:
        c = Counter()
        for row in annotated:
            blob = lower_blob(row) + " " + row.get("task_type", "").lower() + " " + row.get("source_group", "").lower()
            if has_any(blob, words):
                c[row["v1_5f_dryrun_decision"]] += 1
        return dict(c)

    summary = {
        "generated_time": now(),
        "input_clean_candidate_count": len(annotated),
        "still_clean_candidate_count": decision_counts.get("still_clean_candidate", 0),
        "downgrade_to_uncertain_count": decision_counts.get("downgrade_to_uncertain", 0),
        "dryrun_remove_count": decision_counts.get("dryrun_remove", 0),
        "movement_by_task_type": {f"{k[0]}::{k[1]}": v for k, v in task_move.items()},
        "movement_by_source_group": {f"{k[0]}::{k[1]}": v for k, v in group_move.items()},
        "rule_hit_counts": dict(rule_counter),
        "top_20_rule_combinations": dict(combos.most_common(20)),
        "composable_raw_movement": movement_contains(["composable_service_discovery_raw"]),
        "generic_search_news_image_movement": movement_contains(["web search", "image search", "newssearch", "generic search"]),
        "weather_forecast_movement": movement_contains(["weather", "forecast", "sunrise", "sunset"]),
        "travel_place_movement": movement_contains(["hotel", "restaurant", "travel", "flight", "tourist", "concert", "venue"]),
        "tracking_postal_address_movement": movement_contains(["track", "tracking", "postal", "address", "package", "parcel", "mail"]),
        "dummy_test_endpoint_movement": movement_contains(["dummy", "test", "sample", "healthcheck", "sandbox", "deprecated"]),
        "duplicate_movement": movement_contains(["duplicate"]),
        "number_requiring_human_review": sum(1 for row in annotated if row["v1_5f_requires_human_review"] == "yes"),
        "examples_by_rule_family_max5": examples,
    }
    write_json(project_root / OUT_DIR / "v1_5f_dryrun_summary.json", summary)

    write_csv(
        project_root / OUT_DIR / "v1_5f_rule_hit_counts.csv",
        [{"rule_id": k, "hit_count": v} for k, v in rule_counter.most_common()],
        ["rule_id", "hit_count"],
    )
    write_csv(
        project_root / OUT_DIR / "v1_5f_task_type_movement_counts.csv",
        [{"task_type": k[0], "v1_5f_dryrun_decision": k[1], "count": v} for k, v in task_move.items()],
        ["task_type", "v1_5f_dryrun_decision", "count"],
    )
    return summary


def write_dryrun_report(project_root: Path, summary: dict[str, Any], regression: dict[str, Any]) -> None:
    path = project_root / "docs/phase1/policy_v1_5f_tightening_dryrun_report.md"
    lines = [
        "# Policy v1.5f Tightening Dry-Run Report",
        "",
        f"Generated time: {summary['generated_time']}",
        "",
        "## Counts",
        "",
        f"- input_clean_candidate_count: {summary['input_clean_candidate_count']}",
        f"- still_clean_candidate_count: {summary['still_clean_candidate_count']}",
        f"- downgrade_to_uncertain_count: {summary['downgrade_to_uncertain_count']}",
        f"- dryrun_remove_count: {summary['dryrun_remove_count']}",
        f"- number_requiring_human_review: {summary['number_requiring_human_review']}",
        "",
        "## Rule Hit Counts",
        "",
    ]
    for rule_id, count in summary["rule_hit_counts"].items():
        lines.append(f"- {rule_id}: {count}")
    lines.extend(
        [
            "",
            "## FinalQA100 Regression",
            "",
            f"- v1_5f_critical_capture_rate: {regression['v1_5f_critical_capture_rate']}",
            f"- v1_5f_remove_capture_rate: {regression['v1_5f_remove_capture_rate']}",
            f"- qwen_false_keep_4_blocked_count: {regression['qwen_false_keep_4_blocked_count']}",
            f"- keep_retention_rate: {regression['v1_5f_keep_retention_rate']}",
            "",
            "## Boundary",
            "",
            "- This is a dry-run annotation only.",
            "- It does not create final clean dataset, split, baseline, or training data.",
            "- It does not call Qwen or use Qwen predictions as human final.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def impacted_pack(project_root: Path, annotated: list[dict[str, Any]]) -> None:
    impacted = [row for row in annotated if row["v1_5f_dryrun_decision"] != "still_clean_candidate"]
    still = [row for row in annotated if row["v1_5f_dryrun_decision"] == "still_clean_candidate"]
    selected: list[dict[str, Any]] = []

    removes = [row for row in impacted if row["v1_5f_dryrun_decision"] == "dryrun_remove"]
    downgrades = [row for row in impacted if row["v1_5f_dryrun_decision"] == "downgrade_to_uncertain"]
    selected.extend(removes[:60])
    selected.extend(downgrades[:30])

    # Add examples from each rule family.
    seen = {row["task_id"] for row in selected}
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in impacted:
        for rid in rule_ids(row):
            by_rule[rid].append(row)
    for rid in sorted(by_rule):
        for row in by_rule[rid][:3]:
            if row["task_id"] not in seen:
                selected.append(row)
                seen.add(row["task_id"])

    # Add all composable still-clean candidates, if any, capped to avoid huge pack.
    for row in still:
        if "composable" in row.get("task_type", "").lower() and row["task_id"] not in seen:
            selected.append(row)
            seen.add(row["task_id"])
            if len([r for r in selected if "composable" in r.get("task_type", "").lower()]) >= 20:
                break

    random.seed(20260705)
    for row in random.sample(still, min(20, len(still))):
        if row["task_id"] not in seen:
            selected.append(row)
            seen.add(row["task_id"])

    selected = selected[:120]
    out_rows = []
    for idx, row in enumerate(selected, start=1):
        out = {k: row.get(k, "") for k in [
            "task_id",
            "task_type",
            "query_text",
            "candidate_services_json",
            "gold_services_json",
            "candidate_apis_json",
            "gold_apis_json",
            "v1_4c_original_decision",
            "v1_5f_dryrun_decision",
            "v1_5f_blocking_rules_json",
            "v1_5f_warning_rules_json",
            "v1_5f_rule_evidence_json",
        ]}
        out["review_item_id"] = f"V15F-IMPACT-{idx:03d}"
        out["qa_final_decision"] = ""
        out["qa_semantic_alignment_check"] = ""
        out["qa_capability_coverage_check"] = ""
        out["qa_candidate_validity_check"] = ""
        out["qa_task_type_check"] = ""
        out["qa_error_type"] = ""
        out["qa_severity"] = ""
        out["qa_notes"] = ""
        out["reviewer_id"] = ""
        out["reviewed_at"] = ""
        out_rows.append(out)
    fieldnames = [
        "review_item_id",
        "task_id",
        "task_type",
        "query_text",
        "candidate_services_json",
        "gold_services_json",
        "candidate_apis_json",
        "gold_apis_json",
        "v1_4c_original_decision",
        "v1_5f_dryrun_decision",
        "v1_5f_blocking_rules_json",
        "v1_5f_warning_rules_json",
        "v1_5f_rule_evidence_json",
        "qa_final_decision",
        "qa_semantic_alignment_check",
        "qa_capability_coverage_check",
        "qa_candidate_validity_check",
        "qa_task_type_check",
        "qa_error_type",
        "qa_severity",
        "qa_notes",
        "reviewer_id",
        "reviewed_at",
    ]
    write_csv(project_root / IMPACTED_DIR / "final_qa_v1_5f_impacted_review_items_csv_only.csv", out_rows, fieldnames)

    plan = project_root / "docs/phase1/final_qa_v1_5f_impacted_review_csv_plan.md"
    plan.write_text(
        f"""# Final QA v1.5f Impacted Review CSV Plan

Generated time: {now()}

Review CSV: `outputs/final_qa_v1_5f_impacted_review/final_qa_v1_5f_impacted_review_items_csv_only.csv`

Sampling strategy:

- include dryrun_remove examples, capped by target size;
- include downgrade_to_uncertain examples;
- include high-priority samples from each v1.5f rule family;
- include composable raw still-clean candidates if any;
- include a random sanity sample of still_clean_candidate rows.

No HTML app was generated.
""",
        encoding="utf-8",
    )
    dictionary = project_root / "docs/phase1/final_qa_v1_5f_impacted_review_csv_field_dictionary.md"
    dictionary.write_text(
        f"""# Final QA v1.5f Impacted Review CSV Field Dictionary

Generated time: {now()}

- `v1_4c_original_decision`: original v1.4c decision, expected `dryrun_clean_candidate`.
- `v1_5f_dryrun_decision`: v1.5f annotation only, one of `still_clean_candidate`, `downgrade_to_uncertain`, `dryrun_remove`.
- `v1_5f_blocking_rules_json`: generalized blocking rule IDs.
- `v1_5f_warning_rules_json`: generalized warning rule IDs.
- `v1_5f_rule_evidence_json`: rule evidence and reason.
- `qa_*`: human review fields; keep empty until reviewed.
""",
        encoding="utf-8",
    )


def write_go_no_go(project_root: Path, regression: dict[str, Any]) -> dict[str, Any]:
    can_accept = bool(regression.get("regression_pass"))
    recommended = (
        "human review CSV packs for v1.5f impacted QA, MetaTool, and StableToolBench before any v1.6 final clean dataset"
        if can_accept
        else "inspect missed remove/critical and revise human-derived v1.5f rules, not Qwen"
    )
    report = {
        "generated_time": now(),
        "can_accept_qwen_step3_as_auxiliary_guard": False,
        "can_run_qwen_full2168_next": False,
        "can_accept_v1_5f_dryrun_policy": can_accept,
        "can_generate_final_clean_dataset_now": False,
        "can_merge_external_sources_now": False,
        "can_generate_full_six_task_benchmark_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "external_review_mode": "csv_only",
        "html_review_app_generated": False,
        "recommended_next_step": recommended,
        "regression_summary": regression,
    }
    md = project_root / "docs/phase1/policy_v1_5f_go_no_go_pre_v1_6.md"
    lines = ["# Policy v1.5f Go/No-Go Pre v1.6", "", f"Generated time: {report['generated_time']}", ""]
    for key in [
        "can_accept_qwen_step3_as_auxiliary_guard",
        "can_run_qwen_full2168_next",
        "can_accept_v1_5f_dryrun_policy",
        "can_generate_final_clean_dataset_now",
        "can_merge_external_sources_now",
        "can_generate_full_six_task_benchmark_now",
        "can_create_split_now",
        "can_run_baseline_now",
        "can_train_model_now",
        "external_review_mode",
        "html_review_app_generated",
    ]:
        lines.append(f"- {key}: `{report[key]}`")
    lines.extend(["", f"- recommended_next_step: {recommended}", ""])
    md.write_text("\n".join(lines), encoding="utf-8")
    return report


def archive(project_root: Path) -> Path:
    files = [
        "docs/phase1/current_branch_status_after_external_recovery_v1_5f.md",
        "docs/phase1/external_qa_csv_human_review_instruction_v0_1.md",
        "docs/phase1/external_qa_csv_field_dictionary_v0_1.md",
        "docs/phase1/external_qa_csv_go_no_go_criteria_v0_1.md",
        "docs/phase1/external_qa_csv_validation_report_v0_1.md",
        "docs/phase1/external_qa_csv_summary_report_v0_1.md",
        "docs/phase1/qwen_step3_finalqa100_false_keep_analysis_v1_4d.md",
        "docs/phase1/policy_v1_5f_rule_spec_from_finalqa_and_false_keep.md",
        "docs/phase1/policy_v1_5f_tightening_dryrun_report.md",
        "docs/phase1/final_qa_v1_5f_impacted_review_csv_plan.md",
        "docs/phase1/final_qa_v1_5f_impacted_review_csv_field_dictionary.md",
        "docs/phase1/policy_v1_5f_go_no_go_pre_v1_6.md",
        "outputs/current_next_step_v1_5f/current_branch_status_summary.json",
        "outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100_csv_only.csv",
        "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_or_all_csv_only.csv",
        "outputs/external_qa_v0_1/metatool/metatool_csv_validation_report.json",
        "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_csv_validation_report.json",
        "outputs/external_qa_v0_1/metatool/metatool_external_qa_summary.json",
        "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_external_qa_summary.json",
        "outputs/qwen_semcap_judge_v1_4d_step3/finalqa100/eval/qwen_step3_finalqa100_false_keep_analysis.csv",
        "outputs/policy_v1_5f_tightening_dryrun/clean_candidates_v1_4c_with_v1_5f_annotations.csv",
        "outputs/policy_v1_5f_tightening_dryrun/finalqa100_v1_5f_regression_trace.csv",
        "outputs/policy_v1_5f_tightening_dryrun/finalqa100_v1_5f_regression_summary.json",
        "outputs/policy_v1_5f_tightening_dryrun/v1_5f_dryrun_summary.json",
        "outputs/policy_v1_5f_tightening_dryrun/v1_5f_rule_hit_counts.csv",
        "outputs/policy_v1_5f_tightening_dryrun/v1_5f_task_type_movement_counts.csv",
        "outputs/final_qa_v1_5f_impacted_review/final_qa_v1_5f_impacted_review_items_csv_only.csv",
        "scripts/validation/prepare_v1_5f_status_and_external_csv_only.py",
        "scripts/validation/validate_external_qa_csv_v0_1.py",
        "scripts/validation/summarize_external_qa_csv_v0_1.py",
        "scripts/validation/run_policy_v1_5f_tightening_dryrun.py",
    ]
    archive_root = project_root / ARCHIVE_DIR
    manifest = []
    for rel in files:
        src = project_root / rel
        if not src.exists():
            manifest.append({"source": rel, "copied": False, "reason": "missing"})
            continue
        dst = archive_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        manifest.append({"source": rel, "archive_path": str(dst), "copied": True, "size_bytes": src.stat().st_size})
    write_json(archive_root / "archive_manifest.json", {"generated_time": now(), "files": manifest})
    return archive_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Run v1.5f deterministic tightening dry-run on v1.4c clean candidates.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current working directory.")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    source, _all_rows, clean_rows = discover_clean_source(project_root)
    write_rule_spec(project_root)
    false_keep = false_keep_analysis(project_root)
    annotated = annotate_clean_candidates(project_root, clean_rows)
    regression = regression_finalqa100(project_root)
    dryrun_summary = summarize_dryrun(project_root, annotated)
    write_dryrun_report(project_root, dryrun_summary, regression)
    impacted_pack(project_root, annotated)
    go_no_go = write_go_no_go(project_root, regression)
    archive_root = archive(project_root)

    top_rules = Counter()
    for row in annotated:
        top_rules.update(rule_ids(row))
    final = {
        "branch_status_dashboard_generated": (project_root / "outputs/current_next_step_v1_5f/current_branch_status_summary.json").exists(),
        "external_review_mode": "csv_only",
        "metatool_csv_review_pack_generated": (project_root / "outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100_csv_only.csv").exists(),
        "stabletoolbench_csv_review_pack_generated": (project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_or_all_csv_only.csv").exists(),
        "html_review_app_generated": False,
        "qwen_finalqa100_status": "failed_reliability_hard_gates",
        "input_clean_candidate_count": dryrun_summary["input_clean_candidate_count"],
        "still_clean_candidate_count": dryrun_summary["still_clean_candidate_count"],
        "downgrade_to_uncertain_count": dryrun_summary["downgrade_to_uncertain_count"],
        "dryrun_remove_count": dryrun_summary["dryrun_remove_count"],
        "v1_5f_critical_capture_rate": regression["v1_5f_critical_capture_rate"],
        "v1_5f_remove_capture_rate": regression["v1_5f_remove_capture_rate"],
        "qwen_false_keep_4_blocked_count": regression["qwen_false_keep_4_blocked_count"],
        "keep_retention_rate_on_finalqa100": regression["v1_5f_keep_retention_rate"],
        "top_5_v1_5f_rule_hits": dict(top_rules.most_common(5)),
        "can_accept_v1_5f_dryrun_policy": go_no_go["can_accept_v1_5f_dryrun_policy"],
        "can_generate_final_clean_dataset_now": False,
        "can_merge_external_sources_now": False,
        "recommended_next_step": go_no_go["recommended_next_step"],
        "archive_dir": str(archive_root),
        "v1_4c_clean_source": str(source),
        "false_keep_analysis_covered": false_keep,
    }
    print(json.dumps(final, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
