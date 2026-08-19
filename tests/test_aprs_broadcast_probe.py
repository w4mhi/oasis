"""The broadcast-credential probe: does a stored GrayWolf login actually work?

Presence was already reported (health/file's username_set + password_set). What
these cover is the gap that made a WRONG password indistinguishable from a right
one -- graywolf_client swallows every failure by design, and _get_broadcaster()
builds a client without ever logging in.
"""
import os, sys, unittest
from http.cookiejar import CookieJar

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from services.aprs.common.graywolf_client import GraywolfClient, GraywolfError
from services.aprs.common.warning_broadcast import broadcast_probe_state


class BroadcastProbeStateTest(unittest.TestCase):
    """Pure state logic -- no I/O, so it pins the message an operator reads."""

    def test_no_credentials_is_unconfigured(self):
        status, detail = broadcast_probe_state(False, None, None)
        self.assertEqual(status, "unconfigured")
        self.assertIn("OFF", detail)

    def test_credentials_but_graywolf_down_is_unreachable(self):
        status, detail = broadcast_probe_state(True, False, None, "http://x:8080")
        self.assertEqual(status, "unreachable")
        self.assertIn("http://x:8080", detail)

    def test_reachable_but_login_refused_is_rejected(self):
        # The state this whole endpoint exists for.
        status, detail = broadcast_probe_state(True, True, False)
        self.assertEqual(status, "rejected")
        self.assertIn("REJECTED", detail)
        self.assertIn("OFF", detail)

    def test_all_good_is_ok(self):
        status, detail = broadcast_probe_state(True, True, True)
        self.assertEqual(status, "ok")
        self.assertIn("available", detail)

    def test_unconfigured_outranks_every_downstream_symptom(self):
        # Naming the fixable thing beats naming what it caused.
        self.assertEqual(broadcast_probe_state(False, False, False)[0], "unconfigured")

    def test_unreachable_outranks_rejected(self):
        self.assertEqual(broadcast_probe_state(True, False, False)[0], "unreachable")

    def test_every_status_has_a_distinct_message(self):
        msgs = {broadcast_probe_state(*a)[1] for a in
                [(False, None, None), (True, False, None), (True, True, False), (True, True, True)]}
        self.assertEqual(len(msgs), 4)


class _StubClient(GraywolfClient):
    """GraywolfClient with the network replaced, so check_auth's own logic is
    what is under test rather than urllib's."""
    def __init__(self, *a, fail=False, **kw):
        super().__init__(*a, **kw)
        self.fail = fail
        self.login_calls = 0

    def _login(self):
        self.login_calls += 1
        if self.fail:
            raise GraywolfError("login failed: HTTP 401")
        self._authed = True


class CheckAuthTest(unittest.TestCase):
    def test_returns_true_when_login_succeeds(self):
        self.assertTrue(_StubClient("http://x", "u", "p").check_auth())

    def test_returns_false_instead_of_raising_when_login_fails(self):
        # Never raises: the caller is a health probe, and an exception here
        # would turn "credentials are wrong" into "the request failed".
        self.assertFalse(_StubClient("http://x", "u", "p", fail=True).check_auth())

    def test_forces_a_fresh_login_even_when_already_authed(self):
        # A probe that can pass on a session established before the password
        # changed proves nothing -- which is the bug being closed.
        c = _StubClient("http://x", "u", "p")
        c._authed = True
        c.check_auth()
        self.assertEqual(c.login_calls, 1)

    def test_clears_the_cookie_jar_so_a_live_session_cannot_authorise_it(self):
        c = _StubClient("http://x", "u", "p")
        self.assertIsInstance(c._jar, CookieJar)
        c.check_auth()
        self.assertEqual(len(list(c._jar)), 0)

    def test_a_failed_probe_leaves_the_client_unauthed(self):
        c = _StubClient("http://x", "u", "p", fail=True)
        c._authed = True
        c.check_auth()
        self.assertFalse(c._authed)

    def test_repeated_probes_each_re_login(self):
        c = _StubClient("http://x", "u", "p")
        c.check_auth(); c.check_auth()
        self.assertEqual(c.login_calls, 2)


if __name__ == "__main__":
    unittest.main()
