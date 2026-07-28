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

    def test_load_recovers_in_memory_without_clobbering_garbled_file(self):
        # A garbled read is usually a TRANSIENT mid-write (atomic save() makes even
        # that impossible now). load() must recover IN-MEMORY, but must NEVER
        # overwrite the file — doing so let a concurrent /select persist an empty
        # roster over a full one and wipe the whole list.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            with open(p, "w") as fh:
                json.dump([1, 2, 3], fh)          # valid JSON, wrong shape
            data = roster.load(p)
            self.assertEqual(data["satellites"], [])          # recovered in-memory
            with open(p) as fh:
                self.assertEqual(json.load(fh), [1, 2, 3])    # file left INTACT, not clobbered

    def test_save_is_atomic_no_temp_left_behind(self):
        # save() writes a unique temp then os.replace()s it in; on success the dir
        # holds exactly satellites.json and no leftover .satellites-*.tmp files.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            roster.save(p, {"updated": "x", "source": "t", "labels": {},
                            "satellites": [{"norad": 25544, "selected": False}]})
            self.assertEqual(os.listdir(d), ["satellites.json"])
            with open(p) as fh:
                self.assertEqual(json.load(fh)["satellites"][0]["norad"], 25544)

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
