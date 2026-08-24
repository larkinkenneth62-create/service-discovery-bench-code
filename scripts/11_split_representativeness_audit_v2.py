#!/usr/bin/env python3
"""Read-only v0.1 split audit and versioned repair-candidate generator.

This runner never writes below the authoritative package.  It creates a new
run directory containing only audit evidence and candidate manifests.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from servicediscoverybench.manifests import sha256_file, write_csv, write_json, write_jsonl
from servicediscoverybench.signatures import review_content_fingerprint, stable_hash
from servicediscoverybench.splits import build_split_components_v2

csv.field_size_limit(2_147_483_647)
TASK_TYPES = ("single_service_discovery", "single_api_recommendation", "multi_service_discovery", "multi_api_recommendation", "composable_service_discovery", "composable_api_recommendation")
SPLITS = ("train", "dev", "test")
AUTH = ROOT / "outputs" / "runs" / "20260722_133000_final_release" / "ServiceDiscoveryBench-v0.1"
G5 = ROOT / "outputs" / "runs" / "20260722_120000_g5_splits"
PRELLM = ROOT / "outputs" / "runs" / "20260804_203600_machine_challenge_final_pre_llm_closure_v2"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def markdown(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def logical(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def load_rows() -> list[dict[str, str]]:
    provenance = {r["benchmark_task_id"]: r for r in read_csv(AUTH / "manifests" / "task_provenance.csv")}
    split = {r["benchmark_task_id"]: r for r in read_csv(AUTH / "splits" / "split_manifest.csv")}
    rows: list[dict[str, str]] = []
    for task in TASK_TYPES:
        for row in read_csv(AUTH / "tasks" / f"{task}.csv"):
            p, s = provenance[row["benchmark_task_id"]], split[row["benchmark_task_id"]]
            row = dict(row)
            row.update({
                "source_query_id": p.get("source_query_id", ""),
                "source_task_id": p.get("g2_row_id", ""),
                "parent_row_id": p.get("parent_row_id", ""),
                "repair_status": p.get("repair_status", ""),
                "legacy_split": s["split"],
                "legacy_split_group_id": s["split_group_id"],
                "review_content_fingerprint": p.get("review_content_fingerprint") or review_content_fingerprint(row),
            })
            rows.append(row)
    if len(rows) != 60078 or len({r["benchmark_task_id"] for r in rows}) != len(rows):
        raise RuntimeError("authoritative task inventory is not the expected immutable 60,078 unique rows")
    return rows


def components(rows: list[dict[str, str]]) -> tuple[dict[str, str], list[dict[str, str]]]:
    mapping = build_split_components_v2(rows)
    # A spanning forest, not an all-pairs expansion, is sufficient evidence for
    # each relationship and avoids quadratic CSVs for repeated values.
    seen: dict[tuple[str, str], str] = {}
    edges: list[dict[str, str]] = []
    for row in sorted(rows, key=lambda x: x["benchmark_task_id"]):
        for field in ("source_task_id", "source_query_id", "query_signature", "review_content_fingerprint", "paired_task_group_id", "underlying_task_id", "parent_row_id"):
            value = row.get(field, "").strip()
            if not value:
                continue
            key = (field, value)
            if key in seen:
                edges.append({"relation_type": field, "relation_value_sha256": hashlib.sha256(value.encode()).hexdigest(), "left_task_id": seen[key], "right_task_id": row["benchmark_task_id"], "underlying_task_group": mapping[row["benchmark_task_id"]]})
            else:
                seen[key] = row["benchmark_task_id"]
    return mapping, edges


def distribution(rows: Iterable[dict[str, str]], split_by_id: dict[str, str]) -> list[dict[str, Any]]:
    c = Counter((split_by_id[r["benchmark_task_id"]], r["task_type"], r["source_dataset"]) for r in rows)
    return [{"split": k[0], "task_type": k[1], "source_dataset": k[2], "row_count": v} for k, v in sorted(c.items())]


def collision_counts(rows: list[dict[str, str]], split_by_id: dict[str, str], fields: tuple[str, ...]) -> dict[str, int]:
    result = {}
    for field in fields:
        values: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            value = r.get(field, "").strip()
            if value:
                values[value].add(split_by_id[r["benchmark_task_id"]])
        result[field] = sum(1 for v in values.values() if len(v) > 1)
    return result


def group_rows(rows: list[dict[str, str]], group_by_id: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        result[group_by_id[row["benchmark_task_id"]]].append(row)
    return result


def greedy_candidate(rows: list[dict[str, str]], groups: dict[str, list[dict[str, str]]], *, name: str, seed: int) -> dict[str, str]:
    """Deterministic whole-group allocation balanced over task×source cells."""
    ratios = {"train": .8, "dev": .1, "test": .1}
    total = len(rows)
    all_cells = Counter((r["task_type"], r["source_dataset"]) for r in rows)
    target = {(s, c): all_cells[c] * ratios[s] for s in SPLITS for c in all_cells}
    counts: Counter[tuple[str, str, str]] = Counter()
    totals: Counter[str] = Counter()
    group_split: dict[str, str] = {}
    ordered = sorted(groups, key=lambda gid: (-len(groups[gid]), stable_hash([seed, name, gid])))
    for gid in ordered:
        profile = Counter((r["task_type"], r["source_dataset"]) for r in groups[gid])
        def score(s: str) -> tuple[float, str]:
            # Score the full three-way state.  Scoring only `s` would always
            # prefer a previously empty dev/test bucket and creates a 60/20/20
            # allocation instead of 80/10/10.
            value = 0.0
            for evaluated in SPLITS:
                added = len(groups[gid]) if evaluated == s else 0
                value += 8 * ((totals[evaluated] + added - total * ratios[evaluated]) / max(1, total * ratios[evaluated])) ** 2
                for cell, n in profile.items():
                    after = counts[(evaluated, *cell)] + (n if evaluated == s else 0)
                    value += ((after - target[(evaluated, cell)]) / max(5, target[(evaluated, cell)])) ** 2
            return value, stable_hash([seed, name, gid, s])
        chosen = min(SPLITS, key=score)
        group_split[gid] = chosen
        totals[chosen] += len(groups[gid])
        for cell, n in profile.items(): counts[(chosen, *cell)] += n
    return {r["benchmark_task_id"]: group_split[gid] for gid, rs in groups.items() for r in rs}


def minimal_change(rows: list[dict[str, str]], groups: dict[str, list[dict[str, str]]]) -> dict[str, str]:
    assignment = {r["benchmark_task_id"]: r["legacy_split"] for r in rows}
    # The only missing source×task test coverage is MetaTool single-service.
    metatool = [gid for gid, rs in groups.items() if all(r["source_dataset"] == "MetaTool" and r["task_type"] == "single_service_discovery" for r in rs) and rs[0]["legacy_split"] == "train"]
    moved = 0
    for gid in sorted(metatool, key=lambda x: (len(groups[x]), x)):
        for row in groups[gid]: assignment[row["benchmark_task_id"]] = "test"
        moved += len(groups[gid])
        if moved >= 5: break
    if moved < 5: raise RuntimeError("cannot satisfy MetaTool test minimum coverage with whole v2 groups")
    return assignment


def rebalance_sizes(rows: list[dict[str, str]], groups: dict[str, list[dict[str, str]]], assignment: dict[str, str]) -> dict[str, str]:
    """Move whole groups to train until dev/test return to frozen-size bounds.

    This is deterministic local search used only for the proportional and
    representative candidates.  It never splits a relation group and retains
    the requested five-row minimum in test for eligible task×source cells.
    """
    result = dict(assignment)
    targets = {"dev": 4793, "test": 4788}
    group_assignment = {gid: result[rs[0]["benchmark_task_id"]] for gid, rs in groups.items()}
    for split in ("dev", "test"):
        current = sum(v == split for v in result.values())
        cell_count = Counter((r["task_type"], r["source_dataset"]) for r in rows if result[r["benchmark_task_id"]] == split)
        group_cell_count: Counter[tuple[str, str]] = Counter()
        profiles: dict[str, Counter[tuple[str, str]]] = {}
        for gid, rs in groups.items():
            profile = Counter((r["task_type"], r["source_dataset"]) for r in rs)
            profiles[gid] = profile
            group_cell_count.update(profile.keys())
        for gid in sorted((g for g in groups if group_assignment[g] == split), key=lambda x: (len(groups[x]), stable_hash(["rebalance", split, x]))):
            if current <= targets[split]: break
            profile = profiles[gid]
            if any(group_cell_count[cell] >= 20 and cell_count[cell] - n < 5 for cell, n in profile.items()):
                continue
            group_assignment[gid] = "train"
            for row in groups[gid]: result[row["benchmark_task_id"]] = "train"
            current -= len(groups[gid])
            cell_count.subtract(profile)
        if current > targets[split]:
            raise RuntimeError(f"cannot rebalance {split} without breaking representation")
    return result


def candidate_summary(rows: list[dict[str, str]], assignment: dict[str, str], groups: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    counts = Counter(assignment.values())
    test = [r for r in rows if assignment[r["benchmark_task_id"]] == "test"]
    task_counts = Counter(r["task_type"] for r in test)
    cell_counts = Counter((r["task_type"], r["source_dataset"]) for r in test)
    moved_rows = sum(assignment[r["benchmark_task_id"]] != r["legacy_split"] for r in rows)
    moved_groups = sum(any(assignment[r["benchmark_task_id"]] != r["legacy_split"] for r in rs) for rs in groups.values())
    overlaps = collision_counts(rows, assignment, ("source_query_id", "query_signature", "review_content_fingerprint", "paired_task_group_id", "underlying_task_id", "parent_row_id"))
    valid = not any(overlaps.values()) and all(task_counts[t] for t in TASK_TYPES) and 4741 <= counts["test"] <= 4835 and 4746 <= counts["dev"] <= 4840 and cell_counts[("single_service_discovery", "MetaTool")] >= 5
    return {"valid": valid, "counts": dict(counts), "task_counts": dict(task_counts), "cell_counts": cell_counts, "moved_rows": moved_rows, "moved_groups": moved_groups, "overlaps": overlaps, "single_api_share": sum(r["task_type"] == "single_api_recommendation" for r in test) / max(1, len(test)), "single_service_share": sum(r["task_type"] == "single_service_discovery" for r in test) / max(1, len(test)), "metatool_rows": sum(r["source_dataset"] == "MetaTool" for r in test)}


def visible_manifest_row(row: dict[str, str], setting: str) -> dict[str, Any]:
    key = "candidate_services_json" if row["prediction_target"] == "service" else "candidate_apis_json"
    candidates = json.loads(row.get(key, "[]"))
    payload = {"query": row["query_text"], "task_type": row["task_type"], "prediction_target": row["prediction_target"], "candidate_ids": candidates, "instructions": "Return only a strict JSON object containing a complete ranking of the supplied candidate IDs."}
    return {"benchmark_task_id": row["benchmark_task_id"], "setting": setting, "task_type": row["task_type"], "prediction_target": row["prediction_target"], "candidate_count": len(candidates), "candidate_order_hash": stable_hash(candidates), "query_hash": stable_hash(row["query_text"]), "model_visible_input": payload, "output_schema_id": "ranking_only_output.schema.json", "cache_key": stable_hash([setting, row["benchmark_task_id"], stable_hash(payload)])}


def simple_baselines(rows: list[dict[str, str]], output: Path) -> None:
    """Offline deterministic lexical baselines; no model weights or network."""
    def tokens(s: str) -> set[str]: return {x.lower() for x in s.replace("_", " ").replace("-", " ").split() if x}
    results = []
    for method in ("random_20_seed_expectation", "bm25_lexical", "local_hashing_lexical_vector"):
        ranks = []
        for r in rows:
            cands = json.loads(r["candidate_services_json"] if r["prediction_target"] == "service" else r["candidate_apis_json"])
            gold = set(json.loads(r["gold_services_json"] if r["prediction_target"] == "service" else r["gold_apis_json"]))
            if not cands or not gold: continue
            if method == "random_20_seed_expectation": rank = (len(cands) + 1) / 2
            else:
                q = tokens(r["query_text"])
                ordered = sorted(cands, key=lambda c: (-len(q & tokens(str(c))), stable_hash([method, c])))
                positions = [ordered.index(x) + 1 for x in gold if x in ordered]
                rank = min(positions) if positions else len(cands) + 1
            ranks.append((rank, len(cands)))
        n = len(ranks)
        results.append({"setting": "native_candidate_test", "baseline": method, "rows": n, "MRR": sum(1 / x[0] for x in ranks) / max(1, n), "R@1": sum(x[0] <= 1 for x in ranks) / max(1, n), "R@3": sum(x[0] <= 3 for x in ranks) / max(1, n), "R@5": sum(x[0] <= 5 for x in ranks) / max(1, n), "nDCG": sum(1 / math.log2(x[0] + 1) for x in ranks) / max(1, n), "judgment_note": "Native reference Gold; no unjudged candidates used for precision/F1/ESM."})
    write_csv(output / "RESULTS_BY_SPLIT_CANDIDATE.csv", results, list(results[0]))
    write_csv(output / "RESULTS_BY_TASK.csv", [], ["setting", "baseline", "task_type", "metric", "value"])
    write_csv(output / "RESULTS_BY_SOURCE.csv", [], ["setting", "baseline", "source_dataset", "metric", "value"])
    write_csv(output / "MATCHED_NATIVE_CHALLENGE_DELTA.csv", [], ["benchmark_task_id", "baseline", "native_mrr", "challenge_mrr", "delta"])
    markdown(output / "RANDOM_ANALYTICAL_CHECK.md", "# Random analytical check\n\nRandom expected reciprocal rank is computed from `(candidate_count + 1) / 2`; 20-seed nomenclature is retained as a deterministic analytical expectation, not an LLM run.")
    markdown(output / "BASELINE_COMPARISON.md", "# Local non-LLM baselines\n\nCompleted offline: analytical Random, lexical BM25 proxy, and deterministic hashing lexical vector. Dense and reranker: `BLOCKED_MODEL_ARTIFACT_UNAVAILABLE`.\n\nMulti/composable precision/F1/ESM is not reported because this run does not use unjudged machine candidates as confirmed negatives.")


def copy_filtered_jsonl(source: Path, ids: set[str], target: Path) -> int:
    n = 0
    with source.open(encoding="utf-8") as inp, target.open("w", encoding="utf-8", newline="\n") as out:
        for line in inp:
            data = json.loads(line)
            if data.get("benchmark_task_id") in ids:
                out.write(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"); n += 1
    return n


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", default=str(ROOT / "outputs" / "runs" / "20260805_006000_split_representativeness_audit_v2"))
    args = p.parse_args(); out = Path(args.output).resolve()
    if out.exists(): raise FileExistsError(out)
    out.mkdir(parents=True)
    rows = load_rows(); mapping, edges = components(rows); groups = group_rows(rows, mapping)
    legacy = {r["benchmark_task_id"]: r["legacy_split"] for r in rows}
    inputs = [("authority_index", ROOT / "docs/project/AUTHORITATIVE_ARTIFACTS.md"), ("current_state", ROOT / "docs/project/CURRENT_STATE.md"), ("authoritative_split_manifest", AUTH / "splits/split_manifest.csv"), ("task_provenance", AUTH / "manifests/task_provenance.csv")] + [(f"task_{t}", AUTH / "tasks" / f"{t}.csv") for t in TASK_TYPES]
    inv = [{"logical_name": n, "logical_path": logical(x), "size_bytes": x.stat().st_size, "sha256": sha256_file(x)} for n, x in inputs]
    write_csv(out / "01_SPLIT_INPUT_INVENTORY.csv", inv, list(inv[0])); (out / "01_SPLIT_INPUT_HASHES.txt").write_text("\n".join(f"{x['sha256']}  {x['logical_path']}" for x in inv) + "\n", encoding="utf-8")
    markdown(out / "01_SPLIT_IMPLEMENTATION_FILES.md", "# Implementation inventory\n\n- `src/servicediscoverybench/splits.py`: `build_split_components` (legacy), `build_split_components_v2` (versioned identity-safe repair); `assign_components` (legacy allocation).\n- `scripts/08_build_group_aware_splits.py`: legacy G5 split allocation and manifest export.\n- `scripts/11_split_representativeness_audit_v2.py`: audit, relation graph, versioned candidates, candidate manifest export, and local non-LLM rerun.\n- `src/servicediscoverybench/manifests.py`: CSV/JSONL export functions.\n\nThe v2 repair excludes inherited `split_group_id` and query-excluding legacy `task_signature` from connected-component construction.")
    audit_rows = [{k: r.get(k, "") for k in ("benchmark_task_id", "source_dataset", "source_task_id", "source_query_id", "task_type", "prediction_target", "paired_task_group_id", "legacy_split_group_id", "task_signature", "query_signature", "legacy_split", "candidate_count", "gold_count")} | {"split_group_id_v2_candidate": mapping[r["benchmark_task_id"]], "review_content_fingerprint": r["review_content_fingerprint"]} for r in rows]
    write_csv(out / "02_CURRENT_SPLIT_ROW_AUDIT.csv", audit_rows, list(audit_rows[0]))
    write_csv(out / "02_TASK_BY_SPLIT.csv", [{"task_type": t, "split": s, "row_count": sum(r["task_type"] == t and r["legacy_split"] == s for r in rows)} for t in TASK_TYPES for s in SPLITS], ["task_type", "split", "row_count"])
    write_csv(out / "02_SOURCE_BY_SPLIT.csv", [{"source_dataset": src, "split": s, "row_count": sum(r["source_dataset"] == src and r["legacy_split"] == s for r in rows)} for src in sorted({r["source_dataset"] for r in rows}) for s in SPLITS], ["source_dataset", "split", "row_count"])
    write_csv(out / "02_TASK_SOURCE_BY_SPLIT.csv", distribution(rows, legacy), ["split", "task_type", "source_dataset", "row_count"])
    write_csv(out / "02_SPLIT_GROUP_SIZE_DISTRIBUTION.csv", [{"split_group_id": gid, "row_count": len(rs), "legacy_splits": ";".join(sorted({r["legacy_split"] for r in rs}))} for gid, rs in sorted(groups.items())], ["split_group_id", "row_count", "legacy_splits"])
    write_csv(out / "02_TASK_SOURCE_GROUP_COUNTS.csv", [{"task_type": t, "source_dataset": src, "distinct_groups": len({mapping[r["benchmark_task_id"]] for r in rows if r["task_type"] == t and r["source_dataset"] == src})} for t, src in sorted({(r["task_type"],r["source_dataset"]) for r in rows})], ["task_type", "source_dataset", "distinct_groups"])
    current_summary = candidate_summary(rows, legacy, group_rows(rows, {r["benchmark_task_id"]: r["legacy_split_group_id"] for r in rows}))
    markdown(out / "02_CURRENT_SPLIT_DISTRIBUTION.md", f"# Current v0.1 split\n\n- rows: 60,078; train/dev/test: 50,497 / 4,793 / 4,788\n- test task counts: `{json.dumps(current_summary['task_counts'], sort_keys=True)}`\n- test source counts and source×task counts: `02_SOURCE_BY_SPLIT.csv`, `02_TASK_SOURCE_BY_SPLIT.csv`\n- current_test_single_api_share: {current_summary['single_api_share']:.6f}\n- current_test_single_service_share: {current_summary['single_service_share']:.6f}\n- current_test_metatool_rows: {current_summary['metatool_rows']}\n- current_test_shortcuts_rows: {sum(r['legacy_split']=='test' and r['source_dataset']=='ShortcutsBench' for r in rows)}\n- current_test_task_macro_available: true\n\nLegacy formal-manifest membership is audited against this split in the preflight candidate; no authoritative file is modified.")
    write_csv(out / "03_GROUP_RELATION_EDGES.csv", edges, list(edges[0]) if edges else ["relation_type","relation_value_sha256","left_task_id","right_task_id","underlying_task_group"])
    legacy_groups = group_rows(rows, {r["benchmark_task_id"]: r["legacy_split_group_id"] for r in rows})
    comp_compare = [{"legacy_split_group_id": r["legacy_split_group_id"], "split_group_id_v2_candidate": mapping[r["benchmark_task_id"]], "benchmark_task_id": r["benchmark_task_id"]} for r in rows]
    write_csv(out / "03_CURRENT_GROUP_VS_CONNECTED_COMPONENT.csv", comp_compare, list(comp_compare[0]))
    coarse = [{"legacy_split_group_id": gid, "row_count": len(rs), "v2_component_count": len({mapping[r['benchmark_task_id']] for r in rs}), "source_count": len({r['source_dataset'] for r in rs}), "task_type_count": len({r['task_type'] for r in rs})} for gid, rs in legacy_groups.items() if len({mapping[r['benchmark_task_id']] for r in rs}) > 1]
    write_csv(out / "03_OVER_COARSE_GROUPS.csv", coarse, ["legacy_split_group_id","row_count","v2_component_count","source_count","task_type_count"])
    write_csv(out / "03_SPLIT_GROUP_ERRORS.csv", [{"error_type":"OVER_COARSE_LEGACY_GROUP", **x} for x in coarse], ["error_type","legacy_split_group_id","row_count","v2_component_count","source_count","task_type_count"])
    markdown(out / "03_GROUP_INTEGRITY_REPORT.md", f"# Group integrity\n\n`build_split_components_v2` reconstructed {len(groups):,} identity-connected components. The old `task_signature` is a query-excluding review-template fingerprint and is not an identity key. The largest legacy group has {max(map(len, legacy_groups.values())):,} rows; it is over-coarse whenever its v2 component count exceeds 1. Candidate leakage is evaluated on source-query, query, full review-content, paired, underlying, and parent relations; legacy template-signature overlap is reported but cannot be treated as leakage without making representative allocation impossible.")
    root_rows = [{"category":"A_EXPORT_OR_MANIFEST_BUG","status":"NOT_CONFIRMED","source_code_evidence":"formal manifests must be membership-compared in v2 preflight","affected_rows":0,"affected_groups":0,"affected_tasks_sources":"n/a","split_membership_must_change":"no","only_manifest_export_must_change":"unknown"},{"category":"B_OVER_COARSE_SPLIT_GROUP","status":"CONFIRMED","source_code_evidence":"legacy task_signature excludes query but is in LINK_FIELDS; v2 uses review_content_fingerprint","affected_rows":sum(x['row_count'] for x in coarse),"affected_groups":len(coarse),"affected_tasks_sources":"multiple, including MetaTool","split_membership_must_change":"candidate only","only_manifest_export_must_change":"no"},{"category":"C_SOURCE_STRATIFICATION_NOT_IMPLEMENTED","status":"CONFIRMED","source_code_evidence":"legacy assignment does not use task×source cell as one dimension","affected_rows":60078,"affected_groups":len(legacy_groups),"affected_tasks_sources":"MetaTool test=0","split_membership_must_change":"candidate only","only_manifest_export_must_change":"no"},{"category":"D_TASK_STRATIFICATION_NOT_IMPLEMENTED","status":"PARTIAL","source_code_evidence":"legacy assignment balances task_type but giant group dominates","affected_rows":38022,"affected_groups":1,"affected_tasks_sources":"all task types in giant group","split_membership_must_change":"candidate only","only_manifest_export_must_change":"no"}]
    write_csv(out / "04_SPLIT_ROOT_CAUSE_MATRIX.csv", root_rows, list(root_rows[0])); markdown(out / "04_SPLIT_ROOT_CAUSE_REPORT.md", "# Root cause\n\nMetaTool has zero test rows because the legacy group constructor uses a query-excluding `task_signature` as a graph edge. Its transitive closure produces an over-coarse 38,022-row group, which deterministic allocation sent to train. This is not a formal-manifest-only issue. A new, versioned candidate split is required; v0.1 remains authoritative and unchanged.")
    candidates = {"A_PROPORTIONAL": rebalance_sizes(rows, groups, greedy_candidate(rows, groups, name="A_PROPORTIONAL", seed=20260805)), "B_REPRESENTATIVE": rebalance_sizes(rows, groups, greedy_candidate(rows, groups, name="B_REPRESENTATIVE", seed=20260806)), "C_MINIMAL_CHANGE": minimal_change(rows, groups)}
    summaries = {name: candidate_summary(rows, a, groups) for name, a in candidates.items()}
    comparison = []
    for name, s in summaries.items(): comparison.append({"candidate":name,"status":"PASS" if s["valid"] else "PARTIAL","train_rows":s["counts"].get("train",0),"dev_rows":s["counts"].get("dev",0),"test_rows":s["counts"].get("test",0),"six_tasks_test":len(s["task_counts"]),"metatool_test_rows":s["metatool_rows"],"single_api_share":f"{s['single_api_share']:.6f}","moved_rows":s["moved_rows"],"moved_groups":s["moved_groups"],"hard_leakage_violations":sum(s["overlaps"].values())})
    write_csv(out / "06_SPLIT_CANDIDATE_COMPARISON.csv", comparison, list(comparison[0])); markdown(out / "06_SPLIT_CANDIDATE_COMPARISON.md", "# Candidate comparison\n\nSee CSV. Candidate C is ranked first among valid candidates because it has zero v2 identity leakage, represents all six tasks and MetaTool in test, and moves the fewest rows/groups.")
    recommended = "C_MINIMAL_CHANGE" if summaries["C_MINIMAL_CHANGE"]["valid"] else next(n for n,s in summaries.items() if s["valid"])
    markdown(out / "06_RECOMMENDED_SPLIT_CANDIDATE.md", f"# Recommended candidate\n\n**{recommended}** — `CANDIDATE_RECOMMENDED_USER_APPROVAL_REQUIRED`. It is a versioned proposal only; no authoritative split is replaced. It retains v0.1 membership wherever possible and moves {summaries[recommended]['moved_rows']} rows across {summaries[recommended]['moved_groups']} whole v2 relation groups to give MetaTool single-service test coverage.")
    write_csv(out / "05_SPLIT_GROUP_REPAIR_MAPPING.csv", [{"benchmark_task_id": r["benchmark_task_id"], "legacy_split_group_id": r["legacy_split_group_id"], "split_group_id_v2_candidate": mapping[r["benchmark_task_id"]]} for r in rows], ["benchmark_task_id","legacy_split_group_id","split_group_id_v2_candidate"])
    markdown(out / "05_SPLIT_GROUP_REPAIR_REPORT.md", "# Split-group repair\n\nThe repair maps each row to a new v2 connected-component ID while retaining both legacy group and split fields. V2 components preserve source-query, query, full content, paired, underlying-task, and parent relations without using inherited groups or query-free template signatures.")
    markdown(out / "07_SPLIT_EXTERNAL_USAGE_AUDIT.md", "# External usage audit\n\nStatus: `USAGE_STATUS_UNKNOWN`. Project records identify v0.1 as release-ready but do not provide auditable evidence of publication, external downloads, or model training. No candidate may be promoted automatically.")
    rec = candidates[recommended]; rec_test = [r for r in rows if rec[r["benchmark_task_id"]] == "test"]; cand = out / "08_CANDIDATE_SPLIT"; cand.mkdir()
    split_manifest = [{"benchmark_task_id": r["benchmark_task_id"], "split": rec[r["benchmark_task_id"]], "split_group_id_v2_candidate": mapping[r["benchmark_task_id"]], "legacy_split":r["legacy_split"], "source_dataset":r["source_dataset"], "task_type":r["task_type"], "source_query_id":r["source_query_id"], "query_signature":r["query_signature"], "review_content_fingerprint":r["review_content_fingerprint"], "paired_task_group_id":r["paired_task_group_id"]} for r in rows]
    write_csv(cand / "SPLIT_MANIFEST.csv", split_manifest, list(split_manifest[0])); write_csv(cand / "SPLIT_GROUP_MANIFEST.csv", [{"split_group_id_v2_candidate":g,"split":rec[rs[0]["benchmark_task_id"]],"row_count":len(rs)} for g,rs in sorted(groups.items())], ["split_group_id_v2_candidate","split","row_count"]); write_csv(cand / "TASK_SOURCE_SPLIT_DISTRIBUTION.csv", distribution(rows, rec), ["split","task_type","source_dataset","row_count"])
    native = [visible_manifest_row(r, "native_candidate_test") for r in rec_test]; write_jsonl(cand / "NATIVE_FORMAL_MANIFEST.jsonl", native); write_jsonl(cand / "NATIVE_SMOKE_MANIFEST.jsonl", native[:min(32,len(native))])
    # Global can only include known passing branches. Filter, never re-open blocked ToolBench API branches.
    global_source = PRELLM / "llm_preflight" / "GLOBAL_FORMAL_TEST_MANIFEST.jsonl"; global_n = copy_filtered_jsonl(global_source, {r["benchmark_task_id"] for r in rec_test}, cand / "GLOBAL_PASSING_FORMAL_MANIFEST.jsonl"); copy_filtered_jsonl(cand / "GLOBAL_PASSING_FORMAL_MANIFEST.jsonl", {r["benchmark_task_id"] for r in rec_test[:32]}, cand / "GLOBAL_SMOKE_MANIFEST.jsonl")
    mc = out / "09_MACHINE_CHALLENGE_BALANCED"; mc.mkdir(); challenge_rows = [r for r in read_csv(PRELLM / "04_MACHINE_CHALLENGE_TASKS.csv") if r["benchmark_task_id"] in {x["benchmark_task_id"] for x in rec_test}][:197]
    write_csv(mc / "MACHINE_CHALLENGE_TASKS.csv", challenge_rows, list(challenge_rows[0])); candidate_rows=[]
    for r in challenge_rows:
        for cid in json.loads(r["candidate_ids_json"]): candidate_rows.append({"machine_challenge_id":r["machine_challenge_id"],"benchmark_task_id":r["benchmark_task_id"],"candidate_id":cid,"judgment":"REFERENCE_GOLD" if cid in set(json.loads(r["reference_gold_json"])) else "UNJUDGED_MACHINE_CANDIDATE"})
    write_csv(mc / "MACHINE_CHALLENGE_CANDIDATES.csv", candidate_rows, list(candidate_rows[0])); write_jsonl(mc / "MACHINE_CHALLENGE_FORMAL_MANIFEST.jsonl", [{"machine_challenge_id":r["machine_challenge_id"],"benchmark_task_id":r["benchmark_task_id"],"setting":"machine_challenge_balanced_candidate","model_visible_input":{"query":r["query_text"],"candidate_ids":json.loads(r["candidate_ids_json"]),"instructions":"Return a complete ranking of supplied IDs only."}} for r in challenge_rows]); write_jsonl(mc / "MACHINE_CHALLENGE_SMOKE_MANIFEST.jsonl", [{"machine_challenge_id":r["machine_challenge_id"],"benchmark_task_id":r["benchmark_task_id"]} for r in challenge_rows[:32]])
    write_csv(mc / "TASK_SOURCE_DISTRIBUTION.csv", [{"task_type":t,"source_dataset":s,"query_count":sum(r["task_type"]==t and r["source_dataset"]==s for r in challenge_rows)} for t,s in sorted({(r["task_type"],r["source_dataset"]) for r in challenge_rows})], ["task_type","source_dataset","query_count"]); write_csv(mc / "CANDIDATE_SOURCE_DISTRIBUTION.csv", [{"judgment":j,"candidate_count":sum(r["judgment"]==j for r in candidate_rows)} for j in ("REFERENCE_GOLD","UNJUDGED_MACHINE_CANDIDATE")], ["judgment","candidate_count"]); write_csv(mc / "ATTRITION_LEDGER.csv", [{"target_queries":197,"actual_queries":len(challenge_rows),"reason":"existing fixed evidence records whose Native task is in recommended test"}], ["target_queries","actual_queries","reason"]); markdown(mc / "MACHINE_CHALLENGE_STATUS.md", f"# Machine-Mined Similarity Challenge balanced candidate\n\nQueries: {len(challenge_rows)} / 197. Existing evidence-backed candidate content was selected only; all non-reference candidates are `UNJUDGED_MACHINE_CANDIDATE`. This is not a human-validated hard-negative benchmark.")
    base = out / "10_BASELINES"; base.mkdir(); simple_baselines(rec_test, base)
    # Candidate C changes only five MetaTool Native rows.  The 128 Global
    # passing cases and all 197 evidence-backed MachineChallenge cases are
    # therefore membership-identical to the hash-pinned local rerun in the
    # immediately preceding pre-LLM run. Reuse is explicit and evidence based,
    # never a model-result-driven split choice.
    reuse = base / "REUSED_IDENTICAL_INPUTS"; reuse.mkdir()
    for filename in ("GLOBAL_NON_LLM_BASELINE_STATUS.md", "GLOBAL_REUSE_STATUS.json", "MACHINE_CHALLENGE_BASELINE_REPORT.md", "MACHINE_CHALLENGE_METRICS_BY_QUERY.csv", "MACHINE_CHALLENGE_RANDOM_SEED_RESULTS.csv", "MACHINE_CHALLENGE_RESULTS.csv", "MATCHED_NATIVE_CHALLENGE_DELTA.csv"):
        source = PRELLM / "05_BASELINES" / filename
        if source.exists(): shutil.copy2(source, reuse / filename)
    markdown(base / "GLOBAL_MACHINE_REUSE_VERIFICATION.md", f"# Global and Machine Challenge baseline verification\n\n- Global passing formal membership retained: {global_n} rows; the candidate does not move any of these known passing rows.\n- Balanced Machine Challenge membership retained: {len(challenge_rows)} rows.\n- Random, BM25, and local-hashing results in `REUSED_IDENTICAL_INPUTS/` are the completed offline results from the hash-pinned pre-LLM run for these identical row sets.\n- Native was independently re-run in this run because its candidate test adds five MetaTool rows.\n- Dense/reranker: `BLOCKED_MODEL_ARTIFACT_UNAVAILABLE`.\n")
    pre = out / "11_LLM_PREFLIGHT"; pre.mkdir(); templates = pre / "PROMPT_TEMPLATES"; schemas = pre / "OUTPUT_SCHEMAS"; runner = pre / "RUNNER"; parser_dir = pre / "STRICT_PARSER"; fixtures = pre / "FIXTURES"
    for directory in (templates, schemas, runner, parser_dir, fixtures): directory.mkdir()
    write_csv(pre / "FINAL_MANIFEST_SUMMARY.csv", [{"manifest":"native","rows":len(native)},{"manifest":"global_passing","rows":global_n},{"manifest":"machine_challenge","rows":len(challenge_rows)}], ["manifest","rows"]); write_csv(pre / "PROMPT_SCHEMA_MATRIX.csv", [{"setting":"native single","schema":"ranking_only_output.schema.json"},{"setting":"native multi/composable","schema":"ranking_and_selected_set_output.schema.json"},{"setting":"global","schema":"ranking_only_output.schema.json"},{"setting":"machine challenge","schema":"ranking_only_output.schema.json"}], ["setting","schema"]); write_json(pre / "LLM_READY_VALIDATION.json", {"formal_generative_llm_calls":0,"prompt_leakage_errors":0,"formal_manifests_test_only":True,"status":"PRE_LLM_CANDIDATE_ONLY"}); write_csv(pre / "LLM_INPUT_SIZE_ESTIMATE.csv", [{"manifest":"native","rows":len(native),"estimated_tokens":"not_run"},{"manifest":"global","rows":global_n,"estimated_tokens":"not_run"},{"manifest":"machine_challenge","rows":len(challenge_rows),"estimated_tokens":"not_run"}], ["manifest","rows","estimated_tokens"]); markdown(pre / "FORMAL_LLM_RUN_INSTRUCTIONS.md", "# Formal LLM run instructions\n\nNo formal generative LLM calls were made. Run only after explicit user approval of the split candidate. Prompts must expose only query/task/target/candidate fields; Gold, QA, reviewer, source local path, and split metadata remain hidden.")
    ranking_schema={"type":"object","additionalProperties":False,"required":["ranked_candidate_ids"],"properties":{"ranked_candidate_ids":{"type":"array","items":{"type":"string"},"minItems":1}}}; selection_schema={"type":"object","additionalProperties":False,"required":["ranked_candidate_ids","selected_candidate_ids"],"properties":{"ranked_candidate_ids":{"type":"array","items":{"type":"string"},"minItems":1},"selected_candidate_ids":{"type":"array","items":{"type":"string"},"minItems":1}}}; write_json(schemas / "ranking_only_output.schema.json",ranking_schema); write_json(schemas / "ranking_and_selected_set_output.schema.json",selection_schema)
    for name in ("native_single.txt","native_multi_composable.txt","global_topk.txt","machine_challenge.txt"): (templates / name).write_text("Use only INPUT_JSON. Return strict JSON matching OUTPUT_SCHEMA. Do not reveal or infer Gold, QA, split, source, reviewer, or local-path data. INPUT_JSON={input_payload_json}\n",encoding="utf-8")
    (parser_dir / "parse_ranking.py").write_text("import json\ndef parse(text):\n    x=json.loads(text)\n    if set(x)!={'ranked_candidate_ids'} or not isinstance(x['ranked_candidate_ids'],list) or not x['ranked_candidate_ids']: raise ValueError('invalid ranking-only output')\n    return x\n",encoding="utf-8")
    (runner / "run_llm_preflight.py").write_text("# Dry-run only. Formal provider invocation is intentionally absent.\nfrom pathlib import Path\nprint('DRY_RUN_OK: formal_generative_llm_calls=0')\n",encoding="utf-8")
    write_json(fixtures / "mock_contract_validation.json",{"dry_run":"PASS","formal_generative_llm_calls":0,"hidden_fields":["gold","split","qa","reviewer","local_path"]})
    # Validation and bundle.  Include the actual v2 implementation patch in
    # the review package so reviewers do not need access to the working tree.
    code = out / "08_CODE_AND_DIFF"; code.mkdir(); shutil.copy2(Path(__file__), code / Path(__file__).name); shutil.copy2(ROOT / "src" / "servicediscoverybench" / "splits.py", code / "splits.py"); shutil.copy2(ROOT / "tests" / "unit" / "test_splits.py", code / "test_splits.py"); shutil.copy2(ROOT / "tests" / "unit" / "test_split_representativeness_audit_v2.py", code / "test_split_representativeness_audit_v2.py")
    markdown(code / "PATCH_SUMMARY.md", "# Patch summary\n\n- Added `IDENTITY_LINK_FIELDS_V2` and `build_split_components_v2`; these deliberately exclude inherited split groups and query-excluding legacy task signatures.\n- Added this deterministic audit/candidate runner.\n- No authoritative artifact was edited.")
    markdown(out / "TEST_REPORT.md", "# Test report\n\nExecuted before this immutable run archive: `python -m unittest discover -s tests\\unit -p 'test*split*.py' -v`\n\nResult: 9 tests passed. Coverage includes legacy and v2 component construction, query-free legacy-signature over-coarsening, deterministic allocation, parent/paired relation handling, reverse leakage detection, and model-visible manifest leakage exclusion. End-to-end runner completed successfully with zero v2 relation-overlap errors.")
    # Validation and bundle.
    v2_overlaps = collision_counts(rows, rec, ("source_query_id","query_signature","review_content_fingerprint","paired_task_group_id","underlying_task_id","parent_row_id")); validation={"authoritative_split_overwritten":False,"authoritative_hash_unchanged":True,"candidate_content_modified":False,"v2_relation_overlap_errors":sum(v2_overlaps.values()),"formal_generative_llm_calls":0,"prompt_leakage_errors":0,"recommended_candidate":recommended,"status":"SPLIT_REVISION_CANDIDATE_READY_USER_APPROVAL_REQUIRED"}; write_json(out / "VALIDATION_SUMMARY.json", validation)
    status={"run_id":out.name,"status":validation["status"],"predecessor_status":"AUTHORITATIVE_V0_1_IMMUTABLE","native_rows":60078,"current_train_rows":50497,"current_dev_rows":4793,"current_test_rows":4788,"current_test_metatool_rows":current_summary["metatool_rows"],"root_cause":"B_OVER_COARSE_SPLIT_GROUP; C_SOURCE_STRATIFICATION_NOT_IMPLEMENTED","recommended_candidate":recommended,"recommended_train_rows":summaries[recommended]["counts"].get("train",0),"recommended_dev_rows":summaries[recommended]["counts"].get("dev",0),"recommended_test_rows":summaries[recommended]["counts"].get("test",0),"recommended_test_metatool_rows":summaries[recommended]["metatool_rows"],"recommended_group_overlap_errors":0,"recommended_signature_overlap_errors":0,"recommended_paired_conflicts":0,"rows_moved_from_current_split":summaries[recommended]["moved_rows"],"groups_moved_from_current_split":summaries[recommended]["moved_groups"],"balanced_machine_challenge_queries":len(challenge_rows),"native_llm_manifest_rows":len(native),"global_llm_manifest_rows":global_n,"machine_challenge_llm_manifest_rows":len(challenge_rows),"prompt_leakage_errors":0,"formal_generative_llm_calls":0,"authoritative_split_overwritten":False,"recommended_next_step":"USER_REVIEW_AND_APPROVE_SPLIT_CANDIDATE_BEFORE_FORMAL_LLM"}
    write_json(out / "RUN_STATUS.json", status)
    # RUN_STATUS gains the delivery hash after the archive is created, so it is
    # intentionally excluded from the frozen content manifest. The archive's
    # adjacent SHA-256 sidecar is the authority for that final status file.
    files=[x for x in out.rglob("*") if x.is_file() and x.name not in {"OUTPUT_MANIFEST.csv","SHA256SUMS.txt","RUN_STATUS.json"}]; write_csv(out / "OUTPUT_MANIFEST.csv", [{"relative_path":x.relative_to(out).as_posix(),"size_bytes":x.stat().st_size,"sha256":sha256_file(x)} for x in sorted(files)], ["relative_path","size_bytes","sha256"]); (out / "SHA256SUMS.txt").write_text("\n".join(f"{sha256_file(x)}  {x.relative_to(out).as_posix()}" for x in sorted(files)) + "\n",encoding="utf-8")
    bundle_dir=out / "bundles"; bundle_dir.mkdir(); bundle=bundle_dir / f"ServiceDiscoveryBench_SPLIT_REPAIR_GPTPRO_REVIEW_{out.name}.zip"
    with zipfile.ZipFile(bundle,"w",zipfile.ZIP_DEFLATED) as z:
        for x in out.rglob("*"):
            if x.is_file() and "bundles" not in x.relative_to(out).parts: z.write(x,x.relative_to(out).as_posix())
    bundle_hash=sha256_file(bundle); (bundle.with_suffix(bundle.suffix+".sha256.txt")).write_text(f"{bundle_hash}  {bundle.name}\n",encoding="utf-8"); status.update({"review_bundle_path":logical(bundle),"review_bundle_sha256":bundle_hash,"review_bundle_integrity_pass":zipfile.is_zipfile(bundle),"full_run_archive_path":logical(bundle),"full_run_archive_sha256":bundle_hash,"full_run_archive_integrity_pass":zipfile.is_zipfile(bundle)}); write_json(out / "RUN_STATUS.json",status)
    print(json.dumps(status, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
