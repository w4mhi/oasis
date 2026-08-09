"""Regression tests for features/speech/install.py.

The 2026-08-08 speech-dispatcher installer printed a success mark four times
while producing nothing audible on the operator's Pi. These tests pin the
fix: only a real synthesis that produces a non-empty WAV may report success,
a platform Piper cannot serve is declined before pip is ever touched, and a
failed local playback (a headless box has nowhere to play) does not fail the
install.
"""

import importlib.util, os, sys, unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, REPO_ROOT)

_spec = importlib.util.spec_from_file_location(
    "install_speech", os.path.join(REPO_ROOT, "features", "speech", "install.py"))
install_speech = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_speech)


class PlatformGateTest(unittest.TestCase):
    def test_python_310_declines_without_touching_pip(self):
        with mock.patch.object(sys, "version_info", (3, 10, 0)), \
             mock.patch("subprocess.run") as run:
            self.assertEqual(install_speech.run(), 0)   # declining is not failing
            run.assert_not_called()

    def test_armv7l_declines_without_touching_pip(self):
        with mock.patch("platform.machine", return_value="armv7l"), \
             mock.patch("subprocess.run") as run:
            self.assertEqual(install_speech.run(), 0)
            run.assert_not_called()


class VerificationTest(unittest.TestCase):
    """Everything below the pip step is stubbed; what is under test is the
    verification verdict, not the download."""

    def _run_with(self, synth, play_ok=True, player="/usr/bin/pw-play"):
        with mock.patch.object(install_speech, "_install_packages", return_value=0), \
             mock.patch.object(install_speech, "_place_voice", return_value=0), \
             mock.patch.object(install_speech.SPEECH, "synthesize", **synth), \
             mock.patch.object(install_speech.SPEECH, "voice_info",
                               return_value={"name": "en_GB-jenny_dioco-medium",
                                             "model": "/x.onnx",
                                             "sample_rate_hz": 22050}), \
             mock.patch.object(install_speech.PLAY, "player", return_value=player), \
             mock.patch.object(install_speech.PLAY, "play", return_value=play_ok):
            return install_speech.run()

    def test_synthesis_that_raises_fails_the_install(self):
        # The 2026-08-08 installer printed four success marks while producing
        # nothing audible. This is the test that makes that unwritable.
        self.assertEqual(
            self._run_with({"side_effect":
                            install_speech.SPEECH.SpeechUnavailable("nope")}), 1)

    def test_an_empty_wav_fails_the_install(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            empty = fh.name                       # zero bytes on purpose
        try:
            self.assertEqual(self._run_with({"return_value": empty}), 1)
        finally:
            os.unlink(empty)

    def test_a_failed_play_does_not_fail_the_install(self):
        # A headless box has nowhere to play; that is not a broken install.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(b"RIFF....WAVEfake")
            wav = fh.name
        try:
            self.assertEqual(self._run_with({"return_value": wav}, play_ok=False), 0)
        finally:
            os.unlink(wav)

    def test_no_player_at_all_does_not_fail_the_install(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(b"RIFF....WAVEfake")
            wav = fh.name
        try:
            self.assertEqual(self._run_with({"return_value": wav}, player=None), 0)
        finally:
            os.unlink(wav)


if __name__ == "__main__":
    unittest.main()


class GreetingTest(unittest.TestCase):
    """The install utterance names the voice that is actually installed.

    Hardcoding "Jenny" would be a lie on a station running a different .onnx,
    and the operator has no way to correct what the machine says about itself.
    """

    def _greeting_for(self, model_name):
        with mock.patch.object(install_speech.SPEECH, "voice_info",
                               return_value={"name": model_name, "model": "/x.onnx",
                                             "sample_rate_hz": 22050}):
            return install_speech._greeting()

    def test_names_jenny_for_the_jenny_model(self):
        g = self._greeting_for("en_GB-jenny_dioco-medium")
        self.assertIn("Jenny", g)

    def test_names_the_other_voice_when_a_different_model_is_installed(self):
        g = self._greeting_for("en_US-lessac-medium")
        self.assertIn("Lessac", g)
        self.assertNotIn("Jenny", g)

    def test_an_unparseable_model_name_drops_the_name_rather_than_guessing(self):
        for odd in ("", "weird", None):
            with self.subTest(model=odd):
                g = self._greeting_for(odd)
                self.assertTrue(g.strip())
                self.assertIn("announcements", g)

    def test_it_says_what_the_voice_is_for(self):
        # It doubles as the verification utterance; a bare hello leaves the
        # operator knowing it works but not what it does.
        self.assertIn("pass alerts", self._greeting_for("en_GB-jenny_dioco-medium"))

    def test_it_is_within_the_synthesiser_length_cap(self):
        from common import speech as S
        self.assertLessEqual(len(self._greeting_for("en_GB-jenny_dioco-medium")),
                             S.MAX_TEXT_CHARS)
