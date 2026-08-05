"""Requirement tests for the outer-loop observation contract.

The outer loop (an external coordinator that is not doing the work) must be
able to detect the inner loop's execution accurately while spending as few
tokens as possible. The polling ladder it used to climb cost a tool call and
a result on every tick even when nothing had changed, and broke the outer
loop's KV cache each time; observation is now one blocking call.

Two artifacts encode the contract:

1. ``.teamflow/skills/observe-inner-loop/SKILL.md`` — an installable skill
   naming the exact metadata-only commands and the two stop signals.
2. ``.teamflow/AGENTS.md`` — a shared-constraints section telling the outer
   loop to use that skill and forbidding session/prompt/response reads.

The retired SQLite session monitor must stay retired: the skill may never
reach into session files, prompts, reasoning, responses, or credentials.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / ".teamflow" / "skills" / "observe-inner-loop" / "SKILL.md"
SHARED_RULES = ROOT / ".teamflow" / "AGENTS.md"
DOCTOR = ROOT / "scripts" / "doctor.sh"

EVENT_KINDS = (
    "run_started",
    "run_finished",
    "handoff_opened",
    "handoff_finished",
    "artifact_written",
    "runner_exited",
)


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

    def test_skill_leads_with_the_blocking_wait_command(self):
        text = read(SKILL)
        self.assertIn("teamflow wait", text)
        self.assertIn("--run-id", text)
        self.assertIn("--since", text)

    def test_skill_documents_the_handoff_status_escalation(self):
        text = read(SKILL)
        self.assertIn("teamflow handoff status", text)
        self.assertIn("teamflow agents list", text)

    def test_skill_names_every_event_kind(self):
        text = read(SKILL)
        for kind in EVENT_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(kind, text)

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

    def test_skill_names_both_stop_signals(self):
        text = read(SKILL)
        self.assertIn("runner_exited", text)
        self.assertRegex(
            text,
            r"(?i)exactly two",
            "the skill must state that there are exactly two stop signals, so "
            "nothing else terminates the inner loop",
        )


class OuterLoopTokenEfficiencyTests(unittest.TestCase):
    """The outer loop must observe cheaply: metadata only, no polling."""

    def test_skill_forbids_reading_full_artifact_bodies(self):
        text = read(SKILL)
        self.assertRegex(
            text,
            r"do not read|never read|Do not read",
            "skill must forbid reading artifact bodies",
        )

    def test_skill_states_that_an_unchanged_inner_loop_is_free(self):
        text = read(SKILL).lower()
        self.assertRegex(
            text,
            r"costs? (you )?nothing|zero",
            "the skill must state that no change costs no tokens",
        )

    def test_skill_retires_the_polling_ladder(self):
        """A polling interval is a contradiction once the call blocks."""
        text = read(SKILL).lower()
        self.assertRegex(
            text,
            r"never a poll|not a poll|instead of polling|blocks instead",
            "the skill must say observation blocks rather than polls",
        )
        self.assertNotIn(
            "every 30 seconds",
            text,
            "a fixed polling interval must not survive the blocking listener",
        )
        self.assertNotIn(
            "teamflow phase status",
            read(SKILL),
            "the retired phase surface must not be the escalation path",
        )

    def test_filename_carries_the_metadata(self):
        text = read(SKILL)
        self.assertRegex(
            text,
            r"<seq>--<subject>--<kind>--<status>",
            "the skill must show that the file name answers the question, so a "
            "body read is the exception",
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

    def test_shared_rules_name_the_metadata_commands(self):
        text = read(SHARED_RULES)
        self.assertIn("teamflow wait", text)
        self.assertIn("teamflow handoff status", text)

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

    def test_shared_rules_name_both_stop_signals(self):
        text = read(SHARED_RULES)
        self.assertIn("runner_exited", text)
        self.assertIn("BLOCKED", text)


class InstallAndDoctorWiringTests(unittest.TestCase):
    """The skill ships with the installer and doctor verifies it."""

    def test_doctor_checks_observe_inner_loop_skill(self):
        self.assertIn("observe-inner-loop", read(DOCTOR))

    def test_doctor_checks_the_wait_listener(self):
        self.assertIn("wait.py", read(DOCTOR))

    def test_retired_sqlite_monitor_stays_retired(self):
        """The old session-file monitor must not come back with this skill."""
        self.assertFalse(
            (ROOT / "skills" / "outer-loop-monitor").is_dir(),
            "the retired skills/outer-loop-monitor must stay removed",
        )
        self.assertNotIn("outer-loop-monitor", read(SKILL))


if __name__ == "__main__":
    unittest.main()
