#!/usr/bin/env python3
"""Self-tests for the DRA-Pi config.txt transform.

The install/uninstall pair must be symmetric, and the installer must not
disable audio it has no reason to disable.
"""
import importlib.util as _ilu
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common import removal

_spec = _ilu.spec_from_file_location(
    "enable_dra_pi",
    os.path.join(os.path.dirname(_HERE), "features", "dra-audio-interface",
                 "enable-dra-pi.py"))
dra = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(dra)

STOCK = ("# Enable audio (loads snd_bcm2835)\n"
         "dtparam=audio=on\n"
         "\n"
         "# Enable DRM VC4 V3D driver\n"
         "dtoverlay=vc4-kms-v3d\n"
         "max_framebuffers=2\n")


class LeavesAudioAloneTest(unittest.TestCase):
    """The DRA-Pi is an I2S codec. On-board audio is PWM/headphone and HDMI
    audio goes out HDMI — NEITHER contends for the I2S bus, so disabling them
    was never necessary. Bench 2026-08-06: DRAWS, on-board and both HDMI cards
    all coexist happily with dtparam=audio=on. The old edits killed every sound
    card on the box when the feature was uninstalled."""

    def test_does_not_comment_out_onboard_audio(self):
        new, _ = dra.transform_config(STOCK)
        self.assertIn("dtparam=audio=on", new.splitlines())
        self.assertNotIn(dra.AUDIO_OFF_COMMENT, new)

    def test_block_does_not_force_audio_off(self):
        """A `dtparam=audio=off` inside the block would win regardless of what
        the stock line says — removing only the comment-out would be useless."""
        self.assertNotIn("dtparam=audio=off", dra.BLOCK_LINES)

    def test_does_not_add_noaudio_to_the_kms_overlay(self):
        new, _ = dra.transform_config(STOCK)
        self.assertIn("dtoverlay=vc4-kms-v3d", new.splitlines())
        self.assertNotIn("dtoverlay=vc4-kms-v3d,noaudio", new)

    def test_still_loads_the_codec(self):
        new, _ = dra.transform_config(STOCK)
        self.assertIn("dtoverlay=audioinjector-wm8731-audio", new.splitlines())
        self.assertIn("dtparam=i2c_arm=on", new.splitlines())


class RoundTripTest(unittest.TestCase):
    def _undo(self, text, rec):
        return removal.strip_config(text, rec.get("config_blocks", []),
                                    rec.get("config_lines", []),
                                    rec.get("config_subs", []))[0]

    def test_install_then_uninstall_restores_the_original(self):
        installed, _ = dra.transform_config(STOCK)
        self.assertEqual(self._undo(installed, dra.removal_record()).strip(),
                         STOCK.strip())

    def test_install_is_idempotent(self):
        once, _ = dra.transform_config(STOCK)
        twice, _ = dra.transform_config(once)
        self.assertEqual(once, twice)

    def test_uninstall_still_repairs_a_LEGACY_install(self):
        """Boxes installed before this fix carry the old edits. Uninstall must
        still put them back — that is why config_subs stays."""
        legacy = STOCK.replace("dtparam=audio=on", dra.AUDIO_OFF_COMMENT) \
                      .replace("dtoverlay=vc4-kms-v3d", dra.VC4_NOAUDIO)
        legacy += "\n" + "\n".join([dra.BLOCK_BEGIN, "dtparam=audio=off",
                                    dra.BLOCK_END]) + "\n"
        repaired = self._undo(legacy, dra.removal_record())
        self.assertIn("dtparam=audio=on", repaired.splitlines())
        self.assertIn("dtoverlay=vc4-kms-v3d", repaired.splitlines())
        self.assertNotIn("dtparam=audio=off", repaired)


if __name__ == "__main__":
    unittest.main()
