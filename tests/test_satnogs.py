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
        self.assertEqual(labs, [lab for lab in satnogs.LABELS if lab in labs])

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

    def test_fm_out_of_amateur_band_is_not_voice(self):
        # Arktika-M1's real transmitters: mode "FM" on L-band SARSAT / weather DCP.
        # FM modulation != amateur FM voice — must NOT earn VOICE/FM.
        for freq, desc in ((1544.5, "SARSAT L-Band"), (1703.0, "Mode L - DCP"),
                           (400.328, "Mode U - military telemetry")):
            labs = satnogs.labels_for(
                [{"mode": "FM", "type": "Transmitter", "description": desc,
                  "downlink": {"freq_mhz": freq, "freq_high_mhz": None}}], 47719)
            self.assertNotIn("VOICE", labs, f"{freq} MHz must not be VOICE")
            self.assertNotIn("FM", labs, f"{freq} MHz must not be FM")

    def test_fm_in_amateur_band_is_voice(self):
        for freq in (145.960, 436.795, 1260.0):     # 2 m, 70 cm, 23 cm
            labs = satnogs.labels_for(
                [{"mode": "FM", "type": "Transmitter", "description": "voice",
                  "downlink": {"freq_mhz": freq, "freq_high_mhz": None}}], 43017)
            self.assertIn("VOICE", labs)
            self.assertIn("FM", labs)

    def test_fm_without_frequency_is_not_stripped(self):
        # Unknown band is unknowable — only a KNOWN out-of-band freq strips it.
        labs = satnogs.labels_for(
            [{"mode": "FM", "type": "Transceiver", "description": "voice"}], 43017)
        self.assertIn("VOICE", labs)

    def test_transponder_not_gated_by_band(self):
        # A SatNOGS Transponder is an amateur linear transponder regardless of the
        # frequency we happen to have — the type signal is reliable, not gated.
        labs = satnogs.labels_for(
            [{"mode": "SSB", "type": "Transponder", "description": "",
              "downlink": {"freq_mhz": 2401.0, "freq_high_mhz": None}}], 44909)
        self.assertIn("LINEAR", labs)


class OrbitClassTest(unittest.TestCase):
    # Real TLE line-2s (eccentricity cols 27-33, mean motion cols 53-63).
    LEO = "2 43017  97.4665  71.9647 0148707 199.1160 160.4457 15.13166182471038"  # FOX-1B
    MEO = "2 53109  70.1383 285.3666 0009174 281.6416  78.3214  6.42583383 94445"  # MTCube 2
    HEO = "2 47719  63.2571  53.1467 7298516 270.0868  14.5016  2.00610215 39409"  # Arktika-M1 (e=0.73)
    GEO = "2 99999   0.0500  90.0000 0001000   0.0000   0.0000  1.00270000    05"  # ~stationary

    def test_classifies_each_regime(self):
        self.assertEqual(satnogs.orbit_class(self.LEO), "LEO")
        self.assertEqual(satnogs.orbit_class(self.MEO), "MEO")
        self.assertEqual(satnogs.orbit_class(self.HEO), "HEO")   # eccentricity wins
        self.assertEqual(satnogs.orbit_class(self.GEO), "GEO")

    def test_unparseable_tle_is_none(self):
        self.assertIsNone(satnogs.orbit_class("2 25544"))        # truncated
        self.assertIsNone(satnogs.orbit_class(""))
        self.assertIsNone(satnogs.orbit_class(None))


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
        self.assertEqual(list(facet), [lab for lab in satnogs.LABELS if lab in facet])

    def test_record_with_no_recognized_service_is_dropped(self):
        # A bird whose only transmitter is an out-of-band "FM" (L-band weather/SAR)
        # ends up with no label and must be EXCLUDED — otherwise it shows in the
        # roster and "in coverage" (e.g. the Arktika-M1 bug) despite being unworkable.
        sats = {700: {"name": "WX-BIRD", "norad": 700, "sat_id": "X", "status": "alive"},
                701: {"name": "HAM-BIRD", "norad": 701, "sat_id": "Y", "status": "alive"}}
        txs = {
            700: [{"mode": "FM", "type": "Transmitter", "description": "SARSAT L-Band",
                   "downlink": {"freq_mhz": 1544.5, "freq_high_mhz": None}, "uplink": None}],
            701: [{"mode": "FM", "type": "Transmitter", "description": "voice",
                   "downlink": {"freq_mhz": 145.8, "freq_high_mhz": None}, "uplink": None}],
        }
        tle = {700: ("WX", "1", "2"), 701: ("HAM", "1", "2")}
        norads = sorted(r["norad"] for r in satnogs.build_records(sats, txs, tle)[0])
        self.assertEqual(norads, [701])   # WX-BIRD dropped, HAM-BIRD kept

    def test_orbit_tag_appended_to_name(self):
        # From a real TLE the record gains an `orbit` field AND the class is
        # appended to the display name (the "ISS (ZARYA) [LEO]" form).
        sats = {25544: {"name": "ISS (ZARYA)", "norad": 25544, "sat_id": "X", "status": "alive"}}
        txs = {25544: [{"mode": "FM", "type": "Transceiver", "description": "voice",
                        "downlink": {"freq_mhz": 145.8, "freq_high_mhz": None}, "uplink": None}]}
        tle = {25544: ("ISS", "1 25544U", OrbitClassTest.LEO)}
        rec = satnogs.build_records(sats, txs, tle)[0][0]
        self.assertEqual(rec["orbit"], "LEO")
        self.assertEqual(rec["name"], "ISS (ZARYA) [LEO]")

    def test_geo_bird_is_dropped(self):
        # GEO doesn't pass, so a GEO row would be a dead "no pass 24h" entry with
        # no controls — exclude it. (The one amateur GEO, QO-100, is already out
        # via the RTL-range filter; the GEO sats in range are weather telemetry.)
        sats = {40000: {"name": "GOES-X", "norad": 40000, "sat_id": "G", "status": "alive"},
                40001: {"name": "HAM-LEO", "norad": 40001, "sat_id": "H", "status": "alive"}}
        txs = {40000: [{"mode": "GMSK", "type": "Transmitter", "description": "telemetry",
                        "downlink": {"freq_mhz": 468.0, "freq_high_mhz": None}, "uplink": None}],
               40001: [{"mode": "FM", "type": "Transmitter", "description": "voice",
                        "downlink": {"freq_mhz": 145.8, "freq_high_mhz": None}, "uplink": None}]}
        tle = {40000: ("GOES-X", "1", OrbitClassTest.GEO),
               40001: ("HAM-LEO", "1", OrbitClassTest.LEO)}
        norads = sorted(r["norad"] for r in satnogs.build_records(sats, txs, tle)[0])
        self.assertEqual(norads, [40001])   # GEO dropped, LEO kept

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
