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
        # run() gates on a real .venv/bin/python in REPO_ROOT before it reaches
        # anything these tests care about. Left unstubbed, the verdict under test
        # depends on whether a venv happens to sit beside the checkout: green in
        # the maintainer's tree and in CI, red in a fresh clone or git worktree,
        # for a reason that has nothing to do with speech. sys.executable always
        # exists, so this says "the venv is there" without touching the disk.
        with mock.patch.object(install_speech.S, "_venv_bin", return_value=sys.executable), \
             mock.patch.object(install_speech, "_install_packages", return_value=0), \
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


class VenvGateTest(unittest.TestCase):
    """The gate VerificationTest stubs away, tested on purpose instead of by
    accident. It used to be exercised only as a side effect of the maintainer's
    checkout having a venv, which meant it was never really asserted at all."""

    def test_a_missing_venv_fails_the_install(self):
        with mock.patch.object(install_speech.S, "_venv_bin",
                               return_value="/nonexistent/bin/python"), \
             mock.patch.object(install_speech, "_install_packages") as pkgs:
            self.assertEqual(install_speech.run(), 1)
            # Speech installs INTO the server venv; without one there is nothing
            # to install into, so pip must never be reached.
            pkgs.assert_not_called()


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
        # operator knowing it works but not what it does. "Station
        # announcements" rather than "pass alerts" on purpose — it covers the
        # passes and does not go stale the first time something else speaks.
        self.assertIn("station announcements",
                      self._greeting_for("en_GB-jenny_dioco-medium"))

    def test_the_two_copies_of_the_greeting_still_match(self):
        """The kiosk avatar card carries its own copy (jennyGreeting in
        dashboard.html) so that tapping Jenny is a cache HIT on the WAV the
        installer already synthesised. Let the two drift and the tap silently
        becomes a fresh render on the Pi — and the two surfaces introduce the
        voice differently."""
        dash = os.path.join(REPO_ROOT, "oasis-dashboard", "dashboard.html")
        with open(dash, encoding="utf-8") as fh:
            html = fh.read()
        spoken = self._greeting_for("en_GB-jenny_dioco-medium")
        # The JS builds the name by interpolation, so compare around it.
        head, tail = spoken.split("Jenny", 1)
        self.assertIn(head + "${who}" + tail, html)

    def test_it_is_within_the_synthesiser_length_cap(self):
        from common import speech as S
        self.assertLessEqual(len(self._greeting_for("en_GB-jenny_dioco-medium")),
                             S.MAX_TEXT_CHARS)


class AttributionTest(unittest.TestCase):
    """The Jenny dataset's licence requires attribution from any interface that
    generates audio on user action — which is what OASIS is. It asks that the
    voice be called "Jenny" and, where at all practical, "Jenny (Dioco)".

    It is NOT CC-BY. These assertions exist because the wrong licence name was
    the starting assumption, and describing it as CC-BY would state obligations
    (a licence URL, a statement of changes) that this licence does not impose
    while omitting the naming requirement that it does.
    """

    def test_uses_the_required_name_form(self):
        self.assertIn("Jenny (Dioco)", install_speech.ATTRIBUTION)

    def test_credits_the_engine_and_its_licence(self):
        text = install_speech.ATTRIBUTION
        self.assertIn("Piper", text)
        self.assertIn("GPL-3.0", text)
        self.assertIn("OHF-voice/piper1-gpl", text)

    def test_points_at_the_dataset_that_carries_the_terms(self):
        self.assertIn("dioco-group/jenny-tts-dataset", install_speech.ATTRIBUTION)

    def test_does_not_call_it_cc_by(self):
        self.assertNotIn("CC-BY", install_speech.ATTRIBUTION.upper().replace(" ", ""))

    def test_it_is_written_beside_the_model(self):
        # A credit in a README is no use once the .onnx has been copied onto
        # someone else's SD card; the notice has to travel with the file.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            install_speech._write_attribution(tmp)
            path = os.path.join(tmp, "ATTRIBUTION.txt")
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as fh:
                self.assertIn("Jenny (Dioco)", fh.read())

    def test_an_unwritable_dir_does_not_fail_the_install(self):
        with mock.patch("builtins.open", side_effect=OSError("read-only")):
            install_speech._write_attribution("/nowhere")   # must not raise


class SinkGainTest(unittest.TestCase):
    """The output level the ALSA mixer cannot show.

    On a PipeWire box the control alsamixer displays for vc4-hdmi is an
    ATTENUATOR whose maximum is 0 dB, so a card pinned at the top reads "0 dB"
    and looks like no gain at all -- while the actual attenuation sits in a
    PipeWire sink alsamixer never renders. A station was found at ALSA unity
    and PipeWire 0.40, i.e. -7.96 dB, with nothing in the mixer being looked at
    to explain it.
    """

    def _mod(self):
        import importlib.util
        path = os.path.join(REPO_ROOT, "features", "speech", "install.py")
        spec = importlib.util.spec_from_file_location("_speech_install", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_it_reads_the_volume_out_of_wpctl(self):
        m = self._mod()
        self.assertEqual(m._sink_volume(run=lambda a: "Volume: 0.40"), 0.40)
        # Muted is a different problem; the number still has to come out.
        self.assertEqual(m._sink_volume(run=lambda a: "Volume: 0.65 [MUTED]"), 0.65)

    def test_no_wpctl_is_a_question_not_an_answer(self):
        # None is not zero. A headless or ALSA-only station has no sink to read
        # and must not be advised about one.
        m = self._mod()
        self.assertIsNone(m._sink_volume(run=lambda a: None))
        self.assertEqual(m._gain_plan(None)[0], "unknown")

    def test_attenuation_is_raised_to_unity(self):
        m = self._mod()
        action, msg = m._gain_plan(0.40)
        self.assertEqual(action, "raise")
        self.assertIn("0.40", msg)
        self.assertIn("-8.0 dB", msg)          # 20*log10(0.4)
        self.assertIn("PipeWire", msg, "the message must name the mixer that owns it")

    def test_unity_and_above_are_left_alone(self):
        # RAISES, never lowers -- the same rule every installer here follows
        # about versions. A shack deliberately run quiet must not be ambushed,
        # and >1.0 is digital gain the operator asked for.
        m = self._mod()
        self.assertEqual(m._gain_plan(1.0)[0], "ok")
        self.assertEqual(m._gain_plan(1.5)[0], "ok")

    def test_a_silent_sink_does_not_produce_an_infinite_decibel(self):
        m = self._mod()
        action, msg = m._gain_plan(0.0)
        self.assertEqual(action, "raise")
        self.assertIn("silent", msg)
        self.assertNotIn("inf", msg)

    def test_apply_only_writes_when_there_is_attenuation(self):
        m = self._mod()
        calls = []

        def run(argv):
            calls.append(argv)
            return "Volume: 1.00" if argv[1] == "get-volume" else ""

        self.assertEqual(m._apply_sink_gain(run=run), "ok")
        self.assertEqual([c for c in calls if c[1] == "set-volume"], [],
                         "a sink already at unity must not be written to")

    def test_apply_raises_and_says_so(self):
        m = self._mod()
        calls = []

        def run(argv):
            calls.append(argv)
            return "Volume: 0.40" if argv[1] == "get-volume" else ""

        self.assertEqual(m._apply_sink_gain(run=run), "raised")
        self.assertIn(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.0"], calls)

    def test_a_refused_set_reports_the_command_instead_of_failing_the_install(self):
        # Speech that synthesised correctly must not fail because a volume
        # could not be set -- same posture as the playback check.
        m = self._mod()

        def run(argv):
            return "Volume: 0.40" if argv[1] == "get-volume" else None

        self.assertEqual(m._apply_sink_gain(run=run), "failed")
