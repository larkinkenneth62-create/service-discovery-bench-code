#!/usr/bin/env python
"""Build Chinese readable HTML review apps for external policy v0.2.

This creates compact, Chinese-first review pages for manual QA. It preserves
CSV export/import/localStorage and backs up existing HTML pages before
overwriting the default app paths.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


QA_FIELDS = [
    "qa_final_decision",
    "qa_semantic_alignment_check",
    "qa_capability_coverage_check",
    "qa_candidate_validity_check",
    "qa_service_catalog_check",
    "qa_task_type_check",
    "qa_leakage_check",
    "qa_error_type",
    "qa_severity",
    "qa_notes",
    "reviewer_id",
    "reviewed_at",
]


PHRASES = [
    ("Can you help me", "请帮我"),
    ("I need to", "我需要"),
    ("I would like to", "我想"),
    ("I want to", "我想"),
    ("Please provide", "请提供"),
    ("Please fetch", "请获取"),
    ("search for", "搜索"),
    ("find me", "帮我查找"),
    ("provide me with", "为我提供"),
    ("recommend", "推荐"),
    ("current status", "当前状态"),
    ("tracking number", "追踪号码"),
    ("postal code", "邮政编码"),
    ("weather forecast", "天气预报"),
    ("exchange rate", "汇率"),
    ("latest news", "最新新闻"),
    ("API", "接口"),
    ("service", "服务"),
]


WORDS = {
    "academic": "学术",
    "research": "研究",
    "papers": "论文",
    "topic": "主题",
    "game": "游戏",
    "chess": "国际象棋",
    "religious": "宗教",
    "guidance": "指导",
    "life": "生活",
    "search": "搜索",
    "weather": "天气",
    "translation": "翻译",
    "translate": "翻译",
    "image": "图片",
    "images": "图片",
    "news": "新闻",
    "hotel": "酒店",
    "restaurant": "餐厅",
    "movie": "电影",
    "package": "包裹",
    "mail": "邮件",
    "container": "集装箱",
    "carrier": "承运商",
    "address": "地址",
    "country": "国家",
    "city": "城市",
    "data": "数据",
    "details": "详情",
    "status": "状态",
    "description": "描述",
    "list": "列表",
    "all": "所有",
    "current": "当前",
    "latest": "最新",
    "flight": "航班",
    "airport": "机场",
    "phone": "电话",
    "verify": "验证",
    "product": "产品",
    "order": "订单",
    "demo": "演示",
    "test": "测试",
    "tool": "工具",
    "plugin": "插件",
    "query": "需求",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def b64_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def zh(text: str) -> str:
    out = text or ""
    for en, cn in PHRASES:
        out = out.replace(en, cn).replace(en.lower(), cn)
    words = []
    for token in out.split():
        key = "".join(ch for ch in token.lower() if ch.isalpha())
        repl = WORDS.get(key)
        words.append(token.replace(key, repl) if repl and key in token.lower() else (repl or token))
    out = " ".join(words)
    out = (
        out.replace("I am", "我")
        .replace("I'm", "我")
        .replace(" my ", " 我的 ")
        .replace(" and ", " 和 ")
        .replace(" or ", " 或 ")
        .replace(" with ", " 使用/带有 ")
        .replace(" for ", " 用于 ")
        .replace(" of ", " 的 ")
        .replace(" in ", " 在 ")
    )
    return "中文翻译：" + out


def enrich_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        item["query_text_zh_auto"] = zh(row.get("query_text", ""))
        enriched.append(item)
    return enriched


def html_page(
    *,
    title: str,
    source_kind: str,
    fieldnames: list[str],
    rows: list[dict[str, str]],
    output_name: str,
    policy_field: str,
    label_field: str,
    group_field: str,
    generated_at: str,
) -> str:
    all_fields = list(fieldnames)
    if "query_text_zh_auto" not in all_fields:
        all_fields.append("query_text_zh_auto")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#f5f6f8; --card:#fff; --line:#d8dee8; --text:#172033; --muted:#627084; --blue:#2457d6; --red:#b42318; --green:#137a46; --amber:#9a5b00; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:"Microsoft YaHei UI","Segoe UI",Arial,sans-serif; background:var(--bg); color:var(--text); line-height:1.5; }}
header {{ position:sticky; top:0; z-index:5; background:#fff; border-bottom:1px solid var(--line); padding:12px 16px; }}
h1 {{ font-size:20px; margin:0 0 6px; }}
.danger {{ color:var(--red); font-weight:700; }}
.toolbar {{ display:grid; grid-template-columns:1.5fr 1fr 1fr 1fr auto; gap:8px; margin-top:10px; align-items:start; }}
input, textarea, button {{ font:inherit; }}
input, textarea {{ width:100%; border:1px solid var(--line); border-radius:6px; padding:8px; background:#fff; }}
button {{ border:1px solid var(--line); border-radius:6px; background:#fff; padding:7px 10px; cursor:pointer; }}
button.primary {{ background:var(--blue); border-color:var(--blue); color:#fff; }}
button.keep {{ color:var(--green); }} button.uncertain {{ color:var(--amber); }} button.remove {{ color:var(--red); }}
.filter-block {{ display:grid; gap:4px; }}
.filter-title {{ font-size:12px; color:var(--muted); font-weight:800; }}
.chip-group {{ display:flex; flex-wrap:wrap; gap:5px; }}
.chip {{ border:1px solid var(--line); background:#fff; border-radius:999px; padding:5px 9px; font-size:12px; min-height:28px; }}
.chip.active {{ background:#2457d6; color:#fff; border-color:#2457d6; }}
.chip.keep.active {{ background:#137a46; border-color:#137a46; }}
.chip.uncertain.active {{ background:#9a5b00; border-color:#9a5b00; }}
.chip.remove.active {{ background:#b42318; border-color:#b42318; }}
.layout {{ display:grid; grid-template-columns:300px minmax(500px,1fr) 360px; gap:12px; padding:12px; height:calc(100vh - 128px); }}
.panel {{ background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:auto; }}
.panel h2 {{ font-size:16px; margin:0; padding:12px; border-bottom:1px solid var(--line); background:#fbfcfe; }}
.list-item {{ width:100%; border:0; border-bottom:1px solid var(--line); text-align:left; padding:10px; background:#fff; display:grid; gap:5px; }}
.list-item.active, .list-item:hover {{ background:#eef4ff; }}
.small {{ color:var(--muted); font-size:12px; }}
.pill {{ display:inline-block; border:1px solid var(--line); border-radius:999px; padding:2px 7px; font-size:12px; margin:2px; background:#f6f8fb; }}
.content {{ padding:12px; }}
.section {{ border:1px solid var(--line); border-radius:8px; padding:12px; margin-bottom:12px; background:#fff; }}
.section-title {{ font-weight:800; margin-bottom:8px; }}
.query-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
.lang-card {{ border:1px solid var(--line); border-radius:8px; padding:10px; background:#fbfcfe; }}
.zh {{ background:#f0faf4; border-color:#cdebd8; color:#173d2a; }}
.label {{ font-size:12px; color:var(--muted); font-weight:800; margin-bottom:6px; }}
.text {{ white-space:pre-wrap; overflow-wrap:anywhere; }}
.item-card {{ border:1px solid var(--line); border-radius:8px; padding:9px; margin:8px 0; background:#fbfcfe; }}
.item-title {{ font-weight:800; overflow-wrap:anywhere; }}
.item-zh {{ margin-top:6px; background:#f0faf4; border:1px solid #cdebd8; border-radius:6px; padding:7px; color:#173d2a; white-space:pre-wrap; overflow-wrap:anywhere; }}
details {{ border:1px solid var(--line); border-radius:8px; margin:8px 0; background:#fbfcfe; }}
summary {{ padding:9px; cursor:pointer; font-weight:800; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; margin:0; padding:10px; border-top:1px solid var(--line); font-size:12px; }}
.review-grid {{ display:grid; gap:12px; padding:12px; }}
.review-field {{ display:grid; gap:6px; border:1px solid var(--line); border-radius:8px; padding:8px; background:#fbfcfe; }}
.review-field-title {{ font-size:13px; font-weight:800; color:#273449; }}
.preset-box {{ display:grid; gap:8px; border:2px solid #bfdbfe; border-radius:8px; padding:10px; background:#eff6ff; }}
.preset-title {{ font-size:14px; font-weight:900; color:#1e3a8a; }}
.preset-grid {{ display:grid; grid-template-columns:1fr; gap:7px; }}
.preset-btn {{ text-align:left; border-radius:8px; padding:9px 10px; background:#fff; border:1px solid #bfdbfe; }}
.preset-btn strong {{ display:block; font-size:13px; color:#172033; }}
.preset-btn span {{ display:block; font-size:12px; color:#627084; margin-top:2px; }}
.preset-btn.keep {{ border-color:#bbf7d0; background:#f0fdf4; }}
.preset-btn.uncertain {{ border-color:#fde68a; background:#fffbeb; }}
.preset-btn.remove {{ border-color:#fecaca; background:#fef2f2; }}
textarea {{ min-height:86px; resize:vertical; }}
.hint {{ background:#eef2ff; border:1px solid #c7d2fe; color:#3730a3; border-radius:6px; padding:8px; font-size:13px; }}
@media (max-width:1100px) {{ .layout {{ grid-template-columns:1fr; height:auto; }} .query-grid,.toolbar {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="danger">中文易读审核页：只用于人工 QA；不授权 merge / final dataset / split / baseline / training。</div>
  <div class="toolbar">
    <input id="search" placeholder="搜索 review_item_id / task_id / query">
    <div class="filter-block"><div class="filter-title">Policy</div><div id="policyFilter" class="chip-group"></div></div>
    <div class="filter-block"><div class="filter-title">QA 结论</div><div id="qaFilter" class="chip-group"></div></div>
    <div class="filter-block"><div class="filter-title">分组/类型</div><div id="groupFilter" class="chip-group"></div></div>
    <label class="small"><input id="pendingOnly" type="checkbox"> 只看未审核</label>
  </div>
</header>
<div class="layout">
  <aside class="panel"><h2>样本列表 <span id="count" class="pill"></span></h2><div id="list"></div></aside>
  <main class="panel"><h2>当前样本：中文易读视图</h2><div class="content" id="main"></div></main>
  <aside class="panel"><h2>人工填写</h2><div class="review-grid" id="review"></div></aside>
</div>
<script>
"use strict";
const ROWS = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{b64_json(enrich_rows(rows))}"), c => c.charCodeAt(0))));
const FIELDNAMES = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{b64_json(all_fields)}"), c => c.charCodeAt(0))));
const SOURCE_KIND = {json.dumps(source_kind)};
const OUTPUT_NAME = {json.dumps(output_name)};
const POLICY_FIELD = {json.dumps(policy_field)};
const LABEL_FIELD = {json.dumps(label_field)};
const GROUP_FIELD = {json.dumps(group_field)};
const QA_FIELDS = {json.dumps(QA_FIELDS, ensure_ascii=False)};
const storageKey = "external_policy_v0_2_zh_readable_" + SOURCE_KIND;
let state = ROWS.map(r => Object.assign({{}}, r));
let filtered = [];
let pos = 0;
let filterState = {{ policy: "", qa: "", group: "" }};
const allowed = {{
  qa_final_decision:["","keep_for_cleaning_candidate","uncertain","remove"],
  qa_semantic_alignment_check:["","ok","uncertain","mismatch"],
  qa_capability_coverage_check:["","coverage_ok","coverage_uncertain","coverage_mismatch","not_applicable"],
  qa_candidate_validity_check:["","valid","uncertain","invalid"],
  qa_service_catalog_check:["","valid_catalog","catalog_uncertain","invalid_catalog","not_applicable"],
  qa_task_type_check:["","task_type_ok","task_type_uncertain","task_type_invalid","composable_not_strong_dependency","not_applicable"],
  qa_leakage_check:["","no_obvious_leak","service_leak_blocking","api_leak_blocking","leak_uncertain"],
  qa_severity:["","none","low","medium","high","critical"]
}};
const fieldLabels = {{
  qa_final_decision:"最终处理结论",
  qa_semantic_alignment_check:"query 与 gold 语义是否对齐",
  qa_capability_coverage_check:"gold 能力是否覆盖需求",
  qa_candidate_validity_check:"候选项是否有效",
  qa_service_catalog_check:"服务目录是否有效",
  qa_task_type_check:"任务类型是否正确",
  qa_leakage_check:"是否存在 service/API 泄漏",
  qa_error_type:"错误类型",
  qa_severity:"严重程度",
  qa_notes:"人工备注",
  reviewer_id:"审核人",
  reviewed_at:"审核时间"
}};
const valueLabels = {{
  "":"空",
  keep_for_cleaning_candidate:"保留",
  uncertain:"不确定",
  remove:"删除",
  ok:"对齐",
  mismatch:"不匹配",
  coverage_ok:"覆盖",
  coverage_uncertain:"覆盖不确定",
  coverage_mismatch:"能力不匹配",
  not_applicable:"不适用",
  valid:"有效",
  invalid:"无效",
  valid_catalog:"目录有效",
  catalog_uncertain:"目录不确定",
  invalid_catalog:"目录无效",
  task_type_ok:"任务类型正确",
  task_type_uncertain:"任务类型不确定",
  task_type_invalid:"任务类型错误",
  composable_not_strong_dependency:"不是强组合依赖",
  no_obvious_leak:"无明显泄漏",
  service_leak_blocking:"service 泄漏",
  api_leak_blocking:"API 泄漏",
  leak_uncertain:"泄漏不确定",
  none:"无",
  low:"低",
  medium:"中",
  high:"高",
  critical:"严重"
}};
function $(id) {{ return document.getElementById(id); }}
function val(r,k) {{ return r && r[k] != null ? String(r[k]) : ""; }}
function el(tag, cls, text) {{ const e=document.createElement(tag); if(cls)e.className=cls; if(text!=null)e.textContent=String(text); return e; }}
function parseJson(s) {{ try {{ return s ? JSON.parse(s) : null; }} catch(e) {{ return null; }} }}
function listify(x) {{ if(!x) return []; return Array.isArray(x) ? x : [x]; }}
function zhSimple(s, kind) {{
  if(!s) return "中文说明：无内容。";
  let t = String(s);
  const pairs = [["service","服务"],["API","接口"],["api","接口"],["search","搜索"],["weather","天气"],["news","新闻"],["image","图片"],["data","数据"],["details","详情"],["status","状态"],["translation","翻译"],["hotel","酒店"],["restaurant","餐厅"],["package","包裹"],["tracking","追踪"],["current","当前"],["latest","最新"],["list","列表"],["all","所有"],["recommend","推荐"],["query","需求"],["description","描述"]];
  pairs.forEach(([a,b]) => {{ t = t.replaceAll(a,b); }});
  return (kind === "api" ? "接口中文说明：" : kind === "service" ? "服务中文说明：" : "中文翻译：") + t;
}}
function itemName(x) {{ if(typeof x === "string") return x; return x?.service_name || x?.api_name || x?.tool_name || x?.name || ""; }}
function itemDesc(x) {{ if(typeof x === "string") return ""; return x?.service_description || x?.api_description || x?.description || ""; }}
function itemBlock(x, kind) {{
  const box = el("div","item-card");
  box.appendChild(el("div","item-title", itemName(x) || "未命名"));
  const en = [];
  if(typeof x === "object" && x) {{
    if(x.service_name) en.push("Service: " + x.service_name);
    if(x.api_name) en.push("API: " + x.api_name);
    if(x.category_name) en.push("Category: " + x.category_name);
    if(itemDesc(x)) en.push("Description: " + itemDesc(x));
  }} else en.push(String(x || ""));
  box.appendChild(el("div","small text", en.join("\\n")));
  box.appendChild(el("div","item-zh", zhSimple(en.join("。"), kind)));
  return box;
}}
function renderJsonList(parent, title, raw, kind, open=false, limit=18) {{
  const data = listify(parseJson(raw));
  const d = document.createElement("details"); d.open = open;
  d.appendChild(el("summary","", title + "（" + data.length + " 条，默认摘要显示）"));
  const body = el("div","content");
  if(data.length > 80) body.appendChild(el("div","hint","候选项很多，但展开后每条都会显示中文说明。建议先看 Gold 和 policy evidence，再按需检查候选目录。"));
  data.forEach(x => body.appendChild(itemBlock(x, kind)));
  d.appendChild(body); parent.appendChild(d);
}}
function unique(k) {{ return [...new Set(state.map(r=>val(r,k)).filter(Boolean))].sort(); }}
function labelOf(v) {{ return valueLabels[v] || v || "全部"; }}
function fillChipGroup(id, values, label, stateKey) {{
  const box=$(id); box.replaceChildren();
  const all=el("button","chip active",label); all.type="button"; all.onclick=()=>{{filterState[stateKey]=""; refreshFilterChips(id,stateKey); apply(); render();}}; all.dataset.value="";
  box.appendChild(all);
  values.forEach(v=>{{ const b=el("button","chip",labelOf(v)); b.type="button"; b.title=v; b.dataset.value=v; b.onclick=()=>{{filterState[stateKey]=v; refreshFilterChips(id,stateKey); apply(); render();}}; box.appendChild(b); }});
}}
function refreshFilterChips(id,stateKey) {{
  const current=filterState[stateKey] || "";
  Array.from($(id).querySelectorAll("button")).forEach(b=>b.classList.toggle("active", b.dataset.value===current));
}}
function setup() {{
  fillChipGroup("policyFilter", unique(POLICY_FIELD), "全部", "policy");
  fillChipGroup("qaFilter", allowed.qa_final_decision.filter(Boolean), "全部", "qa");
  fillChipGroup("groupFilter", unique(GROUP_FIELD), "全部", "group");
  ["search","pendingOnly"].forEach(id => $(id).addEventListener("input", () => {{ apply(); render(); }}));
}}
function reviewed(r) {{ return val(r,"qa_final_decision").trim() !== ""; }}
function apply() {{
  const q=$("search").value.toLowerCase(), p=filterState.policy, qa=filterState.qa, g=filterState.group, pending=$("pendingOnly").checked;
  filtered=[]; state.forEach((r,i)=>{{
    const hay=[val(r,"review_item_id"),val(r,"task_id"),val(r,"query_text")].join("\\n").toLowerCase();
    if(q && !hay.includes(q)) return;
    if(p && val(r,POLICY_FIELD)!==p) return;
    if(qa && val(r,"qa_final_decision")!==qa) return;
    if(g && val(r,GROUP_FIELD)!==g) return;
    if(pending && reviewed(r)) return;
    filtered.push(i);
  }});
  if(pos >= filtered.length) pos = Math.max(0, filtered.length-1);
}}
function currentIndex() {{ return filtered.length ? filtered[pos] : -1; }}
function renderList() {{
  const list=$("list"); list.replaceChildren(); $("count").textContent=filtered.length + "/" + state.length;
  filtered.forEach((idx,i)=>{{ const r=state[idx]; const b=el("button","list-item"+(i===pos?" active":""),""); b.onclick=()=>{{pos=i;render();}};
    b.appendChild(el("div","small", val(r,"review_item_id")+" | "+val(r,"task_id")));
    b.appendChild(el("div","text", val(r,"query_text").slice(0,180)));
    b.appendChild(el("div","small", val(r,"query_text_zh_auto").slice(0,150)));
    b.appendChild(el("div","small", val(r,POLICY_FIELD)+" / "+(val(r,"qa_final_decision")||"未审核")));
    list.appendChild(b);
  }});
}}
function addSection(parent, title) {{ const s=el("section","section"); s.appendChild(el("div","section-title",title)); parent.appendChild(s); return s; }}
function renderMain() {{
  const m=$("main"); m.replaceChildren(); const idx=currentIndex(); if(idx<0) {{ m.appendChild(el("div","hint","没有匹配样本")); return; }}
  const r=state[idx];
  let s=addSection(m,"1. 先看用户需求 Query（必须看中文）");
  const grid=el("div","query-grid");
  const en=el("div","lang-card"); en.appendChild(el("div","label","英文原文")); en.appendChild(el("div","text",val(r,"query_text")));
  const zh=el("div","lang-card zh"); zh.appendChild(el("div","label","中文翻译")); zh.appendChild(el("div","text",val(r,"query_text_zh_auto")));
  grid.appendChild(en); grid.appendChild(zh); s.appendChild(grid);
  s=addSection(m,"2. 样本关键信息");
  [["review_item_id","审核ID"],["task_id","任务ID"],[GROUP_FIELD,"分组/类型"],[POLICY_FIELD,"policy 决策"],[LABEL_FIELD,"policy 标签"],["source_tool_or_plugin_name","源工具/插件名"],["metatool_rewrite_needed","MetaTool 是否需 rewrite"],["stable_reconstruction_needed","Stable 是否需重构候选"],["stable_rewrite_needed","Stable 是否需 rewrite"],["stable_requires_composable_dependency_review","Stable 是否需组合依赖复核"]].forEach(([k,l])=>{{ if(r[k]!==undefined) s.appendChild(el("div","text",l+"： "+val(r,k))); }});
  s=addSection(m,"3. Gold 正确答案（优先核对）");
  if(r.gold_services_json!==undefined) renderJsonList(s,"Gold Services / 正确服务",val(r,"gold_services_json"),"service",true,20);
  if(r.gold_apis_json!==undefined) renderJsonList(s,"Gold APIs / 正确接口",val(r,"gold_apis_json"),"api",true,20);
  if(r.gold_tools_or_apis_json!==undefined) renderJsonList(s,"Gold Tools/APIs / 正确工具接口",val(r,"gold_tools_or_apis_json"),"api",true,20);
  s=addSection(m,"4. Candidates 候选项摘要（默认少量展示）");
  if(r.candidate_services_json!==undefined) renderJsonList(s,"Candidate Services / 候选服务",val(r,"candidate_services_json"),"service",false,10000);
  if(r.candidate_apis_json!==undefined) renderJsonList(s,"Candidate APIs / 候选接口",val(r,"candidate_apis_json"),"api",false,10000);
  if(r.available_tools_or_apis_json!==undefined) renderJsonList(s,"Available Tools/APIs / 可用工具接口",val(r,"available_tools_or_apis_json"),"api",false,10000);
  s=addSection(m,"5. 审核提示");
  s.appendChild(el("div","hint","顺序：先判断 query 真正要什么，再看 gold 是否覆盖，再看 candidate 是否有选择空间，最后判断 service/API leak。不确定就选 uncertain，不要强行 keep。"));
  s=addSection(m,"6. Raw JSON（最后才看）");
  FIELDNAMES.filter(k=>k.endsWith("_json")).forEach(k=>{{ const d=document.createElement("details"); d.appendChild(el("summary","",k)); d.appendChild(el("pre","",val(r,k))); s.appendChild(d); }});
}}
function chipClassForValue(v) {{
  if(v === "keep_for_cleaning_candidate" || v === "ok" || v === "coverage_ok" || v === "valid" || v === "valid_catalog" || v === "task_type_ok" || v === "no_obvious_leak" || v === "none") return " keep";
  if(v === "remove" || v === "mismatch" || v === "coverage_mismatch" || v === "invalid" || v === "invalid_catalog" || v === "task_type_invalid" || v === "service_leak_blocking" || v === "api_leak_blocking" || v === "critical" || v === "high") return " remove";
  if(v === "uncertain" || v.includes("uncertain") || v === "leak_uncertain" || v === "medium" || v === "composable_not_strong_dependency") return " uncertain";
  return "";
}}
function makeButtonGroup(k) {{
  const wrap=el("div","review-field");
  wrap.appendChild(el("div","review-field-title",fieldLabels[k] || k));
  const group=el("div","chip-group");
  (allowed[k]||[""]).forEach(v=>{{
    const b=el("button","chip"+chipClassForValue(v),labelOf(v)); b.type="button"; b.title=v; b.dataset.value=v;
    if(val(state[currentIndex()],k)===v) b.classList.add("active");
    b.onclick=()=>{{ state[currentIndex()][k]=v; save(); renderReview(); renderList(); }};
    group.appendChild(b);
  }});
  wrap.appendChild(group);
  return wrap;
}}
function makeInput(k, area=false) {{ const lab=el("label","",fieldLabels[k] || k); const e=area?document.createElement("textarea"):document.createElement("input"); e.value=val(state[currentIndex()],k); e.placeholder=fieldLabels[k] || k; e.oninput=()=>{{ state[currentIndex()][k]=e.value; save(); }}; lab.appendChild(e); return lab; }}
function goNextAfterPreset(previousIdx) {{
  save();
  apply();
  const nextPos = filtered.indexOf(previousIdx);
  if(nextPos >= 0) pos = Math.min(nextPos + 1, filtered.length - 1);
  else pos = Math.min(pos, Math.max(0, filtered.length - 1));
  render();
}}
function applyPreset(fields, note) {{
  const idx=currentIndex(); if(idx<0)return;
  const r=state[idx];
  Object.entries(fields).forEach(([k,v])=>{{ r[k]=v; }});
  if(!val(r,"reviewer_id").trim()) r.reviewer_id="user_manual_preset";
  r.reviewed_at=new Date().toISOString().slice(0,19);
  const old=val(r,"qa_notes").trim();
  r.qa_notes=(old?old+"\\n":"")+"Preset: "+note;
  goNextAfterPreset(idx);
}}
function presetButton(cls, title, desc, fields, note) {{
  const b=el("button","preset-btn "+cls,"");
  b.type="button";
  b.appendChild(el("strong","",title));
  b.appendChild(el("span","",desc));
  b.onclick=()=>applyPreset(fields,note);
  return b;
}}
function renderPresetPanel() {{
  const box=el("div","preset-box");
  box.appendChild(el("div","preset-title","预设审核方案：一键填写并自动下一条"));
  box.appendChild(el("div","hint","这些按钮只是帮你快速填写人工 QA 字段。请先看 query、gold、candidate；如果边界不清，优先点“不确定”。"));
  const grid=el("div","preset-grid");
  grid.appendChild(presetButton("keep","全部符合，保留","语义对齐、能力覆盖、候选有效、无明显泄露。",
    {{qa_final_decision:"keep_for_cleaning_candidate",qa_semantic_alignment_check:"ok",qa_capability_coverage_check:"coverage_ok",qa_candidate_validity_check:"valid",qa_service_catalog_check:"valid_catalog",qa_task_type_check:"task_type_ok",qa_leakage_check:"no_obvious_leak",qa_error_type:"none",qa_severity:"none"}},
    "all checks pass; keep as cleaning candidate"));
  grid.appendChild(presetButton("uncertain","存在 service leak，但能力满足","query 直接暴露服务名；gold 能完成任务，但不适合直接进 clean 主集。",
    {{qa_final_decision:"uncertain",qa_semantic_alignment_check:"ok",qa_capability_coverage_check:"coverage_ok",qa_candidate_validity_check:"valid",qa_service_catalog_check:"valid_catalog",qa_task_type_check:"task_type_ok",qa_leakage_check:"service_leak_blocking",qa_error_type:"service_leak",qa_severity:"medium"}},
    "service leak blocking, but semantic/capability coverage is otherwise OK"));
  grid.appendChild(presetButton("remove","存在 API leak，但能力满足","query 直接暴露 gold API/endpoint；能力可满足，但主评测应删除或重写。",
    {{qa_final_decision:"remove",qa_semantic_alignment_check:"ok",qa_capability_coverage_check:"coverage_ok",qa_candidate_validity_check:"valid",qa_service_catalog_check:"valid_catalog",qa_task_type_check:"task_type_ok",qa_leakage_check:"api_leak_blocking",qa_error_type:"api_leak",qa_severity:"high"}},
    "API leak blocking; remove from direct clean evaluation unless rewritten"));
  grid.appendChild(presetButton("remove","gold 不能满足 query","gold service/API 与 query 核心需求不匹配，或缺关键能力。",
    {{qa_final_decision:"remove",qa_semantic_alignment_check:"mismatch",qa_capability_coverage_check:"coverage_mismatch",qa_candidate_validity_check:"valid",qa_service_catalog_check:"valid_catalog",qa_task_type_check:"task_type_ok",qa_leakage_check:"no_obvious_leak",qa_error_type:"capability_mismatch",qa_severity:"high"}},
    "gold/capability mismatch; gold cannot satisfy core query"));
  grid.appendChild(presetButton("uncertain","候选空间无效或太弱","候选没有真实选择空间、候选数等于 gold、或需要 reconstruction。",
    {{qa_final_decision:"uncertain",qa_semantic_alignment_check:"ok",qa_capability_coverage_check:"coverage_ok",qa_candidate_validity_check:"invalid",qa_service_catalog_check:"catalog_uncertain",qa_task_type_check:"task_type_uncertain",qa_leakage_check:"no_obvious_leak",qa_error_type:"candidate_space_invalid",qa_severity:"medium"}},
    "candidate choice space invalid or too weak; needs reconstruction/review"));
  grid.appendChild(presetButton("uncertain","不确定，留待复核","语义、能力、泄露或任务类型有任一项看不准。",
    {{qa_final_decision:"uncertain",qa_semantic_alignment_check:"uncertain",qa_capability_coverage_check:"coverage_uncertain",qa_candidate_validity_check:"uncertain",qa_service_catalog_check:"catalog_uncertain",qa_task_type_check:"task_type_uncertain",qa_leakage_check:"leak_uncertain",qa_error_type:"uncertain",qa_severity:"medium"}},
    "uncertain; needs later manual review"));
  box.appendChild(grid);
  return box;
}}
function renderReview() {{ const p=$("review"); p.replaceChildren(); const idx=currentIndex(); if(idx<0)return; 
  p.appendChild(el("div","hint","这里只能填人工 QA 字段；不会自动把 policy 当 final。"));
  p.appendChild(renderPresetPanel());
  ["qa_final_decision","qa_semantic_alignment_check","qa_capability_coverage_check","qa_candidate_validity_check","qa_service_catalog_check","qa_task_type_check","qa_leakage_check","qa_severity"].forEach(k=>p.appendChild(makeButtonGroup(k)));
  p.appendChild(makeInput("qa_error_type")); p.appendChild(makeInput("qa_notes",true)); p.appendChild(makeInput("reviewer_id")); p.appendChild(makeInput("reviewed_at"));
  const btns=el("div",""); [["keep","keep_for_cleaning_candidate"],["uncertain","uncertain"],["remove","remove"]].forEach(([cls,v])=>{{ const b=el("button",cls,"标为 "+v); b.onclick=()=>{{state[idx].qa_final_decision=v;save();render();}}; btns.appendChild(b); }});
  const t=el("button","","填当前时间"); t.onclick=()=>{{state[idx].reviewed_at=new Date().toISOString().slice(0,19);save();render();}}; btns.appendChild(t);
  const ex=el("button","primary","导出 CSV"); ex.onclick=()=>exportCsv(false); btns.appendChild(ex);
  const im=el("button","","导入 CSV"); im.onclick=()=>$("importer").click(); btns.appendChild(im);
  p.appendChild(btns);
  const file=document.createElement("input"); file.type="file"; file.id="importer"; file.style.display="none"; file.onchange=e=>{{ if(e.target.files[0]) importCsv(e.target.files[0]); }}; p.appendChild(file);
}}
function render() {{ renderList(); renderMain(); renderReview(); }}
function save() {{ localStorage.setItem(storageKey, JSON.stringify(state.map(r=>Object.fromEntries(["review_item_id",...QA_FIELDS].map(k=>[k,val(r,k)]))))); }}
function load() {{ const raw=localStorage.getItem(storageKey); if(!raw)return; const arr=JSON.parse(raw); const map=new Map(arr.map(x=>[x.review_item_id,x])); state.forEach(r=>{{ const p=map.get(r.review_item_id); if(p) QA_FIELDS.forEach(k=>r[k]=val(p,k)); }}); }}
function csvEsc(v) {{ return '"' + String(v??"").replaceAll('"','""') + '"'; }}
function exportCsv(forceDraft) {{ const fields=FIELDNAMES.slice(); QA_FIELDS.forEach(k=>{{if(!fields.includes(k))fields.push(k)}}); const pending=state.filter(r=>!reviewed(r)).length; const name=OUTPUT_NAME.replace(/\\.csv$/,"")+(pending?"_draft":"")+".csv"; const lines=[fields.map(csvEsc).join(",")]; state.forEach(r=>lines.push(fields.map(k=>csvEsc(val(r,k))).join(","))); const blob=new Blob(["\\ufeff"+lines.join("\\r\\n")+"\\r\\n"],{{type:"text/csv;charset=utf-8"}}); const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=name; a.click(); URL.revokeObjectURL(a.href); }}
function parseCsv(text) {{ if(text.charCodeAt(0)===0xFEFF) text=text.slice(1); const rows=[]; let row=[],cell="",q=false; for(let i=0;i<text.length;i++){{const c=text[i],n=text[i+1]; if(q){{ if(c==='"'&&n==='"'){{cell+='"';i++;}} else if(c==='"')q=false; else cell+=c; }} else {{ if(c==='"')q=true; else if(c===','){{row.push(cell);cell='';}} else if(c==='\\n'){{row.push(cell);rows.push(row);row=[];cell='';}} else if(c!=='\\r')cell+=c; }} }} if(cell||row.length){{row.push(cell);rows.push(row);}} const h=rows.shift()||[]; return rows.filter(r=>r.some(Boolean)).map(r=>Object.fromEntries(h.map((k,i)=>[k,r[i]||""]))); }}
function importCsv(file) {{ const rd=new FileReader(); rd.onload=()=>{{ const arr=parseCsv(String(rd.result||"")); const map=new Map(arr.map(r=>[r.review_item_id,r])); let n=0; state.forEach(r=>{{const p=map.get(r.review_item_id); if(p){{QA_FIELDS.forEach(k=>{{if(p[k]!==undefined)r[k]=p[k];}}); n++;}}}}); save(); apply(); render(); alert("已导入 "+n+" 条人工字段"); }}; rd.readAsText(file,"utf-8"); }}
document.addEventListener("keydown",e=>{{ const tag=(e.target.tagName||"").toLowerCase(); if(["input","textarea"].includes(tag))return; if(e.key==="j"||e.key==="ArrowRight"){{pos=Math.min(pos+1,filtered.length-1);render();}} if(e.key==="k"||e.key==="ArrowLeft"){{pos=Math.max(pos-1,0);render();}} }});
load(); setup(); apply(); render();
</script>
</body>
</html>"""


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    dst = path.with_suffix(".before_zh_readable.html")
    shutil.copy2(path, dst)
    return dst


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Chinese readable external v0.2 review HTML apps.")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    generated_at = now_iso()

    meta_csv = root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv"
    stable_csv = root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv"
    if not meta_csv.exists() or not stable_csv.exists():
        raise SystemExit("Missing required v0.2 QA CSV files.")

    meta_fields, meta_rows = read_csv(meta_csv)
    stable_fields, stable_rows = read_csv(stable_csv)
    meta_html = root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_app_v0_2.html"
    stable_html = root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_app_v0_2.html"
    backups = [str(x) for x in [backup(meta_html), backup(stable_html)] if x]

    meta_html.write_text(
        html_page(
            title="MetaTool v0.2 泄漏策略人工审核（中文易读版）",
            source_kind="metatool",
            fieldnames=meta_fields,
            rows=meta_rows,
            output_name="metatool_leakage_policy_review_items_v0_2_reviewed.csv",
            policy_field="metatool_policy_decision",
            label_field="metatool_leakage_policy_label",
            group_field="task_type",
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    stable_html.write_text(
        html_page(
            title="StableToolBench v0.2 过滤策略人工审核（中文易读版）",
            source_kind="stabletoolbench",
            fieldnames=stable_fields,
            rows=stable_rows,
            output_name="stabletoolbench_filter_policy_review_items_v0_2_reviewed.csv",
            policy_field="stable_policy_decision",
            label_field="stable_policy_label",
            group_field="stable_group",
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )

    index = root / "outputs/external_qa_v0_2/external_policy_v0_2_review_index.html"
    index.write_text(
        f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>External v0.2 中文审核入口</title>
<body style="font-family:Microsoft YaHei UI,Segoe UI,Arial,sans-serif;max-width:880px;margin:40px auto;line-height:1.6">
<h1>External Policy v0.2 中文易读审核入口</h1>
<p style="color:#b42318;font-weight:700">只用于人工审核，不授权 merge / final dataset / split / baseline / training。</p>
<ul>
<li><a href="metatool/metatool_leakage_policy_review_app_v0_2.html">MetaTool 中文易读审核页</a></li>
<li><a href="stabletoolbench/stabletoolbench_filter_policy_review_app_v0_2.html">StableToolBench 中文易读审核页</a></li>
</ul>
<p>生成时间：{generated_at}</p></body></html>""",
        encoding="utf-8",
    )

    summary = {
        "generated_at": generated_at,
        "metatool_html": str(meta_html),
        "stabletoolbench_html": str(stable_html),
        "index_html": str(index),
        "backups": backups,
        "metatool_rows": len(meta_rows),
        "stabletoolbench_rows": len(stable_rows),
        "qwen_called": False,
        "external_api_called": False,
        "auto_filled_qa": False,
    }
    out_dir = root / "outputs/external_policy_v0_2_html_review_app"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "zh_readable_review_app_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = root / "docs/phase1/external_policy_v0_2_html_review_zh_readable_update_report.md"
    report.write_text(
        f"""# External Policy v0.2 中文易读 HTML 更新报告

Generated time: {generated_at}

## 本次改动

- 将 MetaTool 和 StableToolBench 默认 HTML 审核页改为中文易读版。
- 页面 UI、提示、审核顺序、按钮和字段说明改为中文。
- 默认优先展示：query 双语、policy 决策、gold 正确答案、候选摘要、审核提示。
- Raw JSON 默认折叠到最后，避免一打开就被长 JSON 淹没。
- MetaTool 的 199-service candidate catalog 默认只展示前 12 条摘要。
- StableToolBench 的 candidate API/service 默认展示摘要。

## 保留功能

- localStorage 自动保存。
- 导入已审核 CSV。
- 导出 reviewed/draft CSV。
- 上一条/下一条快捷键：J / K 或左右方向键。

## 边界

- 没有调用外部翻译 API。
- 没有调用 Qwen/OpenAI/DashScope。
- 没有自动填写 QA 字段。
- 没有生成 final clean dataset。
- 没有 merge、split、baseline、training。

## 输出

- `{meta_html}`
- `{stable_html}`
- `{index}`

## 备份

{chr(10).join('- `' + b + '`' for b in backups)}
""",
        encoding="utf-8",
    )

    archive = root / "outputs/run_archives/2026-07-07_external_policy_v0_2_zh_readable_html"
    archive.mkdir(parents=True, exist_ok=True)
    for p in [meta_html, stable_html, index, report, out_dir / "zh_readable_review_app_summary.json", root / "scripts/validation/build_external_policy_v0_2_readable_zh_review_apps.py"]:
        shutil.copy2(p, archive / p.name)

    print("metatool_html:", meta_html)
    print("stabletoolbench_html:", stable_html)
    print("index_html:", index)
    print("metatool_rows:", len(meta_rows))
    print("stabletoolbench_rows:", len(stable_rows))
    print("backups:", backups)
    print("qwen_called: false")
    print("external_api_called: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
