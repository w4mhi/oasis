import os, sys, tempfile, unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import lookup


class LookupModuleTest(unittest.TestCase):
    def test_common_lookup_module_exports_lookup_helpers(self):
        self.assertTrue(callable(lookup.lookup))
        self.assertTrue(callable(lookup.lookup_prefix))
        self.assertTrue(callable(lookup.lookup_by_name))
        self.assertTrue(callable(lookup.lookup_by_grid))

    def test_build_index_requires_en_dat_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_en = os.path.join(tmp_dir, "EN.dat")
            missing_idx = os.path.join(tmp_dir, "EN.idx")
            with self.assertRaises(FileNotFoundError):
                lookup.build_index(en_path=missing_en, index_path=missing_idx)


if __name__ == "__main__":
    unittest.main()
