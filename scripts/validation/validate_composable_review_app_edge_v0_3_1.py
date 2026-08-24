#!/usr/bin/env python3
"""Validate the composable v0.3.1 review app in a disposable headless Edge profile."""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import websockets


DEFAULT_HTML = Path(
    "outputs/composable_paired_task_preparation_v0_3_1/"
    "composable_paired_task_review_app_v0_3_1.html"
)
DEFAULT_OUTPUT = Path(
    "outputs/composable_paired_task_preparation_v0_3_1/browser_validation"
)
EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real Edge visual and interaction QA for the composable review app."
    )
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--edge", type=Path, default=None)
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def find_edge(explicit: Path | None) -> Path:
    if explicit:
        require(explicit.exists(), f"Edge executable not found: {explicit}")
        return explicit
    for candidate in EDGE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Microsoft Edge executable was not found")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def poll_json(url: str, timeout: float = 30.0) -> Any:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.load(response)
        except Exception as exc:  # pragma: no cover - diagnostic path
            last_error = exc
            time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {url}: {last_error}")


class Cdp:
    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self.next_id = 0

    async def command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        await self.websocket.send(
            json.dumps({"id": request_id, "method": method, "params": params or {}})
        )
        while True:
            message = json.loads(await self.websocket.recv())
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"CDP {method} failed: {message['error']}")
            return message.get("result", {})

    async def evaluate(self, expression: str) -> Any:
        result = await self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        remote = result.get("result", {})
        if remote.get("subtype") == "error":
            raise RuntimeError(f"Browser evaluation failed: {remote}")
        return remote.get("value")


async def wait_expression(
    cdp: Cdp, expression: str, timeout: float = 45.0, interval: float = 0.15
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if await cdp.evaluate(expression):
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"Timed out waiting for browser expression: {expression}")


async def screenshot(cdp: Cdp, output: Path) -> None:
    result = await cdp.command(
        "Page.captureScreenshot", {"format": "png", "fromSurface": True}
    )
    output.write_bytes(base64.b64decode(result["data"]))


async def validate(
    websocket_url: str, html_path: Path, output_dir: Path, download_dir: Path
) -> dict[str, Any]:
    async with websockets.connect(
        websocket_url, origin="http://localhost", max_size=32 * 1024 * 1024
    ) as websocket:
        cdp = Cdp(websocket)
        await cdp.command("Runtime.enable")
        await cdp.command("Page.enable")
        await cdp.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await cdp.command(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(download_dir), "eventsEnabled": True},
        )
        await cdp.command("Page.navigate", {"url": html_path.resolve().as_uri()})
        await wait_expression(
            cdp,
            "Boolean(window.__reviewAppTest && window.__reviewAppTest.rows.length === 200)",
            timeout=90,
        )

        initial = await cdp.evaluate(
            r'''(() => {
              const rect = selector => {
                const box = document.querySelector(selector).getBoundingClientRect();
                return {x:box.x,y:box.y,width:box.width,height:box.height,right:box.right,bottom:box.bottom};
              };
              return {
                rows: window.__reviewAppTest.rows.length,
                progress: document.querySelector('#progressText').textContent,
                queryZh: document.querySelector('.language-pane.zh .query-text').textContent,
                hierarchyText: document.querySelector('.gold-service')?.textContent || '',
                presetCount: document.querySelectorAll('.preset').length,
                selectCount: document.querySelectorAll('select').length,
                sidebar: rect('.sidebar'), main: rect('.main'), review: rect('.review'),
                viewport:{width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth},
                bodyText: document.body.innerText.slice(0, 2000)
              };
            })()'''
        )
        require(initial["rows"] == 200, "Browser did not load all 200 rows")
        require(initial["progress"] == "0 / 200", f"Unexpected progress: {initial['progress']}")
        require(any("\u4e00" <= char <= "\u9fff" for char in initial["queryZh"]), "Chinese query is not visible")
        require("GOLD_SERVICE" in initial["hierarchyText"], "Gold service marker is not visible")
        require(initial["presetCount"] == 8, "Expected eight quick presets")
        require(initial["selectCount"] == 0, "Dropdown controls were found")
        require(initial["sidebar"]["right"] <= initial["main"]["x"] + 1, "Sidebar overlaps main panel")
        require(initial["main"]["right"] <= initial["review"]["x"] + 1, "Main panel overlaps review panel")
        require(initial["viewport"]["scrollWidth"] <= initial["viewport"]["width"] + 1, "Desktop layout overflows horizontally")

        desktop_screenshot = output_dir / "composable_review_app_desktop_1600x1000.png"
        await screenshot(cdp, desktop_screenshot)

        first_id = "COMPOSABLE-PAIRED-REVIEW-V0.3.1-0001"
        clicked = await cdp.evaluate(
            r'''(() => {
              const button = [...document.querySelectorAll('.preset')].find(x => x.textContent.includes('全部符合：两个层级保留'));
              if (!button) return false; button.click(); return true;
            })()'''
        )
        require(clicked, "All-pass quick preset was not found")
        await wait_expression(
            cdp,
            "window.__reviewAppTest.currentRow().review_item_id.endsWith('0002')",
        )
        preset_state = await cdp.evaluate(
            f'''(() => ({{
              currentId: window.__reviewAppTest.currentRow().review_item_id,
              progress: document.querySelector('#progressText').textContent,
              decision: window.__reviewAppTest.decisions()[{json.dumps(first_id)}]
            }}))()'''
        )
        require(preset_state["progress"] == "1 / 200", "Quick preset did not update progress")
        require(preset_state["decision"]["composition_final_label"] == "true_composable", "Preset composition is wrong")
        require(preset_state["decision"]["service_level_eligible"] == "true", "Preset service eligibility is wrong")
        require(preset_state["decision"]["api_level_eligible"] == "true", "Preset API eligibility is wrong")
        require(preset_state["decision"]["composable_release_action"] == "keep_both_levels", "Preset release action is wrong")

        await cdp.command("Page.reload", {"ignoreCache": True})
        await wait_expression(
            cdp,
            "Boolean(window.__reviewAppTest && document.querySelector('#progressText').textContent === '1 / 200')",
            timeout=90,
        )
        restored = await cdp.evaluate(
            f"window.__reviewAppTest.decisions()[{json.dumps(first_id)}].composable_release_action"
        )
        require(restored == "keep_both_levels", "localStorage did not restore the decision")

        import_csv = (
            "review_item_id,composition_final_label,composable_release_action,adjudication_notes\r\n"
            "COMPOSABLE-PAIRED-REVIEW-V0.3.1-0003,parallel_multi,reclassify_as_multi,Edge import check"
        )
        import_ok = await cdp.evaluate(
            f'''(() => {{
              const input=document.querySelector('#importer');
              const file=new File([{json.dumps(import_csv)}],'composable_import_check.csv',{{type:'text/csv'}});
              const transfer=new DataTransfer(); transfer.items.add(file); input.files=transfer.files;
              input.dispatchEvent(new Event('change',{{bubbles:true}})); return true;
            }})()'''
        )
        require(import_ok, "Could not dispatch CSV import")
        await wait_expression(
            cdp,
            "window.__reviewAppTest.decisions()['COMPOSABLE-PAIRED-REVIEW-V0.3.1-0003']?.composable_release_action === 'reclassify_as_multi'",
        )

        csv_result = await cdp.evaluate(
            r'''(() => {
              const parsed=parseCsv(window.__reviewAppTest.buildCsv(false));
              const header=parsed[0], first=parsed.find(row=>row[0]==='COMPOSABLE-PAIRED-REVIEW-V0.3.1-0001');
              return {
                rowsIncludingHeader:parsed.length,
                columnCount:header.length,
                everyRowColumnCount:parsed.every(row=>row.length===header.length),
                firstReleaseAction:first[header.indexOf('composable_release_action')],
                firstComposition:first[header.indexOf('composition_final_label')]
              };
            })()'''
        )
        require(csv_result["rowsIncludingHeader"] == 201, "Export does not contain 200 rows")
        require(csv_result["columnCount"] == 72, "Export does not contain 72 columns")
        require(csv_result["everyRowColumnCount"], "Export contains malformed CSV rows")
        require(csv_result["firstReleaseAction"] == "keep_both_levels", "Export did not merge release action")
        require(csv_result["firstComposition"] == "true_composable", "Export did not merge composition label")

        before_download = {path.name for path in download_dir.glob("*.csv")}
        await cdp.evaluate(
            "[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='导出完整 CSV').click()"
        )
        deadline = time.time() + 15
        downloaded: Path | None = None
        while time.time() < deadline:
            candidates = [
                path
                for path in download_dir.glob("*.csv")
                if path.name not in before_download and not path.name.endswith(".crdownload")
            ]
            if candidates:
                downloaded = candidates[0]
                break
            await asyncio.sleep(0.2)
        require(downloaded is not None, "Export button did not produce a CSV download")
        with downloaded.open("r", encoding="utf-8-sig", newline="") as handle:
            downloaded_rows = list(csv.reader(handle))
        require(len(downloaded_rows) == 201, "Downloaded CSV does not contain 200 rows")
        require(all(len(row) == 72 for row in downloaded_rows), "Downloaded CSV has malformed columns")

        await cdp.evaluate(
            r'''(() => {const input=document.querySelector('#search'); input.value='ToolBench_G2_74008'; input.dispatchEvent(new Event('input',{bubbles:true})); return true})()'''
        )
        await wait_expression(cdp, "document.querySelector('#listSummary').textContent.includes('显示 1 条')")
        search_summary = await cdp.evaluate("document.querySelector('#listSummary').textContent")

        await cdp.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 900, "height": 1200, "deviceScaleFactor": 1, "mobile": False},
        )
        await cdp.evaluate(
            r'''(() => {const input=document.querySelector('#search'); input.value=''; input.dispatchEvent(new Event('input',{bubbles:true})); return true})()'''
        )
        await asyncio.sleep(0.3)
        compact = await cdp.evaluate(
            r'''(() => ({
              width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
              queryVisible:!!document.querySelector('.language-pane.zh .query-text'),
              reviewVisible:!!document.querySelector('#review .preset-grid')
            }))()'''
        )
        require(compact["scrollWidth"] <= compact["width"] + 1, "Compact layout overflows horizontally")
        require(compact["queryVisible"] and compact["reviewVisible"], "Compact layout hides required content")
        compact_screenshot = output_dir / "composable_review_app_compact_900x1200.png"
        await screenshot(cdp, compact_screenshot)

        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "html_file": str(html_path.resolve()),
            "browser": "Microsoft Edge headless via local CDP",
            "rows": initial["rows"],
            "desktop_viewport": initial["viewport"],
            "desktop_panel_geometry": {
                "sidebar": initial["sidebar"],
                "main": initial["main"],
                "review": initial["review"],
            },
            "chinese_query_visible": True,
            "gold_service_marker_visible": True,
            "quick_preset_count": initial["presetCount"],
            "dropdown_count": initial["selectCount"],
            "quick_preset_auto_next": True,
            "local_storage_restore": True,
            "csv_import": True,
            "csv_export": csv_result,
            "downloaded_csv_rows": len(downloaded_rows) - 1,
            "downloaded_csv_columns": len(downloaded_rows[0]),
            "downloaded_filename": downloaded.name,
            "search_filter_summary": search_summary,
            "compact_layout": compact,
            "screenshots": [str(desktop_screenshot), str(compact_screenshot)],
            "passed": True,
        }


def main() -> int:
    args = parse_args()
    html_path = args.html.resolve()
    output_dir = args.output_dir.resolve()
    require(html_path.exists(), f"HTML not found: {html_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    edge = find_edge(args.edge)
    port = free_port()

    with tempfile.TemporaryDirectory(prefix="sdbench_edge_profile_") as profile, tempfile.TemporaryDirectory(
        prefix="sdbench_edge_download_"
    ) as downloads:
        command = [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            targets = poll_json(f"http://127.0.0.1:{port}/json/list", timeout=30)
            page_target = next(target for target in targets if target.get("type") == "page")
            result = asyncio.run(
                validate(
                    page_target["webSocketDebuggerUrl"],
                    html_path,
                    output_dir,
                    Path(downloads),
                )
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    report = output_dir / "composable_review_app_edge_validation_v0_3_1.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
