"""The freshness vocabulary must be identical in Python and JavaScript.

The same condition is named in two places: _UPDATE_BADGE in common/diagnostics.py
(the Diagnostics Data Updates section) and LABEL in common/js/freshness.js (the
dashboard pill and the kiosk pill). Nothing at runtime connects them, so they
drift silently — which is exactly what happened: the kiosk read "DATA STALE"
while Diagnostics read "STALE" for one identical fact, and an operator comparing
the two screens had no way to know they meant the same thing.

Cross-language, so it is parsed rather than imported. Kept deliberately dumb: a
regex over the JS literal beats a JS runtime dependency in a Python suite.
"""
import os
import re
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from common import diagnostics as D  # noqa: E402

_JS = os.path.join(_REPO, "common", "js", "freshness.js")


def _js_labels():
    """Pull the LABEL object literal out of freshness.js."""
    with open(_JS, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"var LABEL\s*=\s*\{(.*?)\}\s*;", src, re.S)
    if not m:
        raise AssertionError("could not find `var LABEL = {...}` in "
                             "common/js/freshness.js — was it renamed?")
    return dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", m.group(1)))


class VocabularyMatchesTest(unittest.TestCase):
    def setUp(self):
        self.js = _js_labels()
        self.py = D._UPDATE_BADGE

    def test_the_same_states_are_named_in_both(self):
        self.assertEqual(set(self.js), set(self.py),
                         "one side knows a state the other does not")

    def test_every_state_uses_the_same_word(self):
        for state in sorted(self.py):
            self.assertEqual(
                self.js[state], self.py[state],
                f"state {state!r} is called {self.js[state]!r} in the browser "
                f"and {self.py[state]!r} on the Diagnostics page — an operator "
                f"comparing the two screens cannot tell they mean the same "
                f"thing")

    def test_labels_are_single_words(self):
        # They sit beside IMPERIAL and OPS on the kiosk pill row; a two-word
        # label makes that row uneven and can wrap mid-state.
        for state, word in self.js.items():
            self.assertNotIn(" ", word,
                             f"{state} label {word!r} must be one word")

    def test_labels_are_upper_case(self):
        for state, word in self.js.items():
            self.assertEqual(word, word.upper(), state)

    def test_no_emoji_in_labels(self):
        # Pi OS Lite has no emoji font; these render on the kiosk.
        for state, word in list(self.js.items()) + list(self.py.items()):
            self.assertEqual([c for c in word if ord(c) >= 0x1F000], [], state)


if __name__ == "__main__":
    unittest.main()
