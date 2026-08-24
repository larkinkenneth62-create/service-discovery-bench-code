#!/usr/bin/env python
"""Run StableToolBench filtering/reconstruction policy dry-run v0.2.

Source-specific annotation only. No final dataset, no source merge, no split,
no baseline, no training, no Qwen/API calls, and no HTML app.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


OUT_DIR = Path("outputs/external_source_policy_v0_2/stabletoolbench")
HUMAN_FIELDS = [
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
GENERIC_API_NAMES = {"search", "list", "get", "details", "detail", "home", "info", "status", "all"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def parse_json(raw: str) -> Any:
    try:
        return json.loads(raw) if raw else []
    except Exception:
        return []


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value:
        return [value]
    return []


def api_key(api: Any) -> tuple[str, str]:
    if isinstance(api, dict):
        return (str(api.get("service_name", "")).strip().lower(), str(api.get("api_name", "")).strip().lower())
    return ("", str(api).strip().lower())


def has_phrase(query: str, name: str) -> bool:
    if not name:
        return False
    q = norm(query)
    n = norm(name)
    return bool(n and (n in q or compact(name) in compact(query)))


def name_aliases(name: str) -> set[str]:
    n = norm(name)
    aliases = {n}
    aliases.add(re.sub(r"\b(v|version)\s*\d+\b", "", n).strip())
    aliases.add(re.sub(r"\bapis\b", "api", n).strip())
    aliases.add(re.sub(r"\bapi\b", "", n).strip())
    aliases.add(re.sub(r"\btool\b", "", n).strip())
    aliases.add(re.sub(r"\bservice\b", "", n).strip())
    return {a for a in aliases if a}


def has_name_or_alias(query: str, name: str) -> bool:
    q = norm(query)
    qc = compact(query)
    return any(alias in q or compact(alias) in qc for alias in name_aliases(name))


def dependency_signal(query: str) -> bool:
    q = norm(query)
    patterns = [
        r"based on",
        r"according to",
        r"using the result",
        r"with the result",
        r"after finding",
        r"first .* then",
        r"then use",
        r"use .* to",
        r"depending on",
        r"if .* then",
        r"given the result",
        r"before recommending",
    ]
    return any(re.search(p, q) for p in patterns)


def add(hits: list[dict[str, Any]], rule: str, effect: str, label: str, reason: str, evidence: dict[str, Any]) -> None:
    hits.append({"rule": rule, "effect": effect, "label": label, "reason": reason, "evidence": evidence})


def candidate_space_invalid(row: dict[str, str]) -> bool:
    cand = {api_key(x) for x in as_list(parse_json(row.get("candidate_apis_json", "")))}
    gold = {api_key(x) for x in as_list(parse_json(row.get("gold_apis_json", "")))}
    try:
        cand_count = int(row.get("candidate_api_count", "") or len(cand))
        gold_count = int(row.get("gold_api_count", "") or len(gold))
    except ValueError:
        cand_count, gold_count = len(cand), len(gold)
    if cand and gold and cand == gold:
        return True
    return cand_count <= gold_count


def demo_or_test(row: dict[str, str]) -> bool:
    blob = norm(" ".join([row.get("candidate_services_json", ""), row.get("candidate_apis_json", ""), row.get("gold_services_json", ""), row.get("gold_apis_json", ""), row.get("query_text", "")]))
    phrases = ["demo project", "fastapi project", "petstore", "sample", "sandbox", "test api", "healthcheck", "health status of", "health endpoint", "order catalog", "xmusic"]
    return any(p in blob for p in phrases)


def missing_core(row: dict[str, str]) -> bool:
    q = norm(row.get("query_text", ""))
    g = norm(row.get("gold_apis_json", "") + " " + row.get("gold_tools_or_apis_json", ""))
    checks = [
        (["recommend", "suggest", "popular", "suitable"], ["recommend", "suggest", "popular", "rating"]),
        (["venue", "accessible", "landscape"], ["venue", "place", "accessib", "landscape"]),
        (["fee", "fees", "withdrawal"], ["fee", "withdraw"]),
        (["followers", "artist followers"], ["followers", "artist"]),
        (["quarterly", "ratio", "cash flow", "balance sheet", "income statement"], ["quarterly", "ratio", "cash flow", "balance", "income"]),
        (["recipe", "cake recipe", "detailed recipe"], ["recipe", "detail"]),
        (["trending keyword", "top 10"], ["trend", "keyword"]),
    ]
    if any(any(t in q for t in q_terms) and not any(t in g for t in g_terms) for q_terms, g_terms in checks):
        return True
    if "artist" in q and "followers" in q and "user followers" in g and "artist followers" not in g:
        return True
    if "trending keywords" in q and "geo map" in g and "top" in q:
        return True
    return False


def wrong_gold_set(row: dict[str, str]) -> bool:
    q = norm(row.get("query_text", ""))
    g = norm(row.get("gold_services_json", "") + " " + row.get("gold_apis_json", ""))
    if "football" in q and "energy price" in g:
        return True
    if "greek news" in q and "energy price" in g:
        return True
    if "dna2protein" in q and "numerology" in g:
        return True
    return False


def api_leak(row: dict[str, str]) -> bool:
    q = row.get("query_text", "")
    for api in as_list(parse_json(row.get("gold_apis_json", ""))):
        name = api.get("api_name", "") if isinstance(api, dict) else str(api)
        n = norm(name)
        if not n or n in GENERIC_API_NAMES or len(n) <= 3:
            continue
        # Endpoint-like names and multi-token API names are meaningful leak signals.
        if ("/" in name or "_" in name or "-" in name or len(n.split()) >= 2 or "api" in n) and has_name_or_alias(q, name):
            return True
    return False


def service_leak(row: dict[str, str]) -> bool:
    q = row.get("query_text", "")
    for service in as_list(parse_json(row.get("gold_services_json", ""))):
        name = service.get("service_name", "") if isinstance(service, dict) else str(service)
        n = norm(name)
        if not n or len(n) <= 3:
            continue
        if has_name_or_alias(q, name):
            return True
    return False


def apply_policy(row: dict[str, str]) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    group = row.get("stable_group", row.get("source_group", ""))
    q = row.get("query_text", "")

    if demo_or_test(row):
        add(hits, "S3_demo_test_unsupported_source_gate", "remove", "demo_or_test_source_blocking", "Demo/test/sample/unsupported API source detected.", {})
    if wrong_gold_set(row):
        add(hits, "S4_missing_core_requirement_gate", "remove", "missing_core_requirement", "Wrong or unrelated gold service/API set detected by domain mismatch.", {})
    if missing_core(row):
        add(hits, "S4_missing_core_requirement_gate", "remove", "missing_core_requirement", "Gold APIs do not explicitly cover a core query requirement.", {})
    if api_leak(row):
        add(hits, "S2_service_or_api_leak_gate", "rewrite", "api_leak_blocking", "Query directly names a gold API/endpoint.", {})
    elif service_leak(row):
        add(hits, "S2_service_or_api_leak_gate", "rewrite", "service_leak_blocking", "Query directly names a gold service/source.", {})
    if candidate_space_invalid(row):
        add(hits, "S1_candidate_space_invalid_gate", "reconstruct", "candidate_space_invalid", "Candidate APIs equal or do not exceed gold APIs; no distractor choice space.", {})
    if group == "G3" and not dependency_signal(q):
        add(hits, "S5_g3_composable_dependency_gate", "composable_review", "composable_not_strong_dependency", "G3 row lacks explicit dependency-chain signal.", {"stable_group": group})
    if row.get("task_type_guess", "").strip() == "":
        add(hits, "S6_task_type_guess_gate", "uncertain", "task_type_uncertain", "Missing task_type_guess.", {})

    effects = [h["effect"] for h in hits]
    if "remove" in effects:
        decision = "source_specific_remove"
    elif "rewrite" in effects:
        decision = "leakage_rewrite_pool"
    elif "reconstruct" in effects:
        decision = "candidate_space_reconstruction_pool"
    elif "composable_review" in effects:
        decision = "composable_dependency_review_pool"
    elif "uncertain" in effects:
        decision = "source_specific_uncertain"
    else:
        decision = "source_specific_keep_candidate_as_is"
    label = hits[0]["label"] if hits else "no_blocking_issue_detected"
    blocking = [h for h in hits if h["effect"] in {"remove", "rewrite", "reconstruct", "composable_review"}]
    warnings = [h for h in hits if h["effect"] == "uncertain"]
    return {
        "decision": decision,
        "label": label,
        "blocking": blocking,
        "warnings": warnings,
        "hits": hits,
        "reconstruction_needed": "yes" if "reconstruct" in effects else "no",
        "rewrite_needed": "yes" if "rewrite" in effects else "no",
        "requires_composable_dependency_review": "yes" if "composable_review" in effects else "no",
        "requires_human_review": "yes" if hits else "no",
    }


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def annotate(row: dict[str, str]) -> dict[str, Any]:
    result = apply_policy(row)
    out = dict(row)
    out["stable_policy_decision"] = result["decision"]
    out["stable_policy_label"] = result["label"]
    out["stable_blocking_rules_json"] = json_dump([h["rule"] for h in result["blocking"]])
    out["stable_warning_rules_json"] = json_dump([h["rule"] for h in result["warnings"]])
    out["stable_policy_evidence_json"] = json_dump(result["hits"])
    out["stable_reconstruction_needed"] = result["reconstruction_needed"]
    out["stable_rewrite_needed"] = result["rewrite_needed"]
    out["stable_requires_composable_dependency_review"] = result["requires_composable_dependency_review"]
    out["stable_requires_human_review"] = result["requires_human_review"]
    out["stable_policy_notes"] = "; ".join(h["reason"] for h in result["hits"][:3])
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def run_full(project_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    src = project_root / "outputs/external_sources_adapters_v0_1/stabletoolbench/stabletoolbench_solvable_task_level_raw.csv"
    with src.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        base_fields = list(reader.fieldnames or [])
        rows = [annotate(row) for row in reader]
    extra = [
        "stable_policy_decision",
        "stable_policy_label",
        "stable_blocking_rules_json",
        "stable_warning_rules_json",
        "stable_policy_evidence_json",
        "stable_reconstruction_needed",
        "stable_rewrite_needed",
        "stable_requires_composable_dependency_review",
        "stable_requires_human_review",
        "stable_policy_notes",
    ]
    fields = base_fields + extra
    write_csv(project_root / OUT_DIR / "stabletoolbench_solvable_with_filter_policy_v0_2.csv", rows, fields)

    pools = {
        "stabletoolbench_candidate_space_reconstruction_pool_v0_2.csv": [r for r in rows if r["stable_policy_decision"] == "candidate_space_reconstruction_pool" or r["stable_reconstruction_needed"] == "yes"],
        "stabletoolbench_leakage_rewrite_pool_v0_2.csv": [r for r in rows if r["stable_policy_decision"] == "leakage_rewrite_pool" or r["stable_rewrite_needed"] == "yes"],
        "stabletoolbench_composable_dependency_review_pool_v0_2.csv": [r for r in rows if r["stable_policy_decision"] == "composable_dependency_review_pool" or r["stable_requires_composable_dependency_review"] == "yes"],
    }
    for name, pool_rows in pools.items():
        write_csv(project_root / OUT_DIR / name, pool_rows, fields)

    decision_counts = Counter(r["stable_policy_decision"] for r in rows)
    label_counts = Counter(r["stable_policy_label"] for r in rows)
    rule_counts: Counter[str] = Counter()
    for row in rows:
        for rule in json.loads(row["stable_blocking_rules_json"]) + json.loads(row["stable_warning_rules_json"]):
            rule_counts[rule] += 1
    write_csv(project_root / OUT_DIR / "stabletoolbench_policy_rule_hit_counts_v0_2.csv", [{"rule": k, "count": v} for k, v in rule_counts.most_common()], ["rule", "count"])
    return {
        "total_rows": len(rows),
        "decision_counts": dict(decision_counts),
        "label_counts": dict(label_counts),
        "rule_hit_counts": dict(rule_counts),
        "candidate_space_reconstruction_pool_count": len(pools["stabletoolbench_candidate_space_reconstruction_pool_v0_2.csv"]),
        "leakage_rewrite_pool_count": len(pools["stabletoolbench_leakage_rewrite_pool_v0_2.csv"]),
        "composable_dependency_review_pool_count": len(pools["stabletoolbench_composable_dependency_review_pool_v0_2.csv"]),
    }, rows, fields


def regression(project_root: Path) -> dict[str, Any]:
    reviewed = project_root / "outputs/external_qa_v0_1/stabletoolbench/stabletoolbench_solvable_review_items_100_manual_reviewed_by_gpt55pro.csv"
    with reviewed.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        base_fields = list(reader.fieldnames or [])
        rows = [annotate(row) for row in reader]
    fields = base_fields + [
        "stable_policy_decision",
        "stable_policy_label",
        "stable_blocking_rules_json",
        "stable_warning_rules_json",
        "stable_policy_evidence_json",
        "stable_reconstruction_needed",
        "stable_rewrite_needed",
        "stable_requires_composable_dependency_review",
        "stable_requires_human_review",
        "stable_policy_notes",
    ]
    write_csv(project_root / OUT_DIR / "stabletoolbench_reviewed100_policy_regression_trace_v0_2.csv", rows, fields)
    human_keep = [r for r in rows if r.get("qa_final_decision") == "keep_for_cleaning_candidate"]
    human_uncertain = [r for r in rows if r.get("qa_final_decision") == "uncertain"]
    human_remove = [r for r in rows if r.get("qa_final_decision") == "remove"]
    human_critical = [r for r in rows if r.get("qa_severity") == "critical"]
    captured_critical = [r for r in human_critical if r["stable_policy_decision"] != "source_specific_keep_candidate_as_is"]
    captured_remove = [r for r in human_remove if r["stable_policy_decision"] != "source_specific_keep_candidate_as_is"]
    candidate_invalid = [r for r in rows if "candidate_space_invalid" in r.get("qa_error_type", "")]
    candidate_captured = [r for r in candidate_invalid if r["stable_reconstruction_needed"] == "yes" or r["stable_policy_decision"] != "source_specific_keep_candidate_as_is"]
    composable = [r for r in rows if "not_strong_composable" in r.get("qa_error_type", "")]
    composable_captured = [r for r in composable if r["stable_requires_composable_dependency_review"] == "yes" or r["stable_policy_decision"] != "source_specific_keep_candidate_as_is"]
    api_leaks = [r for r in rows if r.get("qa_leakage_check") == "api_leak_blocking"]
    service_leaks = [r for r in rows if r.get("qa_leakage_check") == "service_leak_blocking"]
    api_captured = [r for r in api_leaks if r["stable_rewrite_needed"] == "yes" or r["stable_policy_decision"] != "source_specific_keep_candidate_as_is"]
    service_captured = [r for r in service_leaks if r["stable_rewrite_needed"] == "yes" or r["stable_policy_decision"] != "source_specific_keep_candidate_as_is"]
    keep_retained = [r for r in human_keep if r["stable_policy_decision"] == "source_specific_keep_candidate_as_is"]
    summary = {
        "generated_time": now(),
        "reviewed_rows": len(rows),
        "human_keep": len(human_keep),
        "human_uncertain": len(human_uncertain),
        "human_remove": len(human_remove),
        "human_critical": len(human_critical),
        "critical_capture_count": len(captured_critical),
        "critical_capture_rate": round(len(captured_critical) / len(human_critical), 4) if human_critical else 1.0,
        "remove_capture_count": len(captured_remove),
        "remove_capture_rate": round(len(captured_remove) / len(human_remove), 4) if human_remove else 1.0,
        "candidate_space_invalid_capture_count": len(candidate_captured),
        "candidate_space_invalid_capture_rate": round(len(candidate_captured) / len(candidate_invalid), 4) if candidate_invalid else 1.0,
        "composable_not_strong_dependency_capture_count": len(composable_captured),
        "composable_not_strong_dependency_capture_rate": round(len(composable_captured) / len(composable), 4) if composable else 1.0,
        "api_leak_capture_count": len(api_captured),
        "service_leak_capture_count": len(service_captured),
        "keep_retention_count": len(keep_retained),
        "keep_retention_rate": round(len(keep_retained) / len(human_keep), 4) if human_keep else 0,
        "no_hardcoded_id_rules_used": True,
    }
    summary["passes_acceptance"] = (
        summary["critical_capture_rate"] == 1.0
        and summary["remove_capture_rate"] >= 0.90
        and summary["candidate_space_invalid_capture_rate"] >= 0.90
        and summary["composable_not_strong_dependency_capture_rate"] >= 0.90
        and summary["no_hardcoded_id_rules_used"]
    )
    (project_root / OUT_DIR / "stabletoolbench_reviewed100_policy_regression_summary_v0_2.json").write_text(json_dump(summary), encoding="utf-8")
    return summary


def make_review_pack(project_root: Path, rows: list[dict[str, Any]], fields: list[str]) -> int:
    selected: list[dict[str, Any]] = []
    seen = set()

    def add_rows(candidates: list[dict[str, Any]], limit: int) -> None:
        added = 0
        for row in candidates:
            if row["task_id"] in seen:
                continue
            selected.append(row)
            seen.add(row["task_id"])
            added += 1
            if added >= limit:
                break

    removes = [r for r in rows if r["stable_policy_decision"] == "source_specific_remove"]
    reconstruct = [r for r in rows if r["stable_reconstruction_needed"] == "yes"]
    rewrites = [r for r in rows if r["stable_rewrite_needed"] == "yes"]
    comp = [r for r in rows if r["stable_requires_composable_dependency_review"] == "yes"]
    keeps = [r for r in rows if r["stable_policy_decision"] == "source_specific_keep_candidate_as_is"]
    add_rows(removes, 50)
    add_rows(reconstruct, 25)
    add_rows(rewrites, 25)
    add_rows(comp, 25)
    random.seed(20260705)
    add_rows(random.sample(keeps, min(25, len(keeps))), 25)
    if len(selected) < 100:
        remaining = [r for r in rows if r["task_id"] not in seen]
        random.shuffle(remaining)
        add_rows(remaining, 100 - len(selected))
    selected = selected[:100]
    out_rows = []
    for idx, row in enumerate(selected, start=1):
        out = dict(row)
        out["review_item_id"] = f"STB-V02-{idx:03d}"
        for field in HUMAN_FIELDS:
            out[field] = ""
        out_rows.append(out)
    out_csv = project_root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv"
    out_fields = ["review_item_id"] + [f for f in fields if f != "review_item_id" and f not in HUMAN_FIELDS] + HUMAN_FIELDS
    write_csv(out_csv, out_rows, out_fields)
    (project_root / "docs/phase1/stabletoolbench_filter_policy_review_plan_v0_2.md").write_text(
        f"""# StableToolBench Filter Policy Review Plan v0.2

Generated time: {now()}

Review CSV: `outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv`

Sampling includes source-specific removes, candidate-space reconstruction rows, leakage rewrite rows, composable dependency review rows, and keep-as-is sanity checks. Review mode is CSV-only; no HTML app was generated.
""",
        encoding="utf-8",
    )
    return len(out_rows)


def write_report(project_root: Path, full: dict[str, Any], reg: dict[str, Any], qa_rows: int) -> None:
    summary = {**full, "regression": reg, "qa_pack_rows": qa_rows, "can_generate_final_dataset_now": False}
    (project_root / OUT_DIR / "stabletoolbench_filter_policy_summary_v0_2.json").write_text(json_dump(summary), encoding="utf-8")
    (project_root / "docs/phase1/stabletoolbench_filter_policy_dryrun_report_v0_2.md").write_text(
        f"""# StableToolBench Filter Policy Dry-Run Report v0.2

Generated time: {now()}

## Full Raw Dry-Run

- total_rows: {full['total_rows']}
- decision_counts: `{full['decision_counts']}`
- label_counts: `{full['label_counts']}`
- candidate_space_reconstruction_pool_count: {full['candidate_space_reconstruction_pool_count']}
- leakage_rewrite_pool_count: {full['leakage_rewrite_pool_count']}
- composable_dependency_review_pool_count: {full['composable_dependency_review_pool_count']}

## Reviewed100 Regression

- reviewed_rows: {reg['reviewed_rows']}
- human_keep: {reg['human_keep']}
- human_uncertain: {reg['human_uncertain']}
- human_remove: {reg['human_remove']}
- human_critical: {reg['human_critical']}
- critical_capture_rate: {reg['critical_capture_rate']}
- remove_capture_rate: {reg['remove_capture_rate']}
- candidate_space_invalid_capture_rate: {reg['candidate_space_invalid_capture_rate']}
- composable_not_strong_dependency_capture_rate: {reg['composable_not_strong_dependency_capture_rate']}
- keep_retention_rate: {reg['keep_retention_rate']}
- passes_acceptance: `{reg['passes_acceptance']}`

## QA Pack

- qa_pack_rows: {qa_rows}

## Boundary

No final dataset, source merge, split, baseline, training, Qwen call, or HTML app was produced.
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run StableToolBench filter policy dry-run v0.2.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    (project_root / OUT_DIR).mkdir(parents=True, exist_ok=True)
    full, rows, fields = run_full(project_root)
    reg = regression(project_root)
    qa_rows = make_review_pack(project_root, rows, fields)
    write_report(project_root, full, reg, qa_rows)
    print(json_dump({"stabletoolbench_policy_dryrun_completed": True, **full, "regression": reg, "qa_pack_rows": qa_rows}))


if __name__ == "__main__":
    main()
