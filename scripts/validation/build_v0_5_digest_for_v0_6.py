#!/usr/bin/env python
"""Build v0.5 input digest for v0.6 rule revision."""

from __future__ import annotations

import argparse
from collections import Counter

from rule_revision_v0_6_utils import (
    DOCS_DIR,
    OUTPUT_DIR,
    REQUIRED_V05_DOCS,
    REQUIRED_V05_OUTPUTS,
    ensure_dirs,
    file_profile,
    load_round2_final,
    missing_required_inputs,
    now_str,
    pct,
    write_json,
    write_missing_inputs,
)


DIGEST_JSON = OUTPUT_DIR / "v0_5_input_digest.json"
DIGEST_MD = OUTPUT_DIR / "v0_5_input_digest.md"
CAVEAT_MD = DOCS_DIR / "v0_5_interpretation_caveat_for_v0_6.md"


def subset_stats(rows: list[dict], label: str) -> dict:
    decisions = Counter(str(row.get("decision_norm", "")) for row in rows)
    leakage = Counter(str(row.get("leakage_norm", "")) for row in rows)
    semantic = Counter(str(row.get("semantic_norm", "")) for row in rows)
    categories = Counter(str(row.get("user_feedback_category", "") or "<EMPTY>") for row in rows)
    return {
        "label": label,
        "row_count": len(rows),
        "manual_final_decision_distribution": dict(decisions),
        "leakage_distribution": dict(leakage),
        "semantic_distribution": dict(semantic),
        "user_feedback_category_distribution": dict(categories),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build v0.5 digest for v0.6.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def main() -> int:
    parse_args()
    ensure_dirs()
    missing = missing_required_inputs()
    if missing:
        out = write_missing_inputs(missing)
        print(f"ERROR: missing required inputs. See {out}")
        return 2

    all_required = list(REQUIRED_V05_DOCS) + list(REQUIRED_V05_OUTPUTS)
    profiles = [file_profile(path) for path in all_required]
    final_rows = load_round2_final()
    overlay_rows = [row for row in final_rows if row.get("is_overlay")]
    draft_rows = [row for row in final_rows if not row.get("is_overlay")]

    digest = {
        "generated_at": now_str(),
        "input_profiles": profiles,
        "round2_human_final_path": str(REQUIRED_V05_OUTPUTS[0]),
        "subset_stats": {
            "all_80": subset_stats(final_rows, "all_80"),
            "user_feedback_overlay": subset_stats(overlay_rows, "user_feedback_overlay"),
            "draft_retained": subset_stats(draft_rows, "draft_retained"),
        },
        "interpretation_caveat": {
            "normalized_final_not_independent_relabel": True,
            "overlay_subset_is_primary_failure_source": True,
            "draft_vs_human_agreement_limited_by_overlay_construction": True,
        },
        "scope": {
            "full_cleaning": False,
            "split": False,
            "baseline": False,
            "training": False,
        },
    }
    write_json(DIGEST_JSON, digest)

    lines = [
        "# v0.5 Input Digest for v0.6",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 输入文件",
        "",
        "| path | exists | rows/lines | key columns |",
        "|---|---:|---:|---|",
    ]
    for item in profiles:
        rows_or_lines = item.get("row_count")
        if rows_or_lines is None:
            rows_or_lines = item.get("line_count", "")
        cols = ", ".join(item.get("columns", [])[:12])
        lines.append(f"| `{item['path']}` | {item['exists']} | {rows_or_lines} | `{cols}` |")

    lines.extend(
        [
            "",
            "## Round2 Human Final 子集",
            "",
            "| subset | rows | keep | remove | uncertain |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for key in ["all_80", "user_feedback_overlay", "draft_retained"]:
        item = digest["subset_stats"][key]
        dist = item["manual_final_decision_distribution"]
        lines.append(
            f"| `{key}` | {item['row_count']} | {dist.get('keep_for_cleaning_candidate', 0)} | "
            f"{dist.get('remove', 0)} | {dist.get('uncertain', 0)} |"
        )

    lines.extend(
        [
            "",
            "## 解释限制",
            "",
            "- v0.5 的 human final 是 normalized final，不应被误解为 80 条完全独立人工重标。",
            "- 其中 24 条来自用户明确 overlay 修正，是 v0.6 失败模式分析的最重要证据。",
            "- 其余 56 条保留 assistant draft，代表用户未点名修正或默认接受，但不能当成独立人机一致率。",
            "- draft-vs-human agreement 只能用于定位差异，不应作为自动检测器已可靠的证据。",
            "",
            "## Scope",
            "",
            "- 没有 full cleaning。",
            "- 没有 split。",
            "- 没有 baseline。",
            "- 没有训练模型。",
        ]
    )
    DIGEST_MD.write_text("\n".join(lines), encoding="utf-8")

    caveat = [
        "# v0.5 Interpretation Caveat for v0.6",
        "",
        f"生成时间：{now_str()}",
        "",
        "## 样本数量",
        "",
        f"- all Round2 normalized final: `{len(final_rows)}`",
        f"- user_feedback_overlay subset: `{len(overlay_rows)}`",
        f"- draft_retained subset: `{len(draft_rows)}`",
        "",
        "## 必须避免的误读",
        "",
        "v0.5 的 `round2_manual_decisions_80_user_approved.normalized_from_user_overlay.csv` "
        "是为了推进验证而生成的 normalized final。它不是 80 条完全独立、逐条重新标注的人审 gold。",
        "",
        "24 条 user overlay 修正样本直接反映了用户指出的错误，是 v0.6 failure mode taxonomy 的主要证据来源。",
        "",
        "56 条 draft-retained 样本只能说明用户没有在上一轮反馈中点名修正；它们不能被解释成完全独立的人审确认。",
        "",
        "因此 v0.6 不应把 72.5% draft-vs-human agreement 解释为自动系统已经可靠。"
        "它只能说明：在用户明确修正后的 final signal 下，哪些地方需要规则修订。",
        "",
        "## 对 full cleaning 的含义",
        "",
        "v0.5/v0.6 证明的是 audited fields 上的 decision policy，而不是 raw 数据上的自动检测器已经可靠。",
        "full cleaning、split 和 baseline 仍然必须保持 false。",
    ]
    CAVEAT_MD.write_text("\n".join(caveat), encoding="utf-8")

    print(f"v0_5_input_digest_json={DIGEST_JSON}")
    print(f"v0_5_input_digest_md={DIGEST_MD}")
    print(f"v0_5_interpretation_caveat={CAVEAT_MD}")
    print(
        "subset_counts="
        f"all:{len(final_rows)},overlay:{len(overlay_rows)},draft_retained:{len(draft_rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
