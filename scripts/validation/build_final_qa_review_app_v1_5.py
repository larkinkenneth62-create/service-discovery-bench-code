from __future__ import annotations

import argparse
import json
from pathlib import Path

from final_qa_v1_5_common import (
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
        "# Final QA Review Protocol v1.5",
        "",
        f"Generated time: {now_text()}",
        "",
        "Final QA is a fixed-size release-quality audit.",
        "It is not another open-ended rule calibration round.",
        "",
        "## Scope",
        "",
        "- Review only the fixed QA sample in `outputs/final_qa_v1_5/final_qa_review_items_v1_5.csv`.",
        "- Do not create a final clean dataset from this sample.",
        "- Do not split, baseline, train, or revise rules in this phase.",
        "",
        "## Clean Candidate QA Standard",
        "",
        "For `clean_candidate_high_conf`, mark `pass` only when all conditions hold:",
        "",
        "- no blocking API leak",
        "- no service-level service leak",
        "- real choice space",
        "- semantic ok",
        "- coverage ok",
        "- gold in candidates",
        "- task type valid",
        "",
        "Mark `fail` when any release-blocking issue appears:",
        "",
        "- strong API leak",
        "- service-level no-choice",
        "- gold cannot cover query",
        "- semantic mismatch",
        "- gold missing",
        "- wrong task type",
        "",
        "## Removed QA Standard",
        "",
        "- pass: removal reason is valid.",
        "- fail: sample appears clean-ready and removal reason is clearly wrong.",
        "",
        "## Uncertain QA Standard",
        "",
        "- pass: uncertainty is justified.",
        "- fail: sample is obviously clean-ready or obviously removable.",
        "",
        "## Service Leak Only QA Standard",
        "",
        "- pass: query explicitly mentions gold service name in a way that blocks clean service-level discovery.",
        "- fail: service name match is a generic false positive.",
        "",
        "## Duplicate QA Standard",
        "",
        "- pass: duplicates are correctly grouped.",
        "- fail: unrelated queries are grouped together.",
        "",
        "## Release Thresholds For v1.6 Consideration",
        "",
        "- clean_candidate critical error rate <= 2%",
        "- clean_candidate major+critical error rate <= 5%",
        "- strong API leak in clean candidate = 0",
        "- service-level no-choice in clean candidate = 0",
        "- capability mismatch in clean candidate = 0",
        "- gold missing in clean candidate = 0",
        "- duplicate grouping critical error = 0",
    ]
    write_md(path, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build final QA v1.5 single-file HTML review app.")
    parser.add_argument("--input", type=Path, default=OUTPUT_DIR / "final_qa_review_items_v1_5.csv")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "final_qa_review_app_v1_5.html")
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Missing final QA review set: {args.input}")
    rows = read_csv(args.input)
    enriched = []
    for row in rows:
        copy = dict(row)
        copy["_candidate_services_preview"] = json_preview(row.get("candidate_services_json", ""), 1200)
        copy["_candidate_apis_preview"] = json_preview(row.get("candidate_apis_json", ""), 1600)
        copy["_gold_services_preview"] = json_preview(row.get("gold_services_json", ""), 800)
        copy["_gold_apis_preview"] = json_preview(row.get("gold_apis_json", ""), 1000)
        enriched.append(copy)

    data_json = json.dumps(enriched, ensure_ascii=False)
    options_json = json.dumps(QA_FIELD_OPTIONS, ensure_ascii=False)
    human_fields_json = json.dumps(QA_HUMAN_FIELDS, ensure_ascii=False)
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Final QA Review v1.5</title>
  <style>
    :root { --bg:#f6f7f9; --panel:#fff; --text:#1f2937; --muted:#6b7280; --line:#d9dee7; --accent:#1d4ed8; --bad:#b91c1c; --good:#047857; --warn:#b45309; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--text); }
    header { padding:14px 18px; background:#111827; color:#fff; display:flex; gap:16px; align-items:center; justify-content:space-between; }
    header h1 { font-size:18px; margin:0; font-weight:650; }
    header .meta { color:#cbd5e1; font-size:12px; }
    .layout { display:grid; grid-template-columns:320px minmax(0,1fr); height:calc(100vh - 54px); }
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
    .kv { display:grid; grid-template-columns:180px minmax(0,1fr); gap:6px 10px; font-size:13px; }
    .kv div:nth-child(odd) { color:var(--muted); }
    pre { white-space:pre-wrap; word-break:break-word; background:#f8fafc; border:1px solid #e5e7eb; border-radius:6px; padding:10px; font-size:12px; line-height:1.42; max-height:280px; overflow:auto; }
    .query { font-size:15px; line-height:1.55; background:#fff7ed; border-color:#fed7aa; }
    .badge { display:inline-block; border-radius:999px; padding:2px 7px; font-size:12px; border:1px solid var(--line); background:#fff; margin-right:4px; }
    .badge.good { color:var(--good); border-color:#a7f3d0; background:#ecfdf5; }
    .badge.bad { color:var(--bad); border-color:#fecaca; background:#fef2f2; }
    .badge.warn { color:var(--warn); border-color:#fed7aa; background:#fffbeb; }
    .form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
    .form-grid label { font-size:12px; color:var(--muted); display:grid; gap:4px; }
    textarea { min-height:78px; resize:vertical; }
    .muted { color:var(--muted); font-size:12px; }
    @media (max-width:900px) { .layout { grid-template-columns:1fr; height:auto; } aside { height:40vh; border-right:0; border-bottom:1px solid var(--line); } .grid, .form-grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
<header>
  <h1>Final QA Review v1.5</h1>
  <div class="meta">Fixed-size release-quality audit. Not a final clean dataset.</div>
</header>
<div class="layout">
  <aside>
    <div class="filters">
      <input id="search" placeholder="Search qa_item_id / task_id / query">
      <select id="bucketFilter"><option value="">All QA buckets</option></select>
      <select id="decisionFilter"><option value="">All decisions</option><option value="unfilled">Unfilled</option><option value="pass">pass</option><option value="fail">fail</option><option value="uncertain">uncertain</option></select>
      <div class="muted" id="countBox"></div>
    </div>
    <div class="list" id="itemList"></div>
  </aside>
  <main>
    <details class="protocol" open>
      <summary>审核说明 / Review Protocol</summary>
      <p>Final QA 是固定规模的发布质量抽查，不是重新开放式规则校准。</p>
      <p>审核顺序：先看 query 真正需要什么，再看 gold service/API 是否覆盖，再看候选是否有真实选择空间，最后检查 leak、task type 和 duplicate。</p>
      <p>如果证据不足，选 uncertain；不要为了凑 clean 而强行 pass。</p>
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
const STORAGE_KEY = "final_qa_v1_5_decisions";
let decisions = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
let currentIndex = 0;
let filtered = DATA.slice();

function save(){ localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions)); }
function currentDecision(row){ return decisions[row.qa_item_id] || {}; }
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function badge(text, cls=""){ return `<span class="badge ${cls}">${esc(text)}</span>`; }
function decisionClass(row){
  const v = currentDecision(row).qa_final_decision || "";
  if(v === "pass") return "good";
  if(v === "fail") return "bad";
  if(v === "uncertain") return "warn";
  return "";
}
function populateFilters(){
  const buckets = [...new Set(DATA.map(r => r.qa_bucket).filter(Boolean))].sort();
  const sel = document.getElementById("bucketFilter");
  buckets.forEach(b => { const o=document.createElement("option"); o.value=b; o.textContent=b; sel.appendChild(o); });
}
function applyFilters(){
  const q = document.getElementById("search").value.toLowerCase();
  const b = document.getElementById("bucketFilter").value;
  const d = document.getElementById("decisionFilter").value;
  filtered = DATA.filter(row => {
    const text = `${row.qa_item_id} ${row.task_id} ${row.query_text}`.toLowerCase();
    const dec = currentDecision(row).qa_final_decision || "";
    return (!q || text.includes(q)) && (!b || row.qa_bucket === b) && (!d || (d === "unfilled" ? !dec : dec === d));
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
      <div class="sub">${badge(row.qa_bucket)} ${badge(row.qa_subbucket)} ${badge(currentDecision(row).qa_final_decision || "unfilled", decisionClass(row))}</div>
      <div class="sub">${esc(row.query_text).slice(0, 120)}</div>`;
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
      <h2>${esc(row.qa_item_id)} ${badge(row.qa_bucket)} ${badge(row.qa_subbucket)}</h2>
      ${kv([
        ["task_id", row.task_id],
        ["task_type", row.task_type],
        ["source_group", row.source_group],
        ["prediction_level", row.prediction_level],
        ["dryrun_decision", row.dryrun_decision],
        ["dryrun_bucket", row.dryrun_bucket],
        ["clean_confidence_bucket", row.clean_confidence_bucket],
        ["dedup_group_id", row.dedup_group_id],
        ["dedup_group_size", row.dedup_group_size],
        ["is_representative_candidate", row.is_representative_candidate]
      ])}
    </div>
    <div class="panel query"><h2>Query</h2><div>${esc(row.query_text)}</div></div>
    <div class="grid">
      <div class="panel"><h2>Candidate Services</h2><pre>${esc(row._candidate_services_preview)}</pre></div>
      <div class="panel"><h2>Candidate APIs</h2><pre>${esc(row._candidate_apis_preview)}</pre></div>
      <div class="panel"><h2>Gold Services</h2><pre>${esc(row._gold_services_preview)}</pre></div>
      <div class="panel"><h2>Gold APIs</h2><pre>${esc(row._gold_apis_preview)}</pre></div>
    </div>
    <div class="panel">
      <h2>Detector / SemCap Trace</h2>
      ${kv([
        ["blocking_reasons", row.blocking_reasons],
        ["warning_reasons", row.warning_reasons],
        ["triggered_rules", row.triggered_rules],
        ["api_leak_detector_status", row.api_leak_detector_status],
        ["service_leak_detector_status", row.service_leak_detector_status],
        ["candidate_space_status", row.candidate_space_status],
        ["task_type_eligibility_status", row.task_type_eligibility_status],
        ["gold_in_candidate_services", row.gold_in_candidate_services],
        ["gold_in_candidate_apis", row.gold_in_candidate_apis],
        ["v1_semantic_alignment_pred", row.v1_semantic_alignment_pred],
        ["v1_semantic_alignment_confidence", row.v1_semantic_alignment_confidence],
        ["v1_capability_coverage_pred", row.v1_capability_coverage_pred],
        ["v1_capability_coverage_confidence", row.v1_capability_coverage_confidence],
        ["v1_capability_coverage_reason", row.v1_capability_coverage_reason]
      ])}
    </div>
    <div class="panel">
      <h2>QA Form</h2>
      <p class="muted">这些字段必须由人工填写。页面不会自动给出最终 QA 判断。</p>
      <div class="form-grid">${HUMAN_FIELDS.filter(f => f !== "qa_notes").map(f => selectField(row, f)).join("")}</div>
      <label class="muted" style="display:grid;gap:4px;margin-top:10px;">qa_notes<textarea data-field="qa_notes">${esc(dec.qa_notes || "")}</textarea></label>
    </div>`;
  box.querySelectorAll("[data-field]").forEach(el => {
    el.addEventListener("input", e => {
      const field = e.target.getAttribute("data-field");
      decisions[row.qa_item_id] = decisions[row.qa_item_id] || {};
      decisions[row.qa_item_id][field] = e.target.value;
      save(); renderList();
    });
  });
}
function clearCurrent(){
  const row = filtered[currentIndex]; if(!row) return;
  delete decisions[row.qa_item_id]; save(); renderList(); renderDetail();
}
function clearAll(){
  if(confirm("Clear all local QA decisions?")) { decisions = {}; save(); renderList(); renderDetail(); }
}
function csvEscape(v){ const s=String(v ?? ""); return /[",\\n]/.test(s) ? `"${s.replaceAll('"','""')}"` : s; }
function exportCSV(){
  const headers = Object.keys(DATA[0] || {}).filter(h => !h.startsWith("_"));
  const lines = [headers.join(",")];
  DATA.forEach(row => {
    const merged = {...row, ...(decisions[row.qa_item_id] || {})};
    lines.push(headers.map(h => csvEscape(merged[h] || "")).join(","));
  });
  const blob = new Blob(["\\ufeff" + lines.join("\\n")], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "final_qa_review_items_v1_5_user_reviewed.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}
document.getElementById("search").addEventListener("input", applyFilters);
document.getElementById("bucketFilter").addEventListener("change", applyFilters);
document.getElementById("decisionFilter").addEventListener("change", applyFilters);
document.getElementById("prevBtn").onclick = () => { currentIndex = Math.max(0, currentIndex - 1); renderList(); renderDetail(); };
document.getElementById("nextBtn").onclick = () => { currentIndex = Math.min(filtered.length - 1, currentIndex + 1); renderList(); renderDetail(); };
document.getElementById("exportBtn").onclick = exportCSV;
document.getElementById("clearCurrentBtn").onclick = clearCurrent;
document.getElementById("clearAllBtn").onclick = clearAll;
populateFilters(); applyFilters();
</script>
</body>
</html>"""
    html = html.replace("__DATA__", data_json).replace("__OPTIONS__", options_json).replace("__HUMAN_FIELDS__", human_fields_json)
    args.output.write_text(html, encoding="utf-8-sig")
    build_protocol_doc(DOC_DIR / "final_qa_review_protocol_v1_5.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
