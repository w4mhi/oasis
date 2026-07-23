import json, os, sys, time, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import diagnostics as D
from scripts import doctor as DR

_CTX = {"host": "127.0.0.1", "port": 8083}

def _fake(id, group, status, capability, critical, badge="X", tier="v1"):
    chk = D.Check(id=id, group=group, label=id, capability=capability,
                  critical=critical, tier=tier,
                  fn=lambda ctx, s=status, b=badge, g=group, i=id:
                      D._result(i, g, i, s, b, "d",
                                breaks=("broken" if s == "fail" else None),
                                fix=("/system/setup.html" if s == "fail" else None)))
    return chk

class TestRollup(unittest.TestCase):
    def _run(self, checks):
        orig = D.REGISTRY
        D.REGISTRY = checks
        try:
            return D.run_all("127.0.0.1", 8083)
        finally:
            D.REGISTRY = orig

    def test_summary_counts(self):
        r = self._run([_fake("a","CORE","ok","ACCESS",True),
                       _fake("b","CORE","warn","ACCESS",False),
                       _fake("c","SYSTEM","fail","POWER",True)])
        self.assertEqual(r["summary"], {"fail":1,"warn":1,"ok":1})

    def test_capability_fail_needs_critical(self):
        # non-critical fail => capability warn, not fail
        r = self._run([_fake("a","HARDWARE","fail","APRS_RX",False),
                       _fake("b","SERVICES","ok","APRS_RX",True)])
        cap = next(c for c in r["capabilities"] if c["id"]=="APRS_RX")
        self.assertEqual(cap["status"], "warn")

    def test_capability_fail_on_critical(self):
        r = self._run([_fake("a","SERVICES","fail","APRS_RX",True)])
        cap = next(c for c in r["capabilities"] if c["id"]=="APRS_RX")
        self.assertEqual(cap["status"], "fail")

    def test_reference_never_blocks_but_can_warn(self):
        r = self._run([_fake("d","DATA","fail","REFERENCE",False)])
        cap = next(c for c in r["capabilities"] if c["id"]=="REFERENCE")
        self.assertIn(cap["status"], ("warn","fail"))  # its own tile may show it

    def test_fix_now_prefers_critical_then_group_order(self):
        r = self._run([_fake("late","DATA","fail","REFERENCE",False),
                       _fake("crit","CORE","fail","ACCESS",True)])
        self.assertEqual(r["fix_now"]["id"], "crit")

    def test_fix_now_none_when_all_pass(self):
        r = self._run([_fake("a","CORE","ok","ACCESS",True)])
        self.assertIsNone(r["fix_now"])

    def test_backlog_excluded_by_default(self):
        r = self._run([_fake("a","CORE","ok","ACCESS",True),
                       _fake("b","DATA","fail","REFERENCE",False,tier="backlog")])
        ids = [c["id"] for g in r["groups"] for c in g["checks"]]
        self.assertNotIn("b", ids)

class TestMigratedChecks(unittest.TestCase):
    """Per-check tests for the 11 checks moved from doctor.py.

    Each test stubs the signal source (_probe_port / _http_get / _svc_status /
    _which_binary / shutil.disk_usage / os.path) so results are deterministic
    and offline, then asserts the up/down -> status/badge mapping.
    """

    def _shape(self, r):
        self.assertTrue({"id", "group", "label", "status", "badge", "detail",
                          "breaks", "fix"} <= set(r))
        self.assertIn(r["status"], ("ok", "warn", "fail"))

    # -- server --
    def test_server_down_is_fail(self):
        with mock.patch.object(D, "_probe_port", return_value=False):
            r = D.check_server(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["badge"], "DOWN")
        self.assertIsNotNone(r["fix"])

    def test_server_up_gunicorn_is_ok(self):
        with mock.patch.object(D, "_probe_port", return_value=True), \
             mock.patch.object(D, "_http_get", return_value=(True, {"wsgi": "gunicorn", "wsgi_version": "20", "version": "1.0"})), \
             mock.patch.object(D, "_svc_status", return_value={"active": "active", "enabled": "enabled", "installed": True}):
            r = D.check_server(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "RUNNING")

    def test_server_up_dev_server_is_warn(self):
        with mock.patch.object(D, "_probe_port", return_value=True), \
             mock.patch.object(D, "_http_get", return_value=(True, {"wsgi": "werkzeug", "wsgi_version": "2", "version": "1.0"})), \
             mock.patch.object(D, "_svc_status", return_value={"active": "n/a", "enabled": "n/a", "installed": False}):
            r = D.check_server(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "DEV SERVER")

    # -- disk --
    def test_disk_critical_is_fail(self):
        usage = mock.Mock(total=10_000_000_000, free=500_000_000, used=9_500_000_000)
        with mock.patch.object(D.shutil, "disk_usage", return_value=usage):
            r = D.check_disk(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["badge"], "CRITICAL")

    def test_disk_low_is_warn(self):
        usage = mock.Mock(total=10_000_000_000, free=1_500_000_000, used=8_500_000_000)
        with mock.patch.object(D.shutil, "disk_usage", return_value=usage):
            r = D.check_disk(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "LOW")

    def test_disk_ok(self):
        usage = mock.Mock(total=10_000_000_000, free=8_000_000_000, used=2_000_000_000)
        with mock.patch.object(D.shutil, "disk_usage", return_value=usage):
            r = D.check_disk(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "OK")

    # -- fcc --
    def test_fcc_missing_is_fail(self):
        with mock.patch.object(D.os.path, "isfile", return_value=False):
            r = D.check_fcc(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["badge"], "NOT BUILT")

    def test_fcc_ready_is_ok(self):
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"a\nb\nc\n")):
            r = D.check_fcc(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "READY")

    # -- maps --
    def test_maps_no_tiles_is_warn(self):
        with mock.patch.object(D.os.path, "isdir", return_value=False), \
             mock.patch.object(D, "_probe_port", return_value=False):
            r = D.check_maps(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "N/A")

    def test_maps_local_tiles_is_ok(self):
        def isdir(path):
            return path == D.MAPS_DIR
        with mock.patch.object(D.os.path, "isdir", side_effect=isdir), \
             mock.patch.object(D.os, "listdir", return_value=["region.pmtiles"]), \
             mock.patch.object(D, "_probe_port", return_value=False):
            r = D.check_maps(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "READY")

    # -- graywolf --
    def test_graywolf_up_is_ok(self):
        with mock.patch.object(D, "_probe_port", return_value=True), \
             mock.patch.object(D, "_svc_status", return_value={"active": "active", "enabled": "enabled", "installed": True}):
            r = D.check_graywolf(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "UP")

    def test_graywolf_not_installed_is_warn(self):
        with mock.patch.object(D, "_probe_port", return_value=False), \
             mock.patch.object(D, "_svc_status", return_value={"active": "inactive", "enabled": "not-found", "installed": False}):
            r = D.check_graywolf(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "NOT INSTALLED")

    # -- graywolf_api --
    def test_graywolf_api_up_is_ok(self):
        with mock.patch.object(D, "_probe_port", return_value=True), \
             mock.patch.object(D, "_svc_status", return_value={"active": "active", "enabled": "enabled", "installed": True}), \
             mock.patch.object(D, "_http_get", return_value=(True, {"count": 3})):
            r = D.check_graywolf_api(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "UP")

    def test_graywolf_api_not_installed_is_warn(self):
        with mock.patch.object(D, "_probe_port", return_value=False), \
             mock.patch.object(D, "_svc_status", return_value={"active": "inactive", "enabled": "not-found", "installed": False}):
            r = D.check_graywolf_api(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "NOT INSTALLED")

    # -- pat --
    def test_pat_up_is_ok(self):
        with mock.patch.object(D, "_probe_port", return_value=True), \
             mock.patch.object(D, "_which_binary", return_value="/usr/bin/pat"), \
             mock.patch.object(D, "_svc_status", return_value={"active": "active", "enabled": "enabled", "installed": True}), \
             mock.patch.object(D.os.path, "isfile", return_value=False):
            r = D.check_pat(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "UP")

    def test_pat_not_installed_is_warn(self):
        with mock.patch.object(D, "_probe_port", return_value=False), \
             mock.patch.object(D, "_which_binary", return_value=None), \
             mock.patch.object(D, "_svc_status", return_value={"active": "inactive", "enabled": "not-found", "installed": False}):
            r = D.check_pat(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "NOT INSTALLED")

    # -- rtl_sdr --
    def test_rtl_sdr_not_installed_is_warn(self):
        with mock.patch.object(D, "_which_binary", return_value=None):
            r = D.check_rtl_sdr(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "NOT INSTALLED")

    def test_rtl_sdr_ready_is_ok(self):
        with mock.patch.object(D, "_which_binary", return_value="/usr/bin/tool"), \
             mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch.object(D, "_svc_status", return_value={"active": "active", "enabled": "enabled", "installed": True}):
            r = D.check_rtl_sdr(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "READY")

    # -- kiwix --
    def test_kiwix_up_is_ok(self):
        with mock.patch.object(D, "_probe_port", return_value=True), \
             mock.patch.object(D, "_svc_status", return_value={"active": "active", "enabled": "enabled", "installed": True}):
            r = D.check_kiwix(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "UP")

    def test_kiwix_not_installed_is_warn(self):
        with mock.patch.object(D, "_probe_port", return_value=False), \
             mock.patch.object(D, "_which_binary", return_value=None):
            r = D.check_kiwix(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "NOT INSTALLED")

    # -- webssh --
    def test_webssh_down_is_warn(self):
        with mock.patch.object(D, "_probe_port", return_value=False), \
             mock.patch.object(D, "_which_binary", return_value=None):
            r = D.check_webssh(_CTX)
        self.assertIn(r["status"], ("warn", "fail"))

    def test_webssh_up_is_ok(self):
        with mock.patch.object(D, "_probe_port", return_value=True), \
             mock.patch.object(D, "_svc_status", return_value={"active": "active", "enabled": "enabled", "installed": True}):
            r = D.check_webssh(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "UP")

    # -- winlink_forms --
    def test_winlink_forms_missing_all_is_fail(self):
        with mock.patch.object(D.os.path, "isfile", return_value=False):
            r = D.check_winlink_forms(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["badge"], "MISSING")

    def test_winlink_forms_all_present_is_ok(self):
        with mock.patch.object(D.os.path, "isfile", return_value=True):
            r = D.check_winlink_forms(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "READY")

    # -- rtl_sdr result shape (brief example) --
    def test_rtl_sdr_result_shape(self):
        r = D.check_rtl_sdr(_CTX)
        self.assertTrue(set(r) >= {"id", "group", "status", "badge", "detail"})


class TestRegistryAndKiwixCapability(unittest.TestCase):
    def test_all_11_checks_registered(self):
        ids = {c.id for c in D.REGISTRY}
        expected = {"server", "disk", "fcc", "maps", "graywolf", "graywolf_api",
                    "pat", "rtl_sdr", "kiwix", "webssh", "winlink_forms"}
        self.assertTrue(expected <= ids)

    def test_kiwix_capability_is_reference(self):
        chk = next(c for c in D.REGISTRY if c.id == "kiwix")
        self.assertEqual(chk.capability, "REFERENCE")

    def test_reference_members_use_kiwix_not_zim(self):
        members = D.CAPABILITIES["REFERENCE"]["members"]
        self.assertIn("kiwix", members)
        self.assertNotIn("zim", members)

    def test_winlink_forms_is_backlog_tier(self):
        chk = next(c for c in D.REGISTRY if c.id == "winlink_forms")
        self.assertEqual(chk.tier, "backlog")

    def test_run_all_includes_real_checks_when_host_unreachable(self):
        # Uses an unroutable host/port so every network-based check fails fast
        # and deterministically without touching the real system.
        r = D.run_all("127.0.0.1", 8083, include_backlog=True)
        ids = [c["id"] for g in r["groups"] for c in g["checks"]]
        self.assertIn("winlink_forms", ids)

    def test_run_all_excludes_backlog_by_default_for_real_registry(self):
        r = D.run_all("127.0.0.1", 8083)
        ids = [c["id"] for g in r["groups"] for c in g["checks"]]
        self.assertNotIn("winlink_forms", ids)


class TestNewCoreAndHardwareChecks(unittest.TestCase):
    """Per-check tests for Task 3's new checks (station_identity, digirig,
    dra_pi, gps, cooling_hat). Each stubs its signal source so
    results are deterministic and offline."""

    def _shape(self, r):
        self.assertTrue({"id", "group", "label", "status", "badge", "detail",
                          "breaks", "fix"} <= set(r))
        self.assertIn(r["status"], ("ok", "warn", "fail"))

    # -- station_identity --
    def test_station_identity_no_file_is_fail(self):
        with mock.patch.object(D.os.path, "isfile", return_value=False):
            r = D.check_station_identity(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["badge"], "UNSET")
        self.assertIsNotNone(r["fix"])

    def test_station_identity_n0call_placeholder_is_fail(self):
        # setup.py writes "N0CALL" when grid/lat/lon are set without a
        # callsign — must be treated as unset, not a real callsign.
        data = {"callsign": "N0CALL", "grid": "CN87", "lat": None, "lon": None}
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(data))):
            r = D.check_station_identity(_CTX)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["badge"], "UNSET")

    def test_station_identity_callsign_only_is_warn(self):
        data = {"callsign": "W4MHI", "grid": "", "lat": None, "lon": None}
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(data))):
            r = D.check_station_identity(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "PARTIAL")

    def test_station_identity_callsign_and_latlon_no_grid_is_ok(self):
        data = {"callsign": "W4MHI", "grid": "", "lat": 47.6, "lon": -122.3}
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(data))):
            r = D.check_station_identity(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "SET")

    def test_station_identity_full_is_ok(self):
        data = {"callsign": "w4mhi", "grid": "cn87", "lat": 47.6, "lon": -122.3}
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(data))):
            r = D.check_station_identity(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "SET")
        self.assertEqual(r["detail"], "W4MHI · CN87")

    def test_station_identity_corrupt_file_is_fail_not_raise(self):
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data="{not json")):
            r = D.check_station_identity(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "fail")

    # -- digirig --
    def test_digirig_present_is_ok(self):
        cands = [{"path": "/dev/serial/by-id/usb-Silicon_Labs_CP2102-if00-port0",
                  "label": "usb-Silicon_Labs_CP2102-if00-port0"}]
        with mock.patch.object(D.hardware_detect, "list_serial_by_id", return_value=cands), \
             mock.patch.object(D.hardware_detect, "digirig_candidates", return_value=cands):
            r = D.check_digirig(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "PRESENT")
        self.assertIn("CP2102", r["detail"])

    def test_digirig_absent_is_warn(self):
        with mock.patch.object(D.hardware_detect, "list_serial_by_id", return_value=[]), \
             mock.patch.object(D.hardware_detect, "digirig_candidates", return_value=[]):
            r = D.check_digirig(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "ABSENT")

    # -- dra_pi --
    def test_dra_pi_present_is_ok(self):
        inv = mock.Mock(devices={"dra-pi": {"id": "dra-pi", "kind": "dra-pi"}})
        with mock.patch.object(D.hardware, "load", return_value=inv):
            r = D.check_dra_pi(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "PRESENT")

    def test_dra_pi_absent_is_warn(self):
        inv = mock.Mock(devices={})
        with mock.patch.object(D.hardware, "load", return_value=inv):
            r = D.check_dra_pi(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "ABSENT")

    def test_dra_pi_inventory_error_is_warn_unknown_not_raise(self):
        with mock.patch.object(D.hardware, "load", side_effect=Exception("boom")):
            r = D.check_dra_pi(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "UNKNOWN")

    # -- gps --
    def test_gps_3d_fix_is_ok(self):
        with mock.patch.object(D, "_http_get",
                                return_value=(True, {"gps": {"mode": 3, "used": 9, "seen": 12}})):
            r = D.check_gps(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "3D")
        self.assertIn("9 sats", r["detail"])

    def test_gps_2d_fix_is_ok(self):
        with mock.patch.object(D, "_http_get",
                                return_value=(True, {"gps": {"mode": 2, "used": 4}})):
            r = D.check_gps(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "2D")

    def test_gps_mode0_no_fix_is_warn(self):
        with mock.patch.object(D, "_http_get",
                                return_value=(True, {"gps": {"mode": 0}})):
            r = D.check_gps(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "NO FIX")

    def test_gps_null_is_warn_off(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"gps": None})):
            r = D.check_gps(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "OFF")

    def test_gps_server_unreachable_is_warn_off_not_raise(self):
        with mock.patch.object(D, "_http_get", return_value=(False, None)):
            r = D.check_gps(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "OFF")

    # -- cooling_hat --
    def test_cooling_hat_up_is_ok(self):
        with mock.patch.object(D, "_svc_status",
                                return_value={"active": "active", "enabled": "enabled", "installed": True}):
            r = D.check_cooling_hat(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "UP")

    def test_cooling_hat_installed_not_running_is_warn(self):
        with mock.patch.object(D, "_svc_status",
                                return_value={"active": "inactive", "enabled": "enabled", "installed": True}):
            r = D.check_cooling_hat(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "OFF")

    def test_cooling_hat_not_installed_is_warn_na(self):
        with mock.patch.object(D, "_svc_status",
                                return_value={"active": "inactive", "enabled": "not-found", "installed": False}):
            r = D.check_cooling_hat(_CTX)
        self._shape(r)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "N/A")

    def test_cooling_hat_group_and_capability(self):
        chk = next(c for c in D.REGISTRY if c.id == "cooling_hat")
        self.assertEqual(chk.group, "SYSTEM")
        self.assertEqual(chk.capability, "POWER")
        self.assertFalse(chk.critical)


class TestNewChecksRegistered(unittest.TestCase):
    def test_all_new_checks_registered(self):
        ids = {c.id for c in D.REGISTRY}
        expected = {"station_identity", "digirig", "dra_pi", "gps", "cooling_hat"}
        self.assertTrue(expected <= ids)

    def test_station_identity_is_core_critical_access(self):
        chk = next(c for c in D.REGISTRY if c.id == "station_identity")
        self.assertEqual(chk.group, "CORE")
        self.assertEqual(chk.capability, "ACCESS")
        self.assertTrue(chk.critical)

    def test_digirig_and_dra_pi_are_hardware_aprs_rx_not_critical(self):
        for cid in ("digirig", "dra_pi"):
            chk = next(c for c in D.REGISTRY if c.id == cid)
            self.assertEqual(chk.group, "HARDWARE")
            self.assertEqual(chk.capability, "APRS_RX")
            self.assertFalse(chk.critical)

    def test_gps_is_hardware_critical_position(self):
        chk = next(c for c in D.REGISTRY if c.id == "gps")
        self.assertEqual(chk.group, "HARDWARE")
        self.assertEqual(chk.capability, "POSITION")
        self.assertTrue(chk.critical)

    def test_run_all_real_registry_includes_new_checks(self):
        r = D.run_all("127.0.0.1", 8083)
        ids = [c["id"] for g in r["groups"] for c in g["checks"]]
        for cid in ("station_identity", "digirig", "dra_pi", "gps", "cooling_hat"):
            self.assertIn(cid, ids)


class TestDoctorExitGate(unittest.TestCase):
    """doctor.py's exit-code contract: nonzero iff a *critical* check failed
    anywhere in the full sweep, independent of the CORE display group."""

    def _payload(self, results):
        groups = {}
        for r in results:
            groups.setdefault(r["group"], []).append(r)
        return {"groups": [{"name": name, "checks": checks} for name, checks in groups.items()]}

    def test_critical_fail_outside_core_is_flagged(self):
        # rtl_sdr is HARDWARE-group and critical -- not in CORE at all.
        payload = self._payload([
            D._result("server", "CORE", "OASIS Server", "ok", "RUNNING", "d"),
            D._result("rtl_sdr", "HARDWARE", "RTL-SDR", "fail", "ERROR", "d"),
        ])
        critical_by_id = {"server": True, "rtl_sdr": True}
        self.assertEqual(DR._critical_fail_ids(payload, critical_by_id), ["rtl_sdr"])

    def test_non_critical_fail_does_not_flag(self):
        payload = self._payload([
            D._result("kiwix", "DATA", "Kiwix", "fail", "ERROR", "d"),
        ])
        critical_by_id = {"kiwix": False}
        self.assertEqual(DR._critical_fail_ids(payload, critical_by_id), [])

    def test_all_ok_or_warn_is_zero_fails(self):
        payload = self._payload([
            D._result("server", "CORE", "OASIS Server", "ok", "RUNNING", "d"),
            D._result("pat", "SERVICES", "Winlink", "warn", "STOPPED", "d"),
        ])
        critical_by_id = {"server": True, "pat": True}
        self.assertEqual(DR._critical_fail_ids(payload, critical_by_id), [])


class TestDataAge(unittest.TestCase):
    """_data_age(path) -> human freshness string | None. Never raises."""

    def test_missing_path_is_none(self):
        self.assertIsNone(D._data_age("/nonexistent/path/for/sure/repeaterbook.csv"))

    def test_recent_file_is_minutes_old(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            age = D._data_age(f.name)
        self.assertIsNotNone(age)
        self.assertTrue(age.endswith("m old") or age.endswith("h old"), age)

    def test_old_file_is_days_old(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        old_ts = time.time() - 41 * 86400
        os.utime(path, (old_ts, old_ts))
        try:
            age = D._data_age(path)
        finally:
            os.remove(path)
        self.assertEqual(age, "41 days old")

    def test_singular_day(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        old_ts = time.time() - 1.5 * 86400
        os.utime(path, (old_ts, old_ts))
        try:
            age = D._data_age(path)
        finally:
            os.remove(path)
        self.assertEqual(age, "1 day old")


class TestKiwixZimAge(unittest.TestCase):
    """_kiwix_zim_age() -> freshness string of the newest .zim in
    KIWIX_ZIM_DIR | None. Exercises the real filesystem (tempfile + os.utime)
    rather than mocking the function's own internals out."""

    def test_newest_zim_age_from_real_tempdir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            old_path = os.path.join(d, "old.zim")
            new_path = os.path.join(d, "new.zim")
            open(old_path, "w").close()
            open(new_path, "w").close()
            old_ts = time.time() - 41 * 86400
            new_ts = time.time() - 2 * 3600
            os.utime(old_path, (old_ts, old_ts))
            os.utime(new_path, (new_ts, new_ts))
            with mock.patch.object(D, "KIWIX_ZIM_DIR", d):
                age = D._kiwix_zim_age()
        # Must pick the *newest* .zim (new.zim, ~2h old), not old.zim.
        self.assertEqual(age, "2h old")

    def test_non_zim_files_are_ignored(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "readme.txt"), "w").close()
            with mock.patch.object(D, "KIWIX_ZIM_DIR", d):
                age = D._kiwix_zim_age()
        self.assertIsNone(age)

    def test_empty_dir_is_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(D, "KIWIX_ZIM_DIR", d):
                age = D._kiwix_zim_age()
        self.assertIsNone(age)

    def test_missing_dir_is_none(self):
        with mock.patch.object(D, "KIWIX_ZIM_DIR", "/nonexistent/for/sure/zim/dir"):
            age = D._kiwix_zim_age()
        self.assertIsNone(age)


class TestParseTs(unittest.TestCase):
    """_parse_ts(val) -> aware UTC datetime | None. Never raises.

    GrayWolf's *actual* last_heard format is 'YYYY-MM-DD HH:MM:SS.fffffffff
    ±HH:MM' -- a space separator, nanosecond (9-digit) fractional precision,
    and a UTC offset -- not the simplified 'YYYY-MM-DD HH:MM:SS' text a naive
    reading of services/aprs/common/aprs.py's schema might suggest. If this
    doesn't parse, aprs_feed can never report LIVE off real GrayWolf data.
    """

    def test_graywolf_nanosecond_tz_format_parses(self):
        dt = D._parse_ts("2023-11-14 22:15:00.123456789+00:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo.utcoffset(dt), D.datetime.timedelta(0))
        self.assertEqual(
            dt.replace(tzinfo=None),
            D.datetime.datetime(2023, 11, 14, 22, 15, 0, 123456),
        )

    def test_graywolf_format_with_nonzero_offset_parses(self):
        dt = D._parse_ts("2023-11-14 22:15:00.123456789-05:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo.utcoffset(dt), -D.datetime.timedelta(hours=5))

    def test_epoch_number_parses(self):
        dt = D._parse_ts(1700000000)
        self.assertIsNotNone(dt)
        self.assertEqual(dt, D.datetime.datetime.fromtimestamp(1700000000, tz=D.datetime.timezone.utc))

    def test_epoch_float_parses(self):
        dt = D._parse_ts(1700000000.5)
        self.assertIsNotNone(dt)

    def test_epoch_numeric_string_parses(self):
        dt = D._parse_ts("1700000000")
        self.assertIsNotNone(dt)
        self.assertEqual(dt, D.datetime.datetime.fromtimestamp(1700000000, tz=D.datetime.timezone.utc))

    def test_plain_iso_t_separator_with_z_parses(self):
        dt = D._parse_ts("2023-11-14T22:15:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo.utcoffset(dt), D.datetime.timedelta(0))

    def test_plain_iso_with_offset_parses(self):
        dt = D._parse_ts("2023-11-14T22:15:00+02:00")
        self.assertIsNotNone(dt)

    def test_naive_space_separated_treated_as_utc(self):
        dt = D._parse_ts("2023-11-14 22:15:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo.utcoffset(dt), D.datetime.timedelta(0))

    def test_unparseable_returns_none(self):
        self.assertIsNone(D._parse_ts("not-a-timestamp"))
        self.assertIsNone(D._parse_ts("2023-13-99 99:99:99"))

    def test_none_returns_none(self):
        self.assertIsNone(D._parse_ts(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(D._parse_ts(""))

    def test_non_str_non_numeric_returns_none(self):
        self.assertIsNone(D._parse_ts(["not", "a", "timestamp"]))


class TestAprsFeedRealGraywolfFormat(unittest.TestCase):
    """aprs_feed computed off GrayWolf's real nanosecond+tz last_heard shape
    (not the simplified 'YYYY-MM-DD HH:MM:SS' text other tests use)."""

    def _stations(self, last_heard_list):
        return {"ok": True, "count": len(last_heard_list),
                "stations": [{"callsign": f"W{i}", "last_heard": lh}
                             for i, lh in enumerate(last_heard_list)]}

    def _graywolf_ts(self, when):
        return when.strftime("%Y-%m-%d %H:%M:%S.000000000+00:00")

    def test_recent_graywolf_timestamp_is_live(self):
        now = D.datetime.datetime.now(D.datetime.timezone.utc)
        recent = self._graywolf_ts(now - D.datetime.timedelta(seconds=10))
        with mock.patch.object(D, "_http_get", return_value=(True, self._stations([recent]))), \
             mock.patch.object(D, "_aprs_feed_freq", return_value=None):
            r = D.check_aprs_feed(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "LIVE")

    def test_hour_old_graywolf_timestamp_is_idle(self):
        now = D.datetime.datetime.now(D.datetime.timezone.utc)
        stale = self._graywolf_ts(now - D.datetime.timedelta(hours=1))
        with mock.patch.object(D, "_http_get", return_value=(True, self._stations([stale]))), \
             mock.patch.object(D, "_aprs_feed_freq", return_value=None):
            r = D.check_aprs_feed(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "IDLE")


class TestAprsFeedCheck(unittest.TestCase):
    """aprs_feed: SERVICES/APRS_RX, critical. Signal: /api/aprs/stations'
    newest last_heard -> decode age; tuned freq best-effort from the running
    aprs-sdr-feed unit."""

    def _stations(self, last_heard_list):
        return {"ok": True, "count": len(last_heard_list),
                "stations": [{"callsign": f"W{i}", "last_heard": lh}
                             for i, lh in enumerate(last_heard_list)]}

    def test_recent_packet_is_live(self):
        now = D.datetime.datetime.now(D.datetime.timezone.utc)
        recent = (now - D.datetime.timedelta(seconds=8)).strftime("%Y-%m-%d %H:%M:%S")
        with mock.patch.object(D, "_http_get", return_value=(True, self._stations([recent]))), \
             mock.patch.object(D, "_aprs_feed_freq", return_value="144.390M"):
            r = D.check_aprs_feed(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "LIVE")
        self.assertIn("144.390 MHz", r["detail"])

    def test_stale_packet_is_idle(self):
        now = D.datetime.datetime.now(D.datetime.timezone.utc)
        stale = (now - D.datetime.timedelta(seconds=D.APRS_RECENT_SECONDS + 300)).strftime("%Y-%m-%d %H:%M:%S")
        with mock.patch.object(D, "_http_get", return_value=(True, self._stations([stale]))), \
             mock.patch.object(D, "_aprs_feed_freq", return_value=None):
            r = D.check_aprs_feed(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "IDLE")

    def test_no_stations_is_idle(self):
        with mock.patch.object(D, "_http_get", return_value=(True, self._stations([]))), \
             mock.patch.object(D, "_aprs_feed_freq", return_value=None):
            r = D.check_aprs_feed(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "IDLE")

    def test_unreachable_is_down_fail(self):
        with mock.patch.object(D, "_http_get", return_value=(False, None)), \
             mock.patch.object(D, "_aprs_feed_freq", return_value=None):
            r = D.check_aprs_feed(_CTX)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["badge"], "DOWN")
        self.assertIsNotNone(r["breaks"])

    def test_freq_parsed_from_systemctl_cat(self):
        unit_text = (
            "[Service]\n"
            "ExecStart=/bin/sh -c 'rtl_fm -f 144.390M -M fm -s 48000 -g 28 -p 0 - "
            "| socat -u -b 512 - UDP-SENDTO:127.0.0.1:9123'\n"
        )
        completed = mock.Mock(returncode=0, stdout=unit_text)
        with mock.patch.object(D.sys, "platform", "linux"), \
             mock.patch.object(D.subprocess, "run", return_value=completed):
            freq = D._aprs_feed_freq()
        self.assertEqual(freq, "144.390M")

    def test_freq_none_on_non_linux(self):
        with mock.patch.object(D.sys, "platform", "darwin"):
            self.assertIsNone(D._aprs_feed_freq())

    def test_freq_none_when_systemctl_absent(self):
        with mock.patch.object(D.sys, "platform", "linux"), \
             mock.patch.object(D.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(D._aprs_feed_freq())

    def test_freq_none_when_unit_absent(self):
        with mock.patch.object(D.sys, "platform", "linux"), \
             mock.patch.object(D.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="")):
            self.assertIsNone(D._aprs_feed_freq())


class TestPowerTempCpuChecks(unittest.TestCase):
    """power/temp/cpu: SYSTEM/POWER, signal /api/system's throttle /
    cpu_temp_c / cpu_pct."""

    def _throttle(self, now=None, ever=None):
        z = {"under_voltage": False, "freq_capped": False, "throttled": False, "soft_temp": False}
        now = now or dict(z)
        ever = ever or dict(z)
        return {"raw": "0x0", "now": now, "ever": ever,
                "now_any": any(now.values()), "ever_any": any(ever.values())}

    # -- power --
    def test_power_now_undervoltage_is_fail(self):
        throttle = self._throttle(now={"under_voltage": True, "freq_capped": False,
                                        "throttled": False, "soft_temp": False})
        with mock.patch.object(D, "_http_get", return_value=(True, {"throttle": throttle})):
            r = D.check_power(_CTX)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["badge"], "UNDERVOLT")
        self.assertIsNotNone(r["breaks"])

    def test_power_now_throttled_is_fail(self):
        throttle = self._throttle(now={"under_voltage": False, "freq_capped": False,
                                        "throttled": True, "soft_temp": False})
        with mock.patch.object(D, "_http_get", return_value=(True, {"throttle": throttle})):
            r = D.check_power(_CTX)
        self.assertEqual(r["status"], "fail")

    def test_power_ever_only_is_warn(self):
        throttle = self._throttle(ever={"under_voltage": True, "freq_capped": False,
                                         "throttled": False, "soft_temp": False})
        with mock.patch.object(D, "_http_get", return_value=(True, {"throttle": throttle})):
            r = D.check_power(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "PAST")

    def test_power_clean_is_ok(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"throttle": self._throttle()})):
            r = D.check_power(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "OK")

    def test_power_none_throttle_is_warn_na(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"throttle": None})):
            r = D.check_power(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "N/A")

    def test_power_server_unreachable_is_warn_na_not_raise(self):
        with mock.patch.object(D, "_http_get", return_value=(False, None)):
            r = D.check_power(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "N/A")

    # -- temp --
    def test_temp_cool_is_ok(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_temp_c": 48})):
            r = D.check_temp(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertIn("48", r["detail"])

    def test_temp_warm_band_is_warn(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_temp_c": 75})):
            r = D.check_temp(_CTX)
        self.assertEqual(r["status"], "warn")

    def test_temp_boundary_70_is_warn(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_temp_c": 70})):
            r = D.check_temp(_CTX)
        self.assertEqual(r["status"], "warn")

    def test_temp_boundary_80_is_warn_not_fail(self):
        # check_temp fails on "> 80", not ">= 80" -- exactly 80 must stay warn.
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_temp_c": 80})):
            r = D.check_temp(_CTX)
        self.assertEqual(r["status"], "warn")

    def test_temp_85_is_fail(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_temp_c": 85})):
            r = D.check_temp(_CTX)
        self.assertEqual(r["status"], "fail")

    def test_temp_none_is_warn_na(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_temp_c": None})):
            r = D.check_temp(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "N/A")

    def test_temp_never_critical(self):
        chk = next(c for c in D.REGISTRY if c.id == "temp")
        self.assertFalse(chk.critical)

    # -- cpu --
    def test_cpu_low_is_ok(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_pct": 12})):
            r = D.check_cpu(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertIn("12", r["detail"])

    def test_cpu_high_is_warn(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_pct": 90})):
            r = D.check_cpu(_CTX)
        self.assertEqual(r["status"], "warn")

    def test_cpu_boundary_85_is_warn(self):
        # check_cpu warns on ">= 85" -- exactly 85 must already be warn.
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_pct": 85})):
            r = D.check_cpu(_CTX)
        self.assertEqual(r["status"], "warn")

    def test_cpu_boundary_just_under_85_is_ok(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_pct": 84.9})):
            r = D.check_cpu(_CTX)
        self.assertEqual(r["status"], "ok")

    def test_cpu_none_is_warn_na(self):
        with mock.patch.object(D, "_http_get", return_value=(True, {"cpu_pct": None})):
            r = D.check_cpu(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "N/A")

    def test_cpu_never_critical(self):
        chk = next(c for c in D.REGISTRY if c.id == "cpu")
        self.assertFalse(chk.critical)


class TestRepeaterbookAndFormsChecks(unittest.TestCase):
    def test_repeaterbook_missing_is_warn(self):
        with mock.patch.object(D.os.path, "isfile", return_value=False):
            r = D.check_repeaterbook(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "MISSING")

    def test_repeaterbook_present_is_ok_with_age(self):
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch.object(D, "_data_age", return_value="41 days old"):
            r = D.check_repeaterbook(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "READY")
        self.assertIn("41 days old", r["detail"])

    def test_repeaterbook_never_critical(self):
        chk = next(c for c in D.REGISTRY if c.id == "repeaterbook")
        self.assertFalse(chk.critical)

    def test_forms_all_missing_is_warn(self):
        with mock.patch.object(D.os.path, "isfile", return_value=False):
            r = D.check_forms(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "MISSING")

    def test_forms_all_present_is_ok(self):
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch.object(D.os.path, "getmtime", return_value=time.time()):
            r = D.check_forms(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "READY")

    def test_forms_never_critical(self):
        chk = next(c for c in D.REGISTRY if c.id == "forms")
        self.assertFalse(chk.critical)

    def test_forms_two_of_four_present_is_warn_partial(self):
        # ics-205 and ics-214 present, ics-213 and ics-309 missing.
        present_slugs = ("ics-205", "ics-214")

        def isfile(path):
            return any(f"{os.sep}{slug}{os.sep}" in path for slug in present_slugs)

        with mock.patch.object(D.os.path, "isfile", side_effect=isfile), \
             mock.patch.object(D.os.path, "getmtime", return_value=time.time()):
            r = D.check_forms(_CTX)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["badge"], "PARTIAL")
        self.assertIn("2/4", r["detail"])
        self.assertIn("ICS 213", r["detail"])
        self.assertIn("ICS 309", r["detail"])

    def test_forms_getmtime_vanished_file_degrades_gracefully(self):
        # File passes the isfile() check but is gone by the time getmtime()
        # runs (race) -- must not raise, should just drop the age suffix.
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch.object(D.os.path, "getmtime", side_effect=OSError("gone")):
            r = D.check_forms(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["badge"], "READY")
        self.assertNotIn(" old", r["detail"])


class TestTask4ChecksRegistered(unittest.TestCase):
    def test_all_6_registered(self):
        ids = {c.id for c in D.REGISTRY}
        expected = {"aprs_feed", "power", "temp", "cpu", "repeaterbook", "forms"}
        self.assertTrue(expected <= ids)

    def test_metadata_matches_spec_table(self):
        expect = {
            "aprs_feed":    ("SERVICES", "APRS_RX",  True),
            "power":        ("SYSTEM",   "POWER",    True),
            "temp":         ("SYSTEM",   "POWER",    False),
            "cpu":          ("SYSTEM",   "POWER",    False),
            "repeaterbook": ("DATA",     "REFERENCE", False),
            "forms":        ("DATA",     "REFERENCE", False),
        }
        for cid, (group, cap, crit) in expect.items():
            chk = next(c for c in D.REGISTRY if c.id == cid)
            self.assertEqual(chk.group, group, cid)
            self.assertEqual(chk.capability, cap, cid)
            self.assertEqual(chk.critical, crit, cid)
            self.assertEqual(chk.tier, "v1", cid)

    def test_run_all_real_registry_includes_new_checks(self):
        r = D.run_all("127.0.0.1", 8083)
        ids = [c["id"] for g in r["groups"] for c in g["checks"]]
        for cid in ("aprs_feed", "power", "temp", "cpu", "repeaterbook", "forms"):
            self.assertIn(cid, ids)


class TestDataAgeEnrichment(unittest.TestCase):
    """fcc/kiwix detail lines get a data-age suffix; status logic unchanged."""

    def test_fcc_detail_includes_age_when_available(self):
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"a\nb\n")), \
             mock.patch.object(D, "_data_age", return_value="3h old"):
            r = D.check_fcc(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertIn("3h old", r["detail"])

    def test_fcc_detail_omits_age_when_unavailable(self):
        with mock.patch.object(D.os.path, "isfile", return_value=True), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"a\n")), \
             mock.patch.object(D, "_data_age", return_value=None):
            r = D.check_fcc(_CTX)
        self.assertEqual(r["status"], "ok")

    def test_kiwix_up_detail_includes_zim_age_when_available(self):
        with mock.patch.object(D, "_probe_port", return_value=True), \
             mock.patch.object(D, "_svc_status",
                               return_value={"active": "active", "enabled": "enabled", "installed": True}), \
             mock.patch.object(D, "_kiwix_zim_age", return_value="12 days old"):
            r = D.check_kiwix(_CTX)
        self.assertEqual(r["status"], "ok")
        self.assertIn("12 days old", r["detail"])


if __name__ == "__main__":
    unittest.main()
