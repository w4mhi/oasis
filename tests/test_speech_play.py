"""common/speech_play.py — playing a WAV out of this box's own speaker.

The finding these tests pin: the OASIS unit is a SYSTEM service running as the
operator's user (scripts/enable-autostart-pi.py:209). It has the right UID but
NOT the session environment, so $XDG_RUNTIME_DIR is unset and pw-play cannot
find PipeWire's socket — even though that socket is owned by the very UID we
are running as. Injecting it is one line, and finding it out on the Pi instead
of here costs an evening.
"""
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common import speech_play                              # noqa: E402


class PlayerProbeTest(unittest.TestCase):
    def setUp(self):
        speech_play.player.cache_clear()

    def tearDown(self):
        speech_play.player.cache_clear()

    def test_pw_play_wins_when_present(self):
        with mock.patch("shutil.which", side_effect=lambda n: f"/usr/bin/{n}"):
            self.assertEqual(speech_play.player(), "/usr/bin/pw-play")

    def test_falls_through_to_aplay(self):
        # The CM4 measured on 2026-08-08 has pw-play and aplay but NO paplay.
        def which(name):
            return f"/usr/bin/{name}" if name == "aplay" else None
        with mock.patch("shutil.which", side_effect=which):
            self.assertEqual(speech_play.player(), "/usr/bin/aplay")

    def test_paplay_wins_over_aplay(self):
        # The middle of a three-step order is the step that silently rots.
        def which(name):
            return None if name == "pw-play" else f"/usr/bin/{name}"
        with mock.patch("shutil.which", side_effect=which):
            self.assertEqual(speech_play.player(), "/usr/bin/paplay")

    def test_no_player_at_all_is_none_not_a_crash(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(speech_play.player())


class ChildEnvTest(unittest.TestCase):
    def test_xdg_runtime_dir_is_injected_when_missing(self):
        env = {"PATH": "/usr/bin"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("os.geteuid", return_value=1000), \
             mock.patch("os.path.isdir", return_value=True):
            self.assertEqual(speech_play.child_env()["XDG_RUNTIME_DIR"], "/run/user/1000")

    def test_an_existing_xdg_runtime_dir_is_left_alone(self):
        env = {"XDG_RUNTIME_DIR": "/run/user/999"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("os.geteuid", return_value=1000), \
             mock.patch("os.path.isdir", return_value=True):
            self.assertEqual(speech_play.child_env()["XDG_RUNTIME_DIR"], "/run/user/999")

    def test_nothing_is_invented_when_the_runtime_dir_does_not_exist(self):
        # A truly headless box with no lingering session: aplay still works, and
        # pointing PipeWire at a directory that isn't there helps nobody.
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("os.geteuid", return_value=1000), \
             mock.patch("os.path.isdir", return_value=False):
            self.assertNotIn("XDG_RUNTIME_DIR", speech_play.child_env())


class PlayTest(unittest.TestCase):
    def setUp(self):
        speech_play.player.cache_clear()

    def tearDown(self):
        speech_play.player.cache_clear()

    def test_no_player_means_false_not_an_exception(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(speech_play.play("/tmp/x.wav"))

    def test_the_path_is_argv_and_no_shell_is_used(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return mock.Mock(returncode=0)

        with mock.patch("shutil.which", side_effect=lambda n: f"/usr/bin/{n}"), \
             mock.patch("subprocess.run", side_effect=fake_run):
            self.assertTrue(speech_play.play("/tmp/x.wav"))
        self.assertEqual(seen["argv"], ["/usr/bin/pw-play", "/tmp/x.wav"])
        self.assertFalse(seen["kwargs"].get("shell", False))
        self.assertTrue(seen["kwargs"].get("timeout"))

    def test_a_nonzero_exit_is_false(self):
        # stderr MUST be set on the mock. mock.Mock(returncode=1) auto-vivifies
        # stderr as a child Mock, which is truthy and whose .decode() returns
        # another Mock — so the error-logging line raises TypeError and the
        # production code gets blamed for an under-specified test.
        with mock.patch("shutil.which", side_effect=lambda n: f"/usr/bin/{n}"), \
             mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=1, stderr=b"boom")):
            self.assertFalse(speech_play.play("/tmp/x.wav"))

    def test_a_second_call_is_dropped_while_one_is_already_speaking(self):
        # Two announcements at once are mush, and a queued-up backlog of stale
        # warnings is worse than a dropped one.
        speech_play._LOCK.acquire()
        try:
            with mock.patch("shutil.which", side_effect=lambda n: f"/usr/bin/{n}"):
                self.assertFalse(speech_play.play("/tmp/x.wav", wait_s=0.01))
        finally:
            speech_play._LOCK.release()


if __name__ == "__main__":
    unittest.main()
