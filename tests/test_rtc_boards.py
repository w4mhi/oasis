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
import re
import sys
import unittest
from unittest import mock

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


class NeverSurrenderTheFallback(unittest.TestCase):
    """The bug that stranded two stations.

    `run()` used to remove fake-hwclock as its SECOND step — before the RTC
    could possibly work. The config.txt block does nothing until a reboot, and
    `hwclock` was not installed until the step AFTER the removal, so a box whose
    RTC turned out to be absent, unseeded or battery-less lost its clock
    entirely. pi4oasis had no /dev/rtc0 and a masked fallback, booting three
    weeks stale; pi5draws came up at 1970 and was advanced to a month-old
    timestamp fossil.

    Source-level, in the style of tests/test_station_json_writes.py: the
    guarantee is that NO code path in the RTC feature disarms the fallback, and
    only reading the source can promise that."""

    def _rtc_sources(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths = [os.path.join(root, "common", "rtc.py")]
        for d in ("rtc-hat", "rtc-raspad", "rtc-pi5"):
            p = os.path.join(root, "features", d, "enable-rtc.py")
            if os.path.exists(p):
                paths.append(p)
        return paths

    def test_no_rtc_code_path_removes_or_disables_fake_hwclock(self):
        bad = []
        for path in self._rtc_sources():
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if line.lstrip().startswith("#"):
                        continue          # comments explain the history on purpose
                    low = line.lower()
                    if "fake-hwclock" not in low and "fake_hwclock" not in low:
                        continue
                    # WORD boundaries, not substrings: the call is written
                    # _run(["sudo", "apt", "remove", ...]) so "apt remove" never
                    # appears contiguously. \bmask\b also correctly declines to
                    # match "unmask", which is the re-arming advice and the
                    # OPPOSITE of the defect.
                    if re.search(r"\b(remove|purge|disable|mask)\b|update-rc\.d", low):
                        bad.append(f"{os.path.relpath(path)}:{n}: {line.strip()}")
        self.assertEqual(bad, [], "RTC setup must never disarm fake-hwclock:\n"
                                  + "\n".join(bad))

    def test_hwclock_is_ensured_before_config_is_written(self):
        # You cannot verify an RTC with a tool you have not installed yet.
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "common", "rtc.py"), encoding="utf-8").read()
        body = src[src.index("def run("):]
        self.assertLess(body.index("ensure_hwclock()"), body.index("write_config("),
                        "ensure_hwclock() must run before write_config()")


class CapabilityProbes(unittest.TestCase):
    def test_a_missing_rtc_is_not_working(self):
        with mock.patch.object(rtc.os.path, "exists", lambda p: False):
            ok, detail = rtc.rtc_is_working()
        self.assertFalse(ok)
        self.assertIn("not present", detail)

    def test_an_rtc_that_lost_power_is_not_working(self):
        # A flat or absent battery reads 1970 — and reading fine while POWERED
        # is exactly the illusion that made this bug survive.
        with mock.patch.object(rtc.os.path, "exists", lambda p: True), \
             mock.patch("builtins.open", mock.mock_open(read_data="15")):
            ok, detail = rtc.rtc_is_working()
        self.assertFalse(ok)
        self.assertIn("lost power", detail)

    def test_a_sane_rtc_is_working(self):
        with mock.patch.object(rtc.os.path, "exists", lambda p: True), \
             mock.patch("builtins.open", mock.mock_open(read_data="1786504234")):
            ok, _ = rtc.rtc_is_working()
        self.assertTrue(ok)

    def test_installed_but_masked_is_reported_as_not_active(self):
        # The pi4oasis shape: dpkg says `ii`, the unit is symlinked to /dev/null
        # so systemctl answers "not-found". Looking at either half alone lies.
        class R:
            def __init__(self, out): self.stdout = out; self.returncode = 0
        def fake_run(cmd, **kw):
            return R("install ok installed") if cmd[0] == "dpkg-query" else R("not-found\n")
        with mock.patch.object(rtc, "_run", fake_run):
            installed, enabled = rtc.fake_hwclock_state()
        self.assertTrue(installed)
        self.assertFalse(enabled)


class Pi5Board(unittest.TestCase):
    """The Pi 5's RTC is in the SoC: no i2c chip, no overlay, nothing on
    i2cdetect. It owns a config.txt line ONLY when trickle charging is on."""

    def test_charging_off_owns_no_config_line(self):
        owned, prereq_add, present = rtc.plan_lines("", "pi5")
        self.assertEqual(owned, [])
        self.assertEqual(prereq_add, [])

    def test_charging_off_leaves_no_empty_block_behind(self):
        text, owned, _, _ = rtc.render_config("", "pi5")
        self.assertEqual(owned, [])
        self.assertNotIn("OASIS RTC", text)

    def test_charging_on_owns_exactly_the_vchg_dtparam(self):
        line = f"dtparam=rtc_bbat_vchg={rtc.PI5_CHARGE_UV}"
        owned, _, _ = rtc.plan_lines("", "pi5", line)
        self.assertEqual(owned, [line])

    def test_the_owned_line_sits_inside_the_block_so_uninstall_reaches_it(self):
        line = f"dtparam=rtc_bbat_vchg={rtc.PI5_CHARGE_UV}"
        text, _, _, _ = rtc.render_config("", "pi5", line)
        begin, end = rtc.block_markers("pi5")
        self.assertIn(begin, text)
        self.assertIn(end, text)
        self.assertLess(text.index(begin), text.index(line))
        self.assertLess(text.index(line), text.index(end))

    def test_rendering_is_idempotent_with_charging_on(self):
        line = f"dtparam=rtc_bbat_vchg={rtc.PI5_CHARGE_UV}"
        once, _, _, _ = rtc.render_config("", "pi5", line)
        twice, _, _, _ = rtc.render_config(once, "pi5", line)
        self.assertEqual(once, twice)

    def test_the_default_charge_voltage_is_inside_the_kernels_range(self):
        # /sys/class/rtc/rtc0/charging_voltage_{min,max} read 1300000 / 4400000.
        self.assertGreaterEqual(rtc.PI5_CHARGE_UV, 1300000)
        self.assertLessEqual(rtc.PI5_CHARGE_UV, 4400000)

    def test_a_missing_cell_reads_zero_not_none(self):
        # pi5draws: battery_voltage 0. Zero is "no cell", None is "cannot ask" —
        # collapsing them would make a Pi 5 with no battery look like a Pi 4.
        with mock.patch("builtins.open", mock.mock_open(read_data="0")):
            self.assertEqual(rtc.pi5_battery_millivolts(), 0)

    def test_not_a_pi5_reports_none_rather_than_zero(self):
        with mock.patch("builtins.open", side_effect=OSError):
            self.assertIsNone(rtc.pi5_battery_millivolts())


class AllThreeSetupSurfacesAgree(unittest.TestCase):
    """A feature must appear in the CLI registry AND the web checkbox list.

    Miss the HTML and the feature is uninstallable from the browser with no
    error at all — the failure has no symptom, which is why it needs a test."""

    def test_every_rtc_feature_is_on_both_surfaces(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "setup-oasis.py"), encoding="utf-8") as fh:
            cli = set(re.findall(r'Feature\("(rtc[\w-]*)"', fh.read()))
        with open(os.path.join(root, "server", "system", "setup.html"), encoding="utf-8") as fh:
            web = set(re.findall(r'data-feature="(rtc[\w-]*)"', fh.read()))
        self.assertEqual(cli, web, f"CLI-only: {cli - web}   web-only: {web - cli}")

    def test_every_rtc_feature_has_a_script_that_exists(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "setup-oasis.py"), encoding="utf-8") as fh:
            pairs = re.findall(r'Feature\("(rtc[\w-]*)",\s*"[^"]*",\s*"([^"]+)"', fh.read())
        self.assertTrue(pairs, "no RTC features found — the regex has rotted")
        for key, script in pairs:
            self.assertTrue(os.path.exists(os.path.join(root, script)),
                            f"{key} points at a missing script: {script}")

    def test_no_rtc_feature_is_filed_under_radio_interfaces(self):
        # Where they used to be. A clock is not a radio interface.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "server", "system", "setup.html"), encoding="utf-8") as fh:
            html = fh.read()
        radio = html[html.index("<h4>Radio Interfaces</h4>"):]
        radio = radio[:radio.index("</div>")]
        self.assertNotIn('data-feature="rtc', radio)
