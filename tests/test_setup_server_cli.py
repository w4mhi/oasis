import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

_MOD_PATH = os.path.join(_ROOT, "scripts", "setup-server.py")
_spec = importlib.util.spec_from_file_location("setup_server", _MOD_PATH)
setup_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_server)

from common import setup_engine as SE


class SetupServerCLITest(unittest.TestCase):
    def test_features_flag_expands_service_controls_dependency(self):
        with redirect_stdout(io.StringIO()) as out:
            rc = setup_server.main(["--plan", "--features", "service-controls"])
        txt = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("server", txt)
        self.assertIn("service-controls", txt)

    def test_json_mode_emits_job_started_and_job_finished(self):
        with redirect_stdout(io.StringIO()) as out:
            rc = setup_server.main(["--json", "--features", "server"])
        txt = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn('"event": "job_started"', txt)
        self.assertIn('"event": "job_finished"', txt)

    def test_check_flag_uses_legacy_check_path(self):
        with mock.patch.object(setup_server.S, "run") as mocked:
            rc = setup_server.main(["--check"])
        self.assertEqual(rc, 0)
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertTrue(kwargs.get("check_mode"))

    def test_invalid_feature_exits_nonzero_with_reason(self):
        with redirect_stdout(io.StringIO()) as out:
            rc = setup_server.main(["--plan", "--features", "bad-feature"])
        self.assertNotEqual(rc, 0)
        self.assertIn("unknown feature", out.getvalue())


if __name__ == "__main__":
    unittest.main()
