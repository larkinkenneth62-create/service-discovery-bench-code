#!/usr/bin/env python3
"""Build offline tranche-specific composable review apps without changing rows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_composable_review_app_v0_3_3 as base  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a Chinese bilingual single-file composable tranche review app."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--translations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tranche", choices=["A", "B"], required=True)
    parser.add_argument("--source", choices=["ToolBench", "StableToolBench"], required=True)
    parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Review CSV does not exist: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Review CSV has no header: {path}")
        return list(reader), list(reader.fieldnames)


def build_empty_html(source: str, tranche: str, columns: list[str], source_hash: str) -> str:
    title = f"Composable Tranche {tranche} · {source} 人工审核"
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{margin:0;background:#f4f7f9;color:#17232c;font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;letter-spacing:0}}
main{{max-width:900px;margin:48px auto;padding:0 20px}}section{{background:#fff;border:1px solid #d5dee5;border-radius:7px;padding:24px}}
h1{{font-size:24px;margin:0 0 10px}}p,li{{line-height:1.8}}.notice{{border-left:5px solid #9a5d00;background:#fff5df;padding:12px 14px}}
button{{min-height:38px;padding:8px 12px;border:1px solid #9aa9b4;border-radius:6px;background:#fff;cursor:pointer}}
</style></head><body><main><section><h1>{title}</h1>
<p class="notice">当前 tranche 没有通过冻结机器规则 v1.0 的可审核记录。页面没有伪造样本，也没有自动填写任何人工结论。</p>
<ul><li>机器规则只判断结构证据，不产生 human-final composable 标签。</li><li>没有真实 arguments、outputs 或 observations 时，query/schema 不能升级为强依赖证据。</li><li>本页面离线运行，不联网、不调用模型。</li></ul>
<button id="export">导出空 CSV 表头</button></section></main><script>
const COLUMNS={json.dumps(columns, ensure_ascii=False)};document.getElementById('export').onclick=()=>{{const csv='\\ufeff'+COLUMNS.join(',')+'\\r\\n';const u=URL.createObjectURL(new Blob([csv],{{type:'text/csv;charset=utf-8'}}));const a=document.createElement('a');a.href=u;a.download='stabletoolbench_composable_review_tranche_B_empty.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}};
window.__reviewAppTest={{rows:[],sourceHash:{json.dumps(source_hash)}}};</script></body></html>'''


def main() -> int:
    args = parse_args()
    rows, columns = read_csv(args.input)
    missing_human = [field for field in base.HUMAN_FIELDS if field not in columns]
    if missing_human:
        raise ValueError(f"Input is missing frozen human fields: {missing_human}")
    source_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()

    if not rows:
        if not args.allow_empty:
            raise ValueError("Input CSV is empty; pass --allow-empty only for an explicit empty tranche")
        html = build_empty_html(args.source, args.tranche, columns, source_hash)
        translation_count = service_count = api_count = 0
    else:
        translations = base.load_query_translations(args.translations, rows)
        ui = base.build_ui_translations(rows, translations)
        html = (
            base.HTML_TEMPLATE.replace("__ROWS_B64__", base.b64_json(rows))
            .replace("__UI_B64__", base.b64_json(ui))
            .replace("__COLUMNS_JSON__", json.dumps(columns, ensure_ascii=False))
            .replace("__HUMAN_FIELDS_JSON__", json.dumps(base.HUMAN_FIELDS, ensure_ascii=False))
            .replace("__SOURCE_SHA256__", source_hash)
        )
        title = f"Composable Tranche {args.tranche} · {args.source} 联合人工审核"
        subtitle = (
            f"{len(rows)} 条冻结的结构合格候选 · 中文双语 · "
            "Task necessity + Service/API 分层判定 · 离线单文件"
        )
        html = html.replace("Composable v0.3.3 任务必要性联合人工审核", title)
        html = html.replace(
            "97 条结构合格候选（低于 100 条启动门槛） · 中文双语 · Task necessity + Service/API 联合判定 · 离线单文件",
            subtitle,
        )
        html = html.replace("0 / 97", f"0 / {len(rows)}")
        html = html.replace(
            'const STORAGE_KEY="sdbench_composable_review_v033_"+SOURCE_SHA256.slice(0,16);',
            f'const STORAGE_KEY="sdbench_composable_tranche_{args.tranche.lower()}_v102_"+SOURCE_SHA256.slice(0,16);',
        )
        html = html.replace(
            'composable_paired_task_review_items_v0_3_3_filtered.csv',
            f'{args.source.lower()}_composable_review_tranche_{args.tranche}_filtered.csv',
        )
        html = html.replace(
            'composable_paired_task_review_items_v0_3_3_${pending===0?"reviewed":"reviewed_draft"}.csv',
            f'{args.source.lower()}_composable_review_tranche_{args.tranche}_${{pending===0?"reviewed":"reviewed_draft"}}.csv',
        )
        translation_count = len(translations)
        service_count = len(ui["services"])
        api_count = len(ui["apis"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    manifest = {
        "builder_version": "v1.0.2",
        "source": args.source,
        "review_tranche": args.tranche,
        "input_csv": str(args.input.resolve()),
        "input_sha256": source_hash,
        "input_rows": len(rows),
        "query_translation_count": translation_count,
        "service_translation_count": service_count,
        "api_translation_count": api_count,
        "human_fields": base.HUMAN_FIELDS,
        "human_fields_autofilled_count": sum(
            1
            for row in rows
            if any(str(row.get(field, "")).strip() for field in base.HUMAN_FIELDS)
        ),
        "offline_single_file": True,
        "automatic_final_decision": False,
        "output_html": str(args.output.resolve()),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
