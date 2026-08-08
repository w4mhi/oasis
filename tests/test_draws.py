import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

from common import draws


class AddOverlayLineTest(unittest.TestCase):
    def test_adds_line_when_absent(self):
        new, changed = draws.add_overlay_line("dtparam=audio=on\n")
        self.assertTrue(changed)
        self.assertIn("dtoverlay=draws", new.splitlines())

    def test_idempotent_when_present(self):
        new, changed = draws.add_overlay_line("dtoverlay=draws\n")
        self.assertFalse(changed)
        self.assertEqual(new.splitlines().count("dtoverlay=draws"), 1)

    def test_commented_line_does_not_count_as_present(self):
        new, changed = draws.add_overlay_line("# dtoverlay=draws\n")
        self.assertTrue(changed)
        self.assertIn("dtoverlay=draws", new.splitlines())


class OverlayAvailableTest(unittest.TestCase):
    def test_true_when_dtbo_present(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "draws.dtbo"), "w").close()
            self.assertTrue(draws.overlay_available(d))

    def test_false_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(draws.overlay_available(d))


class DeviceProbeTest(unittest.TestCase):
    def test_gps_device_present_true(self):
        with mock.patch("os.path.exists", return_value=True):
            self.assertTrue(draws.gps_device_present("/dev/ttySC0"))

    def test_gps_device_present_false(self):
        with mock.patch("os.path.exists", return_value=False):
            self.assertFalse(draws.gps_device_present("/dev/ttySC0"))

    def test_pps_present_checks_pps0(self):
        seen = {}
        def fake_exists(p):
            seen["path"] = p
            return True
        with mock.patch("os.path.exists", fake_exists):
            self.assertTrue(draws.pps_present())
        self.assertEqual(seen["path"], "/dev/pps0")


class SoundCardPresentTest(unittest.TestCase):
    def _cards_file(self, body):
        fd, path = tempfile.mkstemp(suffix=".cards")
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        self.addCleanup(os.unlink, path)
        return path

    def test_true_when_card_listed(self):
        path = self._cards_file(" 3 [draws          ]: simple-card - draws\n")
        self.assertTrue(draws.sound_card_present("draws", path))

    def test_false_when_absent(self):
        path = self._cards_file(" 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n")
        self.assertFalse(draws.sound_card_present("draws", path))

    def test_false_when_cards_file_missing(self):
        self.assertFalse(draws.sound_card_present("draws", "/no/such/cards"))

    def test_match_is_case_insensitive(self):
        path = self._cards_file(" 3 [DRAWS          ]: simple-card - DRAWS\n")
        self.assertTrue(draws.sound_card_present("draws", path))


class EnsureOverlayTest(unittest.TestCase):
    def test_writes_line_and_reports_changed_then_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.txt")
            with open(cfg, "w") as fh:
                fh.write("dtparam=audio=on\n")
            self.assertTrue(draws.ensure_overlay(cfg))          # first: changed
            with open(cfg) as fh:
                body = fh.read()
            self.assertIn("dtoverlay=draws", body.splitlines())
            self.assertFalse(draws.ensure_overlay(cfg))         # second: no-op

    def test_raises_when_no_config(self):
        with mock.patch.object(draws, "config_path", return_value=None):
            with self.assertRaises(RuntimeError):
                draws.ensure_overlay()


if __name__ == "__main__":
    unittest.main()


class SysfsGpioTest(unittest.TestCase):
    """Direwolf's `PTT GPIO n` is a sysfs GLOBAL number = gpiochip base + BCM.
    Modern kernels base the 40-pin bank at a non-zero offset (512 on the bench
    Pi 4), so 12 and 23 must become 524 and 535."""

    def _chip(self, d, name, base, ngpio, label):
        p = os.path.join(d, name)
        os.makedirs(p)
        for f, v in (("base", base), ("ngpio", ngpio), ("label", label)):
            with open(os.path.join(p, f), "w") as fh:
                fh.write(str(v) + "\n")

    def test_finds_the_40pin_bank_and_adds_the_bcm(self):
        with tempfile.TemporaryDirectory() as d:
            self._chip(d, "gpiochip512", 512, 58, "pinctrl-bcm2711")
            self._chip(d, "gpiochip570", 570, 8, "raspberrypi-exp-gpio")
            self.assertEqual(draws.sysfs_gpio(12, d), 524)
            self.assertEqual(draws.sysfs_gpio(23, d), 535)

    def test_ignores_small_expander_chips(self):
        """An 8-line expander must never be mistaken for the header bank."""
        with tempfile.TemporaryDirectory() as d:
            self._chip(d, "gpiochip570", 570, 8, "raspberrypi-exp-gpio")
            self.assertIsNone(draws.sysfs_gpio(12, d))

    def test_none_when_no_chips(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(draws.sysfs_gpio(12, d))

    def test_base_zero_is_honoured(self):
        with tempfile.TemporaryDirectory() as d:
            self._chip(d, "gpiochip0", 0, 54, "pinctrl-bcm2835")
            self.assertEqual(draws.sysfs_gpio(23, d), 23)


class ManagedBlockTest(unittest.TestCase):
    """NW Digital Radio's DRAWS config.txt block is THREE lines, not one: the
    bare `dtoverlay=` resets overlay parameter scope, `dtoverlay=draws` loads the
    card, and `force_turbo=1` pins the core clock the codec derives from. We used
    to insert only the middle one, so install and uninstall were asymmetric —
    and a bare `dtoverlay=` left behind by something else sat next to ours with
    nothing owning it."""

    def test_block_carries_all_three_lines(self):
        self.assertEqual(draws.BLOCK_LINES,
                         ["dtoverlay=", "dtoverlay=draws", "force_turbo=1"])

    def test_apply_inserts_a_marked_block(self):
        new, changed = draws.add_overlay_block("dtparam=audio=on\n")
        self.assertTrue(changed)
        self.assertIn(draws.BLOCK_BEGIN, new)
        self.assertIn(draws.BLOCK_END, new)
        for ln in draws.BLOCK_LINES:
            self.assertIn(ln, new.splitlines())

    def test_apply_is_idempotent(self):
        once, _ = draws.add_overlay_block("dtparam=audio=on\n")
        twice, changed = draws.add_overlay_block(once)
        self.assertFalse(changed)
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(draws.BLOCK_BEGIN), 1)

    def test_round_trips_back_to_the_original(self):
        """Install then uninstall must leave config.txt exactly as found."""
        from common import removal
        orig = "dtparam=audio=on\ndtoverlay=vc4-kms-v3d\n"
        installed, _ = draws.add_overlay_block(orig)
        back, _ = removal.strip_config(installed,
                                       [[draws.BLOCK_BEGIN, draws.BLOCK_END]], [])
        self.assertEqual(back.strip(), orig.strip())

    def test_a_preexisting_bare_dtoverlay_is_left_alone(self):
        """Ours lives inside markers, so an unrelated bare `dtoverlay=` (this
        board had one) is neither adopted nor removed."""
        from common import removal
        orig = "[all]\ndtoverlay=\nforce_turbo=1\n"
        installed, _ = draws.add_overlay_block(orig)
        back, _ = removal.strip_config(installed,
                                       [[draws.BLOCK_BEGIN, draws.BLOCK_END]], [])
        self.assertEqual(back.strip(), orig.strip())


class ConflictingHatGuardTest(unittest.TestCase):
    """Mirror of the DRA-Pi guard: the Setup checkboxes cannot stop a direct
    installer run, which is how the bench box ended up carrying both HATs."""

    def test_detects_the_dra_pi_overlay(self):
        self.assertTrue(draws.conflicting_overlay(
            "dtoverlay=audioinjector-wm8731-audio\n"))

    def test_ignores_a_commented_out_overlay(self):
        self.assertFalse(draws.conflicting_overlay(
            "# dtoverlay=audioinjector-wm8731-audio\n"))

    def test_clean_config_is_fine(self):
        self.assertFalse(draws.conflicting_overlay("dtparam=audio=on\n"))

    def test_guard_is_symmetric_with_the_dra_pi_side(self):
        """Each installer refuses the OTHER board's overlay — neither refuses
        its own, which would make it uninstallable."""
        self.assertFalse(draws.conflicting_overlay("dtoverlay=draws\n"))
