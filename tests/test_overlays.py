"""
common/overlays.py — the one lookup + install for every vendored .dtbo.

Three features needed an overlay the stock Pi OS image does not provide (or
provides broken): DRAWS's draws/udrc and the CM4Stack panel. Each had grown its
own answer to "where does an overlay come from" — draws asked only whether the
OS happened to ship one, cm4stack carried a private three-path candidate list —
so the rule existed twice and was written down nowhere.

The behaviours worth pinning are the ones a field install depends on: it is
idempotent (a firmware update can wipe a hand-copied overlay, so installers run
this EVERY time), it replaces a stale copy, and it NEVER raises — a read-only
/boot must degrade to a reported reason, not kill the installer mid-run.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import overlays  # noqa: E402


class _Tree:
    """A throwaway repo_root with an overlays/ dir, plus a fake boot dir."""

    def __init__(self, stack, blobs=()):
        self.root = stack.enter_context(tempfile.TemporaryDirectory())
        self.boot = stack.enter_context(tempfile.TemporaryDirectory())
        os.makedirs(os.path.join(self.root, "overlays"), exist_ok=True)
        for name, data in blobs:
            with open(os.path.join(self.root, "overlays", name), "wb") as fh:
                fh.write(data)


class VendoredLookupTest(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    def test_finds_a_blob_in_the_canonical_directory(self):
        t = _Tree(self.stack, [("draws.dtbo", b"DTBO")])
        self.assertEqual(overlays.vendored_path("draws", t.root),
                         os.path.join(t.root, "overlays", "draws.dtbo"))

    def test_the_extension_is_optional(self):
        t = _Tree(self.stack, [("draws.dtbo", b"DTBO")])
        self.assertEqual(overlays.vendored_path("draws.dtbo", t.root),
                         overlays.vendored_path("draws", t.root))

    def test_absent_is_none_not_an_exception(self):
        t = _Tree(self.stack)
        self.assertIsNone(overlays.vendored_path("nope", t.root))

    def test_the_legacy_feature_local_path_still_resolves(self):
        """A bundle built before the unification put the panel overlay under
        displays/cm4stack/packages/. It must keep working."""
        t = _Tree(self.stack)
        legacy = os.path.join(t.root, "displays", "cm4stack", "packages")
        os.makedirs(legacy)
        with open(os.path.join(legacy, "m5stack-cm4.dtbo"), "wb") as fh:
            fh.write(b"PANEL")
        self.assertEqual(overlays.vendored_path("m5stack-cm4", t.root),
                         os.path.join(legacy, "m5stack-cm4.dtbo"))


class InstallTest(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    def test_installs_when_missing(self):
        t = _Tree(self.stack, [("draws.dtbo", b"V1")])
        changed, why = overlays.install("draws", t.boot, t.root)
        self.assertTrue(changed)
        self.assertEqual(why, "installed")
        with open(os.path.join(t.boot, "draws.dtbo"), "rb") as fh:
            self.assertEqual(fh.read(), b"V1")

    def test_second_run_is_a_no_op(self):
        """Installers call this on EVERY run, so the common case must be free."""
        t = _Tree(self.stack, [("draws.dtbo", b"V1")])
        overlays.install("draws", t.boot, t.root)
        changed, why = overlays.install("draws", t.boot, t.root)
        self.assertFalse(changed)
        self.assertEqual(why, "already-current")

    def test_a_stale_copy_is_replaced(self):
        """A kernel/firmware update can leave an older overlay behind — that is
        the case this exists for, and it must not be mistaken for current."""
        t = _Tree(self.stack, [("draws.dtbo", b"NEW")])
        with open(os.path.join(t.boot, "draws.dtbo"), "wb") as fh:
            fh.write(b"OLD")
        changed, why = overlays.install("draws", t.boot, t.root)
        self.assertTrue(changed)
        self.assertEqual(why, "replaced")
        with open(os.path.join(t.boot, "draws.dtbo"), "rb") as fh:
            self.assertEqual(fh.read(), b"NEW")

    def test_nothing_vendored_is_reported_not_raised(self):
        t = _Tree(self.stack)
        self.assertEqual(overlays.install("draws", t.boot, t.root),
                         (False, "no-vendored-copy"))

    def test_a_read_only_boot_degrades_to_a_reason(self):
        """/boot read-only, or not root. The installer reports and carries on;
        dying here would abandon a half-finished setup."""
        t = _Tree(self.stack, [("draws.dtbo", b"V1")])

        def refuse(src, dest):
            raise PermissionError("Read-only file system")

        changed, why = overlays.install("draws", t.boot, t.root, copy=refuse)
        self.assertFalse(changed)
        self.assertIn("Read-only", why)


class AvailabilityTest(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    def test_available_covers_both_installed_and_installable(self):
        """'Your OS is too old' and 'we ship it, run the installer' are different
        problems, and only the first is the operator's to solve."""
        t = _Tree(self.stack, [("draws.dtbo", b"V1")])
        self.assertFalse(overlays.installed("draws", t.boot))
        self.assertTrue(overlays.available("draws", t.boot, t.root),
                        "we ship one, so it is available")
        overlays.install("draws", t.boot, t.root)
        self.assertTrue(overlays.installed("draws", t.boot))

    def test_neither_shipped_nor_installed_is_unavailable(self):
        t = _Tree(self.stack)
        self.assertFalse(overlays.available("draws", t.boot, t.root))


class ShippedBlobsTest(unittest.TestCase):
    """The repo really ships what the DRAWS installer expects to find."""

    def test_draws_and_udrc_are_vendored(self):
        for name in ("draws", "udrc"):
            path = overlays.vendored_path(name)
            self.assertIsNotNone(path, f"{name}.dtbo is missing from overlays/")
            self.assertGreater(os.path.getsize(path), 0)

    def test_every_vendored_blob_is_documented(self):
        """A committed binary with no provenance is one nobody can rebuild."""
        doc = os.path.join(overlays.REPO_ROOT, "overlays", "SOURCE.md")
        self.assertTrue(os.path.isfile(doc))
        with open(doc, encoding="utf-8") as fh:
            text = fh.read()
        vendor_dir = os.path.join(overlays.REPO_ROOT, "overlays")
        for entry in sorted(os.listdir(vendor_dir)):
            if entry.endswith(".dtbo"):
                self.assertIn(entry, text,
                              f"{entry} is committed but not described in SOURCE.md")
        self.assertIn("make dtbs", text, "SOURCE.md must carry the rebuild recipe")


class BootDirTest(unittest.TestCase):
    def test_prefers_the_modern_firmware_path_when_both_exist(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.assertEqual(overlays.boot_dir((a, b)), a)

    def test_falls_back_to_the_older_layout(self):
        with tempfile.TemporaryDirectory() as b:
            self.assertEqual(overlays.boot_dir(("/definitely/not/here", b)), b)

    def test_no_candidate_exists_yields_the_modern_default(self):
        self.assertEqual(overlays.boot_dir(("/nope/a", "/nope/b")), "/nope/a")


if __name__ == "__main__":
    unittest.main()
