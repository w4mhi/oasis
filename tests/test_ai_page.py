import unittest

from ai.orchestrator import routes as ai_routes


class TestAssistantPage(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(ai_routes.bp)
        self.client = app.test_client()

    def test_page_served(self):
        r = self.client.get("/assistant")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"OASIS Assistant", r.data)

    def test_js_and_css_served(self):
        self.assertEqual(self.client.get("/assistant/assistant.js").status_code, 200)
        self.assertEqual(self.client.get("/assistant/assistant.css").status_code, 200)

    def test_unknown_asset_404(self):
        self.assertEqual(self.client.get("/assistant/evil.txt").status_code, 404)
