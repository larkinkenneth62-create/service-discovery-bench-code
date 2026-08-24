import unittest

from servicediscoverybench.joint_split_optimizer_v3 import (
    OptimizerConfig,
    TASKS,
    choose_recommended_candidate,
    solve_split_candidate,
)


class JointSplitOptimizerV3Test(unittest.TestCase):
    def make_rows(self):
        rows = []
        row_to_group = {}
        sources = ["MetaTool", "ToolBench", "StableToolBench"]
        for index in range(600):
            task = TASKS[index % len(TASKS)]
            source = sources[index % len(sources)]
            row_id = f"r{index}"
            rows.append(
                {
                    "benchmark_task_id": row_id,
                    "task_type": task,
                    "source_dataset": source,
                    "candidate_count": "10",
                    "legacy_split": "train" if index < 480 else "dev" if index < 540 else "test",
                }
            )
            row_to_group[row_id] = f"g{index}"
        return rows, row_to_group

    def test_joint_optimizer_keeps_all_three_splits_nonempty_and_exact(self):
        rows, groups = self.make_rows()
        config = OptimizerConfig(
            exact_targets={"train": 480, "dev": 60, "test": 60},
            task_min_test=5,
            task_min_group_threshold=10,
            cell_min_test=2,
            cell_min_group_threshold=10,
            large_cell_row_threshold=10_000,
            time_limit_seconds=60,
        )
        candidates = [
            solve_split_candidate(rows, groups, name, config=config)
            for name in ("A_PROPORTIONAL", "B_REPRESENTATIVE", "C_MINIMAL_CHANGE")
        ]
        for candidate in candidates:
            self.assertTrue(candidate.valid, candidate.solver_message)
            self.assertEqual(candidate.counts, {"train": 480, "dev": 60, "test": 60})
            self.assertTrue(all(candidate.task_test_counts[task] >= 5 for task in TASKS))
        self.assertIsNotNone(choose_recommended_candidate(candidates))

    def test_group_is_never_split(self):
        rows, groups = self.make_rows()
        # Link two rows into one group.
        groups["r0"] = "paired"
        groups["r1"] = "paired"
        config = OptimizerConfig(
            exact_targets={"train": 480, "dev": 60, "test": 60},
            task_min_test=5,
            task_min_group_threshold=10,
            cell_min_test=2,
            cell_min_group_threshold=10,
            large_cell_row_threshold=10_000,
            time_limit_seconds=60,
        )
        candidate = solve_split_candidate(rows, groups, "B_REPRESENTATIVE", config=config)
        self.assertTrue(candidate.valid)
        self.assertEqual(candidate.row_to_split["r0"], candidate.row_to_split["r1"])


if __name__ == "__main__":
    unittest.main()
