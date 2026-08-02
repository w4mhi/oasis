#!/usr/bin/env python3
"""Unit tests for the RGB Cooling HAT daemon (features/rgb-cooling-hat).

Focus: a missing or wedged status OLED must degrade gracefully — the fan + RGB
control (the thermally important half) has to keep running instead of the whole
daemon crash-looping. This reproduces the operator's case of physically removing
the OLED from the HAT."""
import importlib.util
import os
import unittest

_FEAT = os.path.join(os.path.dirname(__file__), "..", "features", "rgb-cooling-hat")


def _load(name, filename):
    """Load a hyphenated feature script by path (not a valid module name)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_FEAT, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load("rgb_cooling_hat", "rgb-cooling-hat.py")


class DeadBus:
    """An I2C bus with no OLED on it: every 0x3c access errors the way the kernel
    does when the address is unpopulated (OSError EIO) — the exact failure the
    operator hit after pulling the panel."""
    def write_byte_data(self, *_):
        raise OSError(5, "Input/output error")

    def write_i2c_block_data(self, *_):
        raise OSError(5, "Input/output error")


class LiveBus:
    """A responsive bus that records every write, standing in for a fitted panel."""
    def __init__(self):
        self.writes = []

    def write_byte_data(self, addr, reg, val):
        self.writes.append((addr, reg, val))

    def write_i2c_block_data(self, addr, reg, data):
        self.writes.append((addr, reg, list(data)))


class OledOptionalTests(unittest.TestCase):
    def test_missing_oled_returns_none_not_raise(self):
        # The bug: SSD1306 init wrote its sequence to 0x3c and the OSError
        # propagated out of run_daemon, killing the fan daemon on every restart.
        self.assertIsNone(R.init_oled(DeadBus()))

    def test_present_oled_returns_driver(self):
        bus = LiveBus()
        oled = R.init_oled(bus)
        self.assertIsNotNone(oled)
        # It actually ran the init sequence, and only ever against the OLED addr.
        self.assertTrue(bus.writes)
        self.assertTrue(all(addr == R.OLED_ADDR for addr, _, _ in bus.writes))


class FanDecisionTests(unittest.TestCase):
    """Fan hysteresis must keep deciding correctly with the OLED gone."""
    def test_turns_on_at_threshold(self):
        self.assertTrue(R.fan_decision(R.FAN_ON, None))

    def test_off_when_cold(self):
        self.assertFalse(R.fan_decision(R.FAN_OFF, None))

    def test_holds_state_in_deadband(self):
        mid = (R.FAN_ON + R.FAN_OFF) / 2
        self.assertTrue(R.fan_decision(mid, True))
        self.assertFalse(R.fan_decision(mid, False))

    def test_first_pass_deadband_defaults_off(self):
        mid = (R.FAN_ON + R.FAN_OFF) / 2
        self.assertFalse(R.fan_decision(mid, None))


if __name__ == "__main__":
    unittest.main()
