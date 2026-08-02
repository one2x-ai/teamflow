"""Contract test for the repository-level AGENTS.md self-consistency rule.

This module asserts the presence and specificity of the architectural
self-consistency rule: every change to this repository must go through
teamflow's own agents.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class AgentsMdSelfConsistencyRuleTests(unittest.TestCase):
    """The repository AGENTS.md must carry the self-consistency rule."""

    def setUp(self):
        self.text = read(ROOT / "AGENTS.md")

    def test_rule_section_present(self):
        """A heading must identify the self-consistency rule.

        If this section is removed, the test must fail.
        """
        self.assertRegex(
            self.text,
            r"(?im)^#+.*self-consistency",
            "AGENTS.md must have a heading identifying the self-consistency "
            "rule; if this section is removed, this test must fail",
        )

    def test_rule_states_agents_do_implementation(self):
        """The rule must say changes go through teamflow's agents.

        Must not pass on a vague sentence that merely mentions "agents"
        in isolation.
        """
        lower = self.text.lower()
        self.assertTrue(
            "through teamflow" in lower,
            "the self-consistency section must state that changes to this "
            "repository go through teamflow's agents, not merely mention "
            "'agents' in isolation",
        )

    def test_outer_loop_may_observe_not_implement(self):
        """The rule must separate observation from implementation."""
        lower = self.text.lower()
        has_prohibition = "may not" in lower or "must not" in lower
        self.assertTrue(
            has_prohibition,
            "the rule must state what an outer loop may not or must not do",
        )
        names_prohibited = any(
            w in lower for w in ("implement", "fix", "refactor")
        )
        self.assertTrue(
            names_prohibited,
            "the prohibition must name at least one of implement, fix, "
            "or refactor",
        )
        names_permitted = any(
            w in lower for w in ("handoff", "observe", "verify", "commit")
        )
        self.assertTrue(
            names_permitted,
            "the rule must name at least one thing an outer loop may do "
            "(handoff, observe, verify, or commit)",
        )

    def test_exceptions_documented(self):
        """The rule must document narrow exceptions.

        Checks for at least 3 of: handoff (writing/correcting the
        handoff), revert or git (reverting a bad change), test
        (temporarily breaking something to prove a test fails), and
        emergency (recovery when the agent path is broken).
        """
        lower = self.text.lower()
        found = 0
        if "handoff" in lower:
            found += 1
        if "revert" in lower or "git" in lower:
            found += 1
        if "test" in lower:
            found += 1
        if "emergency" in lower:
            found += 1
        self.assertGreaterEqual(
            found,
            3,
            "the self-consistency rule must document at least 3 of 4 "
            "narrow exceptions: handoff (writing/correcting the handoff), "
            "revert/git (reverting a bad change), test (temporarily "
            "breaking something to prove a test fails), emergency "
            "(recovery when the agent path is broken)",
        )

    def test_rule_not_in_shared_agents_md(self):
        """The rule is about *this* repository, not shipped projects.

        ``.teamflow/AGENTS.md`` ships to business projects where "changes
        to this repository" is meaningless, so the self-consistency rule
        must not appear there.
        """
        shared = read(ROOT / ".teamflow" / "AGENTS.md").lower()
        self.assertNotIn(
            "self-consistency",
            shared,
            "the self-consistency rule is about this repository only; it "
            "must not appear in .teamflow/AGENTS.md which ships to "
            "business projects",
        )


if __name__ == "__main__":
    unittest.main()
