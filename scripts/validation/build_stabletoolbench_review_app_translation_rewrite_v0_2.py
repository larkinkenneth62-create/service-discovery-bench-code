#!/usr/bin/env python
"""Rebuild StableToolBench v0.2 review HTML with readable Chinese translations.

This script only rebuilds the local HTML review page. It does not modify source
CSV files and does not run cleaning, merge, split, baseline, training, Qwen, or
external APIs.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
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


QUERY_OVERRIDES: dict[str, str] = {}

QUERY_TRANSLATION_RELATIVE_PATH = (
    "outputs/external_qa_v0_2/stabletoolbench/"
    "stabletoolbench_query_translations_zh_v0_3.json"
)


PHRASE_REPLACEMENTS = [
    ("Can you please provide me with", "请提供"),
    ("Could you please provide me with", "请提供"),
    ("Can you provide me with", "请提供"),
    ("Can you provide us with", "请提供"),
    ("Can you give me", "请给我"),
    ("Can you fetch me", "请获取"),
    ("Can you fetch", "请获取"),
    ("Can you suggest", "请推荐"),
    ("Can you recommend", "请推荐"),
    ("Can you help me", "请帮我"),
    ("Could you please", "请"),
    ("Please provide me with", "请提供"),
    ("Please provide us with", "请提供"),
    ("Please provide", "请提供"),
    ("Please fetch", "请获取"),
    ("Please include", "请包含"),
    ("I would like to", "我还想"),
    ("I also need to", "我还需要"),
    ("I also need", "我还需要"),
    ("I need to", "我需要"),
    ("I need", "我需要"),
    ("I want to", "我想"),
    ("I am", "我是"),
    ("I'm", "我是"),
    ("My friend", "我的朋友"),
    ("My family and I", "我和家人"),
    ("My company", "我的公司"),
    ("Additionally,", "另外，"),
    ("Additionally", "另外"),
    ("Also,", "同时，"),
    ("Also", "同时"),
    ("Finally,", "最后，"),
    ("Finally", "最后"),
    ("It would be great if you could", "如果可以，请"),
    ("It would be helpful if you could", "如果可以，请"),
    ("It would be helpful to", "如果能"),
    ("It would be great to", "如果能"),
    ("Thank you for your help", "谢谢"),
    ("Thank you", "谢谢"),
    ("I am interested in", "我想了解"),
    ("I'm interested in", "我想了解"),
    ("I'm curious about", "我想了解"),
    ("I'm looking for", "我正在寻找"),
    ("I'm planning", "我正在计划"),
    ("I am planning", "我正在计划"),
    ("I'm organizing", "我正在组织"),
    ("I am organizing", "我正在组织"),
    ("I'm working on", "我正在做"),
    ("I am working on", "我正在做"),
    ("As a", "作为一名"),
    ("For my", "为了我的"),
]


TERM_REPLACEMENTS = [
    ("surprise birthday party", "惊喜生日派对"),
    ("birthday party", "生日派对"),
    ("search query", "搜索词"),
    ("popular sites", "热门网站"),
    ("main keywords", "主要关键词"),
    ("gather inspiration", "收集灵感"),
    ("memorable event", "难忘的活动"),
    ("media sources", "媒体来源"),
    ("statistics", "统计数据"),
    ("startup news articles", "创业新闻文章"),
    ("Formula 1", "一级方程式赛车"),
    ("themed party", "主题派对"),
    ("quotes", "语录"),
    ("specific quote", "指定语录"),
    ("driver ID", "车手 ID"),
    ("quote ID", "语录 ID"),
    ("house plant", "室内植物"),
    ("flowering plant", "开花植物"),
    ("living room", "客厅"),
    ("ideal light conditions", "理想光照条件"),
    ("common diseases", "常见病害"),
    ("research project", "研究项目"),
    ("learning statistics", "学习统计数据"),
    ("effective study times", "高效学习时间"),
    ("recommended items", "推荐项目"),
    ("energy prices", "能源价格"),
    ("news sources", "新闻来源"),
    ("continents", "大洲"),
    ("interesting facts", "有趣事实"),
    ("popular cities", "热门城市"),
    ("joke of the day", "每日笑话"),
    ("random joke", "随机笑话"),
    ("health status", "健康状态"),
    ("UTC time", "UTC 时间"),
    ("medical guidelines", "医疗指南"),
    ("prenatal care", "产前护理"),
    ("vaccinations", "疫苗接种"),
    ("trending hashtags", "热门话题标签"),
    ("tweet", "推文"),
    ("tweets", "推文"),
    ("zip codes", "邮政编码"),
    ("power generation", "发电"),
    ("power consumption", "用电"),
    ("top tweets", "热门推文"),
    ("retweets", "转发"),
    ("likes", "点赞"),
    ("comments", "评论"),
    ("hashtag data", "话题标签数据"),
    ("music data", "音乐数据"),
    ("weekend getaway", "周末短途旅行"),
    ("breweries", "啤酒厂"),
    ("microbreweries", "小型精酿啤酒厂"),
    ("dog-friendly", "允许带狗"),
    ("reading list", "阅读清单"),
    ("flashcards", "抽认卡"),
    ("multiple-choice", "选择题"),
    ("true or false questions", "判断题"),
    ("pizza recommendations", "披萨推荐"),
    ("catalog", "目录"),
    ("specific category", "指定类别"),
    ("specific product", "指定产品"),
    ("order details", "订单详情"),
    ("income statement", "利润表"),
    ("balance sheet", "资产负债表"),
    ("cash flow", "现金流"),
    ("financial health", "财务健康状况"),
    ("log out", "退出登录"),
    ("current session", "当前会话"),
    ("trending songs", "热门歌曲"),
    ("radio stations", "广播电台"),
    ("web search results", "网页搜索结果"),
    ("nutrition information", "营养信息"),
    ("email addresses", "电子邮件地址"),
    ("dive sites", "潜水地点"),
    ("supported languages", "支持的语言"),
    ("API call", "API 调用"),
    ("endpoint", "端点"),
    ("phone number", "电话号码"),
    ("cocktail recipes", "鸡尾酒配方"),
    ("current location", "当前位置"),
    ("screenshots", "截图"),
    ("customer feedback", "客户反馈"),
    ("star ratings", "星级评分"),
    ("candidate", "候选项"),
    ("service", "服务"),
    ("API", "接口"),
    ("tool", "工具"),
    ("query", "需求"),
    ("details", "详情"),
    ("list", "列表"),
    ("available", "可用"),
    ("latest", "最新"),
    ("current", "当前"),
    ("random", "随机"),
    ("specific", "指定"),
    ("data", "数据"),
    ("information", "信息"),
    ("recommend", "推荐"),
    ("search", "搜索"),
    ("fetch", "获取"),
    ("retrieve", "检索"),
    ("provide", "提供"),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def b64_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def clean_translation(text: str, review_id: str = "") -> str:
    if not text:
        return "无文本。"
    if review_id in QUERY_OVERRIDES:
        return QUERY_OVERRIDES[review_id]
    out = text.strip()
    for src, dst in sorted(PHRASE_REPLACEMENTS, key=lambda x: -len(x[0])):
        out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
    for src, dst in sorted(TERM_REPLACEMENTS, key=lambda x: -len(x[0])):
        out = re.sub(r"\b" + re.escape(src) + r"\b", dst, out, flags=re.IGNORECASE)
    out = out.replace("?", "？").replace(".", "。").replace("!", "！")
    out = out.replace(" and ", "，并且").replace(" or ", "，或者").replace(" with ", "，带有")
    out = out.replace(" for ", "用于").replace(" from ", "来自").replace(" in ", "在")
    out = re.sub(r"\s+", " ", out)
    return out


def parse_json_array(raw: str) -> list[Any]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, list):
        return data
    return [data]


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def strip_markdown(text: str) -> str:
    text = re.sub(r"[*_`#>]+", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def service_name_from_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("service_name") or item.get("name") or item.get("tool_name") or "").strip()
    return str(item or "").strip()


def api_name_from_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("api_name") or item.get("name") or item.get("tool_name") or "").strip()
    return str(item or "").strip()


def api_description_from_item(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return strip_markdown(item.get("api_description") or item.get("description") or item.get("tool_description") or "")


def translate_fragment(text: str) -> str:
    fragment = strip_markdown(text).strip()
    known = {
        "popular sites": "热门网站",
        "main keywords": "主要关键词",
        "similar queries": "相似搜索词",
        "search query": "搜索词",
        "query": "查询词",
        "keyword": "关键词",
        "keywords": "关键词",
        "statistics": "统计数据",
        "details": "详细信息",
        "list": "列表",
        "results": "结果",
    }
    return known.get(normalize_key(fragment), clean_translation(fragment))


def count_english_words(text: str) -> int:
    return len(re.findall(r"\b[A-Za-z]{2,}\b", text or ""))


def infer_api_capability_zh(api_name: str, desc: str) -> str:
    """Return a readable Chinese capability summary without pseudo-translation."""
    source = f"{api_name} {desc}".lower()
    rules = [
        (("health", "status"), "检查服务或任务的当前状态"),
        (("search", "query"), "按查询条件执行搜索并返回结果"),
        (("search",), "按给定条件执行搜索"),
        (("recommend", "suggest"), "根据给定条件生成推荐结果"),
        (("detail", "info", "metadata"), "获取指定对象的详细信息"),
        (("list", "all", "available"), "获取符合条件的可用项目列表"),
        (("history", "historical"), "获取指定对象的历史记录"),
        (("download",), "下载指定资源或文件"),
        (("upload", "ingest"), "上传资源并查询处理进度"),
        (("translate", "translation"), "执行文本翻译或查询翻译能力"),
        (("transliteration", "transliterate"), "把文本转换为另一种文字系统的音译形式"),
        (("route", "direction", "distance", "trip"), "查询路线、距离或行程信息"),
        (("weather", "forecast"), "查询天气或预报信息"),
        (("quote",), "获取符合条件的语录内容"),
        (("news", "article"), "获取或搜索新闻文章"),
        (("product", "catalog"), "查询商品目录或商品信息"),
        (("order",), "查询订单及其明细"),
        (("user", "profile"), "查询用户列表或用户资料"),
        (("email", "inbox"), "验证电子邮箱或读取邮件信息"),
        (("phone", "number"), "验证电话号码或查询号码信息"),
        (("statistics", "stats", "count", "ratio"), "获取统计指标或数量数据"),
        (("image", "picture", "photo", "screenshot"), "获取图片或生成页面截图"),
        (("location", "station", "place", "site"), "查询地点、站点或位置信息"),
        (("price", "rate"), "查询价格、报价或费率"),
    ]
    for keywords, summary in rules:
        if any(keyword in source for keyword in keywords):
            return summary
    return "执行该接口名称所表示的具体操作"


def translate_api_description(desc: str, api_name: str = "") -> str:
    desc = strip_markdown(desc)
    if not desc:
        return ""
    patterns = [
        (r"^Get the (.+?) for a given search query\.?$", "获取给定搜索词对应的{}。"),
        (r"^Get (.+?) for a given search query\.?$", "获取给定搜索词对应的{}。"),
        (r"^Get the (.+?) for a given query\.?$", "获取给定查询词对应的{}。"),
        (r"^Get (.+?) for a given query\.?$", "获取给定查询词对应的{}。"),
        (r"^Search for (.+?)\.?$", "搜索{}。"),
        (r"^Retrieve (.+?)\.?$", "检索{}。"),
        (r"^List (.+?)\.?$", "列出{}。"),
    ]
    for pattern, template in patterns:
        match = re.match(pattern, desc, flags=re.IGNORECASE)
        if match:
            translated = template.format(translate_fragment(match.group(1)))
            if count_english_words(translated) <= 3:
                return translated
            break
    capability = infer_api_capability_zh(api_name, desc)
    return f"接口用途：{capability}。具体输入参数和返回字段请结合左侧英文说明核对。"


def collect_api_context(row: dict[str, str]) -> tuple[dict[str, list[dict[str, str]]], dict[tuple[str, str], str]]:
    """Collect richer API descriptions from the same row for review-only summaries."""
    by_service: dict[str, list[dict[str, str]]] = {}
    by_api: dict[tuple[str, str], str] = {}
    seen_service_api: set[tuple[str, str]] = set()
    for field in [
        "candidate_apis_json",
        "available_tools_or_apis_json",
        "gold_apis_json",
        "gold_tools_or_apis_json",
    ]:
        for item in parse_json_array(row.get(field, "")):
            if not isinstance(item, dict):
                continue
            service = service_name_from_item(item)
            api = api_name_from_item(item)
            desc = api_description_from_item(item)
            if service and api and desc:
                by_api.setdefault((normalize_key(service), normalize_key(api)), desc)
            if not service or not api:
                continue
            service_key = normalize_key(service)
            api_key = normalize_key(api)
            key = (service_key, api_key)
            if key in seen_service_api:
                if desc:
                    for existing in by_service.get(service_key, []):
                        if normalize_key(existing.get("api_name", "")) == api_key and not existing.get("api_description"):
                            existing["api_description"] = desc
                continue
            seen_service_api.add(key)
            by_service.setdefault(service_key, []).append({"api_name": api, "api_description": desc})
    return by_service, by_api


def service_summary_from_apis(service_name: str, service_api_context: dict[str, list[dict[str, str]]]) -> str:
    entries = service_api_context.get(normalize_key(service_name), [])
    if not entries:
        return (
            f"服务名称：{service_name}\n"
            "服务能力摘要：原始 gold/candidate service 字段只提供服务名，没有服务描述；"
            "请结合下方 gold API、candidate API 和 query 人工判断。"
        )
    lines = [
        f"服务名称：{service_name}",
        "服务能力摘要：原始 service 字段只提供名称；下面根据同一服务下的 API 描述自动整理，仅供人工审核参考。",
    ]
    for entry in entries[:5]:
        api = entry.get("api_name", "")
        desc = entry.get("api_description", "")
        if desc:
            lines.append(f"- {api}：{translate_api_description(desc, api)}")
        else:
            lines.append(f"- {api}：原始数据未提供该接口说明。")
    if len(entries) > 5:
        lines.append(f"- 还有 {len(entries) - 5} 个同服务接口未展开。")
    return "\n".join(lines)


def api_summary_from_context(item: dict[str, Any], api_context: dict[tuple[str, str], str]) -> str:
    service = service_name_from_item(item)
    api = api_name_from_item(item)
    desc = api_description_from_item(item)
    if not desc and service and api:
        desc = api_context.get((normalize_key(service), normalize_key(api)), "")
    if desc:
        return translate_api_description(desc, api)
    return f"接口名称：{api or service}。原始数据未提供该接口说明，请结合 query 和同服务候选接口人工判断。"


def add_item_translations(row: dict[str, str]) -> None:
    service_api_context, api_context = collect_api_context(row)
    for field, out_field in [
        ("gold_services_json", "gold_services_zh_json"),
        ("candidate_services_json", "candidate_services_zh_json"),
        ("gold_apis_json", "gold_apis_zh_json"),
        ("candidate_apis_json", "candidate_apis_zh_json"),
        ("available_tools_or_apis_json", "available_tools_or_apis_zh_json"),
        ("gold_tools_or_apis_json", "gold_tools_or_apis_zh_json"),
    ]:
        items = []
        for item in parse_json_array(row.get(field, "")):
            if isinstance(item, dict):
                if field.endswith("services_json"):
                    name = service_name_from_item(item)
                    desc = strip_markdown(item.get("service_description") or item.get("description") or "")
                    description_zh = clean_translation(desc) if desc else service_summary_from_apis(name, service_api_context)
                else:
                    name = item.get("api_name") or item.get("name") or item.get("tool_name") or item.get("service_name") or ""
                    description_zh = api_summary_from_context(item, api_context)
                items.append({"name": name, "description_zh": description_zh})
            else:
                name = str(item)
                if field.endswith("services_json"):
                    description_zh = service_summary_from_apis(name, service_api_context)
                else:
                    description_zh = f"名称：{name}。原始数据未提供详细说明，请结合 query 和候选集合人工判断。"
                items.append({"name": name, "description_zh": description_zh})
        row[out_field] = json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def load_query_translations(path: Path, rows: list[dict[str, str]]) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"Missing required Chinese translation file: {path}")
    try:
        translations = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise SystemExit(f"Invalid Chinese translation JSON: {path}: {exc}") from exc
    if not isinstance(translations, dict):
        raise SystemExit(f"Chinese translation file must be a JSON object: {path}")
    expected_ids = [row.get("review_item_id", "") for row in rows]
    missing = [item_id for item_id in expected_ids if not str(translations.get(item_id, "")).strip()]
    if missing:
        raise SystemExit(
            "Chinese translation file is incomplete; missing review_item_id values: "
            + ", ".join(missing[:20])
        )
    return {str(key): str(value).strip() for key, value in translations.items()}


def enrich_rows(
    rows: list[dict[str, str]], query_translations: dict[str, str]
) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        review_item_id = row.get("review_item_id", "")
        item["query_text_zh_auto"] = query_translations[review_item_id]
        add_item_translations(item)
        enriched.append(item)
    return enriched


def html_page(
    fieldnames: list[str],
    rows: list[dict[str, str]],
    generated_at: str,
    query_translations: dict[str, str],
) -> str:
    all_fields = list(fieldnames)
    for extra in [
        "query_text_zh_auto",
        "gold_services_zh_json",
        "candidate_services_zh_json",
        "gold_apis_zh_json",
        "candidate_apis_zh_json",
        "available_tools_or_apis_zh_json",
        "gold_tools_or_apis_zh_json",
    ]:
        if extra not in all_fields:
            all_fields.append(extra)
    data_b64 = b64_json(enrich_rows(rows, query_translations))
    fields_b64 = b64_json(all_fields)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StableToolBench v0.2 中文审核页</title>
<style>
:root {{ --bg:#f5f6f8; --card:#fff; --line:#d8dee8; --text:#172033; --muted:#627084; --blue:#2457d6; --red:#b42318; --green:#137a46; --amber:#9a5b00; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:"Microsoft YaHei UI","Segoe UI",Arial,sans-serif; background:var(--bg); color:var(--text); line-height:1.55; }}
header {{ position:sticky; top:0; z-index:5; background:#fff; border-bottom:1px solid var(--line); padding:12px 16px; }}
h1 {{ font-size:20px; margin:0 0 6px; }}
.danger {{ color:var(--red); font-weight:700; }}
.toolbar {{ display:grid; grid-template-columns:1.3fr 1fr 1fr 1fr auto; gap:8px; margin-top:10px; align-items:start; }}
input, textarea, button {{ font:inherit; }}
input, textarea {{ width:100%; border:1px solid var(--line); border-radius:6px; padding:8px; background:#fff; }}
button {{ border:1px solid var(--line); border-radius:6px; background:#fff; padding:7px 10px; cursor:pointer; }}
button.primary {{ background:var(--blue); border-color:var(--blue); color:#fff; }}
.layout {{ display:grid; grid-template-columns:310px minmax(520px,1fr) 390px; gap:12px; padding:12px; height:calc(100vh - 130px); }}
.panel {{ background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:auto; }}
.panel h2 {{ font-size:16px; margin:0; padding:12px; border-bottom:1px solid var(--line); background:#fbfcfe; }}
.list-item {{ width:100%; border:0; border-bottom:1px solid var(--line); text-align:left; padding:10px; background:#fff; display:grid; gap:5px; }}
.list-item.active,.list-item:hover {{ background:#eef4ff; }}
.small {{ color:var(--muted); font-size:12px; }}
.content {{ padding:12px; }}
.section {{ border:1px solid var(--line); border-radius:8px; padding:12px; margin-bottom:12px; background:#fff; }}
.section-title {{ font-weight:900; margin-bottom:8px; }}
.query-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.lang-card {{ border:1px solid var(--line); border-radius:8px; padding:10px; background:#fbfcfe; }}
.zh {{ background:#f0faf4; border-color:#b7e4c7; color:#143d2a; }}
.label {{ font-size:12px; color:#526174; font-weight:900; margin-bottom:6px; }}
.text {{ white-space:pre-wrap; overflow-wrap:anywhere; }}
.item-card {{ border:1px solid var(--line); border-radius:8px; padding:9px; margin:8px 0; background:#fbfcfe; }}
.item-title {{ font-weight:900; overflow-wrap:anywhere; }}
.item-zh {{ margin-top:6px; background:#f0faf4; border:1px solid #b7e4c7; border-radius:6px; padding:7px; color:#143d2a; white-space:pre-wrap; overflow-wrap:anywhere; }}
details {{ border:1px solid var(--line); border-radius:8px; margin:8px 0; background:#fbfcfe; }}
summary {{ padding:9px; cursor:pointer; font-weight:900; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; margin:0; padding:10px; border-top:1px solid var(--line); font-size:12px; }}
.review-grid {{ display:grid; gap:12px; padding:12px; }}
.hint {{ background:#eef2ff; border:1px solid #c7d2fe; color:#3730a3; border-radius:6px; padding:8px; font-size:13px; }}
.chip-group {{ display:flex; flex-wrap:wrap; gap:5px; }}
.chip {{ border:1px solid var(--line); background:#fff; border-radius:999px; padding:5px 9px; font-size:12px; min-height:28px; }}
.chip.active {{ background:#2457d6; color:#fff; border-color:#2457d6; }}
.chip.keep.active {{ background:#137a46; border-color:#137a46; }}
.chip.uncertain.active {{ background:#9a5b00; border-color:#9a5b00; }}
.chip.remove.active {{ background:#b42318; border-color:#b42318; }}
.review-field {{ display:grid; gap:6px; border:1px solid var(--line); border-radius:8px; padding:8px; background:#fbfcfe; }}
.review-field-title {{ font-size:13px; font-weight:900; color:#273449; }}
.preset-box {{ display:grid; gap:8px; border:2px solid #bfdbfe; border-radius:8px; padding:10px; background:#eff6ff; }}
.preset-title {{ font-size:14px; font-weight:900; color:#1e3a8a; }}
.preset-grid {{ display:grid; gap:7px; }}
.preset-btn {{ text-align:left; border-radius:8px; padding:9px 10px; background:#fff; border:1px solid #bfdbfe; }}
.preset-btn strong {{ display:block; font-size:13px; color:#172033; }}
.preset-btn span {{ display:block; font-size:12px; color:#627084; margin-top:2px; }}
.preset-btn.keep {{ border-color:#bbf7d0; background:#f0fdf4; }}
.preset-btn.uncertain {{ border-color:#fde68a; background:#fffbeb; }}
.preset-btn.remove {{ border-color:#fecaca; background:#fef2f2; }}
textarea {{ min-height:86px; resize:vertical; }}
@media (max-width:1100px) {{ .layout,.query-grid,.toolbar {{ grid-template-columns:1fr; height:auto; }} }}
</style>
</head>
<body>
<header>
<h1>StableToolBench v0.2 中文审核页</h1>
<div class="danger">本页只用于人工 QA；不生成 final dataset，不授权 merge / split / baseline / training。</div>
<div class="toolbar">
  <input id="search" placeholder="搜索 review_item_id / task_id / query">
  <div><div class="small">Policy</div><div id="policyFilter" class="chip-group"></div></div>
  <div><div class="small">QA 结论</div><div id="qaFilter" class="chip-group"></div></div>
  <div><div class="small">分组</div><div id="groupFilter" class="chip-group"></div></div>
  <label class="small"><input id="pendingOnly" type="checkbox"> 只看未审核</label>
</div>
</header>
<div class="layout">
  <aside class="panel"><h2>样本列表 <span id="count" class="small"></span></h2><div id="list"></div></aside>
  <main class="panel"><h2>当前样本</h2><div class="content" id="main"></div></main>
  <aside class="panel"><h2>人工填写</h2><div class="review-grid" id="review"></div></aside>
</div>
<script>
"use strict";
const ROWS = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{data_b64}"), c => c.charCodeAt(0))));
const FIELDNAMES = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{fields_b64}"), c => c.charCodeAt(0))));
const QA_FIELDS = {json.dumps(QA_FIELDS, ensure_ascii=False)};
const storageKey = "external_policy_v0_2_stabletoolbench_clean_translation";
let state = ROWS.map(r => Object.assign({{}}, r));
let filtered = [];
let pos = 0;
let filterState = {{policy:"", qa:"", group:""}};
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
const valueLabels = {{"":"空","keep_for_cleaning_candidate":"保留","uncertain":"不确定","remove":"删除","ok":"对齐","mismatch":"不匹配","coverage_ok":"覆盖","coverage_uncertain":"覆盖不确定","coverage_mismatch":"能力不匹配","valid":"有效","invalid":"无效","valid_catalog":"目录有效","catalog_uncertain":"目录不确定","invalid_catalog":"目录无效","task_type_ok":"任务类型正确","task_type_uncertain":"任务类型不确定","task_type_invalid":"任务类型错误","composable_not_strong_dependency":"非强组合依赖","no_obvious_leak":"无明显泄露","service_leak_blocking":"service 泄露","api_leak_blocking":"API 泄露","leak_uncertain":"泄露不确定","none":"无","low":"低","medium":"中","high":"高","critical":"严重","not_applicable":"不适用"}};
const fieldLabels = {{qa_final_decision:"最终处理结论",qa_semantic_alignment_check:"语义是否对齐",qa_capability_coverage_check:"能力是否覆盖",qa_candidate_validity_check:"候选是否有效",qa_service_catalog_check:"服务目录是否有效",qa_task_type_check:"任务类型是否正确",qa_leakage_check:"是否存在泄露",qa_error_type:"错误类型",qa_severity:"严重程度",qa_notes:"人工备注",reviewer_id:"审核人",reviewed_at:"审核时间"}};
function $(id){{return document.getElementById(id);}}
function val(r,k){{return r&&r[k]!=null?String(r[k]):"";}}
function el(tag,cls,text){{const e=document.createElement(tag); if(cls)e.className=cls; if(text!=null)e.textContent=String(text); return e;}}
function parseJson(s){{try{{return s?JSON.parse(s):null;}}catch(e){{return null;}}}}
function listify(x){{if(!x)return[]; return Array.isArray(x)?x:[x];}}
function labelOf(v){{return valueLabels[v]||v||"全部";}}
function unique(k){{return [...new Set(state.map(r=>val(r,k)).filter(Boolean))].sort();}}
function fillChipGroup(id,values,label,key){{const box=$(id); box.replaceChildren(); const all=el("button","chip active",label); all.type="button"; all.dataset.value=""; all.onclick=()=>{{filterState[key]=""; refreshFilter(id,key); apply(); render();}}; box.appendChild(all); values.forEach(v=>{{const b=el("button","chip",labelOf(v)); b.type="button"; b.dataset.value=v; b.title=v; b.onclick=()=>{{filterState[key]=v; refreshFilter(id,key); apply(); render();}}; box.appendChild(b);}});}}
function refreshFilter(id,key){{Array.from($(id).querySelectorAll("button")).forEach(b=>b.classList.toggle("active",b.dataset.value===(filterState[key]||"")));}}
function setup(){{fillChipGroup("policyFilter",unique("stable_policy_decision"),"全部","policy"); fillChipGroup("qaFilter",allowed.qa_final_decision.filter(Boolean),"全部","qa"); fillChipGroup("groupFilter",unique("stable_group"),"全部","group"); ["search","pendingOnly"].forEach(id=>$(id).addEventListener("input",()=>{{apply(); render();}}));}}
function reviewed(r){{return val(r,"qa_final_decision").trim()!=="";}}
function apply(){{const q=$("search").value.toLowerCase(), pending=$("pendingOnly").checked; filtered=[]; state.forEach((r,i)=>{{const hay=[val(r,"review_item_id"),val(r,"task_id"),val(r,"query_text"),val(r,"query_text_zh_auto")].join("\\n").toLowerCase(); if(q&&!hay.includes(q))return; if(filterState.policy&&val(r,"stable_policy_decision")!==filterState.policy)return; if(filterState.qa&&val(r,"qa_final_decision")!==filterState.qa)return; if(filterState.group&&val(r,"stable_group")!==filterState.group)return; if(pending&&reviewed(r))return; filtered.push(i);}}); if(pos>=filtered.length)pos=Math.max(0,filtered.length-1);}}
function currentIndex(){{return filtered.length?filtered[pos]:-1;}}
function renderList(){{const list=$("list"); list.replaceChildren(); $("count").textContent=filtered.length+"/"+state.length; filtered.forEach((idx,i)=>{{const r=state[idx]; const b=el("button","list-item"+(i===pos?" active":""),""); b.onclick=()=>{{pos=i;render();}}; b.appendChild(el("div","small",val(r,"review_item_id")+" | "+val(r,"task_id"))); b.appendChild(el("div","text",val(r,"query_text").slice(0,160))); b.appendChild(el("div","small",val(r,"query_text_zh_auto").slice(0,160))); b.appendChild(el("div","small",val(r,"stable_policy_decision")+" / "+(val(r,"qa_final_decision")||"未审核"))); list.appendChild(b);}});}}
function addSection(parent,title){{const s=el("section","section"); s.appendChild(el("div","section-title",title)); parent.appendChild(s); return s;}}
function namesAndZh(raw, zhRaw, kind){{const items=listify(parseJson(raw)); const zh=listify(parseJson(zhRaw)); return items.map((item,i)=>{{const z=zh[i]||{{}}; const name=typeof item==="string"?item:(kind==="api"?(item.api_name||item.name||item.tool_name||item.service_name||"未命名"):(item.service_name||item.name||item.tool_name||item.api_name||"未命名")); const desc=typeof item==="string"?"":(kind==="api"?(item.api_description||item.description||item.tool_description||""):(item.service_description||item.description||item.tool_description||"")); return {{name, desc, zh:z.description_zh||("名称："+name)}};}});}}
function renderJsonList(parent,title,raw,zhRaw,kind,open=false){{const data=namesAndZh(raw,zhRaw,kind); const d=document.createElement("details"); d.open=open; d.appendChild(el("summary","",title+"："+data.length+" 条")); const body=el("div","content"); data.forEach(x=>{{const c=el("div","item-card"); c.appendChild(el("div","item-title",x.name)); if(x.desc)c.appendChild(el("div","small text","英文说明："+x.desc)); c.appendChild(el("div","item-zh",x.zh)); body.appendChild(c);}}); d.appendChild(body); parent.appendChild(d);}}
function renderMain(){{const m=$("main"); m.replaceChildren(); const idx=currentIndex(); if(idx<0){{m.appendChild(el("div","hint","没有匹配样本"));return;}} const r=state[idx]; let s=addSection(m,"1. 先看用户需求 Query（逐条完整中文翻译）"); const grid=el("div","query-grid"); const en=el("div","lang-card"); en.appendChild(el("div","label","英文原文")); en.appendChild(el("div","text",val(r,"query_text"))); const zh=el("div","lang-card zh"); zh.appendChild(el("div","label","中文完整译文")); zh.appendChild(el("div","text",val(r,"query_text_zh_auto"))); grid.appendChild(en); grid.appendChild(zh); s.appendChild(grid);
s=addSection(m,"2. 样本关键信息"); [["review_item_id","审核ID"],["task_id","任务ID"],["stable_group","分组"],["stable_policy_decision","policy 决策"],["stable_policy_label","policy 标签"],["stable_reconstruction_needed","是否需要候选重构"],["stable_rewrite_needed","是否需要 rewrite"],["stable_requires_composable_dependency_review","是否需要组合依赖复核"]].forEach(([k,l])=>{{if(r[k]!==undefined)s.appendChild(el("div","text",l+"："+val(r,k)));}});
s=addSection(m,"3. Gold 正确答案（优先核对）"); renderJsonList(s,"Gold Services / 正确服务",val(r,"gold_services_json"),val(r,"gold_services_zh_json"),"service",true); renderJsonList(s,"Gold APIs / 正确接口",val(r,"gold_apis_json"),val(r,"gold_apis_zh_json"),"api",true); renderJsonList(s,"Gold Tools/APIs / 正确工具接口",val(r,"gold_tools_or_apis_json"),val(r,"gold_tools_or_apis_zh_json"),"api",true);
s=addSection(m,"4. Candidates 候选项"); renderJsonList(s,"Candidate Services / 候选服务",val(r,"candidate_services_json"),val(r,"candidate_services_zh_json"),"service",false); renderJsonList(s,"Candidate APIs / 候选接口",val(r,"candidate_apis_json"),val(r,"candidate_apis_zh_json"),"api",false); renderJsonList(s,"Available Tools/APIs / 可用工具接口",val(r,"available_tools_or_apis_json"),val(r,"available_tools_or_apis_zh_json"),"api",false);
s=addSection(m,"5. 审核提醒"); s.appendChild(el("div","hint","先判断 query 的真实需求，再核对 gold 是否能满足；随后看候选是否有真实选择空间，最后判断 service/API leak。不确定就选 uncertain。"));
s=addSection(m,"6. Raw JSON"); FIELDNAMES.filter(k=>k.endsWith("_json")&&!k.endsWith("_zh_json")).forEach(k=>{{const d=document.createElement("details"); d.appendChild(el("summary","",k)); d.appendChild(el("pre","",val(r,k))); s.appendChild(d);}});}}
function chipClass(v){{if(["keep_for_cleaning_candidate","ok","coverage_ok","valid","valid_catalog","task_type_ok","no_obvious_leak","none"].includes(v))return" keep"; if(["remove","mismatch","coverage_mismatch","invalid","invalid_catalog","task_type_invalid","service_leak_blocking","api_leak_blocking","critical","high"].includes(v))return" remove"; if(v==="uncertain"||String(v).includes("uncertain")||v==="medium")return" uncertain"; return"";}}
function makeButtonGroup(k){{const wrap=el("div","review-field"); wrap.appendChild(el("div","review-field-title",fieldLabels[k]||k)); const group=el("div","chip-group"); (allowed[k]||[""]).forEach(v=>{{const b=el("button","chip"+chipClass(v),labelOf(v)); b.type="button"; b.title=v; if(val(state[currentIndex()],k)===v)b.classList.add("active"); b.onclick=()=>{{state[currentIndex()][k]=v; save(); renderReview(); renderList();}}; group.appendChild(b);}}); wrap.appendChild(group); return wrap;}}
function makeInput(k,area=false){{const lab=el("label","",fieldLabels[k]||k); const e=area?document.createElement("textarea"):document.createElement("input"); e.value=val(state[currentIndex()],k); e.oninput=()=>{{state[currentIndex()][k]=e.value; save();}}; lab.appendChild(e); return lab;}}
function goNextAfterPreset(previousIdx){{save(); apply(); const nextPos=filtered.indexOf(previousIdx); if(nextPos>=0)pos=Math.min(nextPos+1,filtered.length-1); else pos=Math.min(pos,Math.max(0,filtered.length-1)); render();}}
function applyPreset(fields,note){{const idx=currentIndex(); if(idx<0)return; const r=state[idx]; Object.entries(fields).forEach(([k,v])=>{{r[k]=v;}}); if(!val(r,"reviewer_id").trim())r.reviewer_id="user_manual_preset"; r.reviewed_at=new Date().toISOString().slice(0,19); const old=val(r,"qa_notes").trim(); r.qa_notes=(old?old+"\\n":"")+"Preset: "+note; goNextAfterPreset(idx);}}
function presetButton(cls,title,desc,fields,note){{const b=el("button","preset-btn "+cls,""); b.type="button"; b.appendChild(el("strong","",title)); b.appendChild(el("span","",desc)); b.onclick=()=>applyPreset(fields,note); return b;}}
function renderPresetPanel(){{const box=el("div","preset-box"); box.appendChild(el("div","preset-title","预设审核方案：一键填写并自动下一条")); box.appendChild(el("div","hint","预设只帮你快速填写 QA 字段；最终判断仍以你人工审核为准。")); const g=el("div","preset-grid"); g.appendChild(presetButton("keep","全部符合，保留","语义对齐、能力覆盖、候选有效、无明显泄露。",{{qa_final_decision:"keep_for_cleaning_candidate",qa_semantic_alignment_check:"ok",qa_capability_coverage_check:"coverage_ok",qa_candidate_validity_check:"valid",qa_service_catalog_check:"valid_catalog",qa_task_type_check:"task_type_ok",qa_leakage_check:"no_obvious_leak",qa_error_type:"none",qa_severity:"none"}},"all checks pass; keep as cleaning candidate")); g.appendChild(presetButton("uncertain","存在 service leak，但能力满足","query 暴露服务名；gold 能完成任务，但不适合直接进 clean 主集。",{{qa_final_decision:"uncertain",qa_semantic_alignment_check:"ok",qa_capability_coverage_check:"coverage_ok",qa_candidate_validity_check:"valid",qa_service_catalog_check:"valid_catalog",qa_task_type_check:"task_type_ok",qa_leakage_check:"service_leak_blocking",qa_error_type:"service_leak",qa_severity:"medium"}},"service leak blocking, but capability coverage is OK")); g.appendChild(presetButton("remove","存在 API leak，但能力满足","query 暴露 gold API/endpoint；主评测应删除或重写。",{{qa_final_decision:"remove",qa_semantic_alignment_check:"ok",qa_capability_coverage_check:"coverage_ok",qa_candidate_validity_check:"valid",qa_service_catalog_check:"valid_catalog",qa_task_type_check:"task_type_ok",qa_leakage_check:"api_leak_blocking",qa_error_type:"api_leak",qa_severity:"high"}},"API leak blocking")); g.appendChild(presetButton("remove","gold 不能满足 query","gold service/API 与核心需求不匹配或缺关键能力。",{{qa_final_decision:"remove",qa_semantic_alignment_check:"mismatch",qa_capability_coverage_check:"coverage_mismatch",qa_candidate_validity_check:"valid",qa_service_catalog_check:"valid_catalog",qa_task_type_check:"task_type_ok",qa_leakage_check:"no_obvious_leak",qa_error_type:"capability_mismatch",qa_severity:"high"}},"gold/capability mismatch")); g.appendChild(presetButton("uncertain","候选空间无效或太弱","候选没有真实选择空间、候选数等于 gold、或需要 reconstruction。",{{qa_final_decision:"uncertain",qa_semantic_alignment_check:"ok",qa_capability_coverage_check:"coverage_ok",qa_candidate_validity_check:"invalid",qa_service_catalog_check:"catalog_uncertain",qa_task_type_check:"task_type_uncertain",qa_leakage_check:"no_obvious_leak",qa_error_type:"candidate_space_invalid",qa_severity:"medium"}},"candidate choice space invalid")); g.appendChild(presetButton("uncertain","不确定，留待复核","语义、能力、泄露或任务类型有任一项看不准。",{{qa_final_decision:"uncertain",qa_semantic_alignment_check:"uncertain",qa_capability_coverage_check:"coverage_uncertain",qa_candidate_validity_check:"uncertain",qa_service_catalog_check:"catalog_uncertain",qa_task_type_check:"task_type_uncertain",qa_leakage_check:"leak_uncertain",qa_error_type:"uncertain",qa_severity:"medium"}},"uncertain; needs later review")); box.appendChild(g); return box;}}
function renderReview(){{const p=$("review"); p.replaceChildren(); const idx=currentIndex(); if(idx<0)return; p.appendChild(el("div","hint","这里只填写人工 QA 字段；不会把 policy 自动当 final。")); p.appendChild(renderPresetPanel()); ["qa_final_decision","qa_semantic_alignment_check","qa_capability_coverage_check","qa_candidate_validity_check","qa_service_catalog_check","qa_task_type_check","qa_leakage_check","qa_severity"].forEach(k=>p.appendChild(makeButtonGroup(k))); p.appendChild(makeInput("qa_error_type")); p.appendChild(makeInput("qa_notes",true)); p.appendChild(makeInput("reviewer_id")); p.appendChild(makeInput("reviewed_at")); const btns=el("div",""); const ex=el("button","primary","导出 CSV"); ex.onclick=()=>exportCsv(); btns.appendChild(ex); const im=el("button","","导入 CSV"); im.onclick=()=>$("importer").click(); btns.appendChild(im); p.appendChild(btns); const file=document.createElement("input"); file.type="file"; file.id="importer"; file.style.display="none"; file.onchange=e=>{{if(e.target.files[0])importCsv(e.target.files[0]);}}; p.appendChild(file);}}
function render(){{renderList(); renderMain(); renderReview();}}
function save(){{localStorage.setItem(storageKey,JSON.stringify(state.map(r=>Object.fromEntries(["review_item_id",...QA_FIELDS].map(k=>[k,val(r,k)])))));}}
function load(){{const raw=localStorage.getItem(storageKey); if(!raw)return; const arr=JSON.parse(raw); const map=new Map(arr.map(x=>[x.review_item_id,x])); state.forEach(r=>{{const p=map.get(r.review_item_id); if(p)QA_FIELDS.forEach(k=>r[k]=val(p,k));}});}}
function csvEsc(v){{return '"'+String(v??"").replaceAll('"','""')+'"';}}
function exportCsv(){{const fields=FIELDNAMES.slice(); QA_FIELDS.forEach(k=>{{if(!fields.includes(k))fields.push(k);}}); const pending=state.filter(r=>!reviewed(r)).length; const name="stabletoolbench_filter_policy_review_items_v0_2_reviewed"+(pending?"_draft":"")+".csv"; const lines=[fields.map(csvEsc).join(",")]; state.forEach(r=>lines.push(fields.map(k=>csvEsc(val(r,k))).join(","))); const blob=new Blob(["\\ufeff"+lines.join("\\r\\n")+"\\r\\n"],{{type:"text/csv;charset=utf-8"}}); const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=name; a.click(); URL.revokeObjectURL(a.href);}}
function parseCsv(text){{if(text.charCodeAt(0)===0xFEFF)text=text.slice(1); const rows=[]; let row=[],cell="",q=false; for(let i=0;i<text.length;i++){{const c=text[i],n=text[i+1]; if(q){{if(c==='"'&&n==='"'){{cell+='"';i++;}}else if(c==='"')q=false;else cell+=c;}}else{{if(c==='"')q=true;else if(c===','){{row.push(cell);cell='';}}else if(c==='\\n'){{row.push(cell);rows.push(row);row=[];cell='';}}else if(c!=='\\r')cell+=c;}}}} if(cell||row.length){{row.push(cell);rows.push(row);}} const h=rows.shift()||[]; return rows.filter(r=>r.some(Boolean)).map(r=>Object.fromEntries(h.map((k,i)=>[k,r[i]||""])));}}
function importCsv(file){{const rd=new FileReader(); rd.onload=()=>{{const arr=parseCsv(String(rd.result||"")); const map=new Map(arr.map(r=>[r.review_item_id,r])); let n=0; state.forEach(r=>{{const p=map.get(r.review_item_id); if(p){{QA_FIELDS.forEach(k=>{{if(p[k]!==undefined)r[k]=p[k];}}); n++;}}}}); save(); apply(); render(); alert("已导入 "+n+" 条人工字段");}}; rd.readAsText(file,"utf-8");}}
document.addEventListener("keydown",e=>{{const tag=(e.target.tagName||"").toLowerCase(); if(["input","textarea"].includes(tag))return; if(e.key==="j"||e.key==="ArrowRight"){{pos=Math.min(pos+1,filtered.length-1);render();}} if(e.key==="k"||e.key==="ArrowLeft"){{pos=Math.max(pos-1,0);render();}}}});
load(); setup(); apply(); render();
</script>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild StableToolBench v0.2 review app with rewritten Chinese translations.")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    csv_path = root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv"
    html_path = root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_app_v0_2.html"
    if not csv_path.exists():
        raise SystemExit(f"Missing required CSV: {csv_path}")
    if html_path.exists():
        backup = html_path.with_suffix(".before_translation_rewrite.html")
        if backup.exists():
            backup = html_path.with_suffix(".before_gold_service_summary_update.html")
        shutil.copy2(html_path, backup)
    fields, rows = read_csv(csv_path)
    translation_path = root / QUERY_TRANSLATION_RELATIVE_PATH
    query_translations = load_query_translations(translation_path, rows)
    html_path.write_text(
        html_page(fields, rows, now_iso(), query_translations), encoding="utf-8"
    )
    print(f"stable_html={html_path}")
    print(f"rows={len(rows)}")
    print(f"query_translations={len(query_translations)}")
    print("qwen_called=false")
    print("external_api_called=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
