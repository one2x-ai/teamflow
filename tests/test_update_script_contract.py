"""Contract tests for scripts/update.sh memory-skill references.

Pins a latent defect: update.sh referenced ``memory-{continue}`` in its brace
expansion, but no ``memory-continue`` skill directory exists under
``.teamflow/skills/``. The ``--apply`` self-check reads each
``memory-{...}/SKILL.md`` via ``node`` + ``fs.readFileSync``, so the missing
path would throw at apply time. The correct skill is ``memory-recall``
(``memory-{notes,capture,recall,curate}``).

Contracts:

1. scripts/update.sh never contains the substring ``memory-continue``.
2. scripts/update.sh references ``memory-recall`` through the literal brace
   expansion ``memory-{notes,capture,recall,curate}``.
3. Every skill name in every ``memory-{...}`` brace expansion in update.sh
   resolves to an existing directory under ``.teamflow/skills/``. This is the
   test that would have caught the original bug.
4. ``bash -n scripts/update.sh`` exits 0, so the syntax stays valid.
"""

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATE_SH = ROOT / "scripts" / "update.sh"
SKILLS_DIR = ROOT / ".teamflow" / "skills"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class UpdateScriptMemorySkillContractTests(unittest.TestCase):
    """Contracts 1-3: update.sh's memory-skill references are resolvable."""

    def setUp(self):
        self.text = read(UPDATE_SH)
        self.assertTrue(self.text, "scripts/update.sh must exist and be readable")

    def test_no_memory_continue_substring(self):
        """Contract 1: the nonexistent skill name must not appear at all."""
        self.assertNotIn(
            "memory-continue",
            self.text,
            "update.sh must not reference the nonexistent memory-continue skill",
        )

    def test_memory_recall_brace_expansion_present(self):
        """Contract 2: memory-recall appears via the literal brace expansion."""
        self.assertIn(
            "memory-{notes,capture,recall,curate}",
            self.text,
            "update.sh must reference the memory-recall skill through the "
            "memory-{notes,capture,recall,curate} brace expansion",
        )

    def test_brace_expansion_skills_resolve_to_directories(self):
        """Contract 3: every memory-{...} entry resolves to an existing skill dir.

        This is the test that would have caught the original bug: it parses the
        brace-expansion token from update.sh, splits on commas, and asserts each
        ``.teamflow/skills/memory-<name>`` is a real directory.
        """
        tokens = re.findall(r"memory-\{([^}]+)\}", self.text)
        self.assertTrue(
            tokens,
            "update.sh must contain at least one memory-{...} brace expansion",
        )
        for token in tokens:
            for name in token.split(","):
                name = name.strip()
                if not name:
                    continue
                with self.subTest(skill=name):
                    skill_dir = SKILLS_DIR / "memory-{}".format(name)
                    self.assertTrue(
                        skill_dir.is_dir(),
                        "skill directory {} referenced by "
                        "memory-{{{}}} must exist".format(skill_dir, token),
                    )


class UpdateScriptSyntaxTests(unittest.TestCase):
    """Contract 4: the script remains syntactically valid bash."""

    def test_bash_dash_n_passes(self):
        self.assertTrue(
            UPDATE_SH.is_file(),
            "scripts/update.sh must exist",
        )
        completed = subprocess.run(
            ["bash", "-n", str(UPDATE_SH)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            "bash -n scripts/update.sh failed: {}".format(completed.stderr),
        )


if __name__ == "__main__":
    unittest.main()
