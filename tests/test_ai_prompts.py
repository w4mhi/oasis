import os
import tempfile
import unittest
from unittest import mock

from ai.server.tools import prompts


class TestLoadPrompts(unittest.TestCase):
    def test_parses_title_and_body(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "net-briefing.md"), "w", encoding="utf-8") as fh:
            fh.write("# Net status briefing\nGive me a briefing.\nSecond line.\n")
        loaded = prompts.load_prompts(d)
        self.assertEqual(len(loaded), 1)
        name, title, body = loaded[0]
        self.assertEqual(name, "net-briefing")
        self.assertEqual(title, "Net status briefing")
        self.assertIn("Give me a briefing.", body)
        self.assertIn("Second line.", body)
        self.assertNotIn("#", body)

    def test_real_prompts_folder_has_the_four(self):
        here = os.path.dirname(os.path.abspath(prompts.__file__))
        d = os.path.join(here, "..", "prompts")
        names = {n for n, _, _ in prompts.load_prompts(d)}
        self.assertEqual(names,
                         {"net-briefing", "emergency-scan", "whos-on-the-map", "next-passes"})


class TestRegister(unittest.TestCase):
    def test_register_adds_a_prompt_per_file(self):
        added = []
        fake = mock.Mock()
        fake.add_prompt = lambda p: added.append(p)
        prompts.register(fake)
        self.assertEqual(len(added), 4)


try:
    import mcp  # noqa: F401
    _HAVE_MCP = True
except ImportError:
    _HAVE_MCP = False


@unittest.skipUnless(_HAVE_MCP, "mcp SDK not installed")
class TestRegisterOnRealServer(unittest.TestCase):
    def test_list_and_get_prompt_roundtrip(self):
        import asyncio
        from ai import config
        from ai.server.mcp_server import build_server
        server = build_server(config.load("/nonexistent"))
        names = {p.name for p in asyncio.run(server.list_prompts())}
        self.assertIn("net-briefing", names)
        got = asyncio.run(server.get_prompt("net-briefing", {}))
        text = " ".join(m.content.text for m in got.messages
                        if getattr(m.content, "type", None) == "text")
        self.assertIn("briefing", text.lower())
