"""
CSRF guard — decorator behavior plus a repo-wide coverage net.

The coverage test is the point of this file. Every mutating endpoint added after
the original CSRF pass shipped without the `X-OASIS-Request` check, including
`/api/aprs/warnings` (keys the transmitter) and `/api/satellites/listen` (seizes
an RTL-SDR). The check is easy to forget and forgetting it is silent, so this
sweeps the source and fails when a mutating /api route is guarded by neither the
decorator nor an inline header check.
"""

import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from common.web_guard import (  # noqa: E402
    HEADER,
    has_oasis_request_header,
    require_oasis_request,
    require_oasis_request_for,
)


def _app_with(view_factory):
    from flask import Flask

    app = Flask(__name__)
    view_factory(app)
    return app.test_client()


class DecoratorTest(unittest.TestCase):
    def setUp(self):
        def routes(app):
            @app.route("/guarded", methods=["POST"])
            @require_oasis_request
            def guarded():
                from flask import jsonify

                return jsonify({"ok": True, "ran": True})

            @app.route("/mixed/<thing>", methods=["GET", "DELETE"])
            @require_oasis_request_for("DELETE")
            def mixed(thing):
                from flask import jsonify, request

                return jsonify({"ok": True, "method": request.method})

        self.client = _app_with(routes)

    def test_missing_header_is_403_and_view_never_runs(self):
        r = self.client.post("/guarded")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json(), {"ok": False, "error": "forbidden"})
        self.assertNotIn("ran", r.get_json())

    def test_wrong_header_value_is_403(self):
        r = self.client.post("/guarded", headers={HEADER: "0"})
        self.assertEqual(r.status_code, 403)

    def test_header_present_runs_the_view(self):
        r = self.client.post("/guarded", headers={HEADER: "1"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ran"])

    def test_decorator_preserves_the_view_name(self):
        # Flask derives the endpoint name from __name__; losing it would
        # collide every guarded view under a single "wrapper" endpoint.
        self.assertEqual(require_oasis_request(lambda: None).__name__, "<lambda>")

    # ── method-scoped variant ────────────────────────────────────────────────
    def test_unguarded_method_still_open(self):
        r = self.client.get("/mixed/abc")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["method"], "GET")

    def test_guarded_method_needs_the_header(self):
        self.assertEqual(self.client.delete("/mixed/abc").status_code, 403)
        r = self.client.delete("/mixed/abc", headers={HEADER: "1"})
        self.assertEqual(r.status_code, 200)


class HeaderProbeTest(unittest.TestCase):
    def test_probe_accepts_an_injected_request(self):
        class Stub:
            headers = {HEADER: "1"}

        self.assertTrue(has_oasis_request_header(Stub()))

        class Empty:
            headers = {}

        self.assertFalse(has_oasis_request_header(Empty()))


# ── Repo-wide coverage net ───────────────────────────────────────────────────

_ROUTE_RE = re.compile(r'@(?:bp|app)\.route\(\s*["\']([^"\']+)["\']')
_MUTATING = ("POST", "PUT", "PATCH", "DELETE")

# Route rules whose mutating methods are deliberately open. Keep this empty
# unless there is a written reason — an entry here is a decision, not a TODO.
_ALLOWED_UNGUARDED = frozenset()


def _source_files():
    for sub in ("server", "services", "maps"):
        base = os.path.join(_ROOT, sub)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _mutating_routes():
    """Yield (relpath, rule, guarded) for every mutating /api route in the tree."""
    for path in _source_files():
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        for i, line in enumerate(lines):
            m = _ROUTE_RE.search(line)
            if not m or not m.group(1).startswith("/api/"):
                continue
            decorator = "\n".join(lines[i:i + 3])
            if not any(v in decorator for v in _MUTATING):
                continue
            # Decorators sit between the route and the def; the inline check sits
            # in the first statements of the body. One window covers both.
            body = "\n".join(lines[i:i + 45])
            guarded = ("require_oasis_request" in body) or (HEADER in body)
            yield os.path.relpath(path, _ROOT), m.group(1), guarded


class MutatingRouteCoverageTest(unittest.TestCase):
    def test_every_mutating_api_route_is_csrf_guarded(self):
        found = list(_mutating_routes())
        self.assertGreater(len(found), 25, "route scan found too little — regex drifted?")
        unguarded = [
            f"{rule}  ({rel})"
            for rel, rule, guarded in found
            if not guarded and rule not in _ALLOWED_UNGUARDED
        ]
        self.assertEqual(
            unguarded, [],
            "mutating /api routes with no X-OASIS-Request guard:\n  "
            + "\n  ".join(unguarded)
            + "\n\nAdd @require_oasis_request (common/web_guard.py) below the "
              "@bp.route, and send the header from the client fetch.",
        )

    def test_force_parsed_bodies_are_guarded(self):
        """`get_json(force=True)` parses a text/plain body, so such a route is
        reachable by a simple cross-origin POST with no preflight at all — it has
        none of the accidental protection a JSON content-type gives."""
        offenders = []
        for path in _source_files():
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().split("\n")
            for i, line in enumerate(lines):
                if "force=True" not in line:
                    continue
                # Walk back to the owning route decorator.
                start = max(0, i - 45)
                window = "\n".join(lines[start:i + 1])
                if "@bp.route" not in window and "@app.route" not in window:
                    continue
                if "require_oasis_request" not in window and HEADER not in window:
                    offenders.append(f"{os.path.relpath(path, _ROOT)}:{i + 1}")
        self.assertEqual(offenders, [],
                         "force-parsed request bodies with no CSRF guard: " + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
