"""
Enforces docs/api-contract.md.

The contract exists because three consumers pay for inconsistency: small local
models (which cannot infer intent from an irregular shape), MCP servers and other
programmatic clients (which re-implement the same normalisation once per
inconsistency), and the front-end. This test is what keeps the contract from being
a document nobody checks.

Facts come from tests/api_contract_scan.py, which parses the AST and walks each
decorated route's own body. An earlier regex version reported returns belonging to
neighbouring helper functions against whichever route was declared above them —
false positives, and a gate that cries wolf gets switched off.

Design: **shrinking allowlists.** A conformance test that failed on all 85 routes
the day it landed would be disabled within a week, so the sets below record what
is known non-conforming today. They may only shrink: the test fails when a
conforming endpoint regresses AND when a listed endpoint starts conforming without
being removed from its list, so they cannot rot into permanent exemptions.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)

from api_contract_scan import scan_tree  # noqa: E402

# ── Migration status · SHRINK ONLY ───────────────────────────────────────────
# Never add an entry to make a NEW endpoint pass; new endpoints conform on the way
# in. Removing an entry is the last step of migrating that endpoint.

# §2 — `ok: false` served with HTTP 200. These use `ok` to mean "the news is bad"
# rather than "the request failed", which makes the two indistinguishable.
_OK_FALSE_200 = frozenset({
    # /api/health/{probe,service,feed-flow} graduated 2026-08-08 — the cluster
    # this rule was written for. See tests/test_health_contract.py.
    # /api/wifi/{scan,connect,forget} + /api/satellites/refresh graduated
    # 2026-08-08.
    "/api/service", "/api/setup/reboot",
    "/api/winlink/log",
})

# §1 — no `ok` envelope at all; the client cannot branch without knowing the
# endpoint's private shape.
_NO_ENVELOPE = frozenset({
    # /api/adsb/alerts graduated 2026-08-07 — the first endpoint migrated to the
    # contract. See services/adsb/routes.py and tests/test_adsb_alerts_contract.py.
    "/api/hardware/devices", "/api/hardware/guardian",
    # /api/satellites/* graduated 2026-08-08 — roster + prediction first, then
    # the listen/* SDR routes.
})

# §1/§10 — the response shape is not visible at the return site, either because
# it is `jsonify(<variable>)` or because the whole response is delegated to a
# helper (`return _adsb_proxy(...)`). Both are unverifiable and both hid real
# non-conformance. Migrating one means building the envelope inline at the return.
_UNVERIFIABLE = frozenset({
    # graduated 2026-08-07: /api/adsb/{alerts,aircraft,recent,health}
    "/api/adsb/history",     "/api/diagnostics", "/api/forms/list", "/api/forms/save",
    "/api/hardware/console", "/api/hardware/detect",
    # /api/system left this list on the SURFACE FIX, not on a migration: its own
    # return site was always a literal dict. It was listed because the scan
    # merged it with graywolf-api's same-named opaque route. Counting that as
    # progress would be lying to the ratchet.
    "/api/list-ics205", "/api/save-ics205", "/api/service",
    "/api/setup/jobs/<job_id>",
    "/api/winlink/aliases", "/api/winlink/connect", "/api/winlink/disconnect",
    "/api/winlink/mailbox/<box>", "/api/winlink/mailbox/<box>/<mid>",
    "/api/winlink/mailbox/out", "/api/winlink/status",
    # Newly visible once the scanner learned to tell a JSON Response()
    # from a streamed one — they were never conforming, only unseen.
    "/api/winlink/rmslist", "/api/winlink/mailbox/<box>/<mid>/<path:attachment>",
})


# Routes whose ok:false status is COMPUTED, so the AST scan cannot read it (see
# ScanSanityTest). Not debt — each is covered by a runtime test asserting the
# real status. This list exists so the gate cannot go quiet by accident.
_DYNAMIC_ERROR_STATUS = [
    "/api/satellites/listen",          # _CaptureError carries status + slug
    "/api/satellites/listen/stream",   # 409 busy / 400 otherwise
]


# Routes on the internal daemons are NOT the OASIS API — see _DAEMON_MODULES.
def _routes():
    return [r for r in scan_tree(_ROOT) if r[4] == "oasis"]


def _daemon_routes():
    return [r for r in scan_tree(_ROOT) if r[4] == "daemon"]


def _by_rule():
    out = {}
    for rule, path, fname, lineno, surface, facts in _routes():
        out.setdefault(rule, []).extend(
            [(os.path.relpath(path, _ROOT), f) for f in facts])
    return out


# The only two modules allowed to serve routes that this contract does not
# govern. Both build their own Flask app inside a factory and listen on their
# own port; the Flask route in front of each is the contract boundary (§10).
_DAEMON_MODULES = frozenset({
    "services/aprs/common/aprs.py",              # graywolf-api, :8085
    "services/graywolf/enable-graywolf-api.py",  # the copy its installer writes
})


class ScanSanityTest(unittest.TestCase):
    def test_the_scan_sees_the_whole_api(self):
        rules = {r for r, _, _, _, _, _ in _routes()}
        self.assertGreater(len(rules), 60, "route scan drifted — layout or decorators changed?")

    def test_the_daemon_surface_is_exactly_the_two_known_servers(self):
        """The oasis/daemon split is inferred structurally (a route nested in a
        factory function), which is only safe if it cannot drift silently. A new
        internal server, or an OASIS route moved inside a factory, would quietly
        exempt itself from the whole contract — so pin the set.

        This split is not bookkeeping. Three servers in this repo serve a route
        called `/api/system`; merging them by rule string held the conforming
        Flask one hostage to the daemon's opaque `jsonify(gather_system())`.
        """
        got = {os.path.relpath(path, _ROOT) for _, path, _, _, _, _ in _daemon_routes()}
        self.assertEqual(got, _DAEMON_MODULES,
                         "the set of internal daemon modules changed — if this is a "
                         "new server, decide deliberately whether the contract governs "
                         "it; if an OASIS route moved into a factory, it just left the "
                         "contract by accident")

    def test_daemon_routes_still_carry_an_envelope(self):
        """Internal, but not unexamined. The full contract is aimed at the
        surface a browser and a model actually see, so daemons are held to the
        one rule that keeps their proxies honest: every response says `ok`.
        `/api/system` is the one exception — its shape is built in
        gather_system(), and services/aprs/routes.py re-normalises it anyway."""
        offenders = sorted({
            f"{rule}  ({os.path.relpath(path, _ROOT)}:{f['line']})"
            for rule, path, _, _, _, facts in _daemon_routes()
            if rule != "/api/system"
            for f in facts if not f["opaque"] and not f["has_ok"]})
        self.assertEqual(offenders, [],
                         "internal daemon responses with no `ok` envelope:\n  "
                         + "\n  ".join(offenders))


class EnvelopeTest(unittest.TestCase):
    def test_every_json_route_carries_ok(self):
        offenders = []
        for rule, entries in _by_rule().items():
            if not entries or rule in _NO_ENVELOPE:
                continue
            # ALL returns must carry it. A route whose POST envelopes and whose
            # GET does not is precisely the inconsistency this contract removes.
            missing = [(rel, f) for rel, f in entries
                       if not f["opaque"] and not f["has_ok"]]
            if missing:
                rel, f = missing[0]
                offenders.append(f"{rule}  ({rel}:{f['line']})")
        self.assertEqual(offenders, [],
                         "JSON responses with no `ok` envelope (contract §1):\n  "
                         + "\n  ".join(sorted(offenders)))

    def test_no_ok_true_response_carries_an_error_key(self):
        offenders = [f"{rule}  ({rel}:{f['line']})"
                     for rule, entries in _by_rule().items()
                     for rel, f in entries
                     if f["ok_value"] is True and f["has_error"]]
        self.assertEqual(offenders, [],
                         "`ok: true` responses carrying an `error` key (contract §2):\n  "
                         + "\n  ".join(sorted(offenders)))


class OkMeansRequestSucceededTest(unittest.TestCase):
    def test_ok_is_never_computed_from_domain_state(self):
        """`ok: bool(path)`, `ok: active == "active"`, `ok: exists`.

        These slipped past the ok:false-with-200 check for a structural reason:
        there is no constant to compare, so the scan recorded ok_value=None and
        every rule that looks for `False` found nothing. Yet a computed `ok` is
        ALWAYS the §2 defect in its purest form — the value is being derived
        from what the answer says rather than from whether answering worked.

        No allowlist. Nothing in the repo does this any more, and a new one
        should be caught the day it is written rather than negotiated.
        """
        offenders = sorted({f"{rule}  ({rel}:{f['line']})"
                            for rule, entries in _by_rule().items()
                            for rel, f in entries if f["ok_computed"]})
        self.assertEqual(offenders, [],
                         "`ok` computed from domain state (contract §2) — give the "
                         "state its own typed field and make `ok` a literal:\n  "
                         + "\n  ".join(offenders))

    def test_a_dynamic_error_status_is_covered_by_a_runtime_test(self):
        """Some routes compute their status — `return jsonify(…), e.code`, where
        the exception carries it. That is good code (one handler, eight causes),
        but this scan cannot read the value, so the ok:false-with-200 rule above
        skips those returns rather than guessing.

        Skipping silently would be a hole, so the routes that do it are listed
        here and each one has a runtime test asserting the real status. Keep the
        two in step: if a route joins this list, it needs those tests."""
        dynamic = sorted({rule for rule, entries in _by_rule().items()
                          for _, f in entries
                          if f["status_dynamic"] and f["ok_value"] is False})
        self.assertEqual(dynamic, _DYNAMIC_ERROR_STATUS,
                         "a route's error status became (un)computed — see "
                         "tests/test_satellites_listen_contract.py for the "
                         "runtime coverage this list stands in for")

    def test_no_ok_false_served_with_http_200(self):
        """The worst ambiguity for a model: a failed call and a successful report
        of bad news become indistinguishable."""
        offenders = [f"{rule}  ({rel}:{f['line']})"
                     for rule, entries in _by_rule().items()
                     if rule not in _OK_FALSE_200
                     for rel, f in entries
                     if f["ok_value"] is False and f["status"] == 200
                     and not f["status_dynamic"]]
        self.assertEqual(offenders, [],
                         "`ok: false` served with HTTP 200 (contract §2):\n  "
                         + "\n  ".join(sorted(offenders)))


class VerifiableShapeTest(unittest.TestCase):
    def test_no_route_returns_an_unverifiable_shape(self):
        """`jsonify(payload)` hides the envelope from review AND from this gate.
        17 routes were silently passing every other check for exactly this reason."""
        offenders = sorted({f"{rule}  ({rel}:{f['line']})"
                            for rule, entries in _by_rule().items()
                            if rule not in _UNVERIFIABLE
                            for rel, f in entries if f["opaque"]})
        self.assertEqual(offenders, [],
                         "responses whose shape is not visible at the return site "
                         "(contract §1) — build the dict inline:\n  " + "\n  ".join(offenders))


class AllowlistHygieneTest(unittest.TestCase):
    """The lists may only shrink, and may not reference routes that are gone."""

    def test_no_ghost_entries(self):
        rules = {r for r, _, _, _, _, _ in _routes()}
        ghosts = sorted((_OK_FALSE_200 | _NO_ENVELOPE | _UNVERIFIABLE) - rules)
        self.assertEqual(ghosts, [],
                         "allowlists reference routes that no longer exist — remove them:\n  "
                         + "\n  ".join(ghosts))

    def test_ok_false_200_list_only_shrinks(self):
        by_rule = _by_rule()
        graduated = sorted(
            rule for rule in _OK_FALSE_200
            if rule in by_rule
            and not any(f["ok_value"] is False and f["status"] == 200 for _, f in by_rule[rule]))
        self.assertEqual(graduated, [],
                         "these no longer return ok:false with 200 — remove them from "
                         "_OK_FALSE_200:\n  " + "\n  ".join(graduated))

    def test_no_envelope_list_only_shrinks(self):
        by_rule = _by_rule()
        graduated = sorted(
            rule for rule in _NO_ENVELOPE
            if rule in by_rule and by_rule[rule]
            and all(f["has_ok"] for _, f in by_rule[rule]))
        self.assertEqual(graduated, [],
                         "these now carry an `ok` envelope — remove them from "
                         "_NO_ENVELOPE:\n  " + "\n  ".join(graduated))

    def test_unverifiable_list_only_shrinks(self):
        by_rule = _by_rule()
        graduated = sorted(
            rule for rule in _UNVERIFIABLE
            if rule in by_rule and by_rule[rule]
            and not any(f["opaque"] for _, f in by_rule[rule]))
        self.assertEqual(graduated, [],
                         "these now build their response inline — remove them from "
                         "_UNVERIFIABLE:\n  " + "\n  ".join(graduated))

    def test_migration_progress_is_visible(self):
        """Prints the remaining debt so the number is in front of us every run."""
        total = len({r for r, _, _, _, _, _ in _routes()})
        remaining = len(_OK_FALSE_200 | _NO_ENVELOPE | _UNVERIFIABLE)
        # A ratchet: this is the migration debt and may only go DOWN. Lower it
        # as endpoints graduate; never raise it to make something pass.
        self.assertLessEqual(remaining, 23,
                             f"the allowlists grew to {remaining} — they may only shrink")
        self.assertGreater(total, remaining)


class ContractDocTest(unittest.TestCase):
    def test_contract_exists_and_points_at_this_test(self):
        doc = os.path.join(_ROOT, "docs", "api-contract.md")
        self.assertTrue(os.path.isfile(doc), "docs/api-contract.md is the spec this test enforces")
        with open(doc, encoding="utf-8") as fh:
            text = fh.read()
        for heading in ("Envelope", "Errors", "Lists", "Timestamps", "Idempotency"):
            self.assertIn(heading, text, f"contract is missing its {heading} section")
        self.assertIn("tests/test_api_contract.py", text,
                      "the contract must name the test that enforces it")


if __name__ == "__main__":
    unittest.main()
