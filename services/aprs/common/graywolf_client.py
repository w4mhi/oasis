"""Thin HTTP client for the GrayWolf Management API (Swagger 2.0, port 8080).

Cookie-authenticated (POST /auth/login sets a session cookie). All calls are
best-effort with a short timeout and raise GraywolfError on any failure so the
caller can swallow it — broadcasting must never break local warning CRUD.
"""
import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar


class GraywolfError(Exception):
    pass


class GraywolfClient:
    def __init__(self, base_url, username, password, timeout=4.0):
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar))
        self._authed = False

    # ── low-level ────────────────────────────────────────────────────────────
    def _raw(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with self._opener.open(req, timeout=self.timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})

    def _login(self):
        try:
            self._raw("POST", "/auth/login",
                      {"username": self.username, "password": self.password})
            self._authed = True
        except Exception as e:  # noqa: BLE001
            raise GraywolfError(f"login failed: {e}")

    def _call(self, method, path, body=None, auth=True):
        """Call with lazy login + one retry on 401."""
        if auth and not self._authed:
            self._login()
        try:
            return self._raw(method, path, body)
        except urllib.error.HTTPError as e:
            if e.code == 401 and auth:
                self._authed = False
                self._login()
                try:
                    return self._raw(method, path, body)
                except Exception as e2:  # noqa: BLE001
                    raise GraywolfError(f"{method} {path}: {e2}")
            raise GraywolfError(f"{method} {path}: HTTP {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GraywolfError(f"{method} {path}: {e}")

    # ── public API ───────────────────────────────────────────────────────────
    def health(self):
        try:
            code, _ = self._call("GET", "/health", auth=False)
            return code == 200
        except GraywolfError:
            return False

    def create_beacon(self, payload):
        _, data = self._call("POST", "/beacons", payload)
        bid = data.get("id") or data.get("beacon", {}).get("id")
        if not bid:
            raise GraywolfError(f"create_beacon: no id in response {data!r}")
        return str(bid)

    def update_beacon(self, beacon_id, payload):
        self._call("PUT", f"/beacons/{beacon_id}", payload)

    def delete_beacon(self, beacon_id):
        self._call("DELETE", f"/beacons/{beacon_id}")

    def send_now(self, beacon_id):
        self._call("POST", f"/beacons/{beacon_id}/send")

    def list_beacons(self):
        _, data = self._call("GET", "/beacons")
        if isinstance(data, list):
            return data
        return data.get("beacons", [])
