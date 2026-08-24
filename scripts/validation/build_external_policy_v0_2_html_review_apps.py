#!/usr/bin/env python
"""Build offline HTML review apps for external policy v0.2 QA packs.

The generated HTML files are self-contained and offline. They embed CSV rows as
base64-encoded JSON, render user/raw fields with textContent, support
localStorage progress, import/export reviewed CSV, and do not call any external
API.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import shutil
from collections import Counter
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


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def csv_distribution(rows: list[dict[str, str]], col: str) -> dict[str, int]:
    return dict(Counter((row.get(col, "") or "").strip() for row in rows))


def b64_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def render_focus_html(focus_lines: list[str]) -> str:
    # Static focus text only. Row data is rendered in browser with textContent.
    escaped = []
    for line in focus_lines:
        escaped.append(
            line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
    return "\n".join(f"<li>{line}</li>" for line in escaped)


def build_review_html(
    *,
    title: str,
    source_kind: str,
    rows: list[dict[str, str]],
    fieldnames: list[str],
    output_filename: str,
    review_focus: list[str],
    highlight_fields: list[str],
    policy_field: str,
    label_field: str,
    source_group_field: str | None,
    special_filters: list[dict[str, str]],
    generated_at: str,
) -> str:
    rows_b64 = b64_json(rows)
    fields_b64 = b64_json(fieldnames)
    focus_html = render_focus_html(review_focus)
    highlight_b64 = b64_json(highlight_fields)
    filters_b64 = b64_json(special_filters)
    source_group_js = json.dumps(source_group_field or "", ensure_ascii=False)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --panel-2: #f0f3f6;
      --text: #162033;
      --muted: #5d6b82;
      --line: #d9e0ea;
      --blue: #2457d6;
      --green: #137a46;
      --amber: #9a5b00;
      --red: #b42318;
      --purple: #6750a4;
      --shadow: 0 1px 2px rgba(21, 30, 45, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      color: var(--text);
      background: var(--bg);
      line-height: 1.45;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 5;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      box-shadow: var(--shadow);
    }}
    .topbar {{
      display: grid;
      grid-template-columns: minmax(280px, 1fr) auto;
      gap: 16px;
      align-items: center;
      padding: 12px 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .boundary {{
      margin-top: 4px;
      color: var(--red);
      font-size: 12px;
      font-weight: 600;
    }}
    .stats {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .pill {{
      border: 1px solid var(--line);
      background: var(--panel-2);
      border-radius: 6px;
      padding: 4px 8px;
      font-size: 12px;
      white-space: nowrap;
    }}
    .pill.keep {{ color: var(--green); border-color: rgba(19,122,70,.35); }}
    .pill.uncertain {{ color: var(--amber); border-color: rgba(154,91,0,.35); }}
    .pill.remove {{ color: var(--red); border-color: rgba(180,35,24,.35); }}
    .controls {{
      display: grid;
      grid-template-columns: minmax(220px, 2fr) repeat(5, minmax(120px, 1fr));
      gap: 8px;
      padding: 0 16px 12px;
      align-items: end;
    }}
    label {{
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }}
    input, select, textarea, button {{
      font: inherit;
    }}
    input[type="text"], select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 8px 9px;
      min-height: 34px;
    }}
    textarea {{
      min-height: 90px;
      resize: vertical;
    }}
    .checkrow {{
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
      flex-wrap: wrap;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 320px minmax(360px, 1fr) 360px;
      gap: 12px;
      padding: 12px;
      height: calc(100vh - 130px);
      min-height: 620px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .panel-head {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
      font-weight: 700;
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
    }}
    .scroll {{
      overflow: auto;
      height: 100%;
    }}
    .list {{
      height: calc(100% - 42px);
      overflow: auto;
    }}
    .list-item {{
      display: grid;
      gap: 4px;
      width: 100%;
      text-align: left;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: #fff;
      padding: 10px 12px;
      cursor: pointer;
      color: var(--text);
    }}
    .list-item:hover, .list-item.active {{
      background: #eef4ff;
    }}
    .list-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
      color: var(--muted);
      font-size: 11px;
    }}
    .list-query {{
      font-size: 13px;
      max-height: 38px;
      overflow: hidden;
    }}
    main.scroll, aside.scroll {{
      padding: 12px;
    }}
    .section {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
      background: #fff;
    }}
    .section h2 {{
      margin: 0 0 10px;
      font-size: 15px;
      letter-spacing: 0;
    }}
    .field {{
      display: grid;
      gap: 4px;
      margin-bottom: 10px;
    }}
    .field-name {{
      font-size: 12px;
      color: var(--muted);
      font-weight: 700;
    }}
    .field-value {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border: 1px solid var(--line);
      background: #fbfcfe;
      border-radius: 6px;
      padding: 8px;
      min-height: 34px;
      font-size: 13px;
    }}
    .bilingual-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin: 8px 0;
    }}
    .lang-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 10px;
      min-height: 72px;
    }}
    .lang-label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      margin-bottom: 6px;
    }}
    .en-text, .zh-text {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 13px;
    }}
    .zh-text {{
      color: #1f3a2e;
      background: #f1fbf5;
      border-radius: 6px;
      padding: 7px;
      border: 1px solid #cdebd8;
    }}
    .translation-note {{
      background: #eef2ff;
      border: 1px solid #c7d2fe;
      border-radius: 6px;
      padding: 8px 10px;
      color: #3730a3;
      font-size: 12px;
      margin: 8px 0;
    }}
    .hierarchy-list {{
      display: grid;
      gap: 8px;
      margin-top: 8px;
    }}
    .hierarchy-item {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 9px;
    }}
    .hierarchy-title {{
      font-weight: 800;
      margin-bottom: 5px;
      overflow-wrap: anywhere;
    }}
    .hierarchy-meta {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }}
    .badge {{
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 11px;
      margin-left: 5px;
      color: var(--purple);
      background: #f6f2ff;
    }}
    details {{
      border: 1px solid var(--line);
      border-radius: 6px;
      margin-bottom: 8px;
      background: #fbfcfe;
    }}
    summary {{
      cursor: pointer;
      padding: 8px;
      font-weight: 700;
      color: var(--muted);
    }}
    pre {{
      margin: 0;
      padding: 8px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 12px;
      border-top: 1px solid var(--line);
      color: #263449;
    }}
    .buttons {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 8px 0;
    }}
    button {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 6px;
      padding: 7px 10px;
      cursor: pointer;
      min-height: 34px;
    }}
    button:hover {{ border-color: var(--blue); color: var(--blue); }}
    button.primary {{ background: var(--blue); color: #fff; border-color: var(--blue); }}
    button.keep {{ border-color: rgba(19,122,70,.45); color: var(--green); }}
    button.uncertain {{ border-color: rgba(154,91,0,.45); color: var(--amber); }}
    button.remove {{ border-color: rgba(180,35,24,.45); color: var(--red); }}
    .hint {{
      font-size: 12px;
      color: var(--muted);
      background: #f7f9fc;
      border: 1px solid var(--line);
      padding: 8px;
      border-radius: 6px;
    }}
    .focus-list {{
      margin: 0;
      padding-left: 20px;
      color: #303d52;
      font-size: 13px;
    }}
    .danger {{
      color: var(--red);
      font-weight: 700;
    }}
    .warning {{
      color: var(--amber);
      font-weight: 700;
    }}
    .ok {{
      color: var(--green);
      font-weight: 700;
    }}
    .review-grid {{
      display: grid;
      gap: 10px;
    }}
    .footer-note {{
      font-size: 12px;
      color: var(--muted);
      padding: 8px 0 0;
    }}
    .hidden {{ display: none !important; }}
    @media (max-width: 1180px) {{
      .layout {{ grid-template-columns: 280px 1fr; height: auto; }}
      aside.scroll {{ grid-column: 1 / -1; }}
    }}
    @media (max-width: 760px) {{
      .topbar, .controls, .layout {{ grid-template-columns: 1fr; height: auto; }}
      .stats {{ justify-content: flex-start; }}
      .list {{ max-height: 360px; }}
      .bilingual-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>{title}</h1>
        <div class="boundary">This HTML is for manual review only. It does not authorize merge/final dataset/split/baseline/training.</div>
      </div>
      <div class="stats" id="stats"></div>
    </div>
    <div class="controls">
      <label>Search review_item_id / task_id / query_text
        <input id="searchInput" type="text" placeholder="Type to filter">
      </label>
      <label>Policy decision
        <select id="policyFilter"></select>
      </label>
      <label>QA final decision
        <select id="qaFilter"></select>
      </label>
      <label id="groupFilterLabel">Group / task type
        <select id="groupFilter"></select>
      </label>
      <label>Special filter
        <select id="specialFilter"></select>
      </label>
      <div class="checkrow">
        <label><input id="pendingOnly" type="checkbox"> Pending only</label>
        <label><input id="rulesOnly" type="checkbox"> Has blocking/warning</label>
      </div>
    </div>
  </header>

  <div class="layout">
    <section class="panel">
      <div class="panel-head">
        <span>Samples</span>
        <span id="visibleCount" class="pill"></span>
      </div>
      <div class="list" id="sampleList"></div>
    </section>

    <main class="panel scroll">
      <div class="section">
        <h2>Review Focus</h2>
        <ol class="focus-list">
          {focus_html}
        </ol>
      </div>
      <div class="section">
        <h2>Current Sample</h2>
        <div id="currentFields"></div>
      </div>
      <div class="section">
        <h2>Query / Candidate / Gold 双语视图</h2>
        <div class="translation-note">页面必须直接显示中文译文。中文译文用于快速人工审核；最终判断仍以英文原文、candidate/gold 结构和 policy evidence 为准。</div>
        <div id="bilingualView"></div>
      </div>
      <div class="section">
        <h2>JSON Fields</h2>
        <div class="hint">JSON is read-only. It is pretty-printed and collapsed by default so you can inspect gold/candidate services/APIs, rules, and evidence without editing raw fields.</div>
        <div id="jsonFields"></div>
      </div>
    </main>

    <aside class="panel scroll">
      <div class="section">
        <h2>Quick Actions</h2>
        <div class="buttons">
          <button class="keep" id="markKeep">Mark current as keep</button>
          <button class="uncertain" id="markUncertain">Mark current as uncertain</button>
          <button class="remove" id="markRemove">Mark current as remove</button>
          <button id="prevBtn">Previous</button>
          <button id="nextBtn">Next</button>
          <button id="copyTaskId">Copy task_id</button>
          <button id="copyQuery">Copy query_text</button>
        </div>
        <div class="hint">Shortcuts: J / ArrowRight = next, K / ArrowLeft = previous, 1 = keep, 2 = uncertain, 3 = remove, S = save local, E = export draft.</div>
      </div>

      <div class="section">
        <h2>Editable Review Panel</h2>
        <div class="review-grid" id="reviewPanel"></div>
      </div>

      <div class="section">
        <h2>Save / Import / Export</h2>
        <label>Reviewer ID helper
          <input id="reviewerIdHelper" type="text" placeholder="Enter reviewer id">
        </label>
        <div class="buttons">
          <button id="fillReviewerAll">Fill reviewer_id for all blank rows</button>
          <button id="fillReviewedAtCurrent">Fill reviewed_at for current row</button>
          <button id="fillReviewedAtAll">Fill reviewed_at for all reviewed rows missing reviewed_at</button>
          <button id="saveLocal" class="primary">Save local</button>
          <button id="exportReviewed">Export reviewed CSV</button>
          <button id="exportDraft">Export draft CSV</button>
          <button id="importCsvButton">Import reviewed CSV</button>
          <button id="resetCurrent">Reset current row review fields</button>
          <button id="clearLocal">Clear all local saved progress</button>
        </div>
        <input id="importCsvInput" class="hidden" type="file" accept=".csv,text/csv">
        <div id="validationSummary" class="hint"></div>
        <div class="footer-note">Export uses UTF-8 with BOM and quotes all fields. Raw fields remain unchanged; only review fields are updated from the editable panel.</div>
      </div>
    </aside>
  </div>

  <script>
  "use strict";
  const SOURCE_KIND = {json.dumps(source_kind)};
  const OUTPUT_FILENAME = {json.dumps(output_filename)};
  const POLICY_FIELD = {json.dumps(policy_field)};
  const LABEL_FIELD = {json.dumps(label_field)};
  const SOURCE_GROUP_FIELD = {source_group_js};
  const GENERATED_AT = {json.dumps(generated_at)};
  const ROWS_B64 = "{rows_b64}";
  const FIELDNAMES_B64 = "{fields_b64}";
  const HIGHLIGHT_FIELDS = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{highlight_b64}"), c => c.charCodeAt(0))));
  const SPECIAL_FILTERS = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{filters_b64}"), c => c.charCodeAt(0))));
  const QA_FIELDS = {json.dumps(QA_FIELDS)};
  const ERROR_LABELS = [
    "service_leak", "api_leak", "semantic_mismatch", "capability_mismatch",
    "candidate_space_invalid", "invalid_gold_service", "invalid_gold_api",
    "missing_core_requirement", "wrong_task_type", "not_strong_composable",
    "adapter_warning_blocking", "unsupported_external_source",
    "duplicate_or_nonrepresentative", "missing_context", "rewrite_needed",
    "candidate_space_reconstruction_needed", "other"
  ];
  const ALLOWED = {{
    qa_final_decision: ["", "keep_for_cleaning_candidate", "uncertain", "remove"],
    qa_semantic_alignment_check: ["", "ok", "uncertain", "mismatch"],
    qa_capability_coverage_check: ["", "coverage_ok", "coverage_uncertain", "coverage_mismatch", "not_applicable"],
    qa_candidate_validity_check: ["", "valid", "uncertain", "invalid"],
    qa_service_catalog_check: ["", "valid_catalog", "catalog_uncertain", "invalid_catalog", "not_applicable"],
    qa_task_type_check: ["", "task_type_ok", "task_type_uncertain", "task_type_invalid", "composable_not_strong_dependency", "not_applicable"],
    qa_leakage_check: ["", "no_obvious_leak", "service_leak_blocking", "api_leak_blocking", "leak_uncertain"],
    qa_severity: ["", "none", "low", "medium", "high", "critical"]
  }};
  const PHRASE_TRANSLATIONS = [
    ["can you help me", "请帮我"], ["can you", "你能否"], ["i need to", "我需要"], ["i would like to", "我想"],
    ["i want to", "我想"], ["please provide", "请提供"], ["please fetch", "请获取"], ["please find", "请查找"],
    ["find me", "帮我查找"], ["search for", "搜索"], ["look up", "查询"], ["get the details", "获取详情"],
    ["provide me with", "为我提供"], ["recommend", "推荐"], ["current status", "当前状态"], ["tracking number", "追踪号码"],
    ["postal code", "邮政编码"], ["address", "地址"], ["weather forecast", "天气预报"], ["current weather", "当前天气"],
    ["exchange rate", "汇率"], ["latest news", "最新新闻"], ["news article", "新闻文章"], ["image search", "图片搜索"],
    ["hotel", "酒店"], ["restaurant", "餐厅"], ["movie", "电影"], ["tv show", "电视剧"], ["translation", "翻译"],
    ["translate", "翻译"], ["list of", "列表"], ["all countries", "所有国家"], ["flight", "航班"], ["airport", "机场"],
    ["package", "包裹"], ["mail", "邮件"], ["container", "集装箱"], ["carrier", "承运商"], ["api", "接口"],
    ["service", "服务"], ["endpoint", "接口端点"], ["candidate", "候选项"], ["gold", "正确答案"]
  ];
  const WORD_TRANSLATIONS = {{
    "academic": "学术", "research": "研究", "papers": "论文", "topic": "主题", "game": "游戏", "chess": "国际象棋",
    "religious": "宗教", "guidance": "指导", "life": "生活", "music": "音乐", "audio": "音频", "notation": "记谱",
    "children": "儿童", "learning": "学习", "activities": "活动", "fashion": "时尚", "assistant": "助手",
    "question": "问题", "data": "数据", "companies": "公司", "brand": "品牌", "logo": "标志", "colors": "颜色",
    "font": "字体", "search": "搜索", "preview": "预览", "assets": "资源", "price": "价格", "petrol": "汽油",
    "forecast": "预报", "map": "地图", "ranked": "排名", "generate": "生成", "text": "文本", "app": "应用",
    "recommendations": "推荐", "books": "书籍", "keyword": "关键词", "domain": "域名", "traffic": "流量",
    "threats": "威胁", "companies": "公司", "database": "数据库", "cases": "案例", "profile": "资料",
    "image": "图片", "images": "图片", "details": "详情", "status": "状态", "description": "描述", "created": "创建",
    "updated": "更新", "user": "用户", "country": "国家", "countries": "国家", "city": "城市", "cities": "城市",
    "population": "人口", "landmarks": "地标", "blacklisted": "黑名单", "currency": "货币", "bitcoin": "比特币",
    "ethereum": "以太坊", "recipe": "食谱", "cocktail": "鸡尾酒", "food": "食物", "nutrition": "营养",
    "concert": "演唱会", "artist": "艺术家", "festival": "节日", "streaming": "流媒体", "season": "季",
    "episode": "集", "title": "标题", "rating": "评分", "metadata": "元数据", "language": "语言",
    "languages": "语言", "sentence": "句子", "dictionary": "词典", "definition": "定义", "health": "健康",
    "jokes": "笑话", "phone": "电话", "verify": "验证", "example": "示例", "order": "订单", "product": "产品",
    "category": "类别", "categories": "类别", "project": "项目", "demo": "演示", "test": "测试", "source": "来源",
    "tool": "工具", "plugin": "插件", "catalog": "目录", "query": "需求", "request": "请求", "capability": "能力"
  }};
  const rows = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(ROWS_B64), c => c.charCodeAt(0))));
  const fieldnames = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(FIELDNAMES_B64), c => c.charCodeAt(0))));
  const storageKey = "external_policy_v0_2_review_" + SOURCE_KIND;
  let state = rows.map((row) => Object.assign({{}}, row));
  let filteredIndexes = [];
  let currentFilteredPosition = 0;

  function byId(id) {{ return document.getElementById(id); }}
  function valueOf(row, key) {{ return (row && row[key] !== undefined && row[key] !== null) ? String(row[key]) : ""; }}
  function isReviewed(row) {{ return valueOf(row, "qa_final_decision").trim() !== ""; }}
  function hasRules(row) {{
    return Object.keys(row).some((key) => (key.includes("blocking_rules") || key.includes("warning_rules")) && valueOf(row, key).trim() && valueOf(row, key).trim() !== "[]");
  }}
  function getCurrentIndex() {{
    if (!filteredIndexes.length) return -1;
    if (currentFilteredPosition < 0) currentFilteredPosition = 0;
    if (currentFilteredPosition >= filteredIndexes.length) currentFilteredPosition = filteredIndexes.length - 1;
    return filteredIndexes[currentFilteredPosition];
  }}
  function setText(el, text) {{
    el.textContent = text === undefined || text === null ? "" : String(text);
  }}
  function makeEl(tag, className, text) {{
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) setText(el, text);
    return el;
  }}
  function uniqueValues(key) {{
    const vals = Array.from(new Set(state.map((row) => valueOf(row, key).trim()).filter(Boolean)));
    vals.sort();
    return vals;
  }}
  function fillSelect(select, values, allLabel) {{
    select.replaceChildren();
    const all = document.createElement("option");
    all.value = "";
    all.textContent = allLabel || "All";
    select.appendChild(all);
    values.forEach((value) => {{
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value;
      select.appendChild(opt);
    }});
  }}
  function setupFilters() {{
    fillSelect(byId("policyFilter"), uniqueValues(POLICY_FIELD), "All policy decisions");
    fillSelect(byId("qaFilter"), ALLOWED.qa_final_decision.filter(Boolean), "All QA decisions");
    const groupKey = SOURCE_GROUP_FIELD || "task_type";
    fillSelect(byId("groupFilter"), uniqueValues(groupKey), "All groups / task types");
    fillSelect(byId("specialFilter"), SPECIAL_FILTERS.map((item) => item.id), "All special filters");
    const special = byId("specialFilter");
    Array.from(special.options).forEach((opt) => {{
      const match = SPECIAL_FILTERS.find((item) => item.id === opt.value);
      if (match) opt.textContent = match.label;
    }});
    ["searchInput", "policyFilter", "qaFilter", "groupFilter", "specialFilter", "pendingOnly", "rulesOnly"].forEach((id) => {{
      byId(id).addEventListener("input", () => {{ applyFilters(); render(); }});
      byId(id).addEventListener("change", () => {{ applyFilters(); render(); }});
    }});
  }}
  function specialMatch(row, id) {{
    if (!id) return true;
    if (id === "rewrite_needed") return ["metatool_rewrite_needed", "stable_rewrite_needed"].some((key) => valueOf(row, key).toLowerCase() === "yes");
    if (id === "reconstruction_needed") return valueOf(row, "stable_reconstruction_needed").toLowerCase() === "yes";
    if (id === "composable_review") return valueOf(row, "stable_requires_composable_dependency_review").toLowerCase() === "yes";
    if (id === "service_leak") return rowText(row).toLowerCase().includes("service_leak");
    if (id === "api_leak") return rowText(row).toLowerCase().includes("api_leak");
    if (id === "candidate_space_invalid") return rowText(row).toLowerCase().includes("candidate_space_invalid");
    if (id === "missing_context") return rowText(row).toLowerCase().includes("missing_context");
    if (id === "composable_not_strong_dependency") return rowText(row).toLowerCase().includes("composable_not_strong_dependency");
    return true;
  }}
  function rowText(row) {{
    return Object.values(row).join("\\n");
  }}
  function applyFilters() {{
    const search = byId("searchInput").value.trim().toLowerCase();
    const policy = byId("policyFilter").value;
    const qa = byId("qaFilter").value;
    const group = byId("groupFilter").value;
    const special = byId("specialFilter").value;
    const pendingOnly = byId("pendingOnly").checked;
    const rulesOnly = byId("rulesOnly").checked;
    const groupKey = SOURCE_GROUP_FIELD || "task_type";
    filteredIndexes = [];
    state.forEach((row, index) => {{
      const haystack = [valueOf(row, "review_item_id"), valueOf(row, "task_id"), valueOf(row, "query_text")].join("\\n").toLowerCase();
      if (search && !haystack.includes(search)) return;
      if (policy && valueOf(row, POLICY_FIELD) !== policy) return;
      if (qa && valueOf(row, "qa_final_decision") !== qa) return;
      if (group && valueOf(row, groupKey) !== group) return;
      if (pendingOnly && isReviewed(row)) return;
      if (rulesOnly && !hasRules(row)) return;
      if (!specialMatch(row, special)) return;
      filteredIndexes.push(index);
    }});
    if (currentFilteredPosition >= filteredIndexes.length) currentFilteredPosition = Math.max(0, filteredIndexes.length - 1);
  }}
  function renderStats() {{
    const total = state.length;
    const reviewed = state.filter(isReviewed).length;
    const keep = state.filter((row) => valueOf(row, "qa_final_decision") === "keep_for_cleaning_candidate").length;
    const uncertain = state.filter((row) => valueOf(row, "qa_final_decision") === "uncertain").length;
    const remove = state.filter((row) => valueOf(row, "qa_final_decision") === "remove").length;
    const stats = byId("stats");
    stats.replaceChildren();
    [
      ["total", total, ""],
      ["reviewed", reviewed, ""],
      ["pending", total - reviewed, ""],
      ["keep", keep, "keep"],
      ["uncertain", uncertain, "uncertain"],
      ["remove", remove, "remove"]
    ].forEach(([label, count, cls]) => {{
      const pill = makeEl("span", "pill " + cls, label + ": " + count);
      stats.appendChild(pill);
    }});
    setText(byId("visibleCount"), filteredIndexes.length + " visible");
  }}
  function renderList() {{
    const list = byId("sampleList");
    list.replaceChildren();
    filteredIndexes.forEach((rowIndex, pos) => {{
      const row = state[rowIndex];
      const btn = makeEl("button", "list-item" + (pos === currentFilteredPosition ? " active" : ""));
      btn.addEventListener("click", () => {{
        currentFilteredPosition = pos;
        render();
      }});
      const meta = makeEl("div", "list-meta");
      [valueOf(row, "review_item_id"), valueOf(row, "task_id"), valueOf(row, POLICY_FIELD), valueOf(row, "qa_final_decision") || "pending"].forEach((item) => {{
        const span = makeEl("span", "pill", item);
        meta.appendChild(span);
      }});
      const query = makeEl("div", "list-query", valueOf(row, "query_text"));
      btn.appendChild(meta);
      btn.appendChild(query);
      list.appendChild(btn);
    }});
  }}
  function translateTextForReview(text, kind) {{
    const raw = String(text || "").trim();
    if (!raw) return "无内容。";
    let zh = raw;
    PHRASE_TRANSLATIONS.forEach(([en, cn]) => {{
      zh = zh.replace(new RegExp(en.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&"), "gi"), cn);
    }});
    zh = zh.replace(/\\b[A-Za-z][A-Za-z-]*\\b/g, (word) => {{
      const key = word.toLowerCase();
      return WORD_TRANSLATIONS[key] || word;
    }});
    zh = zh
      .replace(/\\bI'm\\b/gi, "我")
      .replace(/\\bI am\\b/gi, "我")
      .replace(/\\bmy\\b/gi, "我的")
      .replace(/\\bme\\b/gi, "我")
      .replace(/\\band\\b/gi, "和")
      .replace(/\\bor\\b/gi, "或")
      .replace(/\\bwith\\b/gi, "使用/带有")
      .replace(/\\bfor\\b/gi, "用于")
      .replace(/\\bfrom\\b/gi, "来自")
      .replace(/\\bto\\b/gi, "到/为了")
      .replace(/\\bof\\b/gi, "的")
      .replace(/\\bin\\b/gi, "在")
      .replace(/\\bon\\b/gi, "关于/在")
      .replace(/\\s+/g, " ")
      .trim();
    if (kind === "service") return "服务中文说明：" + zh;
    if (kind === "api") return "接口中文说明：" + zh;
    if (kind === "query") return "用户需求中文翻译：" + zh;
    return "中文翻译：" + zh;
  }}
  function parseJsonSafe(value) {{
    const raw = String(value || "").trim();
    if (!raw) return null;
    try {{ return JSON.parse(raw); }} catch (err) {{ return null; }}
  }}
  function asList(value) {{
    if (!value) return [];
    return Array.isArray(value) ? value : [value];
  }}
  function addBilingualCard(container, title, englishText, chineseText) {{
    const wrap = makeEl("div", "field");
    wrap.appendChild(makeEl("div", "field-name", title));
    const grid = makeEl("div", "bilingual-grid");
    const en = makeEl("div", "lang-card");
    en.appendChild(makeEl("div", "lang-label", "English 原文"));
    en.appendChild(makeEl("div", "en-text", englishText || ""));
    const zh = makeEl("div", "lang-card");
    zh.appendChild(makeEl("div", "lang-label", "中文翻译"));
    zh.appendChild(makeEl("div", "zh-text", chineseText || "无内容。"));
    grid.appendChild(en);
    grid.appendChild(zh);
    wrap.appendChild(grid);
    container.appendChild(wrap);
  }}
  function itemName(item) {{
    if (typeof item === "string") return item;
    if (!item || typeof item !== "object") return "";
    return item.service_name || item.api_name || item.tool_name || item.name || item.endpoint || "";
  }}
  function itemDescription(item) {{
    if (typeof item === "string") return "";
    if (!item || typeof item !== "object") return "";
    return item.service_description || item.api_description || item.description || item.summary || "";
  }}
  function itemEnglishSummary(item, kind) {{
    if (typeof item === "string") return item;
    if (!item || typeof item !== "object") return "";
    const parts = [];
    if (item.service_name) parts.push("Service: " + item.service_name);
    if (item.api_name) parts.push("API: " + item.api_name);
    if (item.tool_name) parts.push("Tool: " + item.tool_name);
    if (item.category_name) parts.push("Category: " + item.category_name);
    const desc = itemDescription(item);
    if (desc) parts.push("Description: " + desc);
    if (item.is_gold_service === 1 || item.is_gold_service === "1") parts.push("[GOLD_SERVICE]");
    if (item.is_gold_api === 1 || item.is_gold_api === "1") parts.push("[GOLD_API]");
    return parts.join("\\n") || JSON.stringify(item);
  }}
  function itemChineseSummary(item, kind) {{
    if (typeof item === "string") return (kind === "api" ? "接口/工具：" : "服务：") + item + "；中文说明：" + translateTextForReview(item, kind);
    if (!item || typeof item !== "object") return "无内容。";
    const parts = [];
    if (item.service_name) parts.push("服务名称：" + item.service_name);
    if (item.api_name) parts.push("接口名称：" + item.api_name);
    if (item.tool_name) parts.push("工具名称：" + item.tool_name);
    if (item.category_name) parts.push("类别：" + translateTextForReview(item.category_name, kind).replace(/^中文翻译：|^服务中文说明：|^接口中文说明：/, ""));
    const desc = itemDescription(item);
    if (desc) parts.push(translateTextForReview(desc, kind));
    if (!desc && itemName(item)) parts.push(translateTextForReview(itemName(item), kind));
    if (item.is_gold_service === 1 || item.is_gold_service === "1") parts.push("标记：正确服务。");
    if (item.is_gold_api === 1 || item.is_gold_api === "1") parts.push("标记：正确接口。");
    return parts.join("\\n") || "无内容。";
  }}
  function renderBilingualList(container, title, rawValue, kind) {{
    const data = asList(parseJsonSafe(rawValue));
    const details = document.createElement("details");
    details.open = title.includes("Gold") || title.includes("gold");
    const summary = makeEl("summary", "", title + " / " + (kind === "api" ? "接口双语" : "服务双语") + "（" + data.length + "）");
    details.appendChild(summary);
    const list = makeEl("div", "hierarchy-list");
    if (!data.length) {{
      list.appendChild(makeEl("div", "hint", "无可解析 JSON 内容。"));
    }}
    data.forEach((item, idx) => {{
      const box = makeEl("div", "hierarchy-item");
      const titleLine = makeEl("div", "hierarchy-title", (idx + 1) + ". " + (itemName(item) || "Unnamed"));
      if (item && (item.is_gold_service === 1 || item.is_gold_service === "1")) titleLine.appendChild(makeEl("span", "badge", "GOLD_SERVICE"));
      if (item && (item.is_gold_api === 1 || item.is_gold_api === "1")) titleLine.appendChild(makeEl("span", "badge", "GOLD_API"));
      box.appendChild(titleLine);
      addBilingualCard(box, "原文 / 中文", itemEnglishSummary(item, kind), itemChineseSummary(item, kind));
      list.appendChild(box);
    }});
    details.appendChild(list);
    container.appendChild(details);
  }}
  function renderBilingualReview(row, container) {{
    container.replaceChildren();
    addBilingualCard(container, "用户需求 Query", valueOf(row, "query_text"), translateTextForReview(valueOf(row, "query_text"), "query"));
    const fieldPlan = [
      ["Gold services", "gold_services_json", "service"],
      ["Candidate services", "candidate_services_json", "service"],
      ["Gold APIs", "gold_apis_json", "api"],
      ["Candidate APIs", "candidate_apis_json", "api"],
      ["Gold tools / APIs", "gold_tools_or_apis_json", "api"],
      ["Available tools / APIs", "available_tools_or_apis_json", "api"]
    ];
    fieldPlan.forEach(([title, key, kind]) => {{
      if (row[key] !== undefined) renderBilingualList(container, title, row[key], kind);
    }});
  }}
  function renderField(container, key, value) {{
    const wrap = makeEl("div", "field");
    wrap.appendChild(makeEl("div", "field-name", key));
    wrap.appendChild(makeEl("div", "field-value", value));
    container.appendChild(wrap);
  }}
  function prettyJson(value) {{
    const raw = String(value || "").trim();
    if (!raw) return "";
    try {{
      return JSON.stringify(JSON.parse(raw), null, 2);
    }} catch (err) {{
      return raw;
    }}
  }}
  function renderDetails(container, key, value) {{
    const details = document.createElement("details");
    const summary = makeEl("summary", "", key);
    const pre = makeEl("pre", "", prettyJson(value));
    details.appendChild(summary);
    details.appendChild(pre);
    container.appendChild(details);
  }}
  function renderCurrent() {{
    const index = getCurrentIndex();
    const currentFields = byId("currentFields");
    const bilingualView = byId("bilingualView");
    const jsonFields = byId("jsonFields");
    currentFields.replaceChildren();
    bilingualView.replaceChildren();
    jsonFields.replaceChildren();
    if (index < 0) {{
      currentFields.appendChild(makeEl("div", "hint", "No sample matches current filters."));
      bilingualView.appendChild(makeEl("div", "hint", "No sample matches current filters."));
      return;
    }}
    const row = state[index];
    const compact = [
      "review_item_id", "task_id", "source_dataset", "task_type", "task_type_guess",
      "source_group", "stable_group", "query_text", POLICY_FIELD, LABEL_FIELD,
      "metatool_rewrite_needed", "stable_reconstruction_needed", "stable_rewrite_needed",
      "stable_requires_composable_dependency_review", "adapter_notes", "metatool_policy_notes",
      "stable_policy_notes"
    ];
    compact.forEach((key) => {{
      if (fieldnames.includes(key) || row[key] !== undefined) renderField(currentFields, key, valueOf(row, key));
    }});
    HIGHLIGHT_FIELDS.forEach((key) => {{
      if (row[key] !== undefined && compact.indexOf(key) === -1) renderField(currentFields, key, valueOf(row, key));
    }});
    renderBilingualReview(row, bilingualView);
    fieldnames.filter((key) => key.endsWith("_json")).forEach((key) => renderDetails(jsonFields, key, valueOf(row, key)));
  }}
  function makeSelectField(key, values) {{
    const label = makeEl("label", "", key);
    const select = document.createElement("select");
    values.forEach((value) => {{
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value || "(blank)";
      select.appendChild(opt);
    }});
    select.value = valueOf(state[getCurrentIndex()] || {{}}, key);
    select.addEventListener("change", () => updateCurrentField(key, select.value));
    label.appendChild(select);
    return label;
  }}
  function makeTextField(key, multiline) {{
    const label = makeEl("label", "", key);
    const input = multiline ? document.createElement("textarea") : document.createElement("input");
    if (!multiline) input.type = "text";
    input.value = valueOf(state[getCurrentIndex()] || {{}}, key);
    input.addEventListener("input", () => updateCurrentField(key, input.value));
    label.appendChild(input);
    return label;
  }}
  function renderReviewPanel() {{
    const panel = byId("reviewPanel");
    panel.replaceChildren();
    if (getCurrentIndex() < 0) return;
    panel.appendChild(makeSelectField("qa_final_decision", ALLOWED.qa_final_decision));
    panel.appendChild(makeSelectField("qa_semantic_alignment_check", ALLOWED.qa_semantic_alignment_check));
    panel.appendChild(makeSelectField("qa_capability_coverage_check", ALLOWED.qa_capability_coverage_check));
    panel.appendChild(makeSelectField("qa_candidate_validity_check", ALLOWED.qa_candidate_validity_check));
    panel.appendChild(makeSelectField("qa_service_catalog_check", ALLOWED.qa_service_catalog_check));
    panel.appendChild(makeSelectField("qa_task_type_check", ALLOWED.qa_task_type_check));
    panel.appendChild(makeSelectField("qa_leakage_check", ALLOWED.qa_leakage_check));
    panel.appendChild(makeSelectField("qa_severity", ALLOWED.qa_severity));
    const errorWrap = makeTextField("qa_error_type", false);
    const help = makeEl("div", "hint", "Suggested labels: " + ERROR_LABELS.join("; "));
    errorWrap.appendChild(help);
    panel.appendChild(errorWrap);
    panel.appendChild(makeTextField("qa_notes", true));
    panel.appendChild(makeTextField("reviewer_id", false));
    panel.appendChild(makeTextField("reviewed_at", false));
  }}
  function updateCurrentField(key, value) {{
    const index = getCurrentIndex();
    if (index < 0 || !QA_FIELDS.includes(key)) return;
    state[index][key] = value;
    saveLocal(false);
    renderStats();
    updateValidationSummary();
  }}
  function markDecision(value) {{
    const index = getCurrentIndex();
    if (index < 0) return;
    state[index].qa_final_decision = value;
    saveLocal(false);
    render();
  }}
  function currentReviewedAt() {{
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }}
  function saveLocal(showAlert) {{
    localStorage.setItem(storageKey, JSON.stringify(state.map((row) => {{
      const item = {{ review_item_id: row.review_item_id }};
      QA_FIELDS.forEach((key) => item[key] = valueOf(row, key));
      return item;
    }})));
    if (showAlert) alert("Saved to localStorage.");
  }}
  function loadLocal() {{
    const raw = localStorage.getItem(storageKey);
    if (!raw) return;
    try {{
      const items = JSON.parse(raw);
      const byReviewId = new Map(items.map((item) => [item.review_item_id, item]));
      state.forEach((row) => {{
        const patch = byReviewId.get(row.review_item_id);
        if (patch) QA_FIELDS.forEach((key) => row[key] = valueOf(patch, key));
      }});
    }} catch (err) {{
      console.warn("Failed to load local progress", err);
    }}
  }}
  function validateState() {{
    let reviewed = 0;
    let missingDecision = 0;
    let missingErrorForDecision = 0;
    let missingErrorForSeverity = 0;
    let missingReviewer = 0;
    let missingReviewedAt = 0;
    let invalidAllowed = 0;
    state.forEach((row) => {{
      if (isReviewed(row)) reviewed += 1;
      if (!valueOf(row, "qa_final_decision").trim()) missingDecision += 1;
      if (["uncertain", "remove"].includes(valueOf(row, "qa_final_decision")) && !valueOf(row, "qa_error_type").trim()) missingErrorForDecision += 1;
      if (valueOf(row, "qa_severity") && valueOf(row, "qa_severity") !== "none" && !valueOf(row, "qa_error_type").trim()) missingErrorForSeverity += 1;
      if (isReviewed(row) && !valueOf(row, "reviewer_id").trim()) missingReviewer += 1;
      if (isReviewed(row) && !valueOf(row, "reviewed_at").trim()) missingReviewedAt += 1;
      Object.keys(ALLOWED).forEach((key) => {{
        const value = valueOf(row, key);
        if (value && !ALLOWED[key].includes(value)) invalidAllowed += 1;
      }});
    }});
    return {{
      totalRows: state.length,
      reviewedRows: reviewed,
      pendingRows: state.length - reviewed,
      rowsMissingQaFinalDecision: missingDecision,
      rowsWithUncertainOrRemoveMissingQaErrorType: missingErrorForDecision,
      rowsWithSeverityNotNoneMissingQaErrorType: missingErrorForSeverity,
      rowsMissingReviewerId: missingReviewer,
      rowsMissingReviewedAt: missingReviewedAt,
      invalidAllowedValueCount: invalidAllowed
    }};
  }}
  function validationHasIssues(v) {{
    return v.pendingRows > 0 || v.rowsMissingQaFinalDecision > 0 || v.rowsWithUncertainOrRemoveMissingQaErrorType > 0 || v.rowsWithSeverityNotNoneMissingQaErrorType > 0 || v.rowsMissingReviewerId > 0 || v.rowsMissingReviewedAt > 0 || v.invalidAllowedValueCount > 0;
  }}
  function validationText(v) {{
    return [
      "total rows: " + v.totalRows,
      "reviewed rows: " + v.reviewedRows,
      "pending rows: " + v.pendingRows,
      "rows missing qa_final_decision: " + v.rowsMissingQaFinalDecision,
      "uncertain/remove missing qa_error_type: " + v.rowsWithUncertainOrRemoveMissingQaErrorType,
      "severity != none missing qa_error_type: " + v.rowsWithSeverityNotNoneMissingQaErrorType,
      "rows missing reviewer_id: " + v.rowsMissingReviewerId,
      "rows missing reviewed_at: " + v.rowsMissingReviewedAt,
      "invalid allowed value count: " + v.invalidAllowedValueCount
    ].join("\\n");
  }}
  function updateValidationSummary() {{
    const v = validateState();
    setText(byId("validationSummary"), validationText(v));
  }}
  function csvEscape(value) {{
    return '"' + String(value === undefined || value === null ? "" : value).replace(/"/g, '""') + '"';
  }}
  function buildCsv() {{
    const allFields = fieldnames.slice();
    QA_FIELDS.forEach((key) => {{
      if (!allFields.includes(key)) allFields.push(key);
    }});
    const lines = [allFields.map(csvEscape).join(",")];
    state.forEach((row) => {{
      lines.push(allFields.map((key) => csvEscape(valueOf(row, key))).join(","));
    }});
    return "\\ufeff" + lines.join("\\r\\n") + "\\r\\n";
  }}
  function downloadCsv(filename) {{
    const blob = new Blob([buildCsv()], {{ type: "text/csv;charset=utf-8" }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }}
  function exportCsv(forceDraft) {{
    const v = validateState();
    const issues = validationHasIssues(v);
    const summary = validationText(v);
    const base = OUTPUT_FILENAME.replace(/\\.csv$/i, "");
    const filename = forceDraft || issues ? base + "_draft.csv" : OUTPUT_FILENAME;
    const ok = confirm("Validation summary before export:\\n\\n" + summary + "\\n\\nFile name: " + filename + "\\n\\nContinue export?");
    if (ok) downloadCsv(filename);
  }}
  function parseCsv(text) {{
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
    const rowsOut = [];
    let row = [];
    let cell = "";
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {{
      const ch = text[i];
      const next = text[i + 1];
      if (inQuotes) {{
        if (ch === '"' && next === '"') {{
          cell += '"';
          i++;
        }} else if (ch === '"') {{
          inQuotes = false;
        }} else {{
          cell += ch;
        }}
      }} else {{
        if (ch === '"') {{
          inQuotes = true;
        }} else if (ch === ",") {{
          row.push(cell);
          cell = "";
        }} else if (ch === "\\n") {{
          row.push(cell);
          rowsOut.push(row);
          row = [];
          cell = "";
        }} else if (ch !== "\\r") {{
          cell += ch;
        }}
      }}
    }}
    if (cell.length || row.length) {{
      row.push(cell);
      rowsOut.push(row);
    }}
    if (!rowsOut.length) return [];
    const header = rowsOut[0];
    return rowsOut.slice(1).filter((r) => r.some((v) => v !== "")).map((r) => {{
      const obj = {{}};
      header.forEach((key, idx) => obj[key] = r[idx] || "");
      return obj;
    }});
  }}
  function importCsvFile(file) {{
    const reader = new FileReader();
    reader.onload = () => {{
      const imported = parseCsv(String(reader.result || ""));
      const byReviewId = new Map(imported.map((row) => [row.review_item_id, row]));
      let patched = 0;
      state.forEach((row) => {{
        const patch = byReviewId.get(row.review_item_id);
        if (patch) {{
          QA_FIELDS.forEach((key) => {{
            if (patch[key] !== undefined) row[key] = patch[key];
          }});
          patched += 1;
        }}
      }});
      saveLocal(false);
      applyFilters();
      render();
      alert("Imported review fields for " + patched + " rows.");
    }};
    reader.readAsText(file, "utf-8");
  }}
  function render() {{
    renderStats();
    renderList();
    renderCurrent();
    renderReviewPanel();
    updateValidationSummary();
  }}
  function wireButtons() {{
    byId("markKeep").addEventListener("click", () => markDecision("keep_for_cleaning_candidate"));
    byId("markUncertain").addEventListener("click", () => markDecision("uncertain"));
    byId("markRemove").addEventListener("click", () => markDecision("remove"));
    byId("prevBtn").addEventListener("click", () => {{ currentFilteredPosition -= 1; render(); }});
    byId("nextBtn").addEventListener("click", () => {{ currentFilteredPosition += 1; render(); }});
    byId("copyTaskId").addEventListener("click", async () => {{ const row = state[getCurrentIndex()]; if (row) await navigator.clipboard.writeText(valueOf(row, "task_id")); }});
    byId("copyQuery").addEventListener("click", async () => {{ const row = state[getCurrentIndex()]; if (row) await navigator.clipboard.writeText(valueOf(row, "query_text")); }});
    byId("fillReviewerAll").addEventListener("click", () => {{
      const reviewer = byId("reviewerIdHelper").value.trim() || prompt("Reviewer ID to fill for blank rows:");
      if (!reviewer) return;
      state.forEach((row) => {{ if (!valueOf(row, "reviewer_id").trim()) row.reviewer_id = reviewer; }});
      saveLocal(false);
      render();
    }});
    byId("fillReviewedAtCurrent").addEventListener("click", () => {{
      const idx = getCurrentIndex();
      if (idx >= 0) state[idx].reviewed_at = currentReviewedAt();
      saveLocal(false);
      render();
    }});
    byId("fillReviewedAtAll").addEventListener("click", () => {{
      const stamp = currentReviewedAt();
      state.forEach((row) => {{ if (isReviewed(row) && !valueOf(row, "reviewed_at").trim()) row.reviewed_at = stamp; }});
      saveLocal(false);
      render();
    }});
    byId("saveLocal").addEventListener("click", () => saveLocal(true));
    byId("exportReviewed").addEventListener("click", () => exportCsv(false));
    byId("exportDraft").addEventListener("click", () => exportCsv(true));
    byId("importCsvButton").addEventListener("click", () => byId("importCsvInput").click());
    byId("importCsvInput").addEventListener("change", (event) => {{
      const file = event.target.files && event.target.files[0];
      if (file) importCsvFile(file);
      event.target.value = "";
    }});
    byId("resetCurrent").addEventListener("click", () => {{
      const idx = getCurrentIndex();
      if (idx < 0) return;
      if (!confirm("Reset current row review fields?")) return;
      QA_FIELDS.forEach((key) => state[idx][key] = "");
      saveLocal(false);
      render();
    }});
    byId("clearLocal").addEventListener("click", () => {{
      if (!confirm("Clear all local saved progress for this review app?")) return;
      localStorage.removeItem(storageKey);
      state = rows.map((row) => Object.assign({{}}, row));
      applyFilters();
      render();
    }});
    document.addEventListener("keydown", (event) => {{
      const tag = (event.target && event.target.tagName || "").toLowerCase();
      if (["input", "textarea", "select"].includes(tag)) return;
      if (event.key === "j" || event.key === "ArrowRight") {{ currentFilteredPosition += 1; render(); }}
      if (event.key === "k" || event.key === "ArrowLeft") {{ currentFilteredPosition -= 1; render(); }}
      if (event.key === "1") markDecision("keep_for_cleaning_candidate");
      if (event.key === "2") markDecision("uncertain");
      if (event.key === "3") markDecision("remove");
      if (event.key.toLowerCase() === "s") saveLocal(true);
      if (event.key.toLowerCase() === "e") exportCsv(true);
    }});
  }}
  loadLocal();
  setupFilters();
  wireButtons();
  applyFilters();
  render();
  </script>
</body>
</html>
"""


def write_index(path: Path, generated_at: str) -> None:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>External Policy v0.2 Review Index</title>
  <style>
    body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 0; background: #f6f7f9; color: #162033; }}
    main {{ max-width: 880px; margin: 40px auto; background: #fff; border: 1px solid #d9e0ea; border-radius: 8px; padding: 24px; }}
    h1 {{ margin-top: 0; font-size: 22px; }}
    a {{ color: #2457d6; font-weight: 700; }}
    .warning {{ border: 1px solid #f0c36a; background: #fff8e7; padding: 12px; border-radius: 6px; }}
    li {{ margin: 8px 0; }}
  </style>
</head>
<body>
<main>
  <h1>External Policy v0.2 Review Index</h1>
  <p>Generated at: {generated_at}</p>
  <div class="warning">
    This HTML is for manual review only. Final output must be exported as reviewed CSV.
    It does not authorize merge, final dataset generation, split, baseline, or training.
  </div>
  <ul>
    <li><a href="metatool/metatool_leakage_policy_review_app_v0_2.html">MetaTool v0.2 Leakage Policy Review</a></li>
    <li><a href="stabletoolbench/stabletoolbench_filter_policy_review_app_v0_2.html">StableToolBench v0.2 Filter Policy Review</a></li>
  </ul>
  <p>After manual review, export reviewed CSV from each page and run the reviewed CSV validation / summarization scripts.</p>
</main>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def self_check_html(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()
    external_resource_markers = [
        'src="http',
        "src='http",
        'href="http',
        "href='http",
        "@import",
        "cdn.",
        "cdnjs",
        "unpkg",
        "jsdelivr",
    ]
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "has_external_resource_reference": any(marker in lower for marker in external_resource_markers),
        "has_fetch_or_network_code": any(marker in lower for marker in ["fetch(", "xmlhttprequest", "websocket("]),
        "mentions_qwen_or_openai_invocation": any(marker in lower for marker in ["qwen_api_key", "openai_api_key", "dashscope_api_key"]),
        "has_localstorage": "localstorage" in lower,
        "has_export_csv": "exportcsv" in lower and "downloadcsv" in lower,
        "has_import_csv": "importcsv" in lower and "parsecsv" in lower,
        "has_textcontent_rendering": "textcontent" in lower,
    }


def archive_outputs(project_root: Path, files: list[Path]) -> None:
    archive_dir = project_root / "outputs/run_archives/2026-07-05_external_policy_v0_2_html_review_app"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for src in files:
        if not src.exists():
            continue
        if src.is_relative_to(project_root / "docs"):
            dest = archive_dir / src.relative_to(project_root / "docs")
        elif src.is_relative_to(project_root / "outputs"):
            dest = archive_dir / src.relative_to(project_root / "outputs")
        elif src.is_relative_to(project_root / "scripts"):
            dest = archive_dir / src.relative_to(project_root)
        else:
            dest = archive_dir / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build external policy v0.2 offline HTML review apps.")
    parser.add_argument("--project-root", default=".", help="Project root. Default: current directory.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    docs_dir = project_root / "docs/phase1"
    summary_dir = project_root / "outputs/external_policy_v0_2_html_review_app"
    summary_dir.mkdir(parents=True, exist_ok=True)

    required = [
        project_root / "docs/phase1/metatool_policy_v0_2_consistency_audit.md",
        project_root / "docs/phase1/stabletoolbench_policy_v0_2_consistency_audit.md",
        project_root / "docs/phase1/external_policy_v0_2_csv_review_handoff_manifest.md",
        project_root / "docs/phase1/external_policy_v0_2_csv_review_instruction.md",
        project_root / "docs/phase1/external_policy_v0_2_consistency_go_no_go.md",
        project_root / "outputs/external_policy_v0_2_consistency_audit/metatool_v0_2_consistency_audit.json",
        project_root / "outputs/external_policy_v0_2_consistency_audit/stabletoolbench_v0_2_consistency_audit.json",
        project_root / "outputs/external_policy_v0_2_consistency_audit/stabletoolbench_v0_2_primary_decision_distribution.csv",
        project_root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv",
        project_root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv",
    ]
    missing = [str(path.relative_to(project_root)) for path in required if not path.exists()]
    if missing:
        missing_path = summary_dir / "MISSING_INPUTS.md"
        missing_path.write_text("# Missing Inputs\n\n" + "\n".join(f"- `{item}`" for item in missing) + "\n", encoding="utf-8")
        print(f"Missing required inputs: {len(missing)}")
        print(missing_path)
        return 2

    generated_at = now_iso()
    metatool_csv = project_root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv"
    stable_csv = project_root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv"
    metatool_fields, metatool_rows = read_csv_rows(metatool_csv)
    stable_fields, stable_rows = read_csv_rows(stable_csv)

    metatool_html = project_root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_app_v0_2.html"
    stable_html = project_root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_app_v0_2.html"
    index_html = project_root / "outputs/external_qa_v0_2/external_policy_v0_2_review_index.html"

    metatool_focus = [
        "Check whether the query semantically matches the gold service.",
        "Check whether the gold service is in the 199-service candidate catalog.",
        "Check whether the query directly names the gold plugin/service/tool.",
        "If the query directly names the gold service, do not keep it in the clean service discovery set; consider rewrite_needed, uncertain, or remove.",
        "If the query only overlaps with common words such as search, weather, translate, review, or calculator, do not automatically mark blocking leak.",
        "If the query lacks standalone context, such as only naming a product/project/file, mark uncertain or remove.",
        "MetaTool currently supports only single_service_discovery_external, not API-level benchmark construction.",
    ]
    stable_focus = [
        "Check whether source_specific_keep_candidate_as_is has a valid choice space.",
        "If candidate APIs equal gold APIs, there is no real selection space; do not keep as-is.",
        "Check whether candidate_space_reconstruction_pool truly needs candidate reconstruction.",
        "Check whether leakage_rewrite_pool truly reflects service/API leakage.",
        "Check whether composable_dependency_review_pool has a real dependency chain.",
        "Do not automatically treat G3 as strong composable.",
        "Demo/test/generic project sources must not enter the clean benchmark.",
        "Check whether query and gold APIs semantically match and whether gold APIs cover the user's required action.",
    ]

    metatool_html.write_text(
        build_review_html(
            title="MetaTool v0.2 Leakage Policy Review",
            source_kind="metatool_v0_2",
            rows=metatool_rows,
            fieldnames=metatool_fields,
            output_filename="metatool_leakage_policy_review_items_v0_2_reviewed.csv",
            review_focus=metatool_focus,
            highlight_fields=[
                "metatool_policy_decision",
                "metatool_leakage_policy_label",
                "metatool_rewrite_needed",
                "metatool_rewrite_reason",
                "source_tool_or_plugin_name",
                "gold_services_json",
                "candidate_services_json",
                "metatool_blocking_rules_json",
                "metatool_warning_rules_json",
                "metatool_policy_evidence_json",
            ],
            policy_field="metatool_policy_decision",
            label_field="metatool_leakage_policy_label",
            source_group_field="task_type",
            special_filters=[
                {"id": "service_leak", "label": "blocking rule contains service_leak"},
                {"id": "missing_context", "label": "blocking rule contains missing_context"},
                {"id": "rewrite_needed", "label": "rewrite_needed"},
            ],
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )

    stable_html.write_text(
        build_review_html(
            title="StableToolBench v0.2 Filter Policy Review",
            source_kind="stabletoolbench_v0_2",
            rows=stable_rows,
            fieldnames=stable_fields,
            output_filename="stabletoolbench_filter_policy_review_items_v0_2_reviewed.csv",
            review_focus=stable_focus,
            highlight_fields=[
                "stable_policy_decision",
                "stable_policy_label",
                "stable_group",
                "task_type_guess",
                "stable_reconstruction_needed",
                "stable_rewrite_needed",
                "stable_requires_composable_dependency_review",
                "available_tools_or_apis_json",
                "gold_tools_or_apis_json",
                "stable_blocking_rules_json",
                "stable_warning_rules_json",
                "stable_policy_evidence_json",
            ],
            policy_field="stable_policy_decision",
            label_field="stable_policy_label",
            source_group_field="stable_group",
            special_filters=[
                {"id": "candidate_space_invalid", "label": "blocking rule contains candidate_space_invalid"},
                {"id": "api_leak", "label": "blocking rule contains api_leak"},
                {"id": "service_leak", "label": "blocking rule contains service_leak"},
                {"id": "composable_not_strong_dependency", "label": "blocking rule contains composable_not_strong_dependency"},
                {"id": "reconstruction_needed", "label": "reconstruction_needed"},
                {"id": "rewrite_needed", "label": "rewrite_needed"},
                {"id": "composable_review", "label": "requires_composable_dependency_review"},
            ],
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    write_index(index_html, generated_at)

    checks = {
        "metatool": self_check_html(metatool_html),
        "stabletoolbench": self_check_html(stable_html),
        "index": self_check_html(index_html),
    }
    self_check_pass = all(
        item["exists"]
        and not item["has_external_resource_reference"]
        and not item["has_fetch_or_network_code"]
        and not item["mentions_qwen_or_openai_invocation"]
        for item in checks.values()
    )
    app_checks_pass = (
        checks["metatool"]["has_localstorage"]
        and checks["stabletoolbench"]["has_localstorage"]
        and checks["metatool"]["has_export_csv"]
        and checks["stabletoolbench"]["has_export_csv"]
        and checks["metatool"]["has_import_csv"]
        and checks["stabletoolbench"]["has_import_csv"]
        and checks["metatool"]["has_textcontent_rendering"]
        and checks["stabletoolbench"]["has_textcontent_rendering"]
    )
    can_start_manual = bool(self_check_pass and app_checks_pass)

    summary = {
        "generated_at": generated_at,
        "input_csv_files": {
            "metatool": str(metatool_csv),
            "stabletoolbench": str(stable_csv),
        },
        "generated_html_files": {
            "metatool": str(metatool_html),
            "stabletoolbench": str(stable_html),
            "index": str(index_html),
        },
        "metatool_rows_embedded": len(metatool_rows),
        "stabletoolbench_rows_embedded": len(stable_rows),
        "metatool_initial_qa_final_decision_distribution": csv_distribution(metatool_rows, "qa_final_decision"),
        "stabletoolbench_initial_qa_final_decision_distribution": csv_distribution(stable_rows, "qa_final_decision"),
        "self_check": checks,
        "self_check_pass": self_check_pass,
        "app_feature_check_pass": app_checks_pass,
        "metatool_html_review_app_generated": metatool_html.exists(),
        "stabletoolbench_html_review_app_generated": stable_html.exists(),
        "html_review_index_generated": index_html.exists(),
        "review_mode": "html_with_csv_export",
        "can_start_manual_html_review": can_start_manual,
        "can_merge_external_sources_now": False,
        "can_generate_full_six_task_benchmark_now": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "qwen_called": False,
        "external_api_called": False,
    }
    write_json(summary_dir / "html_review_app_build_summary.json", summary)

    instruction = f"""# External Policy v0.2 HTML Review Instruction

Generated at: {generated_at}

## Open The Review Apps

- MetaTool: `{metatool_html}`
- StableToolBench: `{stable_html}`
- Index: `{index_html}`

## How To Review

Use the HTML pages as a manual UI only. The page displays raw fields and policy fields as read-only data. You should fill only:

- `qa_final_decision`
- `qa_semantic_alignment_check`
- `qa_capability_coverage_check`
- `qa_candidate_validity_check`
- `qa_service_catalog_check`
- `qa_task_type_check`
- `qa_leakage_check`
- `qa_error_type`
- `qa_severity`
- `qa_notes`
- `reviewer_id`
- `reviewed_at`

Do not treat policy decisions as human final labels. The HTML does not auto-fill QA fields.

## Export

Use `Export reviewed CSV` after review. The page shows validation before export:

- total rows
- reviewed rows
- pending rows
- missing `qa_final_decision`
- uncertain/remove rows missing `qa_error_type`
- severity not none rows missing `qa_error_type`
- rows missing `reviewer_id`
- rows missing `reviewed_at`
- invalid allowed value count

If review is incomplete, the exported file name will use `_draft.csv`. When all rows are reviewed and validation has no issues, it exports the official reviewed CSV name.

## No-Go Boundary

This HTML stage does not authorize external source merge, final clean dataset generation, split, baseline, training, Qwen calls, or any external API calls.
"""
    (docs_dir / "external_policy_v0_2_html_review_instruction.md").write_text(instruction, encoding="utf-8")

    report = f"""# External Policy v0.2 HTML Review App Report

Generated at: {generated_at}

## Generated HTML Files

- MetaTool: `{metatool_html}`
- StableToolBench: `{stable_html}`
- Index: `{index_html}`

## Input CSV Files

- MetaTool: `{metatool_csv}`
- StableToolBench: `{stable_csv}`

## Row Counts

- MetaTool rows embedded: `{len(metatool_rows)}`
- StableToolBench rows embedded: `{len(stable_rows)}`

## Source-Specific Review Focus

MetaTool focus: leakage, rewrite-needed, catalog validity, missing context, and whether each row can support `single_service_discovery_external`.

StableToolBench focus: valid candidate space, leakage, candidate-space reconstruction, composable dependency validity, and demo/test/generic source blocking.

## How To Export Reviewed CSV

Open either HTML app, complete the editable QA panel, then click `Export reviewed CSV`. Incomplete review exports a `_draft.csv`; complete and valid review exports the official reviewed CSV file name.

## Self-Check

- self_check_pass: `{str(self_check_pass).lower()}`
- app_feature_check_pass: `{str(app_checks_pass).lower()}`
- localStorage supported in generated pages: `true`
- import CSV supported: `true`
- export CSV supported: `true`
- external resource references detected: `{str(any(v['has_external_resource_reference'] for v in checks.values())).lower()}`
- fetch/network code detected: `{str(any(v['has_fetch_or_network_code'] for v in checks.values())).lower()}`

## No-Go Boundary

- Qwen called: `false`
- external API called: `false`
- merge external sources now: `false`
- generate final dataset now: `false`
- split/baseline/training now: `false`
"""
    (docs_dir / "external_policy_v0_2_html_review_app_report.md").write_text(report, encoding="utf-8")

    go = {
        "generated_at": generated_at,
        "metatool_html_review_app_generated": metatool_html.exists(),
        "stabletoolbench_html_review_app_generated": stable_html.exists(),
        "html_review_index_generated": index_html.exists(),
        "review_mode": "html_with_csv_export",
        "can_start_manual_html_review": can_start_manual,
        "can_merge_external_sources_now": False,
        "can_generate_full_six_task_benchmark_now": False,
        "can_generate_final_clean_dataset_now": False,
        "can_create_split_now": False,
        "can_run_baseline_now": False,
        "can_train_model_now": False,
        "qwen_called": False,
        "external_api_called": False,
        "recommended_next_step": (
            "Manually review using HTML apps, export reviewed CSV, then run reviewed CSV validation / summarization."
            if can_start_manual
            else "Fix HTML review app generation issue before manual review."
        ),
    }
    write_json(summary_dir / "html_review_app_go_no_go_summary.json", go)
    go_md = f"""# External Policy v0.2 HTML Review Go / No-Go

Generated at: {generated_at}

- metatool_html_review_app_generated: `{str(go['metatool_html_review_app_generated']).lower()}`
- stabletoolbench_html_review_app_generated: `{str(go['stabletoolbench_html_review_app_generated']).lower()}`
- html_review_index_generated: `{str(go['html_review_index_generated']).lower()}`
- review_mode: `html_with_csv_export`
- can_start_manual_html_review: `{str(go['can_start_manual_html_review']).lower()}`
- can_merge_external_sources_now: `false`
- can_generate_full_six_task_benchmark_now: `false`
- can_generate_final_clean_dataset_now: `false`
- can_create_split_now: `false`
- can_run_baseline_now: `false`
- can_train_model_now: `false`
- qwen_called: `false`
- external_api_called: `false`

Recommended next step: {go['recommended_next_step']}
"""
    (docs_dir / "external_policy_v0_2_html_review_go_no_go.md").write_text(go_md, encoding="utf-8")

    generated_files = [
        metatool_html,
        stable_html,
        index_html,
        docs_dir / "external_policy_v0_2_html_review_instruction.md",
        docs_dir / "external_policy_v0_2_html_review_app_report.md",
        docs_dir / "external_policy_v0_2_html_review_go_no_go.md",
        summary_dir / "html_review_app_build_summary.json",
        summary_dir / "html_review_app_go_no_go_summary.json",
        project_root / "scripts/validation/build_external_policy_v0_2_html_review_apps.py",
    ]
    archive_outputs(project_root, generated_files)

    print("metatool_html_review_app_generated:", metatool_html.exists())
    print("stabletoolbench_html_review_app_generated:", stable_html.exists())
    print("html_review_index_generated:", index_html.exists())
    print("review_mode = html_with_csv_export")
    print("metatool_rows_embedded:", len(metatool_rows))
    print("stabletoolbench_rows_embedded:", len(stable_rows))
    print("can_start_manual_html_review:", can_start_manual)
    print("can_merge_external_sources_now = false")
    print("can_generate_final_clean_dataset_now = false")
    print("qwen_called = false")
    print("external_api_called = false")
    print("recommended_next_step:", go["recommended_next_step"])
    return 0 if can_start_manual else 1


if __name__ == "__main__":
    raise SystemExit(main())
