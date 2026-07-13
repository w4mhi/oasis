import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
import app as oasis_app

class ConflictTest(unittest.TestCase):
    def test_units_allowlisted(self):
        self.assertIn("dump1090-fa", oasis_app._OASIS_SERVICES)
        self.assertIn("adsb-api", oasis_app._OASIS_SERVICES)
        self.assertIn("dump1090-fa", oasis_app._CONTROLLABLE_SERVICES)

    def test_starting_decoder_stops_all_sdr_users(self):
        stop = oasis_app._conflict_stop("dump1090-fa")
        for u in ("aprs-sdr-feed", "graywolf", "openwebrx"):
            self.assertIn(u, stop)

    def test_stopping_decoder_does_not_auto_restart_anything(self):
        # Operator preference: stopping ADS-B frees the SDR but does NOT
        # auto-restart APRS/OpenWebRX — operator brings the next mode up manually.
        self.assertEqual(oasis_app._conflict_restore("dump1090-fa"), [])

    def test_starting_openwebrx_also_stops_decoder(self):
        self.assertIn("dump1090-fa", oasis_app._conflict_stop("openwebrx"))

if __name__ == "__main__":
    unittest.main()
