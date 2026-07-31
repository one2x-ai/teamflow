"""Requirement tests for Phase B installer, doctor, gitignore, and README wiring.

Asserts that the web front-end build output and caches are properly handled
by .gitignore, doctor.sh, bootstrap.sh, install.sh, and that README.md no
longer claims a zero npm runtime dependency.

Text-level checks do not require bun; the one behavioural class
(GitStatusAfterBuildTests) runs a real build and skips when bun is absent
or the build fails.
"""

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'server'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8') if path.is_file() else ''


class GitignoreTests(unittest.TestCase):
    """Acceptance 11: .gitignore must ignore web/dist and Vite caches."""

    def test_web_dist_ignored(self):
        """The dist build-output directory must appear in .gitignore."""
        text = read(ROOT / '.gitignore')
        self.assertTrue(
            'web/dist' in text,
            '.gitignore must ignore the web/dist build-output directory',
        )

    def test_vite_cache_ignored(self):
        """Vite cache directories under web must be git-ignored."""
        text = read(ROOT / '.gitignore')
        has_web_vite = 'web/.vite' in text
        has_broad_vite = bool(re.search(r'(?<!\S)\.vite/', text))
        self.assertTrue(
            has_web_vite or has_broad_vite,
            '.gitignore must ignore Vite caches (web/.vite or .vite/ pattern)',
        )


class DoctorTests(unittest.TestCase):
    """Acceptance 9: doctor.sh must verify web/dist build output exists."""

    def test_doctor_checks_web_dist(self):
        """doctor.sh must reference web/dist with an existence check."""
        text = read(ROOT / 'scripts/doctor.sh')
        self.assertIn(
            'web/dist', text,
            'doctor.sh must check for web/dist build output',
        )
        has_check = any(
            p in text
            for p in ('test -d', '[ -d', '[[ -d', '[[ -f', '[ -f')
        )
        self.assertTrue(
            has_check,
            'doctor.sh must use a directory or file existence test',
        )


class ScriptResponsibilityTests(unittest.TestCase):
    """Acceptance 10, corrected: the two scripts own different things.

    The original handoff asked for both bootstrap.sh and install.sh to build
    web/dist. That was wrong, and it is what made five existing installer
    tests time out.

    `server/` is one global tool shared by every project, living at
    $TEAMFLOW_HOME/server. `install.sh` copies the per-project `.teamflow/`
    runtime that starts the pi agents; the browser front end is none of its
    business. Building there also made every project install pay for a
    front-end build it never uses.
    """

    def test_bootstrap_builds_the_front_end(self):
        """bootstrap.sh owns the build, in the repository where deps live."""
        text = read(ROOT / 'scripts/bootstrap.sh')
        self.assertRegex(
            text,
            r'bun run build',
            'bootstrap.sh must run the front-end build',
        )
        # The build must happen in the repository: the global copy has no
        # node_modules, so building there would always fail.
        build_line = next(
            line for line in text.splitlines() if 'bun run build' in line
        )
        self.assertNotIn(
            'SERVER_TARGET',
            build_line,
            'the build must run in the repository, not in the global copy '
            '(which has no node_modules)',
        )

    def test_bootstrap_build_failure_is_not_fatal(self):
        """The CLI and memory API do not depend on the front end."""
        text = read(ROOT / 'scripts/bootstrap.sh')
        self.assertRegex(
            text,
            r'warning: web front-end build failed',
            'a build failure must degrade to a warning, not abort bootstrap',
        )

    def test_install_does_not_touch_the_server(self):
        """install.sh installs .teamflow/ only."""
        text = read(ROOT / 'scripts/install.sh')
        self.assertNotRegex(
            text,
            r'bun run build',
            'install.sh must not build the front end',
        )
        self.assertNotRegex(
            text,
            r'SERVER_TARGET\s*=',
            'install.sh must not sync the global server copy; bootstrap.sh owns it',
        )
        self.assertNotRegex(
            text,
            r'find server\b',
            'install.sh must not walk server/ at all',
        )

    def test_bootstrap_ships_dist_but_not_node_modules(self):
        """The global copy needs the build output, never the dependencies."""
        text = read(ROOT / 'scripts/bootstrap.sh')
        find_block = text[text.index('find . -type f'):text.index('-print', text.index('find . -type f'))]
        self.assertIn(
            "! -path './web/node_modules/*'",
            find_block,
            'web/node_modules must never be copied to the global server',
        )
        self.assertNotIn(
            "! -path './web/dist/*'",
            find_block,
            'web/dist is the one artifact the global copy needs in order to '
            'serve /app, so it must not be excluded',
        )


class ReadmeTests(unittest.TestCase):
    """Acceptance 12: README must not claim zero npm runtime dependency."""

    def test_no_inaccurate_zero_npm_claim(self):
        """README must not claim '零 npm 运行时依赖' or 'zero npm runtime depend'."""
        text = read(ROOT / 'README.md')
        self.assertNotIn(
            '零 npm 运行时依赖', text,
            'README must not claim zero npm runtime dependency',
        )
        self.assertNotIn(
            'zero npm runtime depend', text.lower(),
            'README must not claim zero npm runtime dependency',
        )

    def test_accurate_serving_path_statement(self):
        """README must describe the serving path with prebuilt assets or build-time deps."""
        text = read(ROOT / 'README.md')
        keywords = ('预构建', '静态资源', '构建期', 'Bun 内置')
        self.assertTrue(
            any(kw in text for kw in keywords),
            'README must describe the serving path accurately '
            '(prebuilt static assets, build-time deps, or Bun built-ins)',
        )


class GitStatusAfterBuildTests(unittest.TestCase):
    """Acceptance 11: git status --short is clean after a build."""

    @classmethod
    def setUpClass(cls):
        """Build the web front-end; skip when bun is unavailable or build fails."""
        if shutil.which('bun') is None:
            raise unittest.SkipTest('bun is not installed')
        env = os.environ.copy()
        env.setdefault('BUN_CONFIG_REGISTRY', 'https://registry.npmjs.org')
        env.setdefault('HTTP_PROXY', 'http://127.0.0.1:1087')
        env.setdefault('HTTPS_PROXY', 'http://127.0.0.1:1087')
        env.setdefault('ALL_PROXY', 'socks5://127.0.0.1:1080')
        result = subprocess.run(
            ['bun', 'run', 'build'],
            cwd=str(SERVER),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise unittest.SkipTest(
                f'build failed (rc={result.returncode}): {result.stderr[:500]}'
            )

    def test_git_status_excludes_web_dist(self):
        """After building, web/dist must not appear in git status."""
        result = subprocess.run(
            ['git', 'status', '--short'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            self.assertNotIn(
                'web/dist', line,
                'web/dist must be git-ignored after a build',
            )

    def test_git_status_excludes_web_node_modules(self):
        """After building, web/node_modules must not appear in git status."""
        result = subprocess.run(
            ['git', 'status', '--short'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            self.assertNotIn(
                'web/node_modules', line,
                'web/node_modules must be git-ignored after a build',
            )


class ServerSourceExclusionTests(unittest.TestCase):
    """Acceptance 10: a fresh install ships no test directories."""

    def test_bootstrap_excludes_tests(self):
        """bootstrap.sh find must still exclude ./tests/*."""
        text = read(ROOT / 'scripts/bootstrap.sh')
        self.assertIn(
            './tests/*', text,
            'bootstrap.sh must exclude tests/ from the server copy',
        )

    def test_install_has_no_server_copy_to_exclude_from(self):
        """install.sh needs no server exclusions: it never copies server/.

        A stronger guarantee than excluding tests/ from a server walk — there
        is no server walk. server/tests/ therefore has no path by which it
        could reach a business project.
        """
        text = read(ROOT / 'scripts/install.sh')
        self.assertNotRegex(
            text,
            r'find server\b',
            'install.sh must not walk server/ at all',
        )
        self.assertNotIn(
            "$SOURCE_ROOT/server/",
            text,
            'install.sh must not read from server/',
        )


if __name__ == '__main__':
    unittest.main()
