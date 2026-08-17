import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
from services.nwr.common import event_map  # noqa: E402


class EventMapTest(unittest.TestCase):
    def test_the_headline_codes(self):
        self.assertEqual(event_map.warning_type("TOR"), "tornado")
        self.assertEqual(event_map.warning_type("SVR"), "storm")
        self.assertEqual(event_map.warning_type("FFW"), "flood")
        self.assertEqual(event_map.warning_type("WSW"), "ice")
        self.assertEqual(event_map.warning_type("HWW"), "wind")
        self.assertEqual(event_map.warning_type("FRW"), "fire")
        self.assertEqual(event_map.warning_type("HMW"), "hazmat")
        self.assertEqual(event_map.warning_type("CEM"), "eoc")

    def test_informational_codes_plot_nothing(self):
        for code in ("RWT", "RMT", "DMO", "ADR", "SPS", "SVS", "NPT"):
            self.assertIsNone(event_map.warning_type(code), code)

    def test_geological_codes_use_the_fallback(self):
        for code in ("EQW", "AVW", "VOW", "LSW"):
            self.assertEqual(event_map.warning_type(code), "weather", code)

    def test_unknown_actionable_code_falls_back_rather_than_vanishing(self):
        # Better to plot an unclassified warning than to silently drop one.
        self.assertEqual(event_map.warning_type("ZZZ"), "weather")

    def test_every_mapped_type_exists_in_the_catalog(self):
        # The guard that stops a typo becoming an invisible marker.
        with open(os.path.join(_ROOT, "maps", "traffic", "warnings.json")) as fh:
            ids = {e["id"] for e in json.load(fh)}
        used = set(event_map.EVENT_TYPE.values()) | {event_map.FALLBACK_TYPE}
        self.assertEqual(used - ids, set())


if __name__ == "__main__":
    unittest.main()
