import unittest

from ai.orchestrator.pending import PendingRegistry


class TestPendingRegistry(unittest.TestCase):
    def test_add_then_take_returns_action_once(self):
        r = PendingRegistry()
        pid = r.add("service_control", {"unit": "graywolf"})
        self.assertEqual(r.take(pid), ("service_control", {"unit": "graywolf"}))
        self.assertIsNone(r.take(pid))          # single-use

    def test_unknown_id(self):
        self.assertIsNone(PendingRegistry().take("nope"))

    def test_expired(self):
        r = PendingRegistry(ttl=-1)             # already expired
        pid = r.add("x", {})
        self.assertIsNone(r.take(pid))

    def test_ids_unique(self):
        r = PendingRegistry()
        self.assertNotEqual(r.add("a", {}), r.add("a", {}))
