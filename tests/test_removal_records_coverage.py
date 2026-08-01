#!/usr/bin/env python3
"""
test_removal_records_coverage.py — the enforcing "installers are the source of
truth" guard.

Every uninstallable feature in build_registry() must expose a removal_record()
(via FeatureSpec.removal_record_fn) that names the artifacts its installer creates.
This is what keeps installed-services.json's removal map from drifting: a new
feature cannot ship an installer without a matching removal record.

Groups are enabled task-by-task (Plan 1 Tasks 4-6); the final full-registry
assertion lands in Task 6.

Run directly:  python3 tests/test_removal_records_coverage.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from common import setup_registry as SR

# Features intentionally without a removal record (see the design's carve-outs).
EXCLUDED = {"server", "wikipedia"}

# The services group delivered in Task 4 (satellites is data-style: advisory only).
SERVICES_GROUP = {
    "graywolf", "winlink", "kiwix", "openwebrx", "adsb",
    "webssh", "service-controls", "ap-fallback", "rgb-cooling-hat", "argon-fan",
}

# The hardware/config group delivered in Task 5.
HARDWARE_GROUP = {
    "rtc", "dra-pi-rx-led", "cm4stack", "gps", "gps-l76x",
    "pi-headless", "pi-local-monitor", "pi-oasis-dashboard",
}


class RemovalRecordCoverageTest(unittest.TestCase):
    def setUp(self):
        self.reg = SR.build_registry(_REPO, payload={})

    def test_services_group_has_removal_records(self):
        for key in SERVICES_GROUP:
            spec = self.reg.get(key)
            self.assertIsNotNone(spec, f"{key} missing from registry")
            self.assertIsNotNone(spec.removal_record_fn, f"{key} has no removal_record_fn")
            rec = spec.removal_record_fn()
            self.assertTrue(rec.get("services") or rec.get("files") or rec.get("script"),
                            f"{key} removal record is empty")

    def test_service_records_name_a_unit_or_file(self):
        # Every service-group feature tears down at least one unit or file.
        for key in SERVICES_GROUP:
            rec = self.reg[key].removal_record_fn()
            self.assertTrue(rec.get("services") or rec.get("files"),
                            f"{key} names no service unit or file")

    def test_hardware_group_has_removal_records(self):
        for key in HARDWARE_GROUP:
            spec = self.reg.get(key)
            self.assertIsNotNone(spec, f"{key} missing from registry")
            self.assertIsNotNone(spec.removal_record_fn, f"{key} has no removal_record_fn")
            rec = spec.removal_record_fn()
            self.assertTrue(rec, f"{key} removal record is empty")

    def test_winlink_removes_credential_and_profiles(self):
        # A factory reset must delete the Winlink password (pat/config.json) and
        # the generated direwolf modem profiles — a credential is not kept data.
        files = self.reg["winlink"].removal_record_fn().get("files", [])
        self.assertTrue(any(f.endswith("/.config/pat/config.json") for f in files),
                        f"winlink must remove pat/config.json (the password): {files}")
        self.assertTrue(any("/.config/direwolf/" in f and f.endswith(".conf") for f in files),
                        f"winlink must remove its direwolf profiles: {files}")

    def test_ap_fallback_keeps_the_hotspot_profile(self):
        # The OASIS-AP NetworkManager profile is the off-grid connectivity
        # lifeline (public PSK) and nothing recreates it, so teardown must NOT
        # delete it — it names no OASIS-AP file, and advises it's left in place.
        rec = self.reg["ap-fallback"].removal_record_fn()
        self.assertFalse(any("OASIS-AP" in f for f in rec.get("files", [])),
                         "ap-fallback must not delete the OASIS-AP hotspot profile")
        self.assertTrue(any("OASIS-AP" in n for n in rec.get("notes", [])),
                        "ap-fallback should advise that OASIS-AP is left in place")

    def test_adsb_stops_the_decoder_daemon(self):
        # The dump1090-fa decoder (apt-provided) must be stopped/disabled on
        # removal, not left running. The leave-apt policy keeps the package; the
        # unit lives in /usr/lib, so the /etc rm is a harmless no-op.
        services = self.reg["adsb"].removal_record_fn().get("services", [])
        self.assertIn("dump1090-fa", services,
                      "adsb removal must stop+disable the dump1090-fa decoder")

    def test_config_features_flag_reboot(self):
        # config.txt-touching removals must require a reboot to take effect.
        for key in ("rtc", "dra-pi-rx-led", "cm4stack", "gps-l76x"):
            rec = self.reg[key].removal_record_fn()
            self.assertTrue(rec.get("requires_reboot"), f"{key} should require reboot")

    def test_kiosk_features_use_script_hook(self):
        for key in ("pi-headless", "pi-local-monitor", "pi-oasis-dashboard"):
            rec = self.reg[key].removal_record_fn()
            self.assertTrue(rec.get("script"), f"{key} should use a teardown script")

    def test_satellites_is_data_style_advisory(self):
        rec = self.reg["satellites"].removal_record_fn()
        self.assertTrue(rec.get("data_paths"), "satellites should advise on its TLE cache")
        self.assertFalse(rec.get("services"), "satellites has no dedicated service to stop")

    def test_data_features_are_advisory_only(self):
        # Data features remove no services/files — only advise (data_paths/notes).
        for key in ("fcc", "repeaterbook", "forms"):
            rec = self.reg[key].removal_record_fn()
            self.assertTrue(rec.get("data_paths") or rec.get("notes"),
                            f"{key} should carry an advisory")
            self.assertFalse(rec.get("services") or rec.get("files"),
                             f"{key} is data-only; it must not remove services/files")

    def test_every_non_excluded_feature_has_a_record(self):
        # The enforcing guard: no feature can ship without a removal record (or an
        # explicit exclusion). This is what keeps the manifest's removal map from
        # drifting from what the installers create.
        missing = sorted(k for k, spec in self.reg.items()
                         if k not in EXCLUDED and spec.removal_record_fn is None)
        self.assertEqual(missing, [], f"features without removal_record_fn: {missing}")

    def test_every_record_is_callable_and_returns_dict(self):
        for key, spec in self.reg.items():
            if spec.removal_record_fn is None:
                continue
            rec = spec.removal_record_fn()
            self.assertIsInstance(rec, dict, f"{key} removal_record must return a dict")


if __name__ == "__main__":
    unittest.main()
