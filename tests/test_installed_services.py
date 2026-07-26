#!/usr/bin/env python3
"""
test_installed_services.py — self-tests for common/installed_services.py

The schema module owns installed-services.json: the `features` list the dashboard
reads and the per-feature `removal` map written by installers. Runs off-Pi.

Run directly:  python3 tests/test_installed_services.py
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

from common import installed_services as ISVC
from common import config_paths


class InstalledServicesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(self.tmp), exist_ok=True)

    def _seed(self, obj):
        with open(config_paths.installed_services_json(self.tmp), "w") as fh:
            json.dump(obj, fh)

    def test_read_missing_returns_empty(self):
        self.assertEqual(ISVC.read(self.tmp), {})

    def test_installed_features_reads_list(self):
        self._seed({"features": ["kiwix", "graywolf"]})
        self.assertEqual(ISVC.installed_features(self.tmp), {"kiwix", "graywolf"})

    def test_add_installed_unions_and_stores_records(self):
        self._seed({"features": ["kiwix"]})
        ISVC.add_installed(self.tmp, {"webssh"}, {"webssh": {"services": ["webssh"]}})
        self.assertEqual(ISVC.installed_features(self.tmp), {"kiwix", "webssh"})
        self.assertEqual(ISVC.removal_map(self.tmp)["webssh"], {"services": ["webssh"]})

    def test_add_installed_legacy_list_gains_removal_map(self):
        self._seed({"features": ["kiwix"]})  # legacy: no removal key
        ISVC.add_installed(self.tmp, {"kiwix"}, {"kiwix": {"services": ["kiwix"]}})
        self.assertIn("kiwix", ISVC.removal_map(self.tmp))

    def test_remove_installed_drops_key_and_record(self):
        self._seed({"features": ["kiwix", "webssh"],
                    "removal": {"kiwix": {"services": ["kiwix"]},
                                "webssh": {"services": ["webssh"]}}})
        ISVC.remove_installed(self.tmp, {"webssh"})
        self.assertEqual(ISVC.installed_features(self.tmp), {"kiwix"})
        self.assertNotIn("webssh", ISVC.removal_map(self.tmp))

    def test_write_is_atomic_and_sorted(self):
        ISVC.write(self.tmp, {"webssh", "kiwix"}, {})
        with open(config_paths.installed_services_json(self.tmp)) as fh:
            data = json.load(fh)
        self.assertEqual(data["features"], ["kiwix", "webssh"])  # sorted
        self.assertIn("updated", data)

    def test_add_installed_never_shrinks(self):
        self._seed({"features": ["kiwix", "graywolf"]})
        ISVC.add_installed(self.tmp, {"webssh"}, {})   # webssh added, none dropped
        self.assertEqual(ISVC.installed_features(self.tmp),
                         {"kiwix", "graywolf", "webssh"})


class ReconcileTest(unittest.TestCase):
    """reconcile() self-heals the ledger: drops features whose install artifacts
    are all gone, but only on positive evidence and only on Linux."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(self.tmp), exist_ok=True)

    def _seed(self, obj):
        with open(config_paths.installed_services_json(self.tmp), "w") as fh:
            json.dump(obj, fh)

    def _as_linux(self):
        return mock.patch.object(ISVC.sys, "platform", "linux")

    def test_off_linux_is_noop(self):
        # A darwin dev box must never drop anything, even with a dead marker.
        self._seed({"features": ["pi-headless"],
                    "removal": {"pi-headless": {"installed_marker": ["/no/such/unit"]}}})
        with mock.patch.object(ISVC.sys, "platform", "darwin"):
            self.assertEqual(ISVC.reconcile(self.tmp), [])
        self.assertEqual(ISVC.installed_features(self.tmp), {"pi-headless"})

    def test_drops_feature_when_installed_marker_absent(self):
        # pi-headless's autostart sentinel (oasis.service unit) is gone -> torn down.
        self._seed({"features": ["pi-headless", "kiwix"],
                    "removal": {"pi-headless": {"installed_marker": ["/no/such/unit.service"]},
                                "kiwix": {"services": ["kiwix"]}}})
        with self._as_linux():
            dropped = ISVC.reconcile(self.tmp)
        self.assertIn("pi-headless", dropped)
        self.assertNotIn("pi-headless", ISVC.installed_features(self.tmp))
        self.assertNotIn("pi-headless", ISVC.removal_map(self.tmp))

    def test_keeps_feature_when_marker_present(self):
        # A real, existing sentinel file means the feature is still installed.
        marker = os.path.join(self.tmp, "oasis.service")
        with open(marker, "w") as fh:
            fh.write("[Unit]\n")
        self._seed({"features": ["pi-headless"],
                    "removal": {"pi-headless": {"installed_marker": [marker]}}})
        with self._as_linux():
            self.assertEqual(ISVC.reconcile(self.tmp), [])
        self.assertEqual(ISVC.installed_features(self.tmp), {"pi-headless"})

    def test_skips_feature_with_no_reliable_marker(self):
        # A record whose only artifacts are user-home files (never a sole basis
        # for dropping — the server user can't always stat them) is left alone.
        self._seed({"features": ["some-feat"],
                    "removal": {"some-feat": {"files": ["/home/pi/.config/autostart/x.desktop"]}}})
        with self._as_linux():
            self.assertEqual(ISVC.reconcile(self.tmp), [])
        self.assertEqual(ISVC.installed_features(self.tmp), {"some-feat"})

    def test_skips_feature_with_no_record(self):
        # No removal record -> we can't prove absence -> keep it.
        self._seed({"features": ["mystery"], "removal": {}})
        with self._as_linux():
            self.assertEqual(ISVC.reconcile(self.tmp), [])
        self.assertEqual(ISVC.installed_features(self.tmp), {"mystery"})

    def test_present_extra_file_keeps_feature_with_reliable_marker(self):
        # Reliable marker gone but a declarative file still present -> not all
        # markers absent -> keep (conservative).
        present = os.path.join(self.tmp, "still-here")
        with open(present, "w") as fh:
            fh.write("x")
        self._seed({"features": ["feat"],
                    "removal": {"feat": {"services": ["ghost"], "files": [present]}}})
        with self._as_linux():
            self.assertEqual(ISVC.reconcile(self.tmp), [])
        self.assertEqual(ISVC.installed_features(self.tmp), {"feat"})


if __name__ == "__main__":
    unittest.main()
