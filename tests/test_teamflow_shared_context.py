import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INITIALIZER = ROOT / "scripts" / "init-project.sh"
RUNTIME = ROOT / ".teamflow" / "bin" / "pi-runtime"
WRAPPER = ROOT / ".teamflow" / "bin" / "teamflow"


class SharedContextLayoutTests(unittest.TestCase):
    def test_shared_rules_use_pi_agent_dir_context_entry(self):
        self.assertTrue((ROOT / ".teamflow" / "AGENTS.md").is_file())
        self.assertFalse((ROOT / ".teamflow" / "instructions" / "AGENTS.md").exists())

    def test_initializer_installs_only_teamflow_agents_context_entry(self):
        source = INITIALIZER.read_text(encoding="utf-8")
        match = re.search(r"\nFILES=\(\n(.*?)\n\)", source, re.DOTALL)
        self.assertIsNotNone(match)
        files = match.group(1)
        self.assertIn('".teamflow/AGENTS.md"', files)
        self.assertNotIn('".teamflow/instructions/AGENTS.md"', files)


class PiContextAssemblyTests(unittest.TestCase):
    def test_runtime_keeps_pi_context_discovery_enabled_without_duplicate_append(self):
        source = RUNTIME.read_text(encoding="utf-8")
        self.assertNotIn("--no-context-files", source)
        self.assertNotIn("--append-system-prompt", source)

        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env.pop("PI_CODING_AGENT_DIR", None)
            completed = subprocess.run(
                [str(WRAPPER), "run", "--agent", "planner", "probe", "--print"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(Path(payload["env"]["PI_CODING_AGENT_DIR"]), ROOT / ".teamflow")
        self.assertNotIn("--no-context-files", payload["argv"])
        self.assertNotIn("--append-system-prompt", payload["argv"])


if __name__ == "__main__":
    unittest.main()
