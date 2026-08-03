"""Retirement tests: the custom Node health gateway is gone.

Under the container-sidecar contract, ``scripts/opencode_health_gateway.js``
is retired.  The Dockerfile directly executes ``opencode web`` on
loopback 127.0.0.1:13000, and the public-port health-probe synthesis and
auth forwarding belong to the Caddy sidecar (documented in
``docs/container-sidecar-deployment.md``, not reimplemented here).

These tests assert the ABSENCE of the gateway script and of any reference
to it as the active runtime across tracked source, docs, and configuration.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_SCRIPT = ROOT / "scripts" / "opencode_health_gateway.js"
DOCKERFILE = ROOT / "Dockerfile"

#: Substring that must not appear in any active-runtime file.
RETIRED_MARKER = "opencode_health_gateway"

#: The gateway runtime path prefix that must be absent from the Dockerfile.
GATEWAY_RUNTIME_PATH = "/opt/teamflow-runtime"

#: Individual files to scan for stale gateway references.
SCAN_FILES = [
    ROOT / "Dockerfile",
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / ".teamflow" / "AGENTS.md",
]

#: Directories whose every file is scanned for stale gateway references.
SCAN_DIRS = [
    ROOT / "scripts",
    ROOT / "docs",
]


class GatewayScriptAbsenceTests(unittest.TestCase):
    """B1: The gateway launcher script must not exist."""

    def test_gateway_script_does_not_exist(self):
        self.assertFalse(
            GATEWAY_SCRIPT.exists(),
            f"Gateway script must be deleted: {GATEWAY_SCRIPT}",
        )


class GatewayReferenceAbsenceTests(unittest.TestCase):
    """B2: No tracked non-run file references ``opencode_health_gateway``."""

    def test_no_stale_gateway_references(self):
        offenders = []

        # --- individual files ---
        for fpath in SCAN_FILES:
            if not fpath.exists():
                continue
            text = fpath.read_text(encoding="utf-8", errors="replace")
            if RETIRED_MARKER in text:
                offenders.append(str(fpath.relative_to(ROOT)))

        # --- directory trees ---
        for dpath in SCAN_DIRS:
            if not dpath.exists():
                continue
            for fpath in sorted(dpath.rglob("*")):
                if not fpath.is_file():
                    continue
                text = fpath.read_text(encoding="utf-8", errors="replace")
                if RETIRED_MARKER in text:
                    offenders.append(str(fpath.relative_to(ROOT)))

        self.assertEqual(
            offenders,
            [],
            "The following files still reference '"
            + RETIRED_MARKER
            + "': "
            + ", ".join(offenders),
        )


class GatewayRuntimePathAbsenceTests(unittest.TestCase):
    """B3: The Dockerfile must not reference the gateway runtime path."""

    def test_dockerfile_has_no_gateway_runtime_path(self):
        if not DOCKERFILE.exists():
            self.fail(f"Dockerfile not found at {DOCKERFILE}")
        text = DOCKERFILE.read_text(encoding="utf-8")
        self.assertNotIn(
            GATEWAY_RUNTIME_PATH,
            text,
            "Dockerfile must not reference '"
            + GATEWAY_RUNTIME_PATH
            + "' (the gateway runtime path is retired)",
        )


if __name__ == "__main__":
    unittest.main()
