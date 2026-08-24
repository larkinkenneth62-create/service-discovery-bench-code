#!/usr/bin/env python
"""Restyle Round2 review HTML to match the v0.2 manual review app layout.

This changes only the HTML shell, CSS, and client-side rendering helpers. It
does not modify the underlying rows, assistant draft decisions, or CSV exports.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTML_PATH = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4" / "main_four_tasks_round2_review_app_80.html"
BACKUP_PATH = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4" / "main_four_tasks_round2_review_app_80.before_layout_v0_2_style.html"
REPORT_PATH = ROOT / "docs" / "phase1" / "main_four_tasks_round2_review_app_layout_v0_2_style_update_report.md"
SUMMARY_PATH = ROOT / "outputs" / "main_four_tasks_round2_small_dryrun_v0_4" / "round2_layout_v0_2_style_update_summary.json"
ARCHIVE_DIR = ROOT / "outputs" / "run_archives" / "2026-06-27_round2_review_app_layout_v0_2_style"


STYLE = r"""<style>
:root {
  --bg: #f6f7f9;
  --panel: #ffffff;
  --ink: #1d2430;
  --muted: #617085;
  --line: #d8dde5;
  --soft: #edf1f5;
  --focus: #2454a6;
  --green: #177245;
  --amber: #996b00;
  --red: #ad2f2f;
  --blue-soft: #e8f0ff;
  --green-soft: #e8f6ef;
  --amber-soft: #fff4d6;
  --red-soft: #ffe8e8;
  --shadow: 0 8px 24px rgba(20, 28, 38, 0.08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: Arial, "Microsoft YaHei", "PingFang SC", sans-serif;
  letter-spacing: 0;
}
button, input, select, textarea { font: inherit; }
.app { min-height: 100vh; display: grid; grid-template-rows: auto 1fr; }
header {
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  box-shadow: var(--shadow);
  z-index: 2;
}
.topbar {
  padding: 14px 18px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 14px;
  align-items: center;
}
h1 { margin: 0 0 6px; font-size: 20px; line-height: 1.25; }
.subtitle { color: var(--muted); font-size: 13px; line-height: 1.45; max-width: 1080px; }
.guide-panel {
  margin-top: 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fbfcfd;
  padding: 8px 10px;
  max-width: 1080px;
}
.guide-panel summary { cursor: pointer; font-weight: 700; color: #173b7a; font-size: 13px; }
.guide-panel ul { margin: 8px 0 0; padding-left: 20px; color: #344155; font-size: 13px; line-height: 1.5; }
.progress { display: grid; grid-template-columns: repeat(5, minmax(94px, auto)); gap: 8px; justify-content: end; }
.stat { border: 1px solid var(--line); background: var(--soft); border-radius: 6px; padding: 8px 10px; min-width: 94px; }
.stat b { display: block; font-size: 18px; line-height: 1.15; }
.stat span { color: var(--muted); font-size: 12px; }
.layout { display: grid; grid-template-columns: 340px minmax(0, 1fr); min-height: 0; }
.sidebar {
  border-right: 1px solid var(--line);
  background: #fbfcfd;
  overflow: auto;
  height: calc(100vh - 190px);
}
.filters {
  padding: 12px 14px;
  position: sticky;
  top: 0;
  z-index: 2;
  border-bottom: 1px solid var(--line);
  background: #fbfcfd;
}
label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; font-weight: 700; }
input, select, textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: white;
  color: var(--ink);
  padding: 8px;
}
.filter-row { display: grid; grid-template-columns: 1fr; gap: 8px; }
.button-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
button {
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--ink);
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
}
button.primary { background: var(--focus); border-color: var(--focus); color: white; font-weight: 700; }
button.danger { border-color: #d99; color: var(--red); }
.sample {
  display: block;
  width: 100%;
  text-align: left;
  border-bottom: 1px solid var(--line);
  background: transparent;
  padding: 12px 14px;
  cursor: pointer;
}
.sample:hover { background: var(--soft); }
.sample.active { background: var(--blue-soft); border-left: 4px solid var(--focus); padding-left: 10px; }
.sample .rid { display: flex; justify-content: space-between; gap: 8px; align-items: center; font-weight: 700; font-size: 13px; }
.sample .sample-title { color: var(--ink); overflow-wrap: anywhere; }
.badge, .tag {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 11px;
  font-weight: 700;
  background: var(--soft);
  color: var(--muted);
  margin: 2px 3px 2px 0;
}
.badge.high_confidence_candidate { background: var(--green-soft); color: var(--green); }
.badge.boundary_review { background: var(--amber-soft); color: var(--amber); }
.badge.high_risk_review { background: var(--red-soft); color: var(--red); }
.decision-pill { flex: 0 0 auto; border-radius: 999px; padding: 3px 7px; font-size: 11px; font-weight: 700; background: var(--soft); color: var(--muted); }
.decision-pill.keep { background: var(--green-soft); color: var(--green); }
.decision-pill.uncertain { background: var(--amber-soft); color: var(--amber); }
.decision-pill.remove { background: var(--red-soft); color: var(--red); }
.decision-pill.todo { background: var(--soft); color: var(--muted); }
main { overflow: auto; height: calc(100vh - 190px); padding: 18px; }
.nav, .toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.nav .group { display: flex; flex-wrap: wrap; gap: 8px; }
section, .section, details.section {
  display: block;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 14px;
  box-shadow: 0 4px 14px rgba(20, 28, 38, 0.04);
}
section h2, .section h2 { margin: 0 0 12px; font-size: 16px; }
h3 { margin: 12px 0 8px; font-size: 14px; }
details.section summary { cursor: pointer; font-weight: 700; color: #173b7a; }
.meta-table { display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 8px; }
.field {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fbfcfd;
  padding: 8px;
  min-width: 0;
}
.field span { display: block; color: var(--muted); font-size: 11px; margin-bottom: 4px; }
.field b { display: block; font-size: 13px; overflow-wrap: anywhere; }
pre {
  white-space: pre-wrap;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  padding: 10px;
  border-radius: 6px;
  overflow: auto;
  line-height: 1.45;
}
.bilingual-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.lang-card { border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: #fbfcfd; }
.lang-label { font-size: 12px; font-weight: 700; color: #374151; margin-bottom: 6px; }
.zh-text { color: #111827; line-height: 1.65; }
.translation-note, .hint-box {
  margin-top: 8px;
  border: 1px solid #cfd9ea;
  border-left: 4px solid var(--focus);
  border-radius: 6px;
  background: #f6f9ff;
  padding: 8px 10px;
  color: #27364c;
  font-size: 13px;
  line-height: 1.45;
}
.tree-service {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
  margin: 8px 0;
  background: #fbfcfd;
}
.tree-service:has(.gold) { border-color: #9ac5a9; background: var(--green-soft); }
.tree-service ul { margin: 8px 0 0; padding-left: 20px; display: grid; gap: 7px; }
.tree-service li { line-height: 1.4; overflow-wrap: anywhere; }
.gold { color: var(--green); font-weight: 700; }
.warning {
  border: 1px solid #e5b4a8;
  border-left: 4px solid var(--red);
  border-radius: 6px;
  background: #fff1ee;
  color: #7a221c;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.45;
  margin-top: 8px;
}
.api-zh { color: #1d4ed8; font-weight: 700; margin-left: 6px; }
.api-desc-zh { color: #374151; margin-top: 4px; font-size: 13px; }
.rule-grid { display: grid; grid-template-columns: repeat(2, minmax(240px, 1fr)); gap: 8px; margin-bottom: 10px; }
.rule-line { border: 1px solid var(--line); border-radius: 6px; background: #fbfcfd; padding: 8px 10px; font-size: 13px; line-height: 1.4; }
.rule-line b { color: #172033; }
.manual-section {
  border-left: 4px solid var(--focus);
}
.manual-grid { display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); gap: 10px; }
.manual-grid label { min-width: 0; }
textarea { min-height: 96px; resize: vertical; line-height: 1.45; }
.field-hint { display: block; color: #52647a; font-size: 11px; font-weight: 400; line-height: 1.35; }
.grid, .raw-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.small { color: var(--muted); font-size: 12px; line-height: 1.45; }
@media (max-width: 1080px) {
  .topbar { grid-template-columns: 1fr; }
  .progress { justify-content: stretch; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .layout { grid-template-columns: 1fr; }
  .sidebar { height: auto; max-height: 300px; border-right: 0; border-bottom: 1px solid var(--line); }
  main { height: auto; }
  .bilingual-grid, .manual-grid, .meta-table, .rule-grid, .grid, .raw-grid { grid-template-columns: 1fr; }
}
</style>"""


BODY_SHELL = r"""<body>
<div class="app">
  <header>
    <div class="topbar">
      <div>
        <h1>Main Four Tasks Round2 Small Dry-run Review App 80</h1>
        <div class="subtitle">
          这是 Round2 small dry-run 人工审核页，不是 final data。页面沿用第一轮 v0.2 的审核工作台排版，并保留 query、service、API 的双语辅助显示。最终判断仍以英文原文、候选层级和 gold 对齐关系为准；不确定就选 uncertain，不要强行 keep。
        </div>
        <details class="guide-panel" open>
          <summary>怎么区分 service-level 和 API-level</summary>
          <ul>
            <li>service-level 判断“需要哪些工具/服务”。</li>
            <li>API-level 判断“在这些工具/服务下面需要哪些具体接口”。</li>
            <li>先看 query 需要哪些大能力，再看 gold services 是否覆盖。</li>
            <li>再看 gold APIs 是否是这些服务下的具体正确接口。</li>
            <li>如果候选服务只有一个，通常不适合作为 service discovery。</li>
            <li>如果 query 直接出现 gold API 名，通常是 API leak。</li>
            <li>如果 service/API 边界不清，不要强行 keep，标 uncertain。</li>
          </ul>
        </details>
      </div>
      <div class="progress">
        <div class="stat"><b id="totalCount">0</b><span>总样本</span></div>
        <div class="stat"><b id="reviewedCount">0</b><span>已填写</span></div>
        <div class="stat"><b id="keepCount">0</b><span>keep</span></div>
        <div class="stat"><b id="uncertainCount">0</b><span>uncertain</span></div>
        <div class="stat"><b id="removeCount">0</b><span>remove</span></div>
      </div>
    </div>
  </header>
  <div class="layout">
    <aside class="sidebar">
      <div class="filters">
        <div class="filter-row">
          <label>搜索 task/query/service/api<input id="search" placeholder="搜索 task_id / query / service / api"></label>
          <label>task_type<select id="taskTypeFilter"><option value="">全部</option><option>multi_service_discovery</option><option>multi_api_recommendation</option></select></label>
          <label>mechanical bucket<select id="bucketFilter"><option value="">全部</option><option>high_confidence_candidate</option><option>boundary_review</option><option>high_risk_review</option></select></label>
          <label>审核状态<select id="doneFilter"><option value="">全部</option><option value="done">已填写</option><option value="todo">未填写</option></select></label>
        </div>
        <div class="button-row">
          <button class="primary" onclick="exportCsv()">Export decisions CSV</button>
        </div>
      </div>
      <div id="list"></div>
    </aside>
    <main>
      <div class="nav">
        <div class="group">
          <button onclick="prevItem()">上一条</button>
          <button onclick="nextItem()">下一条</button>
        </div>
        <div class="group">
          <button onclick="clearCurrent()">清空当前样本</button>
          <button class="danger" onclick="clearAll()">清空全部判断</button>
        </div>
      </div>
      <div id="detail"></div>
    </main>
  </div>
</div>
"""


HELPERS = r'''
function finalClass(value) {
  if (value === "keep_for_cleaning_candidate") return "keep";
  if (value === "uncertain") return "uncertain";
  if (value === "remove") return "remove";
  return "todo";
}
function finalLabel(value) {
  if (value === "keep_for_cleaning_candidate") return "keep";
  if (value === "uncertain") return "uncertain";
  if (value === "remove") return "remove";
  return "todo";
}
function renderStats() {
  const totals = {reviewed: 0, keep: 0, uncertain: 0, remove: 0};
  DATA.forEach(row => {
    const d = decisionFor(row.round2_review_id);
    if (isDone(row.round2_review_id)) totals.reviewed += 1;
    if (d.manual_final_decision === "keep_for_cleaning_candidate") totals.keep += 1;
    if (d.manual_final_decision === "uncertain") totals.uncertain += 1;
    if (d.manual_final_decision === "remove") totals.remove += 1;
  });
  document.getElementById("totalCount").textContent = DATA.length;
  document.getElementById("reviewedCount").textContent = totals.reviewed;
  document.getElementById("keepCount").textContent = totals.keep;
  document.getElementById("uncertainCount").textContent = totals.uncertain;
  document.getElementById("removeCount").textContent = totals.remove;
}
function clearAllToEmptyDecisions() {
  const cleared = {};
  DATA.forEach(row => { cleared[row.round2_review_id] = emptyDecision(); });
  return cleared;
}
'''


RENDER_LIST = r'''function renderList() {
  const box = document.getElementById("list");
  box.innerHTML = "";
  filteredData().forEach(([row, idx]) => {
    const d = decisionFor(row.round2_review_id);
    const div = document.createElement("div");
    div.className = "sample" + (idx === currentIndex ? " active" : "");
    div.onclick = () => { currentIndex = idx; render(); };
    div.innerHTML = `<div class="rid"><span class="sample-title">${row.round2_review_id} | ${row.task_id}</span><span class="decision-pill ${finalClass(d.manual_final_decision)}">${finalLabel(d.manual_final_decision)}</span></div>
      <div class="small">${row.task_type} · ${row.source_group}</div>
      <span class="badge">${row.leak_status}</span><span class="badge ${row.mechanical_screening_bucket}">${row.mechanical_screening_bucket}</span>`;
    box.appendChild(div);
  });
}'''


RENDER_DETAIL = r'''function renderDetail() {
  const row = DATA[currentIndex];
  const d = decisionFor(row.round2_review_id);
  const taskOptions = row.task_type === "multi_service_discovery"
    ? ["", "valid_multi_service_discovery", "should_be_multi_api", "should_be_single_service", "ordinary_or_unclear", "not_eligible"]
    : ["", "valid_multi_api_recommendation", "should_be_multi_service", "should_be_single_api", "ordinary_or_unclear", "not_eligible"];
  document.getElementById("detail").innerHTML = `
  <section class="section">
    <h2>${row.round2_review_id} | ${row.task_id}</h2>
    <div>
      <span class="badge">${row.task_type}</span><span class="badge">${row.source_group}</span><span class="badge ${row.mechanical_screening_bucket}">${row.mechanical_screening_bucket}</span><span class="decision-pill ${finalClass(d.manual_final_decision)}">${finalLabel(d.manual_final_decision)}</span>
    </div>
    <div class="meta-table" style="margin-top:10px">
      <div class="field"><span>candidate services</span><b>${row.candidate_service_count}</b></div>
      <div class="field"><span>gold services</span><b>${row.gold_service_count}</b></div>
      <div class="field"><span>candidate APIs</span><b>${row.candidate_api_count}</b></div>
      <div class="field"><span>gold APIs</span><b>${row.gold_api_count}</b></div>
      <div class="field"><span>leak_status</span><b>${row.leak_status}</b></div>
      <div class="field"><span>query mentions gold API</span><b>${row.query_mentions_any_gold_api}</b></div>
      <div class="field"><span>query mentions gold service</span><b>${row.query_mentions_any_gold_service}</b></div>
      <div class="field"><span>screening bucket</span><b>${row.mechanical_screening_bucket}</b></div>
    </div>
  </section>
  <section class="section">
    <h2>A. 用户需求 Query</h2>
    <div class="bilingual-grid">
      <div class="lang-card"><div class="lang-label">English Original</div><pre>${row.query_text}</pre></div>
      <div class="lang-card"><div class="lang-label">中文翻译</div><pre class="zh-text">${row.query_text_zh || "【待补译】"}</pre></div>
    </div>
    <div class="translation-note">翻译只用于提高人工审核速度；最终判断仍以英文 query、gold service/API 和候选层级为准。</div>
  </section>
  <section class="section">
    <h2>B. Service/API Hierarchy View</h2>
    ${hierarchy(row)}
  </section>
  <section class="section">
    <h2>C. Rule-based Hints</h2>
    <div class="rule-grid">
      <div class="rule-line"><b>candidate_service_count > gold_service_count</b><br>${Number(row.candidate_service_count) > Number(row.gold_service_count) ? "yes" : "no"}</div>
      <div class="rule-line"><b>candidate_api_count > gold_api_count</b><br>${Number(row.candidate_api_count) > Number(row.gold_api_count) ? "yes" : "no"}</div>
      <div class="rule-line"><b>query_mentions_any_gold_api</b><br>${row.query_mentions_any_gold_api}</div>
      <div class="rule-line"><b>query_mentions_any_gold_service</b><br>${row.query_mentions_any_gold_service}</div>
      <div class="rule-line"><b>generic tracking risk</b><br>${row.high_risk_generic_tracking}</div>
      <div class="rule-line"><b>generic address/postal risk</b><br>${row.high_risk_generic_address_or_postal}</div>
      <div class="rule-line"><b>service leak risk</b><br>${row.high_risk_service_leak}</div>
      <div class="rule-line"><b>gold not unique risk</b><br>${row.high_risk_gold_not_unique_possible}</div>
    </div>
    <div class="hint-box"><b>mechanical_screening_reason</b>${row.mechanical_screening_reason}</div>
  </section>
  <section class="section manual-section">
    <h2>D. 人工填写</h2>
    <div class="hint-box"><b>审核顺序</b>1. query 真正要完成什么？2. gold 是否被 query 唯一支持？3. candidate 是否有真实选择空间？4. 是否存在 API/service leak？5. 不确定就选 uncertain。</div>
    <div class="manual-grid">
      <label>manual_semantic_alignment<span class="field-hint">query 和 gold service/API 是否语义对齐</span>${selectHtml("manual_semantic_alignment", ["", "semantic_alignment_ok", "semantic_alignment_uncertain", "semantic_mismatch_uncertain"], d.manual_semantic_alignment)}</label>
      <label>manual_leak_check<span class="field-hint">query 是否直接泄露 gold API/service</span>${selectHtml("manual_leak_check", ["", "no_blocking_leak", "api_leak_blocking", "service_leak_only", "leak_uncertain"], d.manual_leak_check)}</label>
      <label>manual_candidate_gold_validity<span class="field-hint">候选和 gold 是否合理</span>${selectHtml("manual_candidate_gold_validity", ["", "valid", "candidate_set_too_small", "gold_incomplete", "gold_wrong", "uncertain"], d.manual_candidate_gold_validity)}</label>
      <label>manual_task_type_check<span class="field-hint">当前样本是否适合这个 task_type</span>${selectHtml("manual_task_type_check", taskOptions, d.manual_task_type_check)}</label>
      <label>manual_final_decision<span class="field-hint">最终保留、移除或待定</span>${selectHtml("manual_final_decision", ["", "keep_for_cleaning_candidate", "uncertain", "remove"], d.manual_final_decision)}</label>
    </div>
    <label style="margin-top:10px">manual_decision_reason<textarea rows="5" oninput="updateReason(this.value)">${d.manual_decision_reason || ""}</textarea></label>
  </section>
  <details class="section">
    <summary>E. Raw Candidate/Gold JSON</summary>
    <div class="raw-grid" style="margin-top:12px"><div><h3>Candidate Services</h3><pre>${JSON.stringify(parseJson(row.candidate_services_json, []), null, 2)}</pre></div>
    <div><h3>Gold Services</h3><pre>${JSON.stringify(parseJson(row.gold_services_zh_json || row.gold_services_json, []), null, 2)}</pre></div>
    <div><h3>Candidate APIs</h3><pre>${JSON.stringify(parseJson(row.candidate_apis_json, []), null, 2)}</pre></div>
    <div><h3>Gold APIs</h3><pre>${JSON.stringify(parseJson(row.gold_apis_json, []), null, 2)}</pre></div></div>
  </details>
  <details class="section">
    <summary>F. Metadata</summary>
    <pre style="margin-top:12px">${JSON.stringify(row, null, 2)}</pre>
  </details>`;
}'''


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement + text[end:]


def replace_function(text: str, name: str, replacement: str, next_name: str) -> str:
    start_marker = f"function {name}("
    end_marker = f"function {next_name}("
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + replacement + "\n" + text[end:]


def main() -> None:
    if not HTML_PATH.exists():
        raise FileNotFoundError(f"Missing HTML: {HTML_PATH}")
    if not BACKUP_PATH.exists():
        shutil.copy2(HTML_PATH, BACKUP_PATH)
        html = HTML_PATH.read_text(encoding="utf-8")
    else:
        html = BACKUP_PATH.read_text(encoding="utf-8")

    html = re.sub(r"<style>[\s\S]*?</style>", STYLE, html, count=1)
    html = replace_between(html, "<body>", "<script>", BODY_SHELL)

    if "function finalClass(value)" not in html:
        html = html.replace(
            "function isDone(id) {",
            HELPERS + "\nfunction isDone(id) {",
            1,
        )
    html = replace_function(html, "renderList", RENDER_LIST, "option")
    html = replace_function(html, "renderDetail", RENDER_DETAIL, "render")
    html = html.replace(
        "function render() { renderList(); renderDetail(); }",
        "function render() { renderStats(); renderList(); renderDetail(); }",
        1,
    )
    html = html.replace(
        'function clearAll() { if (confirm("确认清空全部人工判断？")) { decisions = {}; saveDecisions(); render(); } }',
        'function clearAll() { if (confirm("确认清空全部人工判断？")) { decisions = clearAllToEmptyDecisions(); saveDecisions(); render(); } }',
        1,
    )
    HTML_PATH.write_text(html, encoding="utf-8")

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "html_file": str(HTML_PATH),
        "backup_file": str(BACKUP_PATH),
        "report_file": str(REPORT_PATH),
        "archive_dir": str(ARCHIVE_DIR),
        "layout_basis": "main_four_tasks_manual_check_v0_2/main_four_tasks_review_app_40.html",
        "kept_bilingual_translations": True,
        "kept_export_csv": True,
        "kept_localStorage": True,
        "no_full_cleaning": True,
        "no_baseline": True,
        "no_training": True,
        "no_split": True,
        "no_top200": True,
        "no_full_g3_research": True,
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report = f"""# Round2 Review App Layout Update: v0.2-style

生成时间：{summary["generated_at"]}

## 本次改了什么

- 将 Round2 审核页从深色简易布局调整为第一轮 v0.2 风格的浅色审核工作台。
- 顶部增加进度卡片：总样本、已填写、keep、uncertain、remove。
- 左侧列表增加 final decision pill，方便快速看到每条的当前判断。
- 右侧信息顺序调整为：样本摘要、Query 双语、Service/API 层级树、rule-based hints、人工填写、Raw JSON、Metadata。
- Raw Candidate/Gold JSON 和 Metadata 改为默认折叠，减少干扰。
- 保留 query/service/API 双语翻译、assistant draft 默认值、localStorage、筛选、搜索、上一条/下一条、清空和导出 CSV。

## 未做的事情

- 没有跑 full cleaning。
- 没有跑 baseline。
- 没有训练模型。
- 没有 split。
- 没有继续 top200。
- 没有重新搜索 full G3。
- 没有改动 80 条样本数据或人工判断字段含义。

## 输出

- 更新 HTML：`{HTML_PATH}`
- 备份 HTML：`{BACKUP_PATH}`
- Summary：`{SUMMARY_PATH}`
- 归档目录：`{ARCHIVE_DIR}`
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in [HTML_PATH, BACKUP_PATH, REPORT_PATH, SUMMARY_PATH, Path(__file__)]:
        if path.exists():
            shutil.copy2(path, ARCHIVE_DIR / path.name)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
