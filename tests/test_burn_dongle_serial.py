import os, sys, unittest
from unittest import mock
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "scripts"))
import burn_dongle_serial as bds

class ValidateSerialTest(unittest.TestCase):
    def test_accepts_plain_digits(self):
        self.assertTrue(bds.valid_serial("1090"))
        self.assertTrue(bds.valid_serial("00000001"))

    def test_rejects_empty(self):
        self.assertFalse(bds.valid_serial(""))

    def test_rejects_shell_metacharacters(self):
        for bad in ["1090;rm -rf /", "1090 && echo x", "$(whoami)", "`id`", "1090|cat"]:
            self.assertFalse(bds.valid_serial(bad), f"should reject: {bad!r}")

    def test_rejects_too_long(self):
        self.assertFalse(bds.valid_serial("1" * 64))

    def test_rejects_non_alnum(self):
        self.assertFalse(bds.valid_serial("abc-def"))
        self.assertFalse(bds.valid_serial("abc def"))

class MainRefusesInvalidTest(unittest.TestCase):
    def test_main_exits_nonzero_on_invalid_serial_without_calling_burn(self):
        with mock.patch.object(bds, "burn_serial") as mocked_burn:
            rc = bds.main(["1090; rm -rf /"])
        self.assertNotEqual(rc, 0)
        mocked_burn.assert_not_called()

    def test_main_calls_burn_on_valid_serial(self):
        with mock.patch.object(bds, "burn_serial") as mocked_burn:
            rc = bds.main(["1090"])
        self.assertEqual(rc, 0)
        mocked_burn.assert_called_once_with("1090")

if __name__ == "__main__":
    unittest.main()
