#!/usr/bin/env python3
"""The rtl-sdr tools feature owns the DVB-driver blacklist teardown (the feed's
removal_record no longer carries it after the rtl-feed/rtl-sdr split)."""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(os.path.dirname(_HERE), "features", "rtl-sdr", "rtl_sdr.py")
_spec = importlib.util.spec_from_file_location("rtl_sdr_mod", _PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class RtlSdrRemovalRecordTest(unittest.TestCase):
    def test_owns_the_dvb_blacklist(self):
        rec = mod.removal_record()
        self.assertIn(mod.BLACKLIST_FILE, rec.get("files", []))

    def test_records_no_service(self):
        # The apt tools are left installed (leave-apt); no unit to stop here — the
        # aprs-sdr-feed unit belongs to the rtl-feed service.
        self.assertEqual(mod.removal_record().get("services", []), [])


if __name__ == "__main__":
    unittest.main()
