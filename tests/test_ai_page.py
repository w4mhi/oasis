import os
import sys
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

    def test_assistant_js_has_confirm_handler(self):
        r = self.client.get("/assistant/assistant.js")
        self.assertIn(b"confirm_required", r.data)
        self.assertIn(b"/api/assistant/confirm", r.data)


class TestDashboardHasAiCard(unittest.TestCase):
    """The dashboard (index.html, served as a static file off SUITE_ROOT) must
    expose a service card for the oasis-ai daemon — via the Flask test client,
    not a live server. Note: index.html is served at /index.html (Flask's
    static_url_path is '' for the whole suite root); '/' itself is a tiny JS
    layout-redirect stub, not the dashboard markup."""

    def setUp(self):
        _here = os.path.dirname(os.path.abspath(__file__))
        _server_dir = os.path.join(os.path.dirname(_here), "server")
        sys.path.insert(0, _server_dir)
        sys.path.insert(0, os.path.dirname(_here))
        import app as oasis_app
        oasis_app.app.config["TESTING"] = True
        self.client = oasis_app.app.test_client()

    def test_dashboard_has_ai_card(self):
        r = self.client.get("/index.html")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'id="card-ai"', r.data)
