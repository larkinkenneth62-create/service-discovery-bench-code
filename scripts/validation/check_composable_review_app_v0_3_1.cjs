#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const target = path.resolve(
  process.argv[2] ||
    "outputs/composable_paired_task_preparation_v0_3_1/composable_paired_task_review_app_v0_3_1.html"
);

if (!fs.existsSync(target)) {
  throw new Error(`HTML not found: ${target}`);
}

const html = fs.readFileSync(target, "utf8");
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  throw new Error("Inline application script not found");
}
new Function(scriptMatch[1]);

function embeddedJson(variable) {
  const match = html.match(new RegExp(`const ${variable}="([A-Za-z0-9+/=]+)";`));
  if (!match) throw new Error(`${variable} not found`);
  return JSON.parse(Buffer.from(match[1], "base64").toString("utf8"));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const rows = embeddedJson("ROWS_B64");
const ui = embeddedJson("UI_B64");
const humanFieldsMatch = html.match(/const HUMAN_FIELDS=(\[[\s\S]*?\]);/);
assert(humanFieldsMatch, "HUMAN_FIELDS not found");
const humanFields = JSON.parse(humanFieldsMatch[1]);

assert(rows.length === 200, `Expected 200 rows, got ${rows.length}`);
assert(Object.keys(ui.queries).length === 200, "Expected 200 query translations");
assert(Object.keys(ui.services).length === 944, "Expected 944 service translations");
assert(Object.keys(ui.apis).length === 1738, "Expected 1738 API translations");
assert(
  Object.values(ui.queries).every((text) => /[\u3400-\u9fff]/u.test(text) && !text.includes("�")),
  "Every query translation must contain valid Chinese text"
);
assert(
  Object.values(ui.services).every((item) => item.name_zh && item.description_zh),
  "Every service requires a Chinese name and capability summary"
);
assert(
  Object.values(ui.apis).every((item) => item.name_zh && item.description_zh),
  "Every API requires a Chinese name and capability summary"
);
assert(
  rows.every((row) => humanFields.every((field) => String(row[field] || "").trim() === "")),
  "Embedded source rows must keep all human fields blank"
);
assert(!/<select[\s>]/i.test(html), "Review UI must not use select/dropdown controls");
[
  "快捷审核方案",
  "Service/API Hierarchy View",
  "Rule-based Hints",
  "导出完整 CSV",
  "导入 CSV",
  "清空当前",
  "清空全部",
  "localStorage",
].forEach((needle) => assert(html.includes(needle), `Missing required feature: ${needle}`));

const summary = {
  html_file: target,
  html_bytes: Buffer.byteLength(html),
  javascript_syntax: "ok",
  embedded_rows: rows.length,
  columns: Object.keys(rows[0]).length,
  query_translations: Object.keys(ui.queries).length,
  service_translations: Object.keys(ui.services).length,
  api_translations: Object.keys(ui.apis).length,
  human_fields: humanFields.length,
  source_human_fields_blank: true,
  dropdown_controls: 0,
  required_static_features: "ok",
};
console.log(JSON.stringify(summary, null, 2));
