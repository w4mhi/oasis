import sys
import unittest

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False


@unittest.skipUnless(HAVE_MCP, "mcp SDK not installed")
class TestAssistantRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ai.orchestrator.mcp_runtime import AssistantRuntime
        cls.rt = AssistantRuntime(sys.executable, ["-m", "ai.server.mcp_server"])

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()

    def test_lists_expected_tools_as_openai_schema(self):
        tools = self.rt.list_tools()
        names = {t["function"]["name"] for t in tools}
        self.assertIn("system_health", names)
        self.assertIn("aprs_stations", names)
        # OpenAI schema shape
        self.assertEqual(tools[0]["type"], "function")
        self.assertIn("parameters", tools[0]["function"])
