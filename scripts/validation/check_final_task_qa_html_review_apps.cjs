#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const appDir = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(root, "ServiceDiscoveryBench-v0.1-candidate", "qa", "html_review_apps");
const manifestPath = path.join(appDir, "html_review_apps_manifest.json");
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const errors = [];

function fail(file, message) {
  errors.push(`${file}: ${message}`);
}

function walkKeys(value, visitor, trail = "DATA") {
  if (Array.isArray(value)) {
    value.forEach((item, index) => walkKeys(item, visitor, `${trail}[${index}]`));
  } else if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      visitor(key, `${trail}.${key}`);
      walkKeys(item, visitor, `${trail}.${key}`);
    }
  }
}

const requiredSnippets = [
  'class="workspace"',
  'class="sidebar"',
  'id="main" class="main"',
  'id="review" class="review"',
  'id="search"',
  'id="reviewFilters"',
  'id="groupFilters"',
  'id="quickFilters"',
  'id="sampleList"',
  'id="progressText"',
  'id="importer"',
  'function renderHierarchy',
  'function renderDependency',
  'function makeField',
  'function presetPanel',
  'function exportAttestation',
  'window.__reviewAppTest',
  '导出完整 CSV',
  '导出当前筛选',
  '清空当前',
  '清空全部',
  'ArrowLeft',
  'toLowerCase()==="j"',
  "localStorage",
  "semantic_alignment_check",
  "gold_validity_check",
  "candidate_validity_check",
  "service_catalog_check",
  "task_type_check",
  "leakage_check",
  "dependency_check",
  "final_decision",
  "severity",
  "notes",
];

const forbiddenKeyFragments = ["machine", "policy", "model", "assessment", "risk"];
const forbiddenExactKeys = new Set(["source_dataset", "source_file", "source_path", "expected_outcome"]);
let checkedApps = 0;
let checkedRows = 0;

for (const entry of manifest.entries) {
  if (!entry.count) continue;
  const filePath = path.join(appDir, entry.file);
  const html = fs.readFileSync(filePath, "utf8");
  checkedApps += 1;

  for (const snippet of requiredSnippets) {
    if (!html.includes(snippet)) fail(entry.file, `missing common element ${snippet}`);
  }
  if (/<script\b[^>]*\bsrc\s*=|<link\b[^>]*\bhref\s*=/i.test(html)) {
    fail(entry.file, "contains an external script/link resource");
  }
  const dataStart = html.indexOf("const DATA=");
  const configMarker = html.slice(dataStart).match(/;\r?\nconst CFG=/);
  const configStart = configMarker ? dataStart + configMarker.index : -1;
  const checksMarker = configStart >= 0 ? html.slice(configStart + 1).match(/;\r?\nconst CHECKS=/) : null;
  const checksStart = checksMarker ? configStart + 1 + checksMarker.index : -1;
  if (dataStart < 0 || configStart < 0 || checksStart < 0) {
    fail(entry.file, "cannot locate embedded DATA/CFG");
    continue;
  }
  const runtimeScript = html.slice(checksStart);
  if (/\b(fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(/.test(runtimeScript)) {
    fail(entry.file, "contains a network-capable runtime call");
  }
  let data;
  let config;
  try {
    data = JSON.parse(html.slice(dataStart + "const DATA=".length, configStart));
    const configPrefix = html.slice(configStart).match(/^;\r?\nconst CFG=/)[0];
    config = JSON.parse(html.slice(configStart + configPrefix.length, checksStart));
  } catch (error) {
    fail(entry.file, `invalid embedded JSON: ${error.message}`);
    continue;
  }
  if (data.length !== entry.count) fail(entry.file, `row count ${data.length} != ${entry.count}`);
  if (config.task !== entry.task || config.round !== entry.round) fail(entry.file, "task/round config mismatch");
  if (new Set(data.map((row) => row.benchmark_task_id)).size !== data.length) fail(entry.file, "duplicate task IDs");
  if (data.some((row) => row.review_round !== entry.round || row.task_type !== entry.task)) {
    fail(entry.file, "embedded row task/round mismatch");
  }
  if (data.some((row) => !String(row.query_translation_zh || "").trim())) {
    fail(entry.file, "missing Chinese query translation");
  }
  if (data.some((row) => String(row.query_translation_zh || "").trim() === String(row.query_text || "").trim())) {
    fail(entry.file, "Chinese query translation repeats the source query");
  }
  checkedRows += data.length;

  walkKeys(data, (key, trail) => {
    const lower = key.toLowerCase();
    if (forbiddenExactKeys.has(lower) || forbiddenKeyFragments.some((fragment) => lower.includes(fragment))) {
      fail(entry.file, `blind-pack leak key ${trail}`);
    }
  });

  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/gi)];
  if (scripts.length !== 1) {
    fail(entry.file, `expected one inline script, found ${scripts.length}`);
  } else {
    try {
      new Function(scripts[0][1]);
    } catch (error) {
      fail(entry.file, `JavaScript syntax error: ${error.message}`);
    }
  }
}

const indexHtml = fs.readFileSync(path.join(appDir, "index.html"), "utf8");
const openLinks = (indexHtml.match(/打开审核页 \/ Open/g) || []).length;
if (openLinks !== checkedApps) fail("index.html", `open-link count ${openLinks} != ${checkedApps}`);
if (manifest.entries.filter((entry) => entry.count === 0).length !== 2) {
  fail("manifest", "expected exactly two zero-row inherited composable primary entries");
}

const result = {
  status: errors.length ? "FAIL" : "PASS",
  checked_apps: checkedApps,
  checked_rows: checkedRows,
  manifest_entries: manifest.entries.length,
  errors,
};
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
process.exitCode = errors.length ? 1 : 0;
