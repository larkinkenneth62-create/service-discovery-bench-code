import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from servicediscoverybench.splits import (
    assign_components,
    audit_passed,
    build_split_components,
    build_split_components_v2,
    reverse_leakage_audit,
)


def row(identifier, *, query="", task="single_service_discovery", source="MetaTool", pair="", underlying="", parent=""):
    return {
        "benchmark_task_id": identifier,
        "split_group_id": "",
        "source_query_id": "",
        "query_signature": query,
        "task_signature": f"task::{identifier}",
        "paired_task_group_id": pair,
        "underlying_task_id": underlying or f"u::{identifier}",
        "parent_row_id": parent,
        "task_type": task,
        "source_dataset": source,
        "candidate_count": "10",
    }


class SplitTest(unittest.TestCase):
    def test_components_are_transitive_across_relationship_types(self):
        rows = [
            row("a", query="q1", underlying="u1"),
            row("b", query="q1", underlying="u2", pair="p1"),
            row("c", query="q2", underlying="u3", pair="p1"),
            row("d", query="q3"),
        ]
        groups = build_split_components(rows)
        self.assertEqual(groups["a"], groups["b"])
        self.assertEqual(groups["b"], groups["c"])
        self.assertNotEqual(groups["d"], groups["a"])

    def test_parent_row_reference_links_family(self):
        rows = [row("original"), row("repair", parent="original")]
        groups = build_split_components(rows)
        self.assertEqual(groups["original"], groups["repair"])

    def test_assignment_is_deterministic_and_audit_clean(self):
        rows = [row(f"r{i}", query=f"q{i // 2}", task="multi_api_recommendation" if i % 2 else "multi_service_discovery") for i in range(40)]
        groups = build_split_components(rows)
        first = assign_components(rows, groups, seed=17)
        second = assign_components(list(reversed(rows)), groups, seed=17)
        self.assertEqual(first.group_to_split, second.group_to_split)
        self.assertTrue(audit_passed(reverse_leakage_audit(rows, first.row_to_split)))
        self.assertEqual(set(first.row_to_split.values()), {"train", "dev", "test"})

    def test_assignment_does_not_starve_dev_or_test(self):
        rows = [row(f"r{i}", query=f"q{i}") for i in range(100)]
        groups = build_split_components(rows)
        assignment = assign_components(rows, groups, seed=23)
        counts = Counter(assignment.row_to_split.values())
        self.assertGreater(counts["train"], 0)
        self.assertGreater(counts["dev"], 0)
        self.assertGreater(counts["test"], 0)

    def test_reverse_audit_detects_cross_split_query(self):
        rows = [row("a", query="shared"), row("b", query="shared")]
        collisions = reverse_leakage_audit(rows, {"a": "train", "b": "test"})
        self.assertEqual(collisions["query_signature"][0]["value"], "shared")
        self.assertFalse(audit_passed(collisions))

    def test_v2_components_do_not_link_query_free_legacy_template_signature(self):
        rows = [
            {**row("a", query="q1"), "task_signature": "same-template", "review_content_fingerprint": "full-a", "source_task_id": "s1"},
            {**row("b", query="q2"), "task_signature": "same-template", "review_content_fingerprint": "full-b", "source_task_id": "s2"},
        ]
        legacy = build_split_components(rows)
        repaired = build_split_components_v2(rows)
        self.assertEqual(legacy["a"], legacy["b"])
        self.assertNotEqual(repaired["a"], repaired["b"])


if __name__ == "__main__":
    unittest.main()
