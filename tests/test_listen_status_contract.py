"""Every LISTEN.<field> the satellites page reads must exist in /listen/status.

This exists because the two drifted in silence and cost a pass. listen.status()
grew `backend` and splatted **tracked_status() into its return; the route builds
its payload by enumerating keys by hand, and that enumeration was never updated.
Nothing raised. The page kept polling, `LISTEN.backend` kept evaluating to
undefined, and the TRACKED badge could never appear no matter what the capture
was doing — so the one signal distinguishing a Doppler-corrected recording from
an uncorrected one was invisible on the only screen that shows it.

capture_backend()'s docstring had already promised the opposite: falling back to
rtl_fm "must never be a SILENT one, which is why the chosen backend rides in
/listen/status". A docstring cannot enforce that. This can.

Deliberately parsed out of the SOURCE rather than asserted against a hand-kept
list: a hand-kept list is the same enumeration that failed, one file over.
"""
import ast
import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SAT = os.path.join(_ROOT, "services", "satellites")
_ROUTES = os.path.join(_SAT, "routes.py")
_PAGE = os.path.join(_SAT, "static", "satellites.html")

# Client-side additions to the LISTEN object. `_err` is set by the page itself
# on a failed start and preserved across the refresh in pollListen(); anything
# underscore-prefixed is ours by that same convention, not the server's.
_CLIENT_SIDE = {"_err"}


def _payload_keys():
    """The literal string keys of the dict jsonify()'d by api_listen_status."""
    with open(_ROUTES, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "api_listen_status"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Dict):
                keys = {k.value for k in sub.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if "ok" in keys:          # the response dict, not some local
                    return keys
    raise AssertionError("api_listen_status not found, or it no longer "
                         "jsonify()s a dict literal — update this test")


def _fields_read_by_page():
    """Every LISTEN.<field> the page dereferences."""
    with open(_PAGE, encoding="utf-8") as fh:
        src = fh.read()
    found = set(re.findall(r"\bLISTEN\.([A-Za-z_]\w*)", src))
    return {f for f in found if not f.startswith("_")} - _CLIENT_SIDE


class ListenStatusContractTest(unittest.TestCase):
    def test_the_page_reads_nothing_the_route_does_not_send(self):
        missing = sorted(_fields_read_by_page() - _payload_keys())
        self.assertEqual(missing, [], "satellites.html reads LISTEN fields that "
                         "/api/satellites/listen/status never sends: %s. They "
                         "evaluate to undefined, which is falsy — the feature "
                         "silently turns itself off." % missing)

    def test_the_backend_is_reported(self):
        """Named explicitly, not just covered by the sweep above. Whether a
        recording was Doppler-corrected is not recoverable after the fact from
        the WAV — a tracked capture and an uncorrected one are the same shape —
        so if this key stops being sent the recording becomes unattributable."""
        self.assertIn("backend", _payload_keys())

    def test_the_sweep_actually_finds_something(self):
        """A regex that matched nothing would make the first test vacuously
        pass, which is the failure mode of every drift guard."""
        fields = _fields_read_by_page()
        self.assertGreater(len(fields), 5, "LISTEN.<field> parse found almost "
                           "nothing — the page's access pattern changed and "
                           "this guard is no longer guarding anything")
        self.assertIn("recording", fields)


if __name__ == "__main__":
    unittest.main()
