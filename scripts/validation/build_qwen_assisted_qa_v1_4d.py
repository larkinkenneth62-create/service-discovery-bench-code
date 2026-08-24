from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path

from qwen_semcap_v1_4d_common import (
    DOC_DIR,
    OUTPUT_DIR,
    QA_DIR,
    QA_HUMAN_FIELDS,
    distribution,
    read_csv,
    stable_score,
    table_lines,
    write_csv,
    write_md,
)


QUOTAS = {
    "random_QWEN_clean_candidate": 30,
    "generic_search_news_image_risk": 15,
    "travel_place_hotel_restaurant_venue_risk": 15,
    "finance_currency_crypto_stock_risk": 10,
    "weather_location_geocoding_risk": 10,
    "social_media_profile_content_risk": 10,
    "G3_or_composable_raw_risk": 10,
}

RISK_TERMS = {
    "generic_search_news_image_risk": ["search", "news", "image", "photo", "logo", "article", "web search", "video"],
    "travel_place_hotel_restaurant_venue_risk": ["travel", "hotel", "restaurant", "venue", "nearby", "place", "attraction", "gas station"],
    "finance_currency_crypto_stock_risk": ["currency", "exchange rate", "crypto", "bitcoin", "stock", "finance", "market"],
    "weather_location_geocoding_risk": ["weather", "forecast", "location", "geocode", "address", "coordinates", "air quality"],
    "social_media_profile_content_risk": ["social", "instagram", "tiktok", "twitter", "profile", "feed", "influencer"],
}


def infer_subbucket(row: dict[str, str]) -> tuple[str, list[str]]:
    text = " ".join(str(row.get(field, "") or "").lower() for field in ["query_text", "candidate_services_json", "candidate_apis_json", "gold_services_json", "gold_apis_json"])
    if row.get("source_group") == "G3" or "composable" in row.get("task_type", "").lower():
        return "G3_or_composable_raw_risk", ["G3_or_composable"]
    for bucket, terms in RISK_TERMS.items():
        hits = [term for term in terms if term in text]
        if hits:
            return bucket, hits
    return "random_QWEN_clean_candidate", []


def select_rows(pool: list[dict[str, str]]) -> tuple[list[dict[str, str]], Counter, list[str]]:
    pools = {bucket: [] for bucket in QUOTAS}
    for row in pool:
        bucket, hits = infer_subbucket(row)
        row["_qa_subbucket"] = bucket
        row["_risk_keywords"] = ";".join(hits)
        pools.setdefault(bucket, []).append(row)
    for bucket, rows in pools.items():
        rows.sort(key=lambda row: stable_score("QWEN_v1_4d", bucket, row.get("task_id", "")))
    selected = []
    selected_ids = set()
    counts = Counter()
    notes = []
    for bucket, target in QUOTAS.items():
        added = 0
        for row in pools.get(bucket, []):
            if row.get("task_id") in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row.get("task_id", ""))
            added += 1
            if added >= target:
                break
        counts[bucket] = added
        if added < target:
            notes.append(f"{bucket}: target {target}, selected {added}; deficit will be backfilled.")
    if len(selected) < 100:
        remaining = [row for row in pool if row.get("task_id") not in selected_ids]
        remaining.sort(key=lambda row: stable_score("QWEN_backfill", row.get("_qa_subbucket", ""), row.get("task_id", "")))
        for row in remaining:
            selected.append(row)
            selected_ids.add(row.get("task_id", ""))
            counts[f"backfill::{row.get('_qa_subbucket', '')}"] += 1
            if len(selected) >= 100:
                break
    return selected[:100], counts, notes


def review_row(row: dict[str, str], index: int) -> dict[str, str]:
    out = {
        "qa_item_id": f"DSQA-1.4D-{index:03d}",
        "qa_bucket": "qwen_assisted_clean_candidate_audit",
        "qa_subbucket": row.get("_qa_subbucket", ""),
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_dataset": row.get("source_dataset", ""),
        "source_group": row.get("source_group", ""),
        "prediction_level": row.get("prediction_level", ""),
        "query_text": row.get("query_text", ""),
        "candidate_services_json": row.get("candidate_services_json", ""),
        "candidate_apis_json": row.get("candidate_apis_json", ""),
        "gold_services_json": row.get("gold_services_json", ""),
        "gold_apis_json": row.get("gold_apis_json", ""),
        "candidate_service_count": row.get("candidate_service_count", ""),
        "gold_service_count": row.get("gold_service_count", ""),
        "candidate_api_count": row.get("candidate_api_count", ""),
        "gold_api_count": row.get("gold_api_count", ""),
        "v1_4c_dryrun_decision": row.get("dryrun_decision_v1_4c", ""),
        "qwen_assisted_decision_v1_4d": row.get("qwen_assisted_decision_v1_4d", ""),
        "qwen_assisted_bucket_v1_4d": row.get("qwen_assisted_bucket_v1_4d", ""),
        "QWEN_semantic_alignment_check": row.get("QWEN_semantic_alignment_check", ""),
        "QWEN_capability_coverage_check": row.get("QWEN_capability_coverage_check", ""),
        "QWEN_missing_requirements_json": row.get("QWEN_missing_requirements_json", ""),
        "QWEN_extra_unrelated_gold_services_json": row.get("QWEN_extra_unrelated_gold_services_json", ""),
        "QWEN_generic_search_overtrust": row.get("QWEN_generic_search_overtrust", ""),
        "QWEN_domain_specific_gap": row.get("QWEN_domain_specific_gap", ""),
        "QWEN_wrong_gold_set": row.get("QWEN_wrong_gold_set", ""),
        "QWEN_decision_risk_level": row.get("QWEN_decision_risk_level", ""),
        "QWEN_reason": row.get("QWEN_reason", ""),
        "risk_keywords_matched": row.get("_risk_keywords", ""),
    }
    for field in QA_HUMAN_FIELDS:
        out[field] = ""
    return out


def build_html(rows: list[dict[str, str]]) -> str:
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    fields_json = json.dumps(QA_HUMAN_FIELDS)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>QWEN-assisted Final QA v1.4d</title>
<style>
body {{ margin:0; font-family: Arial, sans-serif; background:#f6f7f9; color:#17202a; }}
.layout {{ display:grid; grid-template-columns: 330px 1fr; height:100vh; }}
.sidebar {{ overflow:auto; border-right:1px solid #d0d7de; background:#fff; padding:14px; }}
.main {{ overflow:auto; padding:20px; }}
.item {{ padding:8px; border-bottom:1px solid #eee; cursor:pointer; }}
.item.active {{ background:#e8f2ff; }}
.panel {{ background:#fff; border:1px solid #d0d7de; border-radius:6px; padding:14px; margin-bottom:14px; }}
textarea {{ width:100%; min-height:70px; }}
select {{ width:100%; padding:6px; margin-bottom:8px; }}
button {{ padding:8px 10px; margin-right:8px; }}
pre {{ white-space:pre-wrap; word-break:break-word; background:#f3f4f6; padding:10px; border-radius:4px; }}
</style>
</head>
<body>
<div class="layout"><aside class="sidebar"><button onclick="exportCsv()">Export decisions CSV</button><div id="list"></div></aside><main class="main" id="main"></main></div>
<script>
const DATA={data};
const HUMAN_FIELDS={fields_json};
let idx=0;
const STORE='QWEN_final_qa_v1_4d';
let decisions=JSON.parse(localStorage.getItem(STORE)||'{{}}');
function save(){{ localStorage.setItem(STORE, JSON.stringify(decisions)); }}
function esc(s){{ return String(s||'').replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c])); }}
function dec(row){{ return decisions[row.qa_item_id]||{{}}; }}
function renderList(){{ document.getElementById('list').innerHTML=DATA.map((r,i)=>`<div class="item ${{i===idx?'active':''}}" onclick="idx=${{i}};render()">${{esc(r.qa_item_id)}} | ${{esc(r.task_id)}}<br>${{esc(r.qa_subbucket)}}</div>`).join(''); }}
function field(row,f){{ const v=dec(row)[f]||''; const opts={{qa_final_decision:['','pass','fail','uncertain'],qa_semantic_alignment_check:['','ok','uncertain','mismatch','not_applicable'],qa_capability_coverage_check:['','coverage_ok','coverage_uncertain','coverage_mismatch','not_applicable'],qa_leakage_check:['','no_blocking','api_leak_blocking','service_leak_only','ambiguous'],qa_candidate_validity_check:['','valid','insufficient_choice_space','uncertain','not_applicable'],qa_task_type_check:['','valid','invalid','uncertain','not_applicable'],qa_dedup_check:['','unique','duplicate_ok','duplicate_should_remove','uncertain'],qa_error_type:['','none','api_leak','service_leak','choice_space_invalid','gold_missing','semantic_mismatch','capability_mismatch','wrong_gold_set','generic_search_overtrust','domain_specific_gap','duplicate_issue','wrong_bucket','unclear'],qa_severity:['','none','minor','major','critical']}}[f]||['']; return `<label>${{f}}</label><select onchange="setField('${{row.qa_item_id}}','${{f}}',this.value)">${{opts.map(o=>`<option value="${{o}}" ${{o===v?'selected':''}}>${{o}}</option>`).join('')}}</select>`; }}
function setField(id,f,v){{ decisions[id]=decisions[id]||{{}}; decisions[id][f]=v; save(); }}
function setNotes(id,v){{ decisions[id]=decisions[id]||{{}}; decisions[id].qa_notes=v; save(); }}
function render(){{ renderList(); const r=DATA[idx]; document.getElementById('main').innerHTML=`<div class="panel"><button onclick="idx=Math.max(0,idx-1);render()">Prev</button><button onclick="idx=Math.min(DATA.length-1,idx+1);render()">Next</button><h2>${{esc(r.qa_item_id)}} | ${{esc(r.task_id)}}</h2><p>${{esc(r.qa_subbucket)}}</p></div><div class="panel"><h3>Query</h3><p>${{esc(r.query_text)}}</p></div><div class="panel"><h3>Gold / Candidates</h3><pre>${{esc(r.gold_services_json)}}\\n${{esc(r.gold_apis_json)}}\\n\\n${{esc(r.candidate_services_json)}}\\n${{esc(r.candidate_apis_json)}}</pre></div><div class="panel"><h3>QWEN SemCap Evidence</h3><pre>${{esc(JSON.stringify({{semantic:r.QWEN_semantic_alignment_check,coverage:r.QWEN_capability_coverage_check,missing:r.QWEN_missing_requirements_json,extra:r.QWEN_extra_unrelated_gold_services_json,generic:r.QWEN_generic_search_overtrust,domain_gap:r.QWEN_domain_specific_gap,wrong_gold:r.QWEN_wrong_gold_set,reason:r.QWEN_reason}},null,2))}}</pre></div><div class="panel"><h3>Human QA</h3>${{HUMAN_FIELDS.filter(f=>f!=='qa_notes').map(f=>field(r,f)).join('')}}<label>qa_notes</label><textarea onchange="setNotes('${{r.qa_item_id}}',this.value)">${{esc(dec(r).qa_notes||'')}}</textarea></div>`; }}
function csvEscape(v){{ v=String(v||''); return /[",\\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v; }}
function exportCsv(){{ const headers=Object.keys(DATA[0]||{{}}); const lines=[headers.join(',')]; DATA.forEach(r=>{{ const merged={{...r,...dec(r)}}; lines.push(headers.map(h=>csvEscape(merged[h])).join(',')); }}); const blob=new Blob(['\\ufeff'+lines.join('\\n')],{{type:'text/csv;charset=utf-8'}}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='final_qa_review_items_QWEN_v1_4d_user_reviewed.csv'; a.click(); }}
render();
</script>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build QWEN-assisted v1.4d final QA review package.")
    parser.add_argument("--trace", type=Path, default=OUTPUT_DIR / "qwen_assisted_clean_task_trace_v1_4d.csv")
    parser.add_argument("--regression-summary", type=Path, default=OUTPUT_DIR / "regression/QWEN_regression_summary_v1_4d.json")
    parser.add_argument("--output-csv", type=Path, default=QA_DIR / "final_qa_review_items_QWEN_v1_4d.csv")
    parser.add_argument("--output-html", type=Path, default=QA_DIR / "final_qa_review_app_QWEN_v1_4d.html")
    args = parser.parse_args()
    if not args.trace.exists():
        raise FileNotFoundError(f"Missing QWEN-assisted trace: {args.trace}")
    if not args.regression_summary.exists():
        raise FileNotFoundError(f"Missing regression summary: {args.regression_summary}")
    rows = read_csv(args.trace)
    pool = [row for row in rows if row.get("qwen_assisted_decision_v1_4d") == "qwen_assisted_clean_candidate"]
    selected, counts, notes = select_rows(pool)
    if len(selected) != 100:
        raise RuntimeError(f"Expected 100 QWEN-assisted QA rows, got {len(selected)}")
    review_rows = [review_row(row, i) for i, row in enumerate(selected, 1)]
    fieldnames = list(review_rows[0].keys()) if review_rows else []
    write_csv(args.output_csv, review_rows, fieldnames)
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(build_html(review_rows), encoding="utf-8-sig")
    nonblank_human = sum(1 for row in review_rows for field in QA_HUMAN_FIELDS if row.get(field))
    write_md(
        DOC_DIR / "qwen_assisted_final_qa_protocol_v1_4d.md",
        [
            "# QWEN-Assisted Final QA Protocol v1.4d",
            "",
            f"Input trace: `{args.trace}`",
            f"Output CSV: `{args.output_csv}`",
            f"Output HTML: `{args.output_html}`",
            f"Sample count: {len(review_rows)}",
            "",
            "QWEN evidence is advisory. Human QA fields must be filled manually.",
            "No final clean data, split, baseline, or training is generated here.",
            "",
            "## QA Subbucket Distribution",
            "",
            *table_lines(distribution(review_rows, "qa_subbucket")),
            "",
            "## Backfill Notes",
            "",
            *([f"- {note}" for note in notes] if notes else ["- None"]),
        ],
    )
    print(f"QWEN assisted QA rows: {len(review_rows)}")
    print(f"nonblank human QA fields: {nonblank_human}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


