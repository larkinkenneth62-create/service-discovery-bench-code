#!/usr/bin/env python
"""Prepare Round3 targeted validation set and review HTML for v0.6."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from rule_revision_v0_6_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    ROUND2_CANDIDATE_POOL_PATHS,
    ROUND2_V05_DIR,
    get_count,
    html_escape,
    js_string,
    json_names,
    load_round2_final,
    missing_required_inputs,
    now_str,
    pct,
    query_zh_hint,
    read_csv,
    write_csv,
    write_missing_inputs,
)


ROUND3_CSV = OUTPUT_DIR / "round3_targeted_validation_items_100.csv"
ROUND3_REPORT = OUTPUT_DIR / "round3_targeted_validation_sampling_report.md"
ROUND3_HTML = OUTPUT_DIR / "round3_targeted_review_app_100.html"
GO_NO_GO_MD = DOCS_DIR / "round2_rule_revision_v0_6_go_no_go_report.md"

FAILURE_SUMMARY = OUTPUT_DIR / "failure_mode_summary_v0_6.csv"
RULE_SUMMARY = OUTPUT_DIR / "rule_replay_v0_6_summary.json"

TARGET_CATEGORIES = [
    "rule_keep_candidate",
    "endpoint_or_carrier_specific_api_leak_risk",
    "generic_weak_leak_false_positive_risk",
    "capability_coverage_risk",
    "api_level_single_service_boundary",
]


def load_pool_rows() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for path in ROUND2_CANDIDATE_POOL_PATHS:
        if not path.exists():
            continue
        _, file_rows = read_csv(path)
        source = path.stem
        for row in file_rows:
            row = dict(row)
            row["_pool_source"] = source
            rows.append(row)
    return rows


def row_key(row: Dict[str, str]) -> str:
    return f"{row.get('task_type','')}::{row.get('task_id','')}::{row.get('query_signature','')}"


def has_endpoint_pattern(text: str) -> bool:
    return bool(re.search(r"/|:|_|create_task|result_task|correo argentino|oca|carrier", text or "", re.I))


def gold_api_text(row: Dict[str, str]) -> str:
    return f"{row.get('gold_apis_json','')} {row.get('candidate_apis_json','')}"


def is_generic_api_name_risk(row: Dict[str, str]) -> bool:
    text = gold_api_text(row).lower()
    generic_terms = ["latest", "all", "count", "list", "search", "get ", "about"]
    if not any(term in text for term in generic_terms):
        return False
    endpointish = bool(re.search(r"/|create_task|result_task|correo|oca|packages/track|carriers/detect", text, re.I))
    return not endpointish


def has_capability_risk(row: Dict[str, str]) -> bool:
    query = row.get("query_text", "")
    services = row.get("gold_services_json", "") + " " + row.get("gold_apis_json", "")
    if str(row.get("high_risk_generic_tracking", "")).lower() == "true":
        return True
    if str(row.get("high_risk_generic_address_or_postal", "")).lower() == "true":
        return True
    patterns = [
        r"\bzoo\b",
        r"\brestaurant",
        r"\bconcert",
        r"\bgas station",
        r"\blatitude|\blongitude|\bcoordinate",
        r"\bbookstore",
        r"\bvenue",
        r"\bpackage\b|\bparcel\b|\bmail\b",
        r"\bcontainer\b",
    ]
    if any(re.search(pattern, query, re.I) for pattern in patterns):
        if re.search(r"container", services, re.I) and re.search(r"package|parcel|mail", query, re.I):
            return True
        return True
    return False


def candidate_counts(row: Dict[str, str]) -> dict:
    return {
        "candidate_service_count": get_count(row, "candidate_service_count", "candidate_services_json"),
        "gold_service_count": get_count(row, "gold_service_count", "gold_services_json"),
        "candidate_api_count": get_count(row, "candidate_api_count", "candidate_apis_json"),
        "gold_api_count": get_count(row, "gold_api_count", "gold_apis_json"),
    }


def eligible_for_category(row: Dict[str, str], category: str) -> bool:
    counts = candidate_counts(row)
    leak = (row.get("leak_status") or "").strip()
    bucket = row.get("mechanical_screening_bucket", "")
    task = row.get("task_type", "")
    if category == "rule_keep_candidate":
        return (
            leak == "no_obvious_leak"
            and bucket == "high_confidence_candidate"
            and not has_capability_risk(row)
            and counts["candidate_api_count"] is not None
            and counts["gold_api_count"] is not None
            and counts["candidate_api_count"] > counts["gold_api_count"]
        )
    if category == "endpoint_or_carrier_specific_api_leak_risk":
        return (leak == "api_leak" or row.get("query_mentions_any_gold_api") == "1") and has_endpoint_pattern(
            row.get("query_text", "") + " " + gold_api_text(row)
        )
    if category == "generic_weak_leak_false_positive_risk":
        return (leak == "api_leak" or row.get("query_mentions_any_gold_api") == "1") and is_generic_api_name_risk(row)
    if category == "capability_coverage_risk":
        return has_capability_risk(row)
    if category == "api_level_single_service_boundary":
        return "api" in task and counts["candidate_service_count"] == 1
    return False


def risk_subtype(row: Dict[str, str], category: str) -> str:
    if category == "endpoint_or_carrier_specific_api_leak_risk":
        return "endpoint_leak_risk" if has_endpoint_pattern(gold_api_text(row)) else "carrier_specific_leak_risk"
    if category == "generic_weak_leak_false_positive_risk":
        return "generic_false_positive_risk"
    if category == "capability_coverage_risk":
        if str(row.get("high_risk_generic_tracking", "")).lower() == "true":
            return "capability_coverage_risk_generic_tracking"
        if str(row.get("high_risk_generic_address_or_postal", "")).lower() == "true":
            return "capability_coverage_risk_generic_address"
        return "capability_coverage_risk_query_service_mismatch"
    if category == "api_level_single_service_boundary":
        return "task_type_boundary_risk_api_single_service"
    return "clean_ready_candidate_check"


def rule_suggestion(category: str) -> str:
    suggestions = {
        "rule_keep_candidate": "If human confirms no leak, semantic ok, coverage ok, and choice space valid, keep candidate.",
        "endpoint_or_carrier_specific_api_leak_risk": "Remove only if endpoint/carrier/task-flow identity is truly exposed in query.",
        "generic_weak_leak_false_positive_risk": "Do not auto-remove generic terms; mark weak/nonblocking unless endpoint identity is clear.",
        "capability_coverage_risk": "Check whether gold services/APIs can actually satisfy the query; mismatch should remove.",
        "api_level_single_service_boundary": "For API-level, one service is not fatal if candidate_api_count > gold_api_count and coverage is ok.",
    }
    return suggestions[category]


def select_round3(rows: List[Dict[str, str]], target_each: int = 20) -> tuple[List[Dict[str, object]], list[str]]:
    reviewed = {f"{row.get('task_type','')}::{row.get('task_id','')}" for row in load_round2_final()}
    selected: List[Dict[str, object]] = []
    selected_keys: set[str] = set()
    notes: list[str] = []

    def sort_key(row: Dict[str, str]) -> tuple:
        counts = candidate_counts(row)
        return (
            row.get("source_group", ""),
            row.get("task_type", ""),
            counts.get("candidate_service_count") or 999,
            row.get("task_id", ""),
        )

    def choose_stratified(candidates: List[Dict[str, str]], category: str) -> List[Dict[str, str]]:
        candidates = sorted(candidates, key=sort_key)
        if category == "api_level_single_service_boundary":
            return candidates[:target_each]

        service_rows = [row for row in candidates if "service" in row.get("task_type", "")]
        api_rows = [row for row in candidates if "api" in row.get("task_type", "")]
        service_target = target_each // 2
        api_target = target_each - service_target
        chosen = service_rows[:service_target] + api_rows[:api_target]

        if len(chosen) < target_each:
            chosen_keys = {row_key(row) for row in chosen}
            for row in candidates:
                if row_key(row) not in chosen_keys:
                    chosen.append(row)
                    chosen_keys.add(row_key(row))
                if len(chosen) >= target_each:
                    break
        return chosen[:target_each]

    for category in TARGET_CATEGORIES:
        candidates = []
        for row in rows:
            key = row_key(row)
            task_key = f"{row.get('task_type','')}::{row.get('task_id','')}"
            if key in selected_keys or task_key in reviewed:
                continue
            if eligible_for_category(row, category):
                candidates.append(row)
        take = choose_stratified(candidates, category)
        if len(take) < target_each:
            notes.append(f"{category}: only found {len(take)} fresh candidates; filled all available.")
        for row in take:
            selected_keys.add(row_key(row))
            counts = candidate_counts(row)
            selected.append(
                {
                    **row,
                    "round3_review_id": f"R3-{len(selected)+1:03d}",
                    "risk_category": category,
                    "risk_subtype": risk_subtype(row, category),
                    "rule_suggestion": rule_suggestion(category),
                    "query_text_zh_hint": query_zh_hint(row.get("query_text", "")),
                    "candidate_services_display": json_names(row.get("candidate_services_json", ""), key="service_name"),
                    "candidate_apis_display": json_names(row.get("candidate_apis_json", ""), key="api_name"),
                    "gold_services_display": json_names(row.get("gold_services_json", ""), key="service_name"),
                    "gold_apis_display": json_names(row.get("gold_apis_json", ""), key="api_name"),
                    "candidate_service_count_resolved": counts["candidate_service_count"],
                    "gold_service_count_resolved": counts["gold_service_count"],
                    "candidate_api_count_resolved": counts["candidate_api_count"],
                    "gold_api_count_resolved": counts["gold_api_count"],
                    "manual_final_decision": "",
                    "semantic_alignment_check": "",
                    "capability_coverage_check": "",
                    "leakage_check": "",
                    "candidate_validity_check": "",
                    "task_type_check": "",
                    "human_notes": "",
                }
            )

    # Conservative fallback: if a category was short, fill with unreviewed boundary rows
    # while preserving the original category quota notes.
    if len(selected) < target_each * len(TARGET_CATEGORIES):
        for row in sorted(rows, key=sort_key):
            key = row_key(row)
            task_key = f"{row.get('task_type','')}::{row.get('task_id','')}"
            if key in selected_keys or task_key in reviewed:
                continue
            if len(selected) >= target_each * len(TARGET_CATEGORIES):
                break
            counts = candidate_counts(row)
            category = "capability_coverage_risk" if has_capability_risk(row) else "rule_keep_candidate"
            selected_keys.add(key)
            selected.append(
                {
                    **row,
                    "round3_review_id": f"R3-{len(selected)+1:03d}",
                    "risk_category": category,
                    "risk_subtype": risk_subtype(row, category),
                    "rule_suggestion": rule_suggestion(category),
                    "query_text_zh_hint": query_zh_hint(row.get("query_text", "")),
                    "candidate_services_display": json_names(row.get("candidate_services_json", ""), key="service_name"),
                    "candidate_apis_display": json_names(row.get("candidate_apis_json", ""), key="api_name"),
                    "gold_services_display": json_names(row.get("gold_services_json", ""), key="service_name"),
                    "gold_apis_display": json_names(row.get("gold_apis_json", ""), key="api_name"),
                    "candidate_service_count_resolved": counts["candidate_service_count"],
                    "gold_service_count_resolved": counts["gold_service_count"],
                    "candidate_api_count_resolved": counts["candidate_api_count"],
                    "gold_api_count_resolved": counts["gold_api_count"],
                    "manual_final_decision": "",
                    "semantic_alignment_check": "",
                    "capability_coverage_check": "",
                    "leakage_check": "",
                    "candidate_validity_check": "",
                    "task_type_check": "",
                    "human_notes": "",
                }
            )
        notes.append("Fallback fill used because one or more target categories had fewer than 20 fresh candidates.")
    return selected[: target_each * len(TARGET_CATEGORIES)], notes


def write_sampling_report(rows: List[Dict[str, object]], notes: list[str]) -> None:
    category = Counter(str(row.get("risk_category", "")) for row in rows)
    task = Counter(str(row.get("task_type", "")) for row in rows)
    group = Counter(str(row.get("source_group", "")) for row in rows)
    csc = Counter(str(row.get("candidate_service_count_resolved", "")) for row in rows)
    lines = [
        "# Round3 Targeted Validation Sampling Report v0.6",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
    ]
    for path in ROUND2_CANDIDATE_POOL_PATHS:
        lines.append(f"- `{path}`")
    lines.extend(
        [
            "",
            "## 样本数量",
            "",
            f"- selected rows: `{len(rows)}`",
            "",
            "## Risk Category Composition",
            "",
            "| category | count |",
            "|---|---:|",
        ]
    )
    for key, count in category.items():
        lines.append(f"| `{key}` | {count} |")
    lines.extend(["", "## Task Type Balance", "", "| task_type | count |", "|---|---:|"])
    for key, count in task.items():
        lines.append(f"| `{key}` | {count} |")
    lines.extend(["", "## Source Group Balance", "", "| source_group | count |", "|---|---:|"])
    for key, count in group.items():
        lines.append(f"| `{key}` | {count} |")
    lines.extend(["", "## Candidate Service Count", "", "| count | rows |", "|---|---:|"])
    for key, count in sorted(csc.items()):
        lines.append(f"| `{key}` | {count} |")
    lines.extend(["", "## Notes", ""])
    if notes:
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines.append("- No fallback was needed; each target category reached requested size.")
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "- This is a human validation set, not a clean dataset.",
            "- 没有 full cleaning。",
            "- 没有 split。",
            "- 没有 baseline。",
            "- 没有训练模型。",
        ]
    )
    ROUND3_REPORT.write_text("\n".join(lines), encoding="utf-8")


def write_review_html(rows: List[Dict[str, object]]) -> None:
    data = []
    for row in rows:
        data.append(
            {
                "round3_review_id": row.get("round3_review_id", ""),
                "task_id": row.get("task_id", ""),
                "task_type": row.get("task_type", ""),
                "source_group": row.get("source_group", ""),
                "risk_category": row.get("risk_category", ""),
                "risk_subtype": row.get("risk_subtype", ""),
                "rule_suggestion": row.get("rule_suggestion", ""),
                "query_text": row.get("query_text", ""),
                "query_text_zh_hint": row.get("query_text_zh_hint", ""),
                "candidate_services_display": row.get("candidate_services_display", ""),
                "candidate_apis_display": row.get("candidate_apis_display", ""),
                "gold_services_display": row.get("gold_services_display", ""),
                "gold_apis_display": row.get("gold_apis_display", ""),
                "candidate_services_json": row.get("candidate_services_json", ""),
                "candidate_apis_json": row.get("candidate_apis_json", ""),
                "gold_services_json": row.get("gold_services_json", ""),
                "gold_apis_json": row.get("gold_apis_json", ""),
                "candidate_service_count": row.get("candidate_service_count_resolved", ""),
                "gold_service_count": row.get("gold_service_count_resolved", ""),
                "candidate_api_count": row.get("candidate_api_count_resolved", ""),
                "gold_api_count": row.get("gold_api_count_resolved", ""),
            }
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>Round3 Targeted Review v0.6</title>
  <style>
    body {{ margin:0; font-family: Arial, 'Microsoft YaHei', sans-serif; background:#f7f7f5; color:#1f2933; }}
    .app {{ display:grid; grid-template-columns: 310px 1fr; min-height:100vh; }}
    aside {{ border-right:1px solid #d7d7d2; background:#fff; padding:14px; overflow:auto; max-height:100vh; }}
    main {{ padding:20px 28px; overflow:auto; }}
    .topbar {{ display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }}
    input, select, textarea {{ font:inherit; border:1px solid #c8c8c0; border-radius:4px; padding:7px; background:#fff; }}
    button {{ border:1px solid #334155; background:#334155; color:#fff; border-radius:4px; padding:8px 10px; cursor:pointer; }}
    button.secondary {{ background:#fff; color:#334155; }}
    .item {{ padding:8px; border:1px solid #ddd; border-radius:6px; margin-bottom:7px; cursor:pointer; background:#fff; }}
    .item.active {{ border-color:#2563eb; box-shadow:0 0 0 2px rgba(37,99,235,.15); }}
    .meta {{ color:#64748b; font-size:12px; }}
    .pill {{ display:inline-block; padding:2px 7px; border-radius:999px; background:#e2e8f0; margin:2px; font-size:12px; }}
    .panel {{ background:#fff; border:1px solid #ddd; border-radius:8px; padding:14px; margin:12px 0; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    pre {{ white-space:pre-wrap; word-break:break-word; background:#f1f5f9; padding:10px; border-radius:6px; max-height:280px; overflow:auto; }}
    textarea {{ width:100%; min-height:70px; }}
    .fields {{ display:grid; grid-template-columns: repeat(2, minmax(220px,1fr)); gap:10px; }}
    label {{ display:block; font-weight:600; margin-bottom:4px; }}
    h1 {{ font-size:22px; margin:0 0 4px; }}
    h2 {{ font-size:16px; margin:0 0 8px; }}
  </style>
</head>
<body>
<div class="app">
  <aside>
    <div class="topbar">
      <input id="search" placeholder="搜索 query/task/risk" />
      <select id="riskFilter"><option value="">All risks</option></select>
    </div>
    <div id="list"></div>
  </aside>
  <main>
    <div class="topbar">
      <button id="prev">上一条</button>
      <button id="next">下一条</button>
      <button id="export">Export decisions CSV</button>
      <button id="clearCurrent" class="secondary">清空当前</button>
      <button id="clearAll" class="secondary">清空全部</button>
    </div>
    <h1 id="title"></h1>
    <div class="meta" id="subtitle"></div>
    <details class="panel" open>
      <summary><b>审核提示</b></summary>
      <ol>
        <li>先判断 query 真正要完成什么。</li>
        <li>再看 gold service/API 是否能覆盖核心能力。</li>
        <li>endpoint/carrier/task-flow 身份泄露才是 strong API leak；Latest/All/Count 等通用词不要机械删除。</li>
        <li>API-level 下 candidate_service_count=1 不是 fatal，但 API 候选必须有选择空间。</li>
        <li>不确定时选 uncertain，不要强行 keep。</li>
      </ol>
    </details>
    <div class="panel">
      <h2>Query / Risk</h2>
      <div id="risk"></div>
      <p id="query"></p>
      <p id="queryZh" class="meta"></p>
    </div>
    <div class="grid">
      <div class="panel"><h2>Candidate Services</h2><pre id="candServices"></pre></div>
      <div class="panel"><h2>Gold Services</h2><pre id="goldServices"></pre></div>
      <div class="panel"><h2>Candidate APIs</h2><pre id="candApis"></pre></div>
      <div class="panel"><h2>Gold APIs</h2><pre id="goldApis"></pre></div>
    </div>
    <div class="panel">
      <h2>Manual Decisions</h2>
      <div class="fields">
        <div><label>manual_final_decision</label><select data-field="manual_final_decision"><option></option><option>keep_for_cleaning_candidate</option><option>remove</option><option>uncertain</option></select></div>
        <div><label>semantic_alignment_check</label><select data-field="semantic_alignment_check"><option></option><option>semantic_alignment_ok</option><option>semantic_alignment_uncertain</option><option>semantic_mismatch</option></select></div>
        <div><label>capability_coverage_check</label><select data-field="capability_coverage_check"><option></option><option>coverage_ok</option><option>coverage_uncertain</option><option>coverage_mismatch</option></select></div>
        <div><label>leakage_check</label><select data-field="leakage_check"><option></option><option>no_blocking_leak</option><option>api_leak_blocking</option><option>service_leak_only</option><option>ambiguous</option></select></div>
        <div><label>candidate_validity_check</label><select data-field="candidate_validity_check"><option></option><option>valid</option><option>insufficient_choice_space</option><option>uncertain</option><option>invalid</option></select></div>
        <div><label>task_type_check</label><select data-field="task_type_check"><option></option><option>valid_multi_service</option><option>valid_multi_api</option><option>invalid</option><option>uncertain</option></select></div>
      </div>
      <div style="margin-top:10px"><label>human_notes</label><textarea data-field="human_notes"></textarea></div>
    </div>
  </main>
</div>
<script>
const DATA = {js_string(data)};
const STORAGE_KEY = 'round3_targeted_review_v0_6_decisions';
let index = 0;
let decisions = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}');
function save() {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions)); }}
function filtered() {{
  const q = document.getElementById('search').value.toLowerCase();
  const risk = document.getElementById('riskFilter').value;
  return DATA.filter(x => (!risk || x.risk_category === risk) && (!q || JSON.stringify(x).toLowerCase().includes(q)));
}}
function renderList() {{
  const risks = [...new Set(DATA.map(x => x.risk_category))];
  const sel = document.getElementById('riskFilter');
  if (sel.options.length === 1) risks.forEach(r => {{ const o=document.createElement('option'); o.value=r; o.textContent=r; sel.appendChild(o); }});
  const list = document.getElementById('list'); list.innerHTML='';
  filtered().forEach((x,i) => {{
    const div=document.createElement('div'); div.className='item' + (DATA[index]===x?' active':'');
    div.innerHTML=`<b>${{x.round3_review_id}}</b> | ${{x.task_id}}<br><span class="meta">${{x.risk_category}}</span>`;
    div.onclick=()=>{{ index=DATA.indexOf(x); render(); }};
    list.appendChild(div);
  }});
}}
function current() {{ return DATA[index]; }}
function render() {{
  const x=current(); if(!x) return;
  renderList();
  document.getElementById('title').textContent = `${{x.round3_review_id}} | ${{x.task_id}}`;
  document.getElementById('subtitle').textContent = `${{x.task_type}} | ${{x.source_group}} | services ${{x.candidate_service_count}}/${{x.gold_service_count}} | APIs ${{x.candidate_api_count}}/${{x.gold_api_count}}`;
  document.getElementById('risk').innerHTML = `<span class="pill">${{x.risk_category}}</span><span class="pill">${{x.risk_subtype}}</span><p><b>Rule suggestion:</b> ${{x.rule_suggestion}}</p>`;
  document.getElementById('query').textContent = x.query_text;
  document.getElementById('queryZh').textContent = x.query_text_zh_hint;
  document.getElementById('candServices').textContent = x.candidate_services_display || x.candidate_services_json;
  document.getElementById('goldServices').textContent = x.gold_services_display || x.gold_services_json;
  document.getElementById('candApis').textContent = x.candidate_apis_display || x.candidate_apis_json;
  document.getElementById('goldApis').textContent = x.gold_apis_display || x.gold_apis_json;
  document.querySelectorAll('[data-field]').forEach(el => {{ el.value = (decisions[x.round3_review_id] || {{}})[el.dataset.field] || ''; }});
}}
document.querySelectorAll('[data-field]').forEach(el => el.addEventListener('input', () => {{
  const x=current(); decisions[x.round3_review_id] = decisions[x.round3_review_id] || {{}};
  decisions[x.round3_review_id][el.dataset.field] = el.value; save();
}}));
document.getElementById('prev').onclick=()=>{{ index=Math.max(0,index-1); render(); }};
document.getElementById('next').onclick=()=>{{ index=Math.min(DATA.length-1,index+1); render(); }};
document.getElementById('search').oninput=renderList; document.getElementById('riskFilter').onchange=renderList;
document.getElementById('clearCurrent').onclick=()=>{{ delete decisions[current().round3_review_id]; save(); render(); }};
document.getElementById('clearAll').onclick=()=>{{ if(confirm('Clear all manual decisions?')){{ decisions={{}}; save(); render(); }} }};
function csvCell(v) {{ return '"' + String(v ?? '').replaceAll('"','""') + '"'; }}
document.getElementById('export').onclick=()=>{{
  const fields=['round3_review_id','task_id','task_type','source_group','risk_category','risk_subtype','manual_final_decision','semantic_alignment_check','capability_coverage_check','leakage_check','candidate_validity_check','task_type_check','human_notes'];
  const rows=[fields.join(',')];
  DATA.forEach(x=>{{ const d=decisions[x.round3_review_id]||{{}}; rows.push(fields.map(f=>csvCell(d[f] ?? x[f] ?? '')).join(',')); }});
  const blob=new Blob(['\\ufeff'+rows.join('\\n')],{{type:'text/csv;charset=utf-8'}});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='round3_targeted_review_decisions_100.csv'; a.click();
}};
render();
</script>
</body>
</html>"""
    ROUND3_HTML.write_text(html, encoding="utf-8")


def write_go_no_go(rows: List[Dict[str, object]]) -> None:
    rule_summary = {}
    if RULE_SUMMARY.exists():
        rule_summary = json.loads(RULE_SUMMARY.read_text(encoding="utf-8"))
    round2 = rule_summary.get("round2", {})
    conditions = {
        "strong_api_leak_not_leaking_into_keep": round2.get("strong_api_leak_keep_count", 1) == 0,
        "capability_mismatch_not_leaking_into_keep": round2.get("capability_mismatch_keep_count", 1) == 0,
        "api_level_single_service_not_misdeleted": round2.get("api_level_single_service_legal_rule_remove_count", 1) == 0,
        "generic_weak_leak_false_positive_not_removed": round2.get("generic_weak_leak_false_positive_rule_remove_count", 1) == 0,
        "rule_keep_precision_like_ge_90": round2.get("rule_keep_precision_like", 0) >= 0.90,
    }
    can_write = all(conditions.values())
    category_counts = Counter(str(row.get("risk_category", "")) for row in rows)
    lines = [
        "# Round2 Rule Revision v0.6 Go / No-Go Report",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        f"- rule replay summary: `{RULE_SUMMARY}`",
        f"- failure mode summary: `{FAILURE_SUMMARY}`",
        f"- Round3 validation set: `{ROUND3_CSV}`",
        "",
        "## 样本数量",
        "",
        f"- Round3 targeted validation rows: `{len(rows)}`",
        "",
        "## 主要失败模式",
        "",
        "v0.5 暴露出的主要失败模式包括：strong API leak 漏检、generic weak leak 误删、API-level single-service 边界误判、gold service/API capability coverage mismatch，以及 high-risk bucket 不可直接作为 remove/uncertain 预测器。",
        "",
        "## v4.1 修订内容",
        "",
        "- 强 API leak 拆成 endpoint-specific / carrier-specific / task-flow identity。",
        "- generic weak term 不直接 remove。",
        "- API-level candidate_service_count=1 不是 fatal。",
        "- 新增 capability_coverage_check。",
        "- high-risk bucket 改为 needs_review_priority，并拆分风险子类型。",
        "",
        "## rule_keep 可靠性",
        "",
        f"- Round2 rule_keep precision-like: `{round2.get('rule_keep_precision_like', 0):.4f}`",
        "",
        "## high-risk bucket 为什么不能直接作为 remove/uncertain 预测器",
        "",
        "v0.5 中 high-risk human remove/uncertain 只有 5/10 = 50%，说明 high-risk 只是优先复核信号，不是最终决策信号。v4.1 必须使用具体风险子类型，而不是直接 reject。",
        "",
        "## Round3 为什么需要定向验证",
        "",
        "Round3 专门覆盖 v0.5 的失败模式，验证 v4.1 是否减少误删与漏删；它不是扩数据，也不是 clean dataset。",
        "",
        "## Round3 组成",
        "",
        "| risk category | count |",
        "|---|---:|",
    ]
    for key, count in category_counts.items():
        lines.append(f"| `{key}` | {count} |")
    lines.extend(
        [
            "",
            "```text",
            "Go / No-Go Decision v0.6",
            "",
            f"can_write_conservative_cleaning_script: {str(can_write).lower()}",
            "can_run_full_cleaning_now: false",
            "can_create_split_now: false",
            "can_run_paper_baseline_now: false",
            "must_complete_round3_targeted_review_before_full_cleaning: true",
            "",
            "recommended_next_step:",
            "人工完成 Round3 targeted review；之后用 Round3 final 回放 v4.1，再决定是否写/运行更保守的 cleaning script。",
            "```",
            "",
            "## 条件检查",
            "",
        ]
    )
    for key, value in conditions.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "- 没有 full cleaning。",
            "- 没有 split。",
            "- 没有 baseline。",
            "- 没有训练模型。",
        ]
    )
    GO_NO_GO_MD.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Round3 targeted validation set v0.6.")
    parser.add_argument("--target-each", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    missing = missing_required_inputs()
    missing.extend(path for path in ROUND2_CANDIDATE_POOL_PATHS if not path.exists())
    if missing:
        out = write_missing_inputs(missing)
        print(f"ERROR: missing required inputs. See {out}")
        return 2
    pool = load_pool_rows()
    selected, notes = select_round3(pool, target_each=args.target_each)
    write_csv(ROUND3_CSV, selected)
    write_sampling_report(selected, notes)
    write_review_html(selected)
    write_go_no_go(selected)
    counts = Counter(str(row.get("risk_category", "")) for row in selected)
    print(f"round3_targeted_validation_items={ROUND3_CSV}")
    print(f"round3_sampling_report={ROUND3_REPORT}")
    print(f"round3_review_html={ROUND3_HTML}")
    print(f"round2_rule_revision_v0_6_go_no_go_report={GO_NO_GO_MD}")
    print("round3_composition=" + ";".join(f"{k}:{v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
