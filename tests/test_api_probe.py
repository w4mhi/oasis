"""
scripts/api-probe.py — the runtime half of the contract gate (§11).

Its pure decision logic is tested here because the harness is only worth its
output if the tiering is right: `--danger` guards endpoints that key a
transmitter, reboot the host, or cut the link you are probing over, and a
mistake in that table is not a failing test, it is a rebooted station.
"""

import importlib.util
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _load():
    path = os.path.join(_ROOT, "scripts", "api-probe.py")
    spec = importlib.util.spec_from_file_location("api_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe = _load()


class TieringTest(unittest.TestCase):
    def test_a_plain_get_is_readable_by_default(self):
        self.assertEqual(probe.tier_of("/api/system", "GET"), "read")

    def test_the_verb_decides_not_the_rule(self):
        """GET /api/aprs/warnings lists the operator's map pins; POST to the SAME
        rule broadcasts over RF. Tiering per-rule would either skip a useful read
        or let a verb through because its neighbour was harmless."""
        self.assertEqual(probe.tier_of("/api/aprs/warnings", "GET"), "read")
        self.assertEqual(probe.tier_of("/api/aprs/warnings", "POST"), "danger")

    def test_an_intrusive_get_is_not_read(self):
        """/api/hardware/detect runs an exclusive `rtl_test -t`, which takes the
        dongle away from whatever is using it. Read-only in HTTP terms, not on a
        live station."""
        self.assertEqual(probe.tier_of("/api/hardware/detect", "GET"), "mutate")

    def test_an_unknown_mutating_route_defaults_to_danger(self):
        """A route added tomorrow must not be callable by accident today."""
        self.assertEqual(probe.tier_of("/api/not/yet/invented", "POST"), "danger")

    def test_everything_that_transmits_or_reboots_is_danger(self):
        for rule in ("/api/setup/reboot", "/api/service", "/api/winlink/connect",
                     "/api/winlink/mailbox/out", "/api/hardware/burn-serial",
                     "/api/wifi/forget", "/api/satellites/listen"):
            self.assertEqual(probe.tier_of(rule, "POST"), "danger", rule)


class ShapeTest(unittest.TestCase):
    def test_skeleton_keeps_keys_and_types_not_values(self):
        skel = probe.type_skeleton({"ok": True, "n": 3, "s": "x", "z": None})
        self.assertEqual(skel, {"ok": "bool", "n": "int", "s": "str", "z": "null"})

    def test_a_list_reduces_to_its_first_element(self):
        """Two stations hold different numbers of aircraft; the SHAPE is what
        must match."""
        self.assertEqual(probe.type_skeleton([{"hex": "a"}, {"hex": "b"}]),
                         [{"hex": "str"}])

    def test_redaction_replaces_volatile_values_and_records_them(self):
        clean, vol = probe.redact({"ok": True, "uptime_s": 1234, "port": 8083})
        self.assertEqual(clean["ok"], True)
        self.assertEqual(clean["port"], 8083, "stable values survive")
        self.assertEqual(clean["uptime_s"], "<volatile>")
        self.assertIn(("uptime_s", 1234), [(p.lstrip("."), v) for p, v in vol])

    def test_redaction_recurses_into_nested_objects(self):
        clean, _ = probe.redact({"gps": {"lat": 1.0, "seconds": 9}})
        self.assertEqual(clean["gps"]["seconds"], "<volatile>")
        self.assertEqual(clean["gps"]["lat"], 1.0)


class ContractCheckTest(unittest.TestCase):
    def test_a_clean_response_has_no_findings(self):
        self.assertEqual(probe.check_contract("/x", 200, {"ok": True}), [])

    def test_ok_false_with_200_is_caught(self):
        bad = probe.check_contract("/x", 200, {"ok": False, "code": "X"})
        self.assertTrue(any("§2" in b for b in bad))

    def test_ok_false_without_a_code_is_caught(self):
        bad = probe.check_contract("/x", 503, {"ok": False, "error": "down"})
        self.assertTrue(any("§3" in b for b in bad))

    def test_a_bare_array_is_caught(self):
        self.assertTrue(probe.check_contract("/x", 200, ["a", "b"]))

    def test_an_unbounded_list_is_caught(self):
        bad = probe.check_contract("/x", 200, {"ok": True, "stations": [1, 2]})
        self.assertTrue(any("§4" in b for b in bad))

    def test_a_bounded_list_passes(self):
        self.assertEqual(probe.check_contract("/x", 200, {
            "ok": True, "stations": [1], "total": 1, "truncated": False,
            "limit": 500}), [])

    def test_a_non_iso_timestamp_is_caught(self):
        bad = probe.check_contract("/x", 200, {"ok": True, "boot_time": 1754000000})
        self.assertTrue(any("§6" in b for b in bad))


class SafetyTest(unittest.TestCase):
    def test_path_placeholders_never_name_a_real_record(self):
        """Probing must not read or delete a real message. Every placeholder is
        deliberately a non-existent id, so we exercise the NOT-FOUND shape."""
        for value in probe.PATH_VALUES.values():
            self.assertTrue("probe" in value.lower() or value == "in", value)

    def test_the_stream_endpoint_is_never_called(self):
        """It holds the dongle open for as long as the connection lives."""
        self.assertIn("/api/satellites/listen/stream", probe.SKIP)


if __name__ == "__main__":
    unittest.main()
