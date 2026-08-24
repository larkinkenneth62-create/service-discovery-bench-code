#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate manual-check package for main four task dry-run v0.2.

This script only prepares human audit artifacts. It does not perform full
cleaning, baseline evaluation, model training, train/dev/test split, or any
new full-G3 search.
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

INPUT_FILES = {
    "multi_service": PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_dryrun_v0_2"
    / "multi_service_discovery_task_level.csv",
    "multi_api": PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_dryrun_v0_2"
    / "multi_api_recommendation_task_level.csv",
    "summary": PROJECT_ROOT
    / "outputs"
    / "main_four_tasks_dryrun_v0_2"
    / "main_four_tasks_dryrun_summary.json",
    "rule_v3_3": PROJECT_ROOT / "docs" / "phase1" / "manual_audit_rule_v3_3.md",
    "dryrun_report": PROJECT_ROOT
    / "docs"
    / "phase1"
    / "main_four_tasks_dryrun_v0_2_report.md",
    "source_viability": PROJECT_ROOT
    / "docs"
    / "phase1"
    / "main_four_tasks_source_viability_report.md",
    "schema_draft": PROJECT_ROOT
    / "docs"
    / "phase1"
    / "service_discovery_bench_v0_2_schema_draft.md",
}

OUTPUT_DIR = PROJECT_ROOT / "outputs" / "main_four_tasks_manual_check_v0_2"
DOC_DIR = PROJECT_ROOT / "docs" / "phase1"
ARCHIVE_ROOT = (
    PROJECT_ROOT
    / "outputs"
    / "run_archives"
    / "2026-06-24_main_four_tasks_manual_check_package_v0_2"
)

ORIGINAL_FIELDS = [
    "task_id",
    "task_type",
    "source_dataset",
    "source_group",
    "query_text",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "leak_status",
    "semantic_alignment_status",
    "cleaning_status",
    "task_eligibility",
    "task_bucket",
    "split",
    "metadata_json",
]

MANUAL_FIELDS = [
    "manual_semantic_alignment",
    "manual_leak_check",
    "manual_candidate_gold_validity",
    "manual_task_type_check",
    "manual_final_decision",
    "manual_decision_reason",
]

MULTI_SERVICE_TASK_TYPE_VALUES = [
    "valid_multi_service_discovery",
    "should_be_multi_api",
    "should_be_single_service",
    "ordinary_or_unclear",
    "not_eligible",
]

MULTI_API_TASK_TYPE_VALUES = [
    "valid_multi_api_recommendation",
    "should_be_multi_service",
    "should_be_single_api",
    "ordinary_or_unclear",
    "not_eligible",
]


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def check_inputs() -> List[str]:
    missing = [rel(path) for path in INPUT_FILES.values() if not path.exists()]
    return missing


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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
    words = (query or "").split()
    n = len(words)
    if n < 35:
        return "short"
    if n < 75:
        return "medium"
    return "long"


def service_signature(row: Dict[str, str]) -> str:
    gold = try_load_json(row.get("gold_services_json", ""))
    if isinstance(gold, list):
        return " | ".join(str(x) for x in gold[:3])
    return ""


def task_id_sort_key(row: Dict[str, str]) -> Tuple[str, int]:
    task_id = row.get("task_id", "")
    tail = task_id.rsplit("_", 1)[-1]
    return task_id, int(tail) if tail.isdigit() else 0


def add_manual_columns(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    output = []
    for row in rows:
        clean_row = {field: row.get(field, "") for field in ORIGINAL_FIELDS}
        for field in MANUAL_FIELDS:
            clean_row[field] = ""
        output.append(clean_row)
    return output


def choose_diverse(
    rows: Sequence[Dict[str, str]],
    max_rows: int,
    key_fn,
    mandatory_filters: Sequence[Tuple[str, Any]] | None = None,
) -> List[Dict[str, str]]:
    selected: List[Dict[str, str]] = []
    selected_ids = set()

    def add(row: Dict[str, str]) -> None:
        row_id = row.get("task_id", "")
        if row_id and row_id not in selected_ids and len(selected) < max_rows:
            selected.append(row)
            selected_ids.add(row_id)

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
        key=lambda r: (
            get_count(r, "gold_service_count", "gold_services_json"),
            get_count(r, "candidate_service_count", "candidate_services_json"),
            query_length_bucket(r.get("query_text", "")),
            service_signature(r),
            task_id_sort_key(r),
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
        key=lambda r: (
            r.get("source_group", ""),
            r.get("leak_status", ""),
            get_count(r, "gold_api_count", "gold_apis_json"),
            get_count(r, "candidate_api_count", "candidate_apis_json"),
            query_length_bucket(r.get("query_text", "")),
            task_id_sort_key(r),
        ),
    )

    mandatory = [
        ("G1", lambda r: r.get("source_group") == "G1"),
        ("G2", lambda r: r.get("source_group") == "G2"),
        (
            "no_obvious_leak",
            lambda r: r.get("leak_status") == "no_obvious_leak",
        ),
        (
            "service_leak_only",
            lambda r: r.get("leak_status") == "service_leak_only",
        ),
        (
            "G1_no_obvious",
            lambda r: r.get("source_group") == "G1"
            and r.get("leak_status") == "no_obvious_leak",
        ),
        (
            "G2_service_leak",
            lambda r: r.get("source_group") == "G2"
            and r.get("leak_status") == "service_leak_only",
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


def distribution(rows: Sequence[Dict[str, str]], field: str) -> Dict[str, int]:
    return dict(Counter(row.get(field, "") or "<empty>" for row in rows))


def count_distribution(
    rows: Sequence[Dict[str, str]], key: str, fallback_json_field: str
) -> Dict[str, int]:
    return dict(
        Counter(str(get_count(row, key, fallback_json_field)) for row in rows)
    )


def query_bucket_distribution(rows: Sequence[Dict[str, str]]) -> Dict[str, int]:
    return dict(Counter(query_length_bucket(row.get("query_text", "")) for row in rows))


def make_stats(rows: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "row_count": len(rows),
        "source_group_distribution": distribution(rows, "source_group"),
        "leak_status_distribution": distribution(rows, "leak_status"),
        "candidate_service_count_distribution": count_distribution(
            rows, "candidate_service_count", "candidate_services_json"
        ),
        "gold_service_count_distribution": count_distribution(
            rows, "gold_service_count", "gold_services_json"
        ),
        "candidate_api_count_distribution": count_distribution(
            rows, "candidate_api_count", "candidate_apis_json"
        ),
        "gold_api_count_distribution": count_distribution(
            rows, "gold_api_count", "gold_apis_json"
        ),
        "query_length_bucket_distribution": query_bucket_distribution(rows),
    }


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def fenced_json(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def generate_guideline(path: Path) -> None:
    content = f"""
# Main Four Tasks Manual Check Guideline v0.2

## 这份表在检查什么
这次只做人审抽查，不是最终数据。人工检查的目的，是验证 `multi_service_discovery` 和 `multi_api_recommendation` 这两类 dry-run 样本是否真的符合任务定义，并为后续正式清洗脚本提供校准样本。

## multi_service_discovery 怎么判断
`multi_service_discovery` 是服务层任务：模型要从候选服务里选出多个正确服务。这里的重点是“服务能力提供者”是否对，而不是具体 API 名称是否对。

可填 `manual_task_type_check`：
- `valid_multi_service_discovery`: query 需要多个服务，gold services 覆盖这些需求，候选服务里确实存在可混淆选项。
- `should_be_multi_api`: 更像是在同一批服务/API 里选多个具体 API，服务层判断不是核心。
- `should_be_single_service`: 实际只需要一个服务。
- `ordinary_or_unclear`: 需求看起来多，但服务层边界不清楚。
- `not_eligible`: query、候选、gold、leak 或语义对齐存在明显问题。

## multi_api_recommendation 怎么判断
`multi_api_recommendation` 是 API 层任务：模型要从候选 API 里选出多个正确 API。服务名可以帮助定位，但最终 gold 是具体 API。

可填 `manual_task_type_check`：
- `valid_multi_api_recommendation`: query 明确需要多个 API 能力，gold APIs 与 query 对齐。
- `should_be_multi_service`: 更像只需要选服务，不需要细到 API。
- `should_be_single_api`: 实际只需要一个 API。
- `ordinary_or_unclear`: 需求、候选或 gold 的层级不清。
- `not_eligible`: query、候选、gold、leak 或语义对齐存在明显问题。

## service 和 API 层的区别
service 是粗粒度能力提供者，例如某个天气、物流、金融服务。API 是服务下面的具体接口，例如查询天气、追踪包裹、获取股票行情。service-level 看“选哪个服务”，API-level 看“选哪个具体接口”。

## semantic alignment 怎么判断
如果 query 要的事情与 gold service/API 能力一致，就填 `semantic_alignment_ok`。如果 gold 只覆盖 query 的一部分、query 需要的内容与 gold 不匹配、或者候选/接口描述无法支撑 query，就填 `semantic_mismatch_uncertain` 或 `semantic_alignment_uncertain`。语义对齐不能完全交给脚本，因为脚本只会执行字段和规则检查，不能真正理解 query 与 gold 的含义。

## leak 怎么判断
API leak 是 query 直接暴露 gold API 名称，会让任务变成字符串匹配，通常不能进 clean 主数据。service leak 是 query 暴露 gold service 名称，service discovery 主任务要谨慎；但在 API-level 任务里，service leak 可能仍可作为分析数据或待复核数据保留。

## candidate/gold validity 怎么判断
候选集合需要包含合理干扰项，gold 必须非空且能覆盖 query。若候选太少、gold 缺失、候选 JSON 明显坏掉，或 gold 与候选不一致，填 `invalid_candidate_or_gold`。

## 为什么不能只靠行筛选
行筛选只能保证数量、字段、leak flag、候选数等机械条件，不能保证 query 和 gold 语义真的匹配，也不能判断“服务层”和“API 层”是否选对。人工抽查是后续清洗脚本的回归测试集。

## uncertain/remove 的原则
不确定样本先进 `uncertain`，不要强行放进 clean。强 API leak、明显语义错配、candidate/gold 明显无效的样本应移除或标记 not eligible。

## 可复制填写模板

### multi_service 通过
```text
manual_semantic_alignment=semantic_alignment_ok
manual_leak_check=no_blocking_leak
manual_candidate_gold_validity=valid_candidate_gold
manual_task_type_check=valid_multi_service_discovery
manual_final_decision=keep_clean_candidate
manual_decision_reason=query needs multiple services and gold services align with the requested capabilities
```

### multi_api 通过
```text
manual_semantic_alignment=semantic_alignment_ok
manual_leak_check=no_blocking_leak
manual_candidate_gold_validity=valid_candidate_gold
manual_task_type_check=valid_multi_api_recommendation
manual_final_decision=keep_clean_candidate
manual_decision_reason=query needs multiple APIs and gold APIs align with the requested operations
```

### 不确定
```text
manual_semantic_alignment=semantic_alignment_uncertain
manual_leak_check=leak_uncertain
manual_candidate_gold_validity=uncertain_candidate_gold
manual_task_type_check=ordinary_or_unclear
manual_final_decision=send_to_uncertain
manual_decision_reason=query/gold/task level needs further review before clean data
```

### 移除
```text
manual_semantic_alignment=semantic_mismatch_uncertain
manual_leak_check=api_leak_blocking
manual_candidate_gold_validity=invalid_candidate_or_gold
manual_task_type_check=not_eligible
manual_final_decision=remove
manual_decision_reason=blocking leak, semantic mismatch, or invalid candidate/gold prevents benchmark use
```

## 本指南适用范围
本指南只适用于 v0.2 dry-run 人工抽查。它不是最终清洗规则，也不会替代后续正式脚本验证。

## manual_task_type_check 可选值
- multi_service: {", ".join(MULTI_SERVICE_TASK_TYPE_VALUES)}
- multi_api: {", ".join(MULTI_API_TASK_TYPE_VALUES)}
"""
    write_text(path, content)


def generate_package_report(
    path: Path,
    multi_service_selected: Sequence[Dict[str, str]],
    multi_api_selected: Sequence[Dict[str, str]],
) -> None:
    ms_stats = make_stats(multi_service_selected)
    ma_stats = make_stats(multi_api_selected)
    content = f"""
# Main Four Tasks Manual Check Package Report v0.2

## 【本次做了什么】
基于 `outputs/main_four_tasks_dryrun_v0_2` 中已经生成的 dry-run 表，只为 `multi_service_discovery` 和 `multi_api_recommendation` 生成 20 条以内的人工抽查表。没有跑 full cleaning，没有 baseline，没有训练模型，没有 train/dev/test split，也没有继续 top200 或重新搜索 full G3。

## 【multi_service 抽查表输出多少条】
输出 {len(multi_service_selected)} 条，来源为 `multi_service_discovery_task_level.csv`。原始 dry-run 表共有 26 条，因此本次抽取 20 条以内的多样化样本。

## 【multi_api 抽查表输出多少条】
输出 {len(multi_api_selected)} 条，来源为 `multi_api_recommendation_task_level.csv`。原始 dry-run 表共有 50 条，因此本次抽取 20 条以内的多样化样本。

## 【是否覆盖不同 source_group / leak_status】
multi_service 覆盖情况：
{fenced_json({"source_group": ms_stats["source_group_distribution"], "leak_status": ms_stats["leak_status_distribution"], "query_length_bucket": ms_stats["query_length_bucket_distribution"], "gold_service_count": ms_stats["gold_service_count_distribution"], "candidate_service_count": ms_stats["candidate_service_count_distribution"]})}

multi_api 覆盖情况：
{fenced_json({"source_group": ma_stats["source_group_distribution"], "leak_status": ma_stats["leak_status_distribution"], "query_length_bucket": ma_stats["query_length_bucket_distribution"], "gold_api_count": ma_stats["gold_api_count_distribution"], "candidate_api_count": ma_stats["candidate_api_count_distribution"]})}

multi_service 当前 dry-run 全部来自 G2 且全部为 `no_obvious_leak`，所以无法在这张表内覆盖 G1 或 service_leak_only。multi_api 已覆盖 G1/G2，并覆盖 `no_obvious_leak` 与 `service_leak_only`。

## 【哪些任务目前不能抽查】
`single_service_discovery` 和 `single_api_recommendation` 当前 dry-run 输出均为 0 条，因此不能生成有效人工抽查表。

## 【为什么 single_service 和 single_api 当前不抽查】
当前 dry-run 中，G1 更像单服务内部 API 推荐，候选服务数通常为 1，不适合强行作为 `single_service_discovery`。`single_api_recommendation` 需要 exactly one gold API 且排除 API leak，但当前小样本未得到可用输出。因此现在不能为了凑表而伪造或放宽规则。

## 【下一步人工确认怎么做】
先填写两张人工抽查表中的 `manual_*` 列，重点判断：query 与 gold 是否语义对齐、leak 是否阻塞、candidate/gold 是否有效、任务层级是否正确、最终是否应进入 clean/uncertain/remove。

## 【是否建议现在 full cleaning】
不建议现在 full cleaning。应先完成人工确认，再用这些结果校准正式清洗脚本。single_service/single_api 还需要 full G1 检查或 MetaTool/ShortcutsBench 等外部来源补强。
"""
    write_text(path, content)


def generate_validation_design(path: Path) -> None:
    content = """
# Main Four Tasks Cleaning Script Validation Design v0.2

## 核心结论
正式清洗脚本不能直接保证 benchmark 正确。脚本不是 LLM，不能深度理解自然语言 query 与 gold service/API 的语义关系。脚本能可靠执行的是机械规则：字段存在性、JSON 可解析、candidate/gold 数量、leak flag、任务桶约束、空值检查、重复检查和已验证规则回归。

## 为什么脚本不能直接保证正确性
1. query 可能表达多个隐含需求，脚本无法判断 gold 是否完整覆盖。
2. service 与 API 的边界有时依赖领域语义，脚本只能依据字段和计数。
3. service leak 在 API-level 中是否阻塞，需要人工规则校准。
4. ordinary multi 与 composable 的区别依赖“后一步是否使用前一步输出”，关键词不能替代语义判断。
5. semantic alignment 必须由人工抽查或已确认规则校准，不能完全委托给脚本。

## 五层验证方案

### 1. Schema validation
检查所有输出文件列名、必需字段、JSON 字段可解析性、UTF-8 编码、task_id 唯一性、空 query、空 candidate、空 gold。任何 schema 不合格样本不能进入 clean。

### 2. Rule invariant validation
检查每个任务桶必须满足的硬约束：
- `single_service_discovery`: gold service 数 = 1，candidate service 数 > 1，不能有阻塞 leak。
- `single_api_recommendation`: gold API 数 = 1，candidate API 数 > 1，不能有 strong API leak。
- `multi_service_discovery`: gold service 数 > 1，candidate service 数 >= gold service 数，不能有阻塞 leak。
- `multi_api_recommendation`: gold API 数 > 1，candidate API 数 >= gold API 数，不能有 strong API leak。
- `service_leak_only` 不进入 clean service discovery 主数据。
- `uncertain` 不进入 clean 主数据。

### 3. Manual-label regression validation
把本次人工抽查表作为回归测试集。正式脚本对这些 task 的建议结果必须与人工 final decision 高度一致。若 strong API leak、semantic mismatch、invalid candidate/gold 被脚本放进 clean，视为严重失败。

### 4. Fail-closed policy
脚本遇到无法判断、字段异常、JSON 异常、语义状态未验证、leak 状态冲突时，默认输出 `uncertain` 或 `needs_manual_review`，不能默认 clean。宁可少放 clean，也不要把污染样本放进主 benchmark。

### 5. Audit report
每次清洗都必须输出审计报告，至少包括：各任务行数、leak 分布、semantic 状态分布、candidate/gold 数量分布、被移除原因分布、uncertain 分布、人工回归集一致率、随机抽样样例。

## 正式清洗脚本三步走

### Step 1: candidate screening
先做规则候选筛选，只输出 evidence 和建议桶，不给最终 clean 标签。这个阶段的产物用于人工审查。

### Step 2: validation on manual check set
用人工确认表验证脚本规则。重点检查 false clean：脚本是否错误地把 semantic mismatch、blocking leak、invalid candidate/gold 放进 clean。

### Step 3: formal cleaning after validation
只有当人工回归集通过后，才跑正式清洗。清洗后还要做 post-cleaning spot check，抽查 clean、uncertain、remove 三类样本。

## 通过标准建议
- schema 检查零严重错误。
- rule invariant 零严重错误。
- 人工回归集中 blocking leak / semantic mismatch / invalid candidate-gold 的召回率接近 100%。
- clean 样本人工抽查错误率可接受后，才进入后续 benchmark 构建。

## 当前阶段结论
现在还不建议 full cleaning。当前应该先完成人工抽查，拿到人工 final decision，再把这些结果固化为正式脚本的验证集。
"""
    write_text(path, content)


def generate_next_step_report(path: Path) -> None:
    content = """
# Main Four Tasks After Manual Check Package Next Step

## 1. 如果 multi_service 通过，下一步做什么？
如果人工确认大多数 `multi_service_discovery` 样本语义对齐、无阻塞 leak、candidate/gold 有效，则可以把当前规则固化为 `multi_service_discovery` 的候选筛选规则。下一步不是直接 baseline，而是写 formal cleaning script 的 screening 阶段，并用人工表做回归验证。

## 2. 如果 multi_api 通过，下一步做什么？
如果 `multi_api_recommendation` 样本通过，说明 G1/G2 可以贡献 API-level 多 API 推荐任务。下一步需要明确 `service_leak_only` 在 API-level 中的处理：可以保留为可分析桶，但是否进入 clean 需要看人工结果。

## 3. 如果 semantic mismatch 很多，怎么处理？
如果语义错配频繁出现，正式脚本必须提高 fail-closed 强度：所有语义未验证或疑似错配的样本进入 `uncertain`，不能进入 clean。还需要扩大人工抽查，找出错配来源是 query/gold 映射、候选构造，还是原始 ToolBench 标注问题。

## 4. 如果 service_leak_only 在 API-level 有争议，怎么处理？
不要直接删除，也不要直接 clean。建议分成两桶：`api_level_service_leak_review` 和 `clean_no_obvious_leak`。只有人工确认 service 名出现不会让 API 选择退化为简单匹配时，才考虑进入 API-level clean 或保留为单独分析子集。

## 5. single_service / single_api 是否应开始外部数据补强？
是，但要等 multi_service / multi_api 抽查完成后再启动。`single_service_discovery` 可能需要 full G1 以外的数据源，例如 MetaTool 或 ShortcutsBench；`single_api_recommendation` 需要从 full G1/G2 中扩大搜索，并严格排除 API leak。

## 6. 是否需要导师确认？
建议需要。尤其是三个点：service leak 在 API-level 是否可保留、single_service 是否允许引入外部数据源补强、composable seed set 是否作为单独附加任务而非四类主任务。

## 7. 如何用人工结果校准正式清洗脚本？
把人工确认表作为 regression set。脚本每次改规则后都跑一遍对比：
- 人工 clean 的样本，脚本是否仍能筛到 clean candidate。
- 人工 uncertain/remove 的样本，脚本是否 fail-closed。
- 人工认为任务层级错误的样本，脚本是否避免放入错误 task_bucket。
- 对不一致样本逐条写 mismatch reason，再决定改规则还是保留人工例外。

## 当前建议
先填写两张人工抽查表。完成后再做 suggested-vs-final 分析，更新规则到 v0.3，然后才考虑正式 cleaning script 的候选筛选版本。
"""
    write_text(path, content)


def copy_to_archive(paths: Iterable[Path]) -> None:
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if not path.exists():
            continue
        dest = ARCHIVE_ROOT / path.relative_to(PROJECT_ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def main() -> int:
    missing = check_inputs()
    if missing:
        print("Missing required input files:")
        for item in missing:
            print(f"- {item}")
        return 1

    generated_at = datetime.now().isoformat(timespec="seconds")

    multi_service_rows = read_csv_rows(INPUT_FILES["multi_service"])
    multi_api_rows = read_csv_rows(INPUT_FILES["multi_api"])

    multi_service_selected = sample_multi_service(multi_service_rows)
    multi_api_selected = sample_multi_api(multi_api_rows)

    ms_out = OUTPUT_DIR / "multi_service_discovery_manual_check_20.csv"
    ma_out = OUTPUT_DIR / "multi_api_recommendation_manual_check_20.csv"

    output_fields = ORIGINAL_FIELDS + MANUAL_FIELDS
    write_csv_rows(ms_out, add_manual_columns(multi_service_selected), output_fields)
    write_csv_rows(ma_out, add_manual_columns(multi_api_selected), output_fields)

    guideline = DOC_DIR / "main_four_tasks_manual_check_guideline_v0_2.md"
    package_report = DOC_DIR / "main_four_tasks_manual_check_package_report_v0_2.md"
    validation_design = DOC_DIR / "main_four_tasks_cleaning_script_validation_design_v0_2.md"
    next_step = DOC_DIR / "main_four_tasks_after_manual_check_package_next_step.md"

    generate_guideline(guideline)
    generate_package_report(package_report, multi_service_selected, multi_api_selected)
    generate_validation_design(validation_design)
    generate_next_step_report(next_step)

    summary = {
        "generated_at": generated_at,
        "scope_guard": {
            "full_cleaning": False,
            "baseline": False,
            "model_training": False,
            "train_dev_test_split": False,
            "top200_continuation": False,
            "full_g3_research": False,
        },
        "input_files": {name: rel(path) for name, path in INPUT_FILES.items()},
        "outputs": {
            "multi_service_manual_check": rel(ms_out),
            "multi_api_manual_check": rel(ma_out),
            "guideline": rel(guideline),
            "package_report": rel(package_report),
            "validation_design": rel(validation_design),
            "next_step_report": rel(next_step),
            "archive_root": rel(ARCHIVE_ROOT),
        },
        "input_row_counts": {
            "multi_service_discovery": len(multi_service_rows),
            "multi_api_recommendation": len(multi_api_rows),
        },
        "sample_row_counts": {
            "multi_service_discovery": len(multi_service_selected),
            "multi_api_recommendation": len(multi_api_selected),
        },
        "multi_service_selected_stats": make_stats(multi_service_selected),
        "multi_api_selected_stats": make_stats(multi_api_selected),
        "manual_columns": MANUAL_FIELDS,
        "multi_service_manual_task_type_values": MULTI_SERVICE_TASK_TYPE_VALUES,
        "multi_api_manual_task_type_values": MULTI_API_TASK_TYPE_VALUES,
        "recommend_full_cleaning_now": False,
        "reason_not_full_cleaning": (
            "Manual check must validate semantic alignment, leak handling, "
            "and task-level correctness before formal cleaning."
        ),
    }

    summary_path = OUTPUT_DIR / "main_four_tasks_manual_check_package_summary.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    generated_paths = [
        ms_out,
        ma_out,
        summary_path,
        guideline,
        package_report,
        validation_design,
        next_step,
        Path(__file__).resolve(),
    ]
    copy_to_archive(generated_paths)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
