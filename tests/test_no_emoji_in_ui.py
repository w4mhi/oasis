"""
No emoji in shipped UI assets.

Raspberry Pi OS ships no colour-emoji font, so every emoji codepoint in the UI
renders as a tofu box on the device OASIS runs on — including the kiosk an
operator watches during an incident. This is a known rule in the project, but it
was only ever enforced by remembering it, and 66 emoji had accumulated across 11
files: the file browser's type icons, the Setup page's device status dots, the
Winlink mail toolbar, ICS-205's lock/save controls, and — worst — the icon fields
in maps/traffic/warnings.json and configuration/hazards.json, which ARE the map
markers for placed emergencies and detected hazards.

Use inline SVG stroked in currentColor (see common/js/incident-icons.js) or a
plain BMP glyph such as U+25CF, and let colour carry the state.

Markdown is exempt: docs are read on GitHub and in editors, not rendered by the
Pi's browser. The existing curly-quote gate in .github/workflows/js-tests.yml
covers a different failure and only looks at maps/; this runs over every tracked
UI asset as part of the normal test suite.
"""

import os
import subprocess
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# Assets the Pi's browser actually renders.
_UI_SUFFIXES = (".html", ".js", ".css", ".json")

# Third-party bundles we don't author and can't reformat.
_VENDOR_MARKERS = ("/vendor/", "vendor/", "maplibre-gl.js", "pmtiles.js",
                   "satellite.min.js", "pdf-lib.min.js")

# U+FE0F forces emoji presentation on an otherwise-fine BMP glyph, so a bare
# arrow or circle still turns into tofu. Catch it alongside the emoji planes.
_VARIATION_SELECTOR = 0xFE0F


def _tracked_ui_files():
    out = subprocess.check_output(["git", "ls-files"], cwd=_ROOT).decode()
    for rel in out.split("\n"):
        rel = rel.strip()
        if not rel or not rel.endswith(_UI_SUFFIXES):
            continue
        if any(marker in rel for marker in _VENDOR_MARKERS):
            continue
        yield rel


def _emoji_in(path):
    hits = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            for ch in line:
                code = ord(ch)
                if code >= 0x1F000 or code == _VARIATION_SELECTOR:
                    hits.append((lineno, hex(code), ch))
    return hits


class NoEmojiInUiTest(unittest.TestCase):
    def test_no_emoji_codepoints_in_tracked_ui_assets(self):
        offenders = []
        scanned = 0
        for rel in _tracked_ui_files():
            scanned += 1
            for lineno, code, ch in _emoji_in(os.path.join(_ROOT, rel)):
                offenders.append(f"{rel}:{lineno} {code} {ch!r}")
        self.assertGreater(scanned, 50, "file scan found too little — is git ls-files working?")
        self.assertEqual(
            offenders, [],
            "emoji render as tofu boxes on Raspberry Pi OS (no emoji font).\n  "
            + "\n  ".join(offenders)
            + "\n\nUse inline SVG in currentColor (common/js/incident-icons.js) or a "
              "BMP glyph like U+25CF, and let colour carry the state.",
        )

    def test_the_two_incident_catalogs_stay_glyph_free(self):
        """These two feed map markers, so a regression here is the most visible."""
        for rel in ("maps/traffic/warnings.json", "configuration/hazards.json"):
            with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
                raw = fh.read()
            self.assertNotIn('"icon"', raw, f"{rel}: icons come from incident-icons.js, not JSON")
            self.assertNotIn('"emoji"', raw, f"{rel}: icons come from incident-icons.js, not JSON")


if __name__ == "__main__":
    unittest.main()
