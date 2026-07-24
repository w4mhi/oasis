import unittest

from server.routes import service_control as sc


class TestOasisAiControllable(unittest.TestCase):
    def test_oasis_ai_is_known_and_controllable(self):
        self.assertIn("oasis-ai", sc._OASIS_SERVICES)
        self.assertIn("oasis-ai", sc._CONTROLLABLE_SERVICES)
