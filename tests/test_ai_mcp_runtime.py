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


@unittest.skipUnless(HAVE_MCP, "mcp SDK not installed")
class TestAssistantRuntimePrompts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ai.orchestrator.mcp_runtime import AssistantRuntime
        cls.rt = AssistantRuntime(sys.executable, ["-m", "ai.server.mcp_server"])

    @classmethod
    def tearDownClass(cls):
        cls.rt.close()

    def test_lists_and_roundtrips_net_briefing_prompt(self):
        prompts = self.rt.list_prompts()
        names = {p["name"] for p in prompts}
        self.assertIn("net-briefing", names)
        briefing = next(p for p in prompts if p["name"] == "net-briefing")
        self.assertIn("name", briefing)
        self.assertIn("title", briefing)

        text = self.rt.get_prompt("net-briefing")
        # "scannable" appears only in the body of net-briefing.md, not in its
        # title/description - proves the body (not just metadata) round-trips.
        self.assertIn("scannable", text)
