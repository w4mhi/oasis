import os, sys, unittest
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import config_paths as cp

class ConfigPathsTest(unittest.TestCase):
    def test_paths_under_configuration(self):
        r = "/x/oasis"
        self.assertEqual(cp.config_dir(r), "/x/oasis/configuration")
        self.assertEqual(cp.station_json(r), "/x/oasis/configuration/station.json")
        self.assertEqual(cp.installed_services_json(r),
                         "/x/oasis/configuration/installed-services.json")
        self.assertEqual(cp.user_folders_json(r),
                         "/x/oasis/configuration/user-folders.json")

if __name__ == "__main__":
    unittest.main()
