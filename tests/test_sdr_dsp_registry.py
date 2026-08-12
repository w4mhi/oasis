"""The sdr-dsp feature across every surface that has to know about it.

A feature in OASIS is registered in FOUR places, and missing one fails in a
different way each time:

  common/setup_registry.py   miss it -> the browser cannot resolve or install it
  setup-oasis.py             miss it -> the CLI menu never offers it
  server/system/setup.html   miss it -> the checkbox is absent, with NO error
  scripts/offline-manifest.json  miss it -> bundling and version gates skip it

The setup.html one is the nastiest: the page renders, the operator ticks nothing
because there is nothing to tick, and no log line anywhere says why. Hence a test
that reads the actual HTML rather than trusting that someone remembered.
"""
import importlib.util
import json
import os
import unittest

from common import setup_engine as SE
from common import setup_registry as SR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "sdr-dsp"


def _setup_oasis():
    spec = importlib.util.spec_from_file_location(
        "_setup_oasis_for_test", os.path.join(ROOT, "setup-oasis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SurfacesTest(unittest.TestCase):
    def test_orchestrator_registry(self):
        reg = SR.build_registry("/tmp/oasis-test-root")
        self.assertIn(KEY, reg)
        self.assertIn(KEY, SR.PRIVILEGED_FEATURES)   # apt + sudo

    def test_cli_menu(self):
        feats = {f.key: f for f in _setup_oasis().FEATURES}
        self.assertIn(KEY, feats)
        f = feats[KEY]
        self.assertTrue(f.internet, "third-party apt repo — must warn when offline")
        self.assertFalse(f.default, "optional: capture works uncorrected without it")
        self.assertTrue(os.path.exists(os.path.join(ROOT, f.script)), f.script)

    def test_setup_html_checkbox(self):
        with open(os.path.join(ROOT, "server", "system", "setup.html"),
                  encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn(f'data-feature="{KEY}"', html,
                      "no checkbox -> uninstallable from the browser, with no error")

    def test_manifest_entry(self):
        with open(os.path.join(ROOT, "scripts", "offline-manifest.json"),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        self.assertIn(KEY, man["features"])
        entry = man["features"][KEY]
        self.assertEqual(entry["type"], "apt")
        self.assertTrue(entry.get("online_only"),
                        "the bundler does no dep resolution; these pull libfftw3")
        self.assertIn("third_party_repo", entry)

    def test_the_manifest_does_not_install_openwebrx_itself(self):
        """The entire reason this is a separate feature. openwebrx seizes the
        RTL-SDR exclusively — which is why OASIS installs it disabled — while the
        DSP library alone has no such behaviour."""
        with open(os.path.join(ROOT, "scripts", "offline-manifest.json"),
                  encoding="utf-8") as fh:
            pkgs = json.load(fh)["features"][KEY]["packages"]["common"]
        self.assertNotIn("openwebrx", pkgs)
        self.assertEqual(sorted(pkgs),
                         ["csdr", "libcsdr0", "python3-csdr", "rtl-connector"])


class DependencyTest(unittest.TestCase):
    def setUp(self):
        self.reg = SR.build_registry("/tmp/oasis-test-root")

    def test_it_needs_the_rtl_sdr_tools(self):
        self.assertIn("rtl-sdr", self.reg[KEY].dependencies)

    def test_selecting_it_pulls_the_driver_first(self):
        order = SE.resolve_plan([KEY], self.reg).ordered_features
        self.assertLess(order.index("rtl-sdr"), order.index(KEY))

    def test_nothing_depends_on_it(self):
        """Everything above this degrades rather than breaks: a station without
        the DSP stack still records passes through rtl_fm, just uncorrected. If
        some feature ever declares a hard dependency on it, that promise is gone
        and the offline-first story goes with it."""
        for key, spec in self.reg.items():
            self.assertNotIn(KEY, spec.dependencies,
                             f"{key} must degrade without the DSP stack, not require it")


if __name__ == "__main__":
    unittest.main()
