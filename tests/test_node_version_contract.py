"""Node version documentation contract test (P1-1).

``scripts/bootstrap.sh`` is the authoritative Node gate: it assigns
``MIN_NODE_VERSION="22.19.0"``.  ``README.md`` and ``scripts/doctor.sh``
must not advertise a stale "Node.js 20+" requirement that contradicts the
bootstrap gate.  Both must reference a version >= the bootstrap gate, and
``doctor.sh`` must compare against the bootstrap major rather than a
hard-coded ``20``.

This test is RED against the current docs (which still say "Node.js 20+")
and turns GREEN once the docs are updated to reference 22.19.

Pinned tokens
-------------
* Stale token rejected: ``Node.js 20`` (must NOT appear in README or
  doctor.sh).
* Required token: the bootstrap ``major.minor`` (e.g. ``22.19``) must
  appear in both README and doctor.sh.
* doctor.sh ``NODE_MAJOR >= <major>`` must use the bootstrap major.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"
DOCTOR = ROOT / "scripts" / "doctor.sh"
README = ROOT / "README.md"

#: The stale token that contradicts the bootstrap gate.
STALE_TOKEN = "Node.js 20"


def _min_node_version():
    """Derive MIN_NODE_VERSION (e.g. '22.19.0') from scripts/bootstrap.sh."""
    text = BOOTSTRAP.read_text(encoding="utf-8")
    m = re.search(r'MIN_NODE_VERSION="(\d+\.\d+\.\d+)"', text)
    if not m:
        raise AssertionError(
            "scripts/bootstrap.sh must assign MIN_NODE_VERSION=\"x.y.z\""
        )
    return m.group(1)


def _major_minor(version):
    """Return the 'major.minor' substring (e.g. '22.19' for '22.19.0')."""
    return ".".join(version.split(".")[:2])


def _major(version):
    return int(version.split(".")[0])


class NodeVersionContractTests(unittest.TestCase):
    """P1-1: README and doctor.sh must match the bootstrap Node gate."""

    def setUp(self):
        self.min_version = _min_node_version()
        self.major_minor = _major_minor(self.min_version)
        self.major = _major(self.min_version)
        self.readme = README.read_text(encoding="utf-8")
        self.doctor = DOCTOR.read_text(encoding="utf-8")

    def test_readme_does_not_advertise_stale_node_20(self):
        self.assertNotIn(
            STALE_TOKEN,
            self.readme,
            "README.md must not advertise the stale 'Node.js 20+' "
            "requirement; bootstrap gates at " + self.min_version,
        )

    def test_doctor_does_not_advertise_stale_node_20(self):
        self.assertNotIn(
            STALE_TOKEN,
            self.doctor,
            "scripts/doctor.sh must not advertise the stale 'Node.js 20+' "
            "requirement; bootstrap gates at " + self.min_version,
        )

    def test_readme_references_bootstrap_node_version(self):
        self.assertIn(
            self.major_minor,
            self.readme,
            "README.md must reference the bootstrap Node gate ("
            + self.major_minor + "+)",
        )

    def test_doctor_references_bootstrap_node_version(self):
        self.assertIn(
            self.major_minor,
            self.doctor,
            "scripts/doctor.sh must reference the bootstrap Node gate ("
            + self.major_minor + "+)",
        )

    def test_doctor_major_check_matches_bootstrap(self):
        """doctor.sh must check NODE_MAJOR >= the bootstrap major, not 20."""
        m = re.search(r"NODE_MAJOR\s*>=\s*(\d+)", self.doctor)
        if not m:
            self.fail(
                "scripts/doctor.sh must check NODE_MAJOR >= "
                + str(self.major)
            )
        self.assertGreaterEqual(
            int(m.group(1)),
            self.major,
            "scripts/doctor.sh NODE_MAJOR gate (" + m.group(1)
            + ") must be >= bootstrap major (" + str(self.major) + ")",
        )


if __name__ == "__main__":
    unittest.main()
