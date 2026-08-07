"""Tests for common/rtc.py — the two RTC board presets and, above all, the
config.txt block boundary.

The boundary is the safety property: an RTC feature owns exactly ONE line, its
i2c-rtc overlay, inside its own BEGIN/END block. Everything else the board needs
is a prerequisite — added when missing, but always outside the block and never
removed, because it belongs to hardware that outlives the clock.
`dtoverlay=vc4-kms-dsi-7inch,dsi1` IS the Raspad's screen, so no uninstall path
may ever take it out (cf. the installer that once left a Pi with no sound cards).
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from common import rtc  # noqa: E402
from common import removal  # noqa: E402

# A realistic Raspad config.txt: the DSI display overlay is already there, next
# to the stock vc4-kms-v3d line, and OASIS did not put it there.
RASPAD_CFG = """\
# stock Raspberry Pi OS config.txt
dtparam=i2c_arm=on
dtoverlay=vc4-kms-v3d
dtoverlay=vc4-kms-dsi-7inch,dsi1
dtoverlay=audioinjector-wm8731-audio
"""


class BoardTableTest(unittest.TestCase):
    def test_overlays_match_the_hardware(self):
        self.assertEqual(rtc.BOARDS["wittypi"]["overlay"], "dtoverlay=i2c-rtc,ds3231")
        self.assertEqual(rtc.BOARDS["bigtreetech-7in"]["overlay"],
                         "dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi")

    def test_each_board_declares_its_own_chip_and_bus(self):
        # The chip name is what the UI shows in parentheses and what verify()
        # matches against /sys/class/rtc/rtc0/name.
        self.assertEqual(rtc.BOARDS["wittypi"]["chip"], "ds3231")
        self.assertEqual(rtc.BOARDS["wittypi"]["bus"], 1)
        self.assertEqual(rtc.BOARDS["bigtreetech-7in"]["chip"], "pcf8563")
        self.assertEqual(rtc.BOARDS["bigtreetech-7in"]["bus"], 10)

    def test_markers_are_per_board(self):
        # One shared "OASIS RTC" block would let removing either feature strip
        # the other's lines.
        self.assertNotEqual(rtc.block_markers("wittypi"),
                            rtc.block_markers("bigtreetech-7in"))


class PlanLinesTest(unittest.TestCase):
    def test_only_the_rtc_overlay_is_ever_owned(self):
        # The display overlay is NEVER owned — not even when we have to add it.
        for cfg in (RASPAD_CFG, "dtoverlay=vc4-kms-v3d\n", ""):
            owned, _, _ = rtc.plan_lines(cfg, "bigtreetech-7in")
            self.assertEqual(owned, ["dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi"])

    def test_preexisting_display_overlay_is_left_alone(self):
        owned, prereq_add, present = rtc.plan_lines(RASPAD_CFG, "bigtreetech-7in")
        self.assertEqual(owned, ["dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi"])
        self.assertEqual(prereq_add, [])
        self.assertEqual(present, ["dtoverlay=vc4-kms-dsi-7inch,dsi1"])

    def test_missing_display_overlay_is_added_as_a_prerequisite(self):
        cfg = "dtoverlay=vc4-kms-v3d\n"
        owned, prereq_add, present = rtc.plan_lines(cfg, "bigtreetech-7in")
        self.assertEqual(prereq_add, ["dtoverlay=vc4-kms-dsi-7inch,dsi1"])
        self.assertEqual(owned, ["dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi"])
        self.assertEqual(present, [])

    def test_shared_i2c_arm_is_a_prerequisite_too(self):
        # dtparam=i2c_arm=on is shared with every other I2C user on the box.
        owned, prereq_add, present = rtc.plan_lines(RASPAD_CFG, "wittypi")
        self.assertEqual(owned, ["dtoverlay=i2c-rtc,ds3231"])
        self.assertEqual(prereq_add, [])
        self.assertEqual(present, ["dtparam=i2c_arm=on"])
        owned, prereq_add, _ = rtc.plan_lines("", "wittypi")
        self.assertEqual(prereq_add, ["dtparam=i2c_arm=on"])
        self.assertEqual(owned, ["dtoverlay=i2c-rtc,ds3231"])

    def test_commented_out_line_does_not_count_as_present(self):
        cfg = "#dtoverlay=vc4-kms-dsi-7inch,dsi1\n"
        _, prereq_add, _ = rtc.plan_lines(cfg, "bigtreetech-7in")
        self.assertIn("dtoverlay=vc4-kms-dsi-7inch,dsi1", prereq_add)

    def test_parameterised_display_overlay_counts_as_present(self):
        # A tuned display line is the SAME overlay on the same DSI port; appending
        # our plain version would load vc4-kms-dsi-7inch twice with conflicting
        # parameters.
        cfg = "dtoverlay=vc4-kms-dsi-7inch,dsi1,rotate=180\n"
        owned, prereq_add, present = rtc.plan_lines(cfg, "bigtreetech-7in")
        self.assertEqual(prereq_add, [])
        self.assertEqual(present, ["dtoverlay=vc4-kms-dsi-7inch,dsi1"])
        self.assertEqual(owned, ["dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi"])

    def test_other_boards_rtc_overlay_is_not_mistaken_for_ours(self):
        # Same overlay name, different chip: the Witty Pi's line must NOT satisfy
        # the BigTreeTech requirement, or the RTC overlay is silently skipped.
        cfg = "dtoverlay=i2c-rtc,ds3231\n"
        owned, _, present = rtc.plan_lines(cfg, "bigtreetech-7in")
        self.assertIn("dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi", owned)
        self.assertNotIn("dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi", present)

    def test_a_different_dsi_port_is_not_a_match(self):
        cfg = "dtoverlay=vc4-kms-dsi-7inch,dsi0\n"
        _, prereq_add, _ = rtc.plan_lines(cfg, "bigtreetech-7in")
        self.assertIn("dtoverlay=vc4-kms-dsi-7inch,dsi1", prereq_add)


class RenderConfigTest(unittest.TestCase):
    def test_writes_only_the_rtc_overlay_on_a_raspad(self):
        new_text, owned, _, _ = rtc.render_config(RASPAD_CFG, "bigtreetech-7in")
        begin, end = rtc.block_markers("bigtreetech-7in")
        self.assertIn(begin, new_text)
        self.assertIn("dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi", new_text)
        # The display overlay appears exactly once — its original line, NOT a
        # duplicate inside our block.
        self.assertEqual(new_text.count("dtoverlay=vc4-kms-dsi-7inch,dsi1"), 1)
        block = new_text[new_text.index(begin):new_text.index(end)]
        self.assertNotIn("vc4-kms-dsi-7inch", block)
        self.assertEqual(owned, ["dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi"])

    def test_idempotent(self):
        once, _, _, _ = rtc.render_config(RASPAD_CFG, "bigtreetech-7in")
        twice, _, _, _ = rtc.render_config(once, "bigtreetech-7in")
        self.assertEqual(once, twice)

    def test_idempotent_when_a_prerequisite_had_to_be_added(self):
        # The prereq note + line land outside the block; re-rendering must not
        # append a second copy.
        once, _, _, _ = rtc.render_config("dtoverlay=vc4-kms-v3d\n", "bigtreetech-7in")
        twice, _, _, _ = rtc.render_config(once, "bigtreetech-7in")
        self.assertEqual(once, twice)
        self.assertEqual(once.count("dtoverlay=vc4-kms-dsi-7inch,dsi1"), 1)

    def test_added_prerequisite_lands_outside_the_block(self):
        new_text, _, prereq_add, _ = rtc.render_config("dtoverlay=vc4-kms-v3d\n",
                                                       "bigtreetech-7in")
        begin, end = rtc.block_markers("bigtreetech-7in")
        self.assertEqual(prereq_add, ["dtoverlay=vc4-kms-dsi-7inch,dsi1"])
        block = new_text[new_text.index(begin):new_text.index(end)]
        self.assertNotIn("vc4-kms-dsi-7inch", block)
        self.assertIn("dtoverlay=vc4-kms-dsi-7inch,dsi1", new_text)

    def test_both_boards_coexist(self):
        a, _, _, _ = rtc.render_config(RASPAD_CFG, "bigtreetech-7in")
        b, _, _, _ = rtc.render_config(a, "wittypi")
        self.assertIn("dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi", b)
        self.assertIn("dtoverlay=i2c-rtc,ds3231", b)
        for board in ("wittypi", "bigtreetech-7in"):
            self.assertIn(rtc.block_markers(board)[0], b)

    def test_preserves_unrelated_lines(self):
        new_text, _, _, _ = rtc.render_config(RASPAD_CFG, "bigtreetech-7in")
        self.assertIn("dtoverlay=audioinjector-wm8731-audio", new_text)
        self.assertIn("dtoverlay=vc4-kms-v3d", new_text)


class RemovalBoundaryTest(unittest.TestCase):
    """Install then remove, through the real strip_config() the uninstaller uses."""

    def _round_trip(self, cfg, board):
        installed, _, _, _ = rtc.render_config(cfg, board)
        rec = rtc.removal_record("/repo", board)
        removed, _ = removal.strip_config(installed,
                                          [tuple(b) for b in rec["config_blocks"]],
                                          rec.get("config_lines", []))
        return installed, removed

    def test_uninstall_keeps_the_preexisting_display_overlay(self):
        # The whole point: the screen must still work after uninstalling.
        _, removed = self._round_trip(RASPAD_CFG, "bigtreetech-7in")
        self.assertIn("dtoverlay=vc4-kms-dsi-7inch,dsi1", removed)
        self.assertNotIn("dtoverlay=i2c-rtc,pcf8563", removed)

    def test_uninstall_keeps_shared_i2c_arm(self):
        _, removed = self._round_trip(RASPAD_CFG, "wittypi")
        self.assertIn("dtparam=i2c_arm=on", removed)
        self.assertNotIn("dtoverlay=i2c-rtc,ds3231", removed)

    def test_uninstall_keeps_a_display_overlay_WE_added(self):
        # The line is the Raspad's SCREEN, not an RTC artifact: even when this
        # installer put it there, teardown must leave it. Only the i2c-rtc line
        # goes.
        cfg = "dtoverlay=vc4-kms-v3d\n"
        installed, removed = self._round_trip(cfg, "bigtreetech-7in")
        self.assertIn("dtoverlay=vc4-kms-dsi-7inch,dsi1", installed)
        self.assertIn("dtoverlay=vc4-kms-dsi-7inch,dsi1", removed)
        self.assertNotIn("dtoverlay=i2c-rtc,pcf8563", removed)
        self.assertIn("dtoverlay=vc4-kms-v3d", removed)

    def test_uninstall_keeps_an_i2c_arm_WE_added(self):
        installed, removed = self._round_trip("", "wittypi")
        self.assertIn("dtparam=i2c_arm=on", installed)
        self.assertIn("dtparam=i2c_arm=on", removed)
        self.assertNotIn("dtoverlay=i2c-rtc,ds3231", removed)

    def test_removing_one_board_leaves_the_other(self):
        both, _, _, _ = rtc.render_config(
            rtc.render_config(RASPAD_CFG, "bigtreetech-7in")[0], "wittypi")
        rec = rtc.removal_record("/repo", "wittypi")
        removed, _ = removal.strip_config(both, [tuple(b) for b in rec["config_blocks"]], [])
        self.assertNotIn("dtoverlay=i2c-rtc,ds3231", removed)
        self.assertIn("dtoverlay=i2c-rtc,pcf8563,i2c_csi_dsi", removed)

    def test_record_restores_hwclock_set_and_asks_for_a_reboot(self):
        rec = rtc.removal_record("/repo", "bigtreetech-7in")
        self.assertTrue(rec["requires_reboot"])
        self.assertEqual(rec["restore"], [[rtc.HWCLOCK_SET + ".oasis.bak", rtc.HWCLOCK_SET]])


class ThinCliTest(unittest.TestCase):
    def _load(self, path):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "rtc_cli_" + os.path.basename(os.path.dirname(path)).replace("-", "_"), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_each_feature_cli_reports_its_own_board_record(self):
        root = os.path.dirname(_HERE)
        hat = self._load(os.path.join(root, "features", "rtc-hat", "enable-rtc.py"))
        pad = self._load(os.path.join(root, "features", "rtc-raspad", "enable-rtc.py"))
        self.assertEqual(hat.BOARD, "wittypi")
        self.assertEqual(pad.BOARD, "bigtreetech-7in")
        self.assertEqual(hat.removal_record("/repo"), rtc.removal_record("/repo", "wittypi"))
        self.assertEqual(pad.removal_record("/repo"),
                         rtc.removal_record("/repo", "bigtreetech-7in"))
        # Distinct blocks, so uninstalling one never touches the other's lines.
        self.assertNotEqual(hat.removal_record("/repo")["config_blocks"],
                            pad.removal_record("/repo")["config_blocks"])


if __name__ == "__main__":
    unittest.main()
