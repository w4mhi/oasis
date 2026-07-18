import os, sys, json, tempfile, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services", "satellites"))
import roster  # noqa: E402

class RosterTest(unittest.TestCase):
    def test_defaults_have_required_fields(self):
        for s in roster.DEFAULT_ROSTER:
            self.assertIn("name", s)
            self.assertIn("norad", s)
            self.assertIsInstance(s["labels"], list)
            self.assertIsInstance(s["downlinks"], list)

    def test_load_writes_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            data = roster.load(p)
            self.assertTrue(os.path.exists(p))
            self.assertEqual(len(data["satellites"]), len(roster.DEFAULT_ROSTER))

    def test_set_selected_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "satellites.json")
            roster.load(p)
            roster.set_selected(p, 25544, True)
            data = json.load(open(p))
            iss = [s for s in data["satellites"] if s["norad"] == 25544][0]
            self.assertTrue(iss["selected"])

if __name__ == "__main__":
    unittest.main()
