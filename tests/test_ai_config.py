import json
import os
import tempfile
import unittest

from ai import config


class TestConfig(unittest.TestCase):
    def test_defaults_when_file_missing(self):
        cfg = config.load(os.path.join(tempfile.gettempdir(), "no-such-oasis-ai.json"))
        self.assertEqual(cfg.oasis_api_base, "http://127.0.0.1:8083")
        self.assertEqual(cfg.max_tool_iterations, 5)
        self.assertIn("only", cfg.system_prompt.lower())

    def test_file_overrides_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"model": {"name": "custom-7b", "max_tokens": 2048},
                       "max_tool_iterations": 8}, fh)
            path = fh.name
        try:
            cfg = config.load(path)
            self.assertEqual(cfg.model_name, "custom-7b")
            self.assertEqual(cfg.max_tokens, 2048)
            self.assertEqual(cfg.max_tool_iterations, 8)
            # untouched keys still come from defaults
            self.assertEqual(cfg.oasis_api_base, "http://127.0.0.1:8083")
        finally:
            os.unlink(path)
