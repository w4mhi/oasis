import os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import hardware_detect as HD

class SdrServicesActiveTest(unittest.TestCase):
    def test_lists_active_sdr_units_only(self):
        active = HD.sdr_services_active(is_active=lambda u: u == "dump1090-fa")
        self.assertEqual(active, ["dump1090-fa"])

    def test_empty_when_none_active(self):
        self.assertEqual(HD.sdr_services_active(is_active=lambda u: False), [])

class CanBurnSerialTest(unittest.TestCase):
    def test_refuses_when_an_sdr_service_is_active(self):
        ok, reason = HD.can_burn_serial(
            [{"index": 0, "serial": "00000001"}], is_active=lambda u: u == "openwebrx")
        self.assertFalse(ok)
        self.assertIn("openwebrx", reason)

    def test_refuses_when_no_candidate(self):
        ok, reason = HD.can_burn_serial([], is_active=lambda u: False)
        self.assertFalse(ok)
        self.assertIn("no", reason.lower())

    def test_refuses_when_ambiguous(self):
        ok, reason = HD.can_burn_serial(
            [{"index": 0, "serial": "00000001"}, {"index": 1, "serial": "00000001"}],
            is_active=lambda u: False)
        self.assertFalse(ok)
        self.assertIn("ambig", reason.lower())

    def test_allows_exactly_one_candidate_and_nothing_active(self):
        ok, reason = HD.can_burn_serial(
            [{"index": 0, "serial": "00000001"}], is_active=lambda u: False)
        self.assertTrue(ok)

class BurnSerialTest(unittest.TestCase):
    def test_noop_on_non_linux(self):
        HD.burn_serial("1090")  # must not raise

    def test_answers_the_rtl_eeprom_prompt_on_stdin(self):
        # rtl_eeprom blocks on a [y/n] confirmation before writing; without an
        # answer piped in, the web-route call hangs or silently skips the write.
        with mock.patch.object(HD.sys, "platform", "linux"), \
             mock.patch.object(HD.subprocess, "run") as mocked_run:
            HD.burn_serial("00001000")
        args, kwargs = mocked_run.call_args
        self.assertEqual(args[0], ["sudo", "rtl_eeprom", "-s", "00001000"])
        self.assertEqual(kwargs.get("input"), "y\n")

if __name__ == "__main__":
    unittest.main()
