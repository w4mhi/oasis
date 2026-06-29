#!/usr/bin/env python3
"""
test_remove_oasis.py — self-tests for scripts/remove-oasis.py

Runs off-Pi (no sudo, no systemctl): exercises the inventory tables, the pure
config.txt surgery, the read-only data advisory, and the dry-run safety guarantee
(no mutating commands are issued when --apply is absent).

Run directly:  python3 scripts/tests/test_remove_oasis.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest

# ── Import the hyphenated module by path. ─────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_MOD_PATH = os.path.join(_SCRIPTS, "remove-oasis.py")
_spec = importlib.util.spec_from_file_location("remove_oasis", _MOD_PATH)
remove_oasis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(remove_oasis)


class TestInventory(unittest.TestCase):
    def test_services_complete(self):
        for svc in ("oasis", "oasis-panel", "graywolf", "graywolf-api", "pat",
                    "kiwix", "webssh", "aprs-sdr-feed", "openwebrx", "rgb-cooling-hat"):
            self.assertIn(svc, remove_oasis.SERVICES)

    def test_files_and_dirs(self):
        self.assertIn("/etc/sudoers.d/oasis-service-controls", remove_oasis.FILES)
        self.assertIn("/opt/rgb-cooling-hat", remove_oasis.DIRS)


class TestStripConfig(unittest.TestCase):
    SAMPLE = (
        "dtparam=audio=on\n"
        "dtparam=i2c_arm=on\n"
        "dtoverlay=i2c-rtc,ds3231\n"
        "# --- OASIS DRA-Pi-Zero (managed by scripts/enable-dra-pi.py) ---\n"
        "dtparam=audio=off\n"
        "dtoverlay=audioinjector-wm8731-audio\n"
        "# --- end OASIS DRA-Pi-Zero ---\n"
        "# --- OASIS CM4Stack (managed by scripts/enable-cm4stack.py) ---\n"
        "dtoverlay=m5stack-cm4\n"
        "# --- end OASIS CM4Stack ---\n"
        "dtoverlay=vc4-kms-v3d\n"
    )

    def test_removes_oasis_only(self):
        out, changes = remove_oasis.strip_oasis_config(self.SAMPLE)
        self.assertNotIn("audioinjector-wm8731-audio", out)
        self.assertNotIn("m5stack-cm4", out)
        self.assertNotIn("i2c-rtc,ds3231", out)
        self.assertNotIn("OASIS DRA-Pi", out)
        self.assertNotIn("OASIS CM4Stack", out)
        # preserved:
        self.assertIn("dtparam=i2c_arm=on", out)
        self.assertIn("dtparam=audio=on", out)
        self.assertIn("dtoverlay=vc4-kms-v3d", out)
        self.assertTrue(changes)

    def test_idempotent_on_clean(self):
        clean = "dtparam=i2c_arm=on\ndtoverlay=vc4-kms-v3d\n"
        out, changes = remove_oasis.strip_oasis_config(clean)
        self.assertEqual(out.strip(), clean.strip())
        self.assertEqual(changes, [])


class TestDataAdvisory(unittest.TestCase):
    def test_lists_only_existing(self):
        with tempfile.TemporaryDirectory() as d:
            present = os.path.join(d, "maps")
            os.makedirs(present)
            with open(os.path.join(present, "x.mbtiles"), "wb") as fh:
                fh.write(b"0" * 2048)
            orig = remove_oasis.DATA_PATHS
            remove_oasis.DATA_PATHS = [present, os.path.join(d, "absent")]
            try:
                adv = remove_oasis.data_advisory()
            finally:
                remove_oasis.DATA_PATHS = orig
            paths = [p for p, _ in adv]
            self.assertIn(present, paths)
            self.assertNotIn(os.path.join(d, "absent"), paths)


class TestDryRunIsSafe(unittest.TestCase):
    def test_dry_run_makes_no_mutating_calls(self):
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        orig = remove_oasis._run
        remove_oasis._run = fake_run
        try:
            remove_oasis.remove_services(apply=False)
            remove_oasis.remove_files(apply=False)
            remove_oasis.remove_config(apply=False)
            remove_oasis.restore_hwclock(apply=False)
        finally:
            remove_oasis._run = orig
        flat = " ".join(" ".join(str(x) for x in c) for c in calls)
        for bad in ("sudo", " rm ", "disable", "stop", "tee"):
            self.assertNotIn(bad, flat)


if __name__ == "__main__":
    unittest.main()
