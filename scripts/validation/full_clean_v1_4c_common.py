from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


OUTPUT_DIR = Path("outputs/full_clean_dryrun_v1_4c")
REGRESSION_DIR = OUTPUT_DIR / "regression"
DOC_DIR = Path("docs/phase1")

V14B_DIR = Path("outputs/full_clean_dryrun_v1_4b")
V14B_TASK_TRACE = V14B_DIR / "full_clean_task_trace_v1_4b.csv"
V14B_SUMMARY = V14B_DIR / "full_clean_dryrun_summary_v1_4b.json"
V15D_REVIEW_SET = Path("outputs/final_qa_v1_5d/final_qa_review_items_v1_5d.csv")

V15D_FAILED_QA_IDS = [
    "FQA-1.5D-037",
    "FQA-1.5D-039",
    "FQA-1.5D-045",
    "FQA-1.5D-049",
    "FQA-1.5D-054",
    "FQA-1.5D-056",
    "FQA-1.5D-057",
    "FQA-1.5D-061",
    "FQA-1.5D-064",
    "FQA-1.5D-065",
    "FQA-1.5D-066",
    "FQA-1.5D-068",
    "FQA-1.5D-075",
    "FQA-1.5D-076",
    "FQA-1.5D-077",
    "FQA-1.5D-079",
    "FQA-1.5D-080",
    "FQA-1.5D-086",
    "FQA-1.5D-089",
    "FQA-1.5D-091",
    "FQA-1.5D-093",
    "FQA-1.5D-096",
    "FQA-1.5D-099",
    "FQA-1.5D-103",
    "FQA-1.5D-104",
    "FQA-1.5D-105",
    "FQA-1.5D-108",
    "FQA-1.5D-110",
    "FQA-1.5D-115",
    "FQA-1.5D-121",
    "FQA-1.5D-126",
    "FQA-1.5D-129",
]

V14C_POLICY_FIELDS = [
    "dryrun_decision_v1_4c",
    "dryrun_bucket_v1_4c",
    "blocking_reasons_v1_4c",
    "warning_reasons_v1_4c",
    "triggered_rules_v1_4c",
    "is_dryrun_clean_candidate_v1_4c",
    "is_dryrun_removed_v1_4c",
    "is_dryrun_uncertain_v1_4c",
    "is_dryrun_service_leak_only_v1_4c",
    "clean_confidence_bucket_v1_4c",
    "requires_final_qa_v1_4c",
    "v1_4c_change_from_v1_4b",
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_md(path: Path, lines: Sequence[str]) -> None:
    ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def write_json(path: Path, payload: object) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def open_csv_writer(path: Path, fieldnames: Sequence[str]) -> tuple[Any, csv.DictWriter]:
    ensure_dir(path.parent)
    f = path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
    writer.writeheader()
    return f, writer


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return sum(1 for _ in reader)


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def has_any(text: str, terms: Sequence[str]) -> bool:
    t = norm(text)
    return any(term and term in t for term in terms)


def truthy(value: Any) -> bool:
    return norm(value) in {"true", "1", "yes"}


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return []


def table_lines(counter: dict[str, int] | Counter) -> list[str]:
    lines = ["| value | count |", "|---|---:|"]
    for key, count in sorted(dict(counter).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {key} | {count} |")
    return lines


def extract_text(json_value: Any) -> str:
    data = parse_jsonish(json_value)
    chunks: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                chunks.extend(str(v or "") for v in item.values())
            else:
                chunks.append(str(item or ""))
    elif isinstance(data, dict):
        chunks.extend(str(v or "") for v in data.values())
    else:
        chunks.append(str(json_value or ""))
    return norm(" ".join(chunks))


def gold_text(row: dict[str, Any]) -> str:
    return norm(" ".join([extract_text(row.get("gold_services_json", "")), extract_text(row.get("gold_apis_json", ""))]))


def v12_requirements(row: dict[str, Any]) -> list[str]:
    data = parse_jsonish(row.get("v12_core_requirements_json", "[]"))
    if isinstance(data, list):
        return [norm(item) for item in data if str(item or "").strip()]
    return []


def is_general_information_fallback(row: dict[str, Any]) -> bool:
    reqs = v12_requirements(row)
    return reqs == ["general_information"]


def query_has_multi_requirement_signal(query: str) -> bool:
    q = norm(query)
    connectors = [
        "additionally",
        "also",
        "lastly",
        "finally",
        "furthermore",
        "moreover",
        "in addition",
        "and i",
        "also provide",
        "please provide",
    ]
    action_terms = [
        "recommend",
        "suggest",
        "find",
        "fetch",
        "provide",
        "search",
        "convert",
        "check",
        "generate",
        "analyze",
        "extract",
        "list",
        "details",
        "prices",
        "images",
        "venues",
        "housing",
        "speakers",
        "weather",
        "songs",
        "companies",
        "scientific name",
    ]
    connector_count = sum(1 for term in connectors if term in q)
    action_count = sum(1 for term in action_terms if term in q)
    question_count = q.count("?")
    return connector_count >= 1 or action_count >= 3 or question_count >= 2


DOMAIN_REQUIREMENT_RULES: list[dict[str, Any]] = [
    {
        "label": "source_specific_news_topic",
        "triggers": ["from reuters", "from the guardian", "from ieee spectrum", "from the times", "guardian-specific", "news feeds from the guardian"],
        "positive_any": ["search", "keyword", "query", "source", "feed", "guardian", "reuters", "ieee", "times"],
        "negative_any": ["get articles by date"],
    },
    {
        "label": "energy_price_news_articles_by_source",
        "triggers": ["energy prices", "energy price", "different sources in europe", "sources in europe"],
        "positive_any": ["article", "articles", "search", "specific article"],
        "negative_any": ["sources by region", "list of sources"],
    },
    {
        "label": "vegan_restaurant_recommendation",
        "triggers": ["vegan-friendly restaurant", "vegan restaurant"],
        "positive_any": ["vegan restaurant", "vegan-friendly", "cuisine", "dietary", "restaurant recommendation"],
        "negative_any": ["get restaurants by address", "get restaurant by link"],
    },
    {
        "label": "auto_parts_best_deals",
        "triggers": ["auto parts", "best deals"],
        "positive_any": ["auto parts", "car parts", "deal", "search"],
        "negative_any": ["subscribe"],
    },
    {
        "label": "coworking_or_housing_place_search",
        "triggers": ["co-working spaces", "coworking spaces", "housing options", "affordable housing"],
        "positive_any": ["coworking", "co-working", "workspace", "housing", "apartment", "rental", "place"],
        "negative_any": ["currency", "crypto", "stock", "forex"],
    },
    {
        "label": "specific_place_image_retrieval",
        "triggers": ["hawaii", "wallpapers", "scenic", "stable diffusion"],
        "positive_any": ["hawaii", "wallpaper", "scenic", "stable diffusion", "generate image"],
        "negative_any": ["miku", "astro", "random image", "astro photo"],
    },
    {
        "label": "nearest_restaurant_menu_prices",
        "triggers": ["nearest kfc", "dishes available", "prices and quantities", "cater food"],
        "positive_any": ["nearest", "nearby", "address", "location", "price", "prices", "menu", "quantity", "quantities"],
        "negative_any": ["state names"],
    },
    {
        "label": "spoken_realtime_translation",
        "triggers": ["real-time translations", "spoken text", "translate spoken", "voice recordings in different languages"],
        "positive_any": ["spoken", "speech", "audio", "real-time", "realtime", "voice", "multilingual"],
        "negative_any": ["list of available languages"],
    },
    {
        "label": "upcoming_games_schedule",
        "triggers": ["upcoming basketball games", "teams playing"],
        "positive_any": ["game", "games", "schedule", "upcoming", "match"],
        "negative_any": ["get all teams", "all teams"],
    },
    {
        "label": "travel_planning_services",
        "triggers": ["travel planning services", "vacation spots", "travel industry"],
        "positive_any": ["travel planning", "travel agency", "vacation", "tour", "destination"],
        "negative_any": ["linkedin leads", "job titles"],
    },
    {
        "label": "period_specific_currency_rates",
        "triggers": ["historical exchange rate", "historical exchange rates", "past year", "past month", "monthly average", "average rates"],
        "positive_any": ["history", "historical", "average", "averages", "time series", "rates"],
        "negative_any": ["cryptocurrency", "coin", "crypto", "hryvna today"],
    },
    {
        "label": "book_cover_by_title_author",
        "triggers": ["book cover image", "book cover", "by paulo coelho"],
        "positive_any": ["title", "author", "isbn", "search"],
        "negative_any": [],
    },
    {
        "label": "competitor_company_research",
        "triggers": ["potential competitors", "companies in my industry", "directors of these companies"],
        "positive_any": ["competitor", "industry", "company details", "director"],
        "negative_any": ["marketplace list"],
    },
    {
        "label": "venue_recommendation",
        "triggers": ["suggest a popular venue", "recommend a venue", "venue in"],
        "positive_any": ["recommend", "suggest", "venue", "popular times", "place"],
        "negative_any": ["search a place"],
    },
    {
        "label": "playlist_or_theme_song",
        "triggers": ["playlist", "theme songs", "popular songs to play"],
        "positive_any": ["playlist", "theme song", "song recommendation", "popular songs", "music"],
        "negative_any": ["song details"],
    },
    {
        "label": "fundraising_ideas",
        "triggers": ["fundraising ideas", "creative fundraising", "ideas"],
        "positive_any": ["fundraising", "idea", "ideas", "charity"],
        "negative_any": ["joke"],
    },
    {
        "label": "celebrity_voice_mimic",
        "triggers": ["celebrity voices", "mimic celebrity", "favorite celebrity"],
        "positive_any": ["celebrity", "mimic", "clone", "voice model"],
        "negative_any": ["list of voices"],
    },
    {
        "label": "event_planning_full_support",
        "triggers": ["event management software", "conference venues", "keynote speakers", "breakout sessions"],
        "positive_any": ["event management", "venue", "speaker", "breakout", "conference"],
        "negative_any": ["calendar invites", "critical mobile alerting", "subscription"],
    },
    {
        "label": "research_studies_expert_opinions",
        "triggers": ["research studies", "expert opinions", "reliable sources"],
        "positive_any": ["study", "studies", "expert", "research", "source"],
        "negative_any": [],
    },
    {
        "label": "credit_card_recommendation",
        "triggers": ["recommend credit cards", "credit cards with rewards"],
        "positive_any": ["recommend", "credit card", "card rewards", "healthcare"],
        "negative_any": ["merchant reward lookup"],
    },
    {
        "label": "scientific_name_lookup",
        "triggers": ["scientific name", "osteospermum"],
        "positive_any": ["scientific name", "species", "search", "flower"],
        "negative_any": ["get by id"],
    },
    {
        "label": "mountain_weather_recommendation",
        "triggers": ["suggest a mountain peak", "suitable weather", "good weather"],
        "positive_any": ["suggest", "recommend", "weather", "forecast", "peak conditions"],
        "negative_any": ["search mountain peak by name", "by city"],
    },
    {
        "label": "hotel_search_and_public_holiday",
        "triggers": ["available hotels", "beach cities", "public holidays", "upcoming public holidays"],
        "positive_any": ["hotel", "availability", "public holiday", "holidays", "search"],
        "negative_any": ["cities clusters", "isupdate"],
    },
]

WRONG_EXTRA_GOLD_RULES: list[tuple[str, list[str], list[str]]] = [
    ("crypto_or_bybit_extra_without_crypto_need", ["bybit", "cryptocurrency", "crypto", "coin"], ["crypto", "cryptocurrency", "bitcoin", "bybit", "coin"]),
    ("ipl_extra_without_cricket_need", ["latest ipl", "ipl news"], ["ipl", "cricket"]),
    ("energy_price_extra_without_energy_need", ["energy price news"], ["energy", "energy prices"]),
    ("climate_news_extra_without_climate_need", ["climate news feed", "climate news"], ["climate", "environment"]),
    ("instagram_extra_for_tiktok_need", ["instagram"], ["instagram"]),
    ("miku_or_random_image_extra_for_specific_image_need", ["mikuapi", "miku random", "random image"], ["miku", "anime", "random image"]),
    ("signl4_extra_without_alerting_need", ["signl4", "critical mobile alerting"], ["alert", "incident", "mobile alert"]),
]


def detect_v14c_gaps(row: dict[str, Any]) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    q = norm(row.get("query_text", ""))
    g = gold_text(row)
    blocking: list[str] = []
    domain_flags: list[dict[str, str]] = []
    wrong_gold_flags: list[dict[str, str]] = []

    if is_general_information_fallback(row) and query_has_multi_requirement_signal(q):
        blocking.append("general_information_fallback")
        domain_flags.append(
            {
                "rule": "general_information_fallback_cannot_clean_multi_requirement_query",
                "reason": "v12_core_requirements_json only has general_information while query has multiple explicit requirements",
            }
        )

    for rule in DOMAIN_REQUIREMENT_RULES:
        if not has_any(q, rule["triggers"]):
            continue
        positive_hit = has_any(g, rule["positive_any"])
        negative_hit = has_any(g, rule["negative_any"])
        if not positive_hit or negative_hit:
            blocking.append("domain_specific_gap")
            domain_flags.append(
                {
                    "rule": rule["label"],
                    "reason": "explicit query requirement is not directly covered by gold service/API",
                    "positive_terms": ";".join(rule["positive_any"]),
                    "negative_terms": ";".join(rule["negative_any"]),
                }
            )

    source_names = ["guardian", "reuters", "ieee spectrum", "the times"]
    for source in source_names:
        if source in q and source not in g:
            blocking.append("domain_specific_gap")
            domain_flags.append(
                {
                    "rule": "source_specific_news_missing_named_source",
                    "reason": f"query asks for news/feed from {source}, but gold text does not include that source",
                    "positive_terms": source,
                    "negative_terms": "",
                }
            )

    for label, gold_markers, allowed_query_terms in WRONG_EXTRA_GOLD_RULES:
        if has_any(g, gold_markers) and not has_any(q, allowed_query_terms):
            blocking.append("wrong_extra_gold_service")
            wrong_gold_flags.append(
                {
                    "rule": label,
                    "reason": "gold set contains service/API family not requested by query",
                    "gold_markers": ";".join(gold_markers),
                }
            )

    return sorted(set(blocking)), domain_flags, wrong_gold_flags


def policy_decide_v14c(row: dict[str, Any]) -> dict[str, Any]:
    old_decision = row.get("dryrun_decision_v1_4b", "")
    old_bucket = row.get("dryrun_bucket_v1_4b", "")
    out = dict(row)
    blocking: list[str] = []
    warnings: list[str] = []
    rules: list[str] = []

    if old_decision == "dryrun_clean_candidate":
        gap_reasons, domain_flags, wrong_gold_flags = detect_v14c_gaps(row)
        if "wrong_extra_gold_service" in gap_reasons:
            blocking.append("wrong_gold_set")
            rules.append("v14c_gold_set_integrity_hard_gate")
        if "domain_specific_gap" in gap_reasons:
            blocking.append("domain_specific_gap")
            rules.append("v14c_domain_specific_requirement_explicit_match")
        if "general_information_fallback" in gap_reasons:
            warnings.append("general_information_fallback")
            rules.append("v14c_general_information_fallback_blocking")
        out["v1_4c_domain_specific_guard_flags_json"] = json.dumps(domain_flags, ensure_ascii=False, sort_keys=True)
        out["v1_4c_wrong_gold_set_flags_json"] = json.dumps(wrong_gold_flags, ensure_ascii=False, sort_keys=True)
        if blocking:
            priority = ["wrong_gold_set", "domain_specific_gap"]
            primary = next((item for item in priority if item in blocking), blocking[0])
            decision = "dryrun_removed"
            bucket = "removed_wrong_gold_set" if primary == "wrong_gold_set" else "removed_capability_mismatch"
        elif warnings:
            decision = "dryrun_uncertain"
            bucket = "uncertain_semcap_general_information_fallback"
        else:
            decision = old_decision
            bucket = old_bucket
            rules.append("keep_v14c_clean_candidate")
    else:
        decision = old_decision
        bucket = old_bucket
        out["v1_4c_domain_specific_guard_flags_json"] = "[]"
        out["v1_4c_wrong_gold_set_flags_json"] = "[]"
        rules.append("preserve_v14b_non_clean_decision")

    change = "unchanged" if old_decision == decision and old_bucket == bucket else f"{old_decision}:{old_bucket}->{decision}:{bucket}"
    out.update(
        {
            "dryrun_decision_v1_4c": decision,
            "dryrun_bucket_v1_4c": bucket,
            "blocking_reasons_v1_4c": ";".join(sorted(set(blocking))),
            "warning_reasons_v1_4c": ";".join(sorted(set(warnings))),
            "triggered_rules_v1_4c": ";".join(sorted(set(rules))),
            "is_dryrun_clean_candidate_v1_4c": str(decision == "dryrun_clean_candidate").lower(),
            "is_dryrun_removed_v1_4c": str(decision == "dryrun_removed").lower(),
            "is_dryrun_uncertain_v1_4c": str(decision == "dryrun_uncertain").lower(),
            "is_dryrun_service_leak_only_v1_4c": str(decision == "dryrun_service_leak_only").lower(),
            "clean_confidence_bucket_v1_4c": "clean_candidate_high_conf" if decision == "dryrun_clean_candidate" else "",
            "requires_final_qa_v1_4c": "true",
            "v1_4c_change_from_v1_4b": change,
        }
    )
    return out


def dangerous_flags_v14c(row: dict[str, Any]) -> list[str]:
    if row.get("dryrun_decision_v1_4c") != "dryrun_clean_candidate":
        return []
    flags: list[str] = []
    if norm(row.get("api_leak_detector_status")) == "api_leak_blocking" or norm(row.get("api_leak_strength")) == "strong":
        flags.append("blocking_api_leak_into_clean")
    if row.get("gold_in_candidate_services") != "yes" or row.get("gold_in_candidate_apis") not in {"yes", "yes_api_name_only"}:
        flags.append("gold_missing_into_clean")
    if norm(row.get("candidate_space_status")).startswith("invalid"):
        flags.append("no_choice_space_into_clean")
    if norm(row.get("service_leak_detector_status")) == "service_leak_only" and norm(row.get("prediction_level")) == "service":
        flags.append("service_level_service_leak_into_clean")
    if row.get("warning_reasons_v1_4c") or row.get("blocking_reasons_v1_4c"):
        flags.append("v14c_blocking_or_warning_signal_into_clean")
    return sorted(set(flags))


def load_v15d_failed_task_ids() -> dict[str, str]:
    if not V15D_REVIEW_SET.exists():
        return {}
    rows = read_csv(V15D_REVIEW_SET)
    failed = {}
    failed_ids = set(V15D_FAILED_QA_IDS)
    for row in rows:
        qa_id = row.get("qa_item_id", "")
        task_id = row.get("task_id", "")
        if qa_id in failed_ids and task_id:
            failed[task_id] = qa_id
    return failed


def archive_v14c(root: Path) -> list[str]:
    archive_dir = root / "outputs" / "run_archives" / f"{datetime.now().strftime('%Y-%m-%d')}_full_clean_dryrun_v1_4c"
    ensure_dir(archive_dir)
    paths = [
        Path("scripts/validation/full_clean_v1_4c_common.py"),
        Path("scripts/validation/write_v1_4c_rule_docs.py"),
        Path("scripts/validation/apply_full_clean_dryrun_policy_v1_4c.py"),
        Path("scripts/validation/summarize_full_clean_dryrun_v1_4c.py"),
        OUTPUT_DIR,
        DOC_DIR / "final_qa_v1_5d_failure_taxonomy.md",
        DOC_DIR / "semcap_v1_3_tightening_rules_candidate.md",
        DOC_DIR / "policy_v1_4c_tightening_plan.md",
        DOC_DIR / "full_clean_dryrun_v1_4c_go_no_go_report.md",
        DOC_DIR / "full_clean_dryrun_summary_report_v1_4c.md",
    ]
    copied: list[str] = []
    for rel in paths:
        src = root / rel
        if not src.exists():
            continue
        dest = archive_dir / rel
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
            "# Full Clean Dry-Run v1.4c Archive Manifest",
            "",
            f"Generated time: {now_text()}",
            f"Archive directory: `{archive_dir}`",
            "",
            "This archive contains v1.4c targeted tightening dry-run artifacts only.",
            "No final clean dataset, split, baseline, model training, or v1.4b overwrite was produced.",
            "",
            "## Archived Files",
            "",
            *[f"- `{path}`" for path in copied],
        ],
    )
    return copied
