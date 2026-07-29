"""Removal guard for the external outer-loop-monitor (criterion B).

The SQLite/opencode.db session-file monitor has been retired. These tests
assert the skill, its scripts, and its install/CLI wiring are gone, and that
the repository no longer reaches into session SQLite files for monitoring.
Only this negative-migration test mentions the retired paths.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MONITOR_SKILL = ROOT / "skills/outer-loop-monitor"
MONITOR_SCRIPT = MONITOR_SKILL / "scripts/monitor_inner_loop.py"
INIT_PROJECT = ROOT / "scripts/install"
TEAMFLOW_CLI = ROOT / ".teamflow" / "bin" / "teamflow"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class OuterLoopMonitorRemovedTests(unittest.TestCase):
    def test_monitor_skill_directory_is_removed(self):
        self.assertFalse(MONITOR_SKILL.is_dir(), "skills/outer-loop-monitor must be removed")

    def test_monitor_script_is_removed(self):
        self.assertFalse(MONITOR_SCRIPT.is_file(), "monitor_inner_loop.py must be removed")

    def test_installer_does_not_install_monitor(self):
        self.assertNotIn("outer-loop-monitor", read(INIT_PROJECT))

    def test_teamflow_cli_has_no_monitor_dispatch(self):
        cli = read(TEAMFLOW_CLI)
        self.assertNotIn("outer-loop-monitor", cli)
        self.assertNotIn("monitor_inner_loop", cli)

    def test_no_script_reaches_session_sqlite(self):
        for script in ("install", "bootstrap.sh", "doctor.sh", "setup"):
            source = read(ROOT / "scripts" / script)
            with self.subTest(script=script):
                self.assertNotIn("opencode.db", source)
