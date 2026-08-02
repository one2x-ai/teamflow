"""Requirement tests for run-id ``memory-context-phase-b-20260728``.

These tests pin the Phase B dormant cold-memory TypeScript modules
(docs/teamflow-memory-context-design.md §11-13):

  * ``turn-block.ts``          – immutable TurnBlock + canonical XML
                                  serialization, SHA-256 content hash.
  * ``cold-memory-store.ts``   – pure ColdMemoryStore interface with
                                  ZERO basic-memory dependencies.
  * ``file-cold-store.ts``     – file-system ColdMemoryStore with
                                  atomic writes, hash verification, and
                                  offset semantics.
                                  Renamed from
                                  ``basic-memory-adapter.ts``.

Phase B modules are dormant: they are NOT wired into any Pi hooks.
Module-internal source-text assertions and the bun-subprocess
behavioral checks moved to the bun tests under
``.teamflow/extensions/memory-context/*.test.ts``; what remains here is
the wiring bun cannot test.

Wiring tests verify that ``init-project.sh`` ships all three files,
that ``doctor.sh`` checks them, and that ``README.md`` documents them.

All paths are relative to the repository root
``ROOT = Path(__file__).resolve().parents[3]``.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------
# Hermetic installer fixture (mirrors tests/test_memory_context_extension.py)
# --------------------------------------------------------------------


class _IsolatedTools:
    """Hermetic installer fixtures mirroring prior test conventions."""

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
            [str(ROOT / "scripts" / "install.sh"), str(project)],
            cwd=ROOT,
            env=self._base_env(),
            text=True,
            capture_output=True,
            timeout=60,
        )


# --------------------------------------------------------------------
# Cold-memory wiring (init-project.sh, doctor.sh, README.md)
# --------------------------------------------------------------------


class ColdMemoryWiringTests(unittest.TestCase):
    """init-project.sh ships, doctor.sh checks, and README.md documents
    the cold-memory modules."""

    def setUp(self):
        self.init_script = (
            (ROOT / "scripts" / "install.sh").read_text(
                encoding="utf-8"
            )
        )

    def test_init_script_references_turn_block(self):
        self.assertIn("turn-block", self.init_script)

    def test_init_script_references_cold_memory_store(self):
        self.assertIn("cold-memory-store", self.init_script)

    def test_init_script_references_file_cold_store(self):
        self.assertIn("file-cold-store", self.init_script)

    def test_hermetic_install_copies_all_three(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = _IsolatedTools(Path(directory))
            project = tools.new_git_project("target")
            completed = tools.initialize(project)
            self.assertEqual(
                completed.returncode, 0, completed.stderr
            )
            for rel in (
                "turn-block.ts",
                "cold-memory-store.ts",
                "file-cold-store.ts",
            ):
                with self.subTest(file=rel):
                    installed = (
                        project
                        / ".teamflow"
                        / "extensions"
                        / "memory-context"
                        / rel
                    )
                    self.assertTrue(
                        installed.is_file(),
                        f"{rel} must be installed by init-project.sh",
                    )

    def test_doctor_checks_cold_memory_modules(self):
        doctor = (
            (ROOT / "scripts" / "doctor.sh").read_text(encoding="utf-8")
        )
        for module in (
            "turn-block",
            "cold-memory-store",
            "file-cold-store",
        ):
            with self.subTest(module=module):
                self.assertIn(
                    module,
                    doctor,
                    f"doctor.sh must check for {module}",
                )

    def test_readme_documents_cold_memory(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "turn-block",
            readme,
            "README.md must document the cold-memory turn-block module",
        )
        self.assertTrue(
            "cold-memory" in readme or "冷记忆" in readme,
            "README.md must document cold memory (冷记忆)",
        )


if __name__ == "__main__":
    unittest.main()
