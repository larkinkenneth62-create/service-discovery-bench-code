from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


MAX_COPY_BYTES = 100 * 1024 * 1024
SAMPLE_ROWS = 200


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def count_csv_rows(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return max(sum(1 for _ in csv.reader(f)) - 1, 0)
    except Exception:
        return -1


def read_csv_rows(path: Path, limit: Optional[int] = None) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                break
            rows.append({k: (v if v is not None else "") for k, v in row.items()})
    return rows


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def unique_paths(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    out: List[Path] = []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def sample_csv(src: Path, dst: Path, rows: int = SAMPLE_ROWS) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("r", encoding="utf-8-sig", newline="") as f_in, dst.open("w", encoding="utf-8-sig", newline="") as f_out:
        reader = csv.reader(f_in)
        writer = csv.writer(f_out)
        for i, row in enumerate(reader):
            if i > rows:
                break
            writer.writerow(row)


class HandoffBuilder:
    def __init__(self, project_root: Path, out_dir: Path) -> None:
        self.root = project_root.resolve()
        self.out_dir = out_dir.resolve()
        self.manifest: List[Dict[str, Any]] = []
        self.missing: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
        self.copied_files: List[Path] = []

    def rel(self, path: Path) -> str:
        return safe_rel(path, self.root)

    def note_missing(self, group: str, rel_path: str, severity: str, notes: str) -> None:
        self.missing[group].append((rel_path, severity, notes))
        self.manifest.append(
            {
                "relative_path": rel_path,
                "exists": False,
                "file_size_bytes": "",
                "modified_time": "",
                "sha256": "",
                "artifact_group": group,
                "required_or_optional": severity,
                "notes": notes,
            }
        )

    def add_manifest(self, src: Path, group: str, required: str, notes: str, copied_to: Optional[Path] = None) -> None:
        self.manifest.append(
            {
                "relative_path": self.rel(src),
                "exists": src.exists(),
                "file_size_bytes": src.stat().st_size if src.exists() else "",
                "modified_time": datetime.fromtimestamp(src.stat().st_mtime).astimezone().replace(microsecond=0).isoformat() if src.exists() else "",
                "sha256": sha256_file(src) if src.exists() and src.is_file() else "",
                "artifact_group": group,
                "required_or_optional": required,
                "notes": (notes + (f"; copied_to={safe_rel(copied_to, self.out_dir)}" if copied_to else "")),
            }
        )

    def copy_file(self, rel_path: str, group: str, required: str = "helpful_but_optional", notes: str = "") -> Optional[Path]:
        src = self.root / rel_path
        if not src.exists() or not src.is_file():
            self.note_missing(group, rel_path, required, notes or "file not found")
            return None
        dst = self.out_dir / group / rel_path
        if src.stat().st_size > MAX_COPY_BYTES and src.suffix.lower() == ".csv":
            dst = dst.with_name(dst.stem + f".sample_{SAMPLE_ROWS}.csv")
            sample_csv(src, dst)
            row_count = count_csv_rows(src)
            self.add_manifest(src, group, required, f"{notes}; original >100MB, sample copied with first {SAMPLE_ROWS} rows, original_row_count={row_count}", dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            self.add_manifest(src, group, required, notes, dst)
        self.copied_files.append(dst)
        return dst

    def copy_dir(self, rel_dir: str, group: str, required: str = "helpful_but_optional", notes: str = "") -> None:
        src = self.root / rel_dir
        if not src.exists() or not src.is_dir():
            self.note_missing(group, rel_dir, required, notes or "directory not found")
            return
        for path in src.rglob("*"):
            if path.is_file():
                rel = safe_rel(path, self.root)
                self.copy_file(rel, group, required, notes)

    def copy_glob(self, pattern: str, group: str, required: str = "helpful_but_optional", notes: str = "") -> None:
        matches = [p for p in self.root.glob(pattern) if p.is_file()]
        if not matches:
            self.note_missing(group, pattern, required, "glob found no files")
            return
        for path in matches:
            self.copy_file(safe_rel(path, self.root), group, required, notes)


def file_candidates() -> Dict[str, List[Tuple[str, str, str]]]:
    return {
        "schema": [
            ("docs/schema/SCHEMA_UNIFIED_V0_1.md", "helpful_but_optional", "schema doc"),
            ("docs/schema/FIELD_DICTIONARY_UNIFIED_V0_1.md", "helpful_but_optional", "field dictionary"),
            ("docs/schema/ENUM_REGISTRY_UNIFIED_V0_1.md", "helpful_but_optional", "enum registry"),
            ("docs/schema/SOURCE_TO_CANONICAL_MAPPING_MATRIX_V0_1.md", "helpful_but_optional", "source mapping"),
            ("docs/schema/CANONICAL_JSON_OBJECT_SCHEMAS_V0_1.md", "helpful_but_optional", "JSON object schemas"),
            ("docs/schema/UNIFIED_SCHEMA_DECISION_LOG_V0_1.md", "helpful_but_optional", "decision log"),
            ("docs/schema/UNIFIED_SCHEMA_GO_NO_GO_V0_1.md", "helpful_but_optional", "schema Go/No-Go"),
            ("docs/schema/README_UNIFIED_SCHEMA_V0_1.md", "helpful_but_optional", "schema README"),
            ("outputs/unified_schema_v0_1/source_field_inventory.csv", "helpful_but_optional", "source field inventory"),
            ("outputs/unified_schema_v0_1/source_to_canonical_mapping_matrix.csv", "helpful_but_optional", "mapping matrix"),
            ("scripts/schema/inventory_source_fields_v0_1.py", "helpful_but_optional", "schema script"),
            ("scripts/schema/normalize_to_unified_schema_v0_1.py", "helpful_but_optional", "schema script"),
            ("scripts/schema/validate_unified_schema_v0_1.py", "helpful_but_optional", "schema script"),
        ],
        "reviews": [
            ("outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2.csv", "helpful_but_optional", "MetaTool review source CSV"),
            ("outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2_reviewed.csv", "helpful_but_optional", "MetaTool reviewed CSV"),
            ("outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2_reviewed_draft.csv", "helpful_but_optional", "MetaTool reviewed draft"),
            ("outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2.csv", "helpful_but_optional", "StableToolBench review source CSV"),
            ("outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2_reviewed.csv", "helpful_but_optional", "StableToolBench reviewed CSV"),
            ("outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2_reviewed_draft.csv", "helpful_but_optional", "StableToolBench reviewed draft"),
            ("outputs/external_qa_v0_2/metatool/metatool_v0_2_reaudit_by_gpt55pro_schema.csv", "helpful_but_optional", "MetaTool re-audit optional path"),
            ("outputs/external_qa_v0_2/metatool/metatool_v0_2_reaudit_disagreements_only.csv", "helpful_but_optional", "MetaTool re-audit optional path"),
            ("outputs/external_qa_v0_2/metatool/metatool_v0_2_reaudit_summary.json", "helpful_but_optional", "MetaTool re-audit optional path"),
            ("outputs/manual_reaudit/metatool_v0_2_reaudit_by_gpt55pro_schema.csv", "helpful_but_optional", "MetaTool user supplied re-audit"),
            ("outputs/manual_reaudit/metatool_v0_2_reaudit_disagreements_only.csv", "helpful_but_optional", "MetaTool re-audit optional path"),
            ("outputs/manual_reaudit/metatool_v0_2_reaudit_summary.json", "helpful_but_optional", "MetaTool re-audit optional path"),
        ],
        "toolbench_core": [
            ("outputs/policy_v1_5f_tightening_dryrun/clean_candidates_v1_4c_with_v1_5f_annotations.csv", "helpful_but_optional", "ToolBench v1.5f annotations"),
            ("outputs/policy_v1_5f_tightening_dryrun/v1_5f_dryrun_summary.json", "helpful_but_optional", "v1.5f summary"),
            ("outputs/policy_v1_5f_tightening_dryrun/finalqa100_v1_5f_regression_summary.json", "helpful_but_optional", "finalqa100 regression"),
            ("outputs/policy_v1_5f_tightening_dryrun/v1_5f_rule_hit_counts.csv", "helpful_but_optional", "v1.5f rule hit counts"),
            ("outputs/policy_v1_5f_tightening_dryrun/v1_5f_task_type_movement_counts.csv", "helpful_but_optional", "v1.5f task movement"),
            ("outputs/final_qa_v1_5f_impacted_review/final_qa_v1_5f_impacted_review_items_csv_only.csv", "helpful_but_optional", "v1.5f impacted review"),
            ("docs/phase1/policy_v1_5f_tightening_dryrun_report.md", "helpful_but_optional", "v1.5f report"),
            ("docs/phase1/policy_v1_5f_go_no_go_pre_v1_6.md", "helpful_but_optional", "v1.5f Go/No-Go"),
            ("docs/phase1/final_qa_v1_5e_go_no_go_report.md", "helpful_but_optional", "v1.5e Go/No-Go"),
            ("docs/phase1/final_qa_v1_5e_failure_taxonomy.md", "helpful_but_optional", "v1.5e taxonomy"),
            ("docs/phase1/policy_v1_5f_tightening_plan_from_final_qa.md", "helpful_but_optional", "v1.5f plan"),
        ],
        "external_policy_v0_2": [
            ("docs/phase1/metatool_policy_v0_2_consistency_audit.md", "helpful_but_optional", "MetaTool v0.2 audit"),
            ("docs/phase1/stabletoolbench_policy_v0_2_consistency_audit.md", "helpful_but_optional", "StableToolBench v0.2 audit"),
            ("docs/phase1/external_policy_v0_2_consistency_go_no_go.md", "helpful_but_optional", "external v0.2 Go/No-Go"),
            ("docs/phase1/external_policy_v0_2_csv_review_instruction.md", "helpful_but_optional", "review instruction"),
            ("docs/phase1/external_source_integration_strategy_v0_1.md", "helpful_but_optional", "external strategy"),
            ("docs/phase1/external_source_recovery_go_no_go_v1_5f_pre.md", "helpful_but_optional", "external recovery Go/No-Go"),
            ("outputs/external_source_policy_v0_2/metatool/metatool_single_service_with_leakage_policy_v0_2.csv", "helpful_but_optional", "MetaTool v0.2 policy"),
            ("outputs/external_source_policy_v0_2/metatool/metatool_leakage_policy_summary_v0_2.json", "helpful_but_optional", "MetaTool policy summary"),
            ("outputs/external_source_policy_v0_2/metatool/metatool_rewrite_candidate_pool_v0_2.csv", "helpful_but_optional", "MetaTool rewrite pool"),
            ("outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_solvable_with_filter_policy_v0_2.csv", "helpful_but_optional", "StableToolBench v0.2 policy"),
            ("outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_filter_policy_summary_v0_2.json", "helpful_but_optional", "StableToolBench policy summary"),
            ("outputs/external_policy_v0_2_consistency_audit/stabletoolbench_v0_2_primary_decision_distribution.csv", "helpful_but_optional", "Stable primary decision distribution"),
            ("outputs/external_policy_v0_2_consistency_audit/stabletoolbench_v0_2_pool_overlap_matrix.csv", "helpful_but_optional", "Stable pool overlap"),
            ("outputs/external_policy_v0_2_consistency_audit/stabletoolbench_v0_2_rows_with_multiple_pool_memberships.csv", "helpful_but_optional", "Stable multi-pool rows"),
            ("outputs/external_policy_v0_2_consistency_audit/metatool_v0_2_decision_distribution.csv", "helpful_but_optional", "MetaTool decision distribution"),
        ],
        "v0_1_candidate": [
            ("docs/phase1/service_discovery_bench_v0_1_candidate_blueprint.md", "helpful_but_optional", "candidate blueprint"),
            ("docs/phase1/service_discovery_bench_v0_1_candidate_go_no_go.md", "helpful_but_optional", "candidate Go/No-Go"),
            ("docs/phase1/service_discovery_bench_v0_1_candidate_qa_plan.md", "helpful_but_optional", "candidate QA plan"),
        ],
        "scripts": [
            ("scripts/validation/validate_external_policy_v0_2_reviewed_csv.py", "helpful_but_optional", "review helper script"),
            ("scripts/validation/summarize_external_policy_v0_2_reviewed_csv.py", "helpful_but_optional", "review helper script"),
            ("scripts/validation/audit_external_policy_v0_2_consistency.py", "helpful_but_optional", "policy audit script"),
            ("scripts/validation/run_policy_v1_5f_tightening_dryrun.py", "helpful_but_optional", "ToolBench v1.5f script"),
            ("scripts/validation/validate_unified_schema_v0_1.py", "helpful_but_optional", "legacy location optional"),
            ("scripts/validation/normalize_to_unified_schema_v0_1.py", "helpful_but_optional", "legacy location optional"),
        ],
    }


def load_json_if_exists(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def count_distribution_csv(path: Path, col: str) -> Dict[str, int]:
    if not path.exists():
        return {}
    counts: Counter[str] = Counter()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            counts[row.get(col, "") or "<empty>"] += 1
    return dict(counts)


def reviewed_files(root: Path) -> List[Path]:
    patterns = ["outputs/**/*_reviewed.csv", "outputs/**/*_reviewed_draft.csv", "outputs/**/*review*reviewed*.csv"]
    files: List[Path] = []
    for pattern in patterns:
        files.extend([p for p in root.glob(pattern) if p.is_file() and p.suffix.lower() == ".csv" and not is_handoff_path(p, root)])
    files.extend([p for p in root.glob("outputs/**/*reaudit*metatool*.csv") if p.is_file() and not is_handoff_path(p, root)])
    return unique_paths(files)


def is_handoff_path(path: Path, root: Path) -> bool:
    rel = safe_rel(path, root).replace("\\", "/")
    return rel.startswith("outputs/assistant_handoff_")


def reviewer_provenance(root: Path, out_dir: Path) -> List[Dict[str, Any]]:
    rows = []
    for path in reviewed_files(root):
        try:
            csv_rows = read_csv_rows(path)
            if not csv_rows:
                continue
            reviewer_counts = Counter(r.get("reviewer_id", "") or "<empty>" for r in csv_rows)
            reviewer_type_counts = Counter(r.get("reviewer_type", "") or r.get("reaudit_reviewer_type", "") or "<missing>" for r in csv_rows)
            decision_col = "qa_final_decision" if "qa_final_decision" in csv_rows[0] else ("reaudit_final_decision" if "reaudit_final_decision" in csv_rows[0] else "")
            decision_counts = Counter(r.get(decision_col, "") or "<empty>" for r in csv_rows) if decision_col else Counter()
            reviewed_at_missing = sum(1 for r in csv_rows if not (r.get("reviewed_at") or r.get("reaudit_reviewed_at")))
            lower_name = path.name.lower()
            can_human = "false"
            notes = "reviewer_type missing or GPT-assisted/unknown; do not call human_confirmed"
            if all((r.get("reviewer_type") == "human_confirmed") for r in csv_rows if "reviewer_type" in r):
                can_human = "true"
                notes = "reviewer_type explicitly human_confirmed"
            elif "gpt" in lower_name or any("gpt" in k.lower() for k in reviewer_counts):
                notes = "GPT-assisted review/re-audit; not human final"
            rows.append(
                {
                    "file": safe_rel(path, root),
                    "rows": len(csv_rows),
                    "reviewer_id_distribution": json.dumps(dict(reviewer_counts), ensure_ascii=False),
                    "reviewer_type_distribution": json.dumps(dict(reviewer_type_counts), ensure_ascii=False),
                    "reviewed_at_missing_count": reviewed_at_missing,
                    "qa_final_decision_distribution": json.dumps(dict(decision_counts), ensure_ascii=False),
                    "can_be_called_human_confirmed": can_human,
                    "notes": notes,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "file": safe_rel(path, root),
                    "rows": -1,
                    "reviewer_id_distribution": "{}",
                    "reviewer_type_distribution": "{}",
                    "reviewed_at_missing_count": -1,
                    "qa_final_decision_distribution": "{}",
                    "can_be_called_human_confirmed": "false",
                    "notes": f"failed to parse: {exc}",
                }
            )
    fieldnames = [
        "file",
        "rows",
        "reviewer_id_distribution",
        "reviewer_type_distribution",
        "reviewed_at_missing_count",
        "qa_final_decision_distribution",
        "can_be_called_human_confirmed",
        "notes",
    ]
    write_csv(out_dir / "REVIEWER_PROVENANCE_TABLE.csv", rows, fieldnames)
    md = ["# Reviewer Provenance Table", "", f"Generated at: {now_iso()}", "", "| file | rows | human_confirmed? | notes |", "|---|---:|---|---|"]
    for r in rows:
        md.append(f"| `{r['file']}` | {r['rows']} | {r['can_be_called_human_confirmed']} | {r['notes']} |")
    (out_dir / "REVIEWER_PROVENANCE_TABLE.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return rows


def row_count_summary(root: Path, out_dir: Path) -> Dict[str, Any]:
    toolbench_v14c = root / "outputs/full_clean_dryrun_v1_4c/full_clean_task_trace_v1_4c.csv"
    toolbench_v15f = root / "outputs/policy_v1_5f_tightening_dryrun/clean_candidates_v1_4c_with_v1_5f_annotations.csv"
    metatool = root / "outputs/external_source_policy_v0_2/metatool/metatool_single_service_with_leakage_policy_v0_2.csv"
    stable = root / "outputs/external_source_policy_v0_2/stabletoolbench/stabletoolbench_solvable_with_filter_policy_v0_2.csv"
    summary = {
        "generated_at": now_iso(),
        "toolbench_v1_4c_count": count_csv_rows(toolbench_v14c) if toolbench_v14c.exists() else None,
        "toolbench_v1_5f_policy_distribution": {},
        "metatool_total_rows": count_csv_rows(metatool) if metatool.exists() else None,
        "metatool_policy_distribution": count_distribution_csv(metatool, "metatool_policy_decision"),
        "stabletoolbench_total_rows": count_csv_rows(stable) if stable.exists() else None,
        "stabletoolbench_exclusive_primary_decision_distribution": count_distribution_csv(stable, "stable_policy_decision"),
        "stabletoolbench_non_exclusive_pool_membership_warning": "Do not use non-exclusive pool counts as clean counts.",
        "reviewed_csv_row_counts": {},
        "v0_1_candidate_package_present": (root / "outputs/service_discovery_bench_v0_1_candidate").exists(),
    }
    if toolbench_v15f.exists():
        rows = read_csv_rows(toolbench_v15f)
        cols = rows[0].keys() if rows else []
        col = "dryrun_decision_v1_5f" if "dryrun_decision_v1_5f" in cols else ("dryrun_decision_v1_4c" if "dryrun_decision_v1_4c" in cols else "")
        summary["toolbench_v1_5f_policy_distribution"] = dict(Counter(r.get(col, "") or "<empty>" for r in rows)) if col else {"decision_column_missing": len(rows)}
        summary["toolbench_v1_5f_rows"] = len(rows)
    for path in reviewed_files(root):
        summary["reviewed_csv_row_counts"][safe_rel(path, root)] = count_csv_rows(path)
    write_json(out_dir / "ROW_COUNT_SANITY_SUMMARY.json", summary)
    lines = [
        "# Row Count Sanity Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        "",
        f"- ToolBench v1.4c count: {summary['toolbench_v1_4c_count']}",
        f"- ToolBench v1.5f rows: {summary.get('toolbench_v1_5f_rows')}",
        f"- ToolBench v1.5f decision distribution: `{json.dumps(summary['toolbench_v1_5f_policy_distribution'], ensure_ascii=False)}`",
        f"- MetaTool total rows: {summary['metatool_total_rows']}",
        f"- MetaTool policy distribution: `{json.dumps(summary['metatool_policy_distribution'], ensure_ascii=False)}`",
        f"- StableToolBench total rows: {summary['stabletoolbench_total_rows']}",
        f"- StableToolBench exclusive primary decision distribution: `{json.dumps(summary['stabletoolbench_exclusive_primary_decision_distribution'], ensure_ascii=False)}`",
        "- StableToolBench warning: non-exclusive pool membership counts must not be used as clean counts.",
        f"- v0.1 candidate package present: {summary['v0_1_candidate_package_present']}",
        "",
        "## Reviewed CSV Row Counts",
    ]
    for file, count in summary["reviewed_csv_row_counts"].items():
        lines.append(f"- `{file}`: {count}")
    (out_dir / "ROW_COUNT_SANITY_SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def git_status(root: Path, out_dir: Path) -> Dict[str, Any]:
    def run_git(args: List[str]) -> str:
        try:
            return subprocess.check_output(["git"] + args, cwd=root, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace").strip()
        except Exception as exc:
            return f"ERROR: {exc}"
    branch = run_git(["branch", "--show-current"])
    commit = run_git(["rev-parse", "HEAD"])
    status = run_git(["status", "--short"])
    handoff_untracked = "outputs/assistant_handoff_2026_07_08" in status
    lines = [
        "# Git Status",
        "",
        f"Generated at: {now_iso()}",
        "",
        f"- current branch: `{branch}`",
        f"- latest commit hash: `{commit}`",
        f"- handoff bundle itself appears in status: `{handoff_untracked}`",
        "- do not commit automatically.",
        "",
        "## git status --short",
        "```text",
        status,
        "```",
    ]
    (out_dir / "GIT_STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"branch": branch, "commit": commit, "handoff_untracked": handoff_untracked}


def current_status_lock(root: Path, out_dir: Path, row_summary: Dict[str, Any], provenance_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    schema_go = root / "docs/schema/UNIFIED_SCHEMA_GO_NO_GO_V0_1.md"
    status = {
        "generated_at": now_iso(),
        "ToolBench-core status": "v1.4c/v1.5f artifacts present; final clean dataset remains blocked unless later QA reconciles failures.",
        "MetaTool status": "v0.2 source policy exists; re-audit layer present if manual_reaudit file copied; not final clean.",
        "StableToolBench status": "v0.2 source policy exists; use exclusive stable_policy_decision only; not final clean.",
        "ShortcutsBench status": "source present and source-check only; no formal task rows released.",
        "unified schema status": "present" if schema_go.exists() else "missing",
        "v0.1-candidate package status": "present" if row_summary.get("v0_1_candidate_package_present") else "not generated",
        "final clean dataset status": "not generated / blocked",
        "split status": "not generated",
        "baseline status": "not generated",
        "Qwen status": "not called in this handoff; previous Qwen artifacts must not be human final",
        "review mode": "source-specific review/re-audit handoff only",
        "reviewer provenance status": "explicit table generated; GPT-assisted must not be called human_confirmed",
    }
    lines = ["# Current Project Status Lock 2026-07-08", "", f"Generated at: {status['generated_at']}", ""]
    for k, v in status.items():
        if k != "generated_at":
            lines.append(f"- **{k}**: {v}")
    (out_dir / "CURRENT_PROJECT_STATUS_LOCK_2026_07_08.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status


def missing_report(builder: HandoffBuilder) -> Tuple[int, int]:
    fatal = 0
    optional = 0
    lines = ["# Missing For Assistant", "", f"Generated at: {now_iso()}", ""]
    groups = ["schema", "reviews", "toolbench_core", "external_policy_v0_2", "v0_1_candidate", "scripts"]
    for group in groups:
        lines.append(f"## {group} missing")
        items = builder.missing.get(group, [])
        if not items:
            lines.append("- none")
        for rel, severity, notes in items:
            if severity == "fatal_for_next_review":
                fatal += 1
            else:
                optional += 1
            lines.append(f"- `{rel}` | {severity} | {notes}")
        lines.append("")
    (builder.out_dir / "MISSING_FOR_ASSISTANT.md").write_text("\n".join(lines), encoding="utf-8")
    return fatal, optional


def handoff_index(out_dir: Path, summary: Dict[str, Any]) -> None:
    lines = [
        "# Assistant Handoff Index 2026-07-08",
        "",
        f"Generated at: {now_iso()}",
        "",
        "This bundle is a reviewer handoff. It does not contain a final clean dataset and does not authorize merge/split/baseline/training.",
        "",
        "## Start Here",
        "- `CURRENT_PROJECT_STATUS_LOCK_2026_07_08.md`",
        "- `ROW_COUNT_SANITY_SUMMARY.md`",
        "- `REVIEWER_PROVENANCE_TABLE.md`",
        "- `MISSING_FOR_ASSISTANT.md`",
        "- `FILE_MANIFEST.csv` / `FILE_MANIFEST.json`",
        "",
        "## Main Artifact Groups",
        "- `schema/` unified schema docs, previews, validation, and scripts",
        "- `reviews/` external review and re-audit CSVs",
        "- `toolbench_core/` ToolBench-core policy/QA artifacts",
        "- `external_policy_v0_2/` MetaTool/StableToolBench source policy artifacts",
        "- `v0_1_candidate/` candidate package planning files if present",
        "- `scripts/` reviewer helper scripts",
        "",
        "## Fixed Boundaries",
        "- no new cleaning",
        "- no final dataset",
        "- no external source merge",
        "- no split",
        "- no baseline",
        "- no training",
        "- no Qwen/API call",
    ]
    (out_dir / "HANDOFF_INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest(builder: HandoffBuilder) -> None:
    fields = ["relative_path", "exists", "file_size_bytes", "modified_time", "sha256", "artifact_group", "required_or_optional", "notes"]
    write_csv(builder.out_dir / "FILE_MANIFEST.csv", builder.manifest, fields)
    write_json(builder.out_dir / "FILE_MANIFEST.json", {"generated_at": now_iso(), "files": builder.manifest})


def zip_handoff(out_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for path in out_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(out_dir.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create ServiceDiscoveryBench assistant handoff bundle for 2026-07-08.")
    parser.add_argument("--project-root", default=".", help="Project root.")
    parser.add_argument("--output-dir", default="outputs/assistant_handoff_2026_07_08", help="Handoff output directory.")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    out_dir = (root / args.output_dir).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    builder = HandoffBuilder(root, out_dir)
    candidates = file_candidates()
    for group, specs in candidates.items():
        for rel, required, notes in specs:
            builder.copy_file(rel, group, required, notes)

    builder.copy_dir("outputs/unified_schema_v0_1/schema", "schema", "helpful_but_optional", "schema directory")
    builder.copy_dir("outputs/unified_schema_v0_1/previews", "schema", "helpful_but_optional", "schema previews")
    builder.copy_dir("outputs/unified_schema_v0_1/validation", "schema", "helpful_but_optional", "schema validation")
    if (root / "outputs/service_discovery_bench_v0_1_candidate").exists():
        builder.copy_dir("outputs/service_discovery_bench_v0_1_candidate", "v0_1_candidate", "helpful_but_optional", "v0.1 candidate package directory")

    for pattern in ["outputs/**/*reaudit*metatool*.csv", "outputs/**/*reaudit*metatool*.json", "outputs/**/*_reviewed.csv", "outputs/**/*_reviewed_draft.csv"]:
        for p in root.glob(pattern):
            if p.is_file() and not is_handoff_path(p, root):
                builder.copy_file(safe_rel(p, root), "reviews", "helpful_but_optional", f"matched pattern {pattern}")

    provenance = reviewer_provenance(root, out_dir)
    row_summary = row_count_summary(root, out_dir)
    status = current_status_lock(root, out_dir, row_summary, provenance)
    git = git_status(root, out_dir)
    missing_fatal, missing_optional = missing_report(builder)
    handoff_index(out_dir, {"status": status, "git": git})
    write_manifest(builder)

    final_status = {
        "handoff_bundle_created": True,
        "handoff_zip_path": str(root / "outputs/assistant_handoff_2026_07_08.zip"),
        "schema_artifacts_present": (root / "docs/schema/SCHEMA_UNIFIED_V0_1.md").exists(),
        "reviewed_csv_present": bool(reviewed_files(root)),
        "metatool_reviewed_present": (root / "outputs/external_qa_v0_2/metatool/metatool_leakage_policy_review_items_v0_2_reviewed.csv").exists(),
        "stabletoolbench_reviewed_present": (root / "outputs/external_qa_v0_2/stabletoolbench/stabletoolbench_filter_policy_review_items_v0_2_reviewed.csv").exists(),
        "metatool_reaudit_present": (root / "outputs/manual_reaudit/metatool_v0_2_reaudit_by_gpt55pro_schema.csv").exists(),
        "toolbench_v1_5f_outputs_present": (root / "outputs/policy_v1_5f_tightening_dryrun/clean_candidates_v1_4c_with_v1_5f_annotations.csv").exists(),
        "external_v0_2_policy_outputs_present": (root / "outputs/external_source_policy_v0_2").exists(),
        "v0_1_candidate_package_present": (root / "outputs/service_discovery_bench_v0_1_candidate").exists(),
        "current_final_dataset_exists": False,
        "current_split_exists": False,
        "current_baseline_exists": False,
        "missing_fatal_count": missing_fatal,
        "missing_optional_count": missing_optional,
        "recommended_next_step_for_reviewer": "Read CURRENT_PROJECT_STATUS_LOCK, ROW_COUNT_SANITY_SUMMARY, reviewer provenance, then reconcile source-specific reviewed CSV/re-audit before any v0.1 candidate package or final dataset work.",
    }
    write_json(out_dir / "HANDOFF_SUMMARY.json", final_status)

    zip_path = root / "outputs/assistant_handoff_2026_07_08.zip"
    write_json(out_dir / "HANDOFF_SUMMARY.json", final_status)
    zip_handoff(out_dir, zip_path)
    final_status["handoff_zip_size_bytes"] = zip_path.stat().st_size
    write_json(out_dir / "HANDOFF_SUMMARY.json", final_status)
    zip_handoff(out_dir, zip_path)
    print(json.dumps(final_status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
