"""
/api/satellites/listen/* on the contract — the SDR half of the satellites
cluster (status, record, stream, stop, recordings).

These matter beyond tidiness: /listen keys a radio and writes to disk, and
/listen/status is polled by three pages to decide whether the dongle is free.
Every error path here returned a bare `{"error": …}` with no envelope, so a
caller could not tell a refusal from a success without inspecting the status
code and then guessing at prose.

**This file is also the runtime half of a static-gate limitation.** The capture
routes return `jsonify(…), e.code` — one handler, eight causes, the status
carried on the exception. That is better code than eight literal branches, but
tests/api_contract_scan.py cannot read a computed status, so its
ok:false-with-200 rule skips those returns and lists the routes in
_DYNAMIC_ERROR_STATUS instead. The assertions below are what that list promises:
every refusal really is a 4xx, and every one names a stable cause.
"""

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

# Importing the app registers the satellites blueprint, and services/satellites/
# routes.py puts its own directory on sys.path as it loads — which is why the
# bare `import listen` inside these tests resolves.
import app as oasis_app                                # noqa: E402

_HDR = {"X-OASIS-Request": "1"}

_READY = {"missing_deps": [], "dongle_present": True, "assigned": True,
          "device": "RTL-SDR (00000001)", "busy": False, "holder": None}
_IDLE = {"recording": False, "streaming": False, "mode": None, "norad": None,
         "file": None, "seconds": 0, "freq_hz": 0}

_STATUS_KEYS = {"ok", "recording", "streaming", "mode", "norad", "file",
                "seconds", "freq_hz", "missing_deps", "dongle_present",
                "assigned", "device", "busy", "holder",
                # Added 2026-08-12 after a live pass. listen.status() had
                # carried `backend` and the tracked view for some time; the
                # route's hand-written key list had not, so the page's TRACKED
                # badge — which reads LISTEN.backend and LISTEN.doppler_hz —
                # could never light no matter what the capture was doing.
                # tests/test_listen_status_contract.py now derives that side of
                # the contract from the page's own source rather than a list
                # like this one, because a list like this one is what failed.
                "backend", "tracked", "doppler_hz", "corrected_hz",
                "tracker_error"}


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def _listen(self, **over):
        """Patch the listen module as imported inside each view."""
        import listen
        pre = dict(_READY)
        pre.update({k: v for k, v in over.items() if k in _READY})
        st = dict(_IDLE)
        st.update({k: v for k, v in over.items() if k in _IDLE})
        return (mock.patch.object(listen, "preconditions", return_value=pre),
                mock.patch.object(listen, "status", return_value=st))


class StatusTest(_Base):
    def test_envelope_and_full_key_set(self):
        pre, st = self._listen()
        with pre, st:
            r = self.c.get("/api/satellites/listen/status")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(set(d), _STATUS_KEYS,
                         "this was jsonify(status() updated with preconditions()) — "
                         "a union no reader could see at the return site")

    def test_the_fields_three_pages_branch_on_survive(self):
        pre, st = self._listen(recording=True, norad=25544, seconds=42,
                               busy=True, holder="adsb")
        with pre, st:
            d = self.c.get("/api/satellites/listen/status").get_json()
        # satellites.html, index.html and the kiosk each read these.
        self.assertIs(d["recording"], True)
        self.assertEqual(d["norad"], 25544)
        self.assertEqual(d["seconds"], 42)
        self.assertIs(d["busy"], True)
        self.assertEqual(d["holder"], "adsb")
        self.assertEqual(d["missing_deps"], [])


class CaptureRefusalTest(_Base):
    """The runtime half of _DYNAMIC_ERROR_STATUS. Each refusal must be a real
    4xx carrying a stable slug — the scan cannot prove either statically."""

    def _post(self, **over):
        pre, st = self._listen(**over)
        with pre, st:
            return self.c.post("/api/satellites/listen", headers=_HDR,
                               json={"norad": 25544})

    def test_missing_sdr_tools_is_400_with_a_slug(self):
        r = self._post(missing_deps=["rtl_fm", "sox"])
        self.assertEqual(r.status_code, 400)
        d = r.get_json()
        self.assertIs(d["ok"], False)
        self.assertEqual(d["code"], "SDR_TOOLS_MISSING")
        self.assertIn("rtl_fm", d["error"])

    def test_no_dongle_is_400_with_a_slug(self):
        r = self._post(dongle_present=False)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "NO_DONGLE")

    def test_a_dongle_held_by_another_service_is_409(self):
        """409, not 400 — nothing is wrong with the request; the resource is
        taken. A caller can retry this one, which is why it must not look like
        the malformed-input cases above."""
        r = self._post(busy=True, holder="adsb")
        self.assertEqual(r.status_code, 409)
        d = r.get_json()
        self.assertEqual(d["code"], "DONGLE_BUSY")
        self.assertIn("adsb", d["error"])

    def test_no_refusal_is_ever_served_with_http_200(self):
        """The assertion _DYNAMIC_ERROR_STATUS exists to guarantee."""
        for kw in ({"missing_deps": ["rtl_fm"]}, {"dongle_present": False},
                   {"busy": True, "holder": "adsb"}):
            r = self._post(**kw)
            self.assertNotEqual(r.status_code, 200, kw)
            self.assertIs(r.get_json()["ok"], False, kw)
            self.assertTrue(r.get_json()["code"], kw)

    def test_bad_norad_is_400(self):
        r = self.c.post("/api/satellites/listen", headers=_HDR, json={"norad": "abc"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "INVALID_NORAD")

    def test_csrf_header_required(self):
        self.assertEqual(self.c.post("/api/satellites/listen",
                                     json={"norad": 25544}).status_code, 403)


class StreamRefusalTest(_Base):
    def test_bad_norad_is_400_with_an_envelope(self):
        r = self.c.get("/api/satellites/listen/stream?norad=abc")
        self.assertEqual(r.status_code, 400)
        self.assertIs(r.get_json()["ok"], False)
        self.assertEqual(r.get_json()["code"], "INVALID_NORAD")

    def test_a_busy_dongle_is_409_not_200(self):
        pre, st = self._listen(busy=True, holder="adsb")
        with pre, st:
            r = self.c.get("/api/satellites/listen/stream?norad=25544")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "DONGLE_BUSY")


class StopTest(_Base):
    def test_stopping_an_idle_capture_is_a_success(self):
        """Idempotent (§8): the caller asked for 'not recording' and that is the
        state they got. Stopping twice must not look like a failure."""
        import listen
        with mock.patch.object(listen, "stop", return_value={"recording": False}), \
             mock.patch.object(listen, "prune_recordings"):
            r = self.c.post("/api/satellites/listen/stop", headers=_HDR)
            again = self.c.post("/api/satellites/listen/stop", headers=_HDR)
        for resp in (r, again):
            self.assertEqual(resp.status_code, 200)
            d = resp.get_json()
            self.assertIs(d["ok"], True)
            self.assertIs(d["recording"], False)
        self.assertEqual(r.get_json(), again.get_json(), "idempotent")

    def test_csrf_header_required(self):
        self.assertEqual(self.c.post("/api/satellites/listen/stop").status_code, 403)


class RecordingsTest(_Base):
    def test_envelope_and_bounds(self):
        d = self.c.get("/api/satellites/listen/recordings").get_json()
        self.assertIs(d["ok"], True)
        for key in ("recordings", "total", "count", "truncated", "limit"):
            self.assertIn(key, d)

    def test_timestamps_are_iso_not_epoch(self):
        import listen
        with mock.patch.object(listen, "recordings_dir", return_value="/rec"), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("os.listdir", return_value=["ISS_20260808-120000.wav"]), \
             mock.patch("os.path.getsize", return_value=1234), \
             mock.patch("os.path.getmtime", return_value=1_754_000_000):
            d = self.c.get("/api/satellites/listen/recordings").get_json()
        rec = d["recordings"][0]
        self.assertEqual(rec["recorded_at"], "2025-07-31T22:13:20Z")
        self.assertNotIn("mtime", rec, "§6: no raw epoch on the wire")
        self.assertEqual(rec["bytes"], 1234)

    def test_a_file_pruned_mid_listing_is_skipped_not_a_500(self):
        """prune_recordings runs on a timer and on every stop, so a WAV can
        vanish between listdir() and getsize()."""
        import listen
        with mock.patch.object(listen, "recordings_dir", return_value="/rec"), \
             mock.patch("os.path.isdir", return_value=True), \
             mock.patch("os.listdir", return_value=["gone.wav"]), \
             mock.patch("os.path.getsize", side_effect=OSError("vanished")):
            r = self.c.get("/api/satellites/listen/recordings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["recordings"], [])


if __name__ == "__main__":
    unittest.main()
