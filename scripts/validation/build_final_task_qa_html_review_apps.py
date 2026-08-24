#!/usr/bin/env python3
"""Build offline blind-human-QA apps using the frozen composable review UI pattern."""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QA_ROOT = ROOT / "ServiceDiscoveryBench-v0.1-candidate" / "qa"

TASKS = (
    "single_service_discovery",
    "single_api_recommendation",
    "multi_service_discovery",
    "multi_api_recommendation",
    "composable_service_discovery",
    "composable_api_recommendation",
)
TASK_LABELS = {
    "single_service_discovery": "单服务发现 / Single-service discovery",
    "single_api_recommendation": "单服务 API 推荐 / Single-service API recommendation",
    "multi_service_discovery": "多服务发现 / Multi-service discovery",
    "multi_api_recommendation": "多服务 API 推荐 / Multi-service API recommendation",
    "composable_service_discovery": "可组合服务发现 / Composable service discovery",
    "composable_api_recommendation": "可组合 API 推荐 / Composable API recommendation",
}
ROUND_LABELS = {
    "primary": "主审 / Primary review",
    "secondary": "补充审核（非门禁） / Supplemental review",
}
PACK_FIELDS = [
    "blind_item_id", "blind_pack_id", "benchmark_task_id", "review_round",
    "task_type", "prediction_target", "query_text", "user_visible_context_json",
    "candidate_display_json", "gold_display_json", "acceptable_gold_sets_display_json",
    "source_catalog_evidence_json", "dependency_graph_json", "dependency_evidence_json",
    "content_fingerprint",
]
REVIEW_FIELDS = [
    "review_id", "benchmark_task_id", "review_round", "reviewer_id", "blind_pack_id",
    "content_fingerprint", "semantic_alignment_check", "gold_validity_check",
    "candidate_validity_check", "service_catalog_check", "task_type_check",
    "leakage_check", "dependency_check", "final_decision", "error_type",
    "severity", "notes", "reviewed_at",
]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def parse_json(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def sanitize_dependency(value: Any) -> Any:
    """Retain observable trace evidence while excluding machine/policy/source identity fields."""
    blocked_exact = {
        "source_dataset", "source_file", "source_path", "source_record_path",
        "policy_decision", "expected_outcome", "model_label",
    }
    blocked_fragments = ("machine", "policy", "model", "assessment", "risk")
    if isinstance(value, list):
        return [sanitize_dependency(item) for item in value]
    if isinstance(value, dict):
        return {
            key: sanitize_dependency(item)
            for key, item in value.items()
            if key not in blocked_exact
            and not any(fragment in key.lower() for fragment in blocked_fragments)
        }
    return value


def safe_catalog_evidence(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    safe: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_path = str(row.get("source_path", ""))
        safe.append({
            "catalog_version": str(row.get("catalog_version", "")),
            "artifact_name": Path(source_path).name if source_path else "",
            "source_sha256": str(row.get("source_sha256", "")),
        })
    return safe


def public_pack_row(row: dict[str, str], translations: dict[str, str]) -> dict[str, Any]:
    task_id = row["benchmark_task_id"]
    translation = str(translations.get(task_id, "")).strip()
    if not translation:
        raise ValueError(f"Missing Chinese query translation: {task_id}")
    return {
        "blind_item_id": row["blind_item_id"],
        "blind_pack_id": row["blind_pack_id"],
        "benchmark_task_id": row["benchmark_task_id"],
        "review_round": row["review_round"],
        "task_type": row["task_type"],
        "prediction_target": row["prediction_target"],
        "query_text": row["query_text"],
        "query_translation_zh": translation,
        "user_visible_context": parse_json(row["user_visible_context_json"], {}),
        "candidate_display": parse_json(row["candidate_display_json"], []),
        "gold_display": parse_json(row["gold_display_json"], []),
        "acceptable_gold_sets_display": parse_json(row["acceptable_gold_sets_display_json"], []),
        "source_catalog_evidence": safe_catalog_evidence(parse_json(row["source_catalog_evidence_json"], [])),
        "dependency_graph": sanitize_dependency(parse_json(row["dependency_graph_json"], [])),
        "dependency_evidence": sanitize_dependency(parse_json(row["dependency_evidence_json"], [])),
        "content_fingerprint": row["content_fingerprint"],
    }


def app_html(task: str, review_round: str, rows: list[dict[str, Any]]) -> str:
    title = f"{TASK_LABELS[task]} · {ROUND_LABELS[review_round]}"
    data_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    config = {
        "task": task,
        "taskLabel": TASK_LABELS[task],
        "round": review_round,
        "roundLabel": ROUND_LABELS[review_round],
        "isComposable": task.startswith("composable_"),
        "rowCount": len(rows),
        "reviewFields": REVIEW_FIELDS,
        "storageKey": f"sdb_v0_1_human_qa::{task}::{review_round}::composable_ui_v2",
        "downloadName": f"{review_round}_reviews.csv",
    }
    config_json = json.dumps(config, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f4f7f9;--surface:#fff;--surface2:#f8fafb;--line:#d6dee4;--text:#1b2730;--muted:#65727c;--blue:#1769aa;--blue-soft:#e8f2fb;--green:#177245;--green-soft:#e9f7ef;--amber:#9a5d00;--amber-soft:#fff5df;--red:#ad2e2e;--red-soft:#fff0ef;--violet:#6654a3;--violet-soft:#f2effb;--shadow:0 1px 4px rgba(21,42,55,.08)}
*{box-sizing:border-box}html,body{height:100%;margin:0}body{font-family:"Microsoft YaHei UI","Segoe UI",Arial,sans-serif;color:var(--text);background:var(--bg);font-size:14px;overflow:hidden}button,input,textarea{font:inherit}button{border:1px solid var(--line);background:var(--surface);color:var(--text);min-height:36px;padding:7px 11px;border-radius:6px;cursor:pointer}button:hover{border-color:#8ca6b7;background:#f1f6f9}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid rgba(23,105,170,.2);outline-offset:1px}.primary{background:var(--blue);border-color:var(--blue);color:#fff}.primary:hover{background:#12598f}.danger{color:var(--red);border-color:#e1adad}.app{height:100%;display:grid;grid-template-rows:auto 1fr}.topbar{display:flex;align-items:center;gap:16px;padding:10px 16px;background:var(--surface);border-bottom:1px solid var(--line);box-shadow:var(--shadow);z-index:5}.title{min-width:0}.title h1{font-size:19px;margin:0 0 3px}.title p{margin:0;color:var(--muted);font-size:12px}.progress{margin-left:auto;display:flex;align-items:center;gap:10px;min-width:290px}.progress-track{height:8px;flex:1;background:#e7edf1;border-radius:4px;overflow:hidden}.progress-fill{height:100%;width:0;background:var(--green)}.progress-text{white-space:nowrap;font-weight:700}.workspace{min-height:0;display:grid;grid-template-columns:290px minmax(560px,1fr) 420px}.sidebar,.main,.review{min-width:0;min-height:0;background:var(--surface)}.sidebar{border-right:1px solid var(--line);display:flex;flex-direction:column}.main{overflow:auto;background:var(--bg);padding:14px 16px 60px}.review{border-left:1px solid var(--line);overflow:auto;padding:12px 14px 80px}.sidebar-head{padding:12px;border-bottom:1px solid var(--line)}.search{width:100%;height:38px;border:1px solid var(--line);border-radius:6px;padding:0 10px;background:#fff}.filter-block{margin-top:10px}.filter-title{font-size:12px;color:var(--muted);margin-bottom:5px}.chip-row{display:flex;flex-wrap:wrap;gap:5px}.filter-chip{min-height:28px;padding:4px 8px;font-size:12px}.filter-chip.active{background:var(--blue-soft);border-color:#79a9d0;color:#114e7d;font-weight:700}.list-summary{padding:8px 12px;color:var(--muted);border-bottom:1px solid var(--line);font-size:12px}.sample-list{overflow:auto;min-height:0}.sample-item{display:block;width:100%;border:0;border-bottom:1px solid #e8edf0;border-radius:0;text-align:left;padding:10px 12px;min-height:80px;background:#fff}.sample-item:hover{background:#f5f9fc}.sample-item.active{background:#eaf3fb;box-shadow:inset 4px 0 var(--blue)}.sample-line{display:flex;align-items:center;gap:7px;margin-bottom:5px}.dot{width:9px;height:9px;border-radius:50%;background:#b7c2ca;flex:none}.dot.done{background:var(--green)}.dot.partial{background:#d49a1f}.sample-id{font-weight:700;overflow-wrap:anywhere}.group-tag{margin-left:auto;color:var(--muted);font-size:11px;border:1px solid var(--line);padding:1px 5px;border-radius:4px}.sample-query{color:#4f5e68;font-size:12px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.section{background:var(--surface);border:1px solid var(--line);border-radius:7px;margin:0 0 12px;box-shadow:var(--shadow)}.section-head{display:flex;align-items:center;gap:10px;padding:10px 12px;border-bottom:1px solid var(--line)}.section-head h2{font-size:16px;margin:0}.section-head .sub{margin-left:auto;color:var(--muted);font-size:12px}.section-body{padding:12px}.identity{display:flex;flex-wrap:wrap;gap:7px;align-items:center}.badge{display:inline-flex;align-items:center;min-height:24px;padding:3px 7px;border-radius:4px;background:#eef2f4;color:#3f505b;font-size:12px}.badge.gold{background:var(--amber-soft);color:#754500;border:1px solid #efd39c;font-weight:700}.badge.good{background:var(--green-soft);color:var(--green)}.bilingual{display:grid;grid-template-columns:1fr 1fr;gap:10px}.language-pane{border:1px solid var(--line);border-radius:6px;padding:11px;min-width:0}.language-pane.zh{background:#eef9f3;border-color:#b9dfc9}.lang-label{font-size:12px;color:var(--muted);font-weight:700;margin-bottom:6px}.query-text{font-size:16px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere}.aid-text{line-height:1.7;color:#315844}.stat-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.stat{border-left:4px solid #9eb7c8;background:var(--surface2);padding:8px 10px;min-height:58px}.stat strong{display:block;font-size:19px}.stat span{color:var(--muted);font-size:12px}.audit-order{margin:0;padding-left:22px;line-height:1.9}.split-summary{display:grid;grid-template-columns:1fr 1fr;gap:10px}.level-box{border:1px solid var(--line);padding:10px;border-radius:6px}.level-box h3{font-size:14px;margin:0 0 7px}.level-box.service{border-left:4px solid var(--blue)}.level-box.api{border-left:4px solid var(--violet)}.hint-list{margin:0;padding-left:20px;line-height:1.7}.hint-list li{margin:4px 0;overflow-wrap:anywhere}.warning{background:var(--red-soft);border:1px solid #efc0bd;color:#8a2424;padding:8px;border-radius:5px;margin:7px 0}.notice{background:var(--blue-soft);border-left:4px solid var(--blue);padding:9px;line-height:1.55}.hierarchy-controls{margin-left:auto;display:flex;gap:5px}.hierarchy-controls button.active{background:var(--blue-soft);border-color:#79a9d0;color:#114e7d;font-weight:700}.service-block{border:1px solid var(--line);border-radius:6px;margin:8px 0;background:#fff}.service-block.gold-service{border-color:#deb760;box-shadow:inset 4px 0 #d49a1f}.service-block>summary{list-style:none;cursor:pointer;padding:10px 12px;display:flex;align-items:flex-start;gap:8px}.service-block>summary::-webkit-details-marker{display:none}.service-main{min-width:0;flex:1}.service-name{font-weight:800;font-size:14px;overflow-wrap:anywhere}.service-zh{color:#165d3b;margin-top:3px;overflow-wrap:anywhere}.service-content{border-top:1px solid var(--line);padding:10px 12px}.description-pair{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:9px}.description{padding:8px;border-radius:5px;background:var(--surface2);line-height:1.55;overflow-wrap:anywhere;white-space:pre-wrap}.description.zh{background:#eef9f3}.api-list{display:grid;grid-template-columns:1fr;gap:7px}.api-card{border:1px solid #e0e6ea;border-radius:5px;padding:8px 10px;background:#fbfcfd}.api-card.gold-api{border-color:#d8aa48;background:#fffbef}.api-title{display:flex;align-items:flex-start;gap:7px;font-weight:700}.api-title>span:first-child{overflow-wrap:anywhere}.api-meta{color:var(--muted);font-size:12px;overflow-wrap:anywhere}.dependency-step{border-left:3px solid #7b9fb8;padding:7px 10px;margin:8px 0;background:#f8fafb}.step-title{font-weight:800}.kv{display:grid;grid-template-columns:140px minmax(0,1fr);gap:5px;font-size:12px;margin-top:4px}.kv dt{color:var(--muted)}.kv dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.edge{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:8px;border:1px solid #d6e0e7;background:#f5f9fb;padding:7px 9px;border-radius:5px;margin:6px 0}.edge-arrow{color:var(--blue);font-weight:900}.raw-details{margin:8px 0}.raw-details summary{cursor:pointer;font-weight:700}.raw-pre{white-space:pre-wrap;overflow-wrap:anywhere;font-family:Consolas,monospace;font-size:11px;background:#f7f9fa;border:1px solid var(--line);padding:8px;max-height:340px;overflow:auto}.review h2{font-size:16px;margin:12px 0 8px}.review-note{background:var(--blue-soft);border-left:4px solid var(--blue);padding:9px;margin-bottom:10px;line-height:1.55}.guide summary{cursor:pointer;font-weight:800;padding:8px 0}.guide-body{font-size:13px;line-height:1.7;color:#3f4f59}.preset-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin:8px 0 14px}.preset{min-height:68px;text-align:left;padding:8px}.preset strong{display:block;font-size:13px;margin-bottom:4px}.preset span{display:block;color:var(--muted);font-size:11px;line-height:1.35}.preset.keep{border-color:#86c5a4;background:var(--green-soft)}.preset.hold{border-color:#e3c178;background:var(--amber-soft)}.preset.remove{border-color:#e3aaaa;background:var(--red-soft)}.preset.reclass{border-color:#aaa0d0;background:var(--violet-soft)}.field-group{border-top:1px solid var(--line);padding:11px 0}.field-label{font-weight:800;margin-bottom:3px}.field-help{color:var(--muted);font-size:12px;line-height:1.45;margin-bottom:7px}.option-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px}.option{min-height:36px;padding:6px 5px;font-size:12px;line-height:1.25;overflow-wrap:anywhere}.option.selected{background:var(--blue);color:#fff;border-color:var(--blue);font-weight:700}.text-field{width:100%;border:1px solid var(--line);border-radius:6px;padding:8px;background:#fff}.text-field.notes{min-height:90px;resize:vertical}.eligibility-check{padding:9px;border-radius:5px;margin:8px 0;background:var(--surface2);border:1px solid var(--line);font-size:12px;line-height:1.6}.eligibility-check.bad{background:var(--red-soft);border-color:#efc0bd}.attest-grid{display:grid;gap:6px}.check{display:flex;gap:7px;align-items:flex-start;font-size:12px;line-height:1.45}.check input{width:auto;margin-top:2px}.action-bar{position:sticky;bottom:-80px;background:rgba(255,255,255,.97);border-top:1px solid var(--line);margin:12px -14px -80px;padding:10px 14px 18px;box-shadow:0 -2px 8px rgba(21,42,55,.08)}.nav-row,.export-row{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:7px}.export-row{grid-template-columns:1fr 1fr 1fr}.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#1e2d36;color:#fff;padding:10px 16px;border-radius:6px;z-index:20;box-shadow:0 5px 18px rgba(0,0,0,.25);opacity:0;pointer-events:none;transition:opacity .18s}.toast.show{opacity:1}.empty{padding:24px;color:var(--muted);text-align:center}.hidden{display:none!important}
@media(max-width:1300px){.workspace{grid-template-columns:250px minmax(500px,1fr) 380px}.option-grid{grid-template-columns:1fr 1fr}.topbar{gap:10px}.progress{min-width:240px}}
@media(max-width:980px){body{overflow:auto}.app{height:auto;min-height:100%;width:100%;max-width:100vw}.workspace{display:block;width:100%;max-width:100vw;min-width:0}.sidebar,.main,.review{height:auto;overflow:visible;border:0;width:100%;max-width:100vw}.sidebar{max-height:430px}.sample-list{max-height:260px}.topbar{position:sticky;top:0;flex-wrap:wrap}.topbar .title{flex:1 1 100%}.bilingual,.split-summary,.description-pair{grid-template-columns:minmax(0,1fr)}.review{border-top:1px solid var(--line)}.action-bar{bottom:0;margin-bottom:0}.progress{min-width:0;flex:1;margin-left:0}.title p{display:none}}
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <div class="title"><h1>__TITLE__</h1><p>ServiceDiscoveryBench v0.1 · Blind human QA · Single authoritative primary · 离线单文件</p></div>
    <button id="helpTop" type="button">审核说明</button>
    <div class="progress"><div class="progress-track"><div id="progressFill" class="progress-fill"></div></div><div id="progressText" class="progress-text">0 / 0</div></div>
  </header>
  <div class="workspace">
    <aside class="sidebar">
      <div class="sidebar-head">
        <input id="search" class="search" type="search" placeholder="搜索 ID、query、service、API">
        <div class="filter-block"><div class="filter-title">审核状态</div><div id="reviewFilters" class="chip-row"></div></div>
        <div class="filter-block"><div class="filter-title">来源组</div><div id="groupFilters" class="chip-row"></div></div>
        <div class="filter-block"><div class="filter-title">快速定位</div><div id="quickFilters" class="chip-row"></div></div>
      </div>
      <div id="listSummary" class="list-summary"></div>
      <div id="sampleList" class="sample-list"></div>
    </aside>
    <main id="main" class="main"></main>
    <aside id="review" class="review"></aside>
  </div>
</div>
<div id="toast" class="toast"></div>
<input id="importer" class="hidden" type="file" accept=".csv,text/csv">
<script>
"use strict";
const DATA=__DATA__;
const CFG=__CONFIG__;
const CHECKS=["semantic_alignment_check","gold_validity_check","candidate_validity_check","service_catalog_check","task_type_check","leakage_check","dependency_check"];
const FIELD_DEFS=[
 {group:"核心有效性",key:"semantic_alignment_check",label:"语义对齐 / Semantic alignment",help:"Query 与 Gold 是否指向并完成同一真实目标？",options:[["aligned","对齐"],["misaligned","不对齐"],["uncertain","不确定"]]},
 {group:"核心有效性",key:"gold_validity_check",label:"Gold 有效性 / Gold validity",help:"Gold 是否正确、完整、且位于候选空间中？",options:[["true","有效"],["false","无效"],["uncertain","不确定"]]},
 {group:"核心有效性",key:"candidate_validity_check",label:"候选空间 / Candidate validity",help:"候选是否包含 Gold、真实负例与合理选择空间？",options:[["true","有效"],["false","无效"],["uncertain","不确定"]]},
 {group:"目录与分类",key:"service_catalog_check",label:"目录一致性 / Catalog identity",help:"ID、名称、父服务、说明与冻结目录是否一致？RapidAPI host 仅作为本地目录元数据。",options:[["pass","通过"],["fail","失败"],["uncertain","不确定"]]},
 {group:"目录与分类",key:"task_type_check",label:"任务类型 / Task type",help:"single / multi / composable 与 service / API 层级是否正确？",options:[["pass","通过"],["fail","失败"],["uncertain","不确定"]]},
 {group:"安全与依赖",key:"leakage_check",label:"泄露检查 / Leakage",help:"Query 是否直接或近乎直接泄露 Gold 名称、endpoint 或函数？",options:[["no_blocking_leak","无阻断泄露"],["blocking_leak","阻断泄露"],["uncertain","不确定"]]},
 {group:"安全与依赖",key:"dependency_check",label:"依赖检查 / Dependency",help:"Composable 必须有真实跨步骤依赖；普通 single/multi 选“不适用”。共享输入不等于依赖。",options:[["true","真实依赖"],["false","无真实依赖"],["uncertain","不确定"],["not_applicable_parallel_multi","不适用"]]},
 {group:"最终决定",key:"final_decision",label:"最终决定 / Final decision",help:"保留、移除或不确定。",options:[["keep","保留"],["remove","移除"],["uncertain","不确定"]]},
 {group:"最终决定",key:"severity",label:"严重度 / Severity",help:"保留通常为 none；会破坏任务真实性或层级的错误通常为 major/critical。",options:[["none","无"],["minor","轻微"],["major","重大"],["critical","致命"]]}
];
const PRESETS=[
 {cls:"keep",title:"全部符合：保留",desc:"全部检查通过；填写并下一条",note:"人工核对后确认全部通过",fields:{semantic_alignment_check:"aligned",gold_validity_check:"true",candidate_validity_check:"true",service_catalog_check:"pass",task_type_check:"pass",leakage_check:"no_blocking_leak",final_decision:"keep",severity:"none",error_type:""}},
 {cls:"remove",title:"语义 / Gold 问题：移除",desc:"目标错位或 Gold 无效",note:"语义或 Gold 存在重大问题",fields:{semantic_alignment_check:"misaligned",gold_validity_check:"false",candidate_validity_check:"uncertain",service_catalog_check:"uncertain",task_type_check:"uncertain",leakage_check:"uncertain",final_decision:"remove",severity:"major",error_type:"semantic_or_gold"}},
 {cls:"remove",title:"候选 / 目录问题：移除",desc:"候选空间或目录映射无效",note:"候选空间或目录身份存在重大问题",fields:{semantic_alignment_check:"aligned",gold_validity_check:"uncertain",candidate_validity_check:"false",service_catalog_check:"fail",task_type_check:"uncertain",leakage_check:"uncertain",final_decision:"remove",severity:"major",error_type:"candidate_or_catalog"}},
 {cls:"remove",title:"阻断泄露：移除",desc:"Query 直接暴露 Gold",note:"检测到阻断级 Gold 泄露",fields:{semantic_alignment_check:"aligned",gold_validity_check:"true",candidate_validity_check:"true",service_catalog_check:"pass",task_type_check:"pass",leakage_check:"blocking_leak",final_decision:"remove",severity:"critical",error_type:"blocking_leak"}},
 {cls:"hold",title:"证据不足：不确定",desc:"关键事实无法可靠确认",note:"证据不足，需进一步人工核查",fields:{semantic_alignment_check:"uncertain",gold_validity_check:"uncertain",candidate_validity_check:"uncertain",service_catalog_check:"uncertain",task_type_check:"uncertain",leakage_check:"uncertain",dependency_check:"uncertain",final_decision:"uncertain",severity:"major",error_type:"insufficient_evidence"}}
];
const $=id=>document.getElementById(id);
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
function nonempty(v){return String(v??"").trim()!==""}
function entityId(x){return String(x.api_id||x.service_id||x.id||x.api_name||x.service_name||"")}
function pretty(v){return JSON.stringify(v,null,2)}
let state={decisions:{},reviewerId:"",attestation:{}};try{state=Object.assign(state,JSON.parse(localStorage.getItem(CFG.storageKey)||"{}")||{})}catch{}state.decisions=state.decisions||{};state.attestation=state.attestation||{};
let currentId=DATA[0]?.benchmark_task_id||"";
let filtered=[];
let filters={review:"all",group:"all",quick:"all",query:""};
let hierarchyMode="all";
const searchCache=new Map();
function decision(row){return state.decisions[row.benchmark_task_id]||{}}
function ensure(row){state.decisions[row.benchmark_task_id]=state.decisions[row.benchmark_task_id]||{};return state.decisions[row.benchmark_task_id]}
function save(){localStorage.setItem(CFG.storageKey,JSON.stringify(state));updateProgress();renderList()}
function toast(message){const t=$("toast");t.textContent=message;t.classList.add("show");clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.classList.remove("show"),1800)}
function filledCount(row){const d=decision(row);return [...CHECKS,"final_decision","severity"].filter(k=>nonempty(d[k])).length}
function status(row){if(isComplete(row))return"done";return filledCount(row)?"partial":"blank"}
function isReviewed(row){return nonempty(decision(row).final_decision)}
function hardConsistencyIssues(row){const d=decision(row),issues=[];const allowed={semantic_alignment_check:["aligned","misaligned","uncertain"],gold_validity_check:["true","false","uncertain"],candidate_validity_check:["true","false","uncertain"],service_catalog_check:["pass","fail","uncertain"],task_type_check:["pass","fail","uncertain"],leakage_check:["no_blocking_leak","blocking_leak","uncertain"],dependency_check:["true","false","uncertain","not_applicable_parallel_multi"],final_decision:["keep","remove","uncertain"],severity:["none","minor","major","critical"]};Object.entries(allowed).forEach(([k,values])=>{if(!values.includes(String(d[k]||"")))issues.push(k+" 值无效或缺失")});if(d.final_decision==="remove"&&!nonempty(d.error_type))issues.push("remove 必须填写 error_type");if(d.final_decision==="uncertain"&&!nonempty(d.notes))issues.push("uncertain 必须在 notes 写明原因");if(d.final_decision==="keep"&&["major","critical"].includes(d.severity))issues.push("keep 不能同时声明 major/critical 问题");if(!CFG.isComposable&&d.dependency_check!=="not_applicable_parallel_multi")issues.push("普通任务 dependency_check 必须为不适用");if(CFG.isComposable&&d.dependency_check==="not_applicable_parallel_multi")issues.push("composable 不能将 dependency 标为普通任务不适用");return issues}
function isComplete(row){return !!state.reviewerId.trim()&&hardConsistencyIssues(row).length===0}
function reviewedCount(){return DATA.filter(isReviewed).length}
function completeCount(){return DATA.filter(isComplete).length}
function updateProgress(){const n=reviewedCount();$("progressText").textContent=n+" / "+DATA.length;$("progressFill").style.width=(DATA.length?100*n/DATA.length:0)+"%"}
function sourceGroups(row){return [...new Set((row.source_catalog_evidence||[]).map(x=>x.catalog_version).filter(Boolean))]}
function searchBlob(row){if(searchCache.has(row.benchmark_task_id))return searchCache.get(row.benchmark_task_id);const blob=[row.blind_item_id,row.benchmark_task_id,row.query_text,pretty(row.candidate_display),pretty(row.gold_display)].join(" ").toLowerCase();searchCache.set(row.benchmark_task_id,blob);return blob}
function applyFilters(){const q=filters.query.trim().toLowerCase();filtered=DATA.filter(row=>{const st=status(row);if(filters.review!=="all"&&st!==filters.review)return false;if(filters.group!=="all"&&!sourceGroups(row).includes(filters.group))return false;if(filters.quick==="dependency"&&!(row.dependency_graph||[]).length&&!Object.keys(row.dependency_evidence||{}).length)return false;if(filters.quick==="goldmulti"&&(row.gold_display||[]).length<2)return false;if(filters.quick==="large"&&(row.candidate_display||[]).length<20)return false;if(q&&!searchBlob(row).includes(q))return false;return true});if(!filtered.some(x=>x.benchmark_task_id===currentId)&&filtered.length)currentId=filtered[0].benchmark_task_id;renderList();renderCurrent()}
function filterButtons(target,items,key){const box=$(target);box.replaceChildren();items.forEach(([v,label])=>{const b=el("button","filter-chip"+(filters[key]===v?" active":""),label);b.type="button";b.onclick=()=>{filters[key]=v;renderFilters();applyFilters()};box.appendChild(b)})}
function renderFilters(){const groups=[...new Set(DATA.flatMap(sourceGroups))].sort();filterButtons("reviewFilters",[["all","全部"],["blank","未填写"],["partial","部分填写"],["done","已完成"]],"review");filterButtons("groupFilters",[["all","全部"],...groups.map(x=>[x,x])],"group");filterButtons("quickFilters",[["all","不限"],["dependency","有依赖证据"],["goldmulti","多 Gold"],["large","大候选集"]],"quick")}
function renderList(){const list=$("sampleList");list.replaceChildren();$("listSummary").textContent="显示 "+filtered.length+" 条 · 已决定 "+reviewedCount()+" 条 · 正式完整 "+completeCount()+" 条";if(!filtered.length){list.appendChild(el("div","empty","没有符合条件的样本"));return}filtered.forEach((row,i)=>{const st=status(row),b=el("button","sample-item"+(row.benchmark_task_id===currentId?" active":""));b.type="button";const line=el("div","sample-line");line.appendChild(el("span","dot "+st));line.appendChild(el("span","sample-id","#"+String(DATA.indexOf(row)+1).padStart(3,"0")));line.appendChild(el("span","group-tag",row.prediction_target+" · "+row.review_round));b.append(line,el("div","sample-query",row.query_text));b.onclick=()=>{currentId=row.benchmark_task_id;hierarchyMode="all";renderList();renderCurrent();$("main").scrollTop=0;$("review").scrollTop=0};list.appendChild(b)})}
function section(title,sub){const s=el("section","section"),h=el("div","section-head");h.appendChild(el("h2","",title));if(sub)h.appendChild(el("span","sub",sub));const b=el("div","section-body");s.append(h,b);return{s,b,h}}
function currentRow(){return DATA.find(x=>x.benchmark_task_id===currentId)}
function renderIdentity(row){const x=section("1. 样本身份 / Identity","blind snapshot");const box=el("div","identity");[[CFG.taskLabel,""],[CFG.roundLabel,"good"],[row.prediction_target.toUpperCase(),""],[row.blind_pack_id,""],["LOCAL · NO NETWORK","good"]].forEach(([v,c])=>box.appendChild(el("span","badge "+c,v)));x.b.appendChild(box);const n=el("div","notice","本页所有 Query、Gold、候选、依赖证据和 RapidAPI host 均来自本地冻结审核包；host 只是目录身份字段，页面不会访问 RapidAPI 或任何外网。");n.style.marginTop="10px";x.b.appendChild(n);return x.s}
function renderQuery(row){const x=section("2. 双栏 Query / Query","原文与中文辅助译文逐条对应"),g=el("div","bilingual"),a=el("div","language-pane"),z=el("div","language-pane zh");a.append(el("div","lang-label","原文 / Original"),el("div","query-text",row.query_text));z.append(el("div","lang-label","中文辅助译文 / Chinese translation aid"),el("div","query-text aid-text",row.query_translation_zh),el("div","api-meta","专有名词、ID、数字与 URL 请同时核对左侧原文。"));g.append(a,z);x.b.appendChild(g);return x.s}
function depCount(row){return Array.isArray(row.dependency_graph)?row.dependency_graph.length:(row.dependency_graph?Object.keys(row.dependency_graph).length:0)}
function renderStats(row){const x=section("3. 审核统计 / Snapshot statistics"),g=el("div","stat-grid");[[row.candidate_display.length,"候选项"],[row.gold_display.length,"Gold 项"],[row.acceptable_gold_sets_display.length,"可接受 Gold 集"],[depCount(row),"依赖边/项"]].forEach(([n,l])=>{const d=el("div","stat");d.append(el("strong","",String(n)),el("span","",l));g.appendChild(d)});x.b.appendChild(g);return x.s}
function renderAuditOrder(){const x=section("4. 审核顺序 / Audit order"),ol=el("ol","audit-order");["Query 目标与 Gold 语义对齐","Gold 正确性、完整性与可接受集合","候选空间：Gold 是否在内、负例是否真实","Service/API 目录身份和父子映射","任务类型：single / multi / composable 与层级","Query 是否阻断泄露 Gold","Composable 跨步骤依赖（普通任务为不适用）","最终决定、错误类型、严重度与备注"].forEach(t=>ol.appendChild(el("li","",t)));x.b.appendChild(ol);return x.s}
function renderSummary(row){const x=section("5. Service/API 分层摘要","只显示客观结构，不给出机器结论"),g=el("div","split-summary"),s=el("div","level-box service"),a=el("div","level-box api");const svcCount=row.prediction_target==="service"?row.candidate_display.length:new Set(row.candidate_display.map(v=>v.parent_service_id||v.parent_service_name)).size;s.append(el("h3","","Service-level"),el("div","","候选父服务 "+svcCount+" 个；Gold 所属服务 "+new Set(row.gold_display.map(v=>v.service_id||v.parent_service_id||v.parent_service_name)).size+" 个。"));a.append(el("h3","","API-level"),el("div","",row.prediction_target==="api"?"当前行直接审核 API；必须同时核对父服务映射。":"当前行直接审核 Service；API 细粒度结论不从本页推断。"));g.append(s,a);x.b.appendChild(g);const ul=el("ul","hint-list");ul.append(el("li","","候选与 Gold 数量仅用于定位，不能自动决定 keep/remove。"),el("li","",CFG.isComposable?"当前任务需要人工核对跨步骤新输出是否真实进入下游。":"当前任务不是 composable，dependency_check 应选“不适用”。"),el("li","","名称相似、共享输入、同一 host 或同一父服务都不能单独证明能力匹配或依赖。"));x.b.appendChild(ul);return x.s}
function edgeText(e){const from=e.from_step??e.source_step??"?",to=e.to_step??e.target_step??"?";return{from:"Step "+from,to:"Step "+to,mid:[e.upstream_source_path,e.downstream_source_path,e.evidence_value||e.downstream_value].filter(nonempty).join(" → ")||"查看字段详情"}}
function renderDependency(row){const x=section("6. 依赖链证据 / Dependency evidence",CFG.isComposable?"共享输入 ≠ 依赖":"ordinary task: not applicable");if(!CFG.isComposable){x.b.appendChild(el("div","notice","该任务属于普通 single/multi，不要求跨步骤组合依赖；人工字段请选择 not_applicable_parallel_multi。"));return x.s}const edges=Array.isArray(row.dependency_graph)?row.dependency_graph:[];if(!edges.length)x.b.appendChild(el("div","warning","冻结包没有结构化依赖边。请人工检查 dependency evidence；无法确认时选择 uncertain，不能因为存在多个工具就判为组合。"));edges.forEach((e,i)=>{const t=edgeText(e),d=el("div","dependency-step");d.appendChild(el("div","step-title","依赖边 "+(i+1)));const edge=el("div","edge");edge.append(el("span","",t.from),el("span","edge-arrow",t.mid),el("span","",t.to));d.appendChild(edge);const dl=el("dl","kv");Object.entries(e).slice(0,18).forEach(([k,v])=>{dl.append(el("dt","",k),el("dd","",typeof v==="object"?pretty(v):String(v)))});d.appendChild(dl);x.b.appendChild(d)});const rd=el("details","raw-details");rd.append(el("summary","","展开 dependency_evidence 原始结构"),el("pre","raw-pre",pretty(row.dependency_evidence)));x.b.appendChild(rd);return x.s}
function goldSet(row){return new Set(row.gold_display.map(entityId))}
function serviceGroups(row){const gold=goldSet(row);if(row.prediction_target==="service")return row.candidate_display.map(s=>({id:s.service_id||s.service_name,name:s.service_name||s.service_id,description:s.service_description||"",meta:[s.provider,s.host_or_base_url].filter(nonempty).join(" · "),gold:gold.has(entityId(s)),apis:[]}));const map=new Map();row.candidate_display.forEach(api=>{const id=api.parent_service_id||api.parent_service_name||"unknown";if(!map.has(id))map.set(id,{id,name:api.parent_service_name||id,description:"",meta:id,gold:false,apis:[]});const g=map.get(id);g.apis.push({...api,gold:gold.has(entityId(api))});if(g.apis.some(a=>a.gold))g.gold=true});return [...map.values()]}
function renderHierarchy(row){const x=section("7. Service/API 分层视图","Gold 以琥珀色标记"),controls=el("div","hierarchy-controls");[["all","全部候选"],["gold","仅 Gold"]].forEach(([v,l])=>{const b=el("button",hierarchyMode===v?"active":"",l);b.type="button";b.onclick=()=>{hierarchyMode=v;renderCurrent()};controls.appendChild(b)});x.h.appendChild(controls);let groups=serviceGroups(row);if(hierarchyMode==="gold")groups=groups.filter(g=>g.gold).map(g=>({...g,apis:g.apis.filter(a=>a.gold)}));groups.forEach((g,i)=>{const d=el("details","service-block"+(g.gold?" gold-service":""));if(groups.length<12||g.gold)d.open=true;const sm=el("summary",""),main=el("div","service-main");main.append(el("div","service-name",g.name||"未命名服务"),el("div","service-zh","Service ID: "+g.id));sm.append(main,el("span","badge "+(g.gold?"gold":""),g.gold?"GOLD SERVICE":"CANDIDATE"));d.appendChild(sm);const body=el("div","service-content"),pair=el("div","description-pair");pair.append(el("div","description",g.description||"冻结包未提供服务级说明。"),el("div","description zh",g.meta?"目录元数据："+g.meta:"中文辅助：未提供人工校验译文，请依据原文与 ID。"));body.appendChild(pair);if(g.apis.length){const list=el("div","api-list");g.apis.forEach(api=>{const c=el("div","api-card"+(api.gold?" gold-api":"")),t=el("div","api-title");t.append(el("span","",api.api_name||api.api_id),el("span","badge "+(api.gold?"gold":""),api.gold?"GOLD API":"API"));c.append(t,el("div","api-meta",[api.http_method,api.endpoint,api.api_id].filter(nonempty).join(" · ")),el("div","description",api.api_description||"冻结包未提供 API 说明。"));list.appendChild(c)});body.appendChild(list)}d.appendChild(body);x.b.appendChild(d)});if(!groups.length)x.b.appendChild(el("div","empty","当前模式下没有可显示项"));return x.s}
function renderRaw(row){const x=section("8. 原始证据与溯源 / Raw evidence","本地冻结快照");[["可接受 Gold 集",row.acceptable_gold_sets_display],["用户可见上下文",row.user_visible_context],["目录证据（文件名 + SHA-256）",row.source_catalog_evidence],["依赖图",row.dependency_graph]].forEach(([name,v])=>{const d=el("details","raw-details");d.append(el("summary","",name),el("pre","raw-pre",pretty(v)));x.b.appendChild(d)});const dl=el("dl","kv");dl.append(el("dt","","blind_item_id"),el("dd","",row.blind_item_id),el("dt","","benchmark_task_id"),el("dd","",row.benchmark_task_id),el("dt","","content_fingerprint"),el("dd","",row.content_fingerprint));x.b.appendChild(dl);return x.s}
function setField(row,key,val){const d=ensure(row);d[key]=val;d.reviewed_at=new Date().toISOString();save()}
function makeField(row,def){const box=el("div","field-group");box.append(el("div","field-label",def.label),el("div","field-help",def.help));const opts=el("div","option-grid");def.options.forEach(([v,l])=>{const b=el("button","option"+(decision(row)[def.key]===v?" selected":""),l);b.type="button";b.title=v;b.dataset.field=def.key;b.dataset.value=v;b.onclick=()=>{setField(row,def.key,v);renderReview(row)};opts.appendChild(b)});box.appendChild(opts);return box}
function textField(row,key,label,notes=false,placeholder=""){const box=el("div","field-group");box.appendChild(el("div","field-label",label));const input=notes?document.createElement("textarea"):document.createElement("input");input.className="text-field"+(notes?" notes":"");input.value=decision(row)[key]||"";input.placeholder=placeholder;input.dataset.field=key;input.oninput=()=>{ensure(row)[key]=input.value;ensure(row).reviewed_at=new Date().toISOString();localStorage.setItem(CFG.storageKey,JSON.stringify(state))};input.onchange=save;box.appendChild(input);return box}
function reviewerField(){const box=el("div","field-group");box.append(el("div","field-label","审核人 ID / Reviewer ID"),el("div","field-help","ID 可自定；primary 是本次 G4 的唯一权威人工结论。secondary 仅作补充记录，不要求另一位审核者。"));const input=el("input","text-field");input.id="reviewerId";input.placeholder="例如：human_reviewer_01";input.value=state.reviewerId||"";input.oninput=()=>{state.reviewerId=input.value;localStorage.setItem(CFG.storageKey,JSON.stringify(state));updateProgress()};input.onchange=()=>{save();renderList()};box.appendChild(input);return box}
function applyPreset(row,p){const d=ensure(row);Object.assign(d,p.fields);d.dependency_check=p.fields.dependency_check||(CFG.isComposable?"true":"not_applicable_parallel_multi");d.reviewed_at=new Date().toISOString();const old=String(d.notes||"").trim();d.notes=(old?old+"\n":"")+"快捷预设："+p.note;save();toast("已填写："+p.title);go(1,true)}
function presetPanel(row){const wrap=el("div","");wrap.append(el("h2","","快捷审核方案（点击后自动下一条）"),el("div","field-help","预设只是批量填写工具。点击前仍须看 query、Gold、候选和依赖；页面不会根据提示自动替你决定。"));const grid=el("div","preset-grid");PRESETS.forEach(p=>{const b=el("button","preset "+p.cls);b.type="button";b.append(el("strong","",p.title),el("span","",p.desc));b.onclick=()=>applyPreset(row,p);grid.appendChild(b)});wrap.appendChild(grid);return wrap}
function consistencyBox(row){const d=decision(row),issues=hardConsistencyIssues(row);if(d.final_decision==="keep"&&d.severity==="minor")issues.push("提示：keep + minor 请在 notes 说明不影响任务有效性的原因");const box=el("div","eligibility-check"+(issues.length?" bad":""));box.append(el("strong","",issues.length?"字段存在一致性提醒":"字段一致性即时核对"),el("div","",issues.length?issues.join("；"):"尚未发现明显字段冲突。"),el("div","api-meta","硬冲突会阻止正式文件命名，但不会自动修改人工字段。"));return box}
function guide(){const d=el("details","guide");d.id="guide";d.appendChild(el("summary","","审核说明与判定边界"));const b=el("div","guide-body");b.innerHTML="<b>单审规则：</b>primary 是本次 G4 的唯一权威人工结论；secondary 仅作补充审计，不影响门禁，也不触发分歧裁决。<br><b>Service-level：</b>判断需要哪些服务；<b>API-level：</b>判断父服务下需要哪些具体接口。<br><b>Composable：</b>前一步的新输出必须影响后一步的输入、选择或判断；共享输入、多个并列需求、同服务多 API、重试与冗余重算都不能自动视为真实组合。<br><b>本地性：</b>RapidAPI host 仅用于 frozen catalog identity，本页面没有网络请求。边界不清时选 uncertain，并在 notes 中写明。";d.appendChild(b);return d}
function attestationPanel(){const d=el("details","guide");d.id="attestationPanel";d.appendChild(el("summary","","Reviewer attestation / 审核者声明"));const b=el("div","guide-body"),grid=el("div","attest-grid");[["human_reviewer_confirmed","本人是完成人工判断的审核者"],["did_not_use_ai_as_final_judge","本人未使用 AI 作为最终裁判"]].forEach(([k,l])=>{const lab=el("label","check"),input=document.createElement("input");input.type="checkbox";input.checked=!!state.attestation[k];input.dataset.attest=k;input.onchange=()=>{state.attestation[k]=input.checked;save()};lab.append(input,document.createTextNode(l));grid.appendChild(lab)});b.appendChild(grid);const notes=el("textarea","text-field notes");notes.id="attestationNotes";notes.placeholder="声明备注（可选）";notes.value=state.attestation.notes||"";notes.oninput=()=>{state.attestation.notes=notes.value;localStorage.setItem(CFG.storageKey,JSON.stringify(state))};b.appendChild(notes);const ex=el("button","","导出 reviewer_attestation CSV");ex.id="attExportBtn";ex.onclick=exportAttestation;b.appendChild(ex);d.appendChild(b);return d}
function renderReview(row){const p=$("review");p.replaceChildren();p.append(el("div","review-note","这里只填写人工字段。Rule-based hints、旧 detector 和 provisional gold 都不能自动成为 human final。"),guide(),presetPanel(row));let last="";FIELD_DEFS.forEach(def=>{if(def.group!==last){p.appendChild(el("h2","",def.group));last=def.group}p.appendChild(makeField(row,def))});p.append(consistencyBox(row),reviewerField(),textField(row,"error_type","错误类型 / Error type",false,"remove 时必填，例如 semantic_or_gold"),textField(row,"notes","人工备注 / Notes",true,"uncertain 时必填；记录关键证据与边界理由"),attestationPanel());const bar=el("div","action-bar"),nav=el("div","nav-row"),prev=el("button","","← 上一条"),next=el("button","primary","下一条 →");prev.id="prevBtn";next.id="nextBtn";prev.onclick=()=>go(-1);next.onclick=()=>go(1);nav.append(prev,next);bar.appendChild(nav);const exp=el("div","export-row"),ex=el("button","primary","导出完整 CSV"),exf=el("button","","导出当前筛选"),im=el("button","","导入 CSV");ex.id="officialExportBtn";exf.id="filteredExportBtn";im.id="importBtn";ex.onclick=()=>exportCsv(false);exf.onclick=()=>exportCsv(true);im.onclick=()=>$("importer").click();exp.append(ex,exf,im);bar.appendChild(exp);const clr=el("div","nav-row"),cc=el("button","danger","清空当前"),ca=el("button","danger","清空全部");cc.id="clearCurrentBtn";ca.id="clearAllBtn";cc.onclick=()=>clearCurrent(row);ca.onclick=clearAll;clr.append(cc,ca);bar.appendChild(clr);p.appendChild(bar)}
function renderCurrent(){const row=currentRow(),main=$("main");main.replaceChildren();if(!row){main.appendChild(el("div","empty","没有样本"));$("review").replaceChildren();return}main.append(renderIdentity(row),renderQuery(row),renderStats(row),renderAuditOrder(),renderSummary(row),renderDependency(row),renderHierarchy(row),renderRaw(row));renderReview(row)}
function go(delta,fromPreset=false){const pool=filtered.length?filtered:DATA;let i=pool.findIndex(x=>x.benchmark_task_id===currentId);if(i<0)i=0;const ni=Math.max(0,Math.min(pool.length-1,i+delta));if(ni===i&&fromPreset){toast("已经是当前筛选的最后一条");renderCurrent();return}currentId=pool[ni].benchmark_task_id;hierarchyMode="all";renderList();renderCurrent();$("main").scrollTop=0;$("review").scrollTop=0}
function clearCurrent(row){if(!confirm("确认清空当前样本的全部人工字段？原始证据不会改变。"))return;delete state.decisions[row.benchmark_task_id];save();renderCurrent();toast("已清空当前样本")}
function clearAll(){if(!confirm("确认清空本页全部本地人工判断？此操作不会修改源 CSV，但无法撤销。"))return;state.decisions={};localStorage.setItem(CFG.storageKey,JSON.stringify(state));applyFilters();updateProgress();toast("已清空全部人工判断")}
function csvCell(v){const s=String(v??"");return /[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s}
function outputRow(row){const d=decision(row),reviewer=state.reviewerId.trim();return{review_id:`human::${reviewer}::${CFG.round}::${row.benchmark_task_id}`,benchmark_task_id:row.benchmark_task_id,review_round:CFG.round,reviewer_id:reviewer,blind_pack_id:row.blind_pack_id,content_fingerprint:row.content_fingerprint,semantic_alignment_check:d.semantic_alignment_check||"",gold_validity_check:d.gold_validity_check||"",candidate_validity_check:d.candidate_validity_check||"",service_catalog_check:d.service_catalog_check||"",task_type_check:d.task_type_check||"",leakage_check:d.leakage_check||"",dependency_check:d.dependency_check||"",final_decision:d.final_decision||"",error_type:d.error_type||"",severity:d.severity||"",notes:d.notes||"",reviewed_at:d.reviewed_at||""}}
function buildCsv(onlyFiltered=false){const rows=(onlyFiltered?filtered:DATA).map(outputRow);return [CFG.reviewFields.map(csvCell).join(","),...rows.map(r=>CFG.reviewFields.map(c=>csvCell(r[c])).join(","))].join("\r\n")}
function download(text,name){const url=URL.createObjectURL(new Blob([text],{type:"text/csv;charset=utf-8"})),a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function exportCsv(onlyFiltered){const pending=DATA.length-completeCount(),name=onlyFiltered?`${CFG.task}_${CFG.round}_filtered_reviews.csv`:(pending?`${CFG.task}_${CFG.round}_reviews_draft.csv`:CFG.downloadName);download("\ufeff"+buildCsv(onlyFiltered),name);toast("已导出 "+(onlyFiltered?filtered.length:DATA.length)+" 条 CSV"+(pending&&!onlyFiltered?"（草稿）":""))}
function parseCsv(text){const rows=[];let row=[],cell="",quoted=false;for(let i=0;i<text.length;i++){const c=text[i],n=text[i+1];if(quoted){if(c==='"'&&n==='"'){cell+='"';i++}else if(c==='"')quoted=false;else cell+=c}else if(c==='"')quoted=true;else if(c===','){row.push(cell);cell=""}else if(c==='\n'){row.push(cell.replace(/\r$/,"") );rows.push(row);row=[];cell=""}else cell+=c}if(cell.length||row.length){row.push(cell);rows.push(row)}return rows}
function importCsv(file){const reader=new FileReader();reader.onload=()=>{try{const rows=parseCsv(String(reader.result).replace(/^\ufeff/,"")),header=rows.shift()||[],idx=Object.fromEntries(header.map((h,i)=>[h,i])),idIndex=idx.benchmark_task_id;if(idIndex===undefined)throw new Error("缺少 benchmark_task_id 列");const byId=new Map(DATA.map(r=>[r.benchmark_task_id,r]));let matched=0;rows.forEach(cells=>{const id=cells[idIndex],base=byId.get(id);if(!base)return;const get=k=>idx[k]===undefined?"":cells[idx[k]]||"";if(get("review_round")&&get("review_round")!==CFG.round)throw new Error(id+" review_round 不匹配");if(get("blind_pack_id")&&get("blind_pack_id")!==base.blind_pack_id)throw new Error(id+" blind_pack_id 不匹配");if(get("content_fingerprint")&&get("content_fingerprint")!==base.content_fingerprint)throw new Error(id+" fingerprint 不匹配");const d={};[...CHECKS,"final_decision","error_type","severity","notes","reviewed_at"].forEach(k=>{if(nonempty(get(k)))d[k]=get(k)});state.decisions[id]=d;if(!state.reviewerId&&get("reviewer_id"))state.reviewerId=get("reviewer_id");matched++});save();applyFilters();toast("已导入 "+matched+" 条人工字段")}catch(e){alert("导入失败："+e.message)}};reader.readAsText(file,"utf-8")}
function exportAttestation(){const a=state.attestation,required=["human_reviewer_confirmed","did_not_use_ai_as_final_judge"],reviewer=state.reviewerId.trim();if(!reviewer)return alert("请先填写 reviewer_id。");if(!required.every(k=>a[k]))return alert("两项声明必须由审核者本人全部确认。");const fields=["reviewer_id","human_reviewer_confirmed","reviewed_independently","did_not_see_other_reviewer_decisions","did_not_use_ai_as_final_judge","attested_at","notes"],row={reviewer_id:reviewer,human_reviewer_confirmed:"true",reviewed_independently:"not_applicable_single_review",did_not_see_other_reviewer_decisions:"not_applicable_single_review",did_not_use_ai_as_final_judge:"true",attested_at:new Date().toISOString(),notes:a.notes||""};download("\ufeff"+[fields.join(","),fields.map(k=>csvCell(row[k])).join(",")].join("\r\n"),`reviewer_attestation_${reviewer.replace(/[^a-zA-Z0-9._-]+/g,"_")}.csv`);toast("已导出 reviewer attestation")}
$("importer").onchange=e=>{const file=e.target.files&&e.target.files[0];if(file)importCsv(file);e.target.value=""};
$("search").oninput=e=>{filters.query=e.target.value;applyFilters()};
$("helpTop").onclick=()=>{$("guide")?.scrollIntoView({behavior:"smooth",block:"start"});if($("guide"))$("guide").open=true};
document.addEventListener("keydown",e=>{if(e.target.matches("input,textarea"))return;if(e.key==="ArrowLeft"||e.key.toLowerCase()==="j"){e.preventDefault();go(-1)}if(e.key==="ArrowRight"||e.key.toLowerCase()==="k"){e.preventDefault();go(1)}});
window.__reviewAppTest={rows:DATA,config:CFG,buildCsv,decisions:()=>state.decisions,currentRow,applyPreset,isComplete,filters:()=>filters};
renderFilters();applyFilters();updateProgress();
</script>
</body>
</html>'''
    return (
        template.replace("__TITLE__", html.escape(title))
        .replace("__DATA__", data_json)
        .replace("__CONFIG__", config_json)
    )


def index_html(entries: list[dict[str, Any]]) -> str:
    cards = []
    for entry in entries:
        inherited = entry["count"] == 0 and entry["round"] == "primary" and entry["task"].startswith("composable_")
        status = "已由前序 composable 人审继承，无新增 blind rows" if inherited else f'{entry["count"]} rows'
        link = (
            f'<a class="button" href="{html.escape(entry["file"])}">打开审核页 / Open</a>'
            if entry["count"] else '<span class="button disabled">无需新增审核 / No new review</span>'
        )
        cards.append(
            f'<article><div class="round">{html.escape(ROUND_LABELS[entry["round"]])}</div>'
            f'<h2>{html.escape(TASK_LABELS[entry["task"]])}</h2><p>{html.escape(status)}</p>{link}</article>'
        )
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ServiceDiscoveryBench v0.1 人工 QA 审核入口</title><style>
:root{{--bg:#f4f7f9;--surface:#fff;--line:#d6dee4;--text:#1b2730;--muted:#65727c;--blue:#1769aa;--blue-soft:#e8f2fb;--green:#177245;--shadow:0 1px 4px rgba(21,42,55,.08)}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:"Microsoft YaHei UI","Segoe UI",Arial,sans-serif}}header{{padding:24px max(24px,6vw);background:var(--surface);border-bottom:1px solid var(--line);box-shadow:var(--shadow)}}header h1{{margin:0 0 6px;font-size:24px}}header p{{margin:0;color:var(--muted)}}main{{padding:22px max(24px,6vw) 50px}}.notice{{background:var(--blue-soft);border-left:4px solid var(--blue);padding:14px 16px;line-height:1.65;margin-bottom:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:12px}}article{{background:#fff;border:1px solid var(--line);border-radius:7px;padding:15px;box-shadow:var(--shadow)}}article h2{{font-size:16px;margin:7px 0}}article p{{color:var(--muted);margin:5px 0 14px}}.round{{font-size:12px;color:var(--blue);font-weight:700}}.button{{display:inline-block;padding:8px 11px;border-radius:6px;background:var(--blue);color:#fff;text-decoration:none;font-size:13px}}.button.disabled{{background:#e6ecef;color:var(--muted)}}code{{word-break:break-all}}footer{{font-size:12px;color:var(--muted);margin-top:20px;line-height:1.6}}
</style></head><body><header><h1>ServiceDiscoveryBench v0.1 · 人工 QA 审核入口</h1><p>沿用 composable 审核工作台 · Human-only · Blind · Single authoritative primary · Offline</p></header><main><div class="notice"><strong>使用规则：</strong>本次 G4 采用单次人工审核；primary 是唯一权威结论。secondary 仅作补充审计，不是门禁，不要求另一位审核者，也不触发分歧裁决。页面是完全本地的冻结快照，RapidAPI host 只作为目录身份元数据，不会联网。<br><strong>导出：</strong>正式导入只使用完整的 <code>primary_reviews.csv</code>；secondary 导出可保留为补充材料。未完成时文件名自动标记为 draft。</div><div class="grid">{''.join(cards)}</div><footer>快捷键：<strong>J / ←</strong> 上一条，<strong>K / →</strong> 下一条。浏览器仅在本机 localStorage 自动保存。</footer></main></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-root", type=Path, default=DEFAULT_QA_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    qa_root = args.qa_root.resolve()
    output = (args.output or qa_root / "html_review_apps").resolve()
    output.mkdir(parents=True, exist_ok=True)
    translation_path = qa_root / "query_translations_zh.json"
    translations = json.loads(translation_path.read_text(encoding="utf-8"))
    if not isinstance(translations, dict):
        raise ValueError(f"Translation map must be an object: {translation_path}")

    entries: list[dict[str, Any]] = []
    for task in TASKS:
        for review_round in ("primary", "secondary"):
            pack_path = qa_root / "blind_packs" / task / f"{review_round}_blind_pack.csv"
            template_path = qa_root / "review_templates" / task / f"{review_round}_reviews.csv"
            pack_fields, pack_rows = read_csv(pack_path)
            review_fields, review_rows = read_csv(template_path)
            if pack_fields != PACK_FIELDS:
                raise ValueError(f"Blind pack schema mismatch: {pack_path}")
            if review_fields != REVIEW_FIELDS:
                raise ValueError(f"Review template schema mismatch: {template_path}")
            if [r["benchmark_task_id"] for r in pack_rows] != [r["benchmark_task_id"] for r in review_rows]:
                raise ValueError(f"Pack/template row order mismatch: {task} {review_round}")
            public_rows = [public_pack_row(row, translations) for row in pack_rows]
            filename = f"{task}__{review_round}_review.html"
            if public_rows:
                (output / filename).write_text(app_html(task, review_round, public_rows), encoding="utf-8")
            entries.append({"task": task, "round": review_round, "count": len(public_rows), "file": filename})

    (output / "index.html").write_text(index_html(entries), encoding="utf-8")
    manifest = {"format": "sdb-v0.1-html-review-apps-composable-ui-v2", "qa_root": str(qa_root), "entries": entries}
    (output / "html_review_apps_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "apps": sum(e["count"] > 0 for e in entries), "entries": entries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
