import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from common import config_paths

class GraywolfApiPathTest(unittest.TestCase):
    def test_path_under_configuration(self):
        p = config_paths.graywolf_api_json("/opt/oasis")
        self.assertEqual(p, "/opt/oasis/configuration/graywolf_api.json")

if __name__ == "__main__":
    unittest.main()
