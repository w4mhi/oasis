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

    def test_script_hook_runs_teardown_script(self):
        fr = FakeRun()
        out = removal.apply({"script": "small-screen/uninstall.py"}, apply=True, run=fr)
        flat = [" ".join(c) for c in fr.calls]
        self.assertTrue(any("small-screen/uninstall.py" in c for c in flat))
        self.assertTrue(any("small-screen/uninstall.py" in c for c in out["changes"]))

    def test_empty_record_is_ok_noop(self):
        fr = FakeRun()
        out = removal.apply({}, apply=True, run=fr)
        self.assertEqual(fr.calls, [])
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
