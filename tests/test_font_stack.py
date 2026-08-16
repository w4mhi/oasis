"""The served pages and the kiosk must resolve to the SAME font.

They each declare their own `--mono`, and the two silently diverged: the kiosk's
stack listed JetBrains Mono / Cascadia Code where the shared stylesheet listed
Roboto Mono — the one face OASIS actually bundles. The kiosk does not link
css/common.css either, so even naming Roboto Mono would not have helped until the
@font-face rules moved into /common/css/fonts.css.

The failure is invisible on a Mac: both stacks begin with `ui-monospace`, which
exists there, so the divergence only ever appeared on the Pi — where nothing in
either stack exists except the bundled face, and the kiosk fell through to the OS
default monospace while the served pages rendered in Roboto Mono.
"""
import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

COMMON_CSS = os.path.join(_ROOT, "css", "common.css")
FONTS_CSS = os.path.join(_ROOT, "common", "css", "fonts.css")
KIOSK = os.path.join(_ROOT, "oasis-dashboard", "dashboard.html")

# The face OASIS vendors. On a Pi it is the ONLY entry in either stack that
# resolves, so a stack that omits it renders in whatever the OS happens to ship.
BUNDLED_FACE = "Roboto Mono"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _code(path):
    """File contents with CSS and HTML comments removed.

    Scanning raw text finds the words this file's own comments use to EXPLAIN
    the rules — the first draft of these tests failed on its own prose.
    """
    text = _read(path)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _mono_stack(text):
    """The --mono declaration as a normalized list of family names."""
    match = re.search(r"--mono\s*:\s*([^;]+);", text)
    if not match:
        return None
    return [part.strip().strip('"\'') for part in match.group(1).split(",")]


class FontStackTest(unittest.TestCase):
    def test_both_surfaces_declare_the_same_stack(self):
        served = _mono_stack(_read(COMMON_CSS))
        kiosk = _mono_stack(_read(KIOSK))
        self.assertIsNotNone(served, "css/common.css has no --mono")
        self.assertIsNotNone(kiosk, "the kiosk has no --mono")
        self.assertEqual(kiosk, served,
                         "the kiosk and the served pages would render in different fonts")

    def test_the_stack_names_the_bundled_face(self):
        # Without this the Pi falls through to the OS default, which is the whole
        # reason the font is vendored in the first place.
        for path in (COMMON_CSS, KIOSK):
            self.assertIn(BUNDLED_FACE, _mono_stack(_read(path)),
                          f"{os.path.relpath(path, _ROOT)} does not name {BUNDLED_FACE}")

    def test_the_face_is_declared_once_in_a_shared_file(self):
        fonts = _read(FONTS_CSS)
        self.assertIn(BUNDLED_FACE, fonts)
        # A second copy is how the two would drift again.
        self.assertNotIn("@font-face", _code(COMMON_CSS))
        self.assertNotIn("@font-face", _code(KIOSK))

    def test_both_surfaces_actually_reach_the_shared_file(self):
        # Naming the family is useless if the @font-face never loads.
        self.assertIn("/common/css/fonts.css", _read(COMMON_CSS))
        self.assertIn("/common/css/fonts.css", _read(KIOSK))

    def test_font_urls_are_absolute(self):
        # fonts.css is reached from two different directories; a relative url()
        # would resolve against the importer in one of them and 404.
        for url in re.findall(r"url\(['\"]([^'\"]+)['\"]\)", _read(FONTS_CSS)):
            self.assertTrue(url.startswith("/"), f"relative font url: {url}")

    def test_the_font_files_exist(self):
        for url in re.findall(r"url\(['\"]([^'\"]+)['\"]\)", _read(FONTS_CSS)):
            self.assertTrue(os.path.isfile(os.path.join(_ROOT, url.lstrip("/"))),
                            f"missing vendored font: {url}")

    def test_the_import_precedes_every_rule(self):
        # CSS ignores an @import that appears after any other rule, silently.
        text = _code(COMMON_CSS)
        import_at = text.index("@import")
        first_rule = min(
            (text.index(tok) for tok in (":root", "@font-face", "html", "body")
             if tok in text), default=len(text))
        self.assertLess(import_at, first_rule,
                        "@import lands after a rule and will be ignored")


if __name__ == "__main__":
    unittest.main()
