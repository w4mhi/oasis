"""/api/speech/* against docs/api-contract.md.

§2 is the one to watch here: "Piper is not installed" is the ANSWER to
/api/speech/status, not a failed request. It reports ok:true with
available:false. /api/speech/say is different — it was asked to produce audio
and cannot, so that is a real failure with a status code to match.
"""
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

import app as oasis_app                                     # noqa: E402
from common import speech                                   # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()


class StatusTest(_Base):
    _KEYS = {"ok", "available", "voice", "sample_rate_hz", "model",
             "player", "cache_entries", "cache_bytes", "cache_budget_bytes"}

    def test_shape_is_identical_whether_or_not_speech_is_installed(self):
        # §5: the same key set in both worlds, nulls for what cannot be known.
        # A shorter dict when absent makes a caller learn which world it is in
        # before it can read anything.
        for available in (True, False):
            with self.subTest(available=available):
                info = ({"name": "en_GB-jenny_dioco-medium", "model": "/x.onnx",
                         "sample_rate_hz": 22050} if available else
                        {"name": None, "model": None, "sample_rate_hz": None})
                with mock.patch.object(speech, "available", return_value=available), \
                     mock.patch.object(speech, "voice_info", return_value=info):
                    r = self.c.get("/api/speech/status")
                self.assertEqual(r.status_code, 200)
                body = r.get_json()
                self.assertTrue(body["ok"])          # §2: the request succeeded
                self.assertEqual(set(body), self._KEYS)
                self.assertIs(body["available"], available)

    def test_not_installed_is_never_an_error(self):
        with mock.patch.object(speech, "available", return_value=False):
            body = self.c.get("/api/speech/status").get_json()
        self.assertNotIn("error", body)              # §2 corollary


class SayTest(_Base):
    def test_rejected_text_is_400_with_a_stable_code(self):
        r = self.c.get("/api/speech/say?text=")
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["code"], "EMPTY_TEXT")
        self.assertIn("error", body)

    def test_over_length_text_is_400(self):
        r = self.c.get("/api/speech/say?text=" + "a" * 400)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "TEXT_TOO_LONG")

    def test_no_engine_is_503_and_never_a_200(self):
        # §2 corollary: an ok:false body must never be served with HTTP 200.
        with mock.patch.object(speech, "synthesize",
                               side_effect=speech.SpeechUnavailable("nope")):
            r = self.c.get("/api/speech/say?text=ISS")
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.get_json()["ok"])
        self.assertEqual(r.get_json()["code"], "SPEECH_UNAVAILABLE")

    def test_a_successful_call_returns_audio_not_json(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(b"RIFF....WAVEfake")
            path = fh.name
        try:
            with mock.patch.object(speech, "synthesize", return_value=path):
                r = self.c.get("/api/speech/say?text=ISS")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.mimetype.startswith("audio/"))
        finally:
            os.unlink(path)


class EtagTest(_Base):
    """The ETag Werkzeug derives by default is (mtime, size, filename), and
    SPEECH.synthesize()'s cache-HIT path touches the file's mtime on every
    call (an LRU touch — see common/speech.py's _cached, prune() evicts by
    age). Left at the default, that would change the ETag on every request
    and a conditional GET would never get its 304 — confirmed against the
    running app before this fix. The route must pass an explicit,
    content-derived ETag instead, so it stays stable across the touch."""

    def _repo_with_model(self, tmp):
        voices = os.path.join(tmp, "features", "speech", "voices")
        os.makedirs(voices)
        model = os.path.join(voices, "en_GB-jenny_dioco-medium.onnx")
        with open(model, "wb") as fh:
            fh.write(b"not a real model")
        with open(model + ".json", "w", encoding="utf-8") as fh:
            fh.write('{"audio": {"sample_rate": 22050}}')
        return tmp

    @staticmethod
    def _fake_run(argv, **kwargs):
        out = argv[argv.index("-f") + 1]
        with open(out, "wb") as fh:
            fh.write(b"RIFF....WAVEfake")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def test_repeated_requests_for_the_same_text_get_the_same_etag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo_with_model(tmp)
            with mock.patch.object(oasis_app, "SUITE_ROOT", root), \
                 mock.patch("subprocess.run", side_effect=self._fake_run):
                r1 = self.c.get("/api/speech/say?text=ISS")
                # A cache HIT: synthesize()'s _cached() touches the file's
                # mtime right here — exactly what used to change the ETag.
                r2 = self.c.get("/api/speech/say?text=ISS")
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r2.status_code, 200)
            self.assertIsNotNone(r1.headers.get("ETag"))
            self.assertEqual(r1.headers.get("ETag"), r2.headers.get("ETag"))

    def test_a_conditional_request_gets_a_304(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo_with_model(tmp)
            with mock.patch.object(oasis_app, "SUITE_ROOT", root), \
                 mock.patch("subprocess.run", side_effect=self._fake_run):
                first = self.c.get("/api/speech/say?text=ISS")
                etag = first.headers.get("ETag")
                second = self.c.get("/api/speech/say?text=ISS",
                                    headers={"If-None-Match": etag})
            self.assertEqual(second.status_code, 304)


if __name__ == "__main__":
    unittest.main()
