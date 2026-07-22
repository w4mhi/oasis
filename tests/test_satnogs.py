import json, os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
import satnogs  # noqa: E402


def _fx(name):
    with open(os.path.join(_HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.sats = satnogs.parse_satellites(_fx("satnogs-satellites.json"))
        self.txs = satnogs.parse_transmitters(_fx("satnogs-transmitters.json"))

    def test_parse_satellites_keeps_alive_with_norad(self):
        self.assertIn(25544, self.sats)
        self.assertIn(33591, self.sats)
        self.assertNotIn(99999, self.sats)          # not alive
        self.assertEqual(self.sats[25544]["name"], "ISS (ZARYA)")
        self.assertEqual(self.sats[25544]["sat_id"], "ISS-UUID")

    def test_parse_transmitters_active_and_in_range(self):
        iss = self.txs[25544]
        modes = sorted(t["mode"] for t in iss)
        # inactive FM dropped; 15 GHz Ku QPSK dropped (out of RTL range)
        self.assertEqual(modes, ["AFSK", "FM"])

    def test_out_of_range_downlink_dropped(self):
        self.assertTrue(all(t["downlink"]["freq_mhz"] <= 1766
                            for t in self.txs[25544]))

    def test_downlink_uplink_shape(self):
        fm = next(t for t in self.txs[25544] if t["mode"] == "FM")
        self.assertEqual(fm["downlink"], {"freq_mhz": 145.8, "freq_high_mhz": None})
        self.assertEqual(fm["uplink"], {"freq_mhz": 145.99, "freq_high_mhz": None})
        self.assertEqual(fm["description"], "Mode V/U FM Voice Repeater")

    def test_transponder_passband(self):
        tp = self.txs[44909][0]
        self.assertEqual(tp["type"], "Transponder")
        self.assertTrue(tp["invert"])
        self.assertEqual(tp["downlink"]["freq_high_mhz"], 435.67)
        self.assertEqual(tp["uplink"]["freq_high_mhz"], 145.995)


class LabelsTest(unittest.TestCase):
    def test_labels_from_modes(self):
        self.assertEqual(satnogs.labels_for(
            [{"mode": "APT", "type": "Transmitter", "description": ""}], 33591),
            ["WEATHER"])

    def test_messy_mode_token_matches(self):
        # "GMSK USP" must still resolve to DATA via token matching.
        self.assertEqual(satnogs.labels_for(
            [{"mode": "GMSK USP", "type": "Transmitter", "description": "tlm"}], 55555),
            ["DATA"])

    def test_aprs_from_description_suppresses_data(self):
        # mode AFSK alone is DATA, but description "APRS" makes it APRS, not DATA.
        labs = satnogs.labels_for(
            [{"mode": "AFSK", "type": "Transceiver", "description": "Mode V APRS"}], 25544)
        self.assertIn("APRS", labs)
        self.assertNotIn("DATA", labs)

    def test_aprs_and_separate_telemetry_keep_both(self):
        labs = satnogs.labels_for([
            {"mode": "AFSK", "type": "Transceiver", "description": "Mode V APRS"},
            {"mode": "BPSK", "type": "Transmitter", "description": "Telemetry"}], 12345)
        self.assertIn("APRS", labs)
        self.assertIn("DATA", labs)   # the BPSK transmitter still contributes DATA

    def test_iss_is_crewed_and_vocab_ordered(self):
        labs = satnogs.labels_for(
            [{"mode": "FM", "type": "Transceiver", "description": "voice"}], 25544)
        self.assertIn("CREWED", labs)
        self.assertIn("VOICE", labs)
        # order follows the closed vocabulary, never insertion order
        self.assertEqual(labs, [l for l in satnogs.LABELS if l in labs])

    def test_transponder_type_implies_linear(self):
        labs = satnogs.labels_for(
            [{"mode": "SSB", "type": "Transponder", "description": ""}], 44909)
        self.assertIn("LINEAR", labs)
        self.assertIn("SSB", labs)

    def test_unmapped_mode_yields_no_label(self):
        self.assertEqual(satnogs.labels_for(
            [{"mode": "WEIRDMODE", "type": "Transmitter", "description": ""}], 12345),
            [])


if __name__ == "__main__":
    unittest.main()
