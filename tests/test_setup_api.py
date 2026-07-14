import os
import sys
import time
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.join(os.path.dirname(_HERE), "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import app as app_module
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
    with mock.patch.object(app_module, "_setup_registry", return_value=reg):
        r = client.post("/api/setup/plan", json={"selectedFeatures": ["service-controls"]})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["orderedFeatures"] == ["server", "service-controls"]
    assert data["planId"].startswith("setup-plan-")


def test_setup_run_starts_job_and_job_endpoint_returns_status():
    client = app_module.app.test_client()
    reg = {
        "server": _ok_feature("server"),
        "service-controls": _ok_feature("service-controls", deps=["server"]),
    }
    with mock.patch.object(app_module, "_setup_registry", return_value=reg):
        p = client.post("/api/setup/plan", json={"selectedFeatures": ["service-controls"]}).get_json()
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
    with mock.patch.object(app_module, "_setup_registry", return_value=reg):
        p = client.post("/api/setup/plan", json={"selectedFeatures": ["server"]}).get_json()
        with app_module._setup_lock:
            app_module._setup_active_job = "setup-job-lock"
            app_module._setup_jobs["setup-job-lock"] = {
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
            with app_module._setup_lock:
                app_module._setup_active_job = None
                app_module._setup_jobs.pop("setup-job-lock", None)


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
    with mock.patch.object(app_module.HD_detect, "scan", return_value={"rtl_sdr": [], "serial": [], "alsa": []}):
        r = client.get("/api/setup/hardware-detect")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert "detected" in data
    assert "lsusb" in data


def test_setup_plan_blocks_winlink_without_password():
    client = app_module.app.test_client()
    with mock.patch.object(app_module, "has_internet", return_value=True):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "winlink"],
                "winlink": {"password": ""},
                "station": {},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
        )
    assert r.status_code == 200
    data = r.get_json()
    blocked = data["preflight"]["blocked"]
    codes = {b.get("reason_code") for b in blocked}
    assert "WINLINK_PASSWORD_REQUIRED" in codes


def test_setup_plan_blocks_internet_required_features_when_offline():
    client = app_module.app.test_client()
    with mock.patch.object(app_module, "has_internet", return_value=False):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "kiwix"],
                "station": {},
                "winlink": {"password": "x"},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
        )
    assert r.status_code == 200
    data = r.get_json()
    blocked = data["preflight"]["blocked"]
    assert any(b.get("feature") == "kiwix" and b.get("reason_code") == "INTERNET_REQUIRED" for b in blocked)


def test_setup_cancel_requests_active_job():
    client = app_module.app.test_client()
    reg = {"server": _ok_feature("server")}
    with mock.patch.object(app_module, "_setup_registry", return_value=reg):
        p = client.post("/api/setup/plan", json={"selectedFeatures": ["server"]}).get_json()
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
    with mock.patch.object(app_module, "_setup_registry", return_value=reg), \
         mock.patch.object(app_module, "_setup_write_station", side_effect=RuntimeError("disk full")):
        p = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server"],
                "station": {"callsign": "W4MHI"},
                "winlink": {"password": "x"},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
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
    with mock.patch.object(app_module, "has_internet", return_value=True):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server"],
                "station": {},
                "winlink": {"password": "x"},
                "wifi": {"mode": "client", "ssid": "FieldNet", "password": "password123"},
            },
        )
    assert r.status_code == 200
    data = r.get_json()
    assert data["orderedFeatures"][-1] == "wifi"
    assert data["reboot"]["requiredIfRunNow"] is True
    assert "wifi" in data["reboot"]["reasons"]


def test_setup_plan_blocks_linux_only_feature_on_non_linux():
    client = app_module.app.test_client()
    with mock.patch.object(app_module, "has_internet", return_value=True), \
         mock.patch.object(app_module.sys, "platform", "darwin"):
        r = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server", "kiwix"],
                "station": {},
                "winlink": {"password": "x"},
                "wifi": {"mode": "none", "ssid": "", "password": ""},
            },
        )
    assert r.status_code == 200
    data = r.get_json()
    blocked = data["preflight"]["blocked"]
    assert any(b.get("feature") == "kiwix" and b.get("reason_code") == "UNSUPPORTED_PLATFORM" for b in blocked)


def test_setup_run_includes_wifi_feature_state_when_selected():
    client = app_module.app.test_client()
    reg = {"server": _ok_feature("server")}
    with mock.patch.object(app_module, "_setup_registry", return_value=reg), \
         mock.patch.object(app_module, "_setup_apply_wifi", return_value={"ok": True, "requires_reboot": True}), \
         mock.patch.object(app_module, "has_internet", return_value=True):
        p = client.post(
            "/api/setup/plan",
            json={
                "selectedFeatures": ["server"],
                "station": {},
                "winlink": {"password": "x"},
                "wifi": {"mode": "client", "ssid": "FieldNet", "password": "password123"},
            },
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
