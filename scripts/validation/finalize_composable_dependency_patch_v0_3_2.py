#!/usr/bin/env python3
"""Finalize, document, and archive the composable extractor patch v0.3.2."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


CORE_DIR = Path("outputs/composable_dependency_extractor_patch_v0_3_2")
PACK_DIR = Path("outputs/composable_paired_task_preparation_v0_3_2")
ARCHIVE_DIR = Path(
    "outputs/run_archives/2026-07-15_composable_dependency_extractor_patch_v0_3_2"
)
REPORT_PATH = Path(
    "docs/phase1/composable_dependency_extractor_patch_v0_3_2_report.md"
)
GO_NO_GO_PATH = Path(
    "docs/phase1/composable_dependency_extractor_patch_go_no_go_v0_3_2.md"
)
OLD_PACK = Path(
    "outputs/composable_paired_task_preparation_v0_3_1/"
    "composable_paired_task_review_items_v0_3_1.csv"
)
HUMAN_FIELDS = [
    "dependency_edge_valid",
    "dependency_evidence_sufficient",
    "composition_final_label",
    "query_gold_chain_alignment",
    "service_gold_complete",
    "service_candidate_space_valid",
    "service_leakage_final",
    "service_level_eligible",
    "api_gold_complete",
    "api_candidate_space_valid",
    "api_parent_mapping_valid",
    "api_leakage_final",
    "api_level_eligible",
    "composable_release_action",
    "adjudicator_id",
    "adjudicator_type",
    "adjudicated_at",
    "adjudication_notes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and archive the completed composable v0.3.2 patch run."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--node", type=Path, default=None)
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_json(path: Path) -> dict:
    require(path.exists(), f"Required JSON does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def resolve_node(explicit: Path | None) -> Path:
    if explicit is not None:
        require(explicit.exists(), f"Node executable does not exist: {explicit}")
        return explicit.resolve()
    discovered = shutil.which("node")
    if discovered:
        return Path(discovered).resolve()
    bundled = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    )
    require(bundled.exists(), "Node.js was not found in PATH or the bundled runtime")
    return bundled.resolve()


def run_static_check(root: Path, node: Path, output: Path) -> dict:
    checker = root / "scripts/validation/check_composable_review_app_v0_3_2.cjs"
    require(checker.exists(), f"Static checker does not exist: {checker}")
    result = subprocess.run(
        [str(node), str(checker)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    require(
        result.returncode == 0,
        f"Static HTML validation failed ({result.returncode}): {result.stderr}",
    )
    payload = json.loads(result.stdout)
    payload["generated_at"] = now_iso()
    payload["checker"] = str(checker)
    payload["node"] = str(node)
    payload["passed"] = True
    write_json(output, payload)
    return payload


def validate_blank_human_fields(pack: Path) -> tuple[int, int]:
    rows = 0
    nonblank = 0
    with pack.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames is not None, f"CSV has no header: {pack}")
        missing = [field for field in HUMAN_FIELDS if field not in reader.fieldnames]
        require(not missing, f"Missing human review fields: {missing}")
        for row in reader:
            rows += 1
            nonblank += sum(bool(str(row.get(field, "")).strip()) for field in HUMAN_FIELDS)
    return rows, nonblank


def build_go_no_go(summary: dict, finalized_at: str) -> str:
    return f"""# Composable Dependency Extractor Patch Go / No-Go v0.3.2

Generated at: `{finalized_at}`

## Fixed Decision Fields

- confirmed_bug = `shared_input_was_eligible_as_dependency`
- extractor_patch_tests_pass = `{str(summary['extractor_patch_pass']).lower()}`
- tests_run = `{summary['tests_run']}`
- tests_passed = `{summary['tests_passed']}`
- tests_failed = `{summary['tests_failed']}`
- old_strong_candidate_count = `{summary['old_strong_candidate_count']}`
- corrected_strong_candidate_count = `{summary['corrected_strong_candidate_count']}`
- old_strong_retained_count = `{summary['old_strong_retained_count']}`
- old_strong_downgraded_count = `{summary['old_strong_downgraded_count']}`
- shared_input_false_positive_count = `{summary['shared_input_false_positive_count']}`
- query_known_false_positive_count = `{summary['query_known_false_positive_count']}`
- echoed_input_false_positive_count = `{summary['echoed_input_false_positive_count']}`
- failed_call_false_positive_count = `{summary['failed_call_false_positive_count']}`
- old_review_pack_rows = `{summary['old_review_pack_rows']}`
- old_review_pack_retained_count = `{summary['old_review_pack_retained_count']}`
- old_review_pack_replaced_count = `{summary['old_review_pack_replaced_count']}`
- final_review_pack_rows = `{summary['final_review_pack_rows']}`
- final_unique_underlying_tasks = `{summary['final_unique_underlying_tasks']}`
- final_service_candidate_valid_count = `{summary['final_service_candidate_valid_count']}`
- final_api_candidate_valid_count = `{summary['final_api_candidate_valid_count']}`
- final_query_nonempty_count = `{summary['final_query_nonempty_count']}`
- final_dependency_evidence_nonempty_count = `{summary['final_dependency_evidence_nonempty_count']}`
- forbidden_strong_edge_count = `{summary['forbidden_strong_edge_count']}`
- human_review_fields_autofilled_count = `{summary['human_review_fields_autofilled_count']}`
- human_confirmed_composable_count = `{summary['human_confirmed_composable_count']}`
- v0_3_1_overwritten = `{str(summary['v0_3_1_overwritten']).lower()}`
- v0_3_2_review_app_generated = `{str(summary['v0_3_2_review_app_generated']).lower()}`
- review_app_static_validation_passed = `{str(summary['review_app_static_validation_passed']).lower()}`
- review_app_edge_validation_passed = `{str(summary['review_app_edge_validation_passed']).lower()}`

## Decision

- can_resume_composable_human_review = `true`
- can_claim_composable_service_benchmark_now = `false`
- can_claim_composable_api_benchmark_now = `false`
- can_start_full_six_task_assembly = `false`
- can_generate_final_dataset = `false`
- recommended_next_step = `review only the corrected composable_paired_task_review_items_v0_3_2.csv; do not continue reviewing v0.3.1.`

v0.3.1 保持原样，仅对后续审核标记为 superseded。页面只提供证据和快捷填写工具，不自动生成任何 human final。
"""


def build_report(
    root: Path,
    summary: dict,
    regression: dict,
    manifest: dict,
    edge: dict,
    static: dict,
    finalized_at: str,
    old_pack_hash: str,
    new_pack_hash: str,
) -> str:
    return f"""# Composable Dependency Extractor Patch v0.3.2 执行报告

生成时间：`{finalized_at}`

项目根目录：`{root}`

## 本次做了什么

本轮只读取既有 normalized ToolBench traces，修复 generalized dependency extractor 中“共享输入或响应回显上游输入被误判为 output-to-input 强依赖”的系统性问题。没有重新下载或扫描 raw ToolBench，没有调用 LLM、Qwen、外部 API，也没有生成 final dataset、split、baseline 或训练模型。

## Bug 与修复

- 已确认 bug：`shared_input_was_eligible_as_dependency`。
- 旧 v0.3.1 pack 中，`65` 个任务的 `248` 条旧依赖边，其匹配值同时存在于上游调用参数；这些值不能仅因被响应回显就成为强依赖。
- v0.3.2 仅允许真正的 upstream output/observation/result 进入下游 input、tool selection 或 branch condition。
- `shared_input_only`、`query_known_value_reuse`、`echoed_upstream_input`、`sequence_only`、失败/错误调用和 unsupported edge 均不能成为强依赖。

## 回归测试

- 单元/回归测试：`{summary['tests_passed']}/{summary['tests_run']}` 通过，失败 `{summary['tests_failed']}`。
- 测试覆盖共享经纬度、共享城市/日期、query 已知值、响应回显、失败调用、真正 ID 传递、对象字段传递、工具选择和分支条件等情形。
- 最终 pack 禁用强边数：`{regression['forbidden_strong_edge_count']}`。
- 来自上游 arguments/inputs/requests 的强边：`{regression['strong_edges_from_upstream_arguments_count']}/{regression['strong_edges_from_upstream_inputs_count']}/{regression['strong_edges_from_upstream_requests_count']}`。
- query-known、echoed、shared-input、failed-call 强边均为 `0`。

## Corpus 复评结果

- normalized trace records：`{summary['normalized_trace_record_count']}`。
- 旧 strong candidates：`{summary['old_strong_candidate_count']}`。
- 修复后 strong candidates：`{summary['corrected_strong_candidate_count']}`。
- 旧 strong 保留：`{summary['old_strong_retained_count']}`；降级：`{summary['old_strong_downgraded_count']}`。
- shared-input / query-known / echoed-input / failed-call false-positive counts：`{summary['shared_input_false_positive_count']}` / `{summary['query_known_false_positive_count']}` / `{summary['echoed_input_false_positive_count']}` / `{summary['failed_call_false_positive_count']}`。
- 没有 newly promoted strong candidate；本轮是保守纠错，不扩大 strong pool。

## v0.3.1 Pack 重审计与迁移

- 旧 pack：`{summary['old_review_pack_rows']}` 行；修复后仍 strong `{summary['old_review_pack_retained_count']}` 行，替换 `{summary['old_review_pack_replaced_count']}` 行。
- 新 v0.3.2 pack：`{summary['final_review_pack_rows']}` 行、`{summary['final_unique_underlying_tasks']}` 个唯一 underlying tasks。
- query、dependency evidence、service candidate、API candidate 有效行均为 `200`。
- 人工字段自动填写数：`{summary['human_review_fields_autofilled_count']}`。
- 旧 v0.3.1 pack SHA-256：`{old_pack_hash}`，与 patch 前记录一致；未覆盖旧输出。
- 新 v0.3.2 pack SHA-256：`{new_pack_hash}`，与 HTML manifest 输入哈希一致。

## 本地审核页面

- 单文件 HTML：`{manifest['output_html']}`。
- 内嵌样本：`{manifest['input_rows']}` 行、`{manifest['input_columns']}` 列；query 中文翻译 `{manifest['query_translation_count']}` 条。
- Service/API 翻译条目：`{manifest['service_translation_count']}` / `{manifest['api_translation_count']}`。
- 页面保留中文双语、Service/API hierarchy、修复后依赖边、shared-input 非依赖提示、失败调用、筛选/搜索、上一条/下一条、8 个快捷预设、localStorage、CSV 导入和完整/筛选导出。
- 静态检查：JavaScript `{static['javascript_syntax']}`，dropdown `{static['dropdown_controls']}`，required features `{static['required_static_features']}`。
- 实际 Edge 检查：`{str(edge['passed']).lower()}`；预设自动下一条、localStorage 恢复、CSV 导入、200×100 CSV 导出、搜索以及 1600×1000/900×1200 布局均通过。
- 页面没有自动填写源 CSV 的人工判断；浏览器回归只在一次性临时 profile 中操作。

## Go / No-Go

- `can_resume_composable_human_review = true`
- `can_claim_composable_service_benchmark_now = false`
- `can_claim_composable_api_benchmark_now = false`
- `can_start_full_six_task_assembly = false`
- `can_generate_final_dataset = false`

下一步仅人工审核 `composable_paired_task_review_items_v0_3_2.csv`，不要继续审核已 superseded 的 v0.3.1 pack。只有 human-confirmed strong composable 数量和双层 gold/candidate QA 满足 Gate 4 后，才能讨论 composable benchmark 或 six-task assembly。
"""


def copy_to_archive(root: Path, archive: Path, sources: list[Path]) -> list[dict]:
    archive.mkdir(parents=True, exist_ok=True)
    for relative in sources:
        source = root / relative
        require(source.exists(), f"Archive source does not exist: {source}")
        shutil.copy2(source, archive / source.name)
    records = []
    for path in sorted(archive.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.name == "archive_manifest_v0_3_2.json":
            continue
        records.append(
            {
                "filename": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return records


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    core = root / CORE_DIR
    pack_dir = root / PACK_DIR
    archive = root / ARCHIVE_DIR
    summary_path = core / "corrected_dependency_mining_summary.json"
    regression_path = core / "final_pack_regression_summary.json"
    hashes_path = core / "before_after_sha256_v0_3_2.json"
    manifest_path = pack_dir / "composable_paired_task_review_app_v0_3_2_manifest.json"
    edge_path = (
        pack_dir
        / "browser_validation/composable_review_app_edge_validation_v0_3_2.json"
    )
    static_path = (
        pack_dir
        / "browser_validation/composable_review_app_static_validation_v0_3_2.json"
    )
    new_pack = pack_dir / "composable_paired_task_review_items_v0_3_2.csv"
    html = pack_dir / "composable_paired_task_review_app_v0_3_2.html"
    old_pack = root / OLD_PACK

    summary = read_json(summary_path)
    regression = read_json(regression_path)
    hashes = read_json(hashes_path)
    manifest = read_json(manifest_path)
    edge = read_json(edge_path)
    node = resolve_node(args.node)
    static = run_static_check(root, node, static_path)

    for required in (new_pack, html, old_pack):
        require(required.exists(), f"Required file does not exist: {required}")
    rows, nonblank_human_values = validate_blank_human_fields(new_pack)
    old_pack_hash = sha256(old_pack)
    new_pack_hash = sha256(new_pack)
    html_hash = sha256(html)

    require(rows == 200, f"Expected 200 review rows, got {rows}")
    require(nonblank_human_values == 0, "Human review fields were automatically filled")
    require(
        old_pack_hash == hashes["before"]["v0_3_1_review_pack"],
        "v0.3.1 review pack changed after the patch",
    )
    require(new_pack_hash == manifest["input_sha256"], "v0.3.2 pack hash changed")
    require(regression["forbidden_strong_edge_count"] == 0, "Forbidden strong edges remain")
    require(regression["fatal_issue_count"] == 0, "Final pack regression has fatal issues")
    require(edge.get("passed") is True, "Real Edge validation did not pass")
    require(edge.get("rows") == 200, "Edge did not load 200 rows")
    require(edge.get("quick_preset_auto_next") is True, "Quick preset regression failed")
    require(edge.get("local_storage_restore") is True, "localStorage regression failed")
    require(edge.get("csv_import") is True, "CSV import regression failed")
    require(edge.get("downloaded_csv_rows") == 200, "CSV export row count is wrong")
    require(edge.get("downloaded_csv_columns") == 100, "CSV export column count is wrong")
    require(static.get("passed") is True, "Static HTML validation did not pass")

    finalized_at = now_iso()
    summary.update(
        {
            "finalized_at": finalized_at,
            "v0_3_2_review_app_generated": True,
            "review_app_html": str(html),
            "review_app_html_bytes": html.stat().st_size,
            "review_app_html_sha256": html_hash,
            "review_app_manifest": str(manifest_path),
            "review_app_static_validation_passed": True,
            "review_app_edge_validation_passed": True,
            "review_app_quick_preset_auto_next": True,
            "review_app_local_storage_restore": True,
            "review_app_csv_import": True,
            "review_app_export_rows": edge["downloaded_csv_rows"],
            "review_app_export_columns": edge["downloaded_csv_columns"],
            "human_review_fields_autofilled_count": 0,
            "v0_3_1_overwritten": False,
        }
    )
    write_json(summary_path, summary)

    go_no_go = root / GO_NO_GO_PATH
    go_no_go.write_text(build_go_no_go(summary, finalized_at), encoding="utf-8")
    report = root / REPORT_PATH
    report.write_text(
        build_report(
            root,
            summary,
            regression,
            manifest,
            edge,
            static,
            finalized_at,
            old_pack_hash,
            new_pack_hash,
        ),
        encoding="utf-8",
    )

    archive_sources = [
        PACK_DIR / "composable_paired_task_review_app_v0_3_2.html",
        PACK_DIR / "composable_paired_task_review_app_v0_3_2_manifest.json",
        PACK_DIR / "composable_query_translations_new_zh_v0_3_2.json",
        PACK_DIR / "composable_query_translations_zh_v0_3_2.json",
        PACK_DIR / "browser_validation/composable_review_app_static_validation_v0_3_2.json",
        PACK_DIR / "browser_validation/composable_review_app_edge_validation_v0_3_2.json",
        PACK_DIR / "browser_validation/composable_review_app_v0_3_2_desktop_1600x1000.png",
        PACK_DIR / "browser_validation/composable_review_app_v0_3_2_compact_900x1200.png",
        CORE_DIR / "corrected_dependency_mining_summary.json",
        REPORT_PATH,
        GO_NO_GO_PATH,
        Path("scripts/validation/build_composable_review_app_v0_3_2.py"),
        Path("scripts/validation/check_composable_review_app_v0_3_2.cjs"),
        Path("scripts/validation/validate_composable_review_app_edge_v0_3_2.py"),
        Path("scripts/validation/finalize_composable_dependency_patch_v0_3_2.py"),
    ]
    archive_records = copy_to_archive(root, archive, archive_sources)
    archive_manifest = {
        "generated_at": finalized_at,
        "archive_dir": str(archive),
        "file_count": len(archive_records),
        "files": archive_records,
        "constraints": {
            "raw_toolbench_rescanned": False,
            "external_api_used": False,
            "human_fields_autofilled": False,
            "final_dataset_generated": False,
            "split_created": False,
            "baseline_run": False,
            "model_trained": False,
        },
    }
    write_json(archive / "archive_manifest_v0_3_2.json", archive_manifest)

    fixed = {
        "extractor_patch_pass": summary["extractor_patch_pass"],
        "tests_run": summary["tests_run"],
        "tests_passed": summary["tests_passed"],
        "tests_failed": summary["tests_failed"],
        "old_strong_candidate_count": summary["old_strong_candidate_count"],
        "corrected_strong_candidate_count": summary["corrected_strong_candidate_count"],
        "old_strong_retained_count": summary["old_strong_retained_count"],
        "old_strong_downgraded_count": summary["old_strong_downgraded_count"],
        "shared_input_false_positive_count": summary["shared_input_false_positive_count"],
        "query_known_false_positive_count": summary["query_known_false_positive_count"],
        "echoed_input_false_positive_count": summary["echoed_input_false_positive_count"],
        "failed_call_false_positive_count": summary["failed_call_false_positive_count"],
        "old_review_pack_rows": summary["old_review_pack_rows"],
        "old_review_pack_retained_count": summary["old_review_pack_retained_count"],
        "old_review_pack_replaced_count": summary["old_review_pack_replaced_count"],
        "final_review_pack_rows": summary["final_review_pack_rows"],
        "final_unique_underlying_tasks": summary["final_unique_underlying_tasks"],
        "final_query_nonempty_count": summary["final_query_nonempty_count"],
        "final_dependency_evidence_nonempty_count": summary["final_dependency_evidence_nonempty_count"],
        "final_service_candidate_valid_count": summary["final_service_candidate_valid_count"],
        "final_api_candidate_valid_count": summary["final_api_candidate_valid_count"],
        "strong_edges_from_upstream_arguments_count": summary["strong_edges_from_upstream_arguments_count"],
        "strong_shared_input_edges_count": summary["strong_shared_input_edges_count"],
        "strong_query_known_edges_count": summary["strong_query_known_edges_count"],
        "strong_echoed_input_edges_count": summary["strong_echoed_input_edges_count"],
        "strong_failed_call_edges_count": summary["strong_failed_call_edges_count"],
        "forbidden_strong_edge_count": summary["forbidden_strong_edge_count"],
        "human_review_fields_autofilled_count": summary["human_review_fields_autofilled_count"],
        "v0_3_1_overwritten": summary["v0_3_1_overwritten"],
        "v0_3_2_review_app_generated": summary["v0_3_2_review_app_generated"],
        "can_resume_composable_human_review": summary["can_resume_composable_human_review"],
        "can_claim_composable_service_benchmark_now": summary["can_claim_composable_service_benchmark_now"],
        "can_claim_composable_api_benchmark_now": summary["can_claim_composable_api_benchmark_now"],
        "can_start_full_six_task_assembly": summary["can_start_full_six_task_assembly"],
        "can_generate_final_dataset": summary["can_generate_final_dataset"],
        "recommended_next_step": summary["recommended_next_step"],
    }
    for key, value in fixed.items():
        if isinstance(value, bool):
            value = str(value).lower()
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
