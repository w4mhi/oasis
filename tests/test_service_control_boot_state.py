"""Boot-state steps must not fail silently.

/api/service runs two systemctl verbs for the units whose boot state tracks
their running state (_PERSIST_BOOT_STATE): `enable` + `start`, or `stop` +
`disable`. The step loop used to keep only the PRIMARY verb's result, so a
failed enable/disable was discarded and the endpoint answered a clean success.
The operator found out at the next reboot, when the service they had "started"
was not running.

This is a realistic failure, not a hypothetical: the sudoers rule is per-unit
AND per-verb (scripts/enable-service-controls.py UNITS x ACTIONS), so a rule
written before a unit was installed authorizes `start` but not `enable`.
"""
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, os.path.dirname(_HERE))
import app as oasis_app
from routes import service_control


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner(outcomes):
    """subprocess.run stand-in. `outcomes` maps a systemctl verb to a _Proc;
    anything unlisted succeeds. is-active answers 'active'."""
    calls = []

    def run(argv, **kw):
        if argv[:1] == ["systemctl"] and argv[1] == "is-active":
            return _Proc(0, "active\n")
        verb = argv[3] if argv[:2] == ["sudo", "-n"] else argv[1]
        calls.append(verb)
        return outcomes.get(verb, _Proc(0))

    return run, calls


class BootStateStepTest(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()
        # dump1090-fa is in _PERSIST_BOOT_STATE, so start => ["enable", "start"].
        self.unit = "dump1090-fa"
        self.assertIn(self.unit, service_control._PERSIST_BOOT_STATE)

    def _post(self, action, outcomes):
        run, calls = _runner(outcomes)
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch("subprocess.run", side_effect=run):
            r = self.c.post("/api/service",
                            json={"unit": self.unit, "action": action},
                            headers={"X-OASIS-Request": "1"})
        return r, r.get_json(), calls

    def test_failed_enable_is_reported_not_swallowed(self):
        r, d, calls = self._post("start", {
            "enable": _Proc(1, "", "Sorry, user pi is not allowed to execute ..."),
        })
        self.assertIn("enable", calls)
        self.assertIn("start", calls)
        # The service DID start, so this is not a hard failure...
        self.assertTrue(d["ok"], "a started service must not be reported as failed")
        # ...but the caller must be told the boot state was not persisted.
        self.assertFalse(d["boot_state_persisted"])
        self.assertIn("warning", d)
        self.assertIn("reboot", d["warning"].lower())
        # And the permission hint must ride along, since that's the usual cause.
        self.assertIn("enable-service-controls.py", d["warning"])

    def test_failed_disable_on_stop_is_reported(self):
        r, d, calls = self._post("stop", {"disable": _Proc(1, "", "boom")})
        self.assertEqual(calls, ["stop", "disable"])
        self.assertTrue(d["ok"])
        self.assertFalse(d["boot_state_persisted"])
        self.assertIn("disable", d["warning"])

    def test_clean_run_reports_boot_state_persisted_and_no_warning(self):
        r, d, calls = self._post("start", {})
        self.assertEqual(calls, ["enable", "start"])
        self.assertTrue(d["ok"])
        self.assertTrue(d["boot_state_persisted"])
        # §5: always present. null/None is "the question does not apply",
        # which is a different fact from "nothing went wrong".
        self.assertIsNone(d["warning"])

    def test_failed_primary_verb_still_fails_the_request(self):
        # A broken enable must not mask a broken start, and vice versa.
        r, d, _ = self._post("start", {"start": _Proc(1, "", "unit not found")})
        self.assertEqual(r.status_code, 500)
        self.assertFalse(d["ok"])
        self.assertIn("unit not found", d["error"])

    def test_non_persisting_unit_has_one_step_and_no_boot_claim(self):
        # kiwix is deliberately transient — its boot state is left untouched, so
        # there is nothing to persist and nothing to warn about.
        unit = "kiwix"
        self.assertNotIn(unit, service_control._PERSIST_BOOT_STATE)
        run, calls = _runner({})
        with mock.patch.object(sys, "platform", "linux"), \
             mock.patch("subprocess.run", side_effect=run):
            r = self.c.post("/api/service", json={"unit": unit, "action": "start"},
                            headers={"X-OASIS-Request": "1"})
        d = r.get_json()
        self.assertEqual(calls, ["start"])
        self.assertTrue(d["ok"])
        self.assertIsNone(d["boot_state_persisted"], "nothing to persist")
        self.assertIsNone(d["warning"])

    def test_restart_never_touches_boot_state(self):
        r, d, calls = self._post("restart", {})
        self.assertEqual(calls, ["restart"])
        self.assertIsNone(d["warning"])
        # dump1090-fa IS a boot-state-tracking unit, but a RESTART runs only
        # ["restart"] — no enable, no disable. Reporting True here (as it did)
        # claimed a persistence guarantee that was never performed. The old
        # assertion only checked `warning`, so the false claim went unseen.
        self.assertIsNone(d["boot_state_persisted"],
                          "restart touches no boot state, so it must claim none")


class SudoersHintTest(unittest.TestCase):
    def test_hint_appended_only_for_permission_failures(self):
        h = service_control._with_sudoers_hint
        for msg in ("sudo: a password is required",
                    "sudo: a terminal is required to read the password",
                    "Sorry, user pi is not allowed to execute '/bin/systemctl enable x'"):
            self.assertIn("enable-service-controls.py", h(msg), msg)
        # An unrelated failure must not be mislabelled as a permission problem.
        self.assertNotIn("enable-service-controls.py", h("Unit foo.service not found."))
        self.assertEqual(h(""), "")


if __name__ == "__main__":
    unittest.main()
