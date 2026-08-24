from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from unified_schema_v0_1_common import ensure_dirs, find_inputs, now_iso, try_json, write_csv, write_json


def infer_type(values: List[str]) -> str:
    nonempty = [v for v in values if str(v).strip() != ""]
    if not nonempty:
        return "empty"
    int_ok = 0
    float_ok = 0
    json_ok = 0
    for value in nonempty[:100]:
        try:
            int(str(value))
            int_ok += 1
        except Exception:
            pass
        try:
            float(str(value))
            float_ok += 1
        except Exception:
            pass
        ok, _ = try_json(str(value))
        if ok:
            json_ok += 1
    if json_ok == len(nonempty[:100]):
        return "json"
    if int_ok == len(nonempty[:100]):
        return "integer"
    if float_ok == len(nonempty[:100]):
        return "number"
    return "string"


def notes_for_column(name: str) -> str:
    low = name.lower()
    notes = []
    if "id" in low:
        notes.append("id-like")
    if "query" in low:
        notes.append("query-field")
    if "candidate" in low and "service" in low:
        notes.append("candidate-service-field")
    if "gold" in low and "service" in low:
        notes.append("gold-service-field")
    if "candidate" in low and ("api" in low or "tool" in low):
        notes.append("candidate-api-field")
    if "gold" in low and ("api" in low or "tool" in low):
        notes.append("gold-api-field")
    if "policy" in low or "decision" in low or "status" in low:
        notes.append("policy-or-decision-field")
    if low.startswith("qa_") or "review" in low:
        notes.append("qa-or-review-field")
    if "reaudit" in low:
        notes.append("reaudit-field")
    if "leak" in low:
        notes.append("leakage-field")
    if "task_type" in low:
        notes.append("task-type-field")
    return "; ".join(notes)


def inventory_csv(source_branch: str, path: Path, max_rows: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            rows.append({k: (v if v is not None else "") for k, v in row.items()})
    out = []
    for col in fieldnames:
        values = [r.get(col, "") for r in rows]
        non_null = [v for v in values if str(v).strip() != ""]
        enum_values = sorted(Counter(non_null).keys()) if 0 < len(set(non_null)) <= 20 else []
        json_true = sum(1 for v in non_null if try_json(str(v))[0])
        out.append(
            {
                "source_branch": source_branch,
                "source_file": str(path),
                "column_name": col,
                "inferred_type": infer_type(values),
                "non_null_count": len(non_null),
                "null_count": len(values) - len(non_null),
                "example_value": next((str(v)[:500] for v in non_null), ""),
                "possible_enum_values_if_small": json.dumps(enum_values, ensure_ascii=False),
                "json_parseable_true_false": "true" if non_null and json_true == len(non_null) else ("partial" if json_true else "false"),
                "notes": notes_for_column(col),
            }
        )
    return out


def inventory_json(source_branch: str, path: Path, max_rows: int) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        data = json.loads(text)
    except Exception as exc:
        return [
            {
                "source_branch": source_branch,
                "source_file": str(path),
                "column_name": "__json_parse_error__",
                "inferred_type": "error",
                "non_null_count": 0,
                "null_count": 0,
                "example_value": str(exc),
                "possible_enum_values_if_small": "[]",
                "json_parseable_true_false": "false",
                "notes": "JSON source-check file parse failed",
            }
        ]
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            records = data["data"][:max_rows]
        else:
            records = [data]
    elif isinstance(data, list):
        records = data[:max_rows]
    else:
        records = [{"value": data}]
    keys = sorted({k for r in records if isinstance(r, dict) for k in r.keys()})
    out = []
    for key in keys:
        values = [json.dumps(r.get(key, ""), ensure_ascii=False) if isinstance(r.get(key), (dict, list)) else str(r.get(key, "")) for r in records if isinstance(r, dict)]
        non_null = [v for v in values if str(v).strip() not in ("", "null")]
        out.append(
            {
                "source_branch": source_branch,
                "source_file": str(path),
                "column_name": key,
                "inferred_type": infer_type(values),
                "non_null_count": len(non_null),
                "null_count": len(values) - len(non_null),
                "example_value": next((str(v)[:500] for v in non_null), ""),
                "possible_enum_values_if_small": json.dumps(sorted(set(non_null)) if 0 < len(set(non_null)) <= 20 else [], ensure_ascii=False),
                "json_parseable_true_false": "true" if non_null and all(try_json(v)[0] for v in non_null) else "false",
                "notes": notes_for_column(key),
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory source fields for ServiceDiscoveryBench unified schema v0.1.")
    parser.add_argument("--project-root", default=".", help="Project root path.")
    parser.add_argument("--max-rows", type=int, default=1000, help="Rows to inspect per source file.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    ensure_dirs(project_root)
    inputs = find_inputs(project_root)
    branch_map = {
        "toolbench_core": "ToolBench-core",
        "metatool_policy": "MetaTool-single",
        "metatool_reviewed": "MetaTool-single-reviewed",
        "metatool_reaudit": "MetaTool-single-reaudit",
        "stable_policy": "StableToolBench-solvable",
        "stable_reviewed": "StableToolBench-solvable-reviewed",
        "shortcuts_source": "ShortcutsBench-source-check",
    }
    inventory: List[Dict[str, Any]] = []
    missing = []
    for key, path in inputs.items():
        if not path:
            missing.append(key)
            continue
        branch = branch_map[key]
        if path.suffix.lower() == ".csv":
            inventory.extend(inventory_csv(branch, path, args.max_rows))
        elif path.suffix.lower() in {".json", ".jsonl"}:
            inventory.extend(inventory_json(branch, path, args.max_rows))

    out_dir = project_root / "outputs" / "unified_schema_v0_1"
    docs_dir = project_root / "docs" / "schema"
    write_csv(out_dir / "source_field_inventory.csv", inventory)
    write_json(out_dir / "source_field_inventory.json", {"generated_at": now_iso(), "missing_inputs": missing, "fields": inventory})

    lines = [
        "# Source Field Inventory V0.1",
        "",
        f"Generated at: {now_iso()}",
        "",
        "This inventory scans current source CSV/JSON files and records actual columns. It is not a final dataset.",
        "",
        f"Scanned field entries: {len(inventory)}",
        f"Missing source keys: {', '.join(missing) if missing else 'none'}",
        "",
        "| source_branch | columns | source_files |",
        "|---|---:|---|",
    ]
    by_branch: Dict[str, List[Dict[str, Any]]] = {}
    for row in inventory:
        by_branch.setdefault(str(row["source_branch"]), []).append(row)
    for branch, rows in sorted(by_branch.items()):
        files = sorted({Path(str(r["source_file"])).name for r in rows})
        lines.append(f"| {branch} | {len(rows)} | {', '.join(files)} |")
    lines.extend(["", "Detailed inventory is in `outputs/unified_schema_v0_1/source_field_inventory.csv`."])
    (docs_dir / "SOURCE_FIELD_INVENTORY_V0_1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if missing:
        missing_lines = [
            "# Missing Inputs For Unified Schema V0.1",
            "",
            f"Generated at: {now_iso()}",
            "",
            "Schema design can continue, but previews for missing branches may be unavailable.",
            "",
        ]
        for key in missing:
            missing_lines.append(f"- {key}: source-specific preview may be missing or partial.")
        (out_dir / "MISSING_INPUTS.md").write_text("\n".join(missing_lines) + "\n", encoding="utf-8")

    print(f"inventory_rows={len(inventory)}")
    print(f"missing_inputs={','.join(missing) if missing else 'none'}")
    print(f"output_csv={out_dir / 'source_field_inventory.csv'}")


if __name__ == "__main__":
    main()

