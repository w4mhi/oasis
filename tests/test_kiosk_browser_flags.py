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


class SingleAutostartTest(unittest.TestCase):
    """One autostart mechanism per station, or the kiosk starts twice.

    The original code wrote the XDG entry unconditionally and ADDED the labwc
    line when labwc was present, believing labwc ignored the XDG one. Pi OS
    Trixie honours both, so every Wayland kiosk came up twice: two fullscreen
    Chromiums stacked on one screen. Nothing about that is visible — the top
    window looks entirely normal — but the two browsers hold separate page
    state, so muting the pass alerts on the window you can see leaves the one
    behind it chiming with no control that can reach it.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._real_write = autostart._sudo_write
        self._real_run = autostart._run
        self._real_chown = autostart._chown_to
        self._real_comp = autostart._running_compositor
        autostart._sudo_write = lambda path, content: None
        autostart._chown_to = lambda path, user: None

        class _R:
            returncode = 0
        autostart._run = lambda *a, **k: _R()

    def tearDown(self):
        autostart._sudo_write = self._real_write
        autostart._run = self._real_run
        autostart._chown_to = self._real_chown
        autostart._running_compositor = self._real_comp

    def _install(self, compositor):
        autostart._running_compositor = lambda: compositor
        autostart.install_browser("pi", self.home)
        labwc = autostart._labwc_autostart(self.home)
        line = ""
        if os.path.exists(labwc):
            with open(labwc, encoding="utf-8") as f:
                line = f.read()
        return (autostart.BROWSER_BIN in line,
                os.path.exists(autostart._xdg_autostart(self.home)))

    def test_labwc_gets_the_labwc_line_and_no_xdg_entry(self):
        in_labwc, has_xdg = self._install("labwc")
        self.assertTrue(in_labwc)
        self.assertFalse(has_xdg, "labwc honours the XDG entry too — writing "
                                  "both is what starts two kiosks")

    def test_x11_gets_the_xdg_entry_and_no_labwc_line(self):
        in_labwc, has_xdg = self._install("openbox")
        self.assertTrue(has_xdg)
        self.assertFalse(in_labwc)

    def test_wayfire_keeps_the_xdg_entry_or_nothing_starts(self):
        """wayfire needs a manual wayfire.ini edit, so the XDG entry is the only
        thing that will actually launch the kiosk. Treating wayfire as its own
        kind and dropping XDG would leave the station with no kiosk at all."""
        _, has_xdg = self._install("wayfire")
        self.assertTrue(has_xdg)

    def test_re_running_repairs_a_station_that_has_both(self):
        """The repair path, and the reason this removes rather than merely
        declines to write: every Pi installed by an earlier version already has
        both entries, and they are only reachable by running this again."""
        os.makedirs(os.path.join(self.home, ".config", "autostart"))
        with open(autostart._xdg_autostart(self.home), "w") as f:
            f.write(f"[Desktop Entry]\nExec={autostart.BROWSER_BIN}\n")
        in_labwc, has_xdg = self._install("labwc")
        self.assertTrue(in_labwc)
        self.assertFalse(has_xdg)

    def test_removing_the_labwc_line_leaves_the_rest_of_the_file(self):
        """That file is the operator's, not ours — it can hold their own
        startup commands, and this must never be the reason those stop running."""
        os.makedirs(os.path.join(self.home, ".config", "labwc"))
        path = autostart._labwc_autostart(self.home)
        with open(path, "w") as f:
            f.write(f"pcmanfm --desktop &\n{autostart.BROWSER_BIN} &\nkanshi &\n")
        self.assertTrue(autostart._remove_labwc_line(self.home))
        with open(path, encoding="utf-8") as f:
            rest = f.read()
        self.assertIn("pcmanfm --desktop &", rest)
        self.assertIn("kanshi &", rest)
        self.assertNotIn(autostart.BROWSER_BIN, rest)

    def test_disable_removes_both_whichever_was_installed(self):
        """--disable that leaves one behind has not disabled anything: the
        kiosk still comes up on next login."""
        os.makedirs(os.path.join(self.home, ".config", "autostart"))
        os.makedirs(os.path.join(self.home, ".config", "labwc"))
        with open(autostart._xdg_autostart(self.home), "w") as f:
            f.write("[Desktop Entry]\n")
        with open(autostart._labwc_autostart(self.home), "w") as f:
            f.write(f"{autostart.BROWSER_BIN} &\n")
        self.assertTrue(autostart._remove_xdg_autostart(self.home))
        self.assertTrue(autostart._remove_labwc_line(self.home))
        self.assertFalse(os.path.exists(autostart._xdg_autostart(self.home)))
        with open(autostart._labwc_autostart(self.home), encoding="utf-8") as f:
            self.assertNotIn(autostart.BROWSER_BIN, f.read())

    def test_the_compositor_is_read_from_the_process_table(self):
        """Not from the OS release. "Trixie means labwc" is the inference that
        caused this bug; a Pi can boot X11 on Trixie."""
        autostart._running_compositor = self._real_comp
        calls = []

        class _R:
            returncode = 1
        autostart._run = lambda cmd, **k: (calls.append(cmd), _R())[1]
        autostart._running_compositor()
        self.assertTrue(any(c[:2] == ["pgrep", "-x"] for c in calls))


if __name__ == "__main__":
    unittest.main()
