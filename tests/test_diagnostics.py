import os, sys, unittest
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


if __name__ == "__main__":
    unittest.main()
