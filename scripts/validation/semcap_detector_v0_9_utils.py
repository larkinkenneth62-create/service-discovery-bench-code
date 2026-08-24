"""Utilities for v0.9 semantic/capability detector pilot.

This module supports a conservative pilot detector. Predictions are not human
final labels and must not be used to produce a final clean dataset.
"""

from __future__ import annotations

import csv
import html
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


OUTPUT_DIR = Path("outputs/semantic_capability_detector_pilot_v0_9")
DOCS_DIR = Path("docs/phase1")
ARCHIVE_DIR = Path("outputs/run_archives/2026-06-28_semcap_detector_pilot_v0_9")

V42_POLICY_DOC = Path("docs/phase1/manual_audit_rule_v4_2_candidate.md")
AUDIT_EVIDENCE = Path("outputs/cleaning_policy_validation_v0_7/audit_evidence_table_manual40_round2_round3.csv")
ROUND3_REVIEWED = Path("outputs/main_four_tasks_rule_revision_v0_6/round3_targeted_validation_items_100_user_reviewed.csv")
V08_SAMPLE = Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_input_tasks.csv")
V08_DETECTOR_TRACE = Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_detector_trace.csv")
V08_POLICY_TRACE = Path("outputs/small_full_pipeline_trace_v0_8/small_full_pipeline_policy_trace.csv")
V08_REPORTS = [
    Path("docs/phase1/small_full_pipeline_detector_report_v0_8.md"),
    Path("docs/phase1/small_full_pipeline_policy_trace_report_v0_8.md"),
    Path("docs/phase1/small_full_pipeline_dangerous_error_report_v0_8.md"),
    Path("docs/phase1/small_full_pipeline_trace_v0_8_go_no_go_report.md"),
]

MANUAL_COLUMNS = [
    "manual_final_decision",
    "semantic_alignment_check",
    "capability_coverage_check",
    "leakage_check",
    "candidate_validity_check",
    "task_type_check",
    "human_notes",
]

ROUNDED_CALIBRATION_COLUMNS = [
    "round3_review_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "manual_final_decision",
    "semantic_alignment_check",
    "capability_coverage_check",
    "leakage_check",
    "candidate_validity_check",
    "task_type_check",
    "human_notes",
    "risk_category",
    "risk_subtype",
]

PREDICTION_COLUMNS = [
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
    "requires_human_review",
    "detector_version",
    "gold_services_json",
    "gold_apis_json",
    "candidate_services_json",
    "candidate_apis_json",
]

GENERIC_WORDS = {
    "get",
    "list",
    "all",
    "search",
    "details",
    "detail",
    "status",
    "summary",
    "latest",
    "count",
    "data",
    "info",
    "information",
}

CAPABILITY_PATTERNS: Dict[str, List[str]] = {
    "weather_forecast": ["weather", "forecast", "temperature", "rain", "climate"],
    "translation": ["translate", "translation", "french", "chinese", "spanish", "language"],
    "restaurant_place_reviews": ["restaurant", "review", "rating", "places", "place", "attraction", "popular times", "walmart"],
    "hotel_venue_event": ["hotel", "venue", "event space", "planner", "bookstore", "travel destination", "destination"],
    "traffic_route": ["traffic", "route", "directions", "distance", "geocode"],
    "package_tracking": ["package", "parcel", "shipment", "delivery", "tracking", "track", "awb", "mail", "correo"],
    "container_tracking": ["container", "vessel", "shipment container"],
    "postal_address": ["postal", "zip", "postcode", "address", "cep", "city", "longitude", "latitude", "geocode"],
    "news": ["news", "article", "headline"],
    "image_media": ["image", "picture", "photo", "thumbnail"],
    "social_profile": ["profile", "linkedin", "instagram", "twitter", "youtube", "company data"],
    "product_commerce": ["product", "price", "reviews", "specification", "sku", "barcode", "amazon"],
    "recipe_food": ["recipe", "ingredient", "cocktail", "cooking"],
    "property_real_estate": ["property", "transaction", "real estate", "parcel"],
    "subtitle_format": ["subtitle", "srt", "json", "text format", "caption"],
    "health_status": ["health", "quota", "status", "checkhealth", "authentication system"],
    "webhook": ["webhook"],
    "finance_market": ["stock", "finance", "ipo", "market", "gdp", "company", "ticker"],
    "sports": ["sports", "match", "odds", "team", "league"],
    "music_event": ["concert", "music", "artist", "song"],
    "zoo_animal": ["zoo", "animal"],
}

DOMAIN_HINTS = {
    "weather_forecast": {"weather_forecast"},
    "translation": {"translation"},
    "traffic_route": {"traffic_route", "postal_address"},
    "package_tracking": {"package_tracking", "container_tracking"},
    "container_tracking": {"container_tracking", "package_tracking"},
    "postal_address": {"postal_address", "traffic_route"},
    "restaurant_place_reviews": {"restaurant_place_reviews", "hotel_venue_event"},
    "hotel_venue_event": {"hotel_venue_event", "restaurant_place_reviews", "social_profile"},
    "social_profile": {"social_profile"},
    "recipe_food": {"recipe_food"},
    "news": {"news"},
    "image_media": {"image_media"},
    "product_commerce": {"product_commerce"},
    "subtitle_format": {"subtitle_format"},
    "health_status": {"health_status"},
    "finance_market": {"finance_market"},
    "sports": {"sports"},
    "music_event": {"music_event"},
    "zoo_animal": {"zoo_animal"},
}


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, rows: Sequence[Dict[str, object]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = fieldnames_union(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fieldnames_union(rows: Sequence[Dict[str, object]]) -> List[str]:
    cols: List[str] = []
    for row in rows:
        for key in row:
            if key not in cols:
                cols.append(key)
    return cols


def markdown_table(rows: Sequence[Dict[str, object]], cols: Sequence[str], max_rows: int = 60) -> List[str]:
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for row in rows[:max_rows]:
        vals: List[str] = []
        for col in cols:
            text = str(row.get(col, "")).replace("\n", " ").replace("|", "/")
            vals.append(text[:177] + "..." if len(text) > 180 else text)
        lines.append("| " + " | ".join(vals) + " |")
    if not rows:
        lines.append("| " + " | ".join("-" for _ in cols) + " |")
    return lines


def count_by(rows: Sequence[Dict[str, object]], key: str) -> Dict[str, int]:
    return dict(sorted(Counter(str(row.get(key, "not_available")) for row in rows).items()))


def distribution_rows(rows: Sequence[Dict[str, object]], key: str) -> List[Dict[str, object]]:
    return [{"value": k, "count": v} for k, v in count_by(rows, key).items()]


def parse_json(raw: str) -> object:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def parse_list(raw: str) -> List[object]:
    data = parse_json(raw)
    return data if isinstance(data, list) else []


def record_texts(row: Dict[str, str], gold_only: bool = True) -> List[str]:
    texts: List[str] = []
    texts.extend(str(x) for x in parse_list(row.get("gold_services_json", "")) if x)
    for api in parse_list(row.get("gold_apis_json", "")):
        if isinstance(api, dict):
            texts.extend(
                str(api.get(k, ""))
                for k in ["api_name", "api_description", "service_name", "description"]
                if api.get(k)
            )
        elif api:
            texts.append(str(api))
    if not gold_only:
        for key in ["candidate_services_json", "candidate_apis_json"]:
            for item in parse_list(row.get(key, "")):
                if isinstance(item, dict):
                    texts.extend(str(v) for v in item.values() if isinstance(v, str))
                elif item:
                    texts.append(str(item))
    return texts


def normalize(text: str) -> str:
    text = str(text or "").lower()
    text = text.replace("_", " ").replace("-", " ").replace("/", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text)).strip()


def extract_requirements(text: str) -> List[str]:
    normalized = normalize(text)
    found: List[str] = []
    for capability, patterns in CAPABILITY_PATTERNS.items():
        for pattern in patterns:
            if normalize(pattern) in normalized:
                found.append(capability)
                break
    return sorted(set(found))


def extract_gold_capabilities(row: Dict[str, str]) -> List[str]:
    text = " ".join(record_texts(row, gold_only=True))
    found = extract_requirements(text)
    if found:
        return found
    # Last resort: use candidate text as weak evidence only when gold names are too generic.
    return []


def obvious_special_mismatch(core: set[str], covered: set[str]) -> Tuple[bool, str]:
    if "package_tracking" in core and covered == {"container_tracking"}:
        return True, "wrong_entity_scope"
    if "container_tracking" in core and covered == {"package_tracking"}:
        return True, "wrong_entity_scope"
    if "restaurant_place_reviews" in core and "product_commerce" in covered and "restaurant_place_reviews" not in covered:
        return True, "wrong_entity_scope"
    if "music_event" in core and not (covered & {"music_event", "news"}):
        return True, "missing_required_capability"
    if "zoo_animal" in core and not (covered & {"zoo_animal", "restaurant_place_reviews", "hotel_venue_event"}):
        return True, "wrong_service_domain"
    return False, "none"


def semcap_predict(row: Dict[str, str], record_id: str) -> Dict[str, object]:
    query = row.get("query_text", "")
    core = set(extract_requirements(query))
    covered_raw = set(extract_gold_capabilities(row))
    covered: set[str] = set()
    for req in core:
        allowed = DOMAIN_HINTS.get(req, {req})
        if covered_raw & allowed:
            covered.add(req)
    missing = sorted(core - covered)
    special_mismatch, mismatch_type = obvious_special_mismatch(core, covered_raw)
    gold_text = " ".join(record_texts(row, gold_only=True)).strip()
    gold_desc_sparse = len(normalize(gold_text).split()) < 4

    if not core:
        semantic = "uncertain"
        sem_conf = "low"
        sem_type = "not_enough_information"
        sem_reason = "No supported core requirement keyword was extracted from the query."
        coverage = "coverage_uncertain"
        cov_conf = "low"
        cov_type = "not_enough_information"
        cov_reason = "Query requirements were not extractable by the pilot heuristic."
    elif special_mismatch:
        semantic = "mismatch"
        sem_conf = "high"
        sem_type = mismatch_type
        sem_reason = f"Core requirements {sorted(core)} conflict with gold capabilities {sorted(covered_raw)}."
        coverage = "coverage_mismatch"
        cov_conf = "high"
        cov_type = mismatch_type
        cov_reason = "Gold capability appears to cover a related but wrong entity/domain."
    elif covered == core:
        semantic = "ok"
        sem_conf = "medium"
        sem_type = "none"
        sem_reason = "Gold service/API text covers all extracted query requirement categories."
        coverage = "coverage_uncertain"
        cov_conf = "medium"
        cov_type = "insufficient_description" if gold_desc_sparse else "none"
        cov_reason = "All extracted core requirements appear covered, but this heuristic pilot keeps capability coverage uncertain until human/LLM validation."
    elif covered:
        semantic = "uncertain"
        sem_conf = "medium"
        sem_type = "partial_match_only"
        sem_reason = f"Gold covers {sorted(covered)} but misses {missing}."
        coverage = "coverage_uncertain"
        cov_conf = "medium"
        cov_type = "gold_only_partial"
        cov_reason = f"Partial coverage only; missing requirements: {missing}."
    else:
        semantic = "mismatch"
        sem_conf = "medium"
        sem_type = "domain_mismatch"
        sem_reason = f"No gold capability matched extracted query requirements {sorted(core)}."
        coverage = "coverage_mismatch"
        cov_conf = "medium"
        cov_type = "missing_required_capability"
        cov_reason = "Gold service/API text does not cover any extracted core requirement."

    requires_review = not (
        semantic == "ok" and sem_conf == "high" and coverage == "coverage_ok" and cov_conf == "high"
    )
    return {
        "record_id": record_id,
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_group": row.get("source_group", ""),
        "query_text": query,
        "semantic_alignment_pred": semantic,
        "semantic_alignment_confidence": sem_conf,
        "semantic_alignment_reason": sem_reason,
        "semantic_mismatch_type": sem_type,
        "capability_coverage_pred": coverage,
        "capability_coverage_confidence": cov_conf,
        "core_requirements_json": json.dumps(sorted(core), ensure_ascii=False),
        "covered_requirements_json": json.dumps(sorted(covered), ensure_ascii=False),
        "missing_requirements_json": json.dumps(missing, ensure_ascii=False),
        "capability_mismatch_type": cov_type,
        "capability_coverage_reason": cov_reason,
        "requires_human_review": str(requires_review).lower(),
        "detector_version": "v0.9_heuristic_pilot",
        "gold_services_json": row.get("gold_services_json", ""),
        "gold_apis_json": row.get("gold_apis_json", ""),
        "candidate_services_json": row.get("candidate_services_json", ""),
        "candidate_apis_json": row.get("candidate_apis_json", ""),
    }


def confidence_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(str(value), 0)


def apply_pilot_policy(row: Dict[str, str], pred: Dict[str, str]) -> Dict[str, object]:
    blocking: List[str] = []
    warnings: List[str] = []
    rules: List[str] = []
    api_leak = row.get("api_leak_detector_status", "")
    candidate_space = row.get("candidate_space_status", "")
    service_in = row.get("gold_in_candidate_services", "")
    api_in = row.get("gold_in_candidate_apis", "")
    semantic = pred.get("semantic_alignment_pred", "")
    sem_conf = pred.get("semantic_alignment_confidence", "")
    coverage = pred.get("capability_coverage_pred", "")
    cov_conf = pred.get("capability_coverage_confidence", "")
    level = row.get("prediction_level", "")

    if api_leak == "api_leak_blocking":
        decision = "pilot_remove"
        bucket = "remove_api_leak"
        blocking.append("strong_api_leak")
    elif service_in == "no" or (level == "api" and api_in == "no"):
        decision = "pilot_remove"
        bucket = "remove_gold_missing"
        blocking.append("gold_missing")
    elif candidate_space in {"invalid_service_no_choice_space", "invalid_api_no_choice_space"}:
        decision = "pilot_remove"
        bucket = "remove_choice_space_invalid"
        blocking.append(candidate_space)
    elif semantic == "mismatch" or coverage == "coverage_mismatch":
        decision = "pilot_remove"
        bucket = "remove_semantic_or_capability_mismatch"
        blocking.append("semantic_or_capability_mismatch")
    elif semantic == "ok" and coverage == "coverage_ok" and sem_conf == "high" and cov_conf == "high":
        decision = "pilot_keep_candidate"
        bucket = "pilot_clean_ready_candidate_requires_validation"
        rules.append("high_confidence_semantic_and_capability_ok")
    else:
        decision = "pilot_uncertain"
        bucket = "needs_human_or_llm_review"
        warnings.append("semcap_not_high_confidence_ok")
    return {
        **row,
        **{f"pilot_{k}": v for k, v in pred.items() if k not in {"task_id", "task_type", "source_group", "query_text"}},
        "policy_decision_pilot": decision,
        "policy_bucket_pilot": bucket,
        "pilot_blocking_reasons": ";".join(blocking),
        "pilot_warning_reasons": ";".join(warnings),
        "pilot_triggered_rules": ";".join(rules),
        "requires_human_review_pilot": "false" if decision == "pilot_keep_candidate" else "true",
    }


def pct(num: int, den: int) -> str:
    return "0.0%" if den == 0 else f"{num / den * 100:.1f}%"


def archive_v0_9() -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    scripts = [
        Path("scripts/validation/check_v0_9_inputs.py"),
        Path("scripts/validation/build_semcap_calibration_set_v0_9.py"),
        Path("scripts/validation/run_semcap_heuristic_detector_v0_9.py"),
        Path("scripts/validation/evaluate_semcap_detector_on_round3_v0_9.py"),
        Path("scripts/validation/build_semcap_llm_prompt_pack_v0_9.py"),
        Path("scripts/validation/apply_semcap_pilot_to_v0_8_sample_v0_9.py"),
        Path("scripts/validation/prepare_semcap_pilot_review_set_v0_9.py"),
        Path("scripts/validation/semcap_detector_v0_9_utils.py"),
    ]
    for src in scripts:
        if src.exists():
            dst = ARCHIVE_DIR / src
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(dst))
    if OUTPUT_DIR.exists():
        dst = ARCHIVE_DIR / "outputs_semantic_capability_detector_pilot_v0_9"
        shutil.copytree(OUTPUT_DIR, dst, dirs_exist_ok=True)
        copied.append(str(dst))
    docs = [
        DOCS_DIR / "semantic_capability_detector_schema_v0_9.md",
        DOCS_DIR / "semcap_calibration_set_report_v0_9.md",
        DOCS_DIR / "semcap_heuristic_detector_report_v0_9.md",
        DOCS_DIR / "semcap_detector_round3_eval_report_v0_9.md",
        DOCS_DIR / "v0_8_sample_semcap_pilot_policy_trace_report_v0_9.md",
        DOCS_DIR / "semantic_capability_detector_pilot_v0_9_go_no_go_report.md",
    ]
    docs_dest = ARCHIVE_DIR / "docs_phase1"
    for src in docs:
        if src.exists():
            dst = docs_dest / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(dst))
    manifest = ARCHIVE_DIR / "ARCHIVE_MANIFEST.md"
    lines = [
        "# Archive Manifest: semcap_detector_pilot_v0_9",
        "",
        f"Generated time: {now_str()}",
        f"Archive directory: `{ARCHIVE_DIR}`",
        "",
        "Scope: archived v0.9 semantic/capability detector pilot artifacts only.",
        "",
        "No full cleaning, final clean dataset, split, baseline, or model training was run.",
        "",
        "Detector predictions are not human final labels.",
        "",
        "## Files",
        "",
    ]
    for item in copied:
        lines.append(f"- `{item}`")
    manifest.write_text("\n".join(lines), encoding="utf-8")
    return manifest


def html_review_app(rows: Sequence[Dict[str, object]]) -> str:
    data_json = json.dumps(list(rows), ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>SemCap Pilot Review 80</title>
<style>
body {{ margin: 0; font-family: Arial, 'Microsoft YaHei', sans-serif; background: #f6f7f9; color: #1f2937; }}
header {{ padding: 14px 18px; background: #1f2937; color: white; display: flex; gap: 12px; align-items: center; }}
button, select, input, textarea {{ font: inherit; }}
button {{ padding: 7px 10px; border: 1px solid #9ca3af; background: white; border-radius: 4px; cursor: pointer; }}
.layout {{ display: grid; grid-template-columns: 320px 1fr; height: calc(100vh - 56px); }}
.list {{ overflow: auto; border-right: 1px solid #d1d5db; background: white; }}
.item {{ padding: 10px; border-bottom: 1px solid #e5e7eb; cursor: pointer; }}
.item.active {{ background: #e0f2fe; }}
.main {{ overflow: auto; padding: 18px; }}
.panel {{ background: white; border: 1px solid #d1d5db; border-radius: 6px; padding: 14px; margin-bottom: 12px; }}
.grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
pre {{ white-space: pre-wrap; word-break: break-word; background: #f3f4f6; padding: 10px; border-radius: 4px; max-height: 220px; overflow: auto; }}
label {{ display: block; font-weight: 600; margin: 8px 0 4px; }}
textarea {{ width: 100%; height: 80px; }}
select {{ width: 100%; padding: 6px; }}
.muted {{ color: #6b7280; font-size: 13px; }}
</style>
</head>
<body>
<header>
<strong>SemCap Pilot Review 80</strong>
<button onclick="prevItem()">上一条</button>
<button onclick="nextItem()">下一条</button>
<button onclick="exportCsv()">Export decisions CSV</button>
<span id="pos"></span>
</header>
<div class="layout">
<aside class="list" id="list"></aside>
<main class="main" id="main"></main>
</div>
<script>
const rows = {data_json};
let idx = 0;
const fields = ['manual_final_decision','semantic_alignment_check','capability_coverage_check','leakage_check','candidate_validity_check','task_type_check','human_notes'];
function key(i) {{ return 'semcap_v09_' + rows[i].review_item_id; }}
function saved(i) {{ return JSON.parse(localStorage.getItem(key(i)) || '{{}}'); }}
function save(i, field, value) {{ const s=saved(i); s[field]=value; localStorage.setItem(key(i), JSON.stringify(s)); renderList(); }}
function renderList() {{
 const list=document.getElementById('list'); list.innerHTML='';
 rows.forEach((r,i)=>{{ const s=saved(i); const div=document.createElement('div'); div.className='item'+(i===idx?' active':''); div.onclick=()=>{{idx=i;render();}};
 div.innerHTML=`<strong>${{r.review_item_id}}</strong><br>${{r.task_id}}<br><span class="muted">${{r.review_bucket}} / ${{s.manual_final_decision||'unfilled'}}</span>`;
 list.appendChild(div); }});
}}
function val(i,f) {{ return saved(i)[f] || ''; }}
function selectHtml(f, opts) {{ return `<label>${{f}}</label><select onchange="save(idx,'${{f}}',this.value)"><option value=""></option>${{opts.map(o=>`<option value="${{o}}" ${{val(idx,f)===o?'selected':''}}>${{o}}</option>`).join('')}}</select>`; }}
function render() {{
 const r=rows[idx]; document.getElementById('pos').textContent=`${{idx+1}} / ${{rows.length}}`;
 document.getElementById('main').innerHTML = `
 <div class="panel"><h2>${{r.review_item_id}} | ${{r.task_id}}</h2><p><b>Task:</b> ${{r.task_type}} / ${{r.source_group}}</p><p>${{r.query_text}}</p></div>
 <div class="grid">
 <div class="panel"><h3>Gold Services</h3><pre>${{r.gold_services_json}}</pre></div>
 <div class="panel"><h3>Gold APIs</h3><pre>${{r.gold_apis_json}}</pre></div>
 <div class="panel"><h3>Candidate Services</h3><pre>${{r.candidate_services_json}}</pre></div>
 <div class="panel"><h3>Candidate APIs</h3><pre>${{r.candidate_apis_json}}</pre></div>
 </div>
 <div class="panel"><h3>Heuristic Prediction</h3>
 <p><b>semantic:</b> ${{r.semantic_alignment_pred}} (${{r.semantic_alignment_confidence}}) - ${{r.semantic_alignment_reason}}</p>
 <p><b>capability:</b> ${{r.capability_coverage_pred}} (${{r.capability_coverage_confidence}}) - ${{r.capability_coverage_reason}}</p>
 <p><b>core:</b> ${{r.core_requirements_json}}</p><p><b>covered:</b> ${{r.covered_requirements_json}}</p><p><b>missing:</b> ${{r.missing_requirements_json}}</p>
 <p><b>pilot policy:</b> ${{r.policy_decision_pilot || ''}}</p></div>
 <div class="panel"><h3>人工审核</h3>
 <p class="muted">判断顺序：query 真正要什么 -> gold service/API 是否语义同域 -> gold 是否覆盖核心能力 -> leak/candidate/task type 是否阻断。不确定就填 uncertain，不要强行 keep。</p>
 ${{selectHtml('manual_final_decision',['keep_for_cleaning_candidate','remove','uncertain'])}}
 ${{selectHtml('semantic_alignment_check',['ok','uncertain','mismatch'])}}
 ${{selectHtml('capability_coverage_check',['coverage_ok','coverage_uncertain','coverage_mismatch'])}}
 ${{selectHtml('leakage_check',['no_blocking','api_leak_blocking','service_leak_only','ambiguous'])}}
 ${{selectHtml('candidate_validity_check',['valid','uncertain','invalid','insufficient_choice_space'])}}
 ${{selectHtml('task_type_check',['valid_multi_service','valid_multi_api','valid','uncertain','invalid'])}}
 <label>human_notes</label><textarea onchange="save(idx,'human_notes',this.value)">${{val(idx,'human_notes')}}</textarea>
 </div>`;
 renderList();
}}
function prevItem() {{ idx=Math.max(0,idx-1); render(); }}
function nextItem() {{ idx=Math.min(rows.length-1,idx+1); render(); }}
function csvEscape(v) {{ v=(v??'').toString(); return '"' + v.replaceAll('"','""') + '"'; }}
function exportCsv() {{
 const cols = Object.keys(rows[0]).concat(fields.filter(f=>!Object.keys(rows[0]).includes(f)));
 const lines=[cols.join(',')];
 rows.forEach((r,i)=>{{ const s=saved(i); lines.push(cols.map(c=>csvEscape(s[c] ?? r[c] ?? '')).join(',')); }});
 const blob=new Blob([lines.join('\\n')], {{type:'text/csv;charset=utf-8'}});
 const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='semcap_pilot_review_80_filled.csv'; a.click();
}}
render();
</script>
</body>
</html>"""
