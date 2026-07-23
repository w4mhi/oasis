import unittest

from ai import config
from ai.orchestrator.gate import ActionGate


class _Reg:
    def __init__(self): self.added = []
    def add(self, name, args): self.added.append((name, args)); return "pid"


class TestActionGate(unittest.TestCase):
    def _cfg(self, **over):
        base = config.load("/nonexistent")
        # config is frozen; rebuild via a tiny shim dict
        from dataclasses import replace
        return replace(base, **over)

    def test_read_tool_executes(self):
        g = ActionGate(self._cfg(), _Reg())
        self.assertEqual(g.classify("aprs_stations", {}), "execute")

    def test_action_not_auto_confirms(self):
        g = ActionGate(self._cfg(auto_actions=[]), _Reg())
        self.assertEqual(g.classify("service_control", {}), "confirm")

    def test_action_in_auto_executes(self):
        g = ActionGate(self._cfg(auto_actions=["satellite_monitor"]), _Reg())
        self.assertEqual(g.classify("satellite_monitor", {}), "execute")

    def test_readonly_blocks_actions(self):
        g = ActionGate(self._cfg(actions_enabled=False), _Reg())
        self.assertEqual(g.classify("service_control", {}), "blocked")
        # read tools still fine in read-only mode
        self.assertEqual(g.classify("aprs_stations", {}), "execute")

    def test_pending_delegates_to_registry(self):
        reg = _Reg()
        ActionGate(self._cfg(), reg).pending("service_control", {"unit": "x"})
        self.assertEqual(reg.added, [("service_control", {"unit": "x"})])
