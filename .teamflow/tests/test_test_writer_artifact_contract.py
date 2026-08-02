import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class TestWriterArtifactContractTests(unittest.TestCase):
    def test_test_writer_uses_file_granular_artifact_checkpoints(self) -> None:
        agent = (ROOT / ".teamflow/agents/test-writer.md").read_text(encoding="utf-8")
        skill = (ROOT / ".teamflow/skills/write-tests/SKILL.md").read_text(
            encoding="utf-8"
        )

        required = (
            "short ordered target list",
            "exactly one module/file pair at a time",
            "immediately use a tool",
            "checkpoint that pair",
            "Never inspect all target modules first",
            "Do not interleave long explanatory analysis between tool actions",
            "Keep the final handoff compact",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, agent)
                self.assertIn(phrase, skill)

        ordered_skill_steps = tuple(skill.index(phrase) for phrase in required[:4])
        self.assertEqual(ordered_skill_steps, tuple(sorted(ordered_skill_steps)))

    def test_test_writer_does_not_analyze_everything_before_writing(self) -> None:
        for relative in (
            ".teamflow/agents/test-writer.md",
            ".teamflow/skills/write-tests/SKILL.md",
        ):
            with self.subTest(relative=relative):
                contract = (ROOT / relative).read_text(encoding="utf-8")
                prohibition = contract.index("Never inspect all target modules first")
                write_step = contract.index("immediately use a tool")
                self.assertGreater(prohibition, write_step)
                self.assertIn("postpone the patch until the end", contract)
                self.assertIn("batch unrelated files", contract)

    def test_planner_blocks_a_missing_test_patch_instead_of_retrying(self) -> None:
        planner = (ROOT / ".teamflow/agents/planner.md").read_text(encoding="utf-8")

        self.assertIn("DELEGATION_ARTIFACT_MISSING", planner)
        self.assertIn("OUTPUT_TRUNCATED", planner)
        self.assertIn("Do not retry that delegation", planner)
        self.assertIn("teamflow test-patch check", planner)

    def test_shared_policy_treats_output_truncation_as_blocked(self) -> None:
        for relative in ("AGENTS.md", ".teamflow/AGENTS.md"):
            with self.subTest(relative=relative):
                policy = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("finish=length", policy)
                self.assertIn("BLOCKED", policy)


if __name__ == "__main__":
    unittest.main()
