#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate a single-file offline review app for main-four-task dry-run samples.

Scope guard:
- No full cleaning.
- No baseline.
- No model training.
- No split.
- No top200 continuation.
- No new full-G3 search.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_INPUTS = {
    "multi_service": PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_dryrun_v0_2"
    / "multi_service_discovery_task_level.csv",
    "multi_api": PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_dryrun_v0_2"
    / "multi_api_recommendation_task_level.csv",
    "schema": PROJECT_ROOT
    / "docs"
    / "phase1"
    / "service_discovery_bench_v0_2_schema_draft.md",
    "dryrun_report": PROJECT_ROOT
    / "docs"
    / "phase1"
    / "main_four_tasks_dryrun_v0_2_report.md",
}

OUTPUT_HTML = (
    PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_manual_check_v0_2"
    / "main_four_tasks_review_app_40.html"
)
BACKUP_HTML = (
    PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_manual_check_v0_2"
    / "main_four_tasks_review_app_40.backup.html"
)
OUTPUT_REPORT = (
    PROJECT_ROOT
    / "docs"
    / "phase1"
    / "main_four_tasks_review_app_generation_report_v0_2.md"
)
HIERARCHY_UPDATE_REPORT = (
    PROJECT_ROOT
    / "docs"
    / "phase1"
    / "main_four_tasks_review_app_service_api_hierarchy_update_report.md"
)
ARCHIVE_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "run_archives"
    / "2026-06-24_main_four_tasks_review_app_hierarchy_update_v0_2"
)


QUERY_ZH_BY_TASK_ID = {
    "ToolBench_G2_1": "我正在开始做电商业务，需要集成包裹追踪功能。请提供 ID 为 6045e2f44e1b233199a5e77a 的货运追踪数据。另外，我还想检查 SQUAKE 认证系统的健康状态。",
    "ToolBench_G2_2": "我计划去伊斯坦布尔旅行，需要了解不同城区的邮政编码。请提供伊斯坦布尔车牌号 34 对应城区的邮政编码。另外，我还想用任务 ID 987654321 追踪一个包裹。",
    "ToolBench_G2_3": "我计划去布宜诺斯艾利斯旅行，需要了解不同城区的邮政编码。请提供布宜诺斯艾利斯车牌号 1 对应城区的邮政编码。另外，我还想用任务 ID 987654321 追踪一个包裹。",
    "ToolBench_G2_4": "我计划去土耳其旅行，需要了解伊斯坦布尔的邮政编码信息。请提供车牌号 34 对应的伊斯坦布尔省邮政编码和区县信息。另外，我想知道伊斯坦布尔是否有可用的货代/转运机构，并获取它们的名称和联系电话。",
    "ToolBench_G2_5": "我计划去土耳其旅行，需要了解伊斯坦布尔的邮政编码信息。请提供车牌号 34 对应的伊斯坦布尔省邮政编码和区县信息。另外，我想知道伊斯坦布尔是否有可用的货代/转运机构，并获取它们的名称和联系电话。",
    "ToolBench_G2_8": "我正在为最好的朋友筹备生日惊喜派对。请帮我追踪为派对订购的包裹，参考号是 ABC123。同时请提供包裹的最新状态和配送历史。另外，请用邮编 12345 查询派对地点的地址详情。",
    "ToolBench_G2_10": "我正在组织一场慈善活动，需要追踪收到的捐赠物品。请帮我追踪包含捐赠物品的包裹，追踪号是 GHI789。同时请提供最新状态和位置更新。另外，请用邮编 24680 查询活动场地的地址详情。",
    "ToolBench_G2_11": "我是小企业主，需要追踪产品配送。请帮我用追踪号 JKL012 追踪包裹，并提供最新状态和位置更新。另外，请用邮编 13579 查询附近邮局的地址详情。",
    "ToolBench_G2_12": "我正在为伴侣策划惊喜约会之夜。请帮我追踪装有惊喜礼物的包裹，追踪号是 MNO345。同时请提供最新状态和位置更新。另外，请用邮编 56789 查询附近餐厅的地址详情。",
    "ToolBench_G2_17": "我需要追踪 Pack & Send 的参考号 ReferenceNumberHere 对应的包裹，请获取相关信息。另外，请从 Pridnestrovie Post 获取追踪号 RB413450335SG 对应包裹的追踪信息。",
    "ToolBench_G2_27": "我正在为妹妹的生日筹备惊喜派对，需要追踪她礼物的配送。请提供 ID 为 6045e2f44e1b233199a5e77a 的货运当前状态和详细信息。另外，我还想知道该货运是否有错误或错误消息。",
    "ToolBench_G2_29": "我正在为住在伊斯坦布尔的好朋友筹备惊喜派对。请帮我查找他所在社区的邮政编码和地址。另外，我需要用追踪号 YT2003521266065328 追踪寄给他的包裹，并提供当前状态和承运商信息。",
    "ToolBench_G2_30": "我正在巴西组织一场慈善活动，需要寄送邀请函。请用 CEP 号码 75094080 获取地址详情。另外，我还想用 AWB 号码 000-99999970 追踪活动物资的货运，并提供追踪更新和碳排放信息。",
    "ToolBench_G2_31": "我正在计划去土耳其的家庭假期，想探索不同城市。请提供土耳其所有城市的邮政编码和社区详情。另外，我想追踪我们乘坐航班的碳排放，请提供每段行程的航班详情和碳排放信息。",
    "ToolBench_G2_32": "我是跨国公司的物流经理，需要优化运输路线。请获取土耳其所有城市的邮政编码和社区详情。另外，我想追踪货运的碳排放，请提供每票货运的 AWB 号码和碳排放信息。",
    "ToolBench_G2_33": "我是一名旅行博主，计划去巴西旅行并探索不同城市。请用 CEP 号码 75094080 提供地址详情。另外，我想追踪航班的碳排放，请提供每段行程的航班详情和碳排放信息。",
    "ToolBench_G2_34": "我是全球公司的供应链经理，需要优化运输路线。请用 CEP 号码 75094080 获取地址详情。另外，我想追踪货运的碳排放，请提供每票货运的 AWB 号码和碳排放信息。",
    "ToolBench_G2_35": "我是一名记者，正在写一篇关于不同国家邮政服务的文章。请提供土耳其所有城市的邮政编码和社区详情。另外，我想追踪航空货运行业的碳排放，请为一组样例货运提供 AWB 号码和碳排放信息。",
    "ToolBench_G2_36": "我正在为最好的朋友毕业筹备惊喜派对。请推荐一些独特的派对游戏和装饰；另外，我想收集关于毕业派对最新趋势的新闻文章作为灵感；还请推荐当地酒店作为住宿选择。",
    "ToolBench_G2_37": "我正在寻找可靠的运输服务，把包裹寄给在巴西的家人。请提供 ID 为 6045e2f44e1b233199a5e77a 的包裹追踪数据。我还需要用 CEP 号码 75094080 查询目的地地址详情。",
    "ToolBench_G2_39": "我的公司正在把业务扩展到土耳其，需要在伊斯坦布尔为新办公室寻找合适地点。请推荐一些有可用办公空间的区域，并提供相应邮政编码。另外，请追踪 ID 为 6045e2f44e1b233199a5e77a 的包裹。",
    "ToolBench_G2_46": "我最近搬到了新地址，需要更新个人信息。请用邮政编码 75094080 检索我的地址详情。另外，我还想用追踪号 RB413450335SG 追踪一个包裹。",
    "ToolBench_G2_47": "我正在做一个项目，需要一些与巴西地址相关的数据。请用邮政编码 75094080 提供地址详情。另外，我还想知道 SQUAKE API 的健康状态。",
    "ToolBench_G2_52": "我正在组织公司活动，需要寄出邀请函。请用追踪号 RB413450335SG 追踪包裹。另外，请用邮政编码 75094080 提供地址详情。",
    "ToolBench_G2_68": "我正在布宜诺斯艾利斯组织商务活动，需要给参会者发送邀请函。请使用 GS1Parser 工具为活动门票生成二维码。另外，请查找 Correo Argentino、OCA 和 Andreani 最近的网点，用于分发门票。",
    "ToolBench_G2_85": "我需要帮助追踪 ID 为 6045e2f44e1b233199a5e77a 的包裹配送，请持续提供当前状态更新。另外，请获取 Pack & Send 参考号 ReferenceNumberHere 的相关信息。还要确保 suivi-colis API 正常运行。",
    "ToolBench_G2_91": "我正在阿根廷组织家庭聚会，需要寄出邀请函。请获取我寄给表亲的邀请函追踪数据，追踪 ID 是 6045e2f44e1b233199a5e77a。另外，请提供布宜诺斯艾利斯州的城市列表。",
    "ToolBench_G2_93": "请帮我用追踪号 YT2003521266065328 追踪包裹。我需要知道承运商和包裹当前状态。另外，请提供海关代理 Gondrand 的联系信息。谢谢。",
    "ToolBench_G2_98": "请帮我查找海关代理 Gondrand 的联系详情。我需要就一个包裹与他们联系。另外，我想用追踪号 RB413450335SG 追踪这个包裹。感谢帮助！",
    "ToolBench_G1_19": "我需要用追踪号 NY323068698GB 追踪一个包裹。请提供该包裹的追踪信息，并检测这个追踪号对应的承运商。",
    "ToolBench_G1_20": "我的朋友寄出了一个追踪号为 YT2003521266065328 的包裹。我需要追踪这个包裹并获取追踪信息。另外，请检测这个追踪号对应的承运商。",
    "ToolBench_G1_21": "我想用追踪号 YT2003521266065328 追踪一个包裹。请提供该包裹的追踪信息，并检测这个追踪号对应的承运商。",
    "ToolBench_G1_22": "请用追踪号 YT2003521266065328 追踪包裹并提供追踪详情。同时请检测这个追踪号对应的承运商。",
    "ToolBench_G1_23": "我有一个追踪号为 YT2003521266065328 的包裹。请追踪这个包裹并给我追踪信息。另外，请检测这个追踪号对应的承运商。",
    "ToolBench_G1_24": "我的公司寄出了一个追踪号为 YT2003521266065328 的包裹。我需要追踪这个包裹并获取追踪信息。同时请检测这个追踪号对应的承运商。",
}

SERVICE_NAME_ZH = {
    "Air Cargo CO2 Track And Trace": "航空货运 CO2 跟踪与追踪",
    "Amex Australia (Fastway Australia) Tracking": "Aramex/Fastway 澳大利亚物流追踪",
    "CEP Brazil": "巴西 CEP 地址查询",
    "Create Container Tracking": "创建集装箱追踪",
    "GS1Parser": "GS1 条码解析器",
    "Kargom Nerede": "土耳其包裹在哪里",
    "Orderful": "Orderful EDI 服务",
    "Pack & Send": "Pack & Send 物流服务",
    "Pridnestrovie Post": "德涅斯特河沿岸邮政",
    "SQUAKE": "SQUAKE 碳排放与可持续服务",
    "TrackingMore_v2": "TrackingMore 全球包裹追踪",
    "Transitaires": "货代/清关代理服务",
    "Transportistas de Argentina": "阿根廷承运商服务",
    "Turkey Postal Codes": "土耳其邮政编码",
    "suivi-colis": "新喀里多尼亚包裹追踪",
}

SERVICE_DESC_ZH = {
    "Air Cargo CO2 Track And Trace": "追踪航空货运，并通过 190 多家航空公司的数据测量 CO2 排放。",
    "Amex Australia (Fastway Australia) Tracking": "用于 Aramex Australia，也称 Fastway Australia 的包裹追踪 API；问题可联系 [contact removed from public mirror]。",
    "CEP Brazil": "一个免费 API，可通过巴西 CEP 邮编返回 Correios 地址数据。",
    "Create Container Tracking": "用户可以使用这个 API 发起集装箱追踪。",
    "GS1Parser": "解析并验证 GS1 条码数据。",
    "Kargom Nerede": "支持查询土耳其及多家国际物流公司的货运/包裹信息。",
    "Orderful": "用于 EDI 交易数据的 API。",
    "Pack & Send": "物流和货运服务。",
    "Pridnestrovie Post": "德涅斯特河沿岸地区的包裹追踪服务。",
    "SQUAKE": "帮助企业构建可持续产品；可为旅行、出行和物流企业实时计算碳排放并购买认证气候贡献。",
    "TrackingMore_v2": "一体化全球包裹追踪工具，支持追踪 472 家国际快递/承运商。",
    "Transitaires": "新喀里多尼亚的清关货代服务。",
    "Transportistas de Argentina": "获取阿根廷 Andreani、OCA 和 Correo Argentino 的网点、地点和运费价格。",
    "Turkey Postal Codes": "土耳其邮政编码服务。",
    "suivi-colis": "新喀里多尼亚包裹追踪 API。",
}

API_NAME_ZH = {
    "PULL (track)": "拉取货运追踪信息",
    "Track Package": "追踪包裹",
    "Retorna Dados do Endereço através do CEP": "通过 CEP 返回地址数据",
    "Get Tracking Data": "获取追踪数据",
    "/parse": "解析 GS1 条码",
    "companies": "公司列表",
    "Transactions": "交易查询",
    "/api/Tracking/": "Pack & Send 追踪接口",
    "Get track info": "获取追踪信息",
    "Checkhealth": "健康检查",
    "Projects": "项目列表",
    "carriers/detect": "检测承运商",
    "carriers/list": "列出承运商",
    "packages/track (Deprecated)": "获取包裹追踪信息（旧接口）",
    "packages/v2/track": "获取包裹追踪信息 v2",
    "Transitaire": "获取指定货代",
    "Transitaires": "返回全部货代",
    "/cities/postcode/:stateIsoCode/:postCode": "按州 ISO 代码和邮编列出城市",
    "/cities/search/:stateIsoCode/:keyword": "按州 ISO 代码和关键词搜索城市",
    "/cities/states": "列出阿根廷州及 ISO 代码",
    "/cities/states/:stateIsoCode": "按州 ISO 代码列出城市",
    "/offices/search/:service/:stateIsoCode/:keyword": "按服务、州 ISO 代码和关键词搜索网点",
    "/quotes/postcode/oca/:cuit/:operativa/:cost/:weight/:volume/:postCodeSrc/:postCodeDst": "按邮编获取 OCA e-Pack 报价",
    "/tracking/correo_argentino/result_task/:task_id": "按任务 ID 获取追踪结果",
    "il": "土耳其车牌/省份编号",
    "All": "全部历史",
    "Count": "历史步骤数量",
    "Health": "API 健康状态",
    "Latest": "最新状态",
}

API_DESC_ZH = {
    "PULL (track)": "提供有效 AWB 后，可以获取该货运的追踪信息。",
    "Track Package": "使用包裹追踪号查询包裹运输详情。",
    "Retorna Dados do Endereço através do CEP": "返回地址数据。",
    "Get Tracking Data": "用户可以通过该接口取回追踪数据。",
    "/parse": "解析输入的 GS1 条码数据。",
    "companies": "公司列表。",
    "Transactions": "按 ID 获取交易信息。",
    "/api/Tracking/": "如果提供 Pack & Send 参考号，可以返回相关追踪信息。",
    "Get track info": "按追踪号获取追踪信息。",
    "Checkhealth": "检查 API 或系统健康状态。",
    "Projects": "获取项目相关信息。",
    "carriers/detect": "通过追踪号检测承运商。",
    "carriers/list": "列出全部支持的承运商。",
    "packages/track (Deprecated)": "获取指定包裹的追踪信息。",
    "packages/v2/track": "获取指定包裹的追踪信息。",
    "Transitaire": "获取某一个货代的信息。",
    "Transitaires": "返回所有货代。",
    "/cities/postcode/:stateIsoCode/:postCode": "根据州 ISO 代码和邮编列出城市。",
    "/cities/search/:stateIsoCode/:keyword": "根据州 ISO 代码和关键词名称搜索城市。",
    "/cities/states": "列出阿根廷所有州及其 ISO 代码。",
    "/cities/states/:stateIsoCode": "根据州 ISO 代码列出城市。",
    "/offices/search/:service/:stateIsoCode/:keyword": "根据州 ISO 代码和邮编/关键词列出服务网点。",
    "/quotes/postcode/oca/:cuit/:operativa/:cost/:weight/:volume/:postCodeSrc/:postCodeDst": "根据 OCA e-Pack 的邮编等参数获取报价。",
    "/tracking/correo_argentino/result_task/:task_id": "获取某个任务 ID 的结果。",
    "il": "土耳其省份/车牌编号，范围 1 到 81。",
    "All": "返回包裹从发出到当前最新状态的全部历史。",
    "Count": "统计历史中的步骤数量，便于限制网络或 IoT 资源消耗，也可用于更高效地轮询状态。",
    "Health": "获取 API 健康状态。",
    "Latest": "返回包裹当前状态，即最新状态。",
}


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def check_inputs() -> List[str]:
    return [rel(path) for path in REQUIRED_INPUTS.values() if not path.exists()]


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def try_load_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def metadata(row: Dict[str, str]) -> Dict[str, Any]:
    value = try_load_json(row.get("metadata_json", ""))
    return value if isinstance(value, dict) else {}


def list_count_from_json(row: Dict[str, str], field: str) -> int:
    value = try_load_json(row.get(field, ""))
    if isinstance(value, list):
        return len(value)
    return 0


def get_count(row: Dict[str, str], key: str, fallback_json_field: str) -> int:
    meta = metadata(row)
    value = meta.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return list_count_from_json(row, fallback_json_field)


def query_length_bucket(query: str) -> str:
    count = len((query or "").split())
    if count < 35:
        return "short"
    if count < 75:
        return "medium"
    return "long"


def service_signature(row: Dict[str, str]) -> str:
    gold = try_load_json(row.get("gold_services_json", ""))
    if isinstance(gold, list):
        return " | ".join(str(item) for item in gold[:3])
    return ""


def task_id_sort_key(row: Dict[str, str]) -> Tuple[str, int]:
    task_id = row.get("task_id", "")
    tail = task_id.rsplit("_", 1)[-1]
    return task_id, int(tail) if tail.isdigit() else 0


def choose_diverse(
    rows: Sequence[Dict[str, str]],
    max_rows: int,
    key_fn,
    mandatory_filters: Sequence[Tuple[str, Any]] | None = None,
) -> List[Dict[str, str]]:
    selected: List[Dict[str, str]] = []
    seen_ids = set()

    def add(row: Dict[str, str]) -> None:
        task_id = row.get("task_id", "")
        if len(selected) >= max_rows:
            return
        if task_id and task_id not in seen_ids:
            selected.append(row)
            seen_ids.add(task_id)

    if mandatory_filters:
        for _, predicate in mandatory_filters:
            for row in rows:
                if predicate(row):
                    add(row)
                    break

    seen_keys = set()
    for row in rows:
        key = key_fn(row)
        if key not in seen_keys:
            add(row)
            seen_keys.add(key)
        if len(selected) >= max_rows:
            break

    for row in rows:
        add(row)
        if len(selected) >= max_rows:
            break

    return selected


def sample_multi_service(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            get_count(row, "gold_service_count", "gold_services_json"),
            get_count(row, "candidate_service_count", "candidate_services_json"),
            query_length_bucket(row.get("query_text", "")),
            service_signature(row),
            task_id_sort_key(row),
        ),
    )

    def key_fn(row: Dict[str, str]) -> Tuple[Any, ...]:
        return (
            get_count(row, "gold_service_count", "gold_services_json"),
            get_count(row, "candidate_service_count", "candidate_services_json"),
            query_length_bucket(row.get("query_text", "")),
            service_signature(row),
        )

    return choose_diverse(sorted_rows, 20, key_fn)


def sample_multi_api(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            row.get("source_group", ""),
            row.get("leak_status", ""),
            get_count(row, "gold_api_count", "gold_apis_json"),
            get_count(row, "candidate_api_count", "candidate_apis_json"),
            query_length_bucket(row.get("query_text", "")),
            task_id_sort_key(row),
        ),
    )
    mandatory = [
        ("G1", lambda row: row.get("source_group") == "G1"),
        ("G2", lambda row: row.get("source_group") == "G2"),
        (
            "no_obvious_leak",
            lambda row: row.get("leak_status") == "no_obvious_leak",
        ),
        (
            "service_leak_only",
            lambda row: row.get("leak_status") == "service_leak_only",
        ),
        (
            "G1_no_obvious",
            lambda row: row.get("source_group") == "G1"
            and row.get("leak_status") == "no_obvious_leak",
        ),
        (
            "G2_service_leak",
            lambda row: row.get("source_group") == "G2"
            and row.get("leak_status") == "service_leak_only",
        ),
    ]

    def key_fn(row: Dict[str, str]) -> Tuple[Any, ...]:
        return (
            row.get("source_group", ""),
            row.get("leak_status", ""),
            get_count(row, "gold_api_count", "gold_apis_json"),
            get_count(row, "candidate_api_count", "candidate_apis_json"),
            query_length_bucket(row.get("query_text", "")),
        )

    return choose_diverse(sorted_rows, 20, key_fn, mandatory)


def normalize_gold_api(item: Any) -> Tuple[str, str]:
    if isinstance(item, dict):
        return str(item.get("service_name", "")), str(item.get("api_name", ""))
    return "", str(item)


def parsed_list(row: Dict[str, str], field: str) -> List[Any]:
    value = try_load_json(row.get(field, ""))
    return value if isinstance(value, list) else []


def make_search_blob(parts: Iterable[Any]) -> str:
    joined = " ".join(json.dumps(part, ensure_ascii=False) for part in parts)
    return joined.lower()


def service_name_zh(name: str) -> str:
    return SERVICE_NAME_ZH.get(name, name)


def service_desc_zh(name: str, desc: str) -> str:
    return SERVICE_DESC_ZH.get(name, desc or "")


def api_name_zh(name: str) -> str:
    return API_NAME_ZH.get(name, name)


def api_desc_zh(name: str, desc: str) -> str:
    return API_DESC_ZH.get(name, desc or "")


def query_zh(task_id: str, query_text: str) -> str:
    return QUERY_ZH_BY_TASK_ID.get(task_id, query_text)


def enrich_row(row: Dict[str, str], review_id: str) -> Dict[str, Any]:
    candidate_services = parsed_list(row, "candidate_services_json")
    candidate_apis = parsed_list(row, "candidate_apis_json")
    gold_services = [str(item) for item in parsed_list(row, "gold_services_json")]
    gold_apis_raw = parsed_list(row, "gold_apis_json")
    gold_api_pairs = {normalize_gold_api(item) for item in gold_apis_raw}
    gold_service_set = set(gold_services)

    enriched_services = []
    for service in candidate_services:
        service_obj = service if isinstance(service, dict) else {"service_name": str(service)}
        copied = dict(service_obj)
        copied["is_gold_service"] = str(copied.get("service_name", "")) in gold_service_set
        copied["service_name_zh"] = service_name_zh(str(copied.get("service_name", "")))
        copied["service_description_zh"] = service_desc_zh(
            str(copied.get("service_name", "")),
            str(copied.get("service_description", "")),
        )
        enriched_services.append(copied)

    enriched_apis = []
    for api in candidate_apis:
        api_obj = api if isinstance(api, dict) else {"api_name": str(api)}
        copied = dict(api_obj)
        pair = (str(copied.get("service_name", "")), str(copied.get("api_name", "")))
        copied["is_gold_api"] = bool(copied.get("is_gold_api")) or pair in gold_api_pairs
        copied["service_name_zh"] = service_name_zh(str(copied.get("service_name", "")))
        copied["api_name_zh"] = api_name_zh(str(copied.get("api_name", "")))
        copied["api_description_zh"] = api_desc_zh(
            str(copied.get("api_name", "")),
            str(copied.get("api_description", "")),
        )
        enriched_apis.append(copied)

    meta = metadata(row)
    meta.setdefault(
        "candidate_service_count",
        get_count(row, "candidate_service_count", "candidate_services_json"),
    )
    meta.setdefault(
        "candidate_api_count",
        get_count(row, "candidate_api_count", "candidate_apis_json"),
    )
    meta.setdefault(
        "gold_service_count", get_count(row, "gold_service_count", "gold_services_json")
    )
    meta.setdefault("gold_api_count", get_count(row, "gold_api_count", "gold_apis_json"))

    data = {
        "review_id": review_id,
        "task_id": row.get("task_id", ""),
        "task_type": row.get("task_type", ""),
        "source_dataset": row.get("source_dataset", ""),
        "source_group": row.get("source_group", ""),
        "query_text": row.get("query_text", ""),
        "query_text_zh": query_zh(row.get("task_id", ""), row.get("query_text", "")),
        "leak_status": row.get("leak_status", ""),
        "semantic_alignment_status": row.get("semantic_alignment_status", ""),
        "cleaning_status": row.get("cleaning_status", ""),
        "task_eligibility": row.get("task_eligibility", ""),
        "task_bucket": row.get("task_bucket", ""),
        "split": row.get("split", ""),
        "candidate_services": enriched_services,
        "candidate_apis": enriched_apis,
        "gold_services": gold_services,
        "gold_services_zh": [
            {"service_name": name, "service_name_zh": service_name_zh(name)}
            for name in gold_services
        ],
        "gold_apis": gold_apis_raw,
        "gold_apis_zh": [
            {
                "service_name": normalize_gold_api(item)[0],
                "service_name_zh": service_name_zh(normalize_gold_api(item)[0]),
                "api_name": normalize_gold_api(item)[1],
                "api_name_zh": api_name_zh(normalize_gold_api(item)[1]),
            }
            for item in gold_apis_raw
        ],
        "metadata": meta,
    }
    data["search_blob"] = make_search_blob(
        [
            data["review_id"],
            data["task_id"],
            data["task_type"],
            data["source_group"],
            data["query_text"],
            data["query_text_zh"],
            enriched_services,
            enriched_apis,
            gold_services,
            data["gold_services_zh"],
            gold_apis_raw,
            data["gold_apis_zh"],
        ]
    )
    return data


def build_review_data(
    multi_service_rows: Sequence[Dict[str, str]],
    multi_api_rows: Sequence[Dict[str, str]],
) -> List[Dict[str, Any]]:
    review_data: List[Dict[str, Any]] = []
    counter = 1
    for row in multi_service_rows:
        review_data.append(enrich_row(row, f"R{counter:03d}"))
        counter += 1
    for row in multi_api_rows:
        review_data.append(enrich_row(row, f"R{counter:03d}"))
        counter += 1
    return review_data


def counts(rows: Sequence[Dict[str, Any]], field: str) -> Dict[str, int]:
    return dict(Counter(str(row.get(field, "")) or "<empty>" for row in rows))


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Main Four Tasks Review App v0.2</title>
  <style>
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
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }
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
    h1 {
      margin: 0 0 6px;
      font-size: 20px;
      line-height: 1.25;
    }
    .subtitle {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      max-width: 1040px;
    }
    .guide-panel {
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 8px 10px;
      max-width: 1040px;
    }
    .guide-panel summary {
      cursor: pointer;
      font-weight: 700;
      color: #173b7a;
      font-size: 13px;
    }
    .guide-panel ul {
      margin: 8px 0 0;
      padding-left: 20px;
      color: #344155;
      font-size: 13px;
      line-height: 1.5;
    }
    .progress {
      display: grid;
      grid-template-columns: repeat(5, minmax(94px, auto));
      gap: 8px;
      justify-content: end;
    }
    .stat {
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 6px;
      padding: 8px 10px;
      min-width: 94px;
    }
    .stat b {
      display: block;
      font-size: 18px;
      line-height: 1.15;
    }
    .stat span {
      color: var(--muted);
      font-size: 12px;
    }
    .filters {
      padding: 0 18px 14px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .filters button, .toolbar button {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
    }
    .filters button.active {
      border-color: var(--focus);
      background: var(--blue-soft);
      color: #143b82;
      font-weight: 700;
    }
    .search {
      min-width: 260px;
      flex: 1;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      background: white;
    }
    .layout {
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
      min-height: 0;
    }
    aside {
      border-right: 1px solid var(--line);
      background: #fbfcfd;
      overflow: auto;
      height: calc(100vh - 162px);
    }
    .list-item {
      display: block;
      width: 100%;
      text-align: left;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: transparent;
      padding: 12px 14px;
      cursor: pointer;
    }
    .list-item:hover { background: var(--soft); }
    .list-item.active {
      background: var(--blue-soft);
      border-left: 4px solid var(--focus);
      padding-left: 10px;
    }
    .list-main {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      font-size: 13px;
      font-weight: 700;
    }
    .list-meta {
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .decision-pill {
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 3px 7px;
      font-size: 11px;
      font-weight: 700;
      background: var(--soft);
      color: var(--muted);
    }
    .decision-pill.keep { background: var(--green-soft); color: var(--green); }
    .decision-pill.uncertain { background: var(--amber-soft); color: var(--amber); }
    .decision-pill.remove { background: var(--red-soft); color: var(--red); }
    main {
      overflow: auto;
      height: calc(100vh - 162px);
      padding: 18px;
    }
    .empty {
      padding: 32px;
      color: var(--muted);
      text-align: center;
    }
    .section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
      box-shadow: 0 4px 14px rgba(20, 28, 38, 0.04);
    }
    .section h2 {
      margin: 0 0 12px;
      font-size: 16px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(160px, 1fr));
      gap: 8px;
    }
    .field {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 8px;
      min-width: 0;
    }
    .field span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      margin-bottom: 4px;
    }
    .field b {
      display: block;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .query {
      white-space: pre-wrap;
      line-height: 1.55;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .items {
      display: grid;
      gap: 8px;
    }
    .item {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fbfcfd;
    }
    .item.gold {
      border-color: #9ac5a9;
      background: var(--green-soft);
    }
    .item-title {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      font-weight: 700;
      margin-bottom: 5px;
    }
    .tag {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 700;
      background: var(--soft);
      color: var(--muted);
    }
    .tag.gold { background: #ccebd8; color: var(--green); }
    .description {
      color: #344155;
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .translation-box {
      margin-top: 8px;
      border: 1px solid #cfd9ea;
      border-left: 4px solid var(--focus);
      border-radius: 6px;
      background: #f6f9ff;
      padding: 8px 10px;
    }
    .translation-box.compact {
      background: #fbfdff;
    }
    .translation-title {
      color: #173b7a;
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 4px;
    }
    .translation-body {
      color: #27364c;
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .translation-note {
      margin-top: 4px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
    }
    .hierarchy {
      display: grid;
      gap: 10px;
    }
    .hierarchy-service {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 10px;
    }
    .hierarchy-service.gold {
      border-color: #9ac5a9;
      background: var(--green-soft);
    }
    .hierarchy-title {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      font-weight: 700;
      margin-bottom: 8px;
    }
    .api-tree {
      margin: 0;
      padding-left: 20px;
      display: grid;
      gap: 5px;
    }
    .api-tree li {
      line-height: 1.4;
      overflow-wrap: anywhere;
    }
    .warning {
      border: 1px solid #e5b4a8;
      border-left: 4px solid var(--red);
      border-radius: 6px;
      background: #fff1ee;
      color: #7a221c;
      padding: 8px 10px;
      font-size: 12px;
      line-height: 1.45;
      margin-bottom: 8px;
    }
    .rule-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(240px, 1fr));
      gap: 8px;
      margin-bottom: 10px;
    }
    .rule-line {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 8px 10px;
      font-size: 13px;
      line-height: 1.4;
    }
    .rule-line b {
      color: #172033;
    }
    .rule-notes {
      display: grid;
      gap: 6px;
    }
    .rule-note {
      border: 1px solid #cfd9ea;
      border-left: 4px solid var(--focus);
      border-radius: 6px;
      background: #f6f9ff;
      padding: 8px 10px;
      color: #27364c;
      font-size: 13px;
      line-height: 1.45;
    }
    .manual-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(180px, 1fr));
      gap: 10px;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      padding: 8px;
    }
    textarea {
      min-height: 86px;
      resize: vertical;
      grid-column: 1 / -1;
      line-height: 1.45;
    }
    .hint-box {
      border: 1px solid #ead59c;
      border-left: 4px solid var(--amber);
      border-radius: 6px;
      background: #fff9e8;
      padding: 10px 12px;
      margin-bottom: 12px;
      color: #4d3a05;
      font-size: 13px;
      line-height: 1.5;
    }
    .hint-box b {
      display: block;
      margin-bottom: 5px;
    }
    .hint-list {
      margin: 0;
      padding-left: 18px;
    }
    .field-hint {
      display: block;
      color: #52647a;
      font-size: 11px;
      font-weight: 400;
      line-height: 1.35;
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: space-between;
      align-items: center;
      margin-top: 12px;
    }
    .toolbar .group {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    button.primary {
      background: var(--focus);
      border-color: var(--focus);
      color: white;
      font-weight: 700;
    }
    button.danger {
      border-color: #d99;
      color: var(--red);
    }
    .meta-table {
      display: grid;
      grid-template-columns: repeat(3, minmax(160px, 1fr));
      gap: 8px;
    }
    .small {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    @media (max-width: 980px) {
      .topbar { grid-template-columns: 1fr; }
      .progress { justify-content: stretch; grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .layout { grid-template-columns: 1fr; }
      aside { height: auto; max-height: 280px; border-right: 0; border-bottom: 1px solid var(--line); }
      main { height: auto; }
      .grid, .manual-grid, .meta-table { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<div class="app">
  <header>
    <div class="topbar">
      <div>
        <h1>Main Four Tasks Review App v0.2</h1>
        <div class="subtitle">
          这是 dry-run 人工抽查，不是最终数据。页面为 query、候选服务和候选 API 直接显示中文译文，方便双语对照；最终仍以原文和 gold 对齐关系为准。主要判断 query 和 gold 是否对齐、candidate/gold 是否合理、是否存在泄漏；不确定就选 uncertain，不要强行 keep。该页面不会跑 full cleaning，也不会修改原始数据。填完后点击 Export decisions CSV。
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
        <div class="stat"><b id="reviewedCount">0</b><span>已审核</span></div>
        <div class="stat"><b id="keepCount">0</b><span>keep</span></div>
        <div class="stat"><b id="uncertainCount">0</b><span>uncertain</span></div>
        <div class="stat"><b id="removeCount">0</b><span>remove</span></div>
      </div>
    </div>
    <div class="filters">
      <button data-filter="all" class="active">全部</button>
      <button data-filter="multi_service_discovery">multi_service_discovery</button>
      <button data-filter="multi_api_recommendation">multi_api_recommendation</button>
      <button data-filter="unreviewed">未审核</button>
      <button data-filter="keep">keep</button>
      <button data-filter="uncertain">uncertain</button>
      <button data-filter="remove">remove</button>
      <input id="searchInput" class="search" placeholder="搜索 task_id / query_text / service name / api name">
    </div>
  </header>
  <div class="layout">
    <aside id="sampleList"></aside>
    <main id="detail"></main>
  </div>
</div>

<script id="review-data" type="application/json">__REVIEW_DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("review-data").textContent);
const STORAGE_KEY = "main_four_tasks_review_app_v0_2_decisions";
const EMPTY = "";
const commonOptions = {
  manual_semantic_alignment: [
    ["", "未填写"],
    ["semantic_alignment_ok", "semantic_alignment_ok"],
    ["semantic_alignment_uncertain", "semantic_alignment_uncertain"],
    ["semantic_mismatch_uncertain", "semantic_mismatch_uncertain"]
  ],
  manual_leak_check: [
    ["", "未填写"],
    ["no_blocking_leak", "no_blocking_leak"],
    ["api_leak_blocking", "api_leak_blocking"],
    ["service_leak_only", "service_leak_only"],
    ["leak_uncertain", "leak_uncertain"]
  ],
  manual_candidate_gold_validity: [
    ["", "未填写"],
    ["valid", "valid"],
    ["candidate_set_too_small", "candidate_set_too_small"],
    ["gold_incomplete", "gold_incomplete"],
    ["gold_wrong", "gold_wrong"],
    ["uncertain", "uncertain"]
  ],
  manual_final_decision: [
    ["", "未填写"],
    ["keep_for_cleaning_candidate", "keep_for_cleaning_candidate"],
    ["uncertain", "uncertain"],
    ["remove", "remove"]
  ]
};
const taskTypeOptions = {
  multi_service_discovery: [
    ["", "未填写"],
    ["valid_multi_service_discovery", "valid_multi_service_discovery"],
    ["should_be_multi_api", "should_be_multi_api"],
    ["should_be_single_service", "should_be_single_service"],
    ["ordinary_or_unclear", "ordinary_or_unclear"],
    ["not_eligible", "not_eligible"]
  ],
  multi_api_recommendation: [
    ["", "未填写"],
    ["valid_multi_api_recommendation", "valid_multi_api_recommendation"],
    ["should_be_multi_service", "should_be_multi_service"],
    ["should_be_single_api", "should_be_single_api"],
    ["ordinary_or_unclear", "ordinary_or_unclear"],
    ["not_eligible", "not_eligible"]
  ]
};

let decisions = loadDecisions();
let currentFilter = "all";
let searchText = "";
let filtered = DATA.slice();
let currentIndex = 0;

function loadDecisions() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    return {};
  }
}
function saveDecisions() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions));
}
function blankDecision() {
  return {
    manual_semantic_alignment: "",
    manual_leak_check: "",
    manual_candidate_gold_validity: "",
    manual_task_type_check: "",
    manual_final_decision: "",
    manual_decision_reason: ""
  };
}
function getDecision(reviewId) {
  return Object.assign(blankDecision(), decisions[reviewId] || {});
}
function setDecision(reviewId, patch) {
  decisions[reviewId] = Object.assign(getDecision(reviewId), patch);
  saveDecisions();
  render();
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[char]));
}
function truncate(value, limit) {
  const text = String(value ?? "").trim();
  return text.length > limit ? text.slice(0, limit) + "..." : text;
}
function translationBox(zhText, kind) {
  return `
    <div class="translation-box ${kind === "query" ? "" : "compact"}">
      <div class="translation-title">中文译文</div>
      <div class="translation-body">${escapeHtml(zhText || "暂无中文译文")}</div>
      <div class="translation-note">提示：译文仅用于双语对照，审核结论仍以原文、candidate 和 gold 的对应关系为准。</div>
    </div>
  `;
}
function decisionClass(value) {
  if (value === "keep_for_cleaning_candidate") return "keep";
  if (value === "uncertain") return "uncertain";
  if (value === "remove") return "remove";
  return "";
}
function decisionLabel(value) {
  if (value === "keep_for_cleaning_candidate") return "keep";
  if (value === "uncertain") return "uncertain";
  if (value === "remove") return "remove";
  return "未填写";
}
function applyFilters() {
  const needle = searchText.trim().toLowerCase();
  filtered = DATA.filter(item => {
    const decision = getDecision(item.review_id);
    let passFilter = true;
    if (currentFilter === "multi_service_discovery" || currentFilter === "multi_api_recommendation") {
      passFilter = item.task_type === currentFilter;
    } else if (currentFilter === "unreviewed") {
      passFilter = !decision.manual_final_decision;
    } else if (currentFilter === "keep") {
      passFilter = decision.manual_final_decision === "keep_for_cleaning_candidate";
    } else if (currentFilter === "uncertain") {
      passFilter = decision.manual_final_decision === "uncertain";
    } else if (currentFilter === "remove") {
      passFilter = decision.manual_final_decision === "remove";
    }
    const passSearch = !needle || item.search_blob.includes(needle);
    return passFilter && passSearch;
  });
  if (currentIndex >= filtered.length) currentIndex = Math.max(0, filtered.length - 1);
}
function updateStats() {
  const finalValues = DATA.map(item => getDecision(item.review_id).manual_final_decision);
  document.getElementById("totalCount").textContent = DATA.length;
  document.getElementById("reviewedCount").textContent = finalValues.filter(Boolean).length;
  document.getElementById("keepCount").textContent = finalValues.filter(v => v === "keep_for_cleaning_candidate").length;
  document.getElementById("uncertainCount").textContent = finalValues.filter(v => v === "uncertain").length;
  document.getElementById("removeCount").textContent = finalValues.filter(v => v === "remove").length;
}
function renderList() {
  const list = document.getElementById("sampleList");
  if (!filtered.length) {
    list.innerHTML = '<div class="empty">没有符合当前筛选的样本。</div>';
    return;
  }
  list.innerHTML = filtered.map((item, index) => {
    const decision = getDecision(item.review_id).manual_final_decision;
    const active = index === currentIndex ? " active" : "";
    return `
      <button class="list-item${active}" data-index="${index}">
        <div class="list-main">
          <span>${escapeHtml(item.review_id)} · ${escapeHtml(item.task_id)}</span>
          <span class="decision-pill ${decisionClass(decision)}">${escapeHtml(decisionLabel(decision))}</span>
        </div>
        <div class="list-meta">${escapeHtml(item.task_type)} · ${escapeHtml(item.source_group)} · ${escapeHtml(item.leak_status)}</div>
      </button>
    `;
  }).join("");
  list.querySelectorAll(".list-item").forEach(button => {
    button.addEventListener("click", () => {
      currentIndex = Number(button.dataset.index);
      render();
    });
  });
}
function infoField(label, value) {
  return `<div class="field"><span>${escapeHtml(label)}</span><b>${escapeHtml(value || "")}</b></div>`;
}
function renderBasic(item) {
  return `
    <section class="section">
      <h2>A. 基础信息</h2>
      <div class="grid">
        ${infoField("review_id", item.review_id)}
        ${infoField("task_id", item.task_id)}
        ${infoField("task_type", item.task_type)}
        ${infoField("source_dataset", item.source_dataset)}
        ${infoField("source_group", item.source_group)}
        ${infoField("leak_status", item.leak_status)}
        ${infoField("semantic_alignment_status", item.semantic_alignment_status)}
        ${infoField("cleaning_status", item.cleaning_status)}
        ${infoField("task_eligibility", item.task_eligibility)}
        ${infoField("task_bucket", item.task_bucket)}
        ${infoField("split", item.split)}
      </div>
    </section>
  `;
}
function renderServices(item) {
  const goldSet = new Set(item.gold_services || []);
  const services = item.candidate_services || [];
  const html = services.length ? services.map(service => {
    const isGold = service.is_gold_service || goldSet.has(service.service_name);
    return `
      <div class="item ${isGold ? "gold" : ""}">
        <div class="item-title">
          <span>${escapeHtml(service.service_name || "(unnamed service)")}</span>
          ${isGold ? '<span class="tag gold">[GOLD]</span>' : ""}
          ${service.category_name ? `<span class="tag">${escapeHtml(service.category_name)}</span>` : ""}
        </div>
        <div class="description">${escapeHtml(truncate(service.service_description || "", 250))}</div>
        ${translationBox(`${service.service_name_zh || ""}。${service.service_description_zh || ""}`, "service")}
      </div>
    `;
  }).join("") : '<div class="small">无 candidate services。</div>';
  return `<section class="section"><h2>C. Candidate Services</h2><div class="items">${html}</div></section>`;
}
function renderGoldServices(item) {
  const goldServices = item.gold_services_zh || (item.gold_services || []).map(name => ({service_name: name, service_name_zh: name}));
  const html = goldServices.length
    ? goldServices.map(service => `
      <div class="item gold">
        <div class="item-title">
          <span>${escapeHtml(service.service_name)}</span>
          <span class="tag gold">[GOLD]</span>
        </div>
        ${translationBox(service.service_name_zh, "service")}
      </div>
    `).join("")
    : '<span class="small">无 gold services。</span>';
  return `<section class="section"><h2>D. Gold Services</h2><div class="items">${html}</div></section>`;
}
function renderApis(item) {
  const apis = item.candidate_apis || [];
  const html = apis.length ? apis.map(api => {
    const isGold = Boolean(api.is_gold_api);
    return `
      <div class="item ${isGold ? "gold" : ""}">
        <div class="item-title">
          <span>${escapeHtml(api.api_name || "(unnamed api)")}</span>
          ${isGold ? '<span class="tag gold">[GOLD_API]</span>' : ""}
          ${api.service_name ? `<span class="tag">${escapeHtml(api.service_name)}</span>` : ""}
        </div>
        <div class="description">${escapeHtml(truncate(api.api_description || "", 200))}</div>
        ${translationBox(`${api.service_name_zh || ""}。${api.api_name_zh || ""}。${api.api_description_zh || ""}`, "api")}
      </div>
    `;
  }).join("") : '<div class="small">无 candidate APIs。</div>';
  return `<section class="section"><h2>E. Candidate APIs</h2><div class="items">${html}</div></section>`;
}
function renderGoldApis(item) {
  const apis = item.gold_apis_zh || [];
  const html = apis.length ? apis.map(api => {
    const name = api.api_name || "";
    const service = api.service_name || "";
    return `
      <div class="item gold">
        <div class="item-title">
          <span>${escapeHtml(name || "(unnamed api)")}</span>
          <span class="tag gold">[GOLD_API]</span>
          ${service ? `<span class="tag">${escapeHtml(service)}</span>` : ""}
        </div>
        ${translationBox(`${api.service_name_zh || ""}。${api.api_name_zh || ""}`, "api")}
      </div>
    `;
  }).join("") : '<div class="small">无 gold APIs。</div>';
  return `<section class="section"><h2>F. Gold APIs</h2><div class="items">${html}</div></section>`;
}
function renderHierarchyView(item) {
  const services = item.candidate_services || [];
  const apis = item.candidate_apis || [];
  const serviceNames = new Set(services.map(service => service.service_name || ""));
  const apisByService = new Map();
  apis.forEach(api => {
    const serviceName = api.service_name || "(missing service_name)";
    if (!apisByService.has(serviceName)) apisByService.set(serviceName, []);
    apisByService.get(serviceName).push(api);
  });
  const warnings = Array.from(new Set(
    apis
      .filter(api => api.service_name && !serviceNames.has(api.service_name))
      .map(api => api.service_name)
  ));
  const warningHtml = warnings.length
    ? `<div class="warning">WARNING: API service name not found in candidate_services_json: ${escapeHtml(warnings.join(", "))}</div>`
    : "";
  const serviceBlocks = services.map(service => {
    const serviceName = service.service_name || "(unnamed service)";
    const serviceApis = apisByService.get(serviceName) || [];
    const isGoldService = Boolean(service.is_gold_service);
    const apiList = serviceApis.length
      ? serviceApis.map(api => `
          <li>
            API: ${escapeHtml(api.api_name || "(unnamed api)")}
            ${api.api_name_zh ? `<span class="small"> / ${escapeHtml(api.api_name_zh)}</span>` : ""}
            ${api.is_gold_api ? '<span class="tag gold">[GOLD_API]</span>' : ""}
          </li>
        `).join("")
      : '<li class="small">No candidate APIs listed under this service.</li>';
    return `
      <div class="hierarchy-service ${isGoldService ? "gold" : ""}">
        <div class="hierarchy-title">
          <span>Service: ${escapeHtml(serviceName)}</span>
          ${service.service_name_zh ? `<span class="small">/ ${escapeHtml(service.service_name_zh)}</span>` : ""}
          ${isGoldService ? '<span class="tag gold">[GOLD_SERVICE]</span>' : ""}
        </div>
        <ul class="api-tree">${apiList}</ul>
      </div>
    `;
  }).join("");
  const orphanBlocks = warnings.map(serviceName => {
    const serviceApis = apisByService.get(serviceName) || [];
    return `
      <div class="hierarchy-service">
        <div class="hierarchy-title">
          <span>Service: ${escapeHtml(serviceName)}</span>
          <span class="tag">API-only service name</span>
        </div>
        <ul class="api-tree">
          ${serviceApis.map(api => `
            <li>
              API: ${escapeHtml(api.api_name || "(unnamed api)")}
              ${api.api_name_zh ? `<span class="small"> / ${escapeHtml(api.api_name_zh)}</span>` : ""}
              ${api.is_gold_api ? '<span class="tag gold">[GOLD_API]</span>' : ""}
            </li>
          `).join("")}
        </ul>
      </div>
    `;
  }).join("");
  return `
    <section class="section">
      <h2>Service/API Hierarchy View</h2>
      <div class="small">按 service 分组展示 candidate APIs。这个视图帮助判断当前样本更像 service-level 还是 API-level，不会自动填写人工判断。</div>
      ${warningHtml}
      <div class="hierarchy">${serviceBlocks || '<div class="small">No candidate services.</div>'}${orphanBlocks}</div>
    </section>
  `;
}
function numberValue(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
function boolText(value) {
  return value ? "yes" : "no";
}
function goldApiServiceDistribution(item) {
  const counts = {};
  (item.gold_apis_zh || []).forEach(api => {
    const service = api.service_name || "(missing service_name)";
    counts[service] = (counts[service] || 0) + 1;
  });
  return counts;
}
function mechanicalLevelHint(item, counts) {
  const goldApiByService = goldApiServiceDistribution(item);
  const hasMultipleGoldApisSameService = Object.values(goldApiByService).some(value => value > 1);
  if (item.task_type.includes("api")) return "当前 task_type 是 API-level；人工应重点判断 gold APIs 是否覆盖 query 的具体操作。";
  if (item.task_type.includes("service")) {
    if (hasMultipleGoldApisSameService) return "当前 task_type 是 service-level，但同一服务下有多个 gold API；请检查是否其实更像 API-level。";
    return "当前 task_type 是 service-level；人工应重点判断 gold services 是否覆盖 query 的大能力。";
  }
  if (counts.goldApiCount > counts.goldServiceCount || hasMultipleGoldApisSameService) return "机械提示：更像 API-level，因为 gold API 数多于 gold service 数，或同一服务下有多个 gold API。";
  return "机械提示：更像 service-level，但仍需人工核对 query/gold 语义。";
}
function renderRuleBasedHints(item) {
  const meta = item.metadata || {};
  const counts = {
    candidateServiceCount: numberValue(meta.candidate_service_count),
    goldServiceCount: numberValue(meta.gold_service_count),
    candidateApiCount: numberValue(meta.candidate_api_count),
    goldApiCount: numberValue(meta.gold_api_count),
    queryMentionsGoldApi: numberValue(meta.query_mentions_any_gold_api),
    queryMentionsGoldService: numberValue(meta.query_mentions_any_gold_service)
  };
  const serviceChoiceSpace = counts.candidateServiceCount > counts.goldServiceCount;
  const apiChoiceSpace = counts.candidateApiCount > counts.goldApiCount;
  const goldApiByService = goldApiServiceDistribution(item);
  const hasMultipleGoldApisSameService = Object.values(goldApiByService).some(value => value > 1);
  const notes = [];
  if (!serviceChoiceSpace) notes.push("Hint: candidate_service_count <= gold_service_count, service-level discovery may lack real choice space.");
  if (!apiChoiceSpace) notes.push("Hint: candidate_api_count <= gold_api_count, API-level recommendation may lack real choice space.");
  if (counts.queryMentionsGoldApi === 1) notes.push("Hint: query_mentions_any_gold_api = 1, possible blocking API leak.");
  if (counts.queryMentionsGoldService === 1) notes.push("Hint: query_mentions_any_gold_service = 1, possible service leak.");
  if (hasMultipleGoldApisSameService) notes.push("Hint: multiple gold APIs under same service, this may be API-level rather than service-level.");
  if (counts.candidateServiceCount === 1) notes.push("Hint: candidate_service_count = 1, usually weak for service discovery.");
  notes.push(`Hint: ${mechanicalLevelHint(item, counts)}`);
  return `
    <section class="section">
      <h2>Rule-based Hints</h2>
      <div class="small">这些是字段和计数的机械提示，只辅助人工检查，不会自动填写人工判断。</div>
      <div class="rule-grid">
        <div class="rule-line"><b>candidate_service_count:</b> ${counts.candidateServiceCount}</div>
        <div class="rule-line"><b>gold_service_count:</b> ${counts.goldServiceCount}</div>
        <div class="rule-line"><b>candidate_api_count:</b> ${counts.candidateApiCount}</div>
        <div class="rule-line"><b>gold_api_count:</b> ${counts.goldApiCount}</div>
        <div class="rule-line"><b>candidate_service_count > gold_service_count:</b> ${boolText(serviceChoiceSpace)}</div>
        <div class="rule-line"><b>candidate_api_count > gold_api_count:</b> ${boolText(apiChoiceSpace)}</div>
        <div class="rule-line"><b>query_mentions_any_gold_api:</b> ${counts.queryMentionsGoldApi}</div>
        <div class="rule-line"><b>query_mentions_any_gold_service:</b> ${counts.queryMentionsGoldService}</div>
      </div>
      <div class="rule-notes">${notes.map(note => `<div class="rule-note">${escapeHtml(note)}</div>`).join("")}</div>
    </section>
  `;
}
function renderMetadata(item) {
  const meta = item.metadata || {};
  const fields = [
    "candidate_service_count",
    "candidate_api_count",
    "gold_service_count",
    "gold_api_count",
    "query_mentions_any_gold_api",
    "query_mentions_any_gold_service"
  ];
  return `
    <section class="section">
      <h2>G. Metadata</h2>
      <div class="meta-table">
        ${fields.map(field => infoField(field, meta[field] ?? "")).join("")}
      </div>
    </section>
  `;
}
const manualHints = {
  manual_semantic_alignment: "看 query 需要的能力是否被 gold service/API 覆盖。完全对齐选 ok；只覆盖一部分或看不懂选 uncertain；明显不匹配选 mismatch。",
  manual_leak_check: "看 query 是否直接暴露 gold API 名或 gold service 名。API 名直接出现通常 blocking；只出现服务名可标 service_leak_only。",
  manual_candidate_gold_validity: "看候选集合是否足够、gold 是否非空且正确。候选太少、gold 缺失/错误/只覆盖一部分，都不要直接 keep。",
  manual_task_type_check: "判断它到底是服务层任务还是 API 层任务，是 multi 还是 single。层级不对就选 should_be_* 或 not_eligible。",
  manual_final_decision: "只有语义对齐、无阻塞 leak、candidate/gold 有效、任务类型正确时才 keep；拿不准选 uncertain；强泄漏/错配/无效选 remove。"
};
function selectHtml(field, value, options, hint = "") {
  return `
    <label>${escapeHtml(field)}
      ${hint ? `<span class="field-hint">${escapeHtml(hint)}</span>` : ""}
      <select data-manual-field="${escapeHtml(field)}">
        ${options.map(([optionValue, label]) => `<option value="${escapeHtml(optionValue)}" ${optionValue === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}
      </select>
    </label>
  `;
}
function renderManual(item) {
  const decision = getDecision(item.review_id);
  const taskOptions = taskTypeOptions[item.task_type] || [["", "未填写"]];
  return `
    <section class="section">
      <h2>人工判断</h2>
      <div class="hint-box">
        <b>审核顺序</b>
        <ul class="hint-list">
          <li>query 真正要完成什么？</li>
          <li>需要几个 service？</li>
          <li>gold services 是否覆盖 query 的主要需求？</li>
          <li>candidate services 是否有真实选择空间？</li>
          <li>需要几个具体 API？</li>
          <li>gold APIs 是否覆盖 query 的具体操作？</li>
          <li>query 是否直接泄露 gold service/API 名？</li>
          <li>不确定就选 uncertain，别为了凑 clean 强行通过。</li>
        </ul>
      </div>
      <div class="manual-grid">
        ${selectHtml("manual_semantic_alignment", decision.manual_semantic_alignment, commonOptions.manual_semantic_alignment, manualHints.manual_semantic_alignment)}
        ${selectHtml("manual_leak_check", decision.manual_leak_check, commonOptions.manual_leak_check, manualHints.manual_leak_check)}
        ${selectHtml("manual_candidate_gold_validity", decision.manual_candidate_gold_validity, commonOptions.manual_candidate_gold_validity, manualHints.manual_candidate_gold_validity)}
        ${selectHtml("manual_task_type_check", decision.manual_task_type_check, taskOptions, manualHints.manual_task_type_check)}
        ${selectHtml("manual_final_decision", decision.manual_final_decision, commonOptions.manual_final_decision, manualHints.manual_final_decision)}
        <label>manual_decision_reason
          <span class="field-hint">写一句理由：为什么 keep / uncertain / remove。后面验证正式清洗脚本时会用这个理由定位规则问题。</span>
          <textarea data-manual-field="manual_decision_reason" placeholder="填写人工判断理由">${escapeHtml(decision.manual_decision_reason)}</textarea>
        </label>
      </div>
      <div class="toolbar">
        <div class="group">
          <button id="prevBtn">上一条</button>
          <button id="nextBtn">下一条</button>
        </div>
        <div class="group">
          <button id="clearCurrentBtn">清空当前样本</button>
          <button id="clearAllBtn" class="danger">清空全部人工判断</button>
          <button id="exportBtn" class="primary">Export decisions CSV</button>
        </div>
      </div>
    </section>
  `;
}
function renderDetail() {
  const detail = document.getElementById("detail");
  if (!filtered.length) {
    detail.innerHTML = '<div class="empty">请调整筛选或搜索条件。</div>';
    return;
  }
  const item = filtered[currentIndex];
  detail.innerHTML = `
    ${renderBasic(item)}
    <section class="section"><h2>B. 用户需求 query</h2><div class="query">${escapeHtml(item.query_text)}</div>${translationBox(item.query_text_zh, "query")}</section>
    ${renderHierarchyView(item)}
    ${renderServices(item)}
    ${renderGoldServices(item)}
    ${renderApis(item)}
    ${renderGoldApis(item)}
    ${renderMetadata(item)}
    ${renderRuleBasedHints(item)}
    ${renderManual(item)}
  `;
  detail.querySelectorAll("[data-manual-field]").forEach(control => {
    control.addEventListener("change", () => {
      setDecision(item.review_id, {[control.dataset.manualField]: control.value});
    });
    control.addEventListener("input", () => {
      if (control.tagName === "TEXTAREA") {
        decisions[item.review_id] = Object.assign(getDecision(item.review_id), {[control.dataset.manualField]: control.value});
        saveDecisions();
        updateStats();
        renderList();
      }
    });
  });
  document.getElementById("prevBtn").addEventListener("click", () => {
    currentIndex = Math.max(0, currentIndex - 1);
    render();
  });
  document.getElementById("nextBtn").addEventListener("click", () => {
    currentIndex = Math.min(filtered.length - 1, currentIndex + 1);
    render();
  });
  document.getElementById("clearCurrentBtn").addEventListener("click", () => {
    delete decisions[item.review_id];
    saveDecisions();
    render();
  });
  document.getElementById("clearAllBtn").addEventListener("click", () => {
    if (confirm("确定要清空全部人工判断吗？")) {
      decisions = {};
      saveDecisions();
      render();
    }
  });
  document.getElementById("exportBtn").addEventListener("click", exportCsv);
}
function render() {
  applyFilters();
  updateStats();
  renderList();
  renderDetail();
}
document.querySelectorAll("[data-filter]").forEach(button => {
  button.addEventListener("click", () => {
    currentFilter = button.dataset.filter;
    currentIndex = 0;
    document.querySelectorAll("[data-filter]").forEach(item => item.classList.toggle("active", item === button));
    render();
  });
});
document.getElementById("searchInput").addEventListener("input", event => {
  searchText = event.target.value;
  currentIndex = 0;
  render();
});
function csvEscape(value) {
  const text = String(value ?? "");
  return '"' + text.replace(/"/g, '""') + '"';
}
function jsonForCsv(value) {
  return JSON.stringify(value ?? "");
}
function isReviewComplete(decision) {
  return Boolean(
    decision.manual_semantic_alignment &&
    decision.manual_leak_check &&
    decision.manual_candidate_gold_validity &&
    decision.manual_task_type_check &&
    decision.manual_final_decision
  );
}
function exportCsv() {
  const fields = [
    "review_id",
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "leak_status",
    "semantic_alignment_status",
    "cleaning_status",
    "task_eligibility",
    "task_bucket",
    "query_text",
    "query_text_zh",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_services_zh_json",
    "gold_apis_json",
    "gold_apis_zh_json",
    "metadata_json",
    "manual_semantic_alignment",
    "manual_leak_check",
    "manual_candidate_gold_validity",
    "manual_task_type_check",
    "manual_final_decision",
    "manual_decision_reason",
    "review_completed"
  ];
  const lines = [fields.join(",")];
  DATA.forEach(item => {
    const decision = getDecision(item.review_id);
    const row = {
      review_id: item.review_id,
      task_id: item.task_id,
      task_type: item.task_type,
      source_dataset: item.source_dataset,
      source_group: item.source_group,
      leak_status: item.leak_status,
      semantic_alignment_status: item.semantic_alignment_status,
      cleaning_status: item.cleaning_status,
      task_eligibility: item.task_eligibility,
      task_bucket: item.task_bucket,
      query_text: item.query_text,
      query_text_zh: item.query_text_zh,
      candidate_services_json: jsonForCsv(item.candidate_services),
      candidate_apis_json: jsonForCsv(item.candidate_apis),
      gold_services_json: jsonForCsv(item.gold_services),
      gold_services_zh_json: jsonForCsv(item.gold_services_zh),
      gold_apis_json: jsonForCsv(item.gold_apis),
      gold_apis_zh_json: jsonForCsv(item.gold_apis_zh),
      metadata_json: jsonForCsv(item.metadata),
      manual_semantic_alignment: decision.manual_semantic_alignment,
      manual_leak_check: decision.manual_leak_check,
      manual_candidate_gold_validity: decision.manual_candidate_gold_validity,
      manual_task_type_check: decision.manual_task_type_check,
      manual_final_decision: decision.manual_final_decision,
      manual_decision_reason: decision.manual_decision_reason,
      review_completed: isReviewComplete(decision) ? "yes" : "no"
    };
    lines.push(fields.map(field => csvEscape(row[field])).join(","));
  });
  const blob = new Blob(["\ufeff" + lines.join("\r\n")], {type: "text/csv;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "main_four_tasks_manual_decisions_40.csv";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
render();
</script>
</body>
</html>
"""


def generate_html(review_data: Sequence[Dict[str, Any]]) -> None:
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    data_json = json.dumps(review_data, ensure_ascii=False).replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__REVIEW_DATA__", data_json)
    OUTPUT_HTML.write_text(html, encoding="utf-8")


def write_report(
    multi_service_selected: Sequence[Dict[str, str]],
    multi_api_selected: Sequence[Dict[str, str]],
    review_data: Sequence[Dict[str, Any]],
) -> None:
    OUTPUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    source_groups = counts(review_data, "source_group")
    leak_statuses = counts(review_data, "leak_status")
    content = f"""
# Main Four Tasks Review App Generation Report v0.2

## 【本次做了什么】
基于 `main_four_tasks_dryrun_v0_2` 的两个 task-level CSV，生成了一个本地可交互、可离线双击打开的单文件 HTML 审核页面。页面内嵌 40 条以内 dry-run 抽查样本，支持人工填写、localStorage 自动保存、筛选、搜索、上一条/下一条、清空和导出 decisions CSV。

本次还在页面中为 query、候选服务和候选 API 增加了直接中文译文，并在人工判断区域增加了字段级注释。中文译文用于双语对照，不会修改原始数据；审核结论仍以原文、candidate 和 gold 的对应关系为准。

本次没有跑 full cleaning，没有 baseline，没有训练模型，没有 split，没有继续 top200，也没有重新搜索 full G3。

## 【HTML 页面路径】
`{rel(OUTPUT_HTML)}`

## 【multi_service 样本数量】
{len(multi_service_selected)} 条。来源为 `multi_service_discovery_task_level.csv`，抽样时尽量覆盖不同 candidate/gold 数量、query 长度和服务组合。

## 【multi_api 样本数量】
{len(multi_api_selected)} 条。来源为 `multi_api_recommendation_task_level.csv`，抽样时优先覆盖 G1/G2、`no_obvious_leak`、`service_leak_only` 和不同 gold API 数量。

## 【为什么改用交互式 HTML】
CSV 和 Markdown 在人工审核时需要频繁横向滚动、复制、对照 JSON 字段，容易漏看 query、candidate、gold、leak 和 task type 的关系。HTML 页面把一个样本的 query、中文译文、candidate services、candidate APIs、gold、metadata 和人工判断控件放在同一页，更适合逐条审阅。

## 【人工如何使用】
双击打开 HTML 文件。左侧选择样本，右侧阅读 query 原文、中文译文、candidate/gold 和 metadata，在底部按照注释填写人工判断。页面会自动保存到浏览器 localStorage，刷新不会丢失。填完后点击 `Export decisions CSV`。

## 【导出的 decisions CSV 用来做什么】
导出的 `main_four_tasks_manual_decisions_40.csv` 后续用于验证正式清洗脚本。新版导出 CSV 不只包含人工判断，还包含 task 基础字段、query 原文/中文译文、candidate services/APIs JSON、gold services/APIs JSON、metadata 和 `review_completed`。后续会用它检查脚本建议与人工判断是否一致，尤其是 semantic mismatch、blocking leak、candidate/gold 无效样本是否被正确 fail-closed 到 uncertain/remove，而不是进入 clean。

## 【是否建议现在 full cleaning】
不建议现在 full cleaning。应先人工用 HTML 页面审核样本，再用导出的 decisions CSV 校准和验证正式清洗脚本。

## 【当前 single_service / single_api 怎么处理】
当前 `single_service_discovery` 和 `single_api_recommendation` 暂不处理。它们需要后续 full G1 或 MetaTool / ShortcutsBench 补强，不能在当前 dry-run 里硬凑。

## 【覆盖情况摘要】
```json
{json.dumps({"total_review_samples": len(review_data), "source_group_distribution": source_groups, "leak_status_distribution": leak_statuses}, ensure_ascii=False, indent=2)}
```
"""
    OUTPUT_REPORT.write_text(content.strip() + "\n", encoding="utf-8")


def write_hierarchy_update_report() -> None:
    HIERARCHY_UPDATE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    content = f"""
# Main Four Tasks Review App Service/API Hierarchy Update Report

## 【本次改了什么】
在 `main_four_tasks_review_app_40.html` 中新增了三个审核辅助能力：

- 顶部可折叠说明区：`怎么区分 service-level 和 API-level`。
- 每条样本详情里的 `Service/API Hierarchy View`，按 service 分组展示 candidate APIs，并标记 `[GOLD_SERVICE]` 和 `[GOLD_API]`。
- 每条样本详情里的 `Rule-based Hints`，根据字段和计数给出只读机械提示。

同时保留原有功能：上一条/下一条、左侧样本列表、筛选、搜索、localStorage 自动保存、Export decisions CSV、清空当前样本、清空全部人工判断。

## 【为什么要增加 Service/API 层级树】
人工审核时如果只看 JSON，很难快速分清 service 和 API 的层级关系。层级树把候选 API 按所属 service 分组，让人工能直接看到：

- 一个 query 涉及哪些候选服务。
- 每个服务下面有哪些候选 API。
- gold service 和 gold API 是否在合理的服务/API 层级里。
- 是否存在 API 的 `service_name` 在 `candidate_services_json` 里找不到的情况。

如果发现 service/API 边界不清，应选择 `uncertain`，不要强行 keep。

## 【新增了哪些 rule-based hints】
页面新增的 hints 包括：

- `candidate_service_count`
- `gold_service_count`
- `candidate_api_count`
- `gold_api_count`
- `candidate_service_count > gold_service_count`
- `candidate_api_count > gold_api_count`
- `query_mentions_any_gold_api`
- `query_mentions_any_gold_service`
- 当前任务更像 service-level 还是 API-level 的机械提示
- `candidate_service_count <= gold_service_count` 时提示 service-level discovery 可能缺少真实选择空间
- `query_mentions_any_gold_api = 1` 时提示 possible blocking API leak
- 同一 service 下多个 gold API 时提示可能更像 API-level

这些都是机械提示，不代表最终标签。

## 【人工如何根据层级树判断 service-level / API-level】
先看 query 真正需要哪些“大能力”。如果要判断的是应该选哪些工具或服务，就是 service-level。再看 gold services 是否覆盖 query 的主要需求，以及 candidate services 是否有真实选择空间。

然后看每个 gold service 下面的 gold APIs。如果任务重点是从候选 API 中选择具体接口，或者同一个服务下面出现多个需要选择的 gold APIs，就更像 API-level。此时应重点判断 gold APIs 是否覆盖 query 的具体操作。

如果 candidate service 只有一个，通常不适合作为 service discovery 主任务。如果 query 直接出现 gold API 名，通常是 API leak 风险。

## 【是否自动填写人工判断】
不会。页面只提供辅助提示，不自动做最终判断，不会自动填写任何 `manual_*` 字段。

## 【是否建议现在 full cleaning】
不建议现在 full cleaning。当前仍应先完成 HTML 页面人工审核，再用导出的 decisions CSV 校准和验证正式清洗脚本。

## 【输出文件】
- HTML: `{rel(OUTPUT_HTML)}`
- Backup: `{rel(BACKUP_HTML)}`
- Report: `{rel(HIERARCHY_UPDATE_REPORT)}`
- Archive: `{rel(ARCHIVE_ROOT)}`
"""
    HIERARCHY_UPDATE_REPORT.write_text(content.strip() + "\n", encoding="utf-8")


def archive_outputs(paths: Iterable[Path]) -> None:
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if not path.exists():
            continue
        target = ARCHIVE_ROOT / path.relative_to(PROJECT_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main() -> int:
    missing = check_inputs()
    if missing:
        print("Missing required input files:")
        for item in missing:
            print(f"- {item}")
        return 1

    multi_service_all = read_csv_rows(REQUIRED_INPUTS["multi_service"])
    multi_api_all = read_csv_rows(REQUIRED_INPUTS["multi_api"])
    multi_service_selected = sample_multi_service(multi_service_all)
    multi_api_selected = sample_multi_api(multi_api_all)
    review_data = build_review_data(multi_service_selected, multi_api_selected)

    generate_html(review_data)
    write_report(multi_service_selected, multi_api_selected, review_data)
    write_hierarchy_update_report()
    archive_outputs(
        [
            OUTPUT_HTML,
            BACKUP_HTML,
            OUTPUT_REPORT,
            HIERARCHY_UPDATE_REPORT,
            Path(__file__).resolve(),
        ]
    )

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "html": rel(OUTPUT_HTML),
        "backup_html": rel(BACKUP_HTML),
        "report": rel(OUTPUT_REPORT),
        "hierarchy_update_report": rel(HIERARCHY_UPDATE_REPORT),
        "archive_root": rel(ARCHIVE_ROOT),
        "total_samples": len(review_data),
        "multi_service_samples": len(multi_service_selected),
        "multi_api_samples": len(multi_api_selected),
        "source_group_distribution": counts(review_data, "source_group"),
        "leak_status_distribution": counts(review_data, "leak_status"),
        "scope_guard": {
            "full_cleaning": False,
            "baseline": False,
            "model_training": False,
            "split": False,
            "top200_continuation": False,
            "full_g3_search": False,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
