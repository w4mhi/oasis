"""
/api/winlink/* on the contract — an ENVELOPE-ONLY migration, deliberately.

Pat is a third-party Go binary we neither own nor can pin. read-state.js already
copes with MID/Mid/mid and Unread/unread, and mail.html used to try
`pick(s, "Connected", "connected")` — the front-end telling us Pat's casing
varies by version. Renaming Pat's inner fields would need a live Pat to verify
against, so OASIS took the ENVELOPE (ok, named container, list bounds, error
codes) and left Pat's inner objects untouched. That is recorded as §7 debt, not
done blind.

What WAS indefensible and is now fixed:

  §1  /mailbox/<box> and /aliases returned BARE JSON ARRAYS.
  §2  every error was Pat's own body passed through verbatim, so an OASIS
      failure and a Pat refusal were the same thing on the wire.
  §2  /log returned ok:false at HTTP 200 for "this host has no journald".

This file also carries the runtime half of _DYNAMIC_ERROR_STATUS for the two
winlink routes that forward Pat's own status code.
"""

import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

import app as oasis_app  # noqa: E402

_HDR = {"X-OASIS-Request": "1"}

_MSG = {"MID": "ABC123", "Subject": "ICS-213 traffic", "From": {"Addr": "W4MHI"},
        "Unread": True}


class _Resp:
    def __init__(self, body, status=200, ctype="application/json"):
        self._b, self.status = body, status
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def pat(self, body, status=200):
        """Pat answers with `body` (a python object, or raw bytes)."""
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return mock.patch.object(urllib.request, "urlopen",
                                 lambda *a, **k: _Resp(raw, status))

    def pat_raises(self, exc):
        def boom(*a, **k):
            raise exc
        return mock.patch.object(urllib.request, "urlopen", boom)


class MailboxListTest(_Base):
    def test_the_bare_array_is_gone(self):
        """§1: this returned a naked JSON array — no envelope to branch on."""
        with self.pat([_MSG, dict(_MSG, MID="DEF456")]):
            r = self.c.get("/api/winlink/mailbox/in")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIsInstance(d, dict, "a bare array is a §1 violation")
        self.assertIs(d["ok"], True)
        self.assertEqual(d["box"], "in")
        for key in ("messages", "total", "count", "truncated", "limit"):
            self.assertIn(key, d)
        self.assertEqual(d["total"], 2)

    def test_pat_inner_fields_pass_through_untouched(self):
        """The deliberate boundary: we own the envelope, Pat owns the record.
        read-state.js binds MID/Unread and must keep working."""
        with self.pat([_MSG]):
            d = self.c.get("/api/winlink/mailbox/in").get_json()
        self.assertEqual(d["messages"][0], _MSG)

    def test_empty_mailbox_is_success(self):
        with self.pat([]):
            d = self.c.get("/api/winlink/mailbox/sent").get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["messages"], [])
        self.assertEqual(d["total"], 0)

    def test_pat_answering_null_does_not_crash(self):
        with self.pat(None):
            d = self.c.get("/api/winlink/mailbox/in").get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["messages"], [])

    def test_limit_bounds_the_list(self):
        with self.pat([dict(_MSG, MID=str(i)) for i in range(50)]):
            d = self.c.get("/api/winlink/mailbox/in?limit=10").get_json()
        self.assertEqual(len(d["messages"]), 10)
        self.assertEqual(d["total"], 50)
        self.assertIs(d["truncated"], True)

    def test_unknown_mailbox_is_400_with_a_code(self):
        r = self.c.get("/api/winlink/mailbox/nope")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "UNKNOWN_MAILBOX")


class MessageTest(_Base):
    def test_a_message_is_wrapped_not_bare(self):
        with self.pat(_MSG):
            d = self.c.get("/api/winlink/mailbox/in/ABC123").get_json()
        self.assertIs(d["ok"], True)
        self.assertEqual(d["mid"], "ABC123")
        self.assertEqual(d["message"], _MSG)

    def test_delete_is_idempotent_shaped(self):
        with self.pat(b""):
            r = self.c.delete("/api/winlink/mailbox/in/ABC123", headers=_HDR)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["deleted"], True)

    def test_delete_still_needs_the_csrf_header(self):
        self.assertEqual(self.c.delete("/api/winlink/mailbox/in/ABC123").status_code, 403)


class PatFailureTest(_Base):
    """§2: an OASIS failure and a Pat refusal used to be the same bytes."""

    def test_pat_down_is_503_with_a_code(self):
        with self.pat_raises(urllib.error.URLError("connection refused")):
            r = self.c.get("/api/winlink/mailbox/in")
        self.assertEqual(r.status_code, 503)
        d = r.get_json()
        self.assertIs(d["ok"], False)
        self.assertEqual(d["code"], "WINLINK_UNAVAILABLE")

    def test_pat_timeout_is_503_with_its_own_code(self):
        with self.pat_raises(TimeoutError()):
            r = self.c.get("/api/winlink/status")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["code"], "WINLINK_TIMEOUT")

    def test_a_pat_refusal_keeps_pats_status_but_says_it_was_pat(self):
        err = urllib.error.HTTPError("u", 400, "Bad Request", {},
                                     io.BytesIO(b"Validation error: missing date"))
        with self.pat_raises(err):
            r = self.c.get("/api/winlink/mailbox/in/ABC123")
        self.assertEqual(r.status_code, 400, "a 400 from Pat really is our fault")
        d = r.get_json()
        self.assertIs(d["ok"], False)
        self.assertEqual(d["code"], "PAT_REJECTED")
        self.assertIn("Validation error", d["error"], "Pat's own text survives")

    def test_a_non_json_body_is_502(self):
        with self.pat(b"<html>not json</html>"):
            r = self.c.get("/api/winlink/status")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()["code"], "WINLINK_BAD_RESPONSE")


class StatusTest(_Base):
    def test_the_casing_guesswork_moves_off_the_client(self):
        """mail.html ran pick(s, "Connected", "connected") because Pat's casing
        varies. One lower-case field now, for every consumer."""
        with self.pat({"Connected": True, "Dialing": False}):
            d = self.c.get("/api/winlink/status").get_json()
        self.assertIs(d["connected"], True)
        self.assertIs(d["dialing"], False)
        with self.pat({"connected": False, "dialing": True}):
            d = self.c.get("/api/winlink/status").get_json()
        self.assertIs(d["connected"], False)
        self.assertIs(d["dialing"], True)

    def test_an_unknown_flag_is_null_not_false(self):
        """§5: Pat didn't say, so we don't claim it is disconnected."""
        with self.pat({}):
            d = self.c.get("/api/winlink/status").get_json()
        self.assertIsNone(d["connected"])
        self.assertIsNone(d["dialing"])

    def test_pats_raw_object_still_ships(self):
        with self.pat({"Connected": True, "SomethingNew": 42}):
            d = self.c.get("/api/winlink/status").get_json()
        self.assertEqual(d["status"]["SomethingNew"], 42)


class AliasesTest(_Base):
    def test_a_list_from_pat_is_normalised(self):
        with self.pat(["telnet", "ardop"]):
            d = self.c.get("/api/winlink/aliases").get_json()
        self.assertEqual(d["aliases"], ["telnet", "ardop"])
        self.assertEqual(d["count"], 2)

    def test_an_object_from_pat_is_normalised_to_the_same_shape(self):
        """Pat answers with either container depending on version; mail.html had
        to branch on which. One shape now."""
        with self.pat({"ardop": "…", "telnet": "…"}):
            d = self.c.get("/api/winlink/aliases").get_json()
        self.assertEqual(d["aliases"], ["ardop", "telnet"])


class LogTest(_Base):
    def test_no_journald_is_a_successful_answer(self):
        """§2: it was ok:false at HTTP 200 — a failed call and a host without
        systemd were the same value."""
        with mock.patch.object(sys, "platform", "darwin"):
            r = self.c.get("/api/winlink/log")
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["supported"], False)
        self.assertIs(d["read"], False)
        self.assertEqual(d["reason"], "not-linux")

    def test_an_unreadable_journal_is_read_false_not_ok_false(self):
        done = mock.Mock(returncode=1, stdout="", stderr="Permission denied")
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch("subprocess.run", return_value=done):
            d = self.c.get("/api/winlink/log").get_json()
        self.assertIs(d["ok"], True)
        self.assertIs(d["read"], False)
        self.assertEqual(d["reason"], "journal-unreadable")
        self.assertIn("systemd-journal", d["detail"])

    def test_a_quiet_pat_is_read_true_with_no_lines(self):
        """The distinction `read` exists for: an empty tail from a SUCCESSFUL
        read means Pat has been quiet, not that we couldn't look."""
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch("subprocess.run", return_value=done):
            d = self.c.get("/api/winlink/log").get_json()
        self.assertIs(d["read"], True)
        self.assertEqual(d["lines"], [])
        self.assertIsNone(d["reason"])


class DynamicStatusRuntimeTest(_Base):
    """The runtime half of _DYNAMIC_ERROR_STATUS: these two forward Pat's own
    status code, so the AST scan cannot read the literal."""

    def test_attachment_errors_are_enveloped_not_pats_raw_body(self):
        err = urllib.error.HTTPError("u", 404, "NF", {}, io.BytesIO(b"no such file"))
        with self.pat_raises(err):
            r = self.c.get("/api/winlink/mailbox/in/ABC123/form.xml")
        self.assertEqual(r.status_code, 404)
        d = r.get_json()
        self.assertIs(d["ok"], False)
        self.assertEqual(d["code"], "PAT_REJECTED")

    def test_attachment_refusals_are_never_http_200(self):
        for exc in (urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b"boom")),
                    urllib.error.URLError("refused"), TimeoutError()):
            with self.pat_raises(exc):
                r = self.c.get("/api/winlink/mailbox/in/ABC123/form.xml")
            self.assertNotEqual(r.status_code, 200, exc)
            self.assertIs(r.get_json()["ok"], False, exc)

    def test_rmslist_refusals_are_never_http_200(self):
        for exc in (urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b"down")),
                    urllib.error.URLError("refused"), TimeoutError()):
            with self.pat_raises(exc):
                r = self.c.get("/api/winlink/rmslist?mode=ardop")
            self.assertNotEqual(r.status_code, 200, exc)
            self.assertIs(r.get_json()["ok"], False, exc)

    def test_rmslist_unknown_mode_is_400(self):
        r = self.c.get("/api/winlink/rmslist?mode=carrier-pigeon")
        self.assertEqual(r.status_code, 400)
        self.assertIs(r.get_json()["ok"], False)


if __name__ == "__main__":
    unittest.main()
