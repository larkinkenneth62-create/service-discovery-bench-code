#!/usr/bin/env python
"""Search ToolBench full G3 for possible strong-composable candidates.

This script is intentionally a candidate-search utility only. It does not
perform official dataset cleaning, splitting, baseline evaluation, or model
training.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from heapq import heappush, heapreplace
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


G3_FILE = Path("data") / "instruction" / "G3_query.json"

SEARCH_ROOTS = [
    Path("."),
    Path("data"),
    Path("data") / "instruction",
    Path("data") / "test_instruction",
    Path("toolbench"),
    Path("reproduction_data"),
    Path("server"),
]

FILE_NAME_PATTERN = re.compile(
    r"(G3|g3|instruction|test_instruction|tool|api|solution|query)",
    re.IGNORECASE,
)
ALLOWED_SUFFIXES = {".json", ".jsonl", ".csv", ".txt"}

SIGNAL_PRIORITY = {"none": 0, "weak": 1, "medium": 2, "strong": 3}

STRONG_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("based on", re.compile(r"\bbased on\b", re.IGNORECASE | re.DOTALL)),
    ("according to", re.compile(r"\baccording to\b", re.IGNORECASE | re.DOTALL)),
    ("using the result", re.compile(r"\busing the result\b", re.IGNORECASE | re.DOTALL)),
    ("with the result", re.compile(r"\bwith the result\b", re.IGNORECASE | re.DOTALL)),
    ("use the result", re.compile(r"\buse the result\b", re.IGNORECASE | re.DOTALL)),
    ("use the returned", re.compile(r"\buse the returned\b", re.IGNORECASE | re.DOTALL)),
    ("use the retrieved", re.compile(r"\buse the retrieved\b", re.IGNORECASE | re.DOTALL)),
    ("use the obtained", re.compile(r"\buse the obtained\b", re.IGNORECASE | re.DOTALL)),
    ("after finding", re.compile(r"\bafter finding\b", re.IGNORECASE | re.DOTALL)),
    ("after retrieving", re.compile(r"\bafter retrieving\b", re.IGNORECASE | re.DOTALL)),
    ("first ... then ...", re.compile(r"\bfirst\b.{0,220}\bthen\b", re.IGNORECASE | re.DOTALL)),
    (
        "find X and then use it to Y",
        re.compile(
            r"\b(find|retrieve|get|search for|look up)\b.{0,160}\bthen\b.{0,160}\b(use|determine|recommend|choose|filter|search|query)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    ("recommend based on", re.compile(r"\brecommend\b.{0,80}\bbased on\b", re.IGNORECASE | re.DOTALL)),
    ("decide based on", re.compile(r"\bdecide\b.{0,80}\bbased on\b", re.IGNORECASE | re.DOTALL)),
    ("choose based on", re.compile(r"\bchoose\b.{0,80}\bbased on\b", re.IGNORECASE | re.DOTALL)),
    ("depending on", re.compile(r"\bdepending on\b", re.IGNORECASE | re.DOTALL)),
    ("if ... then ...", re.compile(r"\bif\b.{0,220}\bthen\b", re.IGNORECASE | re.DOTALL)),
    ("given the result", re.compile(r"\bgiven the result\b", re.IGNORECASE | re.DOTALL)),
    (
        "use X to determine/recommend/choose/filter/search/query Y",
        re.compile(
            r"\buse\b.{1,140}\bto\b.{1,80}\b(determine|recommend|choose|filter|search|query)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
]

MEDIUM_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("before proceeding", re.compile(r"\bbefore proceeding\b", re.IGNORECASE | re.DOTALL)),
    ("before I proceed", re.compile(r"\bbefore I proceed\b", re.IGNORECASE | re.DOTALL)),
    ("before recommending", re.compile(r"\bbefore recommending\b", re.IGNORECASE | re.DOTALL)),
    ("in order to", re.compile(r"\bin order to\b", re.IGNORECASE | re.DOTALL)),
    ("so that", re.compile(r"\bso that\b", re.IGNORECASE | re.DOTALL)),
    ("after that", re.compile(r"\bafter that\b", re.IGNORECASE | re.DOTALL)),
    ("then use", re.compile(r"\bthen\b.{0,80}\buse\b", re.IGNORECASE | re.DOTALL)),
    ("then find", re.compile(r"\bthen\b.{0,80}\bfind\b", re.IGNORECASE | re.DOTALL)),
    ("then search", re.compile(r"\bthen\b.{0,80}\bsearch\b", re.IGNORECASE | re.DOTALL)),
    ("then recommend", re.compile(r"\bthen\b.{0,80}\brecommend\b", re.IGNORECASE | re.DOTALL)),
    (
        "use X to Y",
        re.compile(r"\buse\b.{1,140}\bto\b.{1,80}\b", re.IGNORECASE | re.DOTALL),
    ),
    (
        "check X before Y",
        re.compile(r"\bcheck\b.{1,140}\bbefore\b.{1,140}", re.IGNORECASE | re.DOTALL),
    ),
]

WEAK_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("recommend", re.compile(r"\brecommend\b", re.IGNORECASE | re.DOTALL)),
    ("suggest", re.compile(r"\bsuggest\b", re.IGNORECASE | re.DOTALL)),
    ("also", re.compile(r"\balso\b", re.IGNORECASE | re.DOTALL)),
    ("additionally", re.compile(r"\badditionally\b", re.IGNORECASE | re.DOTALL)),
    ("and then", re.compile(r"\band then\b", re.IGNORECASE | re.DOTALL)),
    ("then", re.compile(r"\bthen\b", re.IGNORECASE | re.DOTALL)),
    ("after", re.compile(r"\bafter\b", re.IGNORECASE | re.DOTALL)),
    ("plus", re.compile(r"\bplus\b", re.IGNORECASE | re.DOTALL)),
    ("along with", re.compile(r"\balong with\b", re.IGNORECASE | re.DOTALL)),
]

ORDINARY_MULTI_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("also", re.compile(r"\balso\b", re.IGNORECASE | re.DOTALL)),
    ("additionally", re.compile(r"\badditionally\b", re.IGNORECASE | re.DOTALL)),
    ("meanwhile", re.compile(r"\bmeanwhile\b", re.IGNORECASE | re.DOTALL)),
    ("at the same time", re.compile(r"\bat the same time\b", re.IGNORECASE | re.DOTALL)),
    ("along with", re.compile(r"\balong with\b", re.IGNORECASE | re.DOTALL)),
    ("I also need", re.compile(r"\bI also need\b", re.IGNORECASE | re.DOTALL)),
    ("please also", re.compile(r"\bplease also\b", re.IGNORECASE | re.DOTALL)),
    ("and provide", re.compile(r"\band provide\b", re.IGNORECASE | re.DOTALL)),
    ("and get", re.compile(r"\band get\b", re.IGNORECASE | re.DOTALL)),
    ("and find", re.compile(r"\band find\b", re.IGNORECASE | re.DOTALL)),
]

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "for",
    "from",
    "get",
    "give",
    "help",
    "i",
    "in",
    "is",
    "it",
    "list",
    "me",
    "my",
    "need",
    "of",
    "on",
    "or",
    "please",
    "provide",
    "search",
    "some",
    "suggest",
    "the",
    "to",
    "tool",
    "use",
    "using",
    "want",
    "with",
}

GENERIC_API_WORDS = {
    "all",
    "count",
    "get",
    "latest",
    "list",
    "search",
    "companies",
    "company",
    "news",
    "details",
    "info",
    "information",
    "data",
    "status",
    "lookup",
}


@dataclass
class SignalResult:
    strength: str
    evidence: str
    reason: str


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.strip().lower())


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=False)


def iter_json_array(path: Path) -> Iterator[Dict[str, Any]]:
    """Stream objects from a top-level JSON array without loading the file."""
    decoder = json.JSONDecoder()
    buffer = ""
    started = False

    with path.open("r", encoding="utf-8") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            buffer += chunk
            pos = 0
            if not started:
                start = buffer.find("[")
                if start < 0:
                    continue
                pos = start + 1
                started = True

            while True:
                while pos < len(buffer) and buffer[pos] in " \r\n\t,":
                    pos += 1
                if pos >= len(buffer):
                    break
                if buffer[pos] == "]":
                    return
                try:
                    obj, end = decoder.raw_decode(buffer, pos)
                except json.JSONDecodeError:
                    break
                if isinstance(obj, dict):
                    yield obj
                pos = end
            buffer = buffer[pos:]


def snippet(text: str, match: re.Match[str], radius: int = 70) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def first_pattern_match(
    text: str, patterns: Sequence[Tuple[str, re.Pattern[str]]]
) -> Optional[Tuple[str, str]]:
    for label, pattern in patterns:
        match = pattern.search(text)
        if match:
            return label, snippet(text, match)
    return None


def detect_dependency_signal(query: str) -> SignalResult:
    for strength, patterns in (
        ("strong", STRONG_PATTERNS),
        ("medium", MEDIUM_PATTERNS),
        ("weak", WEAK_PATTERNS),
    ):
        found = first_pattern_match(query, patterns)
        if found:
            label, evidence = found
            if strength == "weak":
                reason = (
                    f"Matched weak dependency cue `{label}`. This is only a recall signal and may be ordinary multi-tasking."
                )
            elif strength == "medium":
                reason = (
                    f"Matched medium dependency cue `{label}`. Human review should check whether a later step consumes an earlier result."
                )
            else:
                reason = (
                    f"Matched strong dependency cue `{label}` suggesting a later action may depend on an earlier result."
                )
            return SignalResult(strength=strength, evidence=evidence, reason=reason)
    return SignalResult(strength="none", evidence="", reason="No dependency keyword or structure matched.")


def detect_ordinary_multi_risk(query: str, dependency_strength: str) -> Tuple[str, str]:
    found = first_pattern_match(query, ORDINARY_MULTI_PATTERNS)
    if not found:
        return "low", "No ordinary-multi negative signal matched."
    label, evidence = found
    if dependency_strength == "strong":
        return (
            "medium",
            f"Matched ordinary-multi cue `{label}` despite a strong dependency cue; evidence: {evidence}",
        )
    return (
        "high",
        f"Matched ordinary-multi cue `{label}` without strong dependency evidence; evidence: {evidence}",
    )


def get_gold_apis(task: Dict[str, Any]) -> List[Tuple[str, str]]:
    gold: List[Tuple[str, str]] = []
    for item in task.get("relevant APIs", []) or []:
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) >= 2:
            gold.append((str(item[0]), str(item[1])))
    return gold


def api_key(tool_name: Any, api_name: Any) -> Tuple[str, str]:
    return normalize_text(tool_name), normalize_text(api_name)


def query_mentions_name(query: str, name: str) -> bool:
    query_norm = normalize_text(query)
    name_norm = normalize_text(name)
    if len(name_norm) < 3:
        return False
    return name_norm in query_norm


def is_endpoint_or_technical_name(name: str) -> bool:
    if re.search(r"[/_:{}]", name):
        return True
    if re.search(r"[a-z][A-Z]", name):
        return True
    if len(re.findall(r"[A-Za-z0-9]+", name)) >= 4:
        return True
    return False


def is_generic_api_name(name: str) -> bool:
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]+", name)]
    if not tokens:
        return True
    if len(tokens) <= 2 and all(token in GENERIC_API_WORDS or len(token) <= 2 for token in tokens):
        return True
    if len(tokens) == 1 and tokens[0] in GENERIC_API_WORDS:
        return True
    return False


def detect_leakage(query: str, gold_apis: Sequence[Tuple[str, str]]) -> Tuple[str, str]:
    api_mentions: List[str] = []
    service_mentions: List[str] = []
    for service_name, api_name in gold_apis:
        if query_mentions_name(query, api_name):
            api_mentions.append(api_name)
        if query_mentions_name(query, service_name):
            service_mentions.append(service_name)

    if api_mentions:
        if any(is_endpoint_or_technical_name(name) and not is_generic_api_name(name) for name in api_mentions):
            return "api_leak_blocking", f"Query directly mentions technical gold API name(s): {api_mentions}"
        if all(is_generic_api_name(name) for name in api_mentions):
            return "api_leak_uncertain", f"Query mentions generic gold API name(s): {api_mentions}"
        return "leak_uncertain", f"Query mentions ambiguous gold API name(s): {api_mentions}"

    if service_mentions:
        return "service_leak_only", f"Query directly mentions gold service name(s): {service_mentions}"

    return "no_blocking_leak", "No direct gold API/service name mention detected by exact normalized substring."


def tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", value.lower())
        if token not in STOPWORDS
    }


def detect_semantic_alignment(
    query: str, candidates: Sequence[Dict[str, Any]], gold_apis: Sequence[Tuple[str, str]]
) -> Tuple[str, str]:
    if not candidates or not gold_apis:
        return "candidate_or_gold_missing", "Candidate APIs or gold APIs are missing."

    gold_keys = {api_key(service, api) for service, api in gold_apis}
    gold_text_parts: List[str] = []
    for candidate in candidates:
        if api_key(candidate.get("tool_name"), candidate.get("api_name")) in gold_keys:
            gold_text_parts.extend(
                [
                    str(candidate.get("category_name", "")),
                    str(candidate.get("tool_name", "")),
                    str(candidate.get("api_name", "")),
                    str(candidate.get("api_description", "")),
                ]
            )
    if not gold_text_parts:
        gold_text_parts = [f"{service} {api}" for service, api in gold_apis]

    query_tokens = tokens(query)
    gold_tokens = tokens(" ".join(gold_text_parts))
    if not query_tokens or not gold_tokens:
        return "alignment_uncertain", "Insufficient tokens to estimate query-gold semantic alignment."

    overlap = query_tokens & gold_tokens
    if not overlap:
        return (
            "mismatch_uncertain",
            "No non-stopword overlap between query text and gold API/service text.",
        )
    if len(overlap) <= 1 and len(gold_tokens) >= 8:
        return (
            "alignment_uncertain",
            f"Very small query-gold token overlap: {sorted(overlap)}",
        )
    return "alignment_ok", f"Query-gold token overlap includes: {sorted(overlap)[:8]}"


def extract_tool_sequence_evidence(task: Dict[str, Any]) -> Tuple[str, str, bool]:
    candidate_keys = {
        "solution",
        "solutions",
        "answer",
        "answers",
        "tool_calls",
        "tool_call",
        "api_calls",
        "api_call_sequence",
        "call_sequence",
        "path",
        "paths",
        "chain",
        "chains",
        "dfsd",
        "DFSDT",
    }
    found: Dict[str, Any] = {}

    def walk(value: Any, prefix: str = "") -> None:
        if len(found) >= 5:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                child_prefix = f"{prefix}.{key_text}" if prefix else key_text
                if key_text in candidate_keys or any(marker in key_text.lower() for marker in ["solution", "sequence", "tool_call", "api_call", "chain", "dfsd"]):
                    if child not in (None, "", [], {}):
                        found[child_prefix] = child
                if key_text not in {"api_list"}:
                    walk(child, child_prefix)
        elif isinstance(value, list):
            for index, child in enumerate(value[:20]):
                walk(child, f"{prefix}[{index}]")

    walk(task)
    if not found:
        return "not_available", "not_available", False

    raw_preview = json_dumps(found)
    dependency_like = any(
        isinstance(value, list) and len(value) > 1 for value in found.values()
    )
    evidence = raw_preview[:4000]
    return evidence, raw_preview[:4000], dependency_like


def build_candidate_payload(
    candidates: Sequence[Dict[str, Any]], gold_apis: Sequence[Tuple[str, str]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[Dict[str, str]]]:
    gold_keys = {api_key(service, api) for service, api in gold_apis}
    gold_services = sorted({service for service, _api in gold_apis})
    gold_api_rows = [{"service_name": service, "api_name": api} for service, api in gold_apis]

    candidate_services: List[Dict[str, Any]] = []
    candidate_apis: List[Dict[str, Any]] = []
    seen_services: set[Tuple[str, str]] = set()
    for candidate in candidates:
        category = candidate.get("category_name", "")
        service_name = candidate.get("tool_name", "")
        service_key = (normalize_text(category), normalize_text(service_name))
        if service_key not in seen_services:
            seen_services.add(service_key)
            candidate_services.append(
                {
                    "category_name": category,
                    "service_name": service_name,
                }
            )
        candidate_key = api_key(service_name, candidate.get("api_name"))
        candidate_apis.append(
            {
                "category_name": category,
                "service_name": service_name,
                "api_name": candidate.get("api_name", ""),
                "api_description": candidate.get("api_description", ""),
                "is_gold_api": int(candidate_key in gold_keys),
            }
        )
    return candidate_services, candidate_apis, gold_services, gold_api_rows


def score_candidate(
    dependency_strength: str,
    tool_sequence_dependency: bool,
    ordinary_multi_risk: str,
    leakage_risk: str,
    semantic_alignment_risk: str,
    missing_candidate_or_gold: bool,
) -> int:
    score = {"strong": 40, "medium": 25, "weak": 10, "none": 0}.get(dependency_strength, 0)
    if tool_sequence_dependency:
        score += 30
    if ordinary_multi_risk == "high":
        score -= 30
    if leakage_risk == "api_leak_blocking":
        score -= 50
    elif leakage_risk == "service_leak_only":
        score -= 15
    elif leakage_risk in {"api_leak_uncertain", "leak_uncertain"}:
        score -= 10
    if semantic_alignment_risk == "mismatch_uncertain":
        score -= 40
    if semantic_alignment_risk == "candidate_or_gold_missing":
        score -= 20
    if missing_candidate_or_gold:
        score -= 20
    return score


def discover_g3_files(toolbench_root: Path) -> List[str]:
    found: List[str] = []
    for relative_root in SEARCH_ROOTS:
        root = toolbench_root / relative_root
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            if FILE_NAME_PATTERN.search(path.name):
                found.append(str(path))
    return sorted(set(found))


def make_row(
    task: Dict[str, Any],
    source_file: Path,
    record_index: int,
    dependency: SignalResult,
) -> Dict[str, Any]:
    query = str(task.get("query", "") or "")
    query_id = task.get("query_id", record_index)
    candidates = task.get("api_list", []) or []
    if not isinstance(candidates, list):
        candidates = []
    gold_apis = get_gold_apis(task)

    ordinary_risk, ordinary_reason = detect_ordinary_multi_risk(query, dependency.strength)
    leakage_risk, leakage_reason = detect_leakage(query, gold_apis)
    semantic_risk, semantic_reason = detect_semantic_alignment(query, candidates, gold_apis)
    tool_evidence, raw_solution_path, tool_sequence_dependency = extract_tool_sequence_evidence(task)
    candidate_services, candidate_apis, gold_services, gold_api_rows = build_candidate_payload(
        candidates, gold_apis
    )
    missing_candidate_or_gold = not candidates or not gold_apis
    score = score_candidate(
        dependency.strength,
        tool_sequence_dependency,
        ordinary_risk,
        leakage_risk,
        semantic_risk,
        missing_candidate_or_gold,
    )

    return {
        "search_rank": "",
        "candidate_quality_score": score,
        "source_file": str(source_file),
        "source_group": "G3",
        "original_task_id": query_id,
        "query_text": query,
        "dependency_signal_strength": dependency.strength,
        "query_dependency_evidence": dependency.evidence,
        "dependency_signal_reason": dependency.reason,
        "ordinary_multi_risk": ordinary_risk,
        "ordinary_multi_risk_reason": ordinary_reason,
        "leakage_risk": leakage_risk,
        "leakage_risk_reason": leakage_reason,
        "semantic_alignment_risk": semantic_risk,
        "semantic_alignment_risk_reason": semantic_reason,
        "tool_sequence_evidence": tool_evidence,
        "candidate_services_json": json_dumps(candidate_services),
        "candidate_apis_json": json_dumps(candidate_apis),
        "gold_services_json": json_dumps(gold_services),
        "gold_apis_json": json_dumps(gold_api_rows),
        "raw_related_apis_json": json_dumps(task.get("relevant APIs", [])),
        "raw_solution_path_json": raw_solution_path,
        "raw_record_id": query_id,
        "strong_composable_final_label": "",
        "strong_composable_decision_reason": "",
        "semantic_alignment_manual_check": "",
        "leakage_manual_check": "",
    }


FIELDNAMES = [
    "search_rank",
    "candidate_quality_score",
    "source_file",
    "source_group",
    "original_task_id",
    "query_text",
    "dependency_signal_strength",
    "query_dependency_evidence",
    "dependency_signal_reason",
    "ordinary_multi_risk",
    "ordinary_multi_risk_reason",
    "leakage_risk",
    "leakage_risk_reason",
    "semantic_alignment_risk",
    "semantic_alignment_risk_reason",
    "tool_sequence_evidence",
    "candidate_services_json",
    "candidate_apis_json",
    "gold_services_json",
    "gold_apis_json",
    "raw_related_apis_json",
    "raw_solution_path_json",
    "raw_record_id",
    "strong_composable_final_label",
    "strong_composable_decision_reason",
    "semantic_alignment_manual_check",
    "leakage_manual_check",
]


def sort_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            int(row["candidate_quality_score"]),
            SIGNAL_PRIORITY.get(str(row["dependency_signal_strength"]), 0),
            -int(row["original_task_id"]) if str(row["original_task_id"]).isdigit() else 0,
        ),
        reverse=True,
    )


def write_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        for count, row in enumerate(rows, start=1):
            output_row = dict(row)
            output_row["search_rank"] = count
            writer.writerow(output_row)
    return count


def write_missing_data_report(path: Path, toolbench_root: Path, discovered_files: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = f"""# Full G3 Missing Data Report

## 【本次做了什么】
尝试定位 ToolBench full G3 原始数据，但没有找到可用于搜索的 `data/instruction/G3_query.json`。

## 【检查的项目根目录】
`{Path.cwd()}`

## 【ToolBench 根目录】
`{toolbench_root}`

## 【发现的候选文件数量】
{len(discovered_files)}

## 【处理结论】
未继续 strong composable 搜索。需要先确认 ToolBench full G3 原始 instruction/query 数据是否已经下载完整。
"""
    path.write_text(report, encoding="utf-8")


def write_reports(
    summary: Dict[str, Any],
    output_dir: Path,
    report_path: Path,
    guideline_path: Path,
    next_step_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)

    found_lines = "\n".join(f"- `{path}`" for path in summary["discovered_g3_related_files"][:30])
    if len(summary["discovered_g3_related_files"]) > 30:
        found_lines += f"\n- ... 其余 {len(summary['discovered_g3_related_files']) - 30} 个文件略。"

    enough_for_human = summary["top100_written"] > 0
    high_count = summary["high_confidence_count_total"]
    medium_count = summary["medium_candidate_count_total"]
    still_no_full = "是。当前只是候选搜索结果，还需要人工确认 top20/top50 后才能决定 composable 是否进入主任务。"

    report = f"""# Full G3 Strong Composable Candidate Search Report

## 【本次做了什么】
从 ToolBench full G3 原始 instruction/query 数据中搜索可能的 strong composable 候选，并输出 top100 候选表、high confidence 候选表、medium 候选表和 ordinary_multi 风险样例表。本步骤不做正式清洗、不做 baseline、不训练模型。

## 【为什么从 dry-run audit 扩大到 full G3】
16 条 dry-run 候选的人审结果中，`strong_composable_final_label=strong_composable` 的数量为 0。当前 dry-run audit 样本不足以构建 composable 主任务，需要从原始 ToolBench full G3 扩大搜索。

## 【找到了哪些原始 G3 文件】
使用文件名关键词 `G3/g3/instruction/test_instruction/tool/api/solution/query` 在 ToolBench 目录内搜索，发现相关文件 {len(summary['discovered_g3_related_files'])} 个。核心输入文件为：

`{summary['input_g3_file']}`

部分相关文件：
{found_lines if found_lines else '- 无'}

## 【读取了多少条原始记录】
- 原始 G3 记录数：{summary['records_read']}
- 命中 strong/medium/weak 任一依赖信号的记录数：{summary['non_none_signal_count']}

## 【dependency_signal_strength 分布】
`{json.dumps(summary['dependency_signal_strength_distribution'], ensure_ascii=False)}`

## 【ordinary_multi_risk 分布】
`{json.dumps(summary['ordinary_multi_risk_distribution'], ensure_ascii=False)}`

## 【leakage_risk 分布】
`{json.dumps(summary['leakage_risk_distribution'], ensure_ascii=False)}`

## 【semantic_alignment_risk 分布】
`{json.dumps(summary['semantic_alignment_risk_distribution'], ensure_ascii=False)}`

## 【输出了多少 top100 候选】
- top100 文件：`{output_dir / 'full_g3_strong_composable_candidates_top100.csv'}`
- 实际输出：{summary['top100_written']} 条
- 若少于 100，说明 full G3 中命中非 none 依赖信号的候选不足 100 条。

## 【high confidence 候选有多少】
- 总数：{high_count}
- 输出文件：`{output_dir / 'full_g3_strong_composable_high_confidence.csv'}`

## 【medium 候选有多少】
- 总数：{medium_count}
- 输出文件：`{output_dir / 'full_g3_strong_composable_medium_candidates.csv'}`

## 【ordinary_multi 风险样本有多少】
- ordinary_multi_risk=high 总数：{summary['ordinary_multi_risk_high_count_total']}
- 输出样例文件：`{output_dir / 'full_g3_ordinary_multi_risk_examples.csv'}`

## 【是否足以进入人工确认】
{'足以进入下一轮人工确认：建议优先确认 top20 或 high confidence 表。' if enough_for_human else '暂不足以进入人工确认：没有找到非 none 依赖信号候选。'}

## 【是否仍然不建议跑全量】
{still_no_full}
"""
    report_path.write_text(report, encoding="utf-8")

    guideline = """# Full G3 Strong Composable Human Confirm Guideline

## 【目标】
这张表不是最终数据集，而是候选搜索结果。人工确认的目标是判断每条候选是否真的存在“后一步吃前一步结果”的跨服务依赖。

## 【strong_composable】
填写 `strong_composable` 的条件：前一个服务/API 的输出会影响后一个服务/API 的输入、选择、过滤、判断或推荐。例如：先查到实体 ID、位置、价格、天气、新闻结果，再用这个结果决定下一步查什么或推荐什么。

可复制模板：
`strong_composable_final_label=strong_composable; semantic_alignment_manual_check=semantic_alignment_ok; leakage_manual_check=no_blocking_leak; strong_composable_decision_reason=后一步明确依赖前一步返回结果，符合强组合。`

## 【ordinary_multi】
填写 `ordinary_multi` 的条件：query 中有多个子需求，但它们只是并列完成，没有一个服务的输出被另一个服务使用。例如“找新闻，同时查天气，同时给我股票价格”通常是 ordinary_multi。

可复制模板：
`strong_composable_final_label=ordinary_multi; semantic_alignment_manual_check=semantic_alignment_ok; leakage_manual_check=no_blocking_leak; strong_composable_decision_reason=多个子任务并列出现，没有跨服务结果依赖。`

## 【ambiguous】
填写 `ambiguous` 的条件：query 看起来可能有先后关系，但文本没有明确说明后一步是否使用前一步结果。

可复制模板：
`strong_composable_final_label=ambiguous; semantic_alignment_manual_check=semantic_alignment_uncertain; leakage_manual_check=leak_uncertain; strong_composable_decision_reason=存在可能的先后关系，但无法确认后一步是否依赖前一步输出。`

## 【not_eligible】
填写 `not_eligible` 的条件：gold/API 明显语义不匹配、candidate/gold 缺失、存在 blocking API leak，或者样本不是可评测的服务发现任务。

可复制模板：
`strong_composable_final_label=not_eligible; semantic_alignment_manual_check=semantic_mismatch_uncertain; leakage_manual_check=api_leak_blocking; strong_composable_decision_reason=存在阻断性泄露或语义不匹配，不能作为 composable 主任务候选。`

## 【semantic_alignment_manual_check】
- `semantic_alignment_ok`: query 与 gold service/API 语义一致。
- `semantic_alignment_uncertain`: 有部分对齐，但需要保守复核。
- `semantic_mismatch_uncertain`: query 和 gold 明显不匹配或高度可疑。

## 【leakage_manual_check】
- `no_blocking_leak`: 没有阻断性 API/service 泄露。
- `api_leak_blocking`: query 直接出现 gold API 名，应该阻断进入主数据。
- `service_leak_only`: query 出现 gold service 名，不进 clean service discovery 主任务，但可保留作 API-level 或分析数据。
- `leak_uncertain`: 是否泄露不确定，保守进入人工复核。

## 【为什么不能只看 dependency keywords】
`then`、`after`、`recommend`、`also` 这些词本身不能证明强组合。它们经常只表示多个需求的陈列顺序。真正的 strong composable 要看后一个服务是否使用前一个服务返回的信息。

## 【为什么“后一步吃前一步结果”重要】
composable service discovery 的难点不是选择多个服务，而是识别跨服务依赖链。没有依赖链的多服务任务更适合放在 multi_service_discovery，而不是 composable_service_discovery。
"""
    guideline_path.write_text(guideline, encoding="utf-8")

    next_step = f"""# Full G3 Strong Composable Next Step

## 1. 现在是否建议跑全量清洗？
不建议。当前只是 full G3 strong composable 候选搜索，还没有人工确认这些候选是否真的 strong composable。

## 2. full G3 是否找到足够 strong signal？
- strong signal 总数：{summary['dependency_signal_strength_distribution'].get('strong', 0)}
- high confidence 候选总数：{high_count}
- medium 候选总数：{medium_count}

是否“足够”需要看人工确认后的 strong_composable 命中率。机器信号只能说明值得审，不等于最终正例。

## 3. 是否应先人工确认 top20/top50？
建议先确认 top20；如果 top20 中 strong_composable 命中率可接受，再扩展到 top50。确认时优先看 `full_g3_strong_composable_candidates_top100.csv` 和 `full_g3_strong_composable_high_confidence.csv`。

## 4. 如果 high confidence 仍少，是否应暂缓 composable 主任务？
是。如果 high confidence 人审后仍几乎没有 strong_composable，建议暂缓 composable 主任务，把 composable 作为后续扩展或单独负例分析，而不是强行纳入主 benchmark。

## 5. 是否可先构建 single/multi/API recommendation 四类主任务，把 composable 后续扩展？
可以。当前证据更支持先稳定 single_service_discovery、single_api_recommendation、multi_service_discovery、multi_api_recommendation，再把 composable 作为需要额外证据支撑的扩展任务。

## 6. 下一步建议
先人工确认 `full_g3_strong_composable_candidates_top100.csv` 的前 20 条，并统计 strong_composable / ordinary_multi / ambiguous / not_eligible 分布。确认后再决定是否扩大到 top50，仍不要直接跑全量清洗。
"""
    next_step_path.write_text(next_step, encoding="utf-8")


def run_search(args: argparse.Namespace) -> Dict[str, Any]:
    start_time = time.time()
    toolbench_root = Path(args.toolbench_root)
    input_path = Path(args.input_g3_file) if args.input_g3_file else toolbench_root / G3_FILE
    output_dir = Path(args.output_dir)
    discovered_files = discover_g3_files(toolbench_root)

    if not input_path.exists():
        write_missing_data_report(
            Path("docs/phase1/full_g3_missing_data_report.md"),
            toolbench_root,
            discovered_files,
        )
        raise SystemExit(f"Missing full G3 input file: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    top_heap: List[Tuple[Tuple[int, int, int], int, Dict[str, Any]]] = []
    high_confidence_rows: List[Dict[str, Any]] = []
    medium_rows: List[Dict[str, Any]] = []
    ordinary_high_rows: List[Dict[str, Any]] = []

    dependency_counts: Counter[str] = Counter()
    ordinary_counts: Counter[str] = Counter()
    leakage_counts: Counter[str] = Counter()
    semantic_counts: Counter[str] = Counter()
    records_read = 0
    non_none_signal_count = 0

    for record_index, task in enumerate(iter_json_array(input_path), start=1):
        if args.max_records and record_index > args.max_records:
            break
        records_read += 1
        query = str(task.get("query", "") or "")
        dependency = detect_dependency_signal(query)
        dependency_counts[dependency.strength] += 1
        if dependency.strength == "none":
            continue

        non_none_signal_count += 1
        row = make_row(task, input_path, record_index, dependency)
        ordinary_counts[str(row["ordinary_multi_risk"])] += 1
        leakage_counts[str(row["leakage_risk"])] += 1
        semantic_counts[str(row["semantic_alignment_risk"])] += 1

        score = int(row["candidate_quality_score"])
        priority = SIGNAL_PRIORITY.get(str(row["dependency_signal_strength"]), 0)
        key = (score, priority, -record_index)
        if len(top_heap) < args.max_output:
            heappush(top_heap, (key, record_index, row))
        elif key > top_heap[0][0]:
            heapreplace(top_heap, (key, record_index, row))

        if (
            row["dependency_signal_strength"] == "strong"
            and row["ordinary_multi_risk"] != "high"
            and row["leakage_risk"] != "api_leak_blocking"
            and row["semantic_alignment_risk"] != "mismatch_uncertain"
        ):
            high_confidence_rows.append(row)

        if row["dependency_signal_strength"] == "medium":
            medium_rows.append(row)

        if row["ordinary_multi_risk"] == "high":
            ordinary_high_rows.append(row)

    top_rows = sort_rows([item[2] for item in top_heap])
    high_rows_sorted = sort_rows(high_confidence_rows)
    medium_rows_sorted = sort_rows(medium_rows)
    ordinary_rows_sorted = sort_rows(ordinary_high_rows)

    top_path = output_dir / "full_g3_strong_composable_candidates_top100.csv"
    high_path = output_dir / "full_g3_strong_composable_high_confidence.csv"
    medium_path = output_dir / "full_g3_strong_composable_medium_candidates.csv"
    ordinary_path = output_dir / "full_g3_ordinary_multi_risk_examples.csv"

    top_written = write_csv(top_path, top_rows)
    high_written = write_csv(high_path, high_rows_sorted)
    medium_written = write_csv(medium_path, medium_rows_sorted)
    ordinary_written = write_csv(ordinary_path, ordinary_rows_sorted)

    summary: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "script": str(Path(__file__)),
        "script_scope": "candidate_search_only_no_cleaning_no_baseline_no_training",
        "toolbench_root": str(toolbench_root),
        "input_g3_file": str(input_path),
        "output_dir": str(output_dir),
        "records_read": records_read,
        "non_none_signal_count": non_none_signal_count,
        "dependency_signal_strength_distribution": dict(dependency_counts),
        "ordinary_multi_risk_distribution": dict(ordinary_counts),
        "leakage_risk_distribution": dict(leakage_counts),
        "semantic_alignment_risk_distribution": dict(semantic_counts),
        "top100_written": top_written,
        "high_confidence_count_total": len(high_confidence_rows),
        "high_confidence_written": high_written,
        "medium_candidate_count_total": len(medium_rows),
        "medium_candidate_written": medium_written,
        "ordinary_multi_risk_high_count_total": len(ordinary_high_rows),
        "ordinary_multi_risk_examples_written": ordinary_written,
        "output_files": {
            "top100": str(top_path),
            "high_confidence": str(high_path),
            "medium_candidates": str(medium_path),
            "ordinary_multi_risk_examples": str(ordinary_path),
        },
        "discovered_g3_related_files": discovered_files,
        "elapsed_seconds": round(time.time() - start_time, 3),
    }

    summary_path = output_dir / "full_g3_strong_composable_search_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["output_files"]["summary_json"] = str(summary_path)

    write_reports(
        summary,
        output_dir,
        Path("docs/phase1/full_g3_strong_composable_search_report.md"),
        Path("docs/phase1/full_g3_strong_composable_human_confirm_guideline.md"),
        Path("docs/phase1/full_g3_strong_composable_next_step.md"),
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toolbench-root", default="external_sources/ToolBench")
    parser.add_argument("--input-g3-file", default="")
    parser.add_argument(
        "--output-dir",
        default="outputs/toolbench_full_g3_strong_composable_search_v0_1",
    )
    parser.add_argument("--max-output", type=int, default=100)
    parser.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="Debug limit. 0 means read the full G3 file.",
    )
    return parser.parse_args()


def main() -> None:
    summary = run_search(parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
