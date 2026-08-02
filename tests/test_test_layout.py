"""Requirement tests for the three-tier test layout.

Tests live next to the code they exercise:

  tests/               scripts/ behavior: install, uninstall, wrapper
                       assembly, namespace and cleanup guards
  .teamflow/tests/     the installable runtime: agents, extensions, skills,
                       bin/ — everything under .teamflow/
  server/tests/        the Bun HTTP memory server

Neither .teamflow/tests/ nor server/tests/ may reach a target project:
.teamflow/tests/ is outside the installer's FILES allowlist and outside the
directories it walks, and server/ is not installed at all — the memory
browser is a single global tool, so a copy per project never exists.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTER_TESTS = ROOT / "tests"
RUNTIME_TESTS = ROOT / ".teamflow" / "tests"
SERVER_TESTS = ROOT / "server" / "tests"
INSTALL = ROOT / "scripts" / "install.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class TestTierLayoutTests(unittest.TestCase):
    """Each tier exists and holds Python test modules."""

    def test_all_three_tiers_exist(self):
        for tier in (OUTER_TESTS, RUNTIME_TESTS, SERVER_TESTS):
            with self.subTest(tier=tier.relative_to(ROOT)):
                self.assertTrue(tier.is_dir(), f"{tier} must exist")

    def test_each_tier_has_tests(self):
        for tier in (OUTER_TESTS, RUNTIME_TESTS, SERVER_TESTS):
            with self.subTest(tier=tier.relative_to(ROOT)):
                # server/tests/ migrated from pytest to bun test in Phase C.1:
                # test modules are now *.test.ts, not test_*.py.
                modules = sorted(tier.glob("test_*.py"))
                if tier == SERVER_TESTS:
                    modules.extend(sorted(tier.glob("*.test.ts")))
                self.assertTrue(
                    modules, f"{tier} must contain at least one test module"
                )

    def test_moved_modules_resolve_repository_root(self):
        """A relocated module must still compute ROOT as the repo root."""
        for tier, depth in ((RUNTIME_TESTS, 2), (SERVER_TESTS, 2)):
            for path in sorted(tier.glob("test_*.py")):
                text = read(path)
                if "Path(__file__).resolve().parents" not in text:
                    continue
                with self.subTest(module=str(path.relative_to(ROOT))):
                    self.assertIn(
                        f"parents[{depth}]",
                        text,
                        f"{path.name} sits one level deeper, so ROOT needs "
                        f"parents[{depth}]",
                    )


class OuterTierScopeTests(unittest.TestCase):
    """The outer tier covers scripts/, not runtime internals."""

    # This module documents the layout rule, so it necessarily names the
    # extension path it forbids other modules from inspecting.
    EXEMPT = {"test_test_layout.py"}

    def test_outer_tier_does_not_test_extensions_directly(self):
        """Extension module contracts belong to .teamflow/tests/."""
        for path in sorted(OUTER_TESTS.glob("test_*.py")):
            if path.name in self.EXEMPT:
                continue
            text = read(path)
            with self.subTest(module=path.name):
                self.assertNotIn(
                    "extensions/memory-context/turn-block.ts",
                    text,
                    f"{path.name} inspects an extension module; move it to "
                    ".teamflow/tests/",
                )


class InstallerExcludesTestsTests(unittest.TestCase):
    """The installer ships product files only."""

    def test_install_script_does_not_walk_server(self):
        """server/ no longer ships at all, so no prune rule is needed.

        The memory browser reads the shared cross-project store, so a single
        global copy serves every project; install.sh stopped walking server/
        entirely. That is a stronger guarantee than pruning tests from the
        walk, and it means server/tests/ can never leak.
        """
        text = read(INSTALL)
        self.assertNotIn(
            "find server",
            text,
            "install.sh must not walk server/: the browser is installed once "
            "globally, not per project",
        )
        self.assertNotIn(
            ".teamflow/server/",
            text,
            "install.sh must not manage any server path",
        )

    def test_install_manifest_lists_no_test_modules(self):
        """--dry-run output must not ship any test module.

        A product script may legitimately be named test_patch.py (it
        implements the `teamflow test-patch` gate), so the check targets
        tests/ directories and test_*.py modules that live in a tests tree,
        not every filename containing "test".
        """
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "target"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
                    env.pop(key)
            completed = subprocess.run(
                [str(INSTALL), "--dry-run", str(project)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        offenders = [
            line.strip()
            for line in completed.stdout.splitlines()
            if "/tests/" in line
        ]
        self.assertEqual(
            offenders, [], f"installer must not ship test modules: {offenders}"
        )


if __name__ == "__main__":
    unittest.main()
