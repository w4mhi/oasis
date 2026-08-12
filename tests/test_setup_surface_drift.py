"""Every feature the browser offers must be one the resolver knows.

A feature in OASIS is declared in four places, and the failure mode differs in
each. This guards the pairing that fails WORST: setup.html renders a checkbox,
the operator ticks it, and the Setup Orchestrator answers

    blocked: [{"feature": "rtc-pi5", "reason_code": "UNKNOWN_FEATURE"}]

which looks like a broken installer rather than a missing registry entry. That is
exactly what shipped for rtc-pi5 — present in features/, in setup.html and in
setup-oasis.py's menu, absent from common/setup_registry.py, so it was
installable from the terminal and impossible from the browser.

Per-feature tests cannot catch this class: they are written for the feature being
added, which is the one nobody forgets. This one is written against whatever the
HTML happens to contain, so the next feature is covered before it exists.
"""
import os
import re
import unittest

from common import setup_registry as SR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_HTML = os.path.join(ROOT, "server", "system", "setup.html")


def _checkbox_features():
    """Feature keys the Setup page actually offers.

    Skips JS template interpolations — setup.html builds some rows dynamically
    (`data-feature="${otherKey}"`), and those are not literal keys."""
    with open(SETUP_HTML, encoding="utf-8") as fh:
        html = fh.read()
    return sorted({k for k in re.findall(r'data-feature="([^"]+)"', html)
                   if "${" not in k})


class SetupSurfaceDriftTest(unittest.TestCase):
    def setUp(self):
        self.reg = SR.build_registry("/tmp/oasis-surface-drift-test")

    def test_the_page_offers_nothing_the_registry_cannot_resolve(self):
        keys = _checkbox_features()
        self.assertGreater(len(keys), 20, "parsed suspiciously few checkboxes — "
                                          "has the markup changed shape?")
        missing = [k for k in keys if k not in self.reg]
        self.assertEqual(missing, [],
                         f"setup.html offers {missing} but common/setup_registry.py "
                         "does not define them: the checkbox renders, the operator "
                         "ticks it, and the install is blocked with UNKNOWN_FEATURE.")

    def test_every_offered_feature_can_be_planned(self):
        """Resolvable is not the same as installable — a feature whose dependency
        is missing resolves and then fails at plan time, which is the same dead
        end one step later."""
        from common import setup_engine as SE
        for key in _checkbox_features():
            with self.subTest(feature=key):
                plan = SE.resolve_plan([key], self.reg)
                self.assertIn(key, plan.ordered_features)

    def test_privileged_features_are_all_real(self):
        """A key in PRIVILEGED_FEATURES with no spec behind it is dead weight that
        reads as coverage."""
        unknown = [k for k in SR.PRIVILEGED_FEATURES if k not in self.reg]
        self.assertEqual(unknown, [], f"PRIVILEGED_FEATURES names {unknown}, "
                                      "which build_registry does not define")

    def test_every_install_script_exists(self):
        """The last way a feature can be offered and still not work: the registry
        knows it, and the script it points at was moved or renamed."""
        for key in _checkbox_features():
            spec = self.reg.get(key)
            fn = getattr(spec, "install_fn", None)
            if fn is None:
                continue
            for cell in (getattr(fn, "__closure__", None) or ()):
                v = cell.cell_contents
                if isinstance(v, str) and v.endswith(".py") and "/" in v:
                    with self.subTest(feature=key, script=v):
                        self.assertTrue(os.path.exists(os.path.join(ROOT, v)),
                                        f"{key} installs {v}, which does not exist")


class RtcBoardsTest(unittest.TestCase):
    """All three RTCs, because they are the ones that drifted. Each is a separate
    feature rather than a variant: different bus, different overlay, different
    config.txt block — and a Pi 5 with a Witty Pi attached has two of them."""

    BOARDS = ["rtc", "rtc-raspad", "rtc-pi5"]

    def setUp(self):
        self.reg = SR.build_registry("/tmp/oasis-surface-drift-test")

    def test_all_three_are_registered(self):
        for key in self.BOARDS:
            with self.subTest(board=key):
                self.assertIn(key, self.reg)
                self.assertIn(key, SR.PRIVILEGED_FEATURES)

    def test_none_depends_on_another(self):
        """Ticking one must never drag in another's overlay."""
        for key in self.BOARDS:
            for other in self.BOARDS:
                if key != other:
                    self.assertNotIn(other, self.reg[key].dependencies)


if __name__ == "__main__":
    unittest.main()
