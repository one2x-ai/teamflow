"""Requirement tests for the write-handoff skill, observer rewrite,
de-duplication, and discoverability (Parts B–E).

These tests assert the *desired end state* and must currently FAIL RED because
``write-handoff`` does not exist yet, the de-duplication has not happened, and
the observer skill has not been rewritten with a liveness-first probe.
"""

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

WRITE_HANDOFF = ROOT / ".teamflow" / "skills" / "write-handoff" / "SKILL.md"
OBSERVE_SKILL = ROOT / ".teamflow" / "skills" / "observer" / "SKILL.md"
REPO_AGENTS = ROOT / "AGENTS.md"
SHARED_AGENTS = ROOT / ".teamflow" / "AGENTS.md"
PLAN_CHANGE = ROOT / ".teamflow" / "skills" / "plan-change" / "SKILL.md"
DOCTOR = ROOT / "scripts" / "doctor.sh"
TEAMFLOW_BIN = ROOT / ".teamflow" / "bin" / "teamflow"

DEBUG_TIMEOUT = 60

# Distinctive colon/prefixed forms that only appear when a file restates the
# full handoff field list.  The single owner (write-handoff) should match
# several of these; de-duplicated files should match fewer than 3.
FIELD_TOKENS = [
    "Goal:",
    "Scope:",
    "Out of scope",
    "out-of-scope",
    "Initial test target",
    "Evidence collected",
    "Open questions:",
    "Acceptance:",
]

DEDUP_FILES = [REPO_AGENTS, SHARED_AGENTS, PLAN_CHANGE]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def clean_env(home: Path) -> dict:
    env = dict(os.environ)
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    return env


def count_field_tokens(text: str) -> int:
    return sum(1 for tok in FIELD_TOKENS if tok in text)


# ---------------------------------------------------------------------------
# Part C — write-handoff skill
# ---------------------------------------------------------------------------

class WriteHandoffSkillExistenceTests(unittest.TestCase):
    def test_skill_exists_with_frontmatter(self):
        self.assertTrue(WRITE_HANDOFF.is_file(), "write-handoff SKILL.md must exist")
        text = read(WRITE_HANDOFF)
        self.assertTrue(text.startswith("---"), "SKILL.md must start with frontmatter")
        self.assertRegex(text, r"(?m)^name:\s*write-handoff\s*$")

    def test_skill_is_under_4000_bytes(self):
        size = len(read(WRITE_HANDOFF).encode("utf-8"))
        self.assertLess(
            size, 4000,
            f"write-handoff SKILL.md must stay under 4000 bytes, got {size}",
        )


class WriteHandoffOutputSectionsTests(unittest.TestCase):
    """write-handoff must name every required output section."""

    def test_all_sections_named(self):
        text = read(WRITE_HANDOFF)
        for section in (
            "Goal",
            "Scope",
            "Out of scope",
            "Acceptance",
            "Constraints",
            "Initial test target",
            "Evidence",
            "Open questions",
        ):
            with self.subTest(section=section):
                self.assertIn(section, text, f"write-handoff must name {section!r}")

    def test_input_section_present(self):
        text = read(WRITE_HANDOFF)
        self.assertRegex(text, r"(?m)^##\s*Input",
                         "write-handoff must state its input section")


class WriteHandoffVagueRejectionTests(unittest.TestCase):
    """write-handoff must reject vague criteria by name."""

    def test_rejects_works_correctly_and_is_robust(self):
        text = read(WRITE_HANDOFF)
        self.assertIn("works correctly", text)
        self.assertIn("is robust", text)

    def test_requires_verifiable_by_command_or_artifact(self):
        text = read(WRITE_HANDOFF).lower()
        self.assertIn("command", text)
        self.assertIn("observable artifact", text,
                      "must require each criterion provable by command or observable artifact")


class WriteHandoffSelfCheckTests(unittest.TestCase):
    """write-handoff must include a self-check before sending."""

    def test_self_check_names_boundary_constraint_and_verified(self):
        text = read(WRITE_HANDOFF)
        # architectural boundary or contradict
        self.assertTrue(
            "boundary" in text.lower() or "contradict" in text.lower(),
            "self-check must mention architectural boundary or contradiction",
        )
        # constraint contradiction
        self.assertTrue(
            "contradict" in text.lower(),
            "self-check must mention constraint contradiction",
        )
        # verified-not-assumed
        self.assertTrue(
            "verif" in text.lower() and "assum" in text.lower(),
            "self-check must require verified-not-assumed evidence",
        )

    def test_self_check_mentions_build_mistake_example(self):
        text = read(WRITE_HANDOFF)
        self.assertIn("bootstrap", text.lower(), "must reference bootstrap.sh build mistake")
        self.assertIn("install", text.lower(), "must reference install.sh build mistake")


class WriteHandoffCliConfluenceTests(unittest.TestCase):
    """The skill owns the body; the CLI owns the state. Both must be said."""

    def setUp(self):
        self.text = read(WRITE_HANDOFF)

    def test_skill_names_the_registering_command(self):
        self.assertIn("teamflow handoff open", self.text)
        self.assertIn("teamflow handoff finish", self.text)

    def test_skill_forbids_hand_written_state(self):
        self.assertRegex(
            self.text,
            r"(?i)(never|do not) hand-?write",
            "an agent that can hand-write state can desynchronize it",
        )
        self.assertIn("state.json", self.text)

    def test_goal_is_documented_as_the_registry_title(self):
        self.assertRegex(
            self.text,
            r"(?i)registry title",
            "the Goal line doubles as the board title, which is why it is one line",
        )
        self.assertIn("80", self.text, "the title budget must be stated")

    def test_scope_is_documented_as_paths_for_conflict_detection(self):
        lowered = self.text.lower()
        self.assertIn("overlap", lowered)
        self.assertIn(
            "paths",
            lowered,
            "scope must be paths, or the CLI cannot intersect it across handoffs",
        )


class WriteHandoffEnvTrapsTests(unittest.TestCase):
    """write-handoff must carry environment traps into Constraints."""

    def test_proxy_trap(self):
        text = read(WRITE_HANDOFF).lower()
        self.assertTrue(
            "http_proxy" in text or "proxy" in text,
            "must mention HTTP_PROXY or proxy",
        )

    def test_timeout_above_120(self):
        text = read(WRITE_HANDOFF)
        self.assertIn("120", text, "must mention a timeout literal 120")

    def test_strip_proxy_from_subprocesses(self):
        text = read(WRITE_HANDOFF).lower()
        self.assertIn("subprocess", text, "must mention subprocess")
        self.assertIn("proxy", text, "must mention stripping proxy from subprocesses")


# ---------------------------------------------------------------------------
# Part E — discoverability
# ---------------------------------------------------------------------------

class DiscoverabilityTests(unittest.TestCase):
    def test_doctor_contains_write_handoff(self):
        self.assertIn("write-handoff", read(DOCTOR))

    def test_teamflow_debug_skill_lists_both_skills(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            proc = subprocess.run(
                [str(TEAMFLOW_BIN), "debug", "skill"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=clean_env(home),
                timeout=DEBUG_TIMEOUT,
            )
            output = proc.stdout.decode("utf-8", "replace")
            self.assertIn("write-handoff", output)
            self.assertIn("observer", output)


# ---------------------------------------------------------------------------
# Part B — observer: one blocking call, no ladder
# ---------------------------------------------------------------------------

class ObserveExecuteLoopContractTests(unittest.TestCase):
    """Observation is a single blocking wait, and the skill must say so."""

    def test_skill_under_3000_bytes(self):
        size = len(read(OBSERVE_SKILL).encode("utf-8"))
        self.assertLess(
            size, 3000,
            f"observer SKILL.md must stay under 3000 bytes, got {size}",
        )

    def test_leads_with_the_blocking_listener(self):
        text = read(OBSERVE_SKILL)
        self.assertIn("teamflow wait", text)
        self.assertIn("--since", text, "reconnecting must be idempotent")

    def test_escalation_is_conditional_not_a_fixed_ladder(self):
        text = read(OBSERVE_SKILL)
        self.assertIn("teamflow handoff status", text)
        self.assertIn("artifact", text.lower())
        self.assertNotIn(
            "Rung 1",
            text,
            "the ranked polling ladder is retired along with polling itself",
        )

    def test_invariants_present(self):
        text = read(OBSERVE_SKILL)
        self.assertIn("stale", text)
        self.assertIn("BLOCKED", text)
        self.assertIn("runner_exited", text)
        self.assertTrue(
            "silence" in text.lower(),
            "must mention silence",
        )

    def test_unchanged_state_is_documented_as_free(self):
        text = read(OBSERVE_SKILL).lower()
        self.assertRegex(
            text,
            r"costs? (you )?nothing|zero",
            "the skill must state that an unchanged execute loop costs nothing",
        )


# ---------------------------------------------------------------------------
# Part D — de-duplication
# ---------------------------------------------------------------------------

class HandoffDeDuplicationTests(unittest.TestCase):
    """The handoff field list is stated once in write-handoff; other files
    reference it instead of restating it."""

    def test_dedup_files_reference_write_handoff_and_have_few_field_tokens(self):
        for f in DEDUP_FILES:
            text = read(f)
            with self.subTest(file=str(f)):
                self.assertIn(
                    "write-handoff",
                    text,
                    f"{f} must reference write-handoff",
                )
                matches = count_field_tokens(text)
                self.assertLess(
                    matches,
                    3,
                    f"{f} restates {matches} field tokens; must reference "
                    f"write-handoff instead of restating the field list",
                )

    def test_write_handoff_owns_the_field_list(self):
        text = read(WRITE_HANDOFF)
        matches = count_field_tokens(text)
        self.assertGreaterEqual(
            matches,
            4,
            f"write-handoff must own the field list (≥4 FIELD_TOKENS), got {matches}",
        )


if __name__ == "__main__":
    unittest.main()
