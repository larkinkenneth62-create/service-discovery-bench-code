from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


V12_FIELDS = [
    "v12_semantic_alignment_pred",
    "v12_semantic_alignment_confidence",
    "v12_semantic_alignment_reason",
    "v12_semantic_mismatch_type",
    "v12_capability_coverage_pred",
    "v12_capability_coverage_confidence",
    "v12_core_requirements_json",
    "v12_covered_requirements_json",
    "v12_missing_requirements_json",
    "v12_capability_mismatch_type",
    "v12_capability_coverage_reason",
    "v12_gold_set_integrity_status",
    "v12_extra_gold_service_flags_json",
    "v12_uncovered_core_requirement_flags_json",
    "v12_generic_search_overtrust_flag",
    "v12_domain_specific_guard_flags_json",
    "v12_tightening_triggered_rules_json",
    "v12_requires_human_review",
    "v12_detector_version",
]


GENERIC_SEARCH_TERMS = [
    "web search",
    "search the web",
    "google search",
    "bing",
    "autosuggest",
    "autocomplete",
    "entity search",
    "image search",
    "search api",
    "news search",
    "search",
]


REQUIREMENT_DEFS: list[tuple[str, list[str], list[str], list[str]]] = [
    ("current_weather", ["current weather", "weather now", "today's weather", "temperature", "wind speed", "precipitation"], ["current weather", "realtime weather", "real-time weather", "forecast", "weather api"], ["astronomy", "wave", "surf", "tide", "climate"]),
    ("weather_forecast", ["weather forecast", "next week", "forecast", "3-hour weather", "5-day", "7-day"], ["forecast", "weather"], ["astronomy", "wave", "surf", "tide", "climate news"]),
    ("sunrise_sunset", ["sunrise", "sunset"], ["sunrise", "sunset", "astronomy"], []),
    ("moon_phase", ["moon phase", "lunar"], ["moon", "lunar", "astronomy"], []),
    ("hiking_trail", ["hiking trail", "hiking trails", "trail recommendation", "trails in"], ["hiking", "trail", "outdoor", "tour", "attraction", "place"], ["wave", "surf", "tide"]),
    ("camping_site", ["camping site", "camping sites", "campground"], ["camping", "campground", "site", "place"], []),
    ("restaurant_recommendation", ["restaurant", "restaurants", "catering restaurant", "intimate restaurants", "pizza restaurants"], ["restaurant", "food", "place", "popular times", "rating", "review", "nearby"], ["product review"]),
    ("catering_service", ["catering", "catering service", "catering services", "offer catering"], ["catering", "cater"], []),
    ("nearby_place", ["nearby", "near me", "grocery", "gas station", "store address", "address/contact", "popular attractions", "walmart"], ["nearby", "place", "location", "store", "restaurant", "address", "popular times", "business"], []),
    ("store_address_contact", ["store address", "store's address", "address and contact", "address/contact", "contact information", "contact details"], ["address", "contact", "phone", "details", "location"], []),
    ("hotel_search", ["hotel", "lodging", "accommodation"], ["hotel", "lodging", "accommodation"], ["generic booking list"]),
    ("flight_fare", ["flight fare", "airfare", "flight price", "flights"], ["flight", "airfare", "airline"], ["rail", "train", "irctc"]),
    ("train_fare", ["train fare", "rail fare", "irctc"], ["train", "rail", "irctc"], []),
    ("travel_attraction", ["tourist attraction", "attractions", "destination", "destinations", "trip to", "solo trip", "paris attractions"], ["attraction", "destination", "tourism", "travel", "place"], []),
    ("movie_recommendation", ["movie", "movies", "films"], ["movie", "film", "cinema"], []),
    ("quote_generation_or_retrieval", ["quote", "quotes", "inspiring quote", "motivational quote"], ["quote", "quotes", "inspiration"], ["screenshot", "email"]),
    ("motivational_story", ["entrepreneur story", "business inspiration", "motivational story"], ["story", "entrepreneur", "business inspiration", "motivation"], []),
    ("baby_name_or_word_suggestion", ["baby name", "newborn", "name suggestion", "word suggestion"], ["baby", "name", "word", "suggest"], ["nba", "weather"]),
    ("language_phrase_suggestion", ["words and phrases", "word and phrase", "party banner", "party banners", "signage", "phrase suggestion"], ["phrase", "phrases", "word", "words", "language", "dictionary", "translation"], []),
    ("parenting_news", ["parenting news", "parenting"], ["parenting", "news"], []),
    ("domain_availability", ["domain availability", "available domain", "registerable domain", "domain registration"], ["availability", "available", "domain check", "whois", "register"], ["domain list", "list domains", "your domains", "owned domains"]),
    ("domain_list", ["domain list", "list domains", "your domains"], ["domain list", "list domains", "owned domains"], []),
    ("icd_code_lookup", ["icd code", "icd-10", "diagnosis code"], ["icd", "diagnosis"], []),
    ("translation_sentence", ["translate", "translation", "sentence translation", "native language"], ["translate", "translation", "translator"], ["dictionary", "indic", "hindi"]),
    ("translation_word", ["translate the word", "word translation", "bilingual dictionary"], ["dictionary", "translate", "translation"], []),
    ("ascii_art_generation", ["ascii art"], ["ascii", "art", "generate"], []),
    ("image_retrieval", ["image", "images", "photo", "screenshot", "logo", "alt text"], ["image", "photo", "screenshot", "logo", "alt text"], ["solar", "helioviewer"]),
    ("news_search", ["news", "headlines", "article"], ["news", "headline", "article", "web search"], []),
    ("recipe_search", ["recipe", "recipes"], ["recipe", "cooking", "ingredient"], []),
    ("nutrition_analysis", ["nutrition", "calorie"], ["nutrition", "calorie"], []),
    ("email_validation", ["email validation", "validate email", "temporary email"], ["email", "validation", "temporary email"], []),
    ("webpage_screenshot", ["webpage screenshot", "website screenshot", "capture a screenshot"], ["screenshot", "website"], []),
    ("package_tracking", ["package", "parcel", "mail", "tracking number", "track my"], ["package", "parcel", "postal", "track", "tracking"], ["container", "vessel", "bill of lading"]),
    ("shipping_company_list", ["shipping company", "courier", "carrier list"], ["shipping", "courier", "carrier", "company"], []),
    ("company_image_or_additional_data", ["company image", "company images", "company logo", "additional data", "extra data"], ["image", "logo", "additional", "company data"], []),
    ("historical_events", ["historical event", "historical events", "birth-year", "born in", "history facts"], ["history", "historical", "event", "facts"], ["finance history"]),
    ("fact_lookup", ["facts", "income level", "population", "capital", "country income"], ["fact", "population", "capital", "income", "world bank", "country"], ["language only"]),
    ("file_storage_info", ["file storage", "storage options"], ["file", "storage", "drive", "cloud"], []),
    ("file_storage_options_features", ["file storage options", "available file storage", "storage options with features", "features of file storage", "features and images"], ["feature", "features", "option", "options", "storage", "file"], []),
    ("ip_geolocation_provider", ["ip address", "geolocation data for the ip", "provider information", "provider info"], ["ip", "geolocation", "provider", "isp", "asn"], []),
    ("text_to_speech", ["text-to-speech", "tts", "speech"], ["text-to-speech", "tts", "speech", "voice"], []),
    ("ai_chatbot_help", ["chatbot", "ai-powered chatbot"], ["chatbot", "chatgpt", "assistant"], ["detector"]),
    ("qr_code", ["qr code"], ["qr", "qr code"], []),
    ("password_generation", ["password", "random password"], ["password", "random"], []),
    ("currency_exchange_rate", ["exchange rate", "usd", "gbp", "currency"], ["exchange rate", "currency", "forex"], []),
    ("sports_team_details", ["nba", "teams", "players", "basketball"], ["nba", "basketball", "team", "player"], []),
    ("sports_all_teams_details", ["all the teams", "all teams", "list of all the teams", "team details", "top basketball teams"], ["all teams", "teams list", "team list", "team details", "all nba teams"], ["specific team", "all players"]),
]


GENERIC_OVERTRUST_REQUIREMENTS = {
    "restaurant_recommendation",
    "nearby_place",
    "travel_attraction",
    "hiking_trail",
    "camping_site",
    "movie_recommendation",
    "quote_generation_or_retrieval",
    "motivational_story",
    "baby_name_or_word_suggestion",
    "parenting_news",
    "historical_events",
    "fact_lookup",
    "file_storage_info",
    "file_storage_options_features",
}


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def has_any(text: str, terms: Sequence[str]) -> bool:
    t = norm(text)
    return any(term and term in t for term in terms)


def parse_jsonish(value: object) -> object:
    if isinstance(value, (list, dict)):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def item_text(item: object) -> str:
    if isinstance(item, dict):
        return " ".join(str(v or "") for v in item.values())
    return str(item or "")


def extract_names_and_descriptions(json_text: object) -> tuple[list[str], str]:
    data = parse_jsonish(json_text)
    names: list[str] = []
    details: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for key in ("service_name", "api_name", "name", "title"):
                    if item.get(key):
                        names.append(str(item[key]))
                for key in ("service_description", "api_description", "description", "category_name", "method"):
                    if item.get(key):
                        details.append(str(item[key]))
            elif item:
                names.append(str(item))
    return names, " ".join(names + details)


def extract_gold_candidate_items(row: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for field, kind in [("candidate_services_json", "service"), ("candidate_apis_json", "api")]:
        data = parse_jsonish(row.get(field, ""))
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            gold_key = "is_gold_service" if kind == "service" else "is_gold_api"
            if str(item.get(gold_key, "")).strip().lower() not in {"true", "1", "yes"}:
                continue
            items.append(
                {
                    "kind": kind,
                    "service_name": str(item.get("service_name", "")),
                    "api_name": str(item.get("api_name", "")),
                    "text": item_text(item),
                }
            )
    return items


def gold_text(row: dict[str, Any]) -> str:
    svc_names, svc_text = extract_names_and_descriptions(row.get("gold_services_json", ""))
    api_names, api_text = extract_names_and_descriptions(row.get("gold_apis_json", ""))
    candidate_gold_text = " ".join(item["text"] for item in extract_gold_candidate_items(row))
    return norm(" ".join(svc_names + api_names + [svc_text, api_text, candidate_gold_text]))


def candidate_text(row: dict[str, Any]) -> str:
    svc_names, svc_text = extract_names_and_descriptions(row.get("candidate_services_json", ""))
    api_names, api_text = extract_names_and_descriptions(row.get("candidate_apis_json", ""))
    return norm(" ".join(svc_names + api_names + [svc_text, api_text]))


def extract_requirements(query: str) -> list[str]:
    q = norm(query)
    reqs = [label for label, triggers, _positive, _negative in REQUIREMENT_DEFS if has_any(q, triggers)]
    if not reqs:
        reqs = ["general_information"]
    return sorted(set(reqs))


def requirement_terms(label: str) -> tuple[list[str], list[str]]:
    for req_label, _triggers, positive, negative in REQUIREMENT_DEFS:
        if req_label == label:
            return positive, negative
    return [], []


def detect_translation_direction(query: str, coverage_text: str) -> list[str]:
    q = norm(query)
    g = norm(coverage_text)
    flags: list[str] = []
    languages = ["french", "spanish", "german", "greek", "japanese", "english", "hindi"]
    mentioned = [lang for lang in languages if lang in q]
    for lang in mentioned:
        if lang not in g and lang not in {"english"}:
            flags.append(f"translation_target_language_not_covered:{lang}")
    if ("sentence" in q or "message" in q or "hello, how are you" in q) and "dictionary" in g and not has_any(g, ["sentence", "text translation", "translator"]):
        flags.append("sentence_translation_mapped_to_dictionary")
    if "french" in q and has_any(g, ["hindi", "indic"]) and "french" not in g:
        flags.append("english_to_french_mapped_to_indic_or_hindi")
    return flags


def detect_requirement_coverage(row: dict[str, Any]) -> tuple[list[str], list[str], list[dict[str, str]], list[dict[str, str]], bool]:
    query = row.get("query_text", "")
    q = norm(query)
    reqs = extract_requirements(query)
    g = gold_text(row)
    covered: list[str] = []
    missing_flags: list[dict[str, str]] = []
    domain_flags: list[dict[str, str]] = []
    generic_overtrust = False
    for req in reqs:
        if req == "general_information":
            covered.append(req)
            continue
        positive, negative = requirement_terms(req)
        positive_hit = has_any(g, positive)
        negative_hit = has_any(g, negative)
        if req == "translation_sentence":
            direction_flags = detect_translation_direction(query, g)
            if direction_flags:
                domain_flags.extend({"requirement": req, "flag": flag} for flag in direction_flags)
                positive_hit = False
        if req == "current_weather" and has_any(g, ["astronomy", "wave", "surf", "tide"]) and not has_any(g, ["current weather", "forecast"]):
            domain_flags.append({"requirement": req, "flag": "current_weather_mapped_to_weather_adjacent_api"})
            positive_hit = False
        if req == "domain_availability" and has_any(g, ["domain list", "list domains", "your domains", "owned domains"]) and not has_any(g, ["availability", "available", "domain check"]):
            domain_flags.append({"requirement": req, "flag": "domain_availability_mapped_to_domain_list"})
            positive_hit = False
        if req == "flight_fare" and has_any(g, ["rail", "train", "irctc"]) and not has_any(g, ["flight", "airfare"]):
            domain_flags.append({"requirement": req, "flag": "flight_fare_mapped_to_rail_fare"})
            positive_hit = False
        if req == "language_phrase_suggestion" and not has_any(g, ["phrase", "phrases", "word", "words", "dictionary", "translation"]):
            domain_flags.append({"requirement": req, "flag": "language_phrase_need_mapped_to_unrelated_media_or_news"})
            positive_hit = False
        if req == "catering_service" and not has_any(g, ["catering", "cater"]):
            domain_flags.append({"requirement": req, "flag": "catering_need_mapped_to_generic_restaurant_or_recipe"})
            positive_hit = False
        if req == "store_address_contact" and not (has_any(g, ["address", "location"]) and has_any(g, ["contact", "phone", "details"])):
            domain_flags.append({"requirement": req, "flag": "store_address_contact_details_not_explicitly_covered"})
            positive_hit = False
        if req == "file_storage_options_features" and not (has_any(g, ["storage", "file", "cloud"]) and has_any(g, ["feature", "features", "option", "options"])):
            domain_flags.append({"requirement": req, "flag": "file_storage_options_features_not_explicitly_covered"})
            positive_hit = False
        if req == "ip_geolocation_provider":
            if ("provider" in q or "provider information" in q) and not has_any(g, ["provider", "isp", "asn"]):
                domain_flags.append({"requirement": req, "flag": "ip_provider_information_not_covered"})
                positive_hit = False
            if "geocode" in q and not has_any(g, ["geocode", "geocoder", "coordinates", "latitude", "longitude"]):
                domain_flags.append({"requirement": req, "flag": "ip_geocode_requirement_not_covered"})
                positive_hit = False
        if req == "sports_all_teams_details" and not has_any(g, ["all teams", "teams list", "team list", "all nba teams"]):
            domain_flags.append({"requirement": req, "flag": "all_teams_requirement_mapped_to_specific_team_or_players"})
            positive_hit = False
        if req in {"nearby_place", "restaurant_recommendation", "travel_attraction"} and has_any(g, GENERIC_SEARCH_TERMS) and not has_any(g, positive):
            generic_overtrust = True
            positive_hit = False
        if req in GENERIC_OVERTRUST_REQUIREMENTS and has_any(g, GENERIC_SEARCH_TERMS) and not positive_hit:
            generic_overtrust = True
        if positive_hit and not negative_hit:
            covered.append(req)
        else:
            reason = "domain_specific_guard_violation" if domain_flags and any(flag["requirement"] == req for flag in domain_flags) else "no_explicit_gold_capability"
            if negative_hit:
                reason = "negative_neighbor_capability"
            missing_flags.append({"requirement": req, "reason": reason, "positive_terms": ";".join(positive), "negative_terms": ";".join(negative)})
    return reqs, sorted(set(covered)), missing_flags, domain_flags, generic_overtrust


def detect_extra_gold_services(row: dict[str, Any], requirements: Sequence[str]) -> tuple[str, list[dict[str, str]]]:
    task_type = norm(row.get("task_type", ""))
    prediction_level = norm(row.get("prediction_level", ""))
    if "service" not in task_type and prediction_level != "service":
        return "not_applicable", []
    gold_items = [item for item in extract_gold_candidate_items(row) if item["kind"] == "service"]
    if len(gold_items) <= 1:
        return "ok", []
    flags: list[dict[str, str]] = []
    for item in gold_items:
        text = norm(item["text"] + " " + item["service_name"])
        matched = []
        for req in requirements:
            pos, _neg = requirement_terms(req)
            if req == "general_information" or has_any(text, pos):
                matched.append(req)
        if not matched:
            flags.append({"service_name": item["service_name"], "reason": "gold_service_does_not_match_any_extracted_core_requirement"})
    if flags:
        return "extra_gold_service_detected", flags
    return "ok", []


def run_v1_if_needed(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("v1_semantic_alignment_pred") and row.get("v1_capability_coverage_pred"):
        return dict(row)
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from semcap_v1_1_common import run_semcap_v1_detector

    pred = run_semcap_v1_detector(row, record_id=row.get("task_id", ""))
    out = dict(row)
    out.update(
        {
            "v1_semantic_alignment_pred": pred.get("semantic_alignment_pred", ""),
            "v1_semantic_alignment_confidence": pred.get("semantic_alignment_confidence", ""),
            "v1_semantic_alignment_reason": pred.get("semantic_alignment_reason", ""),
            "v1_semantic_mismatch_type": pred.get("semantic_mismatch_type", ""),
            "v1_capability_coverage_pred": pred.get("capability_coverage_pred", ""),
            "v1_capability_coverage_confidence": pred.get("capability_coverage_confidence", ""),
            "v1_core_requirements_json": pred.get("core_requirements_json", "[]"),
            "v1_covered_requirements_json": pred.get("covered_requirements_json", "[]"),
            "v1_missing_requirements_json": pred.get("missing_requirements_json", "[]"),
            "v1_capability_mismatch_type": pred.get("capability_mismatch_type", ""),
            "v1_capability_coverage_reason": pred.get("capability_coverage_reason", ""),
            "v1_coverage_ok_but_policy_blocked_candidate": pred.get("coverage_ok_but_policy_blocked_candidate", ""),
            "requires_human_review_v1": pred.get("requires_human_review", ""),
        }
    )
    return out


def run_semcap_v12(row: dict[str, Any]) -> dict[str, Any]:
    out = run_v1_if_needed(row)
    requirements, covered, missing_flags, domain_flags, generic_overtrust = detect_requirement_coverage(out)
    missing = [flag["requirement"] for flag in missing_flags]
    gold_integrity_status, extra_flags = detect_extra_gold_services(out, requirements)
    triggered: list[str] = []
    if missing_flags:
        triggered.append("every_core_requirement_must_be_covered")
    if gold_integrity_status == "extra_gold_service_detected":
        triggered.append("extra_gold_service_penalty")
    if generic_overtrust:
        triggered.append("generic_search_is_not_universal_cover")
    if domain_flags:
        triggered.append("domain_specific_capability_must_match")
    if not triggered:
        triggered.append("v12_no_tightening_trigger")

    v1_sem = norm(out.get("v1_semantic_alignment_pred"))
    semantic_pred = out.get("v1_semantic_alignment_pred", "ok") or "ok"
    semantic_conf = out.get("v1_semantic_alignment_confidence", "medium") or "medium"
    semantic_reason = out.get("v1_semantic_alignment_reason", "")
    semantic_mismatch_type = out.get("v1_semantic_mismatch_type", "")
    if v1_sem == "mismatch":
        semantic_pred = "mismatch"
        semantic_conf = out.get("v1_semantic_alignment_confidence", "high") or "high"
    elif missing_flags and len(missing) >= max(2, len(requirements)):
        semantic_pred = "uncertain"
        semantic_conf = "medium"
        semantic_reason = "v1.2 found broad query/gold capability mismatch risk"
        semantic_mismatch_type = "core_requirements_not_covered"

    if missing_flags or domain_flags:
        # Clear missing/negative neighbor signals are treated as mismatch; generic overtrust alone can remain uncertain.
        if domain_flags or len(missing) >= 1:
            capability_pred = "coverage_mismatch"
            capability_conf = "high" if domain_flags or len(missing) >= 2 else "medium"
            mismatch_type = "domain_specific_requirement_not_covered" if domain_flags else "missing_core_requirement"
        else:
            capability_pred = "coverage_uncertain"
            capability_conf = "medium"
            mismatch_type = "partial_or_generic_coverage_uncertain"
    elif gold_integrity_status == "extra_gold_service_detected":
        capability_pred = "coverage_mismatch"
        capability_conf = "high"
        mismatch_type = "wrong_gold_set_for_service_level"
    elif generic_overtrust:
        capability_pred = "coverage_uncertain"
        capability_conf = "medium"
        mismatch_type = "generic_search_overtrusted"
    elif norm(out.get("v1_capability_coverage_pred")) == "coverage_mismatch":
        capability_pred = "coverage_mismatch"
        capability_conf = out.get("v1_capability_coverage_confidence", "high") or "high"
        mismatch_type = out.get("v1_capability_mismatch_type", "v1_mismatch")
    elif norm(out.get("v1_capability_coverage_pred")) == "coverage_uncertain":
        capability_pred = "coverage_uncertain"
        capability_conf = out.get("v1_capability_coverage_confidence", "medium") or "medium"
        mismatch_type = out.get("v1_capability_mismatch_type", "v1_uncertain")
    else:
        capability_pred = "coverage_ok"
        capability_conf = "high"
        mismatch_type = ""

    if capability_pred == "coverage_ok" and (
        gold_integrity_status != "ok"
        or generic_overtrust
        or domain_flags
        or missing_flags
        or semantic_pred != "ok"
    ):
        capability_pred = "coverage_uncertain"
        capability_conf = "medium"
        mismatch_type = "high_confidence_coverage_requires_full_gold_set_integrity"
        triggered.append("high_confidence_coverage_requires_full_gold_set_integrity")

    requires_review = capability_pred != "coverage_ok" or semantic_pred != "ok"
    out.update(
        {
            "v12_semantic_alignment_pred": semantic_pred,
            "v12_semantic_alignment_confidence": semantic_conf,
            "v12_semantic_alignment_reason": semantic_reason or "v1.2 tightened semantic/capability consistency check",
            "v12_semantic_mismatch_type": semantic_mismatch_type,
            "v12_capability_coverage_pred": capability_pred,
            "v12_capability_coverage_confidence": capability_conf,
            "v12_core_requirements_json": compact_json(requirements),
            "v12_covered_requirements_json": compact_json(covered),
            "v12_missing_requirements_json": compact_json(sorted(set(missing))),
            "v12_capability_mismatch_type": mismatch_type,
            "v12_capability_coverage_reason": "; ".join(triggered),
            "v12_gold_set_integrity_status": gold_integrity_status,
            "v12_extra_gold_service_flags_json": compact_json(extra_flags),
            "v12_uncovered_core_requirement_flags_json": compact_json(missing_flags),
            "v12_generic_search_overtrust_flag": str(bool(generic_overtrust)).lower(),
            "v12_domain_specific_guard_flags_json": compact_json(domain_flags),
            "v12_tightening_triggered_rules_json": compact_json(sorted(set(triggered))),
            "v12_requires_human_review": str(requires_review).lower(),
            "v12_detector_version": "v1.2_tightening_heuristic",
        }
    )
    return out


def has_domain_guard_violation(row: dict[str, Any]) -> bool:
    return bool(parse_jsonish(row.get("v12_domain_specific_guard_flags_json", "[]")))


def has_missing_core_requirement(row: dict[str, Any]) -> bool:
    return bool(parse_jsonish(row.get("v12_uncovered_core_requirement_flags_json", "[]")))


def has_extra_gold_service(row: dict[str, Any]) -> bool:
    return norm(row.get("v12_gold_set_integrity_status")) == "extra_gold_service_detected"
