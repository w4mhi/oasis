import io, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import repeaterbook as RB


def _resp(body_bytes):
    resp = mock.MagicMock()
    resp.read.return_value = body_bytes
    resp.__enter__.return_value = resp
    return resp


def _json_opener(payload):
    def _open(req, timeout=None):
        return _resp(json.dumps(payload).encode())
    return _open


def _error_opener(status):
    import urllib.error

    def _open(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, status, "err", {},
                                     io.BytesIO(b""))
    return _open


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "static", "repeaterbook", "data"),
                    exist_ok=True)


class TestStates(unittest.TestCase):
    def test_all_states_plus_dc(self):
        self.assertEqual(len(RB.STATES), 51)
        for code in ("WA", "TX", "DC", "AK", "HI"):
            self.assertIn(code, RB.STATES)

    def test_every_code_has_a_full_name(self):
        for code in RB.STATES:
            self.assertTrue(RB.STATE_NAMES[code])

    def test_user_agent_has_app_and_contact(self):
        # RepeaterBook rejects generic User-Agents and requires an app
        # identifier plus a contact email.
        self.assertIn("OASIS", RB.USER_AGENT)
        self.assertIn("@", RB.USER_AGENT)


class TestFetchState(unittest.TestCase):
    def test_returns_records_from_results_key(self):
        recs = RB.fetch_state("tok", "WA",
                              opener=_json_opener({"results": [{"a": 1}]}))
        self.assertEqual(recs, [{"a": 1}])

    def test_bare_list_payload_also_works(self):
        recs = RB.fetch_state("tok", "WA", opener=_json_opener([{"a": 1}]))
        self.assertEqual(recs, [{"a": 1}])

    def test_429_raises_rate_limited(self):
        with self.assertRaises(RB.RateLimited):
            RB.fetch_state("tok", "WA", opener=_error_opener(429))

    def test_401_raises_auth_rejected(self):
        # Distinct from a transport failure: retrying cannot help, and the
        # operator must be told to fix the token.
        with self.assertRaises(RB.AuthRejected):
            RB.fetch_state("tok", "WA", opener=_error_opener(401))

    def test_403_raises_auth_rejected(self):
        with self.assertRaises(RB.AuthRejected):
            RB.fetch_state("tok", "WA", opener=_error_opener(403))

    def test_ok_false_body_raises_auth_rejected(self):
        # The live API answers 401 with {"ok":false,"message":"Unknown app
        # token."}; a 200 carrying the same shape must not be treated as data.
        opener = _json_opener({"ok": False, "error_code": "auth_invalid",
                               "message": "Unknown app token."})
        with self.assertRaises(RB.AuthRejected):
            RB.fetch_state("tok", "WA", opener=opener)

    def test_html_payload_rejected(self):
        # A captive portal returns 200 with a login page. Never let that
        # overwrite good data.
        def _open(req, timeout=None):
            return _resp(b"<!DOCTYPE html><html>login</html>")
        with self.assertRaises(ValueError):
            RB.fetch_state("tok", "WA", opener=_open)

    def test_unexpected_shape_rejected(self):
        with self.assertRaises(ValueError):
            RB.fetch_state("tok", "WA", opener=_json_opener("a string"))

    def test_sends_token_and_user_agent(self):
        seen = {}

        def _open(req, timeout=None):
            seen.update({k.lower(): v for k, v in req.headers.items()})
            return _resp(b'{"results":[]}')
        RB.fetch_state("tok123", "WA", opener=_open)
        self.assertEqual(seen.get("X-rb-app-token".lower()), "tok123")
        self.assertIn("OASIS", seen.get("User-agent".lower(), ""))

    def test_requests_the_right_state_name(self):
        seen = {}

        def _open(req, timeout=None):
            seen["url"] = req.full_url
            return _resp(b'{"results":[]}')
        RB.fetch_state("tok", "TX", opener=_open)
        self.assertIn("Texas", seen["url"])


class TestStorage(_Tmp):
    def test_write_then_read_index(self):
        RB.write_state_file(self.d, "WA", [{"a": 1}, {"a": 2}])
        idx = RB.read_index(self.d)
        self.assertEqual(idx["WA"]["count"], 2)
        self.assertIn("fetched_at", idx["WA"])

    def test_state_file_is_a_json_list(self):
        RB.write_state_file(self.d, "TX", [{"a": 1}])
        with open(RB.state_path(self.d, "TX")) as fh:
            self.assertEqual(json.load(fh), [{"a": 1}])

    def test_records_stored_opaquely(self):
        # The client must not care about field names — only the viewer does.
        weird = [{"anything": "at all", "nested": {"x": [1, 2]}}]
        RB.write_state_file(self.d, "WA", weird)
        with open(RB.state_path(self.d, "WA")) as fh:
            self.assertEqual(json.load(fh), weird)

    def test_empty_records_refused(self):
        # An empty result is far more likely a bad request than a state with
        # zero repeaters. Never replace good data with nothing.
        with self.assertRaises(ValueError):
            RB.write_state_file(self.d, "WA", [])

    def test_missing_index_is_empty(self):
        self.assertEqual(RB.read_index(self.d), {})


class TestNextStates(_Tmp):
    def test_never_fetched_states_come_first(self):
        RB.write_state_file(self.d, "WA", [{"a": 1}])
        nxt = RB.next_states(self.d, now=0.0, max_age_days=180.0, limit=3)
        self.assertNotIn("WA", nxt)
        self.assertEqual(len(nxt), 3)

    def test_limit_respected(self):
        nxt = RB.next_states(self.d, now=0.0, max_age_days=180.0, limit=5)
        self.assertEqual(len(nxt), 5)

    def test_stale_state_returns_when_all_fetched(self):
        for s in RB.STATES:
            RB.write_state_file(self.d, s, [{"a": 1}])
        idx = RB.read_index(self.d)
        idx["WA"]["fetched_at"] = 0.0
        RB.write_index(self.d, idx)
        nxt = RB.next_states(self.d, now=86400.0 * 400, max_age_days=180.0,
                             limit=1)
        self.assertEqual(nxt, ["WA"])

    def test_all_fresh_returns_empty(self):
        for s in RB.STATES:
            RB.write_state_file(self.d, s, [{"a": 1}])
        self.assertEqual(
            RB.next_states(self.d, now=0.0, max_age_days=180.0, limit=5), [])


if __name__ == "__main__":
    unittest.main()
