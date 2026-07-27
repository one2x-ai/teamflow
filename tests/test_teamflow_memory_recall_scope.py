"""Corrected-contract requirement tests for `teamflow memory recall <query>`.

These tests SUPERSEDE the prior false-contract version of this same file, which
modeled `basic-memory tool search-notes --permalink <value>` as a value-taking
namespace filter. Against REAL Basic Memory 0.22.1 that contract is FALSE:

- `--permalink` is a BOOLEAN search-mode flag. Passing a value after it fails
  with "unexpected extra argument", and it cannot combine a text FTS query with
  permalink filtering. The wrapper MUST NEVER send `--permalink`.
- The only shape the wrapper may use for default recall is unscoped text search:
  `basic-memory tool search-notes <query> --page <n> --page-size <n>
  --project <p> --local`.
- Default scope therefore pages through unscoped text search (page 1 onward,
  page-size 8, until `has_more` is false / results empty), then FILTERS results
  LOCALLY -- keeping only permalinks under `${PROJECT_NAME}/projects/${SLUG}/`
  or `${PROJECT_NAME}/global/` -- dedupes by exact permalink preserving upstream
  ranking order, and emits `{"results": [...], "total": <len>}`.

Invariants: each test uses its own `tempfile.TemporaryDirectory`; the isolated
env strips `TEAMFLOW_*`/`WORKFLOW_*`/`OPENCODE_WORKFLOW_*`/`BASIC_MEMORY_PROJECT`;
a fake `basic-memory` is placed first on PATH (real `git` still reachable) and
(a) FAILS (exit 1) if `--permalink` appears anywhere in argv, (b) serves a
sliced, ranked canned list for `tool search-notes`; every recall is driven with
`subprocess.run([MEMORY_SCRIPT, "recall", query], cwd=<repo>, env=env,
capture_output=True, text=True, timeout=30)`. Stdlib only; no network, no real
basic-memory.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MEMORY_SCRIPT = ROOT / ".teamflow" / "bin" / "memory"
INIT_PROJECT = ROOT / "scripts" / "init-project.sh"


# The fake enforces the corrected real-CLI contract:
#  * --permalink anywhere in argv => stderr + exit 1 (boolean flag, never sent).
#  * tool search-notes WITHOUT --permalink => slice FAKE_BASIC_MEMORY_ALL_RESULTS
#    by (--page, --page-size) and emit the real response shape with has_more.
FAKE_BASIC_MEMORY = """#!/bin/sh
LOG="${FAKE_BASIC_MEMORY_LOG:?}"
printf '%s\\n' "$*" >> "$LOG"

# --permalink is a boolean flag in real basic-memory 0.22.1; the wrapper must
# never send it. Fail loudly so a regression is observable.
for arg in "$@"; do
  if [ "$arg" = "--permalink" ]; then
    printf 'fake: --permalink is a boolean flag, not a value filter\\n' >&2
    exit 1
  fi
done

if [ "${1:-} ${2:-}" = "tool search-notes" ]; then
  PAGE=1
  PAGE_SIZE=8
  PENDING=""
  for arg in "$@"; do
    if [ -n "$PENDING" ]; then
      case "$PENDING" in
        page) PAGE="$arg" ;;
        page_size) PAGE_SIZE="$arg" ;;
      esac
      PENDING=""
      continue
    fi
    case "$arg" in
      --page) PENDING="page" ;;
      --page-size) PENDING="page_size" ;;
    esac
  done
  PAGE="$PAGE" PAGE_SIZE="$PAGE_SIZE" python3 - <<'PY'
import json
import os
import sys

all_results = json.loads(os.environ["FAKE_BASIC_MEMORY_ALL_RESULTS"])
try:
    page = int(os.environ["PAGE"])
except (TypeError, ValueError):
    page = 1
try:
    page_size = int(os.environ["PAGE_SIZE"])
except (TypeError, ValueError):
    page_size = 8
start = (page - 1) * page_size
end = start + page_size
results = all_results[start:end] if start >= 0 else []
sys.stdout.write(json.dumps({
    "results": results,
    "current_page": page,
    "page_size": page_size,
    "total": len(all_results),
    "has_more": end < len(all_results),
}))
PY
  exit 0
fi
exit 0
"""


def write_fake_basic_memory(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir.parent / "basic-memory.log"
    tool = bin_dir / "basic-memory"
    tool.write_text(FAKE_BASIC_MEMORY, encoding="utf-8")
    tool.chmod(0o755)
    return log


def isolated_environment(
    home: Path, bin_dir: Path, log: Path, all_results: list, **extra: str
) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("TEAMFLOW_", "WORKFLOW_", "OPENCODE_WORKFLOW_")):
            env.pop(key)
    env.pop("BASIC_MEMORY_PROJECT", None)
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "FAKE_BASIC_MEMORY_LOG": str(log),
            "FAKE_BASIC_MEMORY_ALL_RESULTS": json.dumps(all_results),
        }
    )
    env.update(extra)
    return env


def make_repo(directory: Path, remote: str | None = None) -> None:
    subprocess.run(
        ["git", "init", "-q"], cwd=directory, check=True,
        capture_output=True, text=True,
    )
    if remote is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", remote], cwd=directory, check=True,
            capture_output=True, text=True,
        )


def run_recall(
    env: dict[str, str], cwd: Path, query: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MEMORY_SCRIPT), "recall", query],
        cwd=cwd, env=env, text=True, capture_output=True, timeout=30,
    )


def search_notes_lines(log_text: str) -> list[str]:
    return [ln for ln in log_text.splitlines() if ln.startswith("tool search-notes")]


def _tokens(line: str) -> list[str]:
    return line.split()


def _flag_value(tokens: list[str], flag: str) -> str | None:
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def _positional_query(line: str) -> str:
    # Tokens: ["tool", "search-notes", <positional...>, "--flag", ...]. The
    # positional query is everything after "search-notes" up to the first flag.
    parts = []
    for token in _tokens(line)[2:]:
        if token.startswith("--"):
            break
        parts.append(token)
    return " ".join(parts)


def _result(permalink: str) -> dict:
    return {"permalink": permalink, "title": permalink.split("/")[-1]}


class TeamflowMemoryRecallScopeTests(unittest.TestCase):
    def _fixture(
        self,
        *,
        remote: str | None = "https://example.com/mcap.git",
        repo_dir: str = "repo",
        permalinks: list[str] | None = None,
        **extra: str,
    ):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        home = root / "home"
        bin_dir = root / "bin"
        repo = root / repo_dir
        home.mkdir()
        bin_dir.mkdir()
        repo.mkdir()
        make_repo(repo, remote=remote)
        log = write_fake_basic_memory(bin_dir)
        all_results = [_result(p) for p in (permalinks or [])]
        env = isolated_environment(home, bin_dir, log, all_results, **extra)
        return env, repo, log

    # 1. Default uses unscoped text search, no --permalink, correct argv shape.
    def test_default_recall_uses_unscoped_text_search_without_permalink(self):
        env, repo, log = self._fixture(
            permalinks=[
                "teamflow/projects/mcap/curated/finding-a",
                "teamflow/projects/teamflow/curated/noise",
                "teamflow/global/curated/finding-c",
            ],
        )
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        calls = log.read_text(encoding="utf-8")
        self.assertNotIn("--permalink", calls)
        lines = search_notes_lines(calls)
        self.assertTrue(lines, f"no search-notes calls logged: {calls!r}")
        for line in lines:
            tokens = _tokens(line)
            self.assertEqual(_positional_query(line), "auth", line)
            self.assertIsNotNone(_flag_value(tokens, "--page"), line)
            self.assertEqual(_flag_value(tokens, "--page-size"), "8", line)
            self.assertEqual(_flag_value(tokens, "--project"), "teamflow", line)
            self.assertIn("--local", tokens, line)
            self.assertNotIn("--permalink", tokens, line)

    # 2. Leakage exclusion: other-repository permalinks are dropped locally.
    def test_default_recall_excludes_other_repository_permalinks(self):
        env, repo, log = self._fixture(
            permalinks=[
                "teamflow/projects/mcap/curated/allowed",
                "teamflow/projects/teamflow/curated/leak",
                "teamflow/global/curated/global-hit",
            ],
        )
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        for item in data.get("results", []):
            permalink = item.get("permalink", "")
            self.assertFalse(
                permalink.startswith("teamflow/projects/teamflow/"),
                f"leaked cross-repository permalink: {permalink!r}",
            )

    # 3. Global inclusion: permalinks under <PROJECT>/global/ are kept.
    def test_default_recall_includes_global_permalinks(self):
        env, repo, log = self._fixture(
            permalinks=[
                "teamflow/projects/mcap/curated/allowed",
                "teamflow/global/curated/global-hit",
            ],
        )
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        permalinks = [item["permalink"] for item in data["results"]]
        self.assertIn("teamflow/global/curated/global-hit", permalinks)

    # 4. Upstream ranking preserved + exact-permalink dedupe (first wins).
    def test_default_recall_preserves_upstream_ranking_and_dedupes_permalink(self):
        # Upstream rank order: a, g1, noise(dropped), a(duplicate, dropped), b.
        env, repo, log = self._fixture(
            permalinks=[
                "teamflow/projects/mcap/curated/a",
                "teamflow/global/curated/g1",
                "teamflow/projects/teamflow/curated/noise",
                "teamflow/projects/mcap/curated/a",
                "teamflow/projects/mcap/curated/b",
            ],
        )
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertEqual(
            [item["permalink"] for item in data["results"]],
            [
                "teamflow/projects/mcap/curated/a",
                "teamflow/global/curated/g1",
                "teamflow/projects/mcap/curated/b",
            ],
        )

    # 5. Multiword query is passed through as a single positional argument.
    def test_default_recall_passes_multiword_query_verbatim(self):
        env, repo, log = self._fixture(
            permalinks=["teamflow/projects/mcap/curated/a"],
        )
        completed = run_recall(env, repo, "alpha beta")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = search_notes_lines(log.read_text(encoding="utf-8"))
        self.assertTrue(lines, "no search-notes calls logged")
        self.assertTrue(
            any(_positional_query(line) == "alpha beta" for line in lines),
            f"multiword query not passed verbatim: {lines!r}",
        )

    # 6. No-remote slug fallback: slug derives from the cwd basename.
    def test_default_recall_no_remote_slug_falls_back_to_dir_basename(self):
        env, repo, log = self._fixture(
            remote=None,
            repo_dir="fallback-repo",
            permalinks=[
                "teamflow/projects/fallback-repo/curated/a",
                "teamflow/projects/other/curated/b",
                "teamflow/global/curated/g1",
            ],
        )
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        permalinks = [item["permalink"] for item in data["results"]]
        self.assertIn("teamflow/projects/fallback-repo/curated/a", permalinks)
        self.assertIn("teamflow/global/curated/g1", permalinks)
        self.assertNotIn("teamflow/projects/other/curated/b", permalinks)
        for permalink in permalinks:
            self.assertFalse(permalink.startswith("teamflow/projects/other/"))

    # 7. Dynamic project name in the local-filter prefix (no hardcoded value).
    def test_default_recall_uses_dynamic_project_name_in_filter_prefix(self):
        env, repo, log = self._fixture(
            permalinks=[
                "myproj/projects/mcap/curated/a",
                "myproj/projects/teamflow/curated/b",
                "myproj/global/curated/g1",
            ],
            TEAMFLOW_MEMORY_PROJECT="myproj",
        )
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        permalinks = [item["permalink"] for item in data["results"]]
        self.assertEqual(
            permalinks,
            [
                "myproj/projects/mcap/curated/a",
                "myproj/global/curated/g1",
            ],
        )
        for permalink in permalinks:
            self.assertFalse(permalink.startswith("myproj/projects/teamflow/"))

    # 8. Pagination / exhaustive scan: late allowed hits on page >= 2 are found.
    # NOTE: this test assumes the wrapper fetches with --page-size 8.
    def test_default_recall_paginates_exhaustively_to_reach_late_allowed_hits(self):
        # 12 items at --page-size 8 span exactly pages 1 and 2. The first 8
        # ranks are teamflow-repository noise; the allowed (mcap/global) hits
        # live ONLY on page 2, so a wrapper that stops at page 1 misses them.
        noise = [
            f"teamflow/projects/teamflow/curated/noise-{i}" for i in range(8)
        ]
        late = [
            "teamflow/projects/mcap/curated/late-1",
            "teamflow/global/curated/late-2",
            "teamflow/projects/mcap/curated/late-3",
            "teamflow/global/curated/late-4",
        ]
        env, repo, log = self._fixture(permalinks=noise + late)
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        lines = search_notes_lines(log.read_text(encoding="utf-8"))
        pages = set()
        for line in lines:
            value = _flag_value(_tokens(line), "--page")
            if value is not None:
                pages.add(int(value))
        # 12 items / page-size 8 => the last content page is 2; the wrapper must
        # stop after page 2 (its response has_more == false), neither stopping at
        # page 1 nor over-fetching page 3.
        self.assertEqual(pages, {1, 2}, f"unexpected page set: {pages!r}")

        data = json.loads(completed.stdout)
        permalinks = [item["permalink"] for item in data["results"]]
        for hit in late:
            self.assertIn(hit, permalinks, f"late allowed hit missing: {hit!r}")

    # 9. total == len(results) over a known injected list.
    def test_default_recall_total_equals_filtered_result_count(self):
        env, repo, log = self._fixture(
            permalinks=[
                "teamflow/projects/mcap/curated/a",
                "teamflow/projects/teamflow/curated/noise",
                "teamflow/global/curated/g1",
                "teamflow/projects/other/curated/b",
            ],
        )
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        data = json.loads(completed.stdout)
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["total"], len(data["results"]))

    # 10. all-scope opt-in: exactly one unscoped call, raw passthrough.
    def test_all_scope_opt_in_is_single_unscoped_raw_passthrough(self):
        all_permalinks = [
            f"teamflow/projects/teamflow/curated/noise-{i}" for i in range(8)
        ] + ["teamflow/projects/mcap/curated/late-1"] * 4
        env, repo, log = self._fixture(
            permalinks=all_permalinks,
            TEAMFLOW_MEMORY_RECALL_SCOPE="all",
        )
        completed = run_recall(env, repo, "auth")
        self.assertEqual(completed.returncode, 0, completed.stderr)

        calls = log.read_text(encoding="utf-8")
        self.assertNotIn("--permalink", calls)
        lines = search_notes_lines(calls)
        self.assertEqual(len(lines), 1, f"opt-in all must be one call: {lines!r}")
        tokens = _tokens(lines[0])
        self.assertIsNone(_flag_value(tokens, "--page"), lines[0])
        self.assertNotIn("--permalink", tokens, lines[0])

        data = json.loads(completed.stdout)
        # Raw passthrough: the single page-size-8 slice, total == full length.
        self.assertEqual(data["total"], len(all_permalinks))
        self.assertEqual(len(data["results"]), 8)
        self.assertEqual(
            [item["permalink"] for item in data["results"]],
            all_permalinks[:8],
        )

    # 11. init-project parity: managed entry + local-filter markers present.
    def test_init_project_parity_keeps_memory_entry_and_filter_markers(self):
        init_source = INIT_PROJECT.read_text(encoding="utf-8")
        self.assertIn('".teamflow/bin/memory"', init_source)

        memory_source = MEMORY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("recall)", memory_source)
        start = memory_source.index("recall)")
        branch = memory_source[start:]
        end = branch.index(";;")
        recall_branch = branch[:end]
        self.assertIn("projects/", recall_branch)
        self.assertIn("global/", recall_branch)


if __name__ == "__main__":
    unittest.main()
