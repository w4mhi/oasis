"""Bundle-ignore rules that fail SILENTLY when wrong.

An over-broad entry in scripts/bundle-ignore does not raise, log, or fail a
build. The bundle is simply missing something, and you find out on the Pi —
possibly in the field. The same reasoning as NoStaleDisplaysPathTest in
tests/test_overlays.py: pin the things that quietly do the wrong thing.

Real regression this file exists for (2026-08-15 -> 16): `static/repeaterbook/
*.csv` was excluded because phase_repeaterbook was going to fetch the directory
into the bundle instead. That phase was later removed, the exclusion was not,
and bundles shipped with no repeater data at all.
"""
import importlib.util
import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)


def _load_ignore(profile="full"):
    """Load the real filter the bundler uses, from the real file."""
    path = os.path.join(_REPO, "scripts", "create-oasis-offline.py")
    spec = importlib.util.spec_from_file_location("_cob_for_tests", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_ignore(profile)


class BundleCarriesOperatorDataTest(unittest.TestCase):
    """Data the operator supplies by hand must reach their own Pi.

    A bundle is how this station reaches its own hardware, not a distribution
    channel. Being gitignored means "not ours to publish", NOT "leave it behind".
    """

    def setUp(self):
        self.patterns = _load_ignore("full")

    def _excluded(self, rel_path):
        """True if any ignore pattern would drop *rel_path* from a bundle."""
        import fnmatch
        name = os.path.basename(rel_path)
        for pat in self.patterns:
            p = pat.rstrip("/")
            if fnmatch.fnmatch(rel_path, p) or fnmatch.fnmatch(name, p):
                return True
            if "/" in p and rel_path.startswith(p + "/"):
                return True
            if "/" not in p and p in rel_path.split("/"):
                return True
        return False

    def test_the_repeater_directory_ships(self):
        # THE regression. Excluded with nothing putting it back, so bundles
        # carried no repeaters and nothing said so.
        self.assertFalse(
            self._excluded("static/repeaterbook/repeaterbook.csv"),
            "the operator's repeater CSV must travel in the bundle — nothing "
            "else puts it there, and its absence is silent")

    def test_no_pattern_blanket_excludes_the_repeaterbook_folder(self):
        for pat in self.patterns:
            self.assertNotIn("repeaterbook", pat.lower(),
                             f"bundle-ignore entry {pat!r} risks dropping "
                             f"hand-exported repeater data")

    def test_station_config_ships(self):
        # Same class: excluding it means re-entering callsign and grid on every
        # rebuild, and an empty template can overwrite a configured Pi.
        self.assertFalse(self._excluded("configuration/station.json"))

    def test_the_ics_forms_ship(self):
        self.assertFalse(self._excluded("static/ics-205/ics205-template.js"))


class BundleExcludesRuntimeStateTest(unittest.TestCase):
    """The other direction: per-station state must NOT travel."""

    def setUp(self):
        self.patterns = _load_ignore("full")

    def test_refresh_state_is_excluded(self):
        # Carrying another box's counters makes data read fresher than it is.
        self.assertIn("configuration/refresh-state.json", self.patterns)

    def test_dev_only_trees_are_excluded(self):
        # load_ignore() strips trailing slashes, so compare bare names.
        bare = {p.rstrip("/") for p in self.patterns}
        for pat in ("specs", ".git", "__pycache__", "tests"):
            self.assertIn(pat, bare, pat)


if __name__ == "__main__":
    unittest.main()
