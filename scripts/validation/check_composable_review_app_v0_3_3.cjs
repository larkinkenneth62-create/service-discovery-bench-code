#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const target = path.resolve(
  process.argv[2] ||
    "outputs/composable_paired_task_preparation_v0_3_3/composable_paired_task_review_app_v0_3_3.html"
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

assert(rows.length === 97, `Expected 97 rows, got ${rows.length}`);
assert(Object.keys(ui.queries).length === 97, "Expected 97 query translations");
assert(Object.keys(rows[0]).length === 141, `Expected 141 columns, got ${Object.keys(rows[0]).length}`);
assert(humanFields.length === 23, `Expected 23 human fields, got ${humanFields.length}`);
assert(Object.keys(ui.services).length > 0, "Expected service translations");
assert(Object.keys(ui.apis).length > 0, "Expected API translations");
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
const strongSourceTypes = new Set([
  "upstream_output_to_downstream_input",
  "upstream_observation_to_downstream_input",
  "upstream_result_to_tool_selection",
  "upstream_result_to_branch_condition",
]);
for (const row of rows) {
  const edges = JSON.parse(row.dependency_edges_json || "[]");
  assert(edges.length > 0, `${row.review_item_id}: corrected strong edge is empty`);
  for (const edge of edges) {
    assert(edge.strong_edge_eligible === true, `${row.review_item_id}: noneligible edge in strong list`);
    assert(strongSourceTypes.has(edge.edge_source_type), `${row.review_item_id}: forbidden source type`);
    assert(!["argument", "input", "request"].includes(edge.upstream_field_role), `${row.review_item_id}: forbidden upstream role`);
    assert(edge.value_present_in_original_query !== true, `${row.review_item_id}: query-known strong edge`);
    assert(edge.upstream_output_is_echo !== true, `${row.review_item_id}: echoed strong edge`);
    assert(!["failed", "error_only"].includes(edge.upstream_call_execution_status), `${row.review_item_id}: failed upstream strong edge`);
  }
}
assert(!/<select[\s>]/i.test(html), "Review UI must not use select/dropdown controls");
[
  "快捷审核方案",
  "Service/API Hierarchy View",
  "Rule-based Hints",
  "Task-level Necessity Gate",
  "A valid trace edge is not sufficient. The dependency must be necessary to satisfy the user query.",
  "Shared input, retry, redundant recomputation, and parallel subgoals are not true composition.",
  "dependency_required_for_query",
  "upstream_already_satisfies_subgoal",
  "full_query_subgoals_covered_by_gold_chain",
  "disconnected_parallel_subgoals_present",
  "cross_service_dependency_valid",
  "Machine-proposed dependency edge",
  "Shared inputs across calls",
  "Shared input is not dependency evidence.",
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
