"""Shared utilities for ServiceDiscoveryBench rule revision v0.6.

All helpers are read-only with respect to source data. New artifacts are written
only under outputs/main_four_tasks_rule_revision_v0_6/ or docs/phase1/.
"""

from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


OUTPUT_DIR = Path("outputs/main_four_tasks_rule_revision_v0_6")
DOCS_DIR = Path("docs/phase1")
ROUND2_V05_DIR = Path("outputs/main_four_tasks_round2_rule_validation_v0_5")
ROUND2_SMALL_DIR = Path("outputs/main_four_tasks_round2_small_dryrun_v0_4")

REQUIRED_V05_DOCS = [
    Path("docs/phase1/round2_manual80_analysis_report_v0_5.md"),
    Path("docs/phase1/round2_draft_vs_human_comparison_report_v0_5.md"),
    Path("docs/phase1/manual40_round2_rule_replay_report_v0_5.md"),
    Path("docs/phase1/manual_audit_rule_v4_candidate.md"),
    Path("docs/phase1/round2_v0_5_go_no_go_report.md"),
]

REQUIRED_V05_OUTPUTS = [
    ROUND2_V05_DIR / "round2_manual_decisions_80_user_approved.normalized_from_user_overlay.csv",
    ROUND2_V05_DIR / "round2_draft_vs_human_trace.csv",
    ROUND2_V05_DIR / "round2_draft_vs_human_confusion_matrix.csv",
    ROUND2_V05_DIR / "manual40_rule_replay_trace.csv",
    ROUND2_V05_DIR / "round2_rule_replay_trace.csv",
]

MANUAL40_PATH = Path(
    "outputs/main_four_tasks_manual_check_v0_2/"
    "main_four_tasks_manual_decisions_40_user_approved_round1.csv"
)

ROUND2_CANDIDATE_POOL_PATHS = [
    ROUND2_SMALL_DIR / "round2_multi_service_candidates_pool.csv",
    ROUND2_SMALL_DIR / "round2_multi_api_candidates_pool.csv",
]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, rows: Sequence[Dict[str, object]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = fieldnames_union(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fieldnames_union(rows: Sequence[Dict[str, object]]) -> List[str]:
    names: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in names:
                names.append(key)
    return names


def file_profile(path: Path) -> Dict[str, object]:
    item: Dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
        "row_count": None,
        "columns": [],
        "error": "",
    }
    if not path.exists():
        return item
    if path.suffix.lower() != ".csv":
        try:
            text = path.read_text(encoding="utf-8")
            item["line_count"] = len(text.splitlines())
            return item
        except Exception as exc:  # pragma: no cover - CLI reporting
            item["error"] = str(exc)
            return item
    try:
        cols, rows = read_csv(path)
        item["row_count"] = len(rows)
        item["columns"] = cols
        item["empty_counts"] = {
            col: sum(1 for row in rows if (row.get(col) or "").strip() == "")
            for col in cols
        }
    except Exception as exc:  # pragma: no cover - CLI reporting
        item["error"] = str(exc)
    return item


def missing_required_inputs() -> List[Path]:
    required = list(REQUIRED_V05_DOCS) + list(REQUIRED_V05_OUTPUTS) + [MANUAL40_PATH]
    return [path for path in required if not path.exists()]


def write_missing_inputs(paths: Sequence[Path]) -> Path:
    ensure_dirs()
    out = OUTPUT_DIR / "MISSING_INPUTS.md"
    lines = [
        "# Missing Inputs for v0.6",
        "",
        f"生成时间：{now_str()}",
        "",
        "以下关键输入缺失，因此停止 v0.6，不继续猜测。",
        "",
        "| missing path |",
        "|---|",
    ]
    for path in paths:
        lines.append(f"| `{path}` |")
    lines.extend(
        [
            "",
            "Scope: 没有 full cleaning、没有 split、没有 baseline、没有训练模型。",
        ]
    )
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def norm_decision(raw: str) -> str:
    text = (raw or "").strip().lower()
    if text in {"keep_for_cleaning_candidate", "keep", "clean_candidate", "clean_ready"}:
        return "keep_for_cleaning_candidate"
    if text in {"remove", "removed", "invalid_candidate_or_gold", "remove_api_leak"}:
        return "remove"
    if text in {"uncertain", "service_leak_only", "api_leak_uncertain", "ambiguous"}:
        return "uncertain"
    return text or "other"


def leakage_bucket(raw: str) -> str:
    text = (raw or "").strip().lower()
    if not text:
        return "other"
    if "api_leak_blocking" in text or text == "api_leak" or "strong_api_leak" in text:
        return "api_leak_blocking"
    if "service_leak_only" in text:
        return "service_leak_only"
    if "ambiguous" in text or "uncertain" in text:
        return "ambiguous"
    if text in {"no_blocking", "no_blocking_leak", "no_obvious_leak", "none"}:
        return "no_blocking"
    return "other"


def semantic_bucket(raw: str) -> str:
    text = (raw or "").strip().lower()
    if "mismatch" in text:
        return "mismatch"
    if "uncertain" in text or "ambiguous" in text:
        return "uncertain"
    if text in {"ok", "semantic_alignment_ok", "aligned"} or "alignment_ok" in text:
        return "ok"
    return text or "other"


def candidate_bucket(raw: str) -> str:
    text = (raw or "").strip().lower()
    if text == "valid" or text.endswith("_valid"):
        return "valid"
    if "insufficient" in text or "choice_space" in text:
        return "insufficient_choice_space"
    if "invalid" in text:
        return "invalid"
    if "uncertain" in text or "ambiguous" in text:
        return "uncertain"
    return text or "other"


def task_type_bucket(raw: str) -> str:
    text = (raw or "").strip().lower()
    if "valid_multi_service" in text:
        return "valid_multi_service"
    if "valid_multi_api" in text:
        return "valid_multi_api"
    if "invalid" in text:
        return "invalid"
    if "uncertain" in text or "ambiguous" in text:
        return "uncertain"
    if text.startswith("valid"):
        return "valid"
    return text or "other"


def task_family(raw: str) -> str:
    text = (raw or "").strip().lower()
    if "multi_service" in text:
        return "multi_service"
    if "multi_api" in text:
        return "multi_api"
    if "single_service" in text:
        return "single_service"
    if "single_api" in text:
        return "single_api"
    return text or "other"


def boolish(raw: str) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "y"}


def as_int(raw: str) -> Optional[int]:
    text = str(raw or "").strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_json_count(raw: str) -> Optional[int]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if isinstance(data, list):
        return len(data)
    return None


def get_count(row: Dict[str, str], count_col: str, json_col: str) -> Optional[int]:
    return as_int(row.get(count_col, "")) or parse_json_count(row.get(json_col, ""))


def standardize_round2_final(row: Dict[str, str]) -> Dict[str, object]:
    human_source = row.get("human_final_source", "")
    return {
        **row,
        "sample_id": row.get("round2_review_id", "") or row.get("review_id", "") or row.get("task_id", ""),
        "decision_norm": norm_decision(row.get("manual_final_decision", "")),
        "assistant_decision_norm": norm_decision(row.get("assistant_draft_manual_final_decision", "")),
        "leakage_norm": leakage_bucket(row.get("manual_leak_check", "") or row.get("leak_status", "")),
        "assistant_leakage_norm": leakage_bucket(row.get("assistant_draft_manual_leak_check", "")),
        "semantic_norm": semantic_bucket(row.get("manual_semantic_alignment", "")),
        "assistant_semantic_norm": semantic_bucket(row.get("assistant_draft_manual_semantic_alignment", "")),
        "candidate_norm": candidate_bucket(row.get("manual_candidate_gold_validity", "")),
        "task_check_norm": task_type_bucket(row.get("manual_task_type_check", "")),
        "task_family": task_family(row.get("task_type", "")),
        "is_overlay": human_source == "user_feedback_overlay" or row.get("human_final_overlay_applied", "") == "yes",
        "candidate_service_count_int": get_count(row, "candidate_service_count", "candidate_services_json"),
        "gold_service_count_int": get_count(row, "gold_service_count", "gold_services_json"),
        "candidate_api_count_int": get_count(row, "candidate_api_count", "candidate_apis_json"),
        "gold_api_count_int": get_count(row, "gold_api_count", "gold_apis_json"),
    }


def standardize_trace(row: Dict[str, str]) -> Dict[str, object]:
    return {
        **row,
        "sample_id": row.get("sample_id", "") or row.get("round2_review_id", "") or row.get("task_id", ""),
        "draft_decision_norm": norm_decision(row.get("draft_manual_final_decision_norm", "") or row.get("draft_manual_final_decision", "")),
        "human_decision_norm": norm_decision(row.get("human_manual_final_decision_norm", "") or row.get("human_manual_final_decision", "")),
        "draft_leakage_norm": leakage_bucket(row.get("draft_leakage_check_norm", "") or row.get("draft_leakage_check", "")),
        "human_leakage_norm": leakage_bucket(row.get("human_leakage_check_norm", "") or row.get("human_leakage_check", "")),
        "draft_semantic_norm": semantic_bucket(row.get("draft_semantic_alignment_check_norm", "") or row.get("draft_semantic_alignment_check", "")),
        "human_semantic_norm": semantic_bucket(row.get("human_semantic_alignment_check_norm", "") or row.get("human_semantic_alignment_check", "")),
        "human_final_source": row.get("human_final_source", ""),
        "user_feedback_category": row.get("user_feedback_category", ""),
    }


def load_round2_final() -> List[Dict[str, object]]:
    _, rows = read_csv(ROUND2_V05_DIR / "round2_manual_decisions_80_user_approved.normalized_from_user_overlay.csv")
    return [standardize_round2_final(row) for row in rows]


def load_draft_vs_human_trace() -> List[Dict[str, object]]:
    _, rows = read_csv(ROUND2_V05_DIR / "round2_draft_vs_human_trace.csv")
    return [standardize_trace(row) for row in rows]


def load_rule_trace(path: Path) -> List[Dict[str, str]]:
    _, rows = read_csv(path)
    return rows


def identify_failure_modes(final_row: Dict[str, object], trace_row: Optional[Dict[str, object]] = None) -> List[str]:
    modes: List[str] = []
    category = str(final_row.get("user_feedback_category", ""))
    decision = str(final_row.get("decision_norm", ""))
    assistant_decision = str(final_row.get("assistant_decision_norm", ""))
    leak = str(final_row.get("leakage_norm", ""))
    assistant_leak = str(final_row.get("assistant_leakage_norm", ""))
    semantic = str(final_row.get("semantic_norm", ""))
    candidate = str(final_row.get("candidate_norm", ""))
    task = str(final_row.get("task_family", ""))
    review_bucket = str(final_row.get("mechanical_screening_bucket", ""))
    csc = final_row.get("candidate_service_count_int")
    gsc = final_row.get("gold_service_count_int")

    if category == "strong_api_leak" or (leak == "api_leak_blocking" and assistant_leak == "no_blocking"):
        modes.append("strong_api_leak_missed_by_draft")
    if category == "strong_api_leak":
        modes.append("endpoint_identity_exposed_in_query")
    if category == "leak_false_positive":
        modes.append("generic_weak_leak_false_positive")
    if category == "api_level_single_service_ok" or (task == "multi_api" and csc == 1 and decision == "keep_for_cleaning_candidate"):
        modes.append("api_level_single_service_ok")
    if "service" in task and isinstance(csc, int) and isinstance(gsc, int) and csc <= gsc:
        modes.append("service_level_single_service_invalid")
    if category == "gold_api_cannot_satisfy_query":
        modes.append("gold_api_cannot_satisfy_query")
    if category in {"gold_service_cannot_satisfy_query", "missing_required_service"}:
        modes.append("gold_service_cannot_satisfy_query")
    if category in {
        "gold_api_cannot_satisfy_query",
        "gold_service_cannot_satisfy_query",
        "missing_required_service",
        "package_vs_container_mismatch",
        "semantic_mismatch_despite_no_leak",
    } or semantic in {"mismatch", "uncertain"} and decision in {"remove", "uncertain"}:
        modes.append("semantic_coverage_mismatch")
    if candidate in {"invalid", "insufficient_choice_space"}:
        modes.append("candidate_space_invalid")
    if leak == "service_leak_only":
        modes.append("service_leak_only_policy_unclear")
    if review_bucket == "high_risk_review" and decision == "keep_for_cleaning_candidate":
        modes.append("high_risk_bucket_false_positive")
    if review_bucket == "high_risk_review":
        modes.append("high_risk_bucket_not_predictive")

    if trace_row:
        if trace_row.get("draft_keep_human_remove_or_uncertain") == "yes":
            modes.append("draft_keep_but_human_remove_or_uncertain")
        if trace_row.get("draft_remove_or_uncertain_human_keep") == "yes":
            modes.append("draft_remove_or_uncertain_but_human_keep")
        if trace_row.get("draft_no_blocking_human_api_leak_blocking") == "yes":
            modes.append("draft_no_blocking_but_human_api_leak_blocking")
        if trace_row.get("draft_ok_human_mismatch_or_uncertain") == "yes":
            modes.append("draft_ok_but_human_mismatch_or_uncertain")
    return sorted(set(modes))


def mode_implication(mode: str) -> str:
    implications = {
        "strong_api_leak_missed_by_draft": "Add endpoint/carrier/task-flow identity leak detector; blocking leak must remove.",
        "endpoint_identity_exposed_in_query": "Treat endpoint-specific, carrier-specific, and task-flow identity mentions as strong API leak.",
        "generic_weak_leak_false_positive": "Do not remove on generic API terms such as Latest/All/Count without endpoint identity.",
        "api_level_single_service_ok": "candidate_service_count=1 is not fatal for API-level if API choice space is valid.",
        "service_level_single_service_invalid": "Service-level discovery still needs candidate_service_count > gold_service_count.",
        "gold_api_cannot_satisfy_query": "Add capability coverage check at API level; no leak does not imply gold correctness.",
        "gold_service_cannot_satisfy_query": "Add capability coverage check at service level; unrelated services must remove.",
        "semantic_coverage_mismatch": "coverage_mismatch should remove; coverage_uncertain should be uncertain.",
        "candidate_space_invalid": "Choice-space validator must run before clean-ready.",
        "service_leak_only_policy_unclear": "Keep service_leak_only out of clean service discovery and route to analysis/API-level review.",
        "high_risk_bucket_false_positive": "High-risk is review priority, not an automatic reject label.",
        "high_risk_bucket_not_predictive": "Split high-risk into concrete risk subtypes before using it in cleaning.",
        "draft_keep_but_human_remove_or_uncertain": "Assistant draft over-keeps; add conservative coverage and leak gates.",
        "draft_remove_or_uncertain_but_human_keep": "Assistant draft over-rejects; reduce generic leak false positives and API-level single-service false rejects.",
        "draft_no_blocking_but_human_api_leak_blocking": "Leak detector must catch carrier/endpoint identity mentions.",
        "draft_ok_but_human_mismatch_or_uncertain": "Semantic/capability detector must be separated from leak detector.",
    }
    return implications.get(mode, "Review and encode a specific rule implication.")


def markdown_table(rows: Sequence[Dict[str, object]], cols: Sequence[str], max_rows: int = 10) -> List[str]:
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for row in rows[:max_rows]:
        vals: List[str] = []
        for col in cols:
            text = str(row.get(col, "")).replace("\n", " ").replace("|", "/")
            if len(text) > 180:
                text = text[:177] + "..."
            vals.append(text)
        lines.append("| " + " | ".join(vals) + " |")
    if not rows:
        lines.append("| " + " | ".join("-" for _ in cols) + " |")
    return lines


def distribution(rows: Sequence[Dict[str, object]], key: str) -> Dict[str, int]:
    return dict(Counter(str(row.get(key, "") or "<EMPTY>") for row in rows))


def pct(numer: int, denom: int) -> str:
    if denom == 0:
        return "0.0%"
    return f"{numer / denom * 100:.1f}%"


def js_string(data: object) -> str:
    return json.dumps(data, ensure_ascii=False)


def html_escape(text: object) -> str:
    return html.escape(str(text or ""))


COMMON_QUERY_ZH_HINTS = [
    (r"\bpackage\b|\bparcel\b|\bshipment\b", "包裹/邮件/货件追踪"),
    (r"\bcontainer\b", "集装箱追踪"),
    (r"\brestaurant", "餐厅"),
    (r"\bzoo\b", "动物园"),
    (r"\bconcert", "音乐会"),
    (r"\bgas station", "加油站"),
    (r"\blatitude|\blongitude|\bcoordinate", "经纬度/坐标"),
    (r"\baddress|\bpostcode|\bzip code|\bCEP\b", "地址/邮编/CEP"),
    (r"\bcarrier\b|\bCorreo Argentino\b|\bOCA\b", "承运商/专名"),
    (r"\btracking\b|\btrack\b", "追踪/查询状态"),
    (r"\bweather\b", "天气"),
    (r"\bnews\b", "新闻"),
    (r"\bbookstore\b", "书店"),
    (r"\bvenue\b", "场地"),
]


def query_zh_hint(query: str) -> str:
    hits = []
    for pattern, label in COMMON_QUERY_ZH_HINTS:
        if re.search(pattern, query or "", flags=re.I):
            hits.append(label)
    if not hits:
        return "中文辅助：请人工根据英文 query 判断核心需求。"
    return "中文辅助关键词：" + "；".join(dict.fromkeys(hits))


def json_names(raw: str, key: str = "api_name") -> str:
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return str(raw)[:500]
    if not isinstance(data, list):
        return str(data)
    names = []
    for item in data:
        if isinstance(item, dict):
            if key == "api_name":
                service = item.get("service_name", "")
                api = item.get("api_name", "")
                names.append(f"{service} / {api}".strip(" /"))
            else:
                names.append(str(item.get(key, "")))
        else:
            names.append(str(item))
    return "; ".join(names)
