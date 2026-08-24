from __future__ import annotations

import argparse
import csv
from pathlib import Path

from full_clean_v1_4_common import (
    DOC_DIR,
    OUTPUT_DIR,
    RAW_CANDIDATE,
    RAW_CANDIDATE_REQUIRED,
    RAW_TASK,
    RAW_TASK_REQUIRED,
    SEMCAP_EVAL_REPORT,
    SEMCAP_RULE_DOC,
    SEMCAP_SCRIPT,
    V1_2_DANGER,
    V1_2_GO_NO_GO,
    V1_3_REPORTS,
    V4_2_POLICY,
    ensure_dir,
    now_text,
    write_json,
    write_md,
)


def inspect_csv(path: Path, required: list[str], sample_size: int) -> dict:
    if not path.exists():
        return {"exists": False, "path": str(path), "missing_required_columns": required}
    row_count = 0
    samples: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        for row in reader:
            row_count += 1
            if len(samples) < sample_size:
                samples.append(dict(row))
    missing = [field for field in required if field not in columns]
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "missing_required_columns": missing,
        "size_bytes": stat.st_size,
        "last_modified": now_text(),
        "sample_rows": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check v1.4 full clean dry-run inputs.")
    parser.add_argument("--raw-task", type=Path, default=RAW_TASK)
    parser.add_argument("--raw-candidate", type=Path, default=RAW_CANDIDATE)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--sample-size", type=int, default=3)
    args = parser.parse_args()

    ensure_dir(args.output_dir)
    task_info = inspect_csv(args.raw_task, RAW_TASK_REQUIRED, args.sample_size)
    candidate_info = inspect_csv(args.raw_candidate, RAW_CANDIDATE_REQUIRED, args.sample_size)
    dependency_paths = [
        V4_2_POLICY,
        SEMCAP_SCRIPT,
        SEMCAP_RULE_DOC,
        SEMCAP_EVAL_REPORT,
        V1_2_DANGER,
        V1_2_GO_NO_GO,
        *V1_3_REPORTS,
    ]
    dependencies = {str(path): path.exists() for path in dependency_paths}
    missing_inputs = []
    if not task_info.get("exists"):
        missing_inputs.append(str(args.raw_task))
    if not candidate_info.get("exists"):
        missing_inputs.append(str(args.raw_candidate))
    missing_dependencies = [path for path, ok in dependencies.items() if not ok]
    fatal = bool(
        missing_inputs
        or task_info.get("missing_required_columns")
        or candidate_info.get("missing_required_columns")
        or missing_dependencies
    )
    payload = {
        "generated_time": now_text(),
        "task_csv": task_info,
        "candidate_csv": candidate_info,
        "dependencies": dependencies,
        "missing_required_inputs": missing_inputs,
        "missing_dependencies": missing_dependencies,
        "fatal_input_error": fatal,
        "no_full_cleaning_no_split_no_baseline_no_training": True,
    }
    write_json(args.output_dir / "input_schema_summary.json", payload)

    lines = [
        "# Full Clean Dry-Run v1.4 Input Check",
        "",
        f"Generated time: {now_text()}",
        f"Task input: `{args.raw_task}`",
        f"Candidate input: `{args.raw_candidate}`",
        "",
        "This check only validates inputs. It does not run full cleaning, split, baseline, or training.",
        "",
        "## Row Counts",
        "",
        f"- Task rows: {task_info.get('row_count', 'missing')}",
        f"- Candidate rows: {candidate_info.get('row_count', 'missing')}",
        "",
        "## Missing Required Columns",
        "",
        f"- Task CSV: {task_info.get('missing_required_columns', [])}",
        f"- Candidate CSV: {candidate_info.get('missing_required_columns', [])}",
        "",
        "## Dependency Check",
        "",
        *[f"- `{path}`: {'exists' if ok else 'MISSING'}" for path, ok in dependencies.items()],
        "",
        "## Decision",
        "",
        f"- Fatal input error: {fatal}",
    ]
    write_md(args.output_dir / "input_check_report.md", lines)
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
