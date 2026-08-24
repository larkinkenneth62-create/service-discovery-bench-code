from __future__ import annotations

import argparse
import json
from pathlib import Path

from final_qa_v1_5d_common import (
    DOC_DIR,
    OUTPUT_DIR,
    QA_FIELD_OPTIONS,
    QA_HUMAN_FIELDS,
    json_preview,
    now_text,
    read_csv,
    write_md,
)


def build_protocol_doc(path: Path) -> None:
    lines = [
        "# Final QA Review Protocol v1.5d",
        "",
        f"Generated time: {now_text()}",
        "Input file: `outputs/final_qa_v1_5d/final_qa_review_items_v1_5d.csv`",
        "Sample size target: 32 previous failed regression checks + 100 current v1.4b clean candidates.",
        "",
        "v1.5d is not a new open-ended rule calibration round. It is an impacted clean-candidate QA after v1.4b tightening.",
        "",
        "## Scope",
        "",
        "- Review only `outputs/final_qa_v1_5d/final_qa_review_items_v1_5d.csv`.",
        "- Do not generate a final clean dataset in this phase.",
        "- Do not create split, run baseline, train model, or certify API-level readiness.",
        "- Human QA fields must be filled by human review only.",
        "",
        "## previous_failed_regression Standard",
        "",
        "pass:",
        "",
        "- current v1.4b bucket is not `dryrun_clean_candidate`",
        "- moved-out reason is reasonable",
        "",
        "fail:",
        "",
        "- still clean candidate",
        "- or moved-out reason is obviously wrong and the sample appears clean-ready",
        "",
        "If still clean candidate:",
        "",
        "- `qa_error_type = regression_not_fixed`",
        "- `qa_severity = critical`",
        "",
        "## current_clean_candidate Standard",
        "",
        "Mark `pass` only when all hold:",
        "",
        "- no blocking API leak",
        "- no service-level service leak",
        "- real service choice space",
        "- gold services/APIs are present in candidates",
        "- semantic alignment ok",
        "- capability coverage ok",
        "- no missing core requirement",
        "- no extra unrelated gold service",
        "- task type valid",
        "- dedup status acceptable",
        "",
        "Mark `fail` when any release-blocking issue appears:",
        "",
        "- strong API leak",
        "- service-level no-choice",
        "- service-level service leak",
        "- gold missing",
        "- semantic mismatch",
        "- capability mismatch",
        "- missing core requirement",
        "- wrong extra gold service",
        "- generic search overtrusted",
        "- domain-specific capability gap",
        "- wrong task type",
        "- critical duplicate issue",
        "",
        "## v1.5d Pass Thresholds",
        "",
        "- previous_failed_regression still-clean count = 0",
        "- current_clean_candidate critical error count = 0",
        "- current_clean_candidate major+critical error rate <= 5%",
        "- strong API leak in current clean candidate = 0",
        "- service-level no-choice in current clean candidate = 0",
        "- capability mismatch in current clean candidate = 0",
        "- wrong gold set in current clean candidate = 0",
        "- generic search overtrust critical error = 0",
        "- duplicate critical error = 0",
        "",
        "v1.5d only certifies service-level clean candidates. API-level final clean readiness remains unverified.",
    ]
    write_md(path, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build v1.5d impacted clean-candidate QA HTML review app.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5d.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "final_qa_review_app_v1_5d.html")
    args = parser.parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Missing v1.5d review set: {args.input}")

    rows = read_csv(args.input)
    enriched = []
    for row in rows:
        copy = dict(row)
        copy["_candidate_services_preview"] = json_preview(row.get("candidate_services_json", ""), 1600)
        copy["_candidate_apis_preview"] = json_preview(row.get("candidate_apis_json", ""), 2200)
        copy["_gold_services_preview"] = json_preview(row.get("gold_services_json", ""), 1000)
        copy["_gold_apis_preview"] = json_preview(row.get("gold_apis_json", ""), 1300)
        enriched.append(copy)

    data_json = json.dumps(enriched, ensure_ascii=False)
    options_json = json.dumps(QA_FIELD_OPTIONS, ensure_ascii=False)
    human_fields_json = json.dumps(QA_HUMAN_FIELDS, ensure_ascii=False)
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Final QA Review v1.5d</title>
  <style>
    :root { --bg:#f6f7f9; --panel:#fff; --text:#1f2937; --muted:#6b7280; --line:#d9dee7; --accent:#1d4ed8; --bad:#b91c1c; --good:#047857; --warn:#b45309; --info:#4338ca; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--text); }
    header { padding:14px 18px; background:#111827; color:#fff; display:flex; gap:16px; align-items:center; justify-content:space-between; }
    header h1 { font-size:18px; margin:0; font-weight:650; }
    header .meta { color:#cbd5e1; font-size:12px; }
    .layout { display:grid; grid-template-columns:340px minmax(0,1fr); height:calc(100vh - 54px); }
    aside { border-right:1px solid var(--line); background:#fff; display:flex; flex-direction:column; min-width:0; }
    .filters { padding:10px; border-bottom:1px solid var(--line); display:grid; gap:8px; }
    input, select, textarea, button { font:inherit; }
    input, select, textarea { width:100%; border:1px solid var(--line); border-radius:6px; padding:8px; background:#fff; color:var(--text); }
    button { border:1px solid var(--line); border-radius:6px; padding:8px 10px; background:#fff; cursor:pointer; }
    button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
    button.danger { color:var(--bad); }
    .list { overflow:auto; padding:8px; display:grid; gap:8px; }
    .item { border:1px solid var(--line); border-radius:7px; padding:8px; cursor:pointer; background:#fff; }
    .item.active { border-color:var(--accent); box-shadow:0 0 0 2px rgba(29,78,216,.12); }
    .item .title { font-size:13px; font-weight:650; }
    .item .sub { font-size:12px; color:var(--muted); margin-top:4px; line-height:1.35; }
    main { overflow:auto; padding:16px 18px 24px; }
    .toolbar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
    details.protocol { background:#eef2ff; border:1px solid #c7d2fe; border-radius:8px; padding:10px 12px; margin-bottom:12px; }
    details.protocol summary { cursor:pointer; font-weight:650; }
    .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; margin-bottom:12px; }
    .panel h2 { font-size:15px; margin:0 0 10px; }
    .panel h3 { font-size:13px; margin:10px 0 6px; color:#374151; }
    .kv { display:grid; grid-template-columns:210px minmax(0,1fr); gap:6px 10px; font-size:13px; }
    .kv div:nth-child(odd) { color:var(--muted); }
    pre { white-space:pre-wrap; word-break:break-word; background:#f8fafc; border:1px solid #e5e7eb; border-radius:6px; padding:10px; font-size:12px; line-height:1.42; max-height:310px; overflow:auto; }
    .query { font-size:15px; line-height:1.55; background:#fff7ed; border-color:#fed7aa; }
    .badge { display:inline-block; border-radius:999px; padding:2px 7px; font-size:12px; border:1px solid var(--line); background:#fff; margin:2px 4px 2px 0; }
    .badge.good { color:var(--good); border-color:#a7f3d0; background:#ecfdf5; }
    .badge.bad { color:var(--bad); border-color:#fecaca; background:#fef2f2; }
    .badge.warn { color:var(--warn); border-color:#fed7aa; background:#fffbeb; }
    .badge.info { color:var(--info); border-color:#c7d2fe; background:#eef2ff; }
    .form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
    .form-grid label { font-size:12px; color:var(--muted); display:grid; gap:4px; }
    textarea { min-height:82px; resize:vertical; }
    .muted { color:var(--muted); font-size:12px; }
    .hint-list { margin:0; padding-left:18px; line-height:1.55; font-size:13px; }
    .service-tree { display:grid; gap:8px; }
    .service-node { border:1px solid #e5e7eb; border-radius:7px; padding:8px; background:#fbfdff; }
    .service-title { font-weight:650; margin-bottom:5px; }
    .api-list { margin:0; padding-left:20px; }
    .api-list li { margin:3px 0; }
    @media (max-width:900px) { .layout { grid-template-columns:1fr; height:auto; } aside { height:42vh; border-right:0; border-bottom:1px solid var(--line); } .grid, .form-grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
<header>
  <h1>Final QA Review v1.5d</h1>
  <div class="meta">Impacted clean-candidate QA only. Not final clean dataset / split / baseline / training.</div>
</header>
<div class="layout">
  <aside>
    <div class="filters">
      <input id="search" placeholder="Search qa_item_id / task_id / query">
      <select id="bucketFilter"><option value="">All QA buckets</option></select>
      <select id="subbucketFilter"><option value="">All subbuckets</option></select>
      <select id="decisionFilter"><option value="">All decisions</option><option value="unfilled">Unfilled</option><option value="pass">pass</option><option value="fail">fail</option><option value="uncertain">uncertain</option></select>
      <div class="muted" id="countBox"></div>
    </div>
    <div class="list" id="itemList"></div>
  </aside>
  <main>
    <details class="protocol" open>
      <summary>审核说明：v1.5d 是 impacted clean-candidate QA</summary>
      <p>本页只审核 32 条旧失败回归样本和 100 条当前 v1.4b clean candidate。它不是 final clean dataset，也不授权 split、baseline 或训练。</p>
      <p><b>previous_failed_regression：</b>应该已经被 v1.4b 移出 clean candidate。如果仍是 clean，通常是 critical regression_not_fixed。</p>
      <p><b>current_clean_candidate_audit：</b>只有 query/gold 语义对齐、能力覆盖完整、无 blocking leak、有真实服务选择空间、task type 正确、dedup 可接受时才 pass。</p>
      <p>如果 service/API 边界、能力覆盖、leak 或 duplicate 不确定，选 uncertain，不要强行 pass。</p>
    </details>
    <div class="toolbar">
      <button id="prevBtn">Previous</button>
      <button id="nextBtn">Next</button>
      <button class="primary" id="exportBtn">Export decisions CSV</button>
      <button id="clearCurrentBtn">Clear current</button>
      <button class="danger" id="clearAllBtn">Clear all local decisions</button>
    </div>
    <div id="detail"></div>
  </main>
</div>
<script>
const DATA = __DATA__;
const OPTIONS = __OPTIONS__;
const HUMAN_FIELDS = __HUMAN_FIELDS__;
const STORAGE_KEY = "final_qa_v1_5d_decisions";
let decisions = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
let currentIndex = 0;
let filtered = DATA.slice();

function save(){ localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions)); }
function currentDecision(row){ return decisions[row.qa_item_id] || {}; }
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function badge(text, cls=""){ return `<span class="badge ${cls}">${esc(text || "(blank)")}</span>`; }
function decisionClass(row){
  const v = currentDecision(row).qa_final_decision || "";
  if(v === "pass") return "good";
  if(v === "fail") return "bad";
  if(v === "uncertain") return "warn";
  return "";
}
function parseMaybeJson(s){ try { return JSON.parse(s || "[]"); } catch(e) { return []; } }
function serviceName(item){ return item.service_name || item.name || item.title || "(unknown service)"; }
function apiName(item){ return item.api_name || item.name || item.title || "(unknown API)"; }
function isGoldService(item){ return ["1","true","yes"].includes(String(item.is_gold_service ?? "").toLowerCase()); }
function isGoldApi(item){ return ["1","true","yes"].includes(String(item.is_gold_api ?? "").toLowerCase()); }
function buildHierarchy(row){
  const services = parseMaybeJson(row.candidate_services_json);
  const apis = parseMaybeJson(row.candidate_apis_json);
  const serviceMap = new Map();
  services.forEach(s => {
    const name = serviceName(s);
    serviceMap.set(name, {svc:s, apis:[]});
  });
  const warnings = [];
  apis.forEach(a => {
    const sname = a.service_name || "(unknown service)";
    if(!serviceMap.has(sname)){
      warnings.push(`WARNING: API service name not found in candidate_services_json: ${sname}`);
      serviceMap.set(sname, {svc:{service_name:sname, is_gold_service:""}, apis:[]});
    }
    serviceMap.get(sname).apis.push(a);
  });
  let html = `<div class="service-tree">`;
  for(const [name, node] of serviceMap.entries()){
    html += `<div class="service-node"><div class="service-title">Service: ${esc(name)} ${isGoldService(node.svc)?badge("GOLD_SERVICE","good"):""}</div>`;
    if(node.apis.length){
      html += `<ul class="api-list">` + node.apis.map(a => `<li>API: ${esc(apiName(a))} ${isGoldApi(a)?badge("GOLD_API","good"):""}</li>`).join("") + `</ul>`;
    } else {
      html += `<div class="muted">No candidate APIs listed under this service.</div>`;
    }
    html += `</div>`;
  }
  html += `</div>`;
  if(warnings.length) html += `<pre>${esc(warnings.join("\\n"))}</pre>`;
  return html;
}
function hintList(row){
  const hints = [];
  const cs = Number(row.candidate_service_count || 0), gs = Number(row.gold_service_count || 0);
  const ca = Number(row.candidate_api_count || 0), ga = Number(row.gold_api_count || 0);
  hints.push(`candidate_service_count = ${cs}`);
  hints.push(`gold_service_count = ${gs}`);
  hints.push(`candidate_api_count = ${ca}`);
  hints.push(`gold_api_count = ${ga}`);
  hints.push(cs > gs ? "candidate_service_count > gold_service_count: service-level has choice space signal." : "candidate_service_count <= gold_service_count: service-level choice space may be weak.");
  hints.push(ca > ga ? "candidate_api_count > gold_api_count: API-level has choice space signal." : "candidate_api_count <= gold_api_count: API-level choice space may be weak.");
  hints.push(`API leak detector: ${row.api_leak_detector_status || "(blank)"}`);
  hints.push(`Service leak detector: ${row.service_leak_detector_status || "(blank)"}`);
  hints.push(`SemCap coverage: ${row.v12_capability_coverage_pred || "(blank)"}`);
  if(row.qa_bucket === "previous_failed_regression") hints.push("This item should be outside clean candidate after v1.4b.");
  if(row.qa_bucket === "current_clean_candidate_audit") hints.push("This item is currently clean candidate; pass only if all release-quality checks hold.");
  return `<ul class="hint-list">${hints.map(h => `<li>${esc(h)}</li>`).join("")}</ul>`;
}
function populateFilters(){
  const buckets = [...new Set(DATA.map(r => r.qa_bucket).filter(Boolean))].sort();
  const subbuckets = [...new Set(DATA.map(r => r.qa_subbucket).filter(Boolean))].sort();
  const bsel = document.getElementById("bucketFilter");
  const ssel = document.getElementById("subbucketFilter");
  buckets.forEach(b => { const o=document.createElement("option"); o.value=b; o.textContent=b; bsel.appendChild(o); });
  subbuckets.forEach(b => { const o=document.createElement("option"); o.value=b; o.textContent=b; ssel.appendChild(o); });
}
function applyFilters(){
  const q = document.getElementById("search").value.toLowerCase();
  const b = document.getElementById("bucketFilter").value;
  const sb = document.getElementById("subbucketFilter").value;
  const d = document.getElementById("decisionFilter").value;
  filtered = DATA.filter(row => {
    const text = `${row.qa_item_id} ${row.task_id} ${row.query_text}`.toLowerCase();
    const dec = currentDecision(row).qa_final_decision || "";
    return (!q || text.includes(q)) && (!b || row.qa_bucket === b) && (!sb || row.qa_subbucket === sb) && (!d || (d === "unfilled" ? !dec : dec === d));
  });
  if(currentIndex >= filtered.length) currentIndex = Math.max(0, filtered.length - 1);
  renderList(); renderDetail();
}
function renderList(){
  const box = document.getElementById("itemList");
  box.innerHTML = "";
  filtered.forEach((row, i) => {
    const div = document.createElement("div");
    div.className = "item" + (i === currentIndex ? " active" : "");
    div.innerHTML = `<div class="title">${esc(row.qa_item_id)} | ${esc(row.task_id)}</div>
      <div class="sub">${badge(row.qa_bucket)} ${badge(row.qa_subbucket,"info")} ${badge(currentDecision(row).qa_final_decision || "unfilled", decisionClass(row))}</div>
      <div class="sub">${esc(row.query_text).slice(0, 130)}</div>`;
    div.onclick = () => { currentIndex = i; renderList(); renderDetail(); };
    box.appendChild(div);
  });
  document.getElementById("countBox").textContent = `${filtered.length} / ${DATA.length} items`;
}
function kv(rows){
  return `<div class="kv">${rows.map(([k,v]) => `<div>${esc(k)}</div><div>${esc(v)}</div>`).join("")}</div>`;
}
function selectField(row, field){
  const saved = currentDecision(row)[field] || "";
  const opts = (OPTIONS[field] || [""]).map(v => `<option value="${esc(v)}" ${v===saved?"selected":""}>${esc(v || "(blank)")}</option>`).join("");
  return `<label>${esc(field)}<select data-field="${esc(field)}">${opts}</select></label>`;
}
function renderDetail(){
  const box = document.getElementById("detail");
  if(!filtered.length){ box.innerHTML = `<div class="panel">No item matched.</div>`; return; }
  const row = filtered[currentIndex];
  const dec = currentDecision(row);
  box.innerHTML = `
    <div class="panel">
      <h2>${esc(row.qa_item_id)} ${badge(row.qa_bucket)} ${badge(row.qa_subbucket,"info")}</h2>
      ${kv([
        ["expected_current_status", row.expected_current_status],
        ["task_id", row.task_id],
        ["task_type", row.task_type],
        ["source_group", row.source_group],
        ["prediction_level", row.prediction_level],
        ["v1.4b dryrun decision", row.v1_4b_dryrun_decision],
        ["v1.4b bucket", row.v1_4b_dryrun_bucket],
        ["clean confidence", row.v1_4b_clean_confidence_bucket],
        ["dedup", `${row.dedup_group_id || "unique"} / size ${row.dedup_group_size || "1"} / representative ${row.is_representative_candidate || ""}`],
      ])}
    </div>
    <div class="panel query"><h2>Query</h2>${esc(row.query_text)}</div>
    <div class="grid">
      <div class="panel"><h2>Candidate Services</h2><pre>${esc(row._candidate_services_preview)}</pre></div>
      <div class="panel"><h2>Candidate APIs</h2><pre>${esc(row._candidate_apis_preview)}</pre></div>
      <div class="panel"><h2>Gold Services</h2><pre>${esc(row._gold_services_preview)}</pre></div>
      <div class="panel"><h2>Gold APIs</h2><pre>${esc(row._gold_apis_preview)}</pre></div>
    </div>
    <div class="panel"><h2>Service/API Hierarchy View</h2>${buildHierarchy(row)}</div>
    <div class="panel"><h2>Rule-based Hints</h2>${hintList(row)}</div>
    <div class="grid">
      <div class="panel">
        <h2>v1.4b Policy Trace</h2>
        ${kv([
          ["blocking reasons", row.v1_4b_blocking_reasons],
          ["warning reasons", row.v1_4b_warning_reasons],
          ["triggered rules", row.v1_4b_triggered_rules],
          ["API leak", row.api_leak_detector_status],
          ["service leak", row.service_leak_detector_status],
        ])}
      </div>
      <div class="panel">
        <h2>SemCap v1.2 Trace</h2>
        ${kv([
          ["semantic", `${row.v12_semantic_alignment_pred} / ${row.v12_semantic_alignment_confidence}`],
          ["coverage", `${row.v12_capability_coverage_pred} / ${row.v12_capability_coverage_confidence}`],
          ["coverage reason", row.v12_capability_coverage_reason],
          ["gold set integrity", row.v12_gold_set_integrity_status],
          ["generic overtrust", row.v12_generic_search_overtrust_flag],
        ])}
      </div>
    </div>
    <div class="grid">
      <div class="panel"><h2>Core Requirements</h2><pre>${esc(row.v12_core_requirements_json)}</pre><h3>Covered</h3><pre>${esc(row.v12_covered_requirements_json)}</pre><h3>Missing</h3><pre>${esc(row.v12_missing_requirements_json)}</pre></div>
      <div class="panel"><h2>Guard Flags</h2><h3>Extra Gold Service</h3><pre>${esc(row.v12_extra_gold_service_flags_json)}</pre><h3>Domain Guards</h3><pre>${esc(row.v12_domain_specific_guard_flags_json)}</pre><h3>Tightening Rules</h3><pre>${esc(row.v12_tightening_triggered_rules_json)}</pre></div>
    </div>
    <div class="panel"><h2>Previous v1.5c Failure Reason</h2>${kv([
      ["severity", row.previous_v1_5c_qa_severity],
      ["failure type", row.previous_v1_5c_failure_type],
      ["reason", row.previous_v1_5c_reason],
    ])}</div>
    <div class="panel">
      <h2>审核顺序</h2>
      <ol class="hint-list">
        <li>query 真正要完成什么？</li>
        <li>gold service/API 是否覆盖所有核心需求？</li>
        <li>candidate 是否有真实选择空间？</li>
        <li>query 是否泄露 gold service/API 名称？</li>
        <li>SemCap missing/core guard 是否提示能力缺口？</li>
        <li>dedup 组是否合理？</li>
        <li>不确定时选 uncertain，不要强行 pass。</li>
      </ol>
    </div>
    <div class="panel">
      <h2>Human QA Fields</h2>
      <div class="form-grid">
        ${HUMAN_FIELDS.filter(f => f !== "qa_notes").map(f => selectField(row, f)).join("")}
        <label style="grid-column:1/-1">qa_notes<textarea data-field="qa_notes">${esc(dec.qa_notes || "")}</textarea></label>
      </div>
    </div>`;
  box.querySelectorAll("[data-field]").forEach(el => {
    el.onchange = () => {
      const field = el.getAttribute("data-field");
      decisions[row.qa_item_id] = decisions[row.qa_item_id] || {};
      decisions[row.qa_item_id][field] = el.value;
      save(); renderList();
    };
    el.oninput = el.onchange;
  });
}
function download(filename, text){
  const blob = new Blob(["\\ufeff" + text], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = filename; a.click();
  URL.revokeObjectURL(a.href);
}
function csvEscape(v){ const s = String(v ?? ""); return /[",\\n\\r]/.test(s) ? `"${s.replace(/"/g,'""')}"` : s; }
function exportCsv(){
  const headers = Object.keys(DATA[0] || {}).filter(h => !h.startsWith("_"));
  const lines = [headers.join(",")];
  DATA.forEach(row => {
    const dec = currentDecision(row);
    const merged = {...row};
    HUMAN_FIELDS.forEach(f => merged[f] = dec[f] || "");
    lines.push(headers.map(h => csvEscape(merged[h] || "")).join(","));
  });
  download("final_qa_review_items_v1_5d_user_reviewed.csv", lines.join("\\n"));
}
document.getElementById("search").oninput = applyFilters;
document.getElementById("bucketFilter").onchange = applyFilters;
document.getElementById("subbucketFilter").onchange = applyFilters;
document.getElementById("decisionFilter").onchange = applyFilters;
document.getElementById("prevBtn").onclick = () => { currentIndex = Math.max(0, currentIndex - 1); renderList(); renderDetail(); };
document.getElementById("nextBtn").onclick = () => { currentIndex = Math.min(filtered.length - 1, currentIndex + 1); renderList(); renderDetail(); };
document.getElementById("exportBtn").onclick = exportCsv;
document.getElementById("clearCurrentBtn").onclick = () => { const row = filtered[currentIndex]; if(row){ delete decisions[row.qa_item_id]; save(); renderList(); renderDetail(); } };
document.getElementById("clearAllBtn").onclick = () => { if(confirm("Clear all local decisions?")){ decisions = {}; save(); renderList(); renderDetail(); } };
populateFilters(); applyFilters();
</script>
</body>
</html>
"""
    html = html.replace("__DATA__", data_json).replace("__OPTIONS__", options_json).replace("__HUMAN_FIELDS__", human_fields_json)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8-sig")
    build_protocol_doc(DOC_DIR / "final_qa_review_protocol_v1_5d.md")
    print(f"html={args.output}")
    print(f"protocol={DOC_DIR / 'final_qa_review_protocol_v1_5d.md'}")
    print(f"row_count={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
