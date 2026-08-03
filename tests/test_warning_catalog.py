import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from services.aprs.common import warning_catalog as wc

class CatalogTest(unittest.TestCase):
    def test_real_catalog_maps_known_types(self):
        m = wc.load_symbol_map(_ROOT)
        self.assertEqual(m["flood"], ("\\", "w"))
        self.assertEqual(m["eoc"], ("/", "o"))
        self.assertEqual(len(m), 15)

    def test_missing_root_returns_empty(self):
        self.assertEqual(wc.load_symbol_map("/no/such/root"), {})

if __name__ == "__main__":
    unittest.main()
