#!/usr/bin/env python3
"""
test_installer_worker_remove.py — the worker's action:remove branch.

The privileged worker gains a remove action that runs common/removal.py's apply()
as root and reports the changes/advisory. Non-removable features (server,
wikipedia) are refused. Runs off-Pi with removal/installed_services mocked.

Run directly:  python3 tests/test_installer_worker_remove.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_MOD = os.path.join(_REPO, "scripts", "oasis_installer_worker.py")
_spec = importlib.util.spec_from_file_location("oasis_installer_worker", _MOD)
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)


class WorkerRemoveTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp()
        self._orig_q = worker.QUEUE_DIR
        worker.QUEUE_DIR = self.q

    def tearDown(self):
        worker.QUEUE_DIR = self._orig_q

    def _job(self, obj):
        p = os.path.join(self.q, "j1.job.json")
        with open(p, "w") as fh:
            json.dump(obj, fh)
        return p

    def _result(self):
        with open(os.path.join(self.q, "j1.result.json")) as fh:
            return json.load(fh)

    def test_remove_calls_apply_and_reports_advisory(self):
        job = self._job({"feature": "kiwix", "action": "remove"})
        fake_apply = mock.Mock(return_value={"ok": True, "changes": ["stop kiwix"],
                                             "advisory": ["ZIMs left in place"],
                                             "requires_reboot": False})
        with mock.patch.object(worker, "removal") as rmod, \
             mock.patch.object(worker, "installed_services") as isvc, \
             mock.patch.object(worker, "removal_backfill") as bf:
            rmod.apply = fake_apply
            isvc.removal_map.return_value = {"kiwix": {"services": ["kiwix"]}}
            bf.record_for.return_value = None
            worker._process_job(job)
        res = self._result()
        self.assertTrue(res["ok"])
        self.assertIn("ZIMs left in place", res["advisory"])
        fake_apply.assert_called_once()
        self.assertTrue(fake_apply.call_args.kwargs.get("apply"))

    def test_remove_rejects_unremovable_feature(self):
        job = self._job({"feature": "server", "action": "remove"})
        worker._process_job(job)
        res = self._result()
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
