#!/usr/bin/env python3
"""
test_removal_runner.py — self-tests for common/removal.py's apply() runner.

apply() undoes one feature's removal record (see common/installed_services.py for
where records are stored). Runs off-Pi with an injected fake `run` so no real
systemctl/rm is ever issued. Verifies dry-run safety, the stop/disable/remove
sequence, the never-touch-data guarantee, and reboot propagation.

Run directly:  python3 tests/test_removal_runner.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

from common import removal


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))

        class R:  # mimic subprocess.CompletedProcess enough for the runner
            stdout = ""
            returncode = 0
        return R()


class RemovalRunnerTest(unittest.TestCase):
    def test_dry_run_changes_nothing(self):
        fr = FakeRun()
        rec = {"services": ["kiwix"], "files": ["/usr/local/bin/kiwix-serve"]}
        out = removal.apply(rec, apply=False, run=fr)
        self.assertEqual(fr.calls, [])              # nothing executed
        self.assertTrue(any("kiwix" in c for c in out["changes"]))

    def test_apply_stops_disables_removes_service(self):
        fr = FakeRun()
        removal.apply({"services": ["kiwix"]}, apply=True, run=fr)
        flat = [" ".join(c) for c in fr.calls]
        self.assertTrue(any("systemctl stop kiwix" in c for c in flat))
        self.assertTrue(any("systemctl disable kiwix" in c for c in flat))
        self.assertTrue(any("rm -f /etc/systemd/system/kiwix.service" in c for c in flat))

    def test_data_paths_are_advisory_only(self):
        fr = FakeRun()
        out = removal.apply({"data_paths": ["/var/lib/graywolf"]}, apply=True, run=fr)
        self.assertEqual(fr.calls, [])              # never touched
        self.assertTrue(any("/var/lib/graywolf" in a for a in out["advisory"]))

    def test_requires_reboot_propagates(self):
        out = removal.apply({"config_lines": ["dtoverlay=i2c-rtc,ds3231"],
                             "requires_reboot": True}, apply=False)
        self.assertTrue(out["requires_reboot"])

    def test_script_hook_runs_teardown_argv(self):
        fr = FakeRun()
        out = removal.apply({"script": ["/repo/scripts/enable-autostart-pi.py", "--disable"]},
                            apply=True, run=fr)
        flat = [" ".join(c) for c in fr.calls]
        self.assertTrue(any("enable-autostart-pi.py --disable" in c for c in flat))
        self.assertFalse(any("--apply" in c for c in flat))  # no forced --apply
        self.assertTrue(any("enable-autostart-pi.py" in c for c in out["changes"]))

    def test_script_hook_accepts_bare_string(self):
        fr = FakeRun()
        removal.apply({"script": "/repo/oasis-dashboard/uninstall.py"}, apply=True, run=fr)
        flat = [" ".join(c) for c in fr.calls]
        self.assertTrue(any("oasis-dashboard/uninstall.py" in c for c in flat))

    def test_notes_surface_as_advisory(self):
        out = removal.apply({"notes": ["gpsd/chrony reconfig left in place"]}, apply=False)
        self.assertIn("gpsd/chrony reconfig left in place", out["advisory"])

    def test_empty_record_is_ok_noop(self):
        fr = FakeRun()
        out = removal.apply({}, apply=True, run=fr)
        self.assertEqual(fr.calls, [])
        self.assertTrue(out["ok"])


class StripConfigTest(unittest.TestCase):
    SAMPLE = (
        "dtparam=audio=on\n"
        "dtparam=i2c_arm=on\n"
        "dtoverlay=i2c-rtc,ds3231\n"
        "# --- OASIS DRA-Pi-Zero (managed by scripts/enable-dra-pi.py) ---\n"
        "dtparam=audio=off\n"
        "dtoverlay=audioinjector-wm8731-audio\n"
        "# --- end OASIS DRA-Pi-Zero ---\n"
        "# --- OASIS CM4Stack (managed by cm4stack/install-cm4stack.py) ---\n"
        "dtoverlay=m5stack-cm4\n"
        "# --- end OASIS CM4Stack ---\n"
        "dtoverlay=vc4-kms-v3d\n"
    )
    BLOCKS = [
        ("# --- OASIS DRA-Pi-Zero (managed by scripts/enable-dra-pi.py) ---",
         "# --- end OASIS DRA-Pi-Zero ---"),
        ("# --- OASIS CM4Stack (managed by cm4stack/install-cm4stack.py) ---",
         "# --- end OASIS CM4Stack ---"),
    ]
    LINES = ["dtoverlay=i2c-rtc,ds3231"]

    def test_removes_oasis_blocks_and_lines_only(self):
        out, changes = removal.strip_config(self.SAMPLE, self.BLOCKS, self.LINES)
        self.assertNotIn("audioinjector-wm8731-audio", out)
        self.assertNotIn("m5stack-cm4", out)
        self.assertNotIn("i2c-rtc,ds3231", out)
        # preserved:
        self.assertIn("dtparam=i2c_arm=on", out)
        self.assertIn("dtparam=audio=on", out)
        self.assertIn("dtoverlay=vc4-kms-v3d", out)
        self.assertTrue(changes)

    def test_idempotent_on_clean(self):
        clean = "dtparam=i2c_arm=on\ndtoverlay=vc4-kms-v3d\n"
        out, changes = removal.strip_config(clean, self.BLOCKS, self.LINES)
        self.assertEqual(out.strip(), clean.strip())
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main()


class ConfigSubsTest(unittest.TestCase):
    """Bench 2026-08-06: the DRA-Pi installer MUTATES stock config.txt lines it
    does not own — it comments out `dtparam=audio=on` and appends `,noaudio` to
    the vc4 overlay. Neither sits inside its BEGIN/END markers, so config_blocks
    removal cannot undo them: uninstalling left the Pi with NO sound cards at
    all, which read as "DRAWS is broken". config_subs is the reversal primitive
    those installers were missing."""

    def test_substitutes_a_line(self):
        text = "a\n# dtparam=audio=on   # disabled by OASIS DRA-Pi\nb\n"
        out, changes = removal.strip_config(
            text, [], [],
            subs=[["# dtparam=audio=on   # disabled by OASIS DRA-Pi",
                   "dtparam=audio=on"]])
        self.assertIn("dtparam=audio=on", out.splitlines())
        self.assertNotIn("# dtparam=audio=on   # disabled by OASIS DRA-Pi",
                         out.splitlines())
        self.assertTrue(changes)

    def test_absent_line_is_a_no_op(self):
        text = "a\nb\n"
        out, changes = removal.strip_config(text, [], [], subs=[["x", "y"]])
        self.assertEqual(out, text)
        self.assertEqual(changes, [])

    def test_is_idempotent(self):
        subs = [["dtoverlay=vc4-kms-v3d,noaudio", "dtoverlay=vc4-kms-v3d"]]
        text = "dtoverlay=vc4-kms-v3d,noaudio\n"
        once, _ = removal.strip_config(text, [], [], subs=subs)
        twice, changes = removal.strip_config(once, [], [], subs=subs)
        self.assertEqual(once, twice)
        self.assertEqual(changes, [])

    def test_preserves_indentation(self):
        out, _ = removal.strip_config("   dtoverlay=vc4-kms-v3d,noaudio\n", [], [],
                                      subs=[["dtoverlay=vc4-kms-v3d,noaudio",
                                             "dtoverlay=vc4-kms-v3d"]])
        self.assertEqual(out, "   dtoverlay=vc4-kms-v3d\n")

    def test_subs_run_alongside_blocks_and_lines(self):
        text = ("dtparam=audio=off\n"
                "# --- B ---\ninner\n# --- E ---\n"
                "dtoverlay=vc4-kms-v3d,noaudio\n")
        out, _ = removal.strip_config(
            text, [["# --- B ---", "# --- E ---"]], ["dtparam=audio=off"],
            subs=[["dtoverlay=vc4-kms-v3d,noaudio", "dtoverlay=vc4-kms-v3d"]])
        self.assertEqual(out.strip(), "dtoverlay=vc4-kms-v3d")
