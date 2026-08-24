from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple


HUMAN_FIELDS = [
    "manual_final_decision",
    "semantic_alignment_check",
    "capability_coverage_check",
    "leakage_check",
    "candidate_validity_check",
    "task_type_check",
    "human_notes",
]

CALIBRATION_FIELDS = [
    "calibration_source",
    "record_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    *HUMAN_FIELDS,
    "pilot_semantic_alignment_pred",
    "pilot_semantic_alignment_confidence",
    "pilot_semantic_alignment_reason",
    "pilot_semantic_mismatch_type",
    "pilot_capability_coverage_pred",
    "pilot_capability_coverage_confidence",
    "pilot_core_requirements_json",
    "pilot_covered_requirements_json",
    "pilot_missing_requirements_json",
    "pilot_capability_mismatch_type",
    "pilot_capability_coverage_reason",
    "review_bucket",
    "risk_category",
    "risk_subtype",
]

PREDICTION_FIELDS = [
    "record_id",
    "task_id",
    "task_type",
    "source_group",
    "query_text",
    "semantic_alignment_pred",
    "semantic_alignment_confidence",
    "semantic_alignment_reason",
    "semantic_mismatch_type",
    "capability_coverage_pred",
    "capability_coverage_confidence",
    "core_requirements_json",
    "covered_requirements_json",
    "missing_requirements_json",
    "capability_mismatch_type",
    "capability_coverage_reason",
    "coverage_ok_but_policy_blocked_candidate",
    "requires_human_review",
    "detector_version",
    "gold_services_json",
    "gold_apis_json",
    "candidate_services_json",
    "candidate_apis_json",
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required input not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_csv_with_fields(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required input not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: Sequence[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_json(path: Path, payload: object) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_md(path: Path, lines: Sequence[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def parse_jsonish(value: object) -> object:
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        return value
    text = str(value).strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def norm(value: object) -> str:
    return str(value or "").strip().lower()


def token_text(*parts: object) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def value_counter(rows: Iterable[Dict[str, str]], field: str) -> Dict[str, int]:
    counter = Counter((row.get(field) or "<blank>").strip() or "<blank>" for row in rows)
    return dict(counter)


def table_lines(counter: Dict[str, int]) -> List[str]:
    lines = ["| value | count |", "|---|---|"]
    for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {count} |")
    return lines


def extract_names_and_descriptions(json_text: object) -> Tuple[List[str], str]:
    data = parse_jsonish(json_text)
    names: List[str] = []
    details: List[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for key in ("service_name", "api_name", "name", "title"):
                    if item.get(key):
                        names.append(str(item[key]))
                for key in ("service_description", "api_description", "description", "category_name", "method"):
                    if item.get(key):
                        details.append(str(item[key]))
            elif item is not None:
                names.append(str(item))
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str):
                if key.endswith("_name") or key in {"name", "title"}:
                    names.append(value)
                else:
                    details.append(value)
    return names, " ".join(names + details)


def extract_flagged_gold_text(json_text: object) -> str:
    data = parse_jsonish(json_text)
    parts: List[str] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            is_gold = item.get("is_gold_api", item.get("is_gold_service", False))
            if str(is_gold).strip().lower() not in {"1", "true", "yes"}:
                continue
            for key in (
                "service_name",
                "api_name",
                "name",
                "title",
                "service_description",
                "api_description",
                "description",
                "category_name",
            ):
                if item.get(key):
                    parts.append(str(item[key]))
    return " ".join(parts)


def count_json_items(json_text: object) -> int:
    data = parse_jsonish(json_text)
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return len(data)
    return 0


def has_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def requirements_from_query(query: str) -> List[str]:
    q = norm(query)
    reqs: List[str] = []
    groups = [
        ("traffic_route", ["traffic", "route", "directions", "driving time"]),
        ("weather", ["weather", "forecast", "temperature", "rain", "snow"]),
        ("hotel_venue_event", ["hotel", "venue", "concert", "event planner", "event planning", "attraction", "zoo"]),
        ("restaurant_place_reviews", ["restaurant", "review", "gas station", "place", "popular times", "walmart"]),
        ("social_profile", ["linkedin", "profile", "company data"]),
        ("company_list", ["company", "companies", "courier", "shipping company", "carrier list"]),
        ("company_images_or_extra_data", ["image", "images", "photo", "logo", "additional data", "extra data"]),
        ("package_tracking", ["package", "parcel", "mail", "postal", "tracking number", "track my", "shipment"]),
        ("container_tracking", ["container", "bill of lading", "vessel"]),
        ("postal_code", ["postal code", "zip code", "postcode"]),
        ("customs_transitaire", ["customs", "transitaire", "freight forwarder", "gondrand", "new caledonia"]),
        ("news_search", ["news", "article", "headline"]),
        ("image_search", ["image search", "find images", "search images", "retrieve images"]),
        ("web_search", ["web search", "search the web", "find websites", "web page"]),
        ("fake_user", ["fake user", "random user", "gender"]),
        ("trustpilot_review", ["trustpilot", "review", "star", "business unit"]),
        ("squake_project_health", ["squake", "checkhealth", "projects", "api health"]),
        ("translation_generation", ["translate", "translation", "ascii art", "hashtag", "generate"]),
        ("geocode", ["latitude", "longitude", "geocode", "coordinates"]),
        ("country_facts", ["population", "country name", "facts", "capital", "languages"]),
        ("finance_market", ["ipo", "stock", "market", "ticker", "exchange"]),
        ("recipe_instruction", ["recipe", "recipes", "ingredients", "cooking instruction", "cooking instructions"]),
        ("whatsapp_registration_status", ["registered on whatsapp", "whatsapp for business", "business status"]),
        ("bookstore_author_profile", ["bookstore", "bookstores", "author profiles", "authors", "new books"]),
        ("place_reviews_ratings", ["top-rated", "ratings", "pizza restaurants", "reviews and ratings"]),
        ("transit_agency_local_scope", ["transit agencies", "transit agency"]),
    ]
    for label, terms in groups:
        if has_any(q, terms):
            reqs.append(label)
    if not reqs:
        reqs.append("general_information")
    return sorted(set(reqs))


def covered_requirements(requirements: Sequence[str], gold_text: str) -> List[str]:
    g = norm(gold_text)
    covered: List[str] = []
    mapping = {
        "traffic_route": ["traffic", "route"],
        "weather": ["weather", "forecast"],
        "hotel_venue_event": ["hotel", "venue", "event", "popular times", "busy and popular"],
        "restaurant_place_reviews": ["restaurant", "place", "review", "popular times", "busy and popular"],
        "social_profile": ["linkedin", "company data", "profile"],
        "company_list": ["company", "companies", "courier", "shipping"],
        "company_images_or_extra_data": ["image", "images", "logo", "additional"],
        "package_tracking": ["package", "parcel", "mail", "postal", "tracking", "track"],
        "container_tracking": ["container", "bill of lading", "vessel"],
        "postal_code": ["postal", "zip", "postcode"],
        "customs_transitaire": ["transitaire", "customs", "freight"],
        "news_search": ["news", "currents", "article", "headline"],
        "image_search": ["image", "images", "imagesearch"],
        "web_search": ["web", "search", "websearch"],
        "fake_user": ["fake", "user", "gender"],
        "trustpilot_review": ["trustpilot", "review", "star"],
        "squake_project_health": ["squake", "project", "checkhealth", "health"],
        "translation_generation": ["translate", "translation", "ascii", "hashtag", "generate"],
        "geocode": ["latitude", "longitude", "geocode", "coordinates"],
        "country_facts": ["population", "country", "fact", "capital", "language"],
        "finance_market": ["ipo", "stock", "market", "ticker", "finance"],
        "recipe_instruction": ["recipe", "ingredient", "cooking"],
        "whatsapp_registration_status": ["registered", "whatsapp for business", "business status"],
        "bookstore_author_profile": ["bookstore", "author", "book"],
        "place_reviews_ratings": ["rating", "ratings", "restaurant", "place review"],
        "transit_agency_local_scope": ["transit agency", "transit agencies"],
        "general_information": [],
    }
    for req in requirements:
        terms = mapping.get(req, [])
        if not terms or has_any(g, terms):
            covered.append(req)
    return sorted(set(covered))


def detect_special(row: Dict[str, str]) -> Dict[str, object]:
    query = row.get("query_text", "")
    gold_service_names, gold_service_text = extract_names_and_descriptions(row.get("gold_services_json", ""))
    gold_api_names, gold_api_text = extract_names_and_descriptions(row.get("gold_apis_json", ""))
    cand_service_names, cand_service_text = extract_names_and_descriptions(row.get("candidate_services_json", ""))
    cand_api_names, cand_api_text = extract_names_and_descriptions(row.get("candidate_apis_json", ""))
    flagged_gold_text = extract_flagged_gold_text(row.get("candidate_services_json", "")) + " " + extract_flagged_gold_text(row.get("candidate_apis_json", ""))
    q = norm(query)
    gold_text = norm(" ".join(gold_service_names + gold_api_names) + " " + gold_service_text + " " + gold_api_text)
    coverage_text = norm(gold_text + " " + flagged_gold_text)
    cand_text = norm(cand_service_text + " " + cand_api_text)
    all_gold = gold_text
    all_context = norm(query + " " + gold_text + " " + cand_text)

    requirements = requirements_from_query(query)
    covered = covered_requirements(requirements, coverage_text)

    mismatch_reasons: List[str] = []
    uncertain_reasons: List[str] = []
    ok_reasons: List[str] = []

    if has_any(q, ["package", "parcel", "mail", "postal"]) and has_any(all_gold, ["container", "bill of lading", "vessel"]):
        mismatch_reasons.append("package_or_mail_tracking_mapped_to_container_tracking")
    if has_any(q, ["container", "bill of lading"]) and not has_any(all_gold, ["container", "bill of lading", "vessel"]):
        mismatch_reasons.append("container_tracking_requirement_not_covered")
    if has_any(q, ["hotel", "venue", "concert", "zoo", "gas station", "restaurant"]) and not has_any(
        coverage_text,
        ["hotel", "venue", "event", "concert", "zoo", "gas station", "restaurant", "popular times", "busy and popular", "place"],
    ):
        mismatch_reasons.append("hotel_venue_place_requirement_not_covered")
    if has_any(q, ["latitude", "longitude", "coordinates", "geocode"]) and not has_any(coverage_text, ["latitude", "longitude", "geocode", "coordinates"]):
        mismatch_reasons.append("geocode_requirement_not_covered")
    if has_any(q, ["traffic"]) and not has_any(coverage_text, ["traffic", "route"]):
        mismatch_reasons.append("traffic_requirement_not_covered")
    if has_any(q, ["weather", "forecast"]) and not has_any(coverage_text, ["weather", "forecast"]):
        mismatch_reasons.append("weather_requirement_not_covered")
    if has_any(q, ["ascii art", "hashtag", "translate", "translation"]) and not has_any(coverage_text, ["ascii", "hashtag", "translate", "translation", "generate"]):
        mismatch_reasons.append("generation_or_translation_requirement_not_covered")
    if has_any(q, ["image", "images", "photo", "logo", "additional data"]) and has_any(q, ["company", "companies"]) and not has_any(coverage_text, ["image", "photo", "logo", "additional"]):
        mismatch_reasons.append("company_images_or_additional_data_missing")
    if has_any(q, ["country", "population", "facts", "capital"]) and has_any(all_gold, ["language"]) and not has_any(all_gold, ["population", "capital", "country facts"]):
        mismatch_reasons.append("country_facts_not_covered_by_language_only_api")
    if has_any(q, ["recipe", "recipes", "ingredients", "cooking instruction", "cooking instructions"]) and not has_any(coverage_text, ["recipe", "ingredient", "cooking"]):
        mismatch_reasons.append("recipe_ingredients_and_instructions_not_covered")
    if "registered on whatsapp" in q and not has_any(gold_text, ["is registered", "registered on whatsapp"]):
        mismatch_reasons.append("whatsapp_registered_check_not_in_gold_apis")
    if "whatsapp for business" in q and not has_any(gold_text, ["is whatsapp for business", "whatsapp for business?"]):
        mismatch_reasons.append("whatsapp_business_account_check_not_in_gold_apis")
    if has_any(q, ["registered on whatsapp", "whatsapp for business", "business status"]) and not has_any(coverage_text, ["registered", "whatsapp for business", "business status"]):
        mismatch_reasons.append("whatsapp_registration_or_business_status_not_covered")
    if has_any(q, ["bookstore", "bookstores", "author profiles", "authors"]) and not has_any(coverage_text, ["bookstore", "bookstores", "author", "book"]):
        mismatch_reasons.append("bookstore_or_author_profile_requirement_not_covered")
    if has_any(q, ["pizza", "restaurant", "restaurants"]) and has_any(q, ["review", "reviews", "rating", "ratings", "top-rated"]) and has_any(all_gold, ["product review", "product reviews"]):
        uncertain_reasons.append("restaurant_reviews_may_be_confused_with_product_reviews")
    if has_any(q, ["turkey", "istanbul"]) and has_any(q, ["transit agency", "transit agencies"]) and has_any(all_gold, ["transitaire"]):
        mismatch_reasons.append("turkey_transit_agency_not_covered_by_new_caledonia_transitaires")

    if has_any(q, ["customs", "transitaire", "new caledonia", "gondrand"]) and has_any(all_gold, ["transitaire"]):
        ok_reasons.append("transitaire_customs_lookup_covered")
    if has_any(q, ["squake", "checkhealth", "projects", "api health"]) and has_any(all_gold, ["squake", "checkhealth", "project"]):
        ok_reasons.append("squake_project_health_covered")
    if has_any(q, ["fake user", "random user", "gender"]) and has_any(coverage_text, ["fake", "user", "gender"]):
        ok_reasons.append("fake_user_gender_covered")
    if "trustpilot" in q and has_any(coverage_text, ["trustpilot", "review", "star"]):
        ok_reasons.append("trustpilot_endpoint_covered")
    if has_any(q, ["news", "article", "headline"]) and has_any(coverage_text, ["news", "currents", "article", "headline", "search"]):
        ok_reasons.append("news_search_covered")
    if has_any(q, ["image search", "find images", "search images", "retrieve images"]) and has_any(coverage_text, ["image", "imagesearch", "search"]):
        ok_reasons.append("image_search_covered")
    if has_any(q, ["web search", "search the web", "find websites"]) and has_any(coverage_text, ["web", "search"]):
        ok_reasons.append("web_search_covered")
    if has_any(q, ["company", "companies", "courier", "shipping"]) and has_any(coverage_text, ["company", "companies", "courier", "shipping"]):
        if has_any(q, ["same-day", "same day", "custom packaging", "remote area", "remote-area", "international shipping", "images", "additional data"]):
            uncertain_reasons.append("logistics_company_specialization_or_extra_data_unclear")
        else:
            ok_reasons.append("company_or_logistics_list_covered")
    if has_any(q, ["package", "parcel", "mail", "postal"]) and has_any(coverage_text, ["package", "parcel", "postal", "tracking", "track"]):
        if not has_any(all_gold, ["container"]):
            ok_reasons.append("package_or_postal_tracking_covered")
    if requirements != ["general_information"] and len(set(requirements) - set(covered)) == 0:
        ok_reasons.append("all_detected_requirements_covered")
    elif covered:
        uncertain_reasons.append("partial_requirement_coverage")

    return {
        "requirements": sorted(set(requirements)),
        "covered": sorted(set(covered)),
        "missing": sorted(set(requirements) - set(covered)),
        "mismatch_reasons": mismatch_reasons,
        "uncertain_reasons": uncertain_reasons,
        "ok_reasons": ok_reasons,
        "gold_text": gold_text,
        "candidate_text": cand_text,
        "all_context": all_context,
    }


def policy_blocked_candidate(row: Dict[str, str]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    prediction_level = norm(row.get("prediction_level") or row.get("task_type"))
    candidate_service_count = row.get("candidate_service_count")
    gold_service_count = row.get("gold_service_count")
    try:
        cand_svc_count = int(str(candidate_service_count))
    except Exception:
        cand_svc_count = count_json_items(row.get("candidate_services_json", ""))
    try:
        gold_svc_count = int(str(gold_service_count))
    except Exception:
        gold_svc_count = count_json_items(row.get("gold_services_json", ""))

    if "service" in prediction_level and cand_svc_count <= max(1, gold_svc_count):
        reasons.append("service_level_no_choice_space")
    if "invalid_service_no_choice_space" in norm(row.get("candidate_space_status")):
        reasons.append("service_level_no_choice_space")
    if "strong" in norm(row.get("api_leak_strength")) or "strong" in norm(row.get("api_leak_detector_status")):
        reasons.append("strong_api_leak")
    if norm(row.get("service_leak_detector_status")) == "service_leak_only" and "service" in prediction_level:
        reasons.append("service_leak_only")
    if norm(row.get("task_type_eligibility_status")).startswith("invalid") or norm(row.get("task_type_check")) == "invalid":
        reasons.append("task_type_invalid")
    return bool(reasons), sorted(set(reasons))


def run_semcap_v1_detector(row: Dict[str, str], record_id: str = "") -> Dict[str, object]:
    special = detect_special(row)
    mismatch_reasons = list(special["mismatch_reasons"])
    uncertain_reasons = list(special["uncertain_reasons"])
    ok_reasons = list(special["ok_reasons"])
    missing = list(special["missing"])
    covered = list(special["covered"])
    requirements = list(special["requirements"])

    if mismatch_reasons:
        semantic_pred = "mismatch"
        semantic_conf = "high"
        capability_pred = "coverage_mismatch"
        capability_conf = "high"
        mismatch_type = mismatch_reasons[0]
        cap_type = mismatch_reasons[0]
        sem_reason = "Clear semantic or scope mismatch: " + "; ".join(mismatch_reasons)
        cap_reason = "Core requirement missing or wrong scope: " + "; ".join(mismatch_reasons)
    elif missing and not ok_reasons:
        semantic_pred = "uncertain"
        semantic_conf = "medium"
        capability_pred = "coverage_uncertain"
        capability_conf = "medium"
        mismatch_type = "partial_or_unclear_domain_match"
        cap_type = "partial_or_unclear_coverage"
        sem_reason = "Some detected requirements are not clearly mapped to gold."
        cap_reason = "Missing or unclear requirements: " + ", ".join(missing)
    elif uncertain_reasons and not ok_reasons:
        semantic_pred = "uncertain"
        semantic_conf = "medium"
        capability_pred = "coverage_uncertain"
        capability_conf = "medium"
        mismatch_type = "unclear_scope"
        cap_type = uncertain_reasons[0]
        sem_reason = "Domain may align but scope is unclear: " + "; ".join(uncertain_reasons)
        cap_reason = "Coverage uncertain: " + "; ".join(uncertain_reasons)
    elif uncertain_reasons and ok_reasons:
        semantic_pred = "ok"
        semantic_conf = "medium"
        capability_pred = "coverage_uncertain"
        capability_conf = "medium"
        mismatch_type = ""
        cap_type = uncertain_reasons[0]
        sem_reason = "Gold appears in the right domain, but some capability details are uncertain."
        cap_reason = "Partial coverage with uncertainty: " + "; ".join(uncertain_reasons)
    else:
        semantic_pred = "ok"
        semantic_conf = "high" if ok_reasons else "medium"
        capability_pred = "coverage_ok"
        capability_conf = "high" if ok_reasons else "medium"
        mismatch_type = ""
        cap_type = ""
        sem_reason = "Gold service/API appears semantically aligned with the query."
        cap_reason = "Detected core requirements are covered: " + ("; ".join(ok_reasons) if ok_reasons else "general coverage inferred")

    blocked, block_reasons = policy_blocked_candidate(row)
    if capability_pred == "coverage_ok" and semantic_pred == "ok" and blocked:
        coverage_ok_but_policy_blocked = "true"
        cap_reason += " Policy gates may still block clean-ready: " + "; ".join(block_reasons)
    else:
        coverage_ok_but_policy_blocked = "false"

    requires_review = "true" if capability_pred != "coverage_ok" or semantic_pred != "ok" or capability_conf != "high" else "false"

    return {
        "record_id": record_id or row.get("record_id") or row.get("review_item_id") or row.get("round3_review_id") or row.get("v0_8_sample_id") or row.get("task_id", ""),
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_group": row.get("source_group", ""),
        "query_text": row.get("query_text", ""),
        "semantic_alignment_pred": semantic_pred,
        "semantic_alignment_confidence": semantic_conf,
        "semantic_alignment_reason": sem_reason,
        "semantic_mismatch_type": mismatch_type,
        "capability_coverage_pred": capability_pred,
        "capability_coverage_confidence": capability_conf,
        "core_requirements_json": compact_json(requirements),
        "covered_requirements_json": compact_json(covered),
        "missing_requirements_json": compact_json(missing),
        "capability_mismatch_type": cap_type,
        "capability_coverage_reason": cap_reason,
        "coverage_ok_but_policy_blocked_candidate": coverage_ok_but_policy_blocked,
        "requires_human_review": requires_review,
        "detector_version": "v1.1_semcap_heuristic",
        "gold_services_json": row.get("gold_services_json", ""),
        "gold_apis_json": row.get("gold_apis_json", ""),
        "candidate_services_json": row.get("candidate_services_json", ""),
        "candidate_apis_json": row.get("candidate_apis_json", ""),
    }


def normalize_pilot_final(value: str) -> str:
    v = norm(value)
    if v == "pilot_remove":
        return "remove"
    if v == "pilot_uncertain":
        return "uncertain"
    if v == "pilot_keep_candidate":
        return "keep_for_cleaning_candidate"
    return value or ""


def compare_label(pred: str, human: str, kind: str) -> bool:
    p = norm(pred)
    h = norm(human)
    if kind == "semantic":
        if h == "ok":
            return p == "ok"
        if h == "uncertain":
            return p == "uncertain"
        if h in {"mismatch", "semantic_mismatch"}:
            return p == "mismatch"
    if kind == "capability":
        return p == h
    return p == h


def grouped_eval(rows: List[Dict[str, str]], predictions: Dict[str, Dict[str, str]], source_filter: str | None = None) -> Dict[str, object]:
    selected = [row for row in rows if source_filter is None or row.get("calibration_source") == source_filter]
    total = len(selected)
    sem_match = cap_match = mismatch_total = mismatch_captured = dangerous = 0
    high_ok_total = high_ok_human_ok = human_ok = human_ok_pred_ok = over_conservative = policy_blocked = 0
    for row in selected:
        pred = predictions.get(row.get("record_id", ""), {})
        sem_h = row.get("semantic_alignment_check", "")
        cap_h = row.get("capability_coverage_check", "")
        sem_p = pred.get("semantic_alignment_pred", "")
        cap_p = pred.get("capability_coverage_pred", "")
        cap_conf = pred.get("capability_coverage_confidence", "")
        if compare_label(sem_p, sem_h, "semantic"):
            sem_match += 1
        if compare_label(cap_p, cap_h, "capability"):
            cap_match += 1
        if cap_h == "coverage_mismatch":
            mismatch_total += 1
            if cap_p in {"coverage_mismatch", "coverage_uncertain"}:
                mismatch_captured += 1
            if cap_p == "coverage_ok" and cap_conf == "high":
                dangerous += 1
        if cap_p == "coverage_ok" and cap_conf == "high":
            high_ok_total += 1
            if cap_h == "coverage_ok":
                high_ok_human_ok += 1
        if cap_h == "coverage_ok":
            human_ok += 1
            if cap_p == "coverage_ok":
                human_ok_pred_ok += 1
            elif cap_p in {"coverage_uncertain", "coverage_mismatch"}:
                over_conservative += 1
        if pred.get("coverage_ok_but_policy_blocked_candidate") == "true":
            policy_blocked += 1
    return {
        "sample_count": total,
        "semantic_agreement": ratio(sem_match, total),
        "capability_agreement": ratio(cap_match, total),
        "dangerous_false_keep": dangerous,
        "coverage_mismatch_capture": ratio(mismatch_captured, mismatch_total),
        "coverage_mismatch_total": mismatch_total,
        "high_confidence_coverage_ok_precision_like": ratio(high_ok_human_ok, high_ok_total),
        "high_confidence_coverage_ok_total": high_ok_total,
        "coverage_ok_recall": ratio(human_ok_pred_ok, human_ok),
        "coverage_ok_human_total": human_ok,
        "coverage_ok_over_conservative_rate": ratio(over_conservative, human_ok),
        "coverage_ok_but_policy_blocked_recognition_count": policy_blocked,
    }


def ratio(num: int, den: int) -> str:
    if den <= 0:
        return "n/a"
    return f"{num / den:.1%}"


def archive_paths(archive_root: Path, paths: Sequence[Path], project_root: Path) -> List[str]:
    ensure_dir(archive_root)
    copied: List[str] = []
    for src in paths:
        if not src.exists():
            continue
        rel = src.relative_to(project_root) if src.is_absolute() and src.is_relative_to(project_root) else src
        dest = archive_root / rel
        ensure_dir(dest.parent)
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        copied.append(str(dest))
    return copied
