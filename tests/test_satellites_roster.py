import os, sys, json, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
import roster  # noqa: E402


class RosterTest(unittest.TestCase):
    def test_load_seeds_empty_envelope_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            data = roster.load(p)
            self.assertTrue(os.path.exists(p))
            self.assertEqual(data["satellites"], [])
            self.assertEqual(data["labels"], {})

    def test_set_selected_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            json.dump({"updated": "x", "labels": {}, "satellites":
                       [{"norad": 25544, "selected": False, "transmitters": []}]},
                      open(p, "w"))
            roster.set_selected(p, 25544, True)
            iss = json.load(open(p))["satellites"][0]
            self.assertTrue(iss["selected"])

    def test_load_recovers_from_non_dict_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            with open(p, "w") as fh:
                json.dump([1, 2, 3], fh)          # valid JSON, wrong shape
            data = roster.load(p)
            self.assertEqual(data["satellites"], [])
            self.assertIsInstance(json.load(open(p)), dict)   # reseeded as a dict

    def test_legacy_downlinks_flattens_transmitters(self):
        sat = {"transmitters": [
            {"mode": "FM", "downlink": {"freq_mhz": 145.8, "freq_high_mhz": None},
             "uplink": {"freq_mhz": 145.99, "freq_high_mhz": None}},
            {"mode": "APRS", "downlink": {"freq_mhz": 145.825, "freq_high_mhz": None},
             "uplink": None},
            {"mode": "BEACONONLYUP", "downlink": None,
             "uplink": {"freq_mhz": 435.0, "freq_high_mhz": None}}]}
        dls = roster.legacy_downlinks(sat)
        self.assertEqual(dls, [{"mode": "FM", "freq_mhz": 145.8},
                               {"mode": "APRS", "freq_mhz": 145.825}])

    def test_legacy_downlinks_dedup_and_aprs_naming(self):
        sat = {"transmitters": [
            {"mode": "AFSK", "description": "ISS APRS Digipeater", "downlink": {"freq_mhz": 145.825}},
            {"mode": "AFSK", "description": "ISS APRS Digipeater", "downlink": {"freq_mhz": 145.825}},
            {"mode": "FM", "description": "Voice Repeater", "downlink": {"freq_mhz": 437.8}},
            {"mode": "FM", "description": "Voice Repeater", "downlink": {"freq_mhz": 437.8}},
            {"mode": "SSTV", "description": "SSTV", "downlink": {"freq_mhz": 145.8}}]}
        dls = roster.legacy_downlinks(sat)
        self.assertEqual(dls, [
            {"mode": "APRS", "freq_mhz": 145.825},   # AFSK + APRS-in-description → APRS
            {"mode": "FM", "freq_mhz": 437.8},       # dup collapsed
            {"mode": "SSTV", "freq_mhz": 145.8}])


if __name__ == "__main__":
    unittest.main()
