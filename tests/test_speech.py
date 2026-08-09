"""common/speech.py — text to a cached WAV.

The tests that matter here are the ones that pin how UNTRUSTED text reaches a
subprocess. A satellite name comes from a TLE file and a future message subject
comes off the air; neither is authored by us. The 2026-08-08 attempt built a
shell command string, which is why `text never appears in argv` is a test and
not a comment.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common import speech                                   # noqa: E402


def _root_with_model(tmp):
    """A repo root carrying a fake voice model, so path logic is exercised
    without a 60 MB download."""
    voices = os.path.join(tmp, "features", "speech", "voices")
    os.makedirs(voices)
    model = os.path.join(voices, "en_GB-jenny_dioco-medium.onnx")
    with open(model, "wb") as fh:
        fh.write(b"not a real model")
    with open(model + ".json", "w", encoding="utf-8") as fh:
        fh.write('{"audio": {"sample_rate": 22050}}')
    return tmp, model


class ValidateTest(unittest.TestCase):
    def test_empty_text_is_rejected(self):
        with self.assertRaises(speech.SpeechRejected) as e:
            speech.validate("   ")
        self.assertEqual(e.exception.code, "EMPTY_TEXT")

    def test_over_length_text_is_rejected(self):
        with self.assertRaises(speech.SpeechRejected) as e:
            speech.validate("a" * (speech.MAX_TEXT_CHARS + 1))
        self.assertEqual(e.exception.code, "TEXT_TOO_LONG")

    def test_control_characters_are_rejected(self):
        with self.assertRaises(speech.SpeechRejected) as e:
            speech.validate("ISS\x07 in ten minutes")
        self.assertEqual(e.exception.code, "INVALID_TEXT")

    def test_tabs_and_newlines_are_allowed(self):
        # Piper treats a newline as a sentence break; rejecting it would ban a
        # legitimate multi-sentence announcement.
        self.assertEqual(speech.validate("one.\ntwo."), "one.\ntwo.")

    def test_ordinary_text_passes_through_unchanged(self):
        self.assertEqual(speech.validate("ISS, in ten minutes"), "ISS, in ten minutes")


class AvailabilityTest(unittest.TestCase):
    def test_no_model_means_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(speech.available(tmp))
            self.assertIsNone(speech.voice_model_path(tmp))

    def test_a_model_on_disk_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, model = _root_with_model(tmp)
            self.assertEqual(speech.voice_model_path(root), model)

    def test_a_model_without_its_json_sidecar_does_not_count(self):
        # Piper will not load one without the other, so reporting available
        # here would promise a voice that cannot speak.
        with tempfile.TemporaryDirectory() as tmp:
            root, model = _root_with_model(tmp)
            os.unlink(model + ".json")
            self.assertIsNone(speech.voice_model_path(root))

    def test_synthesize_without_a_model_raises_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(speech.SpeechUnavailable):
                speech.synthesize(tmp, "ISS, in ten minutes")


class CacheKeyTest(unittest.TestCase):
    def test_same_text_and_voice_give_the_same_key(self):
        self.assertEqual(speech.cache_key("ISS", "jenny"), speech.cache_key("ISS", "jenny"))

    def test_different_text_gives_a_different_key(self):
        self.assertNotEqual(speech.cache_key("ISS", "jenny"), speech.cache_key("NOAA 19", "jenny"))

    def test_different_voice_gives_a_different_key(self):
        # Otherwise changing the voice would serve the old voice's audio forever.
        self.assertNotEqual(speech.cache_key("ISS", "jenny"), speech.cache_key("ISS", "amy"))


class SubprocessShapeTest(unittest.TestCase):
    """The injection guard. A satellite name is not authored by us."""

    def _synth(self, tmp, text, wav=b"RIFF....WAVEfake"):
        root, _ = _root_with_model(tmp)
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            # Piper writes the file named by -f; imitate that.
            out = argv[argv.index("-f") + 1]
            with open(out, "wb") as fh:
                fh.write(wav)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            path = speech.synthesize(root, text)
        return path, seen

    def test_text_never_appears_in_argv(self):
        evil = "ISS`touch /tmp/pwned`"
        with tempfile.TemporaryDirectory() as tmp:
            _, seen = self._synth(tmp, evil)
        self.assertNotIn(evil, seen["argv"])
        for arg in seen["argv"]:
            self.assertNotIn("touch", arg)

    def test_text_is_passed_on_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, seen = self._synth(tmp, "ISS, in ten minutes")
        self.assertEqual(seen["kwargs"].get("input"), "ISS, in ten minutes")

    def test_no_shell_is_ever_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, seen = self._synth(tmp, "ISS")
        self.assertFalse(seen["kwargs"].get("shell", False))

    def test_a_timeout_is_always_set(self):
        # An unbounded synth hangs a gunicorn worker.
        with tempfile.TemporaryDirectory() as tmp:
            _, seen = self._synth(tmp, "ISS")
        self.assertTrue(seen["kwargs"].get("timeout"))

    def test_an_empty_wav_is_a_failure_not_a_success(self):
        # 2026-08-08 shipped an installer that reported success while producing
        # nothing. A zero-byte file is not audio.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(speech.SpeechUnavailable):
                self._synth(tmp, "ISS", wav=b"")

    def test_a_second_call_hits_the_cache_and_does_not_respawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = _root_with_model(tmp)
            calls = []

            def fake_run(argv, **kwargs):
                calls.append(argv)
                out = argv[argv.index("-f") + 1]
                with open(out, "wb") as fh:
                    fh.write(b"RIFF....WAVEfake")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run):
                a = speech.synthesize(root, "ISS, in ten minutes")
                b = speech.synthesize(root, "ISS, in ten minutes")
            self.assertEqual(a, b)
            self.assertEqual(len(calls), 1)

    def test_a_partial_file_is_never_left_in_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = _root_with_model(tmp)
            with mock.patch("subprocess.run",
                            return_value=mock.Mock(returncode=1, stdout="", stderr="boom")):
                with self.assertRaises(speech.SpeechUnavailable):
                    speech.synthesize(root, "ISS")
            cache = os.path.join(root, "features", "speech", "cache")
            leftovers = os.listdir(cache) if os.path.isdir(cache) else []
            self.assertEqual([f for f in leftovers if not f.endswith(".part")], [])


class PruneTest(unittest.TestCase):
    def _cache_with(self, root, sizes):
        cache = os.path.join(root, "features", "speech", "cache")
        os.makedirs(cache, exist_ok=True)
        paths = []
        for i, size in enumerate(sizes):
            p = os.path.join(cache, f"{i:02d}.wav")
            with open(p, "wb") as fh:
                fh.write(b"x" * size)
            os.utime(p, (1000 + i, 1000 + i))     # ascending mtime: last is newest
            paths.append(p)
        return paths

    def test_under_budget_prunes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._cache_with(tmp, [10, 10, 10])
            self.assertEqual(speech.prune(tmp, budget_bytes=1000), 0)
            for p in paths:
                self.assertTrue(os.path.exists(p))

    def test_over_budget_evicts_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._cache_with(tmp, [100, 100, 100])
            removed = speech.prune(tmp, budget_bytes=250)
            self.assertEqual(removed, 1)
            self.assertFalse(os.path.exists(paths[0]))
            self.assertTrue(os.path.exists(paths[2]))

    def test_the_newest_entry_is_never_evicted(self):
        # Evicting the file we just wrote would mean synthesising it again on
        # the very next request, forever.
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._cache_with(tmp, [100, 100])
            speech.prune(tmp, budget_bytes=1)
            self.assertTrue(os.path.exists(paths[-1]))

    def test_cache_stats_counts_what_is_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._cache_with(tmp, [10, 20])
            stats = speech.cache_stats(tmp)
            self.assertEqual(stats["entries"], 2)
            self.assertEqual(stats["bytes"], 30)


if __name__ == "__main__":
    unittest.main()
