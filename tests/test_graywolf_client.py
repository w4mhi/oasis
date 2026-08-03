import json, os, sys, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from services.aprs.common.graywolf_client import GraywolfClient, GraywolfError


class _Handler(BaseHTTPRequestHandler):
    # class-level scratch shared with the test
    state = {"logged_in": False, "beacons": {}, "next_id": 1, "require_auth": True}

    def log_message(self, *a):  # silence
        pass

    def _send(self, code, obj, cookie=False):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", "gwsession=abc; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return (not self.state["require_auth"]) or ("gwsession=abc" in self.headers.get("Cookie", ""))

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if self.path == "/beacons":
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, {"beacons": list(self.state["beacons"].values())})
        return self._send(404, {"error": "nope"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        if self.path == "/auth/login":
            self.state["logged_in"] = True
            return self._send(200, {"ok": True}, cookie=True)
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        if self.path == "/beacons":
            bid = str(self.state["next_id"]); self.state["next_id"] += 1
            rec = json.loads(raw); rec["id"] = bid
            self.state["beacons"][bid] = rec
            return self._send(200, {"id": bid})
        if self.path.endswith("/send"):
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "nope"})

    def do_PUT(self):
        n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n) if n else None
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        return self._send(200, {"ok": True})

    def do_DELETE(self):
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        bid = self.path.rsplit("/", 1)[-1]
        self.state["beacons"].pop(bid, None)
        return self._send(200, {"ok": True})


class GraywolfClientTest(unittest.TestCase):
    def setUp(self):
        _Handler.state = {"logged_in": False, "beacons": {}, "next_id": 1, "require_auth": True}
        self.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        host, port = self.srv.server_address
        self.client = GraywolfClient(f"http://{host}:{port}", "u", "p", timeout=3)

    def tearDown(self):
        self.srv.shutdown()

    def test_health_true(self):
        self.assertTrue(self.client.health())

    def test_create_returns_id_and_logs_in(self):
        bid = self.client.create_beacon({"type": "object", "object_name": "W1234abcd"})
        self.assertEqual(bid, "1")
        self.assertTrue(_Handler.state["logged_in"])

    def test_list_delete_roundtrip(self):
        bid = self.client.create_beacon({"type": "object"})
        self.assertEqual(len(self.client.list_beacons()), 1)
        self.client.delete_beacon(bid)
        self.assertEqual(len(self.client.list_beacons()), 0)

    def test_send_now_ok(self):
        bid = self.client.create_beacon({"type": "custom", "custom_info": ";X_..."})
        self.client.send_now(bid)  # no raise

    def test_error_on_unreachable(self):
        bad = GraywolfClient("http://127.0.0.1:1", "u", "p", timeout=1)
        with self.assertRaises(GraywolfError):
            bad.create_beacon({"type": "object"})


if __name__ == "__main__":
    unittest.main()
