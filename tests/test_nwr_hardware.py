import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from common import hardware as HW  # noqa: E402


class NwrServiceTest(unittest.TestCase):
    def test_nwr_is_a_known_service(self):
        self.assertIn("nwr", HW.SERVICE_UNITS)
        self.assertEqual(HW.SERVICE_UNITS["nwr"], ["nwr-listen"])

    def test_nwr_takes_an_rtl_sdr_and_nothing_else(self):
        self.assertEqual(HW.DEVICE_KIND_FOR_SERVICE["nwr"], {"rtl-sdr"})

    def test_nwr_listen_is_synthetic(self):
        # It must never be handed to systemctl: `systemctl stop nwr-listen`
        # exits fine and does nothing, which is exactly how a running capture
        # would report as stopped while rtl_fm kept the dongle.
        self.assertIn("nwr-listen", HW.SYNTHETIC_UNITS)
        self.assertIn("satellites-listen", HW.SYNTHETIC_UNITS)

    def test_startable_units_excludes_the_synthetic_token(self):
        inv = HW._empty_inventory()
        inv.assignments["nwr"] = "rtl-1"
        self.assertEqual(HW.startable_units(inv, "nwr"), [])


class WrapperChainTest(unittest.TestCase):
    """The two synthetic tokens must BOTH be answerable by one is_active."""

    def test_chained_wrappers_answer_their_own_token_and_delegate(self):
        base = lambda u: u == "dump1090-fa"                      # noqa: E731

        def sat_wrapper(inner):
            def _w(unit):
                return True if unit == "satellites-listen" else inner(unit)
            return _w

        def nwr_wrapper(inner):
            def _w(unit):
                return False if unit == "nwr-listen" else inner(unit)
            return _w

        chained = nwr_wrapper(sat_wrapper(base))
        self.assertTrue(chained("satellites-listen"))
        self.assertFalse(chained("nwr-listen"))
        self.assertTrue(chained("dump1090-fa"))
        self.assertFalse(chained("pat-direwolf"))


if __name__ == "__main__":
    unittest.main()
