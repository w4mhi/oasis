"""
A Flask test client that sends the `X-OASIS-Request` CSRF header, like the real UI.

Route tests exercise business logic, not the guard, so they should arrive at the
view the way a browser on the dashboard does. Without this every mutating-route
test would need `headers={...}` bolted onto each call, and the noise would bury
what each test is actually asserting.

The guard itself is covered separately and deliberately:
  - tests/test_csrf_guard.py — decorator behavior + a repo-wide coverage net that
    fails when any mutating /api route ships unguarded.
  - per-blueprint "no header → 403" tests, which build a PLAIN test client so the
    default header can't mask a missing guard.
"""

from flask.testing import FlaskClient


class OasisTestClient(FlaskClient):
    """Adds `X-OASIS-Request: 1` unless the caller set it explicitly."""

    def open(self, *args, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("X-OASIS-Request", "1")
        kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def _client(app, cls):
    """Build a client of class `cls` WITHOUT permanently reassigning
    `app.test_client_class`.

    Several test modules share one imported `app` object (server/app.py is a
    module-level singleton). Leaving the class reassigned leaked into unrelated
    modules and silently added the header to the very tests that exist to prove a
    route rejects requests without it — turning a real guard test green no matter
    what the route does. Set, build, restore.
    """
    previous = app.test_client_class
    app.test_client_class = cls
    try:
        return app.test_client()
    finally:
        app.test_client_class = previous


def csrf_client(app):
    """Test client that carries the CSRF header (use for logic tests)."""
    return _client(app, OasisTestClient)


def bare_client(app):
    """Test client with NO CSRF header (use to prove a route is guarded)."""
    return _client(app, FlaskClient)
