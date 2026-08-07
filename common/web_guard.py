"""
common/web_guard.py
-------------------
The `X-OASIS-Request` CSRF guard, as a decorator.

OASIS serves an unauthenticated API on the LAN by design (docs/concept.md) — the
only thing standing between a hostile page the operator happens to visit and a
state-changing endpoint is that the browser won't let a cross-origin script set a
custom header without a CORS preflight, which this server never grants. That makes
the header check load-bearing, not decorative: it is the whole cross-origin story
for `/api/service`, `/api/wifi/*`, `/api/setup/reboot`, and friends.

Why a decorator rather than the two-line check copy-pasted into each view: the
check is easy to *forget*, and forgetting it is silent. Every mutating route added
after the original CSRF pass shipped without it, including ones that key the
transmitter. A decorator makes the guard visible in the route's signature, so a
missing one is obvious on review.

Note on `get_json(force=True)`: a route that force-parses is reachable by a *simple*
cross-origin POST (`Content-Type: text/plain` needs no preflight), so it has no
accidental protection from the JSON content-type at all. Those routes need this
guard the most.

Usage — decorate *below* the route so the guard runs on every method the rule
serves, and so Flask registers the wrapped view:

    @bp.route("/api/thing", methods=["POST"])
    @require_oasis_request
    def api_thing():
        ...
"""

import functools

from flask import jsonify, request

HEADER = "X-OASIS-Request"


def has_oasis_request_header(req=None):
    """True when the request carries the OASIS custom header.

    Pure enough to unit-test against a stub with a `.headers` mapping.
    """
    req = request if req is None else req
    return req.headers.get(HEADER) == "1"


def require_oasis_request(view):
    """Reject requests without the `X-OASIS-Request: 1` header with a 403.

    The 403 body matches the hand-written checks this replaces
    (`{"ok": false, "error": "forbidden"}`) so clients see no behavior change.
    """

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not has_oasis_request_header():
            return jsonify({"ok": False, "error": "forbidden"}), 403
        return view(*args, **kwargs)

    return wrapper


def require_oasis_request_for(*methods):
    """Guard only the named HTTP methods on a rule that serves several.

    For views registered as e.g. `methods=["GET", "DELETE"]`, where the read is
    deliberately open but the mutation is not. Guarding the whole view there
    would break plain reads.

        @bp.route("/api/thing/<id>", methods=["GET", "DELETE"])
        @require_oasis_request_for("DELETE")
        def api_thing(id): ...
    """
    guarded = {m.upper() for m in methods}

    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if request.method.upper() in guarded and not has_oasis_request_header():
                return jsonify({"ok": False, "error": "forbidden"}), 403
            return view(*args, **kwargs)

        return wrapper

    return decorator
