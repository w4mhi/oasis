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
        features/cm4stack/packages/. It must keep working."""
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


class BackupAndRestoreTest(unittest.TestCase):
    """Overwriting a stock file with no record of it is how a config.txt edit
    once left a Pi with no sound cards after uninstall. The same risk applies
    here twice: a future Pi OS may ship a FIXED overlay that ours then silently
    replaces, and without the backup that box cannot fall back even after the
    vendored copy is deleted from overlays/."""

    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)

    def test_the_displaced_os_overlay_is_kept(self):
        t = _Tree(self.stack, [("draws.dtbo", b"OASIS")])
        dest = os.path.join(t.boot, "draws.dtbo")
        with open(dest, "wb") as fh:
            fh.write(b"OS-ORIGINAL")
        overlays.install("draws", t.boot, t.root)
        with open(dest + overlays.BACKUP_SUFFIX, "rb") as fh:
            self.assertEqual(fh.read(), b"OS-ORIGINAL")

    def test_a_first_install_leaves_no_backup(self):
        """Nothing was displaced, so there is nothing to keep — and a bogus
        backup would later 'restore' a file the OS never had."""
        t = _Tree(self.stack, [("draws.dtbo", b"OASIS")])
        overlays.install("draws", t.boot, t.root)
        self.assertFalse(os.path.exists(
            os.path.join(t.boot, "draws.dtbo" + overlays.BACKUP_SUFFIX)))

    def test_restore_undoes_the_override(self):
        t = _Tree(self.stack, [("draws.dtbo", b"OASIS")])
        dest = os.path.join(t.boot, "draws.dtbo")
        with open(dest, "wb") as fh:
            fh.write(b"OS-ORIGINAL")
        overlays.install("draws", t.boot, t.root)
        changed, why = overlays.restore("draws", t.boot)
        self.assertTrue(changed)
        self.assertEqual(why, "restored")
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"OS-ORIGINAL")
        self.assertFalse(os.path.exists(dest + overlays.BACKUP_SUFFIX),
                         "a consumed backup must not linger and re-restore")

    def test_restore_with_nothing_to_restore_is_not_an_error(self):
        t = _Tree(self.stack)
        self.assertEqual(overlays.restore("draws", t.boot), (False, "no-backup"))

    def test_the_off_switch_leaves_the_os_file_untouched(self):
        """Deleting the .dtbo from overlays/ is how we stop overriding. The OS's
        own overlay must survive that verbatim."""
        t = _Tree(self.stack)                     # nothing vendored
        dest = os.path.join(t.boot, "draws.dtbo")
        with open(dest, "wb") as fh:
            fh.write(b"NEW-KERNEL-OVERLAY")
        changed, why = overlays.install("draws", t.boot, t.root)
        self.assertFalse(changed)
        self.assertEqual(why, "no-vendored-copy")
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"NEW-KERNEL-OVERLAY")


class BundleDropPointTest(unittest.TestCase):
    """The build writes overlays where the installer looks for them.

    These are two files that must agree and have no other connection:
    scripts/create-oasis-offline.py chooses a download destination, and
    common/overlays.py chooses a search order. They were out of step by
    construction before the unification — the build dropped the panel overlay in
    features/cm4stack/packages/ while nothing else knew that path — so pin the
    agreement rather than trusting two comments to stay true.
    """

    def _build_script(self):
        path = os.path.join(overlays.REPO_ROOT, "scripts", "create-oasis-offline.py")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_the_panel_overlay_is_fetched_into_the_canonical_directory(self):
        src = self._build_script()
        start = src.index("def phase_cm4stack")
        body = src[start:start + 2000]
        self.assertIn('"overlays"', body,
                      "create-oasis-offline must drop m5stack-cm4.dtbo into "
                      "overlays/, the directory common/overlays.py searches first")
        self.assertNotIn('"displays", "cm4stack", "packages"', body,
                         "the panel overlay's old feature-local drop point is "
                         "no longer where the installer looks")

    def test_the_bundle_preserves_the_overlays_directory(self):
        """An incremental rebuild must not wipe the vendored blobs."""
        self.assertIn('"overlays",', self._build_script(),
                      "overlays/ must be in PRESERVE_IN_DEST")

    def test_the_canonical_directory_is_searched_first(self):
        """Order matters: a stale copy in a legacy path must never shadow the
        tracked one."""
        dirs = overlays._vendor_dirs("/repo")
        self.assertEqual(dirs[0], os.path.join("/repo", "overlays"))


class NoStaleDisplaysPathTest(unittest.TestCase):
    """`displays/cm4stack` became `features/cm4stack` on 2026-08-08.

    Most references to it fail loudly. Two would NOT have: the CI workflow's
    `displays/**` path filters (which would simply stop triggering builds) and
    scripts/bundle-ignore.windows (which would start shipping Pi-only panel code
    into the Windows tools bundle). Neither shows up as a test failure or an
    error message — they just quietly do the wrong thing — so they are pinned
    here instead.
    """

    _ALLOWED = (
        # Vendored third-party and prose that use the WORD, never the path.
        "static/graywolf-handbook/",
        "tools/antenna-calc.html",
        "CHANGELOG.md",
        # The one deliberate mention: overlays.py searches the pre-move location
        # last, because those .dtbo paths were gitignored and an in-place pull
        # leaves the old file behind untracked.
        "common/overlays.py",
        # This test.
        "tests/test_overlays.py",
    )

    def test_no_source_file_still_points_at_the_old_layout(self):
        import subprocess
        out = subprocess.run(["git", "grep", "-nI", "displays/"],
                             cwd=overlays.REPO_ROOT, capture_output=True,
                             text=True).stdout.splitlines()
        offenders = [ln for ln in out
                     if not any(ln.startswith(a) for a in self._ALLOWED)]
        self.assertEqual(offenders, [],
                         "these still reference the pre-move layout:\n  "
                         + "\n  ".join(offenders))

    def test_the_feature_directory_is_where_the_registry_says_it_is(self):
        """cm4stack was ALWAYS registered as a feature — a FeatureSpec in
        common/setup_registry.py and a Feature() in setup-oasis.py, next to
        rgb-cooling-hat. Only its directory disagreed. Pin the agreement."""
        script = os.path.join(overlays.REPO_ROOT, "features", "cm4stack",
                              "install-cm4stack.py")
        self.assertTrue(os.path.isfile(script), "the installer moved with it")
        for rel in ("common/setup_registry.py", "setup-oasis.py"):
            with open(os.path.join(overlays.REPO_ROOT, rel), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("features/cm4stack/install-cm4stack.py", text, rel)

    def test_the_windows_bundle_still_excludes_the_panel(self):
        """It was excluded via `displays/`; it must now be excluded via
        `features/`, or Pi-only panel code ships to Windows."""
        with open(os.path.join(overlays.REPO_ROOT, "scripts",
                               "bundle-ignore.windows"), encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()
                     and not ln.strip().startswith("#")]
        self.assertIn("features/", lines)

    def test_ci_still_builds_when_the_panel_changes(self):
        """The workflow watched `displays/**`. Dropping that is only safe
        because `features/**` covers the new location."""
        with open(os.path.join(overlays.REPO_ROOT, ".github", "workflows",
                               "offline-manifest.yml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('- "features/**"', text)
        self.assertNotIn('- "displays/**"', text)


class LicenceDisclosureTest(unittest.TestCase):
    """OASIS is MIT, but overlays/ ships GPL-2.0-derived binaries.

    Redistributing a binary built from GPL source carries an obligation to make
    the corresponding source available. The position is clean — unmodified
    upstream, publicly available, reproducible from the recipe above — but only
    if it is actually WRITTEN DOWN. A committed binary whose licence nobody
    stated is the failure mode; these assertions make it a build failure rather
    than something discovered by a downstream packager.
    """

    def _doc(self):
        with open(os.path.join(overlays.REPO_ROOT, "overlays", "SOURCE.md"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_the_gpl_binaries_carry_a_written_offer(self):
        text = self._doc()
        self.assertIn("GPL-2.0", text)
        self.assertIn("corresponding source", text.lower())
        self.assertIn("github.com/raspberrypi/linux", text,
                      "the offer must name the tree the source comes from")

    def test_it_says_these_are_not_under_the_project_licence(self):
        """The dangerous assumption is that everything in an MIT repo is MIT."""
        text = self._doc()
        self.assertIn("not", text.lower())
        self.assertIn("MIT", text, "SOURCE.md must say these are NOT MIT")

    def test_the_readme_points_at_the_offer(self):
        """Nobody reads overlays/SOURCE.md unprompted; the licence section of the
        README is where a packager or contributor actually looks."""
        with open(os.path.join(overlays.REPO_ROOT, "README.md"),
                  encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("overlays/SOURCE.md", readme)
        self.assertIn("GPL-2.0", readme)

    def test_the_project_licence_file_the_readme_links_to_exists(self):
        path = os.path.join(overlays.REPO_ROOT, "LICENSE")
        self.assertTrue(os.path.isfile(path),
                        "README links to ./LICENSE — a badge is not a licence")
