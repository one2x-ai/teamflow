"""Install isolation for the read-only memory server.

`teamflow server` browses the shared cross-project store under
~/.teamflow/memory/. It reads global data, not project data, so its source
lives only in the Teamflow repository and is never copied into a business
project — the same rule that keeps scripts/ out. Only the thin dispatch
wrapper .teamflow/bin/server ships, and it resolves the server source from
the Teamflow installation.

This asserts scripts/install.sh behavior, so it lives in the outer tests/
tree next to the other install.sh checks. The server's own HTTP, UI, and
scope behavior is tested in server/tests/.

File- and text-level only; no bun runtime required.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def write_fake_tools(bin_dir: Path) -> Path:
    """Place fake `pi` and `basic-memory` on an isolated PATH.

    The fake basic-memory logs every argv and serves canned JSON for the
    read-only tool subcommands the server must call. FAKE_BASIC_MEMORY_MODE
    (`fail` / `badjson` / `badshape`) exercises the 502 paths.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir.parent / "basic-memory.log"
    write_executable(
        bin_dir / "pi",
        """#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  printf '0.82.1\\n'
elif [ "${1:-}" = "debug" ] && [ "${2:-}" = "skill" ]; then
  printf 'plan-change\\nbasic-memory-cli\\n'
fi
exit 0
""",
    )
    write_executable(
        bin_dir / "basic-memory",
        """#!/bin/sh
LOG="${FAKE_BASIC_MEMORY_LOG:-/dev/null}"
printf '%s\\n' "$*" >> "$LOG"
if [ "${1:-} ${2:-}" = "project info" ]; then
  exit 1
fi
if [ "${1:-}" = "status" ]; then
  printf '{}\\n'
  exit 0
fi
if [ "${1:-} ${2:-}" = "tool read-note" ]; then
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "fail" ]; then
    exit 1
  fi
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "badjson" ]; then
    printf 'not-valid-json{{{'
    exit 0
  fi
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "badshape" ]; then
    printf '[]\\n'
    exit 0
  fi
  cat "${FAKE_BASIC_MEMORY_DETAIL_FILE:?}"
  exit 0
fi
if [ "${1:-} ${2:-}" = "tool recent-activity" ] || [ "${1:-} ${2:-}" = "tool search-notes" ]; then
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "fail" ]; then
    exit 1
  fi
  if [ "${FAKE_BASIC_MEMORY_MODE:-}" = "badjson" ]; then
    printf 'not-valid-json{{{'
    exit 0
  fi
  PAGE_SIZE=""
  PENDING=""
  for arg in "$@"; do
    if [ -n "$PENDING" ]; then
      PAGE_SIZE="$arg"
      break
    fi
    if [ "$arg" = "--page-size" ]; then
      PENDING=1
    fi
  done
  if [ -n "$PAGE_SIZE" ] && [ "$PAGE_SIZE" -gt 100 ] 2>/dev/null; then
    printf 'Error: page_size must be <= 100, got %s\\n' "$PAGE_SIZE" >&2
    exit 1
  fi
  if [ "${1:-} ${2:-}" = "tool recent-activity" ]; then
    cat "${FAKE_BASIC_MEMORY_RECENT_FILE:?}"
  else
    cat "${FAKE_BASIC_MEMORY_SEARCH_FILE:?}"
  fi
  exit 0
fi
exit 0
""",
    )
    return log



class MemoryServerInstallerTests(unittest.TestCase):
    """Installer/dispatch wiring. File- and text-level only; no bun required."""

    def test_installer_wires_server_source_and_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            bin_dir = root / "fake-bin"
            launchers = root / "launchers"
            home.mkdir(parents=True)
            launchers.mkdir(parents=True)
            log = write_fake_tools(bin_dir)
            project = root / "target"
            project.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)

            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_", "BASIC_MEMORY_")):
                    env.pop(key)
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "TEAMFLOW_HOME": str(home / ".teamflow"),
                    "TEAMFLOW_BIN_DIR": str(launchers),
                    "FAKE_BASIC_MEMORY_LOG": str(log),
                }
            )
            completed = subprocess.run(
                [str(ROOT / "scripts/install.sh"), str(project)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


            # The memory browser reads the shared cross-project store under
            # ~/.teamflow/memory/, not project data, so its source stays in
            # the Teamflow repository and is never copied per project.
            for relative in (
                ".teamflow/server",
                ".teamflow/server/src/server.ts",
                ".teamflow/server/package.json",
            ):
                with self.subTest(path=relative):
                    self.assertFalse(
                        (project / relative).exists(),
                        f"{relative} must not be installed: the server is a "
                        "single global tool, not per-project runtime",
                    )

            manifest = json.loads(
                (project / ".teamflow/manifest.json").read_text(encoding="utf-8")
            )
            server_entries = [
                key for key in manifest["files"]
                if key.startswith(".teamflow/server/")
            ]
            self.assertEqual(
                server_entries, [],
                f"manifest must not manage server sources: {server_entries}",
            )
            for manifest_key in manifest["files"]:
                with self.subTest(manifest_key=manifest_key):
                    self.assertTrue(manifest_key.startswith(".teamflow/"), manifest_key)

            # The dispatch wrapper still ships, and resolves the server from
            # the Teamflow installation rather than the project.
            self.assertTrue((project / ".teamflow/bin/server").is_file())
            teamflow_bin = (project / ".teamflow/bin/teamflow").read_text(encoding="utf-8")
            self.assertRegex(teamflow_bin, r'["\']server["\']')
            self.assertIn("bin/server", teamflow_bin)

            server_wrapper = (project / ".teamflow/bin/server").read_text(encoding="utf-8")
            self.assertIn("bun", server_wrapper)
            self.assertRegex(
                server_wrapper,
                r"TEAMFLOW_SERVER_DIR|TEAMFLOW_HOME|teamflow/server",
                "bin/server must resolve the globally installed server source",
            )
            self.assertRegex(
                server_wrapper,
                r"exit 1|error:",
                "bin/server must fail with a clear message when the source "
                "cannot be located",
            )


if __name__ == "__main__":
    unittest.main()
