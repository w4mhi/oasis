#!/usr/bin/env python3
"""
test_offline_manifest.py — self-tests for common/manifest.py

Guards three behaviours that the "bookworm bundle / librtlsdr 0.6.0 on Trixie"
bug exposed:

  1. Suite-aware package selection — apt_packages() returns the correct
     suite-specific package name, not the wrong-suite variant.

  2. Capability gates (min_version) — check_min_version() rejects versions that
     are below the floor and accepts versions that meet it.

  3. Newest-source-wins resolution — resolve_source() always installs the newest
     obtainable version; a stale bundled copy must never win over a newer apt or
     installed version on a different suite.

Run directly:  python3 tests/test_offline_manifest.py
"""

import os
import sys
import unittest

# ── Make `import common.manifest` and `import manifest` both work whether this
#    script is run from the repo root or from tests/. ──────────────────────────
_HERE       = os.path.dirname(os.path.abspath(__file__))
_ROOT       = os.path.dirname(_HERE)
_COMMON     = os.path.join(_ROOT, "common")
for _p in (_ROOT, _COMMON):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import manifest as M   # common/manifest.py


# ─────────────────────────────────────────────────────────────────────────────
# 1. Suite-aware package selection
# ─────────────────────────────────────────────────────────────────────────────
class TestSuiteAwarePackages(unittest.TestCase):
    """apt_packages() must return the suite-specific package name, not the other
    suite's variant."""

    def test_rtl_sdr_bookworm_includes_librtlsdr0(self):
        pkgs = M.apt_packages("rtl-sdr", suite="bookworm")
        self.assertIn("librtlsdr0", pkgs,
                      "bookworm suite must include librtlsdr0")

    def test_rtl_sdr_bookworm_excludes_librtlsdr2(self):
        pkgs = M.apt_packages("rtl-sdr", suite="bookworm")
        self.assertNotIn("librtlsdr2", pkgs,
                         "bookworm suite must NOT include librtlsdr2 (trixie-only)")

    def test_rtl_sdr_trixie_includes_librtlsdr2(self):
        pkgs = M.apt_packages("rtl-sdr", suite="trixie")
        self.assertIn("librtlsdr2", pkgs,
                      "trixie suite must include librtlsdr2")

    def test_rtl_sdr_trixie_excludes_librtlsdr0(self):
        pkgs = M.apt_packages("rtl-sdr", suite="trixie")
        self.assertNotIn("librtlsdr0", pkgs,
                         "trixie suite must NOT include librtlsdr0 (bookworm-only)")

    def test_rtl_sdr_common_packages_present_for_any_suite(self):
        for suite in ("bookworm", "trixie"):
            with self.subTest(suite=suite):
                pkgs = M.apt_packages("rtl-sdr", suite=suite)
                self.assertIn("rtl-sdr", pkgs)
                self.assertIn("libusb-1.0-0", pkgs)

    def test_no_suite_returns_union(self):
        pkgs = M.apt_packages("rtl-sdr", suite=None)
        self.assertIn("librtlsdr0", pkgs)
        self.assertIn("librtlsdr2", pkgs)

    def test_rtl_sdr_feed_has_no_suite_split(self):
        """rtl-sdr-feed uses common-only packages (no by_suite block)."""
        pkgs_bw = M.apt_packages("rtl-sdr-feed", suite="bookworm")
        pkgs_tx = M.apt_packages("rtl-sdr-feed", suite="trixie")
        self.assertEqual(sorted(pkgs_bw), sorted(pkgs_tx))
        self.assertIn("socat", pkgs_bw)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Capability gates (min_version)
# ─────────────────────────────────────────────────────────────────────────────
class TestCapabilityGates(unittest.TestCase):
    """check_min_version() must enforce the librtlsdr >= 2.0 gate correctly."""

    def _check(self, versions):
        return M.check_min_version("rtl-sdr", versions)

    # Gate should fire for versions below the floor
    def test_rejects_none(self):
        unmet = self._check({"librtlsdr": None})
        self.assertEqual(len(unmet), 1)
        self.assertEqual(unmet[0]["package"], "librtlsdr")

    def test_rejects_bookworm_version(self):
        """The exact version bookworm ships (pre-2.0) must fail the gate."""
        unmet = self._check({"librtlsdr": "0.6.0"})
        self.assertEqual(len(unmet), 1)
        self.assertEqual(unmet[0]["required"], "2.0")

    def test_rejects_old_fork_version(self):
        unmet = self._check({"librtlsdr": "1.0.0"})
        self.assertEqual(len(unmet), 1)

    # Gate should pass for versions that meet or exceed the floor
    def test_accepts_exact_minimum(self):
        unmet = self._check({"librtlsdr": "2.0"})
        self.assertEqual(unmet, [])

    def test_accepts_trixie_version(self):
        """The version that ships in trixie must satisfy the gate."""
        unmet = self._check({"librtlsdr": "2.0.2"})
        self.assertEqual(unmet, [])

    def test_accepts_future_version(self):
        unmet = self._check({"librtlsdr": "3.1.0"})
        self.assertEqual(unmet, [])

    def test_no_gate_on_feature_without_min_version(self):
        """Features without a min_version block must never produce unmet gates."""
        unmet = M.check_min_version("graywolf", {"graywolf": "0.1.0"})
        self.assertEqual(unmet, [])

    def test_gate_reports_reason(self):
        unmet = self._check({"librtlsdr": "0.6.0"})
        self.assertTrue(
            unmet[0].get("reason"),
            "Gate failure should include the human-readable reason string",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Newest-source-wins resolution
# ─────────────────────────────────────────────────────────────────────────────
class TestResolveSource(unittest.TestCase):
    """resolve_source() must always pick the newest available version.

    The critical invariant: a stale bundled version (e.g. bookworm 0.6.0) must
    NOT win over a newer apt version on a different suite (trixie 2.0.2).
    """

    def test_apt_wins_over_older_bundle(self):
        """Core regression: trixie apt 2.0.2 must beat the bookworm bundle 0.6.0."""
        src = M.resolve_source(
            installed=None,
            apt_candidate="2.0.2",
            bundled="0.6.0",
        )
        self.assertEqual(src, "apt")

    def test_bundle_wins_when_newer_than_apt(self):
        src = M.resolve_source(
            installed=None,
            apt_candidate="1.9.0",
            bundled="2.0.2",
        )
        self.assertEqual(src, "bundle")

    def test_skip_when_installed_is_current(self):
        src = M.resolve_source(
            installed="2.0.2",
            apt_candidate="2.0.2",
            bundled="2.0.2",
        )
        self.assertEqual(src, "skip")

    def test_skip_when_installed_is_newer(self):
        src = M.resolve_source(
            installed="3.0.0",
            apt_candidate="2.0.2",
            bundled="0.6.0",
        )
        self.assertEqual(src, "skip")

    def test_apt_wins_tie_over_bundle(self):
        """On an exact tie, apt is preferred (matches the host suite)."""
        src = M.resolve_source(
            installed=None,
            apt_candidate="2.0.2",
            bundled="2.0.2",
        )
        self.assertEqual(src, "apt")

    def test_bundle_used_when_no_apt(self):
        src = M.resolve_source(
            installed=None,
            apt_candidate=None,
            bundled="2.0.2",
        )
        self.assertEqual(src, "bundle")

    def test_apt_used_when_no_bundle(self):
        src = M.resolve_source(
            installed=None,
            apt_candidate="2.0.2",
            bundled=None,
        )
        self.assertEqual(src, "apt")

    def test_none_when_nothing_available(self):
        src = M.resolve_source(
            installed=None,
            apt_candidate=None,
            bundled=None,
        )
        self.assertIsNone(src)

    def test_skip_when_nothing_obtainable_but_installed(self):
        src = M.resolve_source(
            installed="2.0.2",
            apt_candidate=None,
            bundled=None,
        )
        self.assertEqual(src, "skip")

    def test_upgrade_from_installed_to_newer_apt(self):
        src = M.resolve_source(
            installed="0.6.0",
            apt_candidate="2.0.2",
            bundled=None,
        )
        self.assertEqual(src, "apt")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Real-manifest sanity checks
# ─────────────────────────────────────────────────────────────────────────────
class TestManifestSanity(unittest.TestCase):
    """Smoke-tests against the actual offline-manifest.json on disk."""

    def setUp(self):
        M._cache.clear()   # ensure each test loads fresh from disk
        self.m = M.load_manifest()

    def test_schema_version_present(self):
        self.assertIn("schema_version", self.m)

    def test_known_suites(self):
        self.assertIn("bookworm", M.suites(self.m))
        self.assertIn("trixie", M.suites(self.m))

    def test_known_features(self):
        for feat in ("server", "rtl-sdr", "graywolf", "kiwix", "webssh", "winlink"):
            with self.subTest(feature=feat):
                self.assertIn(feat, M.features(self.m))

    def test_rtl_sdr_has_min_version_gate(self):
        gate = M.min_version("rtl-sdr", self.m)
        self.assertIsNotNone(gate, "rtl-sdr must declare a min_version gate")
        self.assertIn("librtlsdr", gate)

    def test_rtl_sdr_bundle_group_shared(self):
        """rtl-sdr, rtl-sdr-feed, and rtl-sdr-diag all share one bundle group."""
        for feat in ("rtl-sdr", "rtl-sdr-feed", "rtl-sdr-diag"):
            with self.subTest(feature=feat):
                self.assertEqual(M.bundle_group(feat, self.m), "rtl-sdr")

    def test_rtl_sdr_bench_bundles_sox(self):
        pkgs = M.apt_packages("rtl-sdr-bench", suite="trixie", m=self.m)
        self.assertIn("sox", pkgs)

    def test_rtl_sdr_bench_shares_bundle_group(self):
        self.assertEqual(M.bundle_group("rtl-sdr-bench", self.m), "rtl-sdr")

    def test_server_is_pypi_type(self):
        self.assertEqual(M.source_type("server", self.m), "pypi")
        pkgs = M.pypi_packages("server", self.m)
        names = [p["name"] for p in pkgs]
        self.assertIn("Flask", names)

    def test_bundle_dir_suite_scoped_for_apt(self):
        bw = M.bundle_dir("/bundle", "rtl-sdr", suite="bookworm", m=self.m)
        tx = M.bundle_dir("/bundle", "rtl-sdr", suite="trixie",   m=self.m)
        self.assertNotEqual(bw, tx)
        self.assertTrue(bw.endswith("bookworm"))
        self.assertTrue(tx.endswith("trixie"))

    def test_bundle_dir_not_suite_scoped_for_github_release(self):
        d = M.bundle_dir("/bundle", "graywolf", suite=None, m=self.m)
        self.assertNotIn("bookworm", d)
        self.assertNotIn("trixie", d)

    def test_bundle_dir_feature_local_uses_bundle_base(self):
        # rtl-sdr sets bundle_base → packages live under the feature dir. The
        # bundle/repo root is recovered as the parent of the offline-packages root.
        p = M.bundle_dir("/bundle/offline-packages", "rtl-sdr", suite="bookworm", m=self.m)
        self.assertEqual(
            p, os.path.join("/bundle", "features/rtl-sdr/packages", "rtl-sdr", "bookworm"))
        # direwolf shares the same feature-local base (both live in features/rtl-sdr/).
        d = M.bundle_dir("/bundle/offline-packages", "direwolf", suite="bookworm", m=self.m)
        self.assertEqual(
            d, os.path.join("/bundle", "features/rtl-sdr/packages", "direwolf", "bookworm"))

    def test_bundle_dir_central_default_unchanged(self):
        # A feature without bundle_base stays under offline-packages (root used as-is).
        p = M.bundle_dir("/bundle/offline-packages", "graywolf", m=self.m)
        self.assertEqual(p, os.path.join("/bundle/offline-packages", "graywolf"))

    def test_satellites_voice_is_apt(self):
        self.assertEqual(M.source_type("satellites-voice", self.m), "apt")

    def test_satellites_voice_bundles_speech_stack(self):
        """The pass-alert voice must vendor speech-dispatcher + espeak-ng for
        every suite it declares (common packages, no suite split)."""
        for suite in ("bookworm", "trixie"):
            with self.subTest(suite=suite):
                pkgs = M.apt_packages("satellites-voice", suite=suite, m=self.m)
                self.assertIn("speech-dispatcher", pkgs)
                self.assertIn("speech-dispatcher-espeak-ng", pkgs)
                self.assertIn("espeak-ng", pkgs)

    def test_satellites_voice_bundle_dir_feature_local(self):
        # satellites-voice sets bundle_base → packages live under the service dir,
        # suite-scoped (bookworm and trixie never collide). The installer
        # (services/satellites/install-voice.py) reads this same path.
        p = M.bundle_dir("/bundle/offline-packages", "satellites-voice", suite="trixie", m=self.m)
        self.assertEqual(
            p, os.path.join("/bundle", "services/satellites/packages", "satellites-voice", "trixie"))
        bw = M.bundle_dir("/bundle/offline-packages", "satellites-voice", suite="bookworm", m=self.m)
        self.assertNotEqual(p, bw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
