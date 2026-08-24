from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, List

from semcap_v1_1_common import (
    ensure_dir,
    grouped_eval,
    now_text,
    read_csv,
    table_lines,
    value_counter,
    write_csv,
    write_json,
    write_md,
)


DEFAULT_OUTPUT_DIR = Path("outputs/semcap_detector_v1_implementation_v1_1")
COMBINED = DEFAULT_OUTPUT_DIR / "combined_semcap_calibration_180.csv"
PREDICTIONS = DEFAULT_OUTPUT_DIR / "semcap_predictions_combined_180_v1.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate SemCap detector v1.1 against human labels.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root. Default: current working directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--combined-input", type=Path, default=COMBINED, help="Combined calibration CSV.")
    parser.add_argument("--predictions-input", type=Path, default=PREDICTIONS, help="SemCap v1 prediction CSV.")
    return parser.parse_args()


def match_rows(rows: List[Dict[str, str]], predictions: Dict[str, Dict[str, str]]) -> List[Dict[str, str]]:
    trace: List[Dict[str, str]] = []
    for row in rows:
        pred = predictions.get(row.get("record_id", ""), {})
        out = dict(row)
        out.update(
            {
                "v1_semantic_alignment_pred": pred.get("semantic_alignment_pred", ""),
                "v1_semantic_alignment_confidence": pred.get("semantic_alignment_confidence", ""),
                "v1_capability_coverage_pred": pred.get("capability_coverage_pred", ""),
                "v1_capability_coverage_confidence": pred.get("capability_coverage_confidence", ""),
                "v1_capability_coverage_reason": pred.get("capability_coverage_reason", ""),
                "v1_coverage_ok_but_policy_blocked_candidate": pred.get("coverage_ok_but_policy_blocked_candidate", ""),
                "semantic_match": "yes" if semantic_match(row.get("semantic_alignment_check", ""), pred.get("semantic_alignment_pred", "")) else "no",
                "capability_match": "yes" if row.get("capability_coverage_check", "") == pred.get("capability_coverage_pred", "") else "no",
                "dangerous_false_keep": "yes"
                if row.get("capability_coverage_check") == "coverage_mismatch"
                and pred.get("capability_coverage_pred") == "coverage_ok"
                and pred.get("capability_coverage_confidence") == "high"
                else "no",
            }
        )
        trace.append(out)
    return trace


def semantic_match(human: str, pred: str) -> bool:
    if human == "ok":
        return pred == "ok"
    if human == "uncertain":
        return pred == "uncertain"
    if human in {"mismatch", "semantic_mismatch"}:
        return pred == "mismatch"
    return human == pred


def compare_to_v0_9(rows: List[Dict[str, str]], v1_pred_by_id: Dict[str, Dict[str, str]]) -> Dict[str, object]:
    total = len(rows)
    v09_cap = sum(1 for row in rows if row.get("pilot_capability_coverage_pred") == row.get("capability_coverage_check"))
    v09_sem = sum(1 for row in rows if semantic_match(row.get("semantic_alignment_check", ""), row.get("pilot_semantic_alignment_pred", "")))
    v1_cap = sum(1 for row in rows if v1_pred_by_id.get(row.get("record_id", ""), {}).get("capability_coverage_pred") == row.get("capability_coverage_check"))
    v1_sem = sum(1 for row in rows if semantic_match(row.get("semantic_alignment_check", ""), v1_pred_by_id.get(row.get("record_id", ""), {}).get("semantic_alignment_pred", "")))
    return {
        "sample_count": total,
        "v0_9_semantic_agreement": f"{v09_sem / total:.1%}" if total else "n/a",
        "v1_semantic_agreement": f"{v1_sem / total:.1%}" if total else "n/a",
        "v0_9_capability_agreement": f"{v09_cap / total:.1%}" if total else "n/a",
        "v1_capability_agreement": f"{v1_cap / total:.1%}" if total else "n/a",
        "v0_9_capability_match_count": v09_cap,
        "v1_capability_match_count": v1_cap,
    }


def make_report(output_path: Path, rows: List[Dict[str, str]], predictions: Dict[str, Dict[str, str]], summary: Dict[str, object], comparison: Dict[str, object], input_paths: List[Path]) -> None:
    dangerous_rows = [
        row
        for row in rows
        if row.get("capability_coverage_check") == "coverage_mismatch"
        and predictions.get(row.get("record_id", ""), {}).get("capability_coverage_pred") == "coverage_ok"
        and predictions.get(row.get("record_id", ""), {}).get("capability_coverage_confidence") == "high"
    ]
    lines = [
        "# SemCap Detector v1.1 Eval Report",
        "",
        f"Generated time: {now_text()}",
        "",
        "Input files:",
        *[f"- `{path}`" for path in input_paths],
        "",
        f"Sample count: {len(rows)}",
        "",
        "Scope: detector evaluation only. No full cleaning, split, baseline, or model training was run.",
        "",
        "## Round3 Eval",
        "",
        *metric_lines(summary["round3"]),
        "",
        "## SemCap80 Eval",
        "",
        *metric_lines(summary["semcap80"]),
        "",
        "## Combined Eval",
        "",
        *metric_lines(summary["combined"]),
        "",
        "## v1 compared with v0.9",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for key, value in comparison.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Dangerous False Keep",
            "",
            f"Count: {len(dangerous_rows)}",
            "",
            "| record_id | task_id | human capability | v1 pred | reason |",
            "|---|---|---|---|---|",
        ]
    )
    for row in dangerous_rows[:20]:
        pred = predictions.get(row.get("record_id", ""), {})
        lines.append(
            f"| {row.get('record_id')} | {row.get('task_id')} | {row.get('capability_coverage_check')} | "
            f"{pred.get('capability_coverage_pred')} | {pred.get('capability_coverage_reason', '')[:180]} |"
        )
    lines.extend(
        [
            "",
            "## Recommendation Signals",
            "",
            "- Passing suggestion remains conservative: dangerous_false_keep should be 0.",
            "- coverage_mismatch_capture should be at least 90%.",
            "- high-confidence coverage_ok precision-like should be at least 85% before trusting high-confidence ok routing.",
            "- Even if these pass, this does not authorize full cleaning.",
        ]
    )
    write_md(output_path, lines)


def metric_lines(metrics: Dict[str, object]) -> List[str]:
    lines = ["| metric | value |", "|---|---|"]
    for key, value in metrics.items():
        lines.append(f"| {key} | {value} |")
    return lines


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    output_dir = root / args.output_dir
    ensure_dir(output_dir)

    rows = read_csv(root / args.combined_input)
    pred_rows = read_csv(root / args.predictions_input)
    predictions = {row.get("record_id", ""): row for row in pred_rows}

    trace = match_rows(rows, predictions)
    fieldnames = list(trace[0].keys()) if trace else []
    write_csv(output_dir / "semcap_v1_eval_trace_combined_180.csv", trace, fieldnames)

    summary = {
        "generated_time": now_text(),
        "input_rows": len(rows),
        "prediction_rows": len(pred_rows),
        "round3": grouped_eval(rows, predictions, "round3"),
        "semcap80": grouped_eval(rows, predictions, "semcap80"),
        "combined": grouped_eval(rows, predictions, None),
        "human_capability_distribution": value_counter(rows, "capability_coverage_check"),
        "v1_capability_distribution": value_counter(pred_rows, "capability_coverage_pred"),
        "v1_semantic_distribution": value_counter(pred_rows, "semantic_alignment_pred"),
    }
    comparison = compare_to_v0_9(rows, predictions)
    summary["v1_vs_v0_9"] = comparison
    write_json(output_dir / "semcap_v1_eval_summary.json", summary)
    make_report(root / "docs/phase1/semcap_detector_v1_1_eval_report.md", rows, predictions, summary, comparison, [args.combined_input, args.predictions_input])

    print("Wrote SemCap v1.1 eval outputs.")
    print("dangerous_false_keep:", summary["combined"]["dangerous_false_keep"])
    print("coverage_mismatch_capture:", summary["combined"]["coverage_mismatch_capture"])
    print("high_confidence_coverage_ok_precision_like:", summary["combined"]["high_confidence_coverage_ok_precision_like"])
    print("coverage_ok_recall:", summary["combined"]["coverage_ok_recall"])
    print("No full cleaning, split, baseline, or model training was run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
