"""Requirement tests for the outer-loop observation contract.

The outer loop (an external coordinator that is not doing the work) must be
able to detect the inner loop's execution path accurately while spending as
few tokens as possible. Two artifacts encode that contract:

1. ``.teamflow/skills/observe-inner-loop/SKILL.md`` — an installable skill
   that lists the exact metadata-only commands and the polling discipline.
2. ``.teamflow/AGENTS.md`` — a shared-constraints section telling the outer
   loop to use that skill and forbidding session/prompt/response reads.

The retired SQLite session monitor must stay retired: the skill may never
reach into session files, prompts, reasoning, responses, or credentials.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".teamflow" / "skills" / "observe-inner-loop" / "SKILL.md"
SHARED_RULES = ROOT / ".teamflow" / "AGENTS.md"
DOCTOR = ROOT / "scripts" / "doctor.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class ObserveInnerLoopSkillTests(unittest.TestCase):
    """The observation skill exists and is installable under .teamflow/."""

    def test_skill_file_exists(self):
        self.assertTrue(
            SKILL.is_file(),
            ".teamflow/skills/observe-inner-loop/SKILL.md must exist",
        )

    def test_skill_has_frontmatter_name_and_description(self):
        text = read(SKILL)
        self.assertTrue(text.startswith("---"), "SKILL.md must start with frontmatter")
        self.assertRegex(text, r"(?m)^name:\s*observe-inner-loop\s*$")
        self.assertRegex(text, r"(?m)^description:\s*\S")

    def test_skill_lists_phase_status_command(self):
        text = read(SKILL)
        self.assertIn("teamflow phase status", text)
        self.assertIn("--run-id", text)

    def test_skill_lists_session_list_json_command(self):
        text = read(SKILL)
        self.assertIn("teamflow session list", text)
        self.assertIn("--format json", text)

    def test_skill_documents_artifact_existence_checks(self):
        text = read(SKILL)
        self.assertIn(".teamflow/runs/", text)

    def test_skill_documents_terminal_silence_is_not_failure(self):
        text = read(SKILL)
        self.assertRegex(
            text,
            r"[Ss]ilence",
            "skill must state that terminal silence alone is not a failure",
        )

    def test_skill_documents_blocked_and_stale_distinction(self):
        text = read(SKILL)
        self.assertIn("BLOCKED", text)
        self.assertIn("stale", text)


class OuterLoopTokenEfficiencyTests(unittest.TestCase):
    """The outer loop must observe cheaply: metadata only, bounded polling."""

    def test_skill_forbids_reading_full_artifact_bodies(self):
        text = read(SKILL)
        self.assertRegex(
            text,
            r"do not read|never read",
            "skill must forbid reading artifact bodies",
        )

    def test_skill_prescribes_bounded_polling_interval(self):
        text = read(SKILL)
        self.assertRegex(
            text,
            r"\b\d+\s*(second|s\b|minute)",
            "skill must prescribe a concrete polling interval",
        )

    def test_skill_forbids_re_polling_unchanged_state(self):
        text = read(SKILL)
        self.assertRegex(
            text,
            r"unchanged|same status|has not changed",
            "skill must avoid re-reporting unchanged state",
        )

    def test_skill_is_compact(self):
        """The observation skill itself must stay cheap to inject."""
        size = len(read(SKILL).encode("utf-8"))
        self.assertLess(
            size, 3000,
            f"observe-inner-loop SKILL.md must stay under 3000 bytes, got {size}",
        )


class OuterLoopIsolationTests(unittest.TestCase):
    """The observation path must never reach into session or secret data."""

    # Concrete paths and config keys the skill must never point a reader at.
    # Data-class nouns like "reasoning" are excluded here on purpose: the
    # skill is required to name them in its forbidden list.
    FORBIDDEN_PATHS = (
        "opencode.db",
        "sessions/",
        "session.jsonl",
        "auth.json",
        "apiKey",
        "models.json",
    )

    def test_skill_does_not_reference_session_or_secret_paths(self):
        text = read(SKILL)
        for token in self.FORBIDDEN_PATHS:
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_skill_explicitly_forbids_session_and_credential_reads(self):
        text = read(SKILL)
        for noun in ("session files", "prompts", "reasoning", "credentials"):
            with self.subTest(noun=noun):
                self.assertIn(
                    noun, text,
                    f"skill must name {noun!r} as a forbidden data class",
                )

    def test_skill_declares_read_only_observation(self):
        text = read(SKILL)
        self.assertRegex(
            text,
            r"read-only|never write|do not write",
            "skill must declare observation is read-only",
        )


class SharedRulesOuterLoopSectionTests(unittest.TestCase):
    """.teamflow/AGENTS.md must tell the outer loop to use this skill."""

    def test_shared_rules_have_outer_loop_section(self):
        text = read(SHARED_RULES)
        self.assertRegex(
            text,
            r"(?m)^##\s+.*[Oo]uter loop",
            ".teamflow/AGENTS.md must have an outer-loop section",
        )

    def test_shared_rules_reference_the_skill_by_name(self):
        self.assertIn("observe-inner-loop", read(SHARED_RULES))

    def test_shared_rules_name_the_two_metadata_commands(self):
        text = read(SHARED_RULES)
        self.assertIn("teamflow phase status", text)
        self.assertIn("teamflow session list", text)

    def test_shared_rules_forbid_session_and_prompt_reads(self):
        text = read(SHARED_RULES)
        self.assertRegex(
            text,
            r"[Nn]ever read .*session",
            ".teamflow/AGENTS.md must forbid reading session files",
        )

    def test_shared_rules_state_silence_is_not_failure(self):
        text = read(SHARED_RULES)
        self.assertRegex(text, r"[Ss]ilence")


class InstallAndDoctorWiringTests(unittest.TestCase):
    """The skill ships with the installer and doctor verifies it."""

    def test_doctor_checks_observe_inner_loop_skill(self):
        self.assertIn("observe-inner-loop", read(DOCTOR))

    def test_retired_sqlite_monitor_stays_retired(self):
        """The old session-file monitor must not come back with this skill."""
        self.assertFalse(
            (ROOT / "skills" / "outer-loop-monitor").is_dir(),
            "the retired skills/outer-loop-monitor must stay removed",
        )
        self.assertNotIn("outer-loop-monitor", read(SKILL))


if __name__ == "__main__":
    unittest.main()
