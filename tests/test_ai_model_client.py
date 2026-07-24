import unittest
from unittest import mock

from ai import config
from ai.orchestrator import model_client


def _resp(payload):
    r = mock.Mock(status_code=200)
    r.raise_for_status = mock.Mock()
    r.json = mock.Mock(return_value=payload)
    return r


class TestModelClient(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load("/nonexistent")
        self.model = model_client.OpenAIChatModel(self.cfg)

    def test_parses_plain_text_answer(self):
        payload = {"choices": [{"message": {"role": "assistant",
                                            "content": "3 stations are active."}}]}
        with mock.patch("ai.orchestrator.model_client.httpx.post",
                        return_value=_resp(payload)):
            msg = self.model.complete([{"role": "user", "content": "how many?"}], [])
        self.assertEqual(msg.content, "3 stations are active.")
        self.assertEqual(msg.tool_calls, [])

    def test_parses_tool_call_with_json_arguments(self):
        payload = {"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "fcc_lookup",
                                         "arguments": '{"callsign": "W4MHI"}'}}]}}]}
        with mock.patch("ai.orchestrator.model_client.httpx.post",
                        return_value=_resp(payload)):
            msg = self.model.complete([{"role": "user", "content": "lookup W4MHI"}], [])
        self.assertEqual(len(msg.tool_calls), 1)
        tc = msg.tool_calls[0]
        self.assertEqual(tc.name, "fcc_lookup")
        self.assertEqual(tc.arguments, {"callsign": "W4MHI"})
        self.assertEqual(tc.id, "call_1")
