#!/usr/bin/env python3
"""
test_winlink_proxy.py — self-tests for the OASIS Winlink (Pat) proxy routes.

These never touch a live Pat instance. They stub `urllib.request.urlopen`
(the call app.py uses to reach Pat on port 8082) so we can assert the proxy's
behaviour: same-origin pass-through of Pat's JSON, box-name validation, query
forwarding, and a clean 503 when Pat is unreachable.

Run directly:  .venv/bin/python tests/test_winlink_proxy.py
(plain unittest — no pytest, to stay within the offline wheel set: flask only.)
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

# ── Make `import app` (and its sibling `import lookup`) resolve whether run
#    from the repo root or from tests/. ─────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_SERVER    = os.path.join(os.path.dirname(_HERE), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import app as oasis_app   # server/app.py


class _FakePatResponse:
    """Minimal stand-in for the object urllib.request.urlopen returns."""

    def __init__(self, body: bytes, status: int = 200, headers=None):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(*, body=b"", status=200, exc=None, capture=None, headers=None):
    """Return a mock.patch context replacing urlopen in app's namespace.

    If `capture` is a dict, the requested URL/method are recorded so a test can
    assert which Pat endpoint the proxy hit.
    """

    def fake(req, timeout=None):
        if capture is not None:
            capture["url"] = req.full_url if hasattr(req, "full_url") else req
            capture["method"] = getattr(req, "method", None)
        if exc is not None:
            raise exc
        return _FakePatResponse(body, status, headers)

    return mock.patch.object(urllib.request, "urlopen", fake)


class WinlinkProxyTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.client = oasis_app.app.test_client()

    # ── Mailbox listing ──────────────────────────────────────────────────────
    def test_mailbox_list_wraps_pats_array_and_forwards_correctly(self):
        """No longer a byte pass-through: OASIS owns the envelope (a bare array
        violates contract §1) while Pat's message objects pass through as-is.
        See tests/test_winlink_contract.py for the full shape."""
        payload = b'[{"MID":"ABC","Subject":"Net check-in"}]'
        cap = {}
        with _patch_urlopen(body=payload, status=200, capture=cap):
            resp = self.client.get("/api/winlink/mailbox/in")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIs(body["ok"], True)
        self.assertEqual(body["messages"], [{"MID": "ABC", "Subject": "Net check-in"}])
        self.assertIn("application/json", resp.content_type)
        # The part this test really guards: WHICH Pat URL we call.
        self.assertIn("127.0.0.1:8082", cap["url"])
        self.assertTrue(cap["url"].endswith("/api/mailbox/in"))

    def test_unknown_box_is_rejected_without_hitting_pat(self):
        called = {"hit": False}

        def fake(req, timeout=None):
            called["hit"] = True
            return _FakePatResponse(b"[]")

        with mock.patch.object(urllib.request, "urlopen", fake):
            resp = self.client.get("/api/winlink/mailbox/bogus")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(called["hit"])

    # ── Pat unreachable ──────────────────────────────────────────────────────
    def test_pat_down_returns_503(self):
        with _patch_urlopen(exc=urllib.error.URLError("Connection refused")):
            resp = self.client.get("/api/winlink/status")
        self.assertEqual(resp.status_code, 503)
        self.assertTrue(resp.is_json)
        self.assertFalse(resp.get_json()["ok"])

    # ── Connect (transport session kickoff) ──────────────────────────────────
    # Mutating (keys the transmitter on RF): POST-only, gated by the same
    # X-OASIS-Request CSRF header as /api/service. Pat's side stays GET.
    def test_connect_forwards_url_param(self):
        cap = {}
        with _patch_urlopen(body=b'{"ok":true}', status=200, capture=cap):
            resp = self.client.post("/api/winlink/connect?url=telnet%3A%2F%2Fcms",
                                    headers={"X-OASIS-Request": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("url=telnet", cap["url"])

    def test_connect_without_header_forbidden_before_pat(self):
        called = {"hit": False}

        def fake(req, timeout=None):
            called["hit"] = True
            return _FakePatResponse(b"")

        with mock.patch.object(urllib.request, "urlopen", fake):
            resp = self.client.post("/api/winlink/connect?url=telnet%3A%2F%2Fcms")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(called["hit"])

    def test_connect_get_is_rejected(self):
        # 404 not 405: with the suite root as static folder, an unmatched GET
        # falls through to static serving. Either way, GET can't start a session.
        resp = self.client.get("/api/winlink/connect?url=telnet%3A%2F%2Fcms",
                               headers={"X-OASIS-Request": "1"})
        self.assertIn(resp.status_code, (404, 405))

    # ── Compose / queue ───────────────────────────────────────────────────────
    def test_compose_posts_to_outbox(self):
        cap = {}
        # Compose queues OUTBOUND RADIO EMAIL and is now CSRF-guarded, so the
        # header is required (mail.html's api() has always sent it on non-GET).
        with _patch_urlopen(body=b"{}", status=200, capture=cap):
            resp = self.client.post(
                "/api/winlink/mailbox/out",
                data={"to": "W4ABC", "subject": "Test", "body": "hi"},
                headers={"X-OASIS-Request": "1"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.get_json()["queued"], True)
        self.assertTrue(cap["url"].endswith("/api/mailbox/out"))
        self.assertEqual(cap["method"], "POST")

    def test_compose_without_the_csrf_header_is_refused(self):
        """It was UNGUARDED until 2026-08-08: the coverage sweep's 45-line
        window was catching a neighbouring route's inline check."""
        resp = self.client.post("/api/winlink/mailbox/out",
                                data={"to": "W4ABC", "subject": "x", "body": "y"})
        self.assertEqual(resp.status_code, 403)

    # ── Attachments ───────────────────────────────────────────────────────────
    def test_attachment_passes_through_pats_content_type(self):
        cap = {}
        with _patch_urlopen(body=b"<RMS_Express_Form/>", status=200,
                            headers={"Content-Type": "text/xml"}, capture=cap):
            resp = self.client.get(
                "/api/winlink/mailbox/in/ABC123/RMS_Express_Form_ICS213.xml")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"<RMS_Express_Form/>")
        # Must NOT be forced to application/json like the JSON proxies.
        self.assertIn("text/xml", resp.content_type)
        self.assertTrue(cap["url"].endswith(
            "/api/mailbox/in/ABC123/RMS_Express_Form_ICS213.xml"))

    def test_attachment_download_sets_content_disposition(self):
        with _patch_urlopen(body=b"data", status=200,
                            headers={"Content-Type": "text/plain"}):
            resp = self.client.get(
                "/api/winlink/mailbox/in/ABC123/FormData.txt?download=1")
        self.assertEqual(resp.status_code, 200)
        cd = resp.headers.get("Content-Disposition", "")
        self.assertIn("attachment", cd)
        self.assertIn("FormData.txt", cd)

    # ── RMS gateway list ──────────────────────────────────────────────────────
    def test_rmslist_slims_and_strips_prediction(self):
        fake_list = json.dumps([{
            "callsign": "ZL4RMS", "gridsquare": "RE44QI",
            "distance": 4929.6, "azimuth": 270, "modes": "ARDOP 2000",
            "freq": {"hz": 7137500, "khz": 7137.5, "desc": "..."},
            "dial": {"hz": 7136000, "khz": 7136, "desc": "..."},
            "url": "ardop:///ZL4RMS?freq=7136",
            "prediction": {"link_quality": 22, "output_raw": "X" * 5000},
        }]).encode()
        with _patch_urlopen(body=fake_list, status=200):
            resp = self.client.get("/api/winlink/rmslist?mode=ardop")
        self.assertEqual(resp.status_code, 200)
        d = resp.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["count"], 1)
        g = d["gateways"][0]
        self.assertEqual(g["callsign"], "ZL4RMS")
        self.assertEqual(g["dial_khz"], 7136)
        self.assertEqual(g["link_quality"], 22)
        self.assertEqual(g["url"], "ardop:///ZL4RMS?freq=7136")
        # The heavy VOACAP text must be gone.
        self.assertNotIn("output_raw", resp.get_data(as_text=True))

    def test_rmslist_unknown_mode_rejected(self):
        called = {"hit": False}

        def fake(req, timeout=None):
            called["hit"] = True
            return _FakePatResponse(b"[]")

        with mock.patch.object(urllib.request, "urlopen", fake):
            resp = self.client.get("/api/winlink/rmslist?mode=bogus")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(called["hit"])

    def test_rmslist_pat_down_returns_503(self):
        with _patch_urlopen(exc=urllib.error.URLError("refused")):
            resp = self.client.get("/api/winlink/rmslist?mode=ardop")
        self.assertEqual(resp.status_code, 503)

    # ── Disconnect / abort ────────────────────────────────────────────────────
    # Mutating: POST-only + CSRF header, same as connect.
    def test_disconnect_proxies_pat(self):
        cap = {}
        with _patch_urlopen(body=b'{"ok":true}', status=200, capture=cap):
            resp = self.client.post("/api/winlink/disconnect",
                                    headers={"X-OASIS-Request": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(cap["url"].endswith("/api/disconnect"))

    def test_disconnect_without_header_is_forbidden(self):
        resp = self.client.post("/api/winlink/disconnect")
        self.assertEqual(resp.status_code, 403)

    def test_disconnect_get_is_rejected(self):
        # See test_connect_get_is_rejected for why 404 (static fall-through).
        resp = self.client.get("/api/winlink/disconnect",
                               headers={"X-OASIS-Request": "1"})
        self.assertIn(resp.status_code, (404, 405))

    def test_attachment_unknown_box_rejected(self):
        called = {"hit": False}

        def fake(req, timeout=None):
            called["hit"] = True
            return _FakePatResponse(b"")

        with mock.patch.object(urllib.request, "urlopen", fake):
            resp = self.client.get("/api/winlink/mailbox/bogus/ABC123/file.xml")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(called["hit"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
