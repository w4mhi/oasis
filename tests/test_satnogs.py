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

    def test_weather_from_137mhz_band(self):
        # NOAA/METEOR downlinks in 137-138 MHz are WEATHER regardless of the
        # (non-APT) mode SatNOGS assigns them.
        labs = satnogs.labels_for([{"mode": "DSB", "type": "Transmitter",
            "description": "Direct Sounder Broadcast",
            "downlink": {"freq_mhz": 137.35, "freq_high_mhz": None}}], 25338)
        self.assertIn("WEATHER", labs)
        # A 435 MHz downlink with the same mode is NOT weather.
        labs2 = satnogs.labels_for([{"mode": "DSB", "type": "Transmitter",
            "description": "", "downlink": {"freq_mhz": 435.0, "freq_high_mhz": None}}], 1)
        self.assertNotIn("WEATHER", labs2)

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

    def test_cw_beacon_does_not_imply_linear(self):
        # A CW telemetry beacon (Transmitter, not Transponder) is not a linear
        # transponder — it must not pollute the LINEAR/SSB filter.
        labs = satnogs.labels_for(
            [{"mode": "CW", "type": "Transmitter", "description": "beacon"}], 12345)
        self.assertNotIn("LINEAR", labs)
        self.assertNotIn("SSB", labs)

    def test_unmapped_mode_yields_no_label(self):
        self.assertEqual(satnogs.labels_for(
            [{"mode": "WEIRDMODE", "type": "Transmitter", "description": ""}], 12345),
            [])


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.sats = satnogs.parse_satellites(_fx("satnogs-satellites.json"))
        self.txs = satnogs.parse_transmitters(_fx("satnogs-transmitters.json"))
        # TLE index: ISS + NOAA 19 present; RS-44 (44909) absent on purpose.
        self.tle = {25544: ("ISS", "1 25544U", "2 25544"),
                    33591: ("NOAA 19", "1 33591U", "2 33591")}

    def test_intersection_filter(self):
        records, _ = satnogs.build_records(self.sats, self.txs, self.tle)
        norads = sorted(r["norad"] for r in records)
        # 25544 + 33591 kept; 44909 dropped (no TLE); 55555 dropped (no sat);
        # 99999 dropped (dead); null dropped (no norad).
        self.assertEqual(norads, [25544, 33591])

    def test_record_shape_and_uplink(self):
        records, _ = satnogs.build_records(self.sats, self.txs, self.tle)
        iss = next(r for r in records if r["norad"] == 25544)
        self.assertEqual(iss["sat_id"], "ISS-UUID")
        self.assertEqual(iss["status"], "alive")
        self.assertIn("CREWED", iss["labels"])
        self.assertIn("APRS", iss["labels"])         # from AFSK "Mode V APRS"
        self.assertNotIn("DATA", iss["labels"])      # APRS suppressed generic DATA
        self.assertEqual(len(iss["transmitters"]), 2)  # Ku + inactive dropped
        fm = next(t for t in iss["transmitters"] if t["mode"] == "FM")
        self.assertEqual(fm["uplink"]["freq_mhz"], 145.99)
        self.assertFalse(iss["selected"])            # default off

    def test_selected_carried_over(self):
        records, _ = satnogs.build_records(
            self.sats, self.txs, self.tle, prev_selected={25544: True})
        iss = next(r for r in records if r["norad"] == 25544)
        self.assertTrue(iss["selected"])

    def test_facet_counts_in_vocab_order(self):
        records, facet = satnogs.build_records(self.sats, self.txs, self.tle)
        self.assertEqual(facet.get("WEATHER"), 1)    # NOAA 19
        self.assertEqual(facet.get("CREWED"), 1)     # ISS
        self.assertEqual(list(facet), [l for l in satnogs.LABELS if l in facet])

    def test_diff_added_removed_changed(self):
        old = [{"norad": 25544, "name": "ISS (ZARYA)", "status": "alive",
                "labels": ["CREWED"], "transmitters": []},
               {"norad": 40000, "name": "OLDSAT", "status": "alive",
                "labels": [], "transmitters": []}]
        new = [{"norad": 25544, "name": "ISS (ZARYA)", "status": "alive",
                "labels": ["CREWED"], "transmitters": [{"mode": "FM"}]},
               {"norad": 33591, "name": "NOAA 19", "status": "alive",
                "labels": ["WEATHER"], "transmitters": []}]
        d = satnogs.diff_rosters(old, new)
        self.assertEqual(d["added"], [33591])
        self.assertEqual(d["removed"], [40000])
        self.assertEqual(d["changed"], [25544])      # transmitters differ


class GapfillConfigTest(unittest.TestCase):
    def test_noaa_apt_birds_are_gapfilled(self):
        # NOAA 15/18/19 sit in no CelesTrak group; they must be gap-filled by
        # NORAD so the APT weather birds survive the intersection.
        import tle
        for norad in (25338, 28654, 33591):
            self.assertIn(norad, tle.GAPFILL_NORADS)
        self.assertIn("CATNR={}", tle.CATNR_URL)


class BuildRosterCliTest(unittest.TestCase):
    def test_help_runs(self):
        import subprocess
        script = os.path.join(os.path.dirname(_HERE),
                              "services", "satellites", "build-roster.py")
        p = subprocess.run([sys.executable, script, "--help"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertIn("--cache", p.stdout)


if __name__ == "__main__":
    unittest.main()
