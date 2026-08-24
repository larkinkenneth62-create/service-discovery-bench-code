from __future__ import annotations

import argparse
from collections import Counter

from full_clean_v1_4c_common import (
    DOC_DIR,
    OUTPUT_DIR,
    V15D_FAILED_QA_IDS,
    V15D_REVIEW_SET,
    ensure_dir,
    now_text,
    read_csv,
    table_lines,
    write_json,
    write_md,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write v1.5d failure taxonomy and v1.4c tightening rule documents.")
    parser.parse_args()
    ensure_dir(OUTPUT_DIR)
    rows = read_csv(V15D_REVIEW_SET) if V15D_REVIEW_SET.exists() else []
    by_id = {row.get("qa_item_id", ""): row for row in rows}
    failed_rows = [by_id[qa_id] for qa_id in V15D_FAILED_QA_IDS if qa_id in by_id]
    missing_ids = [qa_id for qa_id in V15D_FAILED_QA_IDS if qa_id not in by_id]
    core_req_counts = Counter(row.get("v12_core_requirements_json", "") for row in failed_rows)
    subbucket_counts = Counter(row.get("qa_subbucket", "") for row in failed_rows)
    source_group_counts = Counter(row.get("source_group", "") for row in failed_rows)

    failure_table = [
        "| qa_item_id | task_id | source_group | v12_core_requirements_json | primary failure signal |",
        "|---|---|---|---|---|",
    ]
    for row in failed_rows:
        signal = "general_information_fallback" if row.get("v12_core_requirements_json") == '["general_information"]' else row.get("qa_subbucket", "")
        failure_table.append(
            f"| {row.get('qa_item_id')} | {row.get('task_id')} | {row.get('source_group')} | `{row.get('v12_core_requirements_json')}` | {signal} |"
        )

    write_md(
        DOC_DIR / "final_qa_v1_5d_failure_taxonomy.md",
        [
            "# Final QA v1.5d Failure Taxonomy",
            "",
            f"Generated time: {now_text()}",
            f"Input review set: `{V15D_REVIEW_SET}`",
            f"Failed current clean-candidate labels supplied by v1.5d QA: {len(V15D_FAILED_QA_IDS)}",
            f"Matched labels in review set: {len(failed_rows)}",
            "",
            "v1.5d did not pass. The 32 previous failed regression items were correctly moved out of clean candidate by v1.4b, but the current clean-candidate audit still found about 32 major/critical false keeps among 100 current clean candidates.",
            "",
            "These QA IDs are regression labels only. They are not used as hard-coded clean/removal rules in v1.4c policy.",
            "",
            "## Main Failure Modes",
            "",
            "1. `general_information` fallback is too broad. Multi-requirement queries can pass as `coverage_ok` without explicit requirement extraction.",
            "2. Gold-set integrity is still too weak for service-level discovery. Unrelated services such as crypto, IPL, energy price, climate feed, or platform-mismatched services can remain in gold.",
            "3. Generic search/news/image APIs are still overtrusted for recommendation, venue, housing, coworking, book-cover-by-title, public holiday, celebrity voice, event planning, and similar domain-specific lookup tasks.",
            "4. Some query subrequirements are only partially covered. This should not be high-confidence clean.",
            "",
            "## Failed QA IDs",
            "",
            *failure_table,
            "",
            "## v12 Core Requirement Distribution Among Failed Clean Candidates",
            "",
            *table_lines(core_req_counts),
            "",
            "## v1.5d Subbucket Distribution Among Failed Clean Candidates",
            "",
            *table_lines(subbucket_counts),
            "",
            "## Source Group Distribution",
            "",
            *table_lines(source_group_counts),
            "",
            "## Missing QA IDs",
            "",
            *([f"- `{qa_id}`" for qa_id in missing_ids] if missing_ids else ["- None"]),
            "",
            "## Release Gate Consequence",
            "",
            "Because current clean-candidate major/critical failures are far above the 5% threshold, v1.5d cannot authorize v1.6.",
        ],
    )

    write_md(
        DOC_DIR / "semcap_v1_3_tightening_rules_candidate.md",
        [
            "# SemCap v1.3 Tightening Rules Candidate",
            "",
            f"Generated time: {now_text()}",
            "",
            "This document records the v1.4c-targeted SemCap tightening direction. It is a dry-run rule candidate, not a final dataset rule freeze.",
            "",
            "## Rule A: General-Information Fallback Cannot Produce High-Confidence Clean",
            "",
            "If `v12_core_requirements_json` is only `[\"general_information\"]` but the query contains multiple explicit actions, entities, domains, or subrequirements, the sample cannot remain `dryrun_clean_candidate`.",
            "",
            "Target bucket: `uncertain_semcap_general_information_fallback` unless another stronger mismatch or wrong-gold-set rule applies.",
            "",
            "## Rule B: Service-Level Gold-Set Integrity Hard Gate",
            "",
            "For service-level tasks, the gold service/API set must not contain unrelated extra services. If gold includes a service family not requested by the query, the sample should move to `removed_wrong_gold_set`.",
            "",
            "Examples of extra-service families detected in v1.5d include crypto/Bybit, IPL, Energy Price News, Climate News Feed, Instagram for TikTok queries, and unrelated random-image services.",
            "",
            "## Rule C: Explicit Domain Requirement Match",
            "",
            "Domain-specific requirements must be explicitly covered by gold services/APIs. Generic search, generic news, generic image, generic place, or adjacent information APIs are not enough for:",
            "",
            "- recommendation tasks",
            "- venue/coworking/housing/hotel/public-holiday lookup",
            "- celebrity voice mimic",
            "- book-cover-by-title/author",
            "- event speaker / conference venue / breakout tips",
            "- fundraising ideas",
            "- nearest restaurant plus menu prices",
            "- source-specific news feeds",
            "- historical or period-specific currency rates",
            "",
            "## Rule D: Clean Candidate Requirements",
            "",
            "A sample can remain high-confidence clean only when every explicit query subrequirement maps to at least one gold service/API, there is no unrelated extra gold service, and the decision is not based on general-information fallback.",
            "",
            "## Boundary",
            "",
            "These rules still produce a dry-run trace only. They do not generate final clean data, split, baseline, or training artifacts.",
        ],
    )

    write_md(
        DOC_DIR / "policy_v1_4c_tightening_plan.md",
        [
            "# Policy v1.4c Tightening Plan",
            "",
            f"Generated time: {now_text()}",
            "",
            "v1.4c is a targeted dry-run tightening pass after v1.5d failed. It does not modify raw data or v1.4b outputs.",
            "",
            "## Inputs",
            "",
            "- `outputs/final_qa_v1_5d/final_qa_review_items_v1_5d.csv`",
            "- `outputs/full_clean_dryrun_v1_4b/full_clean_task_trace_v1_4b.csv`",
            "- `outputs/full_clean_dryrun_v1_4b/full_raw_semcap_v1_2_trace_v1_4b.csv`",
            "- `docs/phase1/semcap_v1_2_tightening_rules_candidate.md`",
            "- `docs/phase1/policy_tightening_plan_v1_5c.md`",
            "",
            "## Policy Behavior",
            "",
            "1. Preserve all v1.4b non-clean decisions.",
            "2. Apply v1.4c gates only to v1.4b `dryrun_clean_candidate` rows.",
            "3. Move general-information fallback multi-requirement cases to `dryrun_uncertain` / `uncertain_semcap_general_information_fallback`.",
            "4. Move clear wrong-gold-set cases to `dryrun_removed` / `removed_wrong_gold_set`.",
            "5. Move explicit domain capability gaps to `dryrun_removed` / `removed_capability_mismatch`.",
            "6. Keep remaining clean candidates as dry-run clean candidates only, pending v1.5e QA.",
            "",
            "## Regression Gate",
            "",
            "All 32 v1.5d failed current clean candidates must move out of `dryrun_clean_candidate` in v1.4c. If any remains clean, v1.4c is a No-Go.",
            "",
            "## Next Step",
            "",
            "If the regression gate passes, the next step is `v1.5e small clean-candidate QA only`, max 100 rows. It is still not v1.6.",
        ],
    )

    write_json(
        OUTPUT_DIR / "v1_5d_failed_clean_candidate_labels_v1_4c.json",
        {
            "generated_time": now_text(),
            "input_review_set": str(V15D_REVIEW_SET),
            "failed_qa_ids": V15D_FAILED_QA_IDS,
            "matched_count": len(failed_rows),
            "missing_qa_ids": missing_ids,
            "task_ids": [row.get("task_id", "") for row in failed_rows],
            "note": "These labels are used for regression evaluation only, not as hard-coded v1.4c policy rules.",
        },
    )
    print(f"matched_failed_labels={len(failed_rows)}")
    print(f"missing_failed_labels={missing_ids}")
    print(f"docs={DOC_DIR / 'final_qa_v1_5d_failure_taxonomy.md'}, {DOC_DIR / 'semcap_v1_3_tightening_rules_candidate.md'}, {DOC_DIR / 'policy_v1_4c_tightening_plan.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
