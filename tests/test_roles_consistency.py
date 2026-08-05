"""Role-set documentation consistency tests (P1-2, P1-3).

The canonical agent roster lives on disk under ``.teamflow/agents/*.md``.
The documentation surfaces — root ``AGENTS.md``, ``.teamflow/AGENTS.md``,
and the ``README.md`` model table — must all mention ``memory-indexer``
(P1-2) and must keep their backticked role-token sets consistent with each
other and with the on-disk roster (P1-3).

Pinned tokens / contract
------------------------
* P1-2: the substring ``memory-indexer`` must appear in README.md, root
  ``AGENTS.md``, and ``.teamflow/AGENTS.md``.
* P1-3: root ``AGENTS.md`` must mention ``supervisor``,
  ``emotional-salience-sensor``, ``title-compressor``, and
  ``memory-indexer`` as backticked role tokens.
* P1-3: the set of backticked role tokens (intersected with the on-disk
  roster) in root ``AGENTS.md`` must equal the set in
  ``.teamflow/AGENTS.md``, and both must equal the full on-disk roster.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / ".teamflow" / "agents"
ROOT_AGENTS_MD = ROOT / "AGENTS.md"
SHARED_AGENTS_MD = ROOT / ".teamflow" / "AGENTS.md"
README = ROOT / "README.md"

#: Roles explicitly missing from root AGENTS.md today (P1-3 focus set).
P1_3_FOCUS_ROLES = (
    "supervisor",
    "emotional-salience-sensor",
    "title-compressor",
    "memory-indexer",
)


def read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"required file not found: {path}")
    return path.read_text(encoding="utf-8")


def canonical_agents():
    """Return the set of agent names defined under .teamflow/agents/."""
    return {p.stem for p in AGENTS_DIR.glob("*.md")}


def backtick_tokens(text):
    """Return the set of `` `token` `` backticked tokens in *text*."""
    return set(re.findall(r"`([\w-]+)`", text))


def role_tokens(text, roster):
    """Backticked tokens that name a real on-disk agent."""
    return backtick_tokens(text) & roster


class MemoryIndexerDocumentationTests(unittest.TestCase):
    """P1-2: memory-indexer must appear in every role documentation surface."""

    def test_memory_indexer_in_readme(self):
        self.assertIn(
            "memory-indexer",
            read(README),
            "README.md model table must list the memory-indexer agent",
        )

    def test_memory_indexer_in_root_agents_md(self):
        self.assertIn(
            "memory-indexer",
            read(ROOT_AGENTS_MD),
            "root AGENTS.md Roles section must mention memory-indexer",
        )

    def test_memory_indexer_in_shared_agents_md(self):
        self.assertIn(
            "memory-indexer",
            read(SHARED_AGENTS_MD),
            ".teamflow/AGENTS.md Roles section must mention memory-indexer",
        )


class RootAgentsMdFocusRolesTests(unittest.TestCase):
    """P1-3: root AGENTS.md must mention the four previously-missing roles."""

    def test_focus_roles_present_as_backticked_tokens(self):
        text = read(ROOT_AGENTS_MD)
        tokens = backtick_tokens(text)
        for role in P1_3_FOCUS_ROLES:
            with self.subTest(role=role):
                self.assertIn(
                    role,
                    tokens,
                    f"root AGENTS.md must mention `{role}` as a backticked "
                    "role token",
                )


class RoleSetConsistencyTests(unittest.TestCase):
    """P1-3: both AGENTS.md files cover the same, complete on-disk roster."""

    def setUp(self):
        self.roster = canonical_agents()
        self.root_roles = role_tokens(read(ROOT_AGENTS_MD), self.roster)
        self.shared_roles = role_tokens(read(SHARED_AGENTS_MD), self.roster)

    def test_root_agents_md_covers_full_roster(self):
        missing = self.roster - self.root_roles
        self.assertFalse(
            missing,
            "root AGENTS.md must mention every on-disk agent; missing: "
            + ", ".join(sorted(missing)),
        )

    def test_shared_agents_md_covers_full_roster(self):
        missing = self.roster - self.shared_roles
        self.assertFalse(
            missing,
            ".teamflow/AGENTS.md must mention every on-disk agent; "
            "missing: " + ", ".join(sorted(missing)),
        )

    def test_root_and_shared_role_sets_are_equal(self):
        self.assertEqual(
            self.root_roles,
            self.shared_roles,
            "root AGENTS.md and .teamflow/AGENTS.md must document the "
            "same role set; root-only: "
            + ", ".join(sorted(self.root_roles - self.shared_roles))
            + "; shared-only: "
            + ", ".join(sorted(self.shared_roles - self.root_roles)),
        )


if __name__ == "__main__":
    unittest.main()
