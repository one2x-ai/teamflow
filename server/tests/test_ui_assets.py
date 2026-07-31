"""Requirement tests for the split memory-browser front end.

The two pages used to live as inline template literals inside server.ts,
which meant no editor support and a 638-line module mixing routing with
markup. The front end is now split into per-page assets:

    server/src/ui/list.{html,css,js}
    server/src/ui/detail.{html,css,js}

server.ts assembles a page from its assets at request time. There is no
bundler in the serving path: `teamflow server` runs the TypeScript source
directly under Bun, so a fresh clone and a global install both work with no
build step. Vite is available for local front-end iteration only, and its
output is never what the server serves.

The security contract is unchanged and still asserted here at the source
level: memory data reaches the DOM through textContent/setAttribute, never
innerHTML, and no page offers a write control.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
SRC = SERVER / "src"
UI = SRC / "ui"
SERVER_TS = SRC / "server.ts"
# Phase A moved page assembly out of the entrypoint into its own module.
PAGES_TS = SRC / "pages.ts"

PAGES = ("list", "detail")
ASSET_SUFFIXES = ("html", "css", "js")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


class UiAssetLayoutTests(unittest.TestCase):
    """Each page owns an html, css, and js file."""

    def test_ui_directory_exists(self):
        self.assertTrue(UI.is_dir(), "server/src/ui/ must exist")

    def test_every_page_has_all_assets(self):
        for page in PAGES:
            for suffix in ASSET_SUFFIXES:
                path = UI / f"{page}.{suffix}"
                with self.subTest(asset=path.name):
                    self.assertTrue(path.is_file(), f"{path} must exist")
                    self.assertTrue(
                        read(path).strip(), f"{path} must not be empty"
                    )

    def test_html_assets_are_document_fragments(self):
        """Structure lives in .html; server.ts supplies the document shell."""
        for page in PAGES:
            text = read(UI / f"{page}.html")
            with self.subTest(page=page):
                self.assertNotIn(
                    "<!DOCTYPE",
                    text,
                    f"{page}.html holds body structure; the doctype and head "
                    "belong to the assembler",
                )
                self.assertNotIn("<style", text, "styles belong in the .css file")
                self.assertNotIn("<script", text, "behavior belongs in the .js file")

    def test_css_assets_carry_no_markup(self):
        for page in PAGES:
            text = read(UI / f"{page}.css")
            with self.subTest(page=page):
                self.assertNotIn("<style", text)
                self.assertNotIn("<!DOCTYPE", text)

    def test_js_assets_carry_no_markup(self):
        for page in PAGES:
            text = read(UI / f"{page}.js")
            with self.subTest(page=page):
                self.assertNotIn("<script", text)
                self.assertNotIn("<!DOCTYPE", text)


class ServerNoLongerInlinesMarkupTests(unittest.TestCase):
    """The assembler wraps assets; it does not embed page bodies.

    Phase A extracted page assembly from server.ts into pages.ts, so the
    shell assertions target that module. server.ts is checked separately for
    staying assembly-only.
    """

    def setUp(self):
        self.text = read(PAGES_TS)

    def test_server_has_no_inline_style_or_script_blocks(self):
        """The shell may open <style>/<script>; the content must be injected.

        server.ts still writes the tags, because it assembles the document.
        What it must not do is hold the rules or statements: each tag has to
        be immediately followed by an interpolated asset.
        """
        for tag, slot in (("<style>", "${css}"), ("<script>", "${js}")):
            with self.subTest(tag=tag):
                index = self.text.find(tag)
                self.assertNotEqual(
                    index, -1, f"the document shell should still emit {tag}"
                )
                following = self.text[index + len(tag) : index + len(tag) + 12]
                self.assertIn(
                    slot,
                    following,
                    f"{tag} must be followed by {slot}, not inline content",
                )

    def test_server_holds_no_css_rules_or_dom_calls(self):
        """Page behavior and styling belong to the assets, not the module."""
        for marker in (
            "box-sizing",
            "color-scheme",
            "addEventListener",
            "querySelector",
            "createElement",
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(
                    marker,
                    self.text,
                    f"{marker} indicates front-end code left in server.ts",
                )

    def test_server_reads_ui_assets(self):
        self.assertRegex(
            self.text,
            r"ui/|UI_DIR|readAsset|loadAsset",
            "the assembler must load page assets from server/src/ui/",
        )

    def test_entrypoint_delegates_page_rendering(self):
        """server.ts must route to the assembler, not render inline."""
        entry = read(SERVER_TS)
        self.assertRegex(
            entry,
            r'from "\./pages"',
            "server.ts must import the page assembler",
        )
        self.assertNotIn(
            "<!DOCTYPE",
            entry,
            "the entrypoint must not build documents itself",
        )

    def test_server_module_is_smaller(self):
        """Extracting markup and modules should show up as a real shrink."""
        line_count = len(read(SERVER_TS).splitlines())
        self.assertLess(
            line_count,
            400,
            f"server.ts should stay routing-sized after the split, got "
            f"{line_count} lines",
        )


class NoBuildStepInServingPathTests(unittest.TestCase):
    """Running the server never requires a bundler."""

    def test_launcher_executes_typescript_source(self):
        wrapper = read(ROOT / ".teamflow" / "bin" / "server")
        self.assertIn("src/server.ts", wrapper)
        for token in ("vite", "dist/", "npm run build", "bun run build"):
            with self.subTest(token=token):
                self.assertNotIn(
                    token,
                    wrapper,
                    "the launcher must run source directly, with no build step",
                )

    def test_server_does_not_read_build_output(self):
        for module in (SERVER_TS, PAGES_TS):
            with self.subTest(module=module.name):
                self.assertNotIn(
                    "dist/",
                    read(module),
                    f"{module.name} must serve source assets, not bundler output",
                )

    def test_vite_output_is_not_committed(self):
        gitignore = read(ROOT / ".gitignore")
        self.assertRegex(
            gitignore,
            r"(?m)^server/dist/?\s*$",
            "Vite output is local iteration scratch and must be git-ignored",
        )

    def test_vite_is_a_dev_dependency_only(self):
        package = read(SERVER / "package.json")
        if "vite" not in package:
            self.skipTest("Vite is optional; no front-end tooling configured")
        dependencies_block = re.search(
            r'"dependencies"\s*:\s*\{(.*?)\}', package, re.S
        )
        if dependencies_block:
            self.assertNotIn(
                "vite",
                dependencies_block.group(1),
                "Vite must be a devDependency: the served path has no runtime "
                "npm dependency",
            )

    def test_build_script_only_builds_the_web_frontend(self):
        """A build script may exist, but only for web/dist prebuilt assets.

        Phase B added a `build` script that produces server/web/dist for the
        static SPA at /app. The serving path stays build-free at request
        time (static.ts reads files with Bun.file()), so the script must
        never bundle the server source itself.
        """
        package = read(SERVER / "package.json")
        scripts = re.search(r'"scripts"\s*:\s*\{(.*?)\}', package, re.S)
        self.assertIsNotNone(scripts)
        build_match = re.search(r'"build"\s*:\s*"([^"]*)"', scripts.group(1))
        if build_match is None:
            self.skipTest("no build script; nothing to constrain")
        build_script = build_match.group(1)
        self.assertIn(
            "web",
            build_script,
            "the build script must only build the web front end (web/dist)",
        )
        self.assertNotIn(
            "src/",
            build_script,
            "the build script must not bundle server source; the server runs "
            "TypeScript directly at request time",
        )

    def test_preview_reuses_the_shipped_fragments(self):
        """Vite must render the real fragments, not a duplicated shell.

        An earlier attempt checked in src/ui/index.html, which duplicated 18
        lines of list.html and would silently drift from what the server
        assembles. The preview now reads the fragments through a plugin.
        """
        config = SERVER / "vite.config.ts"
        if not config.is_file():
            self.skipTest("no Vite config present")
        self.assertFalse(
            (UI / "index.html").exists(),
            "a checked-in index.html duplicates list.html; generate the "
            "preview shell from the fragment instead",
        )
        text = read(config)
        self.assertRegex(
            text,
            r"readFileSync|Bun\.file|readFile",
            "the preview shell must read the page fragment from disk",
        )
        for page in PAGES:
            with self.subTest(page=page):
                self.assertIn(
                    page,
                    text,
                    f"the preview must route {page}.html",
                )


class SafeRenderingContractTests(unittest.TestCase):
    """The XSS contract survives the split, asserted at the source level."""

    def test_js_assets_never_use_innerhtml(self):
        for page in PAGES:
            text = read(UI / f"{page}.js")
            with self.subTest(page=page):
                self.assertNotIn(
                    "innerHTML",
                    text,
                    f"{page}.js must render memory data via textContent",
                )

    def test_js_assets_use_text_content(self):
        for page in PAGES:
            text = read(UI / f"{page}.js")
            with self.subTest(page=page):
                self.assertIn("textContent", text)

    def test_no_page_offers_a_write_control(self):
        """Read-only means no create, edit, or delete affordance.

        A search form is a read operation, so <form> and its submit button
        are allowed; what must be absent is any control that would mutate
        memory.
        """
        for page in PAGES:
            html = read(UI / f"{page}.html")
            for verb in ("Delete", "Create", "Save", "Edit", "method=\"post\""):
                with self.subTest(page=page, verb=verb):
                    self.assertNotIn(verb, html)

    def test_search_form_stays_a_get_operation(self):
        """The only form is search; it must never post."""
        for page in PAGES:
            html = read(UI / f"{page}.html")
            if "<form" not in html:
                continue
            with self.subTest(page=page):
                self.assertNotRegex(
                    html,
                    r'method\s*=\s*["\']post["\']',
                    f"{page}.html must not submit a mutating request",
                )
                self.assertIn(
                    'role="search"',
                    html,
                    f"the form in {page}.html must declare itself as search",
                )


if __name__ == "__main__":
    unittest.main()
