"""
/api/system — the kiosk's endpoint, and the assistant's `system_health` tool.

Polled every 5 s by oasis-dashboard/dashboard.html and index.html, read by four
checks in common/diagnostics.py and by the CM4 stack's physical panel. It is one
of the ~11 endpoints a small model ever sees, which is why its shape matters
more than the endpoint's own consumer count suggests.

Three §5 defects, all of the same family — the response changed SHAPE depending
on the machine it ran on, so a consumer could not learn it once:

  disk        {label,total_gb,…}  or  {"error": "unavailable"}
  load        {avg1,cores,pct}    or  null (Windows, no getloadavg)
  gps/chrony  keys appeared and disappeared with fix state / query permission

A caller then cannot distinguish "this field is absent on this build" from
"this value is genuinely unknown". The rule applied here: **null means the
subsystem is absent; a dict always carries its full key set.**

Plus §6: `boot_str` was "Fri Aug 07 19:29" — no year, no timezone, parseable by
a human reading a card and by nothing else.
"""

import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))

import app as oasis_app          # noqa: E402
from routes import system as sysmod   # noqa: E402

_TOP_LEVEL = ("ok", "hostname", "ip", "cpu_pct", "cpu_count", "cpu_cores",
              "top_procs", "cpu_temp_c", "ram", "disk", "load", "uptime_s",
              "boot_time", "fcc_db_updated", "throttle", "net", "gps",
              "chrony", "cooling")

_ISO = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class _Base(unittest.TestCase):
    def setUp(self):
        oasis_app.app.config["TESTING"] = True
        self.c = oasis_app.app.test_client()

    def get(self):
        return self.c.get("/api/system")


class EnvelopeTest(_Base):
    def test_ok_and_every_key_present(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIs(d["ok"], True)
        for key in _TOP_LEVEL:
            self.assertIn(key, d, f"§5: `{key}` must always be present")

    def test_no_removed_field_lingers(self):
        d = self.get().get_json()
        for gone in ("uptime_sec", "boot_str", "fcc_db_date"):
            self.assertNotIn(gone, d, f"`{gone}` was renamed; two names for one "
                                      f"fact is the defect being removed")

    def test_psutil_absent_is_a_real_failure_with_a_code(self):
        """§2/§3: unlike a stopped optional service, there is no answer to give —
        psutil is what this endpoint is made of. So NOT ok:false with HTTP 200."""
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def no_psutil(name, *a, **kw):
            if name == "psutil":
                raise ImportError("no psutil")
            return real_import(name, *a, **kw)

        with mock.patch("builtins.__import__", no_psutil):
            r = self.get()
        self.assertEqual(r.status_code, 503)
        d = r.get_json()
        self.assertIs(d["ok"], False)
        self.assertEqual(d["code"], "SYSTEM_METRICS_UNAVAILABLE")


class DiskShapeTest(_Base):
    """The worst of the three: a metrics block that turned into an error block."""

    def test_disk_always_carries_the_same_keys(self):
        d = self.get().get_json()
        self.assertEqual(set(d["disk"]),
                         {"label", "total_gb", "used_gb", "free_gb", "pct"})

    def test_unreadable_disk_is_nulls_not_an_error_object(self):
        with mock.patch("psutil.disk_usage", side_effect=OSError("nope")):
            d = self.get().get_json()
        self.assertIs(d["ok"], True, "an unreadable disk is not a failed request")
        self.assertNotIn("error", d["disk"],
                         "§5: `disk` must not switch to a different shape — both "
                         "dashboards had to guard on !d.disk.error to read d.disk.pct")
        self.assertEqual(set(d["disk"]),
                         {"label", "total_gb", "used_gb", "free_gb", "pct"})
        self.assertIsNone(d["disk"]["pct"])


class LoadShapeTest(_Base):
    def test_load_always_carries_the_same_keys(self):
        d = self.get().get_json()
        self.assertEqual(set(d["load"]), {"avg1", "cores", "pct"})

    def test_no_getloadavg_still_reports_core_count(self):
        """Windows has no getloadavg. `cores` is knowable anyway, so reporting
        the whole block as null threw away a fact we had."""
        with mock.patch("psutil.getloadavg", side_effect=AttributeError):
            d = self.get().get_json()
        self.assertEqual(set(d["load"]), {"avg1", "cores", "pct"})
        self.assertIsNone(d["load"]["avg1"])
        self.assertIsNone(d["load"]["pct"])
        self.assertGreaterEqual(d["load"]["cores"], 1)


class TimestampTest(_Base):
    def test_boot_time_is_iso_utc(self):
        d = self.get().get_json()
        self.assertRegex(d["boot_time"], _ISO)

    def test_fcc_db_updated_is_an_instant_or_null(self):
        d = self.get().get_json()
        if d["fcc_db_updated"] is not None:
            self.assertRegex(d["fcc_db_updated"], _ISO)

    def test_uptime_is_a_number_of_seconds(self):
        d = self.get().get_json()
        self.assertIsInstance(d["uptime_s"], int)
        self.assertGreaterEqual(d["uptime_s"], 0)


class SubsystemNullRuleTest(_Base):
    """null = the subsystem is absent. A dict = every key present."""

    _BLOCKS = {
        "throttle": {"raw", "now", "ever", "now_any", "ever_any"},
        "net":      {"ssid", "clients"},
        "cooling":  {"kind", "installed", "running"},
        "gps":      {"mode", "lat", "lon", "alt_m", "hdop", "seen", "used",
                     "device", "interface", "otherDetected"},
        "chrony":   {"running", "queryable", "synced", "source", "gps", "offset_s"},
    }

    def test_present_blocks_carry_their_full_key_set(self):
        d = self.get().get_json()
        for name, keys in self._BLOCKS.items():
            block = d[name]
            if block is None:
                continue      # subsystem absent on this machine — allowed
            self.assertEqual(set(block), keys,
                             f"§5: `{name}` must always carry every key")


class ChronyShapeTest(unittest.TestCase):
    """_chrony_state() returned THREE different shapes. Tested directly because
    reaching them through the endpoint means having chrony installed."""

    _KEYS = {"running", "queryable", "synced", "source", "gps", "offset_s"}

    def _run(self, is_active, tracking=None):
        def fake_run(cmd, **kw):
            out = mock.Mock()
            if cmd[0] == "systemctl":
                out.stdout = "active" if is_active else "inactive"
                out.returncode = 0
            else:
                if tracking is None:
                    raise FileNotFoundError("chronyc")
                out.stdout, out.returncode = tracking, 0
            return out
        with mock.patch.object(sysmod.subprocess, "run", fake_run):
            return sysmod._chrony_state()

    def test_not_running(self):
        s = self._run(False)
        self.assertEqual(set(s), self._KEYS)
        self.assertIs(s["running"], False)

    def test_running_but_not_queryable(self):
        """The server user often can't reach chronyd's command socket. That is
        `synced: null` — we could not ask — NOT `synced: false`, which would
        claim the clock is unsynced when we simply don't know."""
        s = self._run(True)
        self.assertEqual(set(s), self._KEYS)
        self.assertIs(s["running"], True)
        self.assertIs(s["queryable"], False)
        self.assertIsNone(s["synced"])

    def test_fully_queryable(self):
        s = self._run(True, "1,GPS,0,0,0.000123,0,0,0,0,Normal")
        self.assertEqual(set(s), self._KEYS)
        self.assertIs(s["synced"], True)
        self.assertIs(s["gps"], True)
        self.assertAlmostEqual(s["offset_s"], 0.000123)

    def test_unparseable_offset_is_null_not_a_missing_key(self):
        s = self._run(True, "1,NTP,0,0,notanumber,0,0,0,0,Normal")
        self.assertEqual(set(s), self._KEYS)
        self.assertIsNone(s["offset_s"])
        self.assertIs(s["gps"], False)


class _FakeGpsdSocket:
    """Speaks just enough of gpsd's line protocol to drive _gps_info()."""

    def __init__(self, lines):
        self._chunks = [ln.encode() + b"\n" for ln in lines] + [b""]

    def sendall(self, _):
        pass

    def settimeout(self, _):
        pass

    def recv(self, _n):
        return self._chunks.pop(0) if self._chunks else b""

    def close(self):
        pass


class GpsShapeTest(unittest.TestCase):
    """gpsd omits lat/lon/hdop until it HAS them, so an acquiring receiver used
    to return a different set of keys from one with a fix. Tested directly — a
    dev box has no gpsd, so going through the endpoint would never reach here."""

    _KEYS = {"mode", "lat", "lon", "alt_m", "hdop", "seen", "used",
             "device", "interface", "otherDetected"}

    def _info(self, lines):
        with mock.patch.object(sysmod.socket, "create_connection",
                               return_value=_FakeGpsdSocket(lines)), \
             mock.patch.object(sysmod, "_gps_presence_status",
                               return_value={"device": None, "interface": None,
                                             "otherDetected": []}):
            return sysmod._gps_info()

    def test_no_fix_yet_reports_every_key_as_null(self):
        info = self._info(['{"class":"TPV","mode":0}',
                           '{"class":"SKY","nSat":9,"uSat":0}'])
        self.assertEqual(set(info), self._KEYS)
        self.assertEqual(info["mode"], 0)
        self.assertIsNone(info["lat"], "no position yet is null, not a missing key")
        self.assertIsNone(info["hdop"])
        self.assertEqual(info["seen"], 9)
        self.assertEqual(info["used"], 0)

    def test_full_fix_reports_the_same_key_set(self):
        info = self._info([
            '{"class":"TPV","mode":3,"lat":47.6,"lon":-122.3,"altMSL":58.2}',
            '{"class":"SKY","hdop":0.8,"nSat":12,"uSat":9}'])
        self.assertEqual(set(info), self._KEYS,
                         "a fixed receiver and an acquiring one must be the same shape")
        self.assertEqual(info["mode"], 3)
        self.assertEqual(info["lat"], 47.6)
        self.assertEqual(info["alt_m"], 58.2)
        self.assertEqual(info["hdop"], 0.8)

    def test_gpsd_unreachable_is_null_the_subsystem_is_absent(self):
        with mock.patch.object(sysmod.socket, "create_connection",
                               side_effect=OSError("refused")):
            self.assertIsNone(sysmod._gps_info())


class WifiShapeTest(unittest.TestCase):
    def test_ssid_without_client_count_still_reports_both_keys(self):
        """`iwgetid` answered but `iw` is absent. Omitting `clients` made "no
        stations associated" and "we couldn't ask" the same answer."""
        def fake_run(cmd, **kw):
            out = mock.Mock()
            if cmd[0] == "iwgetid":
                out.stdout, out.returncode = "OASIS\n", 0
                return out
            raise FileNotFoundError("iw")
        with mock.patch.object(sysmod.subprocess, "run", fake_run):
            info = sysmod._wifi_info()
        self.assertEqual(set(info), {"ssid", "clients"})
        self.assertEqual(info["ssid"], "OASIS")
        self.assertIsNone(info["clients"])

    def test_nothing_readable_is_null(self):
        with mock.patch.object(sysmod.subprocess, "run",
                               side_effect=FileNotFoundError):
            self.assertIsNone(sysmod._wifi_info())


if __name__ == "__main__":
    unittest.main()
