"""Requirement tests for run-id ``pi-task-role-launcher-20260727``.

These tests pin the Pi "task" role-launcher extension contract: a
``.teamflow/extensions/teamflow-task/index.ts`` extension that registers a
``task`` tool gated on ``TEAMFLOW_AGENT_DEPTH``, plus ``pi-runtime`` wiring
that exports ``TEAMFLOW_AGENT_ROLE``/``TEAMFLOW_AGENT_DEPTH`` and passes
``--extension`` to ``pi``.

Open questions (assumptions encoded below):
- The ``--extension`` value may be either the directory
  ``extensions/teamflow-task`` or the file ``extensions/teamflow-task/index.ts``.
  The assertion uses a substring match on ``extensions/teamflow-task`` so
  either form passes.
- The installer may list the extension explicitly in FILES or via a
  ``find .teamflow/extensions`` block. The tests assert the init script text
  mentions ``extensions/teamflow-task`` AND that a hermetic install copies
  the file.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[1]``.
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
EXTENSION_FILE = ROOT / ".teamflow" / "extensions" / "teamflow-task" / "index.ts"


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


class ExtensionFileTests(unittest.TestCase):
    """Contract 1-3: extension file, depth gate, tool registration, child spawn."""

    def test_extension_file_exists(self):
        self.assertTrue(
            EXTENSION_FILE.is_file(),
            ".teamflow/extensions/teamflow-task/index.ts must exist",
        )

    def test_depth_gate_reads_teamflow_agent_depth_and_compares_to_zero(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertIn("TEAMFLOW_AGENT_DEPTH", text)
        self.assertTrue(
            re.search(r"===\s*0|==\s*0|===\s*['\"]0['\"]|<\s*1|!==\s*0", text),
            "depth gate must compare TEAMFLOW_AGENT_DEPTH to 0",
        )

    def test_registers_task_tool_with_agent_and_prompt_parameters(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertRegex(text, r"['\"]task['\"]")
        self.assertIn("agent", text)
        self.assertIn("prompt", text)

    def test_references_agents_directory_as_source_of_truth(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertTrue(
            "agents/" in text or ".teamflow/agents" in text,
            "extension must reference the agents/ directory",
        )

    def test_splits_model_on_slash(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertRegex(text, r"split\s*\(\s*['\"]\/['\"]")

    def test_child_env_sets_teamflow_agent_depth_to_one(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"TEAMFLOW_AGENT_DEPTH['\"]?\s*[:=]\s*['\"]?1",
        )

    def test_child_env_sets_teamflow_agent_role(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertIn("TEAMFLOW_AGENT_ROLE", text)

    def test_child_argv_contains_required_pi_flags(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        for token in ("--mode", "json", "--provider", "--model", "--system-prompt"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_handles_nonzero_exit_code(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertTrue(
            re.search(r"!==\s*0|!=\s*0|>\s*0|nonzero", text),
            "extension must handle nonzero child exit codes",
        )

    def test_handles_finish_reasons_error_aborted_length(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        for reason in ("error", "aborted", "length"):
            with self.subTest(reason=reason):
                self.assertIn(reason, text)

    def test_handles_unknown_or_invalid_role(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertTrue(
            re.search(
                r"throw|unknown|invalid|not\s+found|existsSync|isFile|exists",
                text,
                re.IGNORECASE,
            ),
            "extension must reject unknown/invalid agents",
        )

    def test_has_abort_or_cancellation_path(self):
        text = EXTENSION_FILE.read_text(encoding="utf-8")
        self.assertTrue(
            "abort" in text.lower() or "cancel" in text.lower(),
            "extension must handle abort/cancellation",
        )


class PiRuntimeDepthEnvTests(unittest.TestCase):
    """Contract 4: pi-runtime exports role/depth env and passes --extension."""

    def _run_print(self, role, prompt):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                    env.pop(key)
            completed = _run(
                [str(WRAPPER), "run", "--agent", role, prompt, "--print"],
                env=env,
            )
        self.assertEqual(
            completed.returncode, 0,
            f"wrapper --print exited {completed.returncode}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
        )
        self.assertTrue(completed.stdout.strip(), "--print must emit JSON on stdout")
        return json.loads(completed.stdout)

    def test_planner_run_print_exports_role_and_depth(self):
        data = self._run_print("planner", "ping")
        env = data.get("env", {})
        self.assertEqual(env.get("TEAMFLOW_AGENT_ROLE"), "planner")
        self.assertEqual(env.get("TEAMFLOW_AGENT_DEPTH"), "0")

    def test_coder_run_print_exports_role(self):
        data = self._run_print("coder", "do-work")
        env = data.get("env", {})
        self.assertEqual(env.get("TEAMFLOW_AGENT_ROLE"), "coder")
        self.assertEqual(env.get("TEAMFLOW_AGENT_DEPTH"), "0")

    def test_argv_includes_extension_pointing_at_teamflow_task(self):
        data = self._run_print("planner", "ping")
        argv = data.get("argv", [])
        self.assertIsInstance(argv, list)
        self.assertIn("--extension", argv)
        idx = argv.index("--extension")
        self.assertGreater(len(argv), idx + 1)
        self.assertIn("extensions/teamflow-task", argv[idx + 1])


class InstallerExtensionTests(unittest.TestCase):
    """Contract 6: the installer ships the extension file under .teamflow/."""

    def setUp(self):
        self.init_script = (ROOT / "scripts/init-project.sh").read_text(encoding="utf-8")

    def test_init_script_references_extension(self):
        self.assertIn("extensions/teamflow-task", self.init_script)

    def test_hermetic_install_copies_extension_file(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = _IsolatedTools(Path(directory))
            project = tools.new_git_project("target")
            completed = tools.initialize(project)
            self.assertEqual(completed.returncode, 0, completed.stderr)

            installed = project / ".teamflow" / "extensions" / "teamflow-task" / "index.ts"
            self.assertTrue(
                installed.is_file(),
                ".teamflow/extensions/teamflow-task/index.ts must be installed",
            )

            manifest = json.loads(
                (project / ".teamflow" / "manifest.json").read_text(encoding="utf-8")
            )
            files = manifest.get("files")
            keys = list(files.keys()) if isinstance(files, dict) else list(files)
            self.assertTrue(
                any(key.startswith(".teamflow/extensions/") for key in keys),
                f"manifest must list a key under .teamflow/extensions/: {keys}",
            )


class DoctorExtensionTests(unittest.TestCase):
    """Contract 7: doctor validates the teamflow-task extension."""

    def test_doctor_script_contains_extension_check(self):
        doctor = (ROOT / "scripts/doctor.sh").read_text(encoding="utf-8")
        self.assertTrue(
            "extensions/teamflow-task" in doctor or "extensions/" in doctor,
            "doctor.sh must contain an extension-presence check",
        )


class _IsolatedTools:
    """Hermetic installer fixtures mirroring tests/test_pi_wrapper.py."""

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
            if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                env.pop(key)
        env.update({
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{env['PATH']}",
        })
        return env

    def initialize(self, project):
        return subprocess.run(
            [str(ROOT / "scripts/init-project.sh"), str(project)],
            cwd=ROOT,
            env=self._base_env(),
            text=True,
            capture_output=True,
            timeout=60,
        )


if __name__ == "__main__":
    unittest.main()
