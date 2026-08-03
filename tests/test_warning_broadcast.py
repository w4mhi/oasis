import os, sys, time, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from services.aprs.common import warning_broadcast as wb


class FormatTest(unittest.TestCase):
    def test_object_name_prefix_and_len(self):
        self.assertEqual(wb.object_name("3f9a1b2c9999"), "W3f9a1b2c")
        self.assertEqual(len(wb.object_name("ab")), 9)  # padded

    def test_format_lat(self):
        self.assertEqual(wb.format_lat(47.5495), "4732.97N")
        self.assertEqual(wb.format_lat(-33.0), "3300.00S")

    def test_format_lon(self):
        self.assertEqual(wb.format_lon(-122.0298), "12201.79W")
        self.assertEqual(wb.format_lon(5.5), "00530.00E")

    def test_object_payload_shape(self):
        w = {"id": "3f9a1b2cdeadbeef", "lat": 47.5495, "lon": -122.0298, "note": "road out"}
        p = wb.object_payload(w, "\\", "w", "both", 1800)
        self.assertEqual(p["type"], "object")
        self.assertEqual(p["object_name"], "W3f9a1b2c")
        self.assertEqual(p["symbol_table"], "\\")
        self.assertEqual(p["symbol"], "w")
        self.assertEqual(p["send_path"], "both")
        self.assertEqual(p["interval"], 1800)
        self.assertEqual(p["comment"], "road out")
        self.assertAlmostEqual(p["latitude"], 47.5495)

    def test_kill_info_has_underscore_and_name(self):
        ts = time.struct_time((2026, 8, 3, 18, 30, 0, 0, 0, 0))
        info = wb.kill_info("W3f9a1b2c", 47.5495, -122.0298, "\\", "w", ts)
        self.assertTrue(info.startswith(";W3f9a1b2c"))
        self.assertEqual(info[10], "_")            # kill flag after 9-char name
        self.assertIn("031830z", info)             # DDHHMMz
        self.assertIn("4732.97N", info)
        self.assertIn("12201.79W", info)
        self.assertTrue(info.endswith("w"))        # symbol code last

    def test_kill_payload_is_custom(self):
        ts = time.gmtime(0)
        p = wb.kill_payload("W3f9a1b2c", 1.0, 2.0, "/", "o", "both", ts)
        self.assertEqual(p["type"], "custom")
        self.assertTrue(p["custom_info"].startswith(";W3f9a1b2c_"))
        self.assertEqual(p["send_path"], "both")


if __name__ == "__main__":
    unittest.main()
