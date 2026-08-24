import unittest

from servicediscoverybench.split_identity_v3 import build_identity_v3


class SplitIdentityV3Test(unittest.TestCase):
    def test_source_local_ids_are_namespaced(self):
        rows = [
            {
                "benchmark_task_id": "meta-1",
                "source_dataset": "MetaTool",
                "source_query_id": "1",
                "source_task_id": "1",
                "query_signature": "q-meta",
                "review_content_fingerprint": "f-meta",
            },
            {
                "benchmark_task_id": "tool-1",
                "source_dataset": "ToolBench",
                "source_query_id": "1",
                "source_task_id": "1",
                "query_signature": "q-tool",
                "review_content_fingerprint": "f-tool",
            },
        ]
        result = build_identity_v3(rows)
        self.assertNotEqual(result.row_to_group["meta-1"], result.row_to_group["tool-1"])
        self.assertEqual(result.collision_summary["source_local_cross_source_edges_after_namespacing"], 0)

    def test_exact_query_signature_links_across_sources(self):
        rows = [
            {
                "benchmark_task_id": "a",
                "source_dataset": "A",
                "source_query_id": "1",
                "source_task_id": "1",
                "query_signature": "same-query",
                "review_content_fingerprint": "fa",
            },
            {
                "benchmark_task_id": "b",
                "source_dataset": "B",
                "source_query_id": "2",
                "source_task_id": "2",
                "query_signature": "same-query",
                "review_content_fingerprint": "fb",
            },
        ]
        result = build_identity_v3(rows)
        self.assertEqual(result.row_to_group["a"], result.row_to_group["b"])

    def test_legacy_task_signature_does_not_link(self):
        rows = [
            {
                "benchmark_task_id": "a",
                "source_dataset": "A",
                "task_signature": "legacy-template",
                "query_signature": "qa",
                "review_content_fingerprint": "fa",
            },
            {
                "benchmark_task_id": "b",
                "source_dataset": "A",
                "task_signature": "legacy-template",
                "query_signature": "qb",
                "review_content_fingerprint": "fb",
            },
        ]
        result = build_identity_v3(rows)
        self.assertNotEqual(result.row_to_group["a"], result.row_to_group["b"])

    def test_parent_exact_row_reference_links(self):
        rows = [
            {
                "benchmark_task_id": "original",
                "source_dataset": "A",
                "query_signature": "q1",
                "review_content_fingerprint": "f1",
            },
            {
                "benchmark_task_id": "repair",
                "source_dataset": "A",
                "query_signature": "q2",
                "review_content_fingerprint": "f2",
                "parent_row_id": "original",
            },
        ]
        result = build_identity_v3(rows)
        self.assertEqual(result.row_to_group["original"], result.row_to_group["repair"])


if __name__ == "__main__":
    unittest.main()
