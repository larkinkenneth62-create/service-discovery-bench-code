#!/usr/bin/env python
"""Unit tests for the read-only ToolBench composable trace audit."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load_module(
    "toolbench_audit_v0_1",
    SCRIPT_DIR / "audit_toolbench_composable_trace_availability_v0_1.py",
)
extract = load_module(
    "toolbench_extract_v0_1",
    SCRIPT_DIR / "extract_toolbench_composable_objective_evidence_v0_1.py",
)


def sample_step(index: int, service: str, api: str, arguments: Any, outputs: Any) -> dict[str, Any]:
    return {
        "step_index": index,
        "service_name": service,
        "api_name": api,
        "function_name": f"{api}_for_{service}",
        "arguments": arguments,
        "outputs": outputs,
        "observation": outputs,
        "source_file": "C:/readonly/source.json",
        "source_json_path": f"$.steps[{index}]",
        "argument_source_path": f"$.steps[{index}].arguments",
        "output_source_path": f"$.steps[{index}].outputs",
    }


def parse_summary(**overrides: Any) -> dict[str, Any]:
    base = {
        "parse_status": "ok",
        "ordered_steps_found": "true",
        "step_count": 2,
        "distinct_service_count": 2,
        "distinct_api_count": 2,
        "arguments_found": "true",
        "outputs_found": "true",
        "observations_found": "true",
    }
    base.update(overrides)
    return base


class ToolBenchTraceAuditTests(unittest.TestCase):
    def test_exact_output_to_later_input_match(self) -> None:
        record = {
            "source_task_id": "task-1",
            "query_text": "Find a venue and then fetch its weather.",
            "steps": [
                sample_step(1, "venue", "find", {}, {"venue_id": "VENUE-9284"}),
                sample_step(2, "weather", "forecast", {"venue_id": "VENUE-9284"}, {}),
            ],
        }
        edges = extract.extract_dependency_edges(record)
        objective = [edge for edge in edges if edge["dependency_type"] != "sequence_only"]
        self.assertEqual(len(objective), 1)
        self.assertTrue(all(not edge["query_known_value_filtered"] for edge in objective))
        self.assertEqual(extract.classify_evidence(parse_summary(), edges), "strong_objective_evidence_available")

    def test_query_known_value_is_filtered(self) -> None:
        record = {
            "source_task_id": "task-2",
            "query_text": "Use venue ID VENUE-9284 to get weather.",
            "steps": [
                sample_step(1, "venue", "find", {}, {"venue_id": "VENUE-9284"}),
                sample_step(2, "weather", "forecast", {"venue_id": "VENUE-9284"}, {}),
            ],
        }
        edges = extract.extract_dependency_edges(record)
        filtered = [edge for edge in edges if edge.get("query_known_value_filtered")]
        self.assertGreaterEqual(len(filtered), 1)
        self.assertEqual(extract.classify_evidence(parse_summary(), edges), "no_dependency_evidence")

    def test_only_explicit_order_is_sequence_only(self) -> None:
        summary = parse_summary(arguments_found="false", outputs_found="false", observations_found="false")
        edges = [{"dependency_type": "sequence_only", "query_known_value_filtered": False}]
        self.assertEqual(extract.classify_evidence(summary, edges), "sequence_only")

    def test_arguments_without_outputs_is_not_strong(self) -> None:
        summary = parse_summary(outputs_found="false", observations_found="false")
        self.assertEqual(extract.classify_evidence(summary, []), "no_dependency_evidence")

    def test_multiple_tools_without_dependency_is_not_composable_evidence(self) -> None:
        record = {
            "source_task_id": "task-5",
            "query_text": "Get weather and news.",
            "steps": [
                sample_step(1, "weather", "current", {"city": "Paris"}, {"temperature": 20}),
                sample_step(2, "news", "latest", {"topic": "technology"}, {"count": 10}),
            ],
        }
        edges = extract.extract_dependency_edges(record)
        self.assertFalse(any(edge["dependency_type"] not in {"sequence_only", "none"} for edge in edges))
        self.assertEqual(extract.classify_evidence(parse_summary(), edges), "no_dependency_evidence")

    def test_exact_id_join(self) -> None:
        inventory = {
            "source_dataset": "ToolBench",
            "source_group": "G3",
            "source_query_id": "545",
            "task_id": "ToolBench_G3_545",
        }
        index = {
            ("G3", "545"): [
                {
                    "source_file": "C:/repo/data/answer/G3_answer/545_model.json",
                    "record_offset_or_json_path": "$",
                }
            ]
        }
        status, join_type, matches, reason = audit.resolve_join(inventory, index)
        self.assertEqual(status, "joined")
        self.assertEqual(join_type, "exact_source_query_id")
        self.assertEqual(len(matches), 1)
        self.assertEqual(reason, "")

    def test_ambiguous_exact_id_join_does_not_choose(self) -> None:
        inventory = {
            "source_dataset": "ToolBench",
            "source_group": "G3",
            "source_query_id": "545",
            "task_id": "ToolBench_G3_545",
        }
        index = {
            ("G3", "545"): [
                {"source_file": "C:/repo/data/answer/G3_answer/a/545_a.json", "record_offset_or_json_path": "$"},
                {"source_file": "C:/repo/data/answer/G3_answer/b/545_b.json", "record_offset_or_json_path": "$"},
            ]
        }
        status, join_type, matches, reason = audit.resolve_join(inventory, index)
        self.assertEqual(status, "ambiguous")
        self.assertEqual(join_type, "ambiguous")
        self.assertEqual(len(matches), 2)
        self.assertIn("none was selected", reason)

    def test_jsonl_is_read_streamingly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_text('{"id": 1}\n{"id": 2}\n{"id": 3}\n', encoding="utf-8")
            records = list(audit.iter_jsonl(path, limit=2))
            self.assertEqual([record[1]["id"] for record in records], [1, 2])

    def test_untrusted_binary_is_recorded_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.pkl"
            marker = Path(directory) / "executed.txt"
            path.write_bytes(b"cos\nsystem\n(S'echo unsafe'\ntR.")
            self.assertFalse(audit.is_safe_parse_supported(path))
            fingerprint = audit.schema_fingerprint(path)
            self.assertEqual(fingerprint["parse_status"], "recorded_not_executed")
            self.assertFalse(marker.exists())

    def test_source_path_remains_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(json.dumps({"answer_generation": {"train_messages": [], "query": "q"}}), encoding="utf-8")
            before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            before_stat = path.stat()
            record = audit.load_json_file(path)
            audit.parse_trace_record(record, {"inventory_id": "x", "task_id": "t", "query_text": "q"}, str(path))
            after_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            after_stat = path.stat()
            self.assertEqual(before_hash, after_hash)
            self.assertEqual(before_stat.st_size, after_stat.st_size)
            self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_final_answer_exactly_selects_one_prefix_chain(self) -> None:
        final_text = "completed result"
        record = {
            "answer_generation": {
                "query": "q",
                "final_answer": json.dumps({"return_type": "give_answer", "final_answer": final_text}),
                "train_messages": [
                    [
                        {"role": "assistant", "function_call": {"name": "first_for_alpha", "arguments": "{}"}},
                        {"role": "function", "name": "first_for_alpha", "content": "{\"id\": \"X-123\"}"},
                    ],
                    [
                        {"role": "assistant", "function_call": {"name": "first_for_alpha", "arguments": "{}"}},
                        {"role": "function", "name": "first_for_alpha", "content": "{\"id\": \"X-123\"}"},
                        {"role": "assistant", "function_call": {"name": "second_for_beta", "arguments": "{\"id\": \"X-123\"}"}},
                        {"role": "function", "name": "second_for_beta", "content": "{}"},
                        {"role": "assistant", "function_call": {"name": "Finish", "arguments": json.dumps({"return_type": "give_answer", "final_answer": final_text})}},
                    ],
                ],
            }
        }
        normalized, summary = audit.parse_trace_record(record, {"inventory_id": "x", "task_id": "t", "query_text": "q"}, "source.json")
        self.assertEqual(summary["parse_status"], "ok")
        self.assertEqual(summary["step_count"], 2)
        self.assertEqual([step["function_name"] for step in normalized["steps"]], ["first_for_alpha", "second_for_beta"])

    def test_give_up_finish_type_selects_unique_terminal_chain(self) -> None:
        record = {
            "answer_generation": {
                "query": "q",
                "finish_type": "give_up",
                "final_answer": json.dumps({"return_type": "give_up_and_restart"}),
                "train_messages": [
                    [
                        {"role": "assistant", "function_call": {"name": "first_for_alpha", "arguments": "{}"}},
                        {"role": "function", "name": "first_for_alpha", "content": "{}"},
                    ],
                    [
                        {"role": "assistant", "function_call": {"name": "first_for_alpha", "arguments": "{}"}},
                        {"role": "function", "name": "first_for_alpha", "content": "{}"},
                        {"role": "assistant", "function_call": {"name": "Finish", "arguments": json.dumps({"return_type": "give_up_and_restart"})}},
                    ],
                ],
            }
        }
        normalized, summary = audit.parse_trace_record(record, {"inventory_id": "x", "task_id": "t", "query_text": "q"}, "source.json")
        self.assertEqual(summary["parse_status"], "ok")
        self.assertEqual(summary["step_count"], 1)
        self.assertEqual(normalized["steps"][0]["function_name"], "first_for_alpha")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ToolBench composable trace audit unit tests.")
    parser.add_argument("--output-summary", default="outputs/toolbench_composable_trace_audit_v0_1/test_summary.json")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ToolBenchTraceAuditTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "passed": result.wasSuccessful(),
        "failure_details": [message for _, message in result.failures],
        "error_details": [message for _, message in result.errors],
    }
    output_path = Path(args.output_summary).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
