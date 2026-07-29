"""Requirement tests for Phase C: visible XML context injection.

Phase C (docs/teamflow-memory-context-design.md S22.C, S23 可见上下文):

- ``pi-runtime`` passes ``--no-context-files`` so Pi does NOT auto-inject
  AGENTS.md / CLAUDE.md into the system prompt.
- The ``memory-context`` extension's ``before_agent_start`` handler reads
  the project rules file (AGENTS.md), computes its SHA-256 hash, and
  returns a ``message`` with ``display: true`` — a visible XML custom
  message that participates in LLM context and the UI.
- The XML payload is wrapped in ``<teamflow_context>`` with a
  ``<context_manifest>`` listing each source ``kind`` and ``hash``.
- No hidden ``systemPrompt`` concatenation occurs.
- Phase D lifts the context-hook and compact-interception non-goals;
  see ``test_memory_context_phase_d.py``.

All source-text assertions read EXTENSION_FILE
(``.teamflow/extensions/memory-context/index.ts``).  The pi-runtime
wiring test uses the provider-free ``--print`` mode established in
``test_memory_context_extension.py``.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / ".teamflow" / "bin" / "teamflow"
EXTENSION_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
)
README_FILE = ROOT / "README.md"


def _run(args, *, cwd=ROOT, env=None, timeout=30):
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


# --------------------------------------------------------------------
# pi-runtime wiring (contract: --no-context-files)
# --------------------------------------------------------------------


class PiRuntimeNoContextFilesTests(unittest.TestCase):
    """pi-runtime argv must include --no-context-files (Phase C)."""

    def _run_print(self, role, prompt):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            for key in list(env):
                if key.startswith(
                    ("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")
                ):
                    env.pop(key)
            completed = _run(
                [str(WRAPPER), "run", "--agent", role, prompt, "--print"],
                env=env,
            )
        self.assertEqual(
            completed.returncode,
            0,
            f"wrapper --print exited {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
        )
        self.assertTrue(
            completed.stdout.strip(),
            "--print must emit JSON on stdout",
        )
        return json.loads(completed.stdout)

    def test_pi_runtime_passes_no_context_files(self):
        """pi-runtime argv must contain --no-context-files."""
        data = self._run_print("planner", "ping")
        argv = data.get("argv", [])
        self.assertIsInstance(argv, list)
        self.assertIn(
            "--no-context-files",
            argv,
            "pi-runtime argv must include --no-context-files "
            f"(got: {argv})",
        )


# --------------------------------------------------------------------
# before_agent_start returns a visible message (Phase C contracts)
# --------------------------------------------------------------------


class BeforeAgentStartMessageTests(unittest.TestCase):
    """The before_agent_start handler returns a display:true message."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    def test_extension_file_exists(self):
        self.assertTrue(
            EXTENSION_FILE.is_file(),
            ".teamflow/extensions/memory-context/index.ts must exist",
        )

    def test_before_agent_start_returns_message(self):
        """The before_agent_start handler must return a result with a
        ``message`` field (BeforeAgentStartEventResult.message)."""
        self.assertIn("before_agent_start", self.text)
        self.assertIn("message", self.text)

    def test_message_has_custom_type(self):
        """Source must define a customType for the context message."""
        self.assertTrue(
            "teamflow:context" in self.text
            or "teamflow:context_xml" in self.text,
            "before_agent_start message must use a teamflow:context* customType",
        )

    def test_message_has_display_true(self):
        """The injected message must be visible (display: true)."""
        self.assertTrue(
            "display: true" in self.text or "display:true" in self.text,
            "before_agent_start message must set display: true",
        )

    def test_message_contains_teamflow_context_xml(self):
        """The message content must be wrapped in <teamflow_context>."""
        self.assertIn(
            "<teamflow_context",
            self.text,
            "message content must contain <teamflow_context> XML wrapper",
        )

    def test_message_contains_context_manifest(self):
        """The XML must include a <context_manifest> section."""
        self.assertTrue(
            "context_manifest" in self.text
            or "<context_manifest" in self.text,
            "message content must contain context_manifest",
        )

    def test_manifest_has_source_with_hash(self):
        """The manifest must list a source with kind and hash."""
        self.assertTrue(
            'kind="project_rules"' in self.text
            or "project_rules" in self.text,
            'manifest source must specify kind="project_rules"',
        )
        self.assertTrue(
            'hash="sha256:' in self.text or "sha256:" in self.text,
            'manifest source must include hash="sha256:..."',
        )

    def test_reads_project_rules_file(self):
        """The handler must read AGENTS.md from the project."""
        self.assertIn("AGENTS.md", self.text)
        self.assertTrue(
            "readFileSync" in self.text or "fs.read" in self.text,
            "handler must use readFileSync or fs.read to load AGENTS.md",
        )

    def test_computes_hash_of_project_rules(self):
        """The handler must compute a SHA-256 hash of project rules."""
        self.assertTrue(
            "createHash" in self.text or "sha256" in self.text.lower(),
            "handler must compute SHA-256 of project rules",
        )


# --------------------------------------------------------------------
# Role-aware project rules injection (needs_project_rules frontmatter)
# --------------------------------------------------------------------


class RoleAwareProjectRulesTests(unittest.TestCase):
    """Roles control context injection via needs_project_rules frontmatter."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    def test_extension_reads_needs_project_rules(self):
        """Extension must parse needs_project_rules from agent frontmatter."""
        self.assertIn("needs_project_rules", self.text)

    def test_extension_has_role_context_level(self):
        """Extension must have a function that returns the context level."""
        self.assertIn("roleContextLevel", self.text)

    def test_extension_caches_context_level(self):
        """Extension must cache the frontmatter parse result."""
        self.assertIn("contextLevelCache", self.text)

    def test_extension_supports_three_levels(self):
        """Extension must support full, shared, and none levels."""
        self.assertIn('"full"', self.text)
        self.assertIn('"shared"', self.text)
        self.assertIn('"none"', self.text)

    def test_extension_injects_shared_rules(self):
        """Extension must read .teamflow/AGENTS.md for shared_rules section."""
        self.assertIn("shared_rules", self.text)
        self.assertIn(".teamflow", self.text)

    def test_test_runner_has_needs_project_rules_false(self):
        """test-runner.md must declare needs_project_rules: false."""
        agent = (
            ROOT / ".teamflow" / "agents" / "test-runner.md"
        ).read_text(encoding="utf-8")
        self.assertIn("needs_project_rules: false", agent)

    def test_command_has_needs_project_rules_false(self):
        """command.md must declare needs_project_rules: false."""
        agent = (
            ROOT / ".teamflow" / "agents" / "command.md"
        ).read_text(encoding="utf-8")
        self.assertIn("needs_project_rules: false", agent)

    def test_planner_does_not_have_needs_project_rules_false(self):
        """planner.md must NOT declare needs_project_rules: false."""
        agent = (
            ROOT / ".teamflow" / "agents" / "planner.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("needs_project_rules: false", agent)


# --------------------------------------------------------------------
# Phase C invariant (context/compact hooks added by Phase D)
# --------------------------------------------------------------------


class PhaseNonGoalTests(unittest.TestCase):
    """Phase C invariant: no hidden systemPrompt.  Context hook and
    compact interception were Phase C non-goals, lifted by Phase D."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    def test_no_hidden_system_prompt(self):
        """before_agent_start must NOT return a systemPrompt string literal
        (no hidden business system-prompt concatenation)."""
        self.assertFalse(
            bool(
                re.search(
                    r"""systemPrompt\s*:\s*['"]""",
                    self.text,
                )
            ),
            "before_agent_start must NOT return systemPrompt: '...'",
        )


# --------------------------------------------------------------------
# README documentation
# --------------------------------------------------------------------


class ReadmePhaseCTests(unittest.TestCase):
    """README.md must document Phase C visible XML context injection."""

    def test_readme_documents_phase_c(self):
        readme = README_FILE.read_text(encoding="utf-8")
        self.assertTrue(
            "visible XML" in readme
            or "Phase C" in readme
            or "context inject" in readme.lower()
            or "可见 XML" in readme
            or "context 注入" in readme,
            "README must mention Phase C visible XML / context injection",
        )


if __name__ == "__main__":
    unittest.main()
