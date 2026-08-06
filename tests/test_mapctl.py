import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from maps import mapctl


class _FakeProc:
    """Minimal stand-in for the go-pmtiles Popen: streams one line, then exits
    with the given return code."""
    def __init__(self, returncode):
        self._rc = returncode
        self.returncode = None
        self.stdout = iter(["extracting…\n"])

    def wait(self):
        self.returncode = self._rc


class MapctlExtractPartialTest(unittest.TestCase):
    """extract() must never leave a half-written .pmtiles behind when the run
    doesn't finish cleanly — a partial reads as 'present' (blue on the coverage
    map, listed in the base-map dropdown) yet is corrupt."""

    def _extract(self, maps_dir, returncode):
        out_path = os.path.join(maps_dir, "Testland.pmtiles")

        def fake_popen(cmd, **kwargs):
            # Simulate go-pmtiles having written a partial archive before it dies.
            with open(out_path, "wb") as fh:
                fh.write(b"partial-bytes")
            return _FakeProc(returncode)

        with mock.patch.object(mapctl, "resolve_pmtiles", return_value="/fake/pmtiles"), \
             mock.patch.object(mapctl, "default_source", return_value="planet.pmtiles"), \
             mock.patch("maps.mapctl.subprocess.Popen", side_effect=fake_popen):
            list(mapctl.extract(maps_dir, name="Testland", bbox="-1,-1,1,1"))
        return out_path

    def test_cancel_or_failure_removes_partial(self):
        # Non-zero exit (a SIGTERM from Cancel gives a negative code) -> no partial.
        with tempfile.TemporaryDirectory() as d:
            out_path = self._extract(d, returncode=-15)
            self.assertFalse(os.path.exists(out_path),
                             "canceled/failed extract left a partial .pmtiles behind")

    def test_success_keeps_archive(self):
        # Clean exit -> the finished archive stays.
        with tempfile.TemporaryDirectory() as d:
            out_path = self._extract(d, returncode=0)
            self.assertTrue(os.path.exists(out_path),
                            "successful extract removed its own output")


if __name__ == "__main__":
    unittest.main()
