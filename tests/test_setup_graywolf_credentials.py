"""POST /api/setup/graywolf-credentials — the targeted save.

Before this existed the GrayWolf login could only be written by "Run selected",
which fires the whole install plan; the box had no save control at all. These
cover the two things that make a save button trustworthy: it writes what it says
it wrote, and it refuses loudly when it cannot.
"""
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "server"))

from common import config_paths  # noqa: E402


def _load_writer():
    """_setup_write_graywolf without importing Flask app state."""
    from routes.setup import _setup_write_graywolf
    return _setup_write_graywolf


class GraywolfCredentialWriteTest(unittest.TestCase):
    """The shared writer the endpoint delegates to. Owning the
    empty-means-unchanged rule in ONE place is the point of the delegation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = os.path.join(self.tmp.name, "configuration")
        os.makedirs(cfg)
        self.path = os.path.join(cfg, "graywolf_api.json")
        self._seed({"base_url": "http://127.0.0.1:8080",
                    "username": "", "password": "", "send_path": "both"})
        import routes.setup as rs
        self._rs = rs
        self._orig_root = rs.SUITE_ROOT
        rs.SUITE_ROOT = self.tmp.name
        self.addCleanup(lambda: setattr(rs, "SUITE_ROOT", self._orig_root))

    def _seed(self, obj):
        with open(self.path, "w") as fh:
            json.dump(obj, fh)

    def _read(self):
        with open(self.path) as fh:
            return json.load(fh)

    def test_path_helper_points_where_the_test_seeded(self):
        self.assertEqual(config_paths.graywolf_api_json(self.tmp.name), self.path)

    def test_writes_both_halves(self):
        _load_writer()({"graywolf": {"username": "op", "password": "s3cret"}})
        got = self._read()
        self.assertEqual(got["username"], "op")
        self.assertEqual(got["password"], "s3cret")

    def test_preserves_base_url_and_send_path(self):
        # Read-modify-write: this form owns two fields and must not flatten the
        # rest, or the broadcaster loses the address it talks to.
        _load_writer()({"graywolf": {"username": "op", "password": "s3cret"}})
        got = self._read()
        self.assertEqual(got["base_url"], "http://127.0.0.1:8080")
        self.assertEqual(got["send_path"], "both")

    def test_empty_password_means_unchanged_not_cleared(self):
        # The failure this rule prevents: saving a username wipes a working
        # password, and nothing says so.
        self._seed({"base_url": "http://x", "username": "op", "password": "keepme"})
        _load_writer()({"graywolf": {"username": "op2", "password": ""}})
        got = self._read()
        self.assertEqual(got["username"], "op2")
        self.assertEqual(got["password"], "keepme")

    def test_empty_username_leaves_the_stored_one_alone(self):
        self._seed({"base_url": "http://x", "username": "op", "password": "pw"})
        _load_writer()({"graywolf": {"username": "", "password": "new"}})
        got = self._read()
        self.assertEqual(got["username"], "op")
        self.assertEqual(got["password"], "new")

    def test_both_empty_is_a_no_op(self):
        before = self._read()
        _load_writer()({"graywolf": {"username": "", "password": ""}})
        self.assertEqual(self._read(), before)

    def test_username_is_stripped(self):
        _load_writer()({"graywolf": {"username": "  op  ", "password": "pw"}})
        self.assertEqual(self._read()["username"], "op")

    def test_corrupt_file_raises_rather_than_overwriting(self):
        # Refusing beats replacing it with just the credentials and losing
        # base_url — the endpoint turns this into a 500 with the reason.
        with open(self.path, "w") as fh:
            fh.write("{not json")
        with self.assertRaises(ValueError):
            _load_writer()({"graywolf": {"username": "op", "password": "pw"}})

    def test_absent_file_is_a_silent_no_op_in_the_writer(self):
        # Correct INSIDE a plan (the installer stubs the file moments later) and
        # wrong for a save button — which is why the endpoint checks for the
        # file itself and answers explicitly instead of relying on this.
        os.remove(self.path)
        _load_writer()({"graywolf": {"username": "op", "password": "pw"}})
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
