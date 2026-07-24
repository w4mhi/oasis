import os
import sys
import time
import json
import tempfile
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.join(os.path.dirname(_HERE), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import app as app_module
from routes import setup as setup_module
from common import setup_engine as SE


def _ok_feature(key, deps=None):
    return SE.FeatureSpec(
        key=key,
        dependencies=deps or [],
        install_fn=lambda: {"ok": True},
        verify_fn=lambda: {"ok": True},
        enable_policy="none",
    )


def test_setup_plan_resolves_dependency_order():
    client = app_module.app.test_client()
    reg = {
        "server": _ok_feature("server"),
        "service-controls": _ok_feature("service-controls", deps=["server"]),
    }
    with mock.patch.object(setup_module, "_setup_registry", return_value=reg):
        r = client.post(
            "/api/setup/plan",
            json={"selectedFeatures": ["service-controls"]},
            headers={"X-OASIS-Request": "1"},
        )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["orderedFeatures"] == ["server", "service-controls"]
    assert data["planId"].startswith("setup-plan-")


def test_setup_plan_requires_oasis_header():
    client = app_module.app.test_client()
    r = client.post("/api/setup/plan", json={"selectedFeatures": ["server"]})
    assert r.status_code == 403


def test_setup_run_starts_job_and_job_endpoint_returns_status():
    client = app_module.app.test_client()
    reg = {
        "server": _ok_feature("server"),
        "service-controls": _ok_feature("service-controls", deps=["server"]),
    }
    with mock.patch.object(setup_module, "_setup_registry", return_value=reg):
        p = client.post(
            "/api/setup/plan",
            json={"selectedFeatures": ["service-controls"]},
            headers={"X-OASIS-Request": "1"},
        ).get_json()
        rr = client.post("/api/setup/run", json={"planId": p["planId"]}, headers={"X-OASIS-Request": "1"})
        assert rr.status_code == 200
        data = rr.get_json()
        assert data["ok"] is True
        job_id = data["jobId"]

        # Poll briefly for completion in async thread.
        last = None
        for _ in range(20):
            last = client.get(f"/api/setup/jobs/{job_id}").get_json()
            if last["job"]["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)

        assert last["ok"] is True
        assert last["job"]["id"] == job_id
        assert len(last["features"]) == 2


def test_setup_run_rejects_when_active_job_locked():
    client = app_module.app.test_client()
    reg = {"server": _ok_feature("server")}
    with mock.patch.object(setup_module, "_setup_registry", return_value=reg):
        p = client.post(
            "/api/setup/plan",
            json={"selectedFeatures": ["server"]},
            headers={"X-OASIS-Request": "1"},
        ).get_json()
        with setup_module._setup_lock:
            setup_module._setup_active_job = "setup-job-lock"
            setup_module._setup_jobs["setup-job-lock"] = {
                "id": "setup-job-lock",
                "status": "running",
                "events": [],
                "featureStates": {},
                "orderedFeatures": [],
            }
        try:
            r = client.post("/api/setup/run", json={"planId": p["planId"]}, headers={"X-OASIS-Request": "1"})
            assert r.status_code == 409
            data = r.get_json()
            assert data["reasonCode"] == "JOB_LOCKED"
        finally:
            with setup_module._setup_lock:
                setup_module._setup_active_job = None
                setup_module._setup_jobs.pop("setup-job-lock", None)


def test_setup_permissions_endpoint_shape():
    client = app_module.app.test_client()
    r = client.get("/api/setup/permissions")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert "localCommand" in data
    assert "serviceControlsGranted" in data


def test_setup_hardware_detect_endpoint_shape():
    client = app_module.app.test_client()
    with mock.patch.object(setup_module.HD_detect, "scan", return_value={"rtl_sdr": [], "serial": [], "alsa": []}):
        r = client.get("/api/setup/hardware-detect")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert "detected" in data
    assert "lsusb" in data


def test_setup_run_forwards_winlink_credentials_from_payload_to_install_script():
    # _setup_run_job must thread the request payload into _setup_registry so the
    # winlink install_fn closure can build --callsign/--locator/--password from
    # what the user actually typed, instead of invoking services/winlink/install.py bare.
    # winlink is a privileged feature, so it normally runs via the installer
    # queue+worker instead of in-process — stand in for a worker that runs the
    # job the instant it's queued, so the args-building logic below runs
    # synchronously in this test the same way it always has.
    client = app_module.app.test_client()
    calls = []

    def fake_run_script(repo_root, script_rel, args=None, timeout=900):
        calls.append((script_rel, args))
        return {"ok": True}

    def fake_privileged_run(key, spec, payload=None, job_id=None):
        return spec.install_fn()

    with mock.patch.object(setup_module.SETUP_REGISTRY, "_setup_run_script", side_effect=fake_run_script), \
         mock.patch.object(setup_module.SETUP_REGISTRY, "_setup_server_install", return_value={"ok": True}), \
         mock.patch.object(setup_module, "_setup_enqueue_and_wait_install", side_effect=fake_privileged_run), \
         mock.patch.object(app_module.sys, "platform", "linux"), \
         mock.patch.object(setup_module, "has_internet", return_value=True):
        p = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "winlink"],
                "station": {},
                "winlink": {"callsign": "W4MHI", "password": "hunter2", "locator": "FM18"},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
            headers={"X-OASIS-Request": "1"},
        ).get_json()
        rr = client.post("/api/setup/run", json={"planId": p["planId"]}, headers={"X-OASIS-Request": "1"})
        assert rr.status_code == 200
        job_id = rr.get_json()["jobId"]

        last = None
        for _ in range(30):
            last = client.get(f"/api/setup/jobs/{job_id}").get_json()
            if last["job"]["status"] in ("completed", "failed", "canceled"):
                break
            time.sleep(0.05)

    assert last is not None
    assert last["job"]["status"] == "completed"
    winlink_calls = [c for c in calls if c[0] == "services/winlink/install.py"]
    assert len(winlink_calls) == 1
    _, args = winlink_calls[0]
    assert "--callsign" in args and "W4MHI" in args
    assert "--locator" in args and "FM18" in args
    assert "--password" in args and "hunter2" in args
    assert "--no-password" not in args


def test_setup_run_uses_no_password_flag_when_winlink_password_omitted():
    client = app_module.app.test_client()
    calls = []

    def fake_run_script(repo_root, script_rel, args=None, timeout=900):
        calls.append((script_rel, args))
        return {"ok": True}

    def fake_privileged_run(key, spec, payload=None, job_id=None):
        return spec.install_fn()

    # Bypass the WINLINK_PASSWORD_REQUIRED preflight blocker so the plan runs;
    # we're only exercising the arg-building for a payload with no password.
    with mock.patch.object(setup_module.SETUP_REGISTRY, "_setup_run_script", side_effect=fake_run_script), \
         mock.patch.object(setup_module.SETUP_REGISTRY, "_setup_server_install", return_value={"ok": True}), \
         mock.patch.object(setup_module, "_setup_enqueue_and_wait_install", side_effect=fake_privileged_run), \
         mock.patch.object(setup_module, "_setup_preflight_blockers", return_value=[]), \
         mock.patch.object(app_module.sys, "platform", "linux"), \
         mock.patch.object(setup_module, "has_internet", return_value=True):
        p = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "winlink"],
                "station": {},
                "winlink": {"callsign": "W4MHI"},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
            headers={"X-OASIS-Request": "1"},
        ).get_json()
        rr = client.post("/api/setup/run", json={"planId": p["planId"]}, headers={"X-OASIS-Request": "1"})
        job_id = rr.get_json()["jobId"]

        last = None
        for _ in range(30):
            last = client.get(f"/api/setup/jobs/{job_id}").get_json()
            if last["job"]["status"] in ("completed", "failed", "canceled"):
                break
            time.sleep(0.05)

    winlink_calls = [c for c in calls if c[0] == "services/winlink/install.py"]
    assert len(winlink_calls) == 1
    _, args = winlink_calls[0]
    assert "--no-password" in args
    assert "--password" not in args


def test_setup_privileged_install_fails_fast_when_installer_daemon_not_enabled():
    # Regression test: a privileged install used to silently queue a job and
    # poll for up to 15 minutes with zero log output before finally timing out
    # if the root installer daemon (scripts/enable-oasis-installer.py) was
    # never enabled. It must now fail immediately with a clear reason instead.
    reg = setup_module._setup_registry()
    spec = reg.get("webssh")
    with mock.patch.object(setup_module, "_installer_daemon_enabled", return_value=False):
        result = setup_module._setup_enqueue_and_wait_install("webssh", spec, {}, "job-1")
    assert result["ok"] is False
    assert result["reason_code"] == "INSTALLER_DAEMON_UNAVAILABLE"
    assert "enable-oasis-installer.py" in result["reason_text"]


def test_setup_reboot_invokes_resolved_absolute_path_not_bare_reboot():
    # sudo authorizes an exact command-path match (see the OASIS_REBOOT
    # Cmnd_Alias in scripts/enable-service-controls.py); the route must call
    # the same resolved absolute path, not PATH-resolved bare "reboot".
    client = app_module.app.test_client()
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch.object(app_module.sys, "platform", "linux"), \
         mock.patch.object(setup_module.shutil, "which", return_value=None), \
         mock.patch.object(setup_module.subprocess, "run", side_effect=fake_run):
        r = client.post("/api/setup/reboot", headers={"X-OASIS-Request": "1"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert captured["argv"] == ["sudo", "-n", "/sbin/reboot"]
    assert "reboot" not in captured["argv"]


def test_setup_plan_blocks_winlink_without_password():
    client = app_module.app.test_client()
    with mock.patch.object(setup_module, "has_internet", return_value=True):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "winlink"],
                "winlink": {"password": ""},
                "station": {},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
            headers={"X-OASIS-Request": "1"},
        )
    assert r.status_code == 200
    data = r.get_json()
    blocked = data["preflight"]["blocked"]
    codes = {b.get("reason_code") for b in blocked}
    assert "WINLINK_PASSWORD_REQUIRED" in codes


def test_setup_plan_blocks_internet_required_features_when_offline():
    client = app_module.app.test_client()
    with mock.patch.object(setup_module, "has_internet", return_value=False):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "kiwix"],
                "station": {},
                "winlink": {"password": "x"},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
            headers={"X-OASIS-Request": "1"},
        )
    assert r.status_code == 200
    data = r.get_json()
    blocked = data["preflight"]["blocked"]
    assert any(b.get("feature") == "kiwix" and b.get("reason_code") == "INTERNET_REQUIRED" for b in blocked)


def test_setup_cancel_requests_active_job():
    client = app_module.app.test_client()
    reg = {"server": _ok_feature("server")}
    with mock.patch.object(setup_module, "_setup_registry", return_value=reg):
        p = client.post(
            "/api/setup/plan",
            json={"selectedFeatures": ["server"]},
            headers={"X-OASIS-Request": "1"},
        ).get_json()
        rr = client.post("/api/setup/run", json={"planId": p["planId"]}, headers={"X-OASIS-Request": "1"})
        assert rr.status_code == 200
        job_id = rr.get_json()["jobId"]

        rc = client.post("/api/setup/cancel", json={"jobId": job_id}, headers={"X-OASIS-Request": "1"})
        assert rc.status_code == 200
        data = rc.get_json()
        assert data["ok"] is True
        assert data["jobId"] == job_id


def test_setup_run_fails_when_station_write_raises():
    client = app_module.app.test_client()
    reg = {"server": _ok_feature("server")}
    with mock.patch.object(setup_module, "_setup_registry", return_value=reg), \
         mock.patch.object(setup_module, "_setup_write_station", side_effect=RuntimeError("disk full")):
        p = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server"],
                "station": {"callsign": "W4MHI"},
                "winlink": {"password": "x"},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
            headers={"X-OASIS-Request": "1"},
        ).get_json()
        rr = client.post("/api/setup/run", json={"planId": p["planId"]}, headers={"X-OASIS-Request": "1"})
        assert rr.status_code == 200
        job_id = rr.get_json()["jobId"]

        last = None
        for _ in range(30):
            last = client.get(f"/api/setup/jobs/{job_id}").get_json()
            if last["job"]["status"] in ("completed", "failed", "canceled"):
                break
            time.sleep(0.05)

        assert last is not None
        assert last["job"]["status"] == "failed"


def test_setup_plan_appends_wifi_last_and_marks_reboot_required():
    client = app_module.app.test_client()
    with mock.patch.object(setup_module, "has_internet", return_value=True):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server"],
                "station": {},
                "winlink": {"password": "x"},
                "wifi": {"mode": "client", "ssid": "FieldNet", "password": "password123"},
            },
            headers={"X-OASIS-Request": "1"},
        )
    assert r.status_code == 200
    data = r.get_json()
    assert data["orderedFeatures"][-1] == "wifi"
    assert data["reboot"]["requiredIfRunNow"] is True
    assert "wifi" in data["reboot"]["reasons"]


def test_setup_plan_blocks_linux_only_feature_on_non_linux():
    client = app_module.app.test_client()
    with mock.patch.object(setup_module, "has_internet", return_value=True), \
         mock.patch.object(app_module.sys, "platform", "darwin"):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "kiwix"],
                "station": {},
                "winlink": {"password": "x"},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
            headers={"X-OASIS-Request": "1"},
        )
    assert r.status_code == 200
    data = r.get_json()
    blocked = data["preflight"]["blocked"]
    assert any(b.get("feature") == "kiwix" and b.get("reason_code") == "UNSUPPORTED_PLATFORM" for b in blocked)


def test_setup_run_includes_wifi_feature_state_when_selected():
    client = app_module.app.test_client()
    reg = {"server": _ok_feature("server")}
    with mock.patch.object(setup_module, "_setup_registry", return_value=reg), \
         mock.patch.object(setup_module, "_setup_apply_wifi", return_value={"ok": True, "requires_reboot": True}), \
         mock.patch.object(setup_module, "has_internet", return_value=True):
        p = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server"],
                "station": {},
                "winlink": {"password": "x"},
                "wifi": {"mode": "client", "ssid": "FieldNet", "password": "password123"},
            },
            headers={"X-OASIS-Request": "1"},
        ).get_json()
        rr = client.post("/api/setup/run", json={"planId": p["planId"]}, headers={"X-OASIS-Request": "1"})
        assert rr.status_code == 200
        job_id = rr.get_json()["jobId"]

        last = None
        for _ in range(30):
            last = client.get(f"/api/setup/jobs/{job_id}").get_json()
            if last["job"]["status"] in ("completed", "failed", "canceled"):
                break
            time.sleep(0.05)

        assert last is not None
        wifi = next((f for f in last["features"] if f.get("feature") == "wifi"), None)
        assert wifi is not None
        assert wifi.get("status") == "installed_needs_reboot"


def test_setup_run_script_classifies_internet_required_from_network_error():
    cp = mock.Mock()
    cp.returncode = 1
    cp.stderr = "ERROR: Could not reach the internet to fetch the FlightAware repo signing key."
    cp.stdout = ""
    with mock.patch.object(setup_module.SETUP_REGISTRY.os.path, "exists", return_value=True), \
         mock.patch.object(setup_module.SETUP_REGISTRY.subprocess, "run", return_value=cp):
        res = setup_module.SETUP_REGISTRY._setup_run_script(setup_module.SUITE_ROOT, "services/adsb/install.py")
    assert res["ok"] is False
    assert res["reason_code"] == "INTERNET_REQUIRED"


def test_setup_run_script_classifies_missing_tool():
    cp = mock.Mock()
    cp.returncode = 1
    cp.stderr = "bash: gpg: command not found"
    cp.stdout = ""
    with mock.patch.object(setup_module.SETUP_REGISTRY.os.path, "exists", return_value=True), \
         mock.patch.object(setup_module.SETUP_REGISTRY.subprocess, "run", return_value=cp):
        res = setup_module.SETUP_REGISTRY._setup_run_script(setup_module.SUITE_ROOT, "services/adsb/install.py")
    assert res["ok"] is False
    assert res["reason_code"] == "MISSING_TOOL"


def test_setup_plan_allows_winlink_without_new_password_when_pat_password_exists():
    client = app_module.app.test_client()
    with mock.patch.object(setup_module, "has_internet", return_value=True), \
         mock.patch.object(setup_module, "_setup_pat_password_set", return_value=True):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "winlink"],
                "winlink": {"password": ""},
                "station": {},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
            headers={"X-OASIS-Request": "1"},
        )
    assert r.status_code == 200
    data = r.get_json()
    codes = {b.get("reason_code") for b in data["preflight"]["blocked"]}
    assert "WINLINK_PASSWORD_REQUIRED" not in codes


def test_setup_write_station_preserves_existing_lat_lon_when_omitted():
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(setup_module, "SUITE_ROOT", td):
            cfg_dir = os.path.join(td, "configuration")
            os.makedirs(cfg_dir, exist_ok=True)
            st_path = os.path.join(cfg_dir, "station.json")
            with open(st_path, "w", encoding="utf-8") as fh:
                json.dump({"callsign": "W4MHI", "grid": "EM95", "lat": 35.91, "lon": -79.05}, fh)
                fh.write("\n")

            setup_module._setup_write_station({
                "station": {"callsign": "W4MHI", "grid": "EM96", "lat": None, "lon": None}
            })

            with open(st_path, "r", encoding="utf-8") as fh:
                body = json.load(fh)
            assert body["grid"] == "EM96"
            assert body["lat"] == 35.91
            assert body["lon"] == -79.05


import unittest


class SetupWriteStationAprsFreqTests(unittest.TestCase):
    # A TestCase (not a bare def) so `unittest discover` — the CI runner — actually
    # collects it. The APRS frequency is owned by its own Setup control
    # (/api/aprs/frequency), not the station form, so a station save must keep it.
    def test_preserves_aprs_freq(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(setup_module, "SUITE_ROOT", td):
                cfg_dir = os.path.join(td, "configuration")
                os.makedirs(cfg_dir, exist_ok=True)
                st_path = os.path.join(cfg_dir, "station.json")
                with open(st_path, "w", encoding="utf-8") as fh:
                    json.dump({"callsign": "W4MHI", "grid": "EM95", "aprs_freq": "144.800M"}, fh)
                    fh.write("\n")

                setup_module._setup_write_station({
                    "station": {"callsign": "W4MHI", "grid": "EM96"}
                })

                with open(st_path, "r", encoding="utf-8") as fh:
                    body = json.load(fh)
                self.assertEqual(body["grid"], "EM96")
                self.assertEqual(body["aprs_freq"], "144.800M")


def test_gps_module_exposes_run_helper_import():
    import importlib
    gps_mod = importlib.import_module("features.gps.gps")
    assert hasattr(gps_mod, "_run")


def _summary_with(features):
    # features: list of (key, status) -> object with a .features list of dicts,
    # matching what JobSummary exposes to _setup_record_installed_features.
    from types import SimpleNamespace
    return SimpleNamespace(features=[{"feature": k, "status": s} for k, s in features])


def _seed_manifest(tmp_path, obj):
    # The manifest now lives under <SUITE_ROOT>/configuration/ and is written by
    # common/installed_services.py; tests patch SUITE_ROOT and seed there.
    cfg = tmp_path / "configuration"
    cfg.mkdir(exist_ok=True)
    manifest = cfg / "installed-services.json"
    manifest.write_text(json.dumps(obj) + "\n")
    return manifest


def test_record_keeps_unticked_installed_feature_additive(tmp_path):
    manifest = _seed_manifest(tmp_path, {"features": ["fcc", "adsb"]})
    summary = _summary_with([("kiwix", SE.STATUS_INSTALLED)])
    with mock.patch.object(setup_module, "SUITE_ROOT", str(tmp_path)):
        setup_module._setup_record_installed_features(summary)
    got = set(json.loads(manifest.read_text())["features"])
    # fcc was NOT ticked this run and is a former GATE_AUTHORITATIVE member, but
    # additive recording must never drop it. kiwix is added.
    assert got == {"fcc", "adsb", "kiwix"}


def test_record_does_not_drop_former_gate_authoritative_on_untick(tmp_path):
    manifest = _seed_manifest(tmp_path, {"features": ["fcc", "repeaterbook", "forms"]})
    # A run that installs nothing new and ticks none of the content gates.
    summary = _summary_with([])
    with mock.patch.object(setup_module, "SUITE_ROOT", str(tmp_path)):
        setup_module._setup_record_installed_features(summary)
    got = set(json.loads(manifest.read_text())["features"])
    assert got == {"fcc", "repeaterbook", "forms"}


def test_record_adds_only_successful_installs(tmp_path):
    manifest = _seed_manifest(tmp_path, {"features": []})
    summary = _summary_with([
        ("kiwix", SE.STATUS_INSTALLED),
        ("adsb", SE.STATUS_INSTALL_FAILED),
    ])
    with mock.patch.object(setup_module, "SUITE_ROOT", str(tmp_path)):
        setup_module._setup_record_installed_features(summary)
    got = set(json.loads(manifest.read_text())["features"])
    assert got == {"kiwix"}


class EnqueueRemoveShapeTest(unittest.TestCase):
    # A TestCase so `unittest discover` (the CI runner) collects it. Verifies the
    # remove variant hands the shared enqueue-and-wait helper a job body carrying
    # action:"remove" for the right feature — without running the time/IO loop.
    def test_remove_job_body_has_action(self):
        captured = {}

        def _fake_wait(body, key, job_id=None):
            captured["body"] = body
            captured["key"] = key
            return {"ok": True}

        with mock.patch.object(setup_module, "_setup_enqueue_and_wait", _fake_wait):
            res = setup_module._setup_enqueue_and_wait_remove("kiwix", job_id=None)
        self.assertTrue(res["ok"])
        self.assertEqual(captured["key"], "kiwix")
        self.assertEqual(captured["body"]["feature"], "kiwix")
        self.assertEqual(captured["body"]["action"], "remove")

    def test_remove_rejects_unremovable(self):
        res = setup_module._setup_enqueue_and_wait_remove("server", job_id=None)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason_code"], "REMOVE_FAILED")


class RecordRemovedTest(unittest.TestCase):
    def test_removed_features_drop_from_manifest(self):
        import tempfile
        from common import installed_services, config_paths
        root = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(root), exist_ok=True)
        installed_services.write(root, {"kiwix", "graywolf"},
                                 {"kiwix": {"services": ["kiwix"]},
                                  "graywolf": {"services": ["graywolf"]}})
        with mock.patch.object(setup_module, "SUITE_ROOT", root):
            setup_module._setup_record_removed_features({"kiwix"})
        self.assertEqual(installed_services.installed_features(root), {"graywolf"})
        self.assertNotIn("kiwix", installed_services.removal_map(root))


class RunUninstallsTest(unittest.TestCase):
    def _seed(self):
        import tempfile
        from common import installed_services, config_paths
        root = tempfile.mkdtemp()
        os.makedirs(config_paths.config_dir(root), exist_ok=True)
        installed_services.write(root, {"kiwix"}, {"kiwix": {"services": ["kiwix"]}})
        return root

    def test_run_uninstalls_removes_on_success(self):
        from common import installed_services
        root = self._seed()
        with mock.patch.object(setup_module, "SUITE_ROOT", root), \
             mock.patch.object(setup_module, "_setup_enqueue_and_wait_remove",
                               lambda key, job_id=None: {"ok": True, "advisory": []}), \
             mock.patch.object(setup_module, "_setup_emit_event", lambda *a, **k: None):
            removed = setup_module._setup_run_uninstalls("job-1", ["kiwix"])
        self.assertEqual(removed, ["kiwix"])
        self.assertEqual(installed_services.installed_features(root), set())

    def test_run_uninstalls_keeps_feature_on_failure(self):
        from common import installed_services
        root = self._seed()
        with mock.patch.object(setup_module, "SUITE_ROOT", root), \
             mock.patch.object(setup_module, "_setup_enqueue_and_wait_remove",
                               lambda key, job_id=None: {"ok": False, "reason_text": "boom"}), \
             mock.patch.object(setup_module, "_setup_emit_event", lambda *a, **k: None):
            removed = setup_module._setup_run_uninstalls("job-1", ["kiwix"])
        self.assertEqual(removed, [])
        self.assertEqual(installed_services.installed_features(root), {"kiwix"})
