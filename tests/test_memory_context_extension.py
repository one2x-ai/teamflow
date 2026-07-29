"""Requirement tests for run-id ``memory-context-phase-a-20260727``.

These tests pin the Phase A "observation-mode" Pi extension contract
(docs/teamflow-memory-context-design.md S22.A): a
``.teamflow/extensions/memory-context/index.ts`` extension that is an
observation-only state machine.  It registers turn/tool tracking hooks,
computes SHA-256 observation manifests, validates tool causal-pairs, and
appends exactly one observation receipt per turn -- all without modifying
context projection, adding hidden system prompt content, enabling
--no-context-files, or persisting cold memory.

Wiring requirements also assert that ``pi-runtime`` loads both extensions
(``teamflow-task`` **and** ``memory-context``), that the installer ships
the new file, that ``doctor.sh`` validates it, and that ``README.md``
documents it.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[1]``.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / ".teamflow" / "bin" / "teamflow"
EXTENSION_FILE = (
    ROOT / ".teamflow" / "extensions" / "memory-context" / "index.ts"
)


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
# Source-text assertions on the extension .ts file
# --------------------------------------------------------------------


class ObservationExtensionFileTests(unittest.TestCase):
    """Phase A contracts 1-5 and invariants, asserted on extension source text."""

    def setUp(self):
        self.text = (
            EXTENSION_FILE.read_text(encoding="utf-8")
            if EXTENSION_FILE.is_file()
            else ""
        )

    # -- existence ------------------------------------------------------

    def test_extension_file_exists(self):
        self.assertTrue(
            EXTENSION_FILE.is_file(),
            ".teamflow/extensions/memory-context/index.ts must exist",
        )

    # -- required hook registrations (contract 1) ----------------------

    def test_registers_before_agent_start_hook(self):
        self.assertIn("before_agent_start", self.text)

    def test_registers_agent_settled_hook(self):
        self.assertIn("agent_settled", self.text)

    def test_registers_session_start_hook(self):
        self.assertIn("session_start", self.text)

    def test_registers_tool_call_hook(self):
        self.assertIn("tool_call", self.text)

    def test_registers_tool_result_hook(self):
        self.assertIn("tool_result", self.text)

    # -- Phase A non-goals superseded by Phase D (context hook + compact interception) --

    def test_extension_does_not_register_no_context_files_flag(self):
        # Phase C: a comment or documentation mention of --no-context-files
        # is acceptable; the extension must NOT programmatically register
        # the flag (e.g. via pi.registerFlag("no-context-files", ...) or a
        # noContextFiles option field).
        pattern = re.compile(
            r'registerFlag\s*\(\s*["\']no-context-files["\']'
            r'|noContextFiles\s*[:=]',
            re.MULTILINE,
        )
        self.assertFalse(
            bool(pattern.search(self.text)),
            "extension must NOT register no-context-files as a programmatic flag",
        )

    def test_does_not_add_hidden_system_prompt(self):
        # The observation extension must NOT inject a systemPrompt field
        # into BeforeAgentStartEventResult (contract: no hidden prompt).
        self.assertFalse(
            bool(
                re.search(
                    r"""systemPrompt\s*:\s*['"]""",
                    self.text,
                )
            ),
            "Phase A extension must NOT add a systemPrompt field to results",
        )

    def test_persists_cold_memory_in_phase_b(self):
        # Phase B completion: writeTurn MUST now appear (persistence wired).
        self.assertIn(
            "writeTurn",
            self.text,
            "Phase B extension must call writeTurn to persist turns",
        )

    # -- SHA-256 manifest computation (contract 4) ---------------------

    def test_computes_sha256_manifest(self):
        self.assertTrue(
            ("sha256" in self.text.lower() or "createHash" in self.text),
            "extension must compute SHA-256 hashes",
        )
        self.assertTrue(
            (
                "systemPromptHash" in self.text
                or "manifestHash" in self.text
                or "content_hash" in self.text
                or "contextMessagesHash" in self.text
            ),
            "extension must name at least one manifest hash field",
        )

    # -- observation receipt (contract 4) ------------------------------

    def test_appends_observation_receipt(self):
        self.assertIn("appendEntry", self.text)
        self.assertTrue(
            "'teamflow:observation'" in self.text
            or '"teamflow:observation"' in self.text,
            "extension must appendEntry('teamflow:observation', ...)",
        )

    def test_observation_receipt_has_version(self):
        self.assertIn("version", self.text)
        self.assertIn("1", self.text)

    # -- tool causal-pair validation (contract 4) ----------------------

    def test_validates_tool_causal_pairs(self):
        self.assertTrue(
            any(
                token in self.text
                for token in (
                    "unmatchedCalls",
                    "unmatchedResults",
                    "unmatched",
                    "causal",
                )
            ),
            "extension must validate tool causal-pairs",
        )

    # -- turn boundary tracking (contract 2-3) -------------------------

    def test_tracks_turn_index(self):
        self.assertTrue(
            "turnIndex" in self.text or "turn_index" in self.text,
            "extension must track a turnIndex",
        )

    def test_tracks_tool_call_id(self):
        self.assertTrue(
            "toolCallId" in self.text or "tool_call_id" in self.text,
            "extension must track tool calls by toolCallId",
        )


# --------------------------------------------------------------------
# Provider-free smoke test
# --------------------------------------------------------------------


class ObservationExtensionLoadingTests(unittest.TestCase):
    """The extension file must parse / load under pi without a provider."""

    def test_extension_loads_provider_free(self):
        if shutil.which("pi") is None:
            self.skipTest("pi not on PATH")
        env = os.environ.copy()
        env["TEAMFLOW_AGENT_ROLE"] = "planner"
        env["TEAMFLOW_AGENT_DEPTH"] = "0"
        completed = _run(
            [
                "pi",
                "--extension",
                str(EXTENSION_FILE),
                "--help",
            ],
            env=env,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"pi --extension ... --help exited {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
        )


# --------------------------------------------------------------------
# pi-runtime wiring (contract 6)
# --------------------------------------------------------------------


class PiRuntimeWiringTests(unittest.TestCase):
    """pi-runtime must load BOTH extensions via --extension."""

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

    def _extension_values(self, argv):
        """Return the list of values following every --extension flag."""
        values = []
        i = 0
        while i < len(argv) - 1:
            if argv[i] == "--extension":
                values.append(argv[i + 1])
                i += 2
            else:
                i += 1
        return values

    def test_argv_includes_memory_context_extension(self):
        data = self._run_print("planner", "ping")
        argv = data.get("argv", [])
        self.assertIsInstance(argv, list)
        values = self._extension_values(argv)
        self.assertTrue(
            any("memory-context" in v for v in values),
            f"--extension values must include memory-context: {values}",
        )

    def test_argv_includes_both_extensions(self):
        data = self._run_print("planner", "ping")
        argv = data.get("argv", [])
        values = self._extension_values(argv)
        self.assertTrue(
            any("teamflow-task" in v for v in values),
            f"--extension values must include teamflow-task: {values}",
        )
        self.assertTrue(
            any("memory-context" in v for v in values),
            f"--extension values must include memory-context: {values}",
        )


# --------------------------------------------------------------------
# Installer wiring (contract 7)
# --------------------------------------------------------------------


class InstallerExtensionTests(unittest.TestCase):
    """init-project.sh must ship the memory-context extension."""

    def setUp(self):
        self.init_script = (ROOT / "scripts" / "install").read_text(
            encoding="utf-8"
        )

    def test_init_script_references_memory_context(self):
        self.assertIn("memory-context", self.init_script)

    def test_hermetic_install_copies_memory_context(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = _IsolatedTools(Path(directory))
            project = tools.new_git_project("target")
            completed = tools.initialize(project)
            self.assertEqual(completed.returncode, 0, completed.stderr)

            installed = (
                project
                / ".teamflow"
                / "extensions"
                / "memory-context"
                / "index.ts"
            )
            self.assertTrue(
                installed.is_file(),
                ".teamflow/extensions/memory-context/index.ts must be installed",
            )

            manifest = json.loads(
                (project / ".teamflow" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            files = manifest.get("files")
            keys = list(files.keys()) if isinstance(files, dict) else list(files)
            self.assertTrue(
                any(
                    key.startswith(".teamflow/extensions/memory-context")
                    for key in keys
                ),
                f"manifest must list memory-context key: {keys}",
            )


# --------------------------------------------------------------------
# Doctor wiring (contract 8)
# --------------------------------------------------------------------


class DoctorExtensionTests(unittest.TestCase):
    """doctor.sh must validate the memory-context extension."""

    def test_doctor_checks_memory_context_extension(self):
        doctor = (ROOT / "scripts" / "doctor.sh").read_text(encoding="utf-8")
        self.assertIn("memory-context", doctor)


# --------------------------------------------------------------------
# README documentation (contract 9)
# --------------------------------------------------------------------


class ReadmeDocumentationTests(unittest.TestCase):
    """README.md must document the observation extension."""

    def test_readme_documents_memory_context(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("memory-context", readme)


# --------------------------------------------------------------------
# Hermetic installer fixture (mirrors tests/test_pi_task_extension.py)
# --------------------------------------------------------------------


class _IsolatedTools:
    """Hermetic installer fixtures mirroring tests/test_pi_task_extension.py."""

    def __init__(self, root):
        self.root = root
        self.home = root / "home"
        self.bin = root / "fake-bin"
        self.home.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        self._write_executable(
            self.bin / "pi",
            "#!/bin/sh\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            "  printf '0.82.1\\n'\n"
            'elif [ "${1:-}" = "debug" ] && [ "${2:-}" = "skill" ]; then\n'
            "  printf 'plan-change\\nbasic-memory-cli\\n'\n"
            "fi\n"
            "exit 0\n",
        )
        self._write_executable(
            self.bin / "basic-memory",
            "#!/bin/sh\n"
            'if [ "${1:-} ${2:-}" = "project info" ]; then\n'
            "  exit 1\n"
            "fi\n"
            'if [ "${1:-}" = "status" ]; then\n'
            "  printf '{}\\n'\n"
            "fi\n"
            "exit 0\n",
        )

    def _write_executable(self, path, text):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def new_git_project(self, name):
        project = self.root / name
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        return project

    def _base_env(self):
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(
                ("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")
            ):
                env.pop(key)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin}:{env['PATH']}",
            }
        )
        return env

    def initialize(self, project):
        return subprocess.run(
            [str(ROOT / "scripts" / "install"), str(project)],
            cwd=ROOT,
            env=self._base_env(),
            text=True,
            capture_output=True,
            timeout=60,
        )


if __name__ == "__main__":
    unittest.main()
