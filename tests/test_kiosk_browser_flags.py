#!/usr/bin/env python3
"""The Chromium kiosk launcher's flags — the ones whose ABSENCE is silent.

Every flag pinned here fixes a fault that produces no error message and nothing
on screen: the kiosk simply doesn't make a sound, or renders a blank map, or
burns a core. That makes them exactly the flags a future tidy-up drops without
noticing, so they are asserted rather than trusted to a comment.

The launcher is a generated shell script, so these tests read what would be
WRITTEN — no root, no Chromium, no Pi required.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(_HERE)
sys.path.insert(0, REPO)

# Hyphenated filename — not importable by name.
_spec = importlib.util.spec_from_file_location(
    "enable_autostart_pi", os.path.join(REPO, "scripts", "enable-autostart-pi.py"))
autostart = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(autostart)


class LauncherFlagsTest(unittest.TestCase):
    def setUp(self):
        self.written = {}
        self._real_write = autostart._sudo_write
        self._real_run = autostart._run
        self._real_chown = autostart._chown_to
        autostart._sudo_write = lambda path, content: self.written.__setitem__(path, content)
        autostart._chown_to = lambda path, user: None    # no sudo in a test

        class _R:
            returncode = 0
        autostart._run = lambda *a, **k: _R()
        # A real HOME: install_browser also writes the desktop autostart entry
        # there, and /home/pi does not exist on a dev box.
        self._home = tempfile.mkdtemp()

    def tearDown(self):
        autostart._sudo_write = self._real_write
        autostart._run = self._real_run
        autostart._chown_to = self._real_chown

    def _launcher(self, **kw):
        autostart.install_browser("pi", self._home, **kw)
        return self.written[autostart.BROWSER_BIN]

    def test_autoplay_policy_is_set_or_the_kiosk_is_mute(self):
        """Without this, an AudioContext stays SUSPENDED until a user gesture.

        A kiosk boots unattended and is never touched, so no gesture ever
        arrives and the satellite pass chime never sounds — with nothing on
        screen to say why. This one flag is the difference between the shack
        being audible and silently deaf."""
        self.assertIn("--autoplay-policy=no-user-gesture-required", self._launcher())

    def test_speech_dispatcher_is_enabled_or_there_is_no_voice(self):
        """Chromium gates Web Speech behind this flag on Linux, so kiosk mode
        exposes zero voices without it and the spoken pass heads-up silently
        degrades to chime-only (see services/satellites/install-voice.py)."""
        self.assertIn("--enable-speech-dispatcher", self._launcher())

    def test_gl_backend_is_angle_not_egl(self):
        """--use-gl=egl left MapLibre's WebGL context uninitialised on
        vc4-kms-v3d: the map rendered BLANK while its data loaded fine."""
        launcher = self._launcher()
        self.assertIn("--use-gl=angle", launcher)
        self.assertNotIn("--use-gl=egl", launcher)

    def test_breakpad_is_disabled(self):
        """Crash uploads go nowhere offline; leaving breakpad on had
        crashpad_handler churning a core writing minidumps on every blip."""
        self.assertIn("--disable-breakpad", self._launcher())

    def test_launcher_waits_for_the_server_before_opening(self):
        """Chromium starting before Flask is up shows an error page and stays
        there — the kiosk never retries on its own."""
        launcher = self._launcher()
        self.assertIn("curl", launcher)
        self.assertIn("sleep", launcher)

    def test_resolution_adds_touch_and_native_scale(self):
        launcher = self._launcher(resolution="1024x600")
        self.assertIn("--window-size=1024,600", launcher)
        self.assertIn("--touch-events=enabled", launcher)
        # Fractional scaling forces a repaint path the Pi cannot afford.
        self.assertIn("--force-device-scale-factor=1", launcher)


if __name__ == "__main__":
    unittest.main()
