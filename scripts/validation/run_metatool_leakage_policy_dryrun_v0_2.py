#!/usr/bin/env python
"""Run MetaTool leakage/rewrite policy dry-run v0.2.

This is source-specific annotation only. It does not rewrite queries, merge
sources, generate final datasets, split, baseline, train, or call any API.
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


OUT_DIR = Path("outputs/external_source_policy_v0_2/metatool")
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
COMMON_SERVICE_WORDS = {
    "search",
    "review",
    "weather",
    "translate",
    "translation",
    "calculator",
    "writer",
    "assistant",
    "tool",
    "data",
    "news",
    "image",
    "video",
    "music",
    "finance",
    "product",
    "job",
    "trip",
    "local",
    "discount",
    "discounts",
    "coupon",
    "coupons",
    "deal",
    "deals",
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def tokens(text: str) -> list[str]:
    return [t for t in norm(text).split() if t]


def has_phrase(query: str, service: str) -> bool:
    q = norm(query)
    s = norm(service)
    if not s:
        return False
    if s in q:
        return True
    return compact(service) and compact(service) in compact(query)


def service_is_common(service: str) -> bool:
    ts = tokens(service)
    return bool(ts) and all(t in COMMON_SERVICE_WORDS or len(t) <= 2 for t in ts)


def service_named_as_source(query: str, service: str) -> bool:
    if not has_phrase(query, service):
        return False
    s = re.escape(norm(service))
    q = norm(query)
    patterns = [
        rf"(use|using|ask|from|according to|with|via|through|on|in) {s}",
        rf"{s} (api|tool|plugin|service|website|platform|agent|source)",
        rf"(api|tool|plugin|service|website|platform|agent|source) {s}",
    ]
    return any(re.search(p, q) for p in patterns)


def missing_context(query: str) -> bool:
    q = norm(query)
    phrases = [
        "above",
        "previous",
        "earlier",
        "linked above",
        "this link",
        "that link",
        "this product",
        "that product",
        "attached",
        "uploaded",
        "the document",
        "the file",
    ]
    return any(p in q for p in phrases)


def semantic_mismatch(query: str, service: str, gold_json: str) -> bool:
    q = norm(query)
    g = norm(gold_json + " " + service)
    # Website/about-source queries are not necessarily capability requests.
    if has_phrase(query, service) and any(p in q for p in ["know more about", "tell me about", "what is", "link to", "learn about"]):
        return True
    if "link to" in q and "recommend" in g:
        return True
    return False


def add(hits: list[dict[str, Any]], rule: str, effect: str, label: str, reason: str, evidence: dict[str, Any]) -> None:
    hits.append({"rule": rule, "effect": effect, "label": label, "reason": reason, "evidence": evidence})


def apply_policy(row: dict[str, str]) -> dict[str, Any]:
    query = row.get("query_text", "")
    service = row.get("source_tool_or_plugin_name", "")
    gold_json = row.get("gold_services_json", "")
    adapter = " ".join([row.get("adapter_notes", ""), row.get("adapter_warnings", ""), row.get("gold_service_unmatched", "")]).lower()
    hits: list[dict[str, Any]] = []

    if "unmatched" in adapter and "no" not in adapter:
        add(hits, "M6_candidate_catalog_validity", "remove", "adapter_warning_blocking", "Gold service is unmatched or adapter warning blocks catalog validity.", {"adapter": adapter[:300]})

    if missing_context(query):
        add(hits, "M4_missing_context_for_standalone_query", "remove", "missing_context_blocking", "Query depends on missing external context and is not standalone.", {"query_fragment": query[:240]})

    if semantic_mismatch(query, service, gold_json):
        add(hits, "M5_semantic_alignment_gate", "remove", "semantic_mismatch_blocking", "Query appears to ask about/navigate to the service itself rather than requesting the service capability.", {"service": service})

    if has_phrase(query, service):
        if service_named_as_source(query, service) and not service_is_common(service):
            add(hits, "M3_source_or_platform_name_leak", "rewrite", "service_leak_blocking", "Query explicitly names the gold service/source/plugin as the requested source or agent.", {"service": service})
        elif service_is_common(service):
            add(hits, "M2_partial_or_common_word_overlap_not_automatic_blocking", "keep_warning", "leak_uncertain", "Gold service name is mostly common words; overlap is tracked but not an automatic hard leak.", {"service": service})
        else:
            add(hits, "M1_exact_gold_service_name_leak_blocking", "rewrite", "service_leak_blocking", "Query contains the exact gold service/plugin name.", {"service": service})
    else:
        service_tokens = [t for t in tokens(service) if t not in COMMON_SERVICE_WORDS and len(t) > 2]
        overlap = [t for t in service_tokens if re.search(rf"\b{re.escape(t)}\b", norm(query))]
        if overlap:
            add(hits, "M2_partial_or_common_word_overlap_not_automatic_blocking", "keep_warning", "leak_uncertain", "Query overlaps non-unique parts of service name; tracked as a leakage hint, not a hard block.", {"overlap": overlap[:5], "service": service})

    effects = {h["effect"] for h in hits}
    if "remove" in effects:
        decision = "source_specific_remove"
    elif "rewrite" in effects:
        decision = "rewrite_pool_only"
    elif "uncertain" in effects:
        decision = "source_specific_uncertain"
    else:
        decision = "source_specific_keep_candidate"

    blocking = [h for h in hits if h["effect"] in {"remove", "rewrite"}]
    warnings = [h for h in hits if h["effect"] in {"uncertain", "keep_warning"}]
    label = "no_obvious_leak"
    if blocking:
        label = blocking[0]["label"]
    elif warnings:
        label = warnings[0]["label"]
    return {
        "decision": decision,
        "label": label,
        "blocking": blocking,
        "warnings": warnings,
        "hits": hits,
        "rewrite_needed": "yes" if decision == "rewrite_pool_only" else "no",
        "rewrite_reason": "; ".join(h["reason"] for h in blocking if h["effect"] == "rewrite"),
        "requires_human_review": "yes" if decision != "source_specific_keep_candidate" else "no",
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def annotate_row(row: dict[str, str]) -> dict[str, Any]:
    result = apply_policy(row)
    out = dict(row)
    out["metatool_policy_decision"] = result["decision"]
    out["metatool_leakage_policy_label"] = result["label"]
    out["metatool_blocking_rules_json"] = json_dump([h["rule"] for h in result["blocking"]])
    out["metatool_warning_rules_json"] = json_dump([h["rule"] for h in result["warnings"]])
    out["metatool_policy_evidence_json"] = json_dump(result["hits"])
    out["metatool_rewrite_needed"] = result["rewrite_needed"]
    out["metatool_rewrite_reason"] = result["rewrite_reason"]
    out["metatool_requires_human_review"] = result["requires_human_review"]
    out["metatool_policy_notes"] = "; ".join(h["reason"] for h in result["hits"][:3])
    return out


def run_full(project_root: Path) -> dict[str, Any]:
    src = project_root / "outputs/external_sources_adapters_v0_1/metatool/metatool_single_service_task_level_raw.csv"
    out_csv = project_root / OUT_DIR / "metatool_single_service_with_leakage_policy_v0_2.csv"
    rewrite_csv = project_root / OUT_DIR / "metatool_rewrite_candidate_pool_v0_2.csv"
    rule_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    rewrite_rows: list[dict[str, Any]] = []
    fieldnames: list[str] | None = None
    extra = [
        "metatool_policy_decision",
        "metatool_leakage_policy_label",
        "metatool_blocking_rules_json",
        "metatool_warning_rules_json",
        "metatool_policy_evidence_json",
        "metatool_rewrite_needed",
        "metatool_rewrite_reason",
        "metatool_requires_human_review",
        "metatool_policy_notes",
    ]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with src.open("r", encoding="utf-8-sig", newline="") as f_in, out_csv.open("w", encoding="utf-8-sig", newline="") as f_out:
        reader = csv.DictReader(f_in)
        fieldnames = list(reader.fieldnames or []) + extra
        writer = csv.DictWriter(f_out, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for row in reader:
            annotated = annotate_row(row)
            writer.writerow(annotated)
            decision_counts[annotated["metatool_policy_decision"]] += 1
            label_counts[annotated["metatool_leakage_policy_label"]] += 1
            for rule in json.loads(annotated["metatool_blocking_rules_json"]) + json.loads(annotated["metatool_warning_rules_json"]):
                rule_counts[rule] += 1
            if annotated["metatool_rewrite_needed"] == "yes":
                rewrite_rows.append(annotated)

    if rewrite_rows:
        write_csv(rewrite_csv, rewrite_rows, fieldnames or list(rewrite_rows[0].keys()))
    else:
        write_csv(rewrite_csv, [], fieldnames or [])
    write_csv(project_root / OUT_DIR / "metatool_leakage_policy_rule_hit_counts_v0_2.csv", [{"rule": k, "count": v} for k, v in rule_counts.most_common()], ["rule", "count"])
    return {
        "total_rows": sum(decision_counts.values()),
        "decision_counts": dict(decision_counts),
        "label_counts": dict(label_counts),
        "rule_hit_counts": dict(rule_counts),
        "rewrite_pool_count": len(rewrite_rows),
    }


def regression(project_root: Path) -> dict[str, Any]:
    reviewed = project_root / "outputs/external_qa_v0_1/metatool/metatool_single_service_review_items_100_manual_reviewed_by_gpt55pro.csv"
    rows = []
    with reviewed.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        base_fields = list(reader.fieldnames or [])
        for row in reader:
            rows.append(annotate_row(row))
    out_trace = project_root / OUT_DIR / "metatool_reviewed100_policy_regression_trace_v0_2.csv"
    fields = base_fields + [
        "metatool_policy_decision",
        "metatool_leakage_policy_label",
        "metatool_blocking_rules_json",
        "metatool_warning_rules_json",
        "metatool_policy_evidence_json",
        "metatool_rewrite_needed",
        "metatool_rewrite_reason",
        "metatool_requires_human_review",
        "metatool_policy_notes",
    ]
    write_csv(out_trace, rows, fields)
    human_keep = [r for r in rows if r.get("qa_final_decision") == "keep_for_cleaning_candidate"]
    human_remove = [r for r in rows if r.get("qa_final_decision") == "remove"]
    human_uncertain = [r for r in rows if r.get("qa_final_decision") == "uncertain"]
    human_critical = [r for r in rows if r.get("qa_severity") == "critical"]
    service_leak = [r for r in rows if r.get("qa_leakage_check") == "service_leak_blocking"]
    leak_uncertain = [r for r in rows if r.get("qa_leakage_check") == "leak_uncertain"]
    service_captured = [r for r in service_leak if r["metatool_policy_decision"] in {"rewrite_pool_only", "source_specific_remove"}]
    remove_captured = [r for r in human_remove if r["metatool_policy_decision"] in {"rewrite_pool_only", "source_specific_remove"}]
    keep_retained = [r for r in human_keep if r["metatool_policy_decision"] == "source_specific_keep_candidate"]
    leak_unc_after = [r for r in leak_uncertain if r["metatool_leakage_policy_label"] == "leak_uncertain"]
    summary = {
        "generated_time": now(),
        "reviewed_rows": len(rows),
        "human_keep": len(human_keep),
        "human_uncertain": len(human_uncertain),
        "human_remove": len(human_remove),
        "human_critical": len(human_critical),
        "service_leak_blocking_rows": len(service_leak),
        "service_leak_blocking_captured_count": len(service_captured),
        "service_leak_blocking_capture_rate": round(len(service_captured) / len(service_leak), 4) if service_leak else 1.0,
        "remove_capture_count": len(remove_captured),
        "remove_capture_rate": round(len(remove_captured) / len(human_remove), 4) if human_remove else 1.0,
        "keep_retention_count": len(keep_retained),
        "keep_retention_rate": round(len(keep_retained) / len(human_keep), 4) if human_keep else 0,
        "leak_uncertain_rows": len(leak_uncertain),
        "leak_uncertain_after_policy_count": len(leak_unc_after),
        "no_hardcoded_id_rules_used": True,
    }
    summary["passes_acceptance"] = (
        summary["service_leak_blocking_capture_rate"] == 1.0
        and summary["human_critical"] == 0
        and summary["remove_capture_rate"] >= 0.80
        and summary["keep_retention_rate"] >= 0.70
        and summary["no_hardcoded_id_rules_used"]
    )
    (project_root / OUT_DIR / "metatool_reviewed100_policy_regression_summary_v0_2.json").write_text(json_dump(summary), encoding="utf-8")
    return summary


def make_review_pack(project_root: Path) -> int:
    annotated = project_root / OUT_DIR / "metatool_single_service_with_leakage_policy_v0_2.csv"
    rows: list[dict[str, str]] = []
    with annotated.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    selected: list[dict[str, str]] = []
    seen = set()

    def add_rows(candidates: list[dict[str, str]], limit: int) -> None:
        added = 0
        for row in candidates:
            if row["task_id"] in seen:
                continue
            selected.append(row)
            seen.add(row["task_id"])
            added += 1
            if added >= limit:
                break

    service_leaks = [r for r in rows if r["metatool_leakage_policy_label"] == "service_leak_blocking"]
    removes = [r for r in rows if r["metatool_policy_decision"] == "source_specific_remove"]
    rewrites = [r for r in rows if r["metatool_policy_decision"] == "rewrite_pool_only"]
    uncertain = [r for r in rows if r["metatool_policy_decision"] == "source_specific_uncertain"]
    keeps = [r for r in rows if r["metatool_policy_decision"] == "source_specific_keep_candidate"]
    add_rows(service_leaks, 30)
    add_rows(removes, 20)
    add_rows(rewrites, 30)
    random.seed(20260705)
    add_rows(random.sample(uncertain, min(20, len(uncertain))), 20)
    add_rows(random.sample(keeps, min(30, len(keeps))), 30)
    if len(selected) < 100:
        remaining = [r for r in rows if r["task_id"] not in seen]
        random.shuffle(remaining)
        add_rows(remaining, 100 - len(selected))
    selected = selected[:100]
    out_rows = []
    for idx, row in enumerate(selected, start=1):
        out = dict(row)
        out["review_item_id"] = f"MT-V02-{idx:03d}"
        for field in HUMAN_FIELDS:
            out[field] = ""
        out_rows.append(out)
    out_csv = project_root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv"
    fields = ["review_item_id"] + [f for f in out_rows[0].keys() if f != "review_item_id" and f not in HUMAN_FIELDS] + HUMAN_FIELDS
    write_csv(out_csv, out_rows, fields)
    plan = project_root / "docs/phase1/metatool_leakage_policy_review_plan_v0_2.md"
    plan.write_text(
        f"""# MetaTool Leakage Policy Review Plan v0.2

Generated time: {now()}

Review CSV: `outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv`

Sampling includes service-leak blocking, source-specific removes, rewrite-pool rows, leak-uncertain rows, and random keep candidates. Review mode is CSV-only; no HTML app was generated.
""",
        encoding="utf-8",
    )
    return len(out_rows)


def write_report(project_root: Path, full: dict[str, Any], reg: dict[str, Any], qa_rows: int) -> None:
    summary = {**full, "regression": reg, "qa_pack_rows": qa_rows, "can_generate_final_dataset_now": False}
    (project_root / OUT_DIR / "metatool_leakage_policy_summary_v0_2.json").write_text(json_dump(summary), encoding="utf-8")
    md = project_root / "docs/phase1/metatool_leakage_policy_dryrun_report_v0_2.md"
    md.write_text(
        f"""# MetaTool Leakage Policy Dry-Run Report v0.2

Generated time: {now()}

## Full Raw Dry-Run

- total_rows: {full['total_rows']}
- decision_counts: `{full['decision_counts']}`
- label_counts: `{full['label_counts']}`
- rewrite_pool_count: {full['rewrite_pool_count']}

## Reviewed100 Regression

- reviewed_rows: {reg['reviewed_rows']}
- human_keep: {reg['human_keep']}
- human_uncertain: {reg['human_uncertain']}
- human_remove: {reg['human_remove']}
- human_critical: {reg['human_critical']}
- service_leak_blocking_capture_rate: {reg['service_leak_blocking_capture_rate']}
- remove_capture_rate: {reg['remove_capture_rate']}
- keep_retention_rate: {reg['keep_retention_rate']}
- leak_uncertain_rows: {reg['leak_uncertain_rows']}
- leak_uncertain_after_policy_count: {reg['leak_uncertain_after_policy_count']}
- passes_acceptance: `{reg['passes_acceptance']}`

## QA Pack

- qa_pack_rows: {qa_rows}

## Boundary

No query rewrite, final dataset, source merge, split, baseline, training, Qwen call, or HTML app was produced.
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MetaTool leakage policy dry-run v0.2.")
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    OUT_DIR_ABS = project_root / OUT_DIR
    OUT_DIR_ABS.mkdir(parents=True, exist_ok=True)
    full = run_full(project_root)
    reg = regression(project_root)
    qa_rows = make_review_pack(project_root)
    write_report(project_root, full, reg, qa_rows)
    print(json_dump({"metatool_policy_dryrun_completed": True, **full, "regression": reg, "qa_pack_rows": qa_rows}))


if __name__ == "__main__":
    main()
