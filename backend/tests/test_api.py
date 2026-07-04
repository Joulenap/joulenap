"""Milestone 4 REST routers: status, config, guests, scheduler, jobs, wol, logs."""

from __future__ import annotations

import time

import pytest
from fakes import FakePve, UnreachablePve, make_deps
from fastapi.testclient import TestClient

from app.config import load_config
from app.connectors.pve import Guest
from app.jobs import AlreadyRunningError, JobService
from app.main import create_app


@pytest.fixture
def app_ctx(temp_config, temp_db, monkeypatch):
    """Authenticated TestClient + app. PBS reachability is stubbed off by default so
    status/cycle tests don't touch the network; inject fake job deps per test."""
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"username": "admin", "password": "secret"})
        yield client, app


def _inject(app, **deps_kwargs):
    """Swap app.state.job_service for one wired to in-memory connector fakes."""
    deps, pve, pbs, power = make_deps(**deps_kwargs)
    app.state.job_service = JobService(app.state.config_store, deps=deps)
    return pve, pbs, power


def _wait_run(client, run_id, *, timeout=5.0):
    """Poll a run until it leaves RUNNING (background thread), return its final body."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


# --- auth guard --------------------------------------------------------------


def test_protected_endpoints_require_auth(temp_config, temp_db):
    with TestClient(create_app()) as client:
        for path in ("/api/status", "/api/config", "/api/guests", "/api/runs"):
            assert client.get(path).status_code == 401
        assert client.post("/api/backup/run").status_code == 401


# --- status ------------------------------------------------------------------


def test_status_shape(app_ctx):
    client, _app = app_ctx
    body = client.get("/api/status").json()
    assert body["scheduler_enabled"] is True  # example config enables backups
    assert body["job_running"] is False
    assert body["pbs_online"] is False
    assert body["last_run"] is None
    assert "next_run" in body and "schedule" in body


# --- config ------------------------------------------------------------------


def test_config_get_redacts_secrets(app_ctx):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    assert cfg["pve"]["api_token_secret"] == "***REDACTED***"
    assert cfg["app"]["secret_key"] == "***REDACTED***"


def test_config_put_preserves_redacted_secrets_and_rearms(app_ctx, temp_config):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["backup"]["schedule"] = "30 2 * * *"  # change a non-secret; secrets stay REDACTED

    r = client.put("/api/config", json=cfg)
    assert r.status_code == 200

    on_disk = load_config(temp_config)
    assert on_disk.backup.schedule == "30 2 * * *"
    # The real secret survived the round-trip rather than being overwritten with REDACTED.
    assert on_disk.pve.api_token_secret == "test-pve-secret"
    assert on_disk.app.secret_key not in ("", "***REDACTED***")


def test_config_put_sets_new_secret(app_ctx, temp_config):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["pve"]["api_token_secret"] = "brand-new-secret"

    assert client.put("/api/config", json=cfg).status_code == 200
    assert load_config(temp_config).pve.api_token_secret == "brand-new-secret"


def test_config_put_rejects_invalid(app_ctx):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["backup"]["mode"] = "not-a-mode"
    assert client.put("/api/config", json=cfg).status_code == 422


def test_config_put_partial_body_preserves_secrets(app_ctx, temp_config):
    client, _app = app_ctx
    before = load_config(temp_config)
    # A partial body (only backup.schedule) must not reset anything else.
    r = client.put("/api/config", json={"backup": {"schedule": "15 5 * * *"}})
    assert r.status_code == 200

    after = load_config(temp_config)
    assert after.backup.schedule == "15 5 * * *"
    assert after.backup.mode == before.backup.mode              # untouched within section
    assert after.pve.api_token_secret == "test-pve-secret"      # secret survived
    assert after.app.secret_key == before.app.secret_key
    assert after.app.auth.password_hash == before.app.auth.password_hash
    assert after.app.auth.username == before.app.auth.username


def test_config_put_ignores_client_managed_secrets(app_ctx, temp_config):
    client, _app = app_ctx
    before = load_config(temp_config)
    cfg = client.get("/api/config").json()
    cfg["app"]["secret_key"] = "attacker-known-key"
    cfg["app"]["auth"]["password_hash"] = ""   # attempt to reset to first-run
    assert client.put("/api/config", json=cfg).status_code == 200

    after = load_config(temp_config)
    assert after.app.secret_key == before.app.secret_key
    assert after.app.auth.password_hash == before.app.auth.password_hash
    # Not dropped back to open first-run setup.
    assert client.get("/api/auth/status").json()["setup_needed"] is False


def test_config_put_custom_urls_replace_keep_and_mixed(app_ctx, temp_config):
    client, _app = app_ctx
    cfg = client.get("/api/config").json()
    cfg["notifications"]["custom_urls"] = ["gotify://h/t1", "gotify://h/t2"]
    assert client.put("/api/config", json=cfg).status_code == 200
    assert load_config(temp_config).notifications.custom_urls == ["gotify://h/t1", "gotify://h/t2"]

    # GET masks them; echoing the all-sentinel list back keeps the stored URLs.
    masked = client.get("/api/config").json()
    assert masked["notifications"]["custom_urls"] == ["***REDACTED***", "***REDACTED***"]
    assert client.put("/api/config", json=masked).status_code == 200
    assert load_config(temp_config).notifications.custom_urls == ["gotify://h/t1", "gotify://h/t2"]

    # A mixed sentinel/real list is rejected rather than silently dropping an entry.
    mixed = client.get("/api/config").json()
    mixed["notifications"]["custom_urls"] = ["***REDACTED***", "gotify://h/t3"]
    assert client.put("/api/config", json=mixed).status_code == 422


# --- scheduler toggle --------------------------------------------------------


def test_scheduler_toggle_off(app_ctx, temp_config):
    client, _app = app_ctx
    body = client.post("/api/scheduler/toggle", json={"enabled": False}).json()
    assert body["enabled"] is False
    assert body["next_run"] is None
    assert load_config(temp_config).backup.enabled is False


# --- guests ------------------------------------------------------------------


def test_guests_lists_from_pve(app_ctx):
    client, app = app_ctx
    _inject(
        app,
        pve=FakePve(
            guests=[Guest(vmid=100, name="ct", type="lxc", status="running")]
        ),
    )
    guests = client.get("/api/guests").json()
    assert guests == [
        {"vmid": 100, "name": "ct", "type": "lxc", "status": "running", "last_backup": None}
    ]


def test_guests_include_cached_last_backup(app_ctx):
    from datetime import UTC, datetime

    from app.db import session_scope
    from app.db.guest_backups import upsert_last_backups

    client, app = app_ctx
    _inject(
        app,
        pve=FakePve(guests=[Guest(vmid=100, name="ct", type="lxc", status="running")]),
    )
    epoch = 1_700_000_000
    with session_scope() as session:
        upsert_last_backups(session, {100: epoch})

    guests = client.get("/api/guests").json()
    assert guests[0]["last_backup"] is not None
    # Served as UTC-aware (with an offset) so the frontend converts it to local time.
    assert datetime.fromisoformat(guests[0]["last_backup"]) == datetime.fromtimestamp(
        epoch, tz=UTC
    )


def test_guests_pve_unreachable_returns_502(app_ctx):
    client, app = app_ctx
    deps, _pve, _pbs, _power = make_deps()
    deps.build_pve = lambda _c: UnreachablePve()
    app.state.job_service = JobService(app.state.config_store, deps=deps)
    assert client.get("/api/guests").status_code == 502


# --- wol test ----------------------------------------------------------------


def test_wol_test_sends(app_ctx):
    client, app = app_ctx
    calls: list[int] = []
    _inject(app, wol=lambda _c: calls.append(1))
    r = client.post("/api/wol/test")
    assert r.status_code == 200 and r.json()["sent"] is True
    assert calls == [1]


def test_wol_test_no_mac_returns_400(app_ctx):
    client, app = app_ctx
    app.state.config_store.update(lambda cfg: setattr(cfg.pbs, "mac", ""))
    assert client.post("/api/wol/test").status_code == 400


# --- backup / gc run ---------------------------------------------------------


def test_backup_run_records_and_completes(app_ctx):
    client, app = app_ctx
    _inject(app, reachable=True)  # fakes -> cycle succeeds quickly

    r = client.post("/api/backup/run")
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    final = _wait_run(client, run_id)
    assert final["kind"] == "cycle"
    assert final["status"] == "success"
    assert any(s["name"] == "poweroff" for s in final["steps"])
    # Shows up in history and produced log lines.
    assert run_id in [r["id"] for r in client.get("/api/runs").json()]
    assert len(client.get("/api/logs").json()) > 0


def test_gc_run_records(app_ctx):
    client, app = app_ctx
    _inject(app)
    run_id = client.post("/api/gc/run").json()["run_id"]
    assert _wait_run(client, run_id)["kind"] == "gc"


def test_backup_run_conflict_when_busy(app_ctx):
    client, app = app_ctx

    def busy(_trigger):
        raise AlreadyRunningError("already running")

    app.state.job_service.submit_backup = busy
    assert client.post("/api/backup/run").status_code == 409


# --- runs / logs -------------------------------------------------------------


def test_run_not_found(app_ctx):
    client, _app = app_ctx
    assert client.get("/api/runs/999999").status_code == 404


def test_tasklog_empty_when_nothing_ran(app_ctx):
    client, _app = app_ctx
    assert client.get("/api/tasklog").json() == {"run_id": None, "lines": []}


def test_tasklog_returns_lines_and_supports_after_cursor(app_ctx):
    client, app = app_ctx
    pve = FakePve(log_lines=["INFO: creating vzdump", "VM 100: done"])
    deps, _pve, _pbs, _power = make_deps(pve=pve, reachable=True)
    app.state.job_service = JobService(app.state.config_store, deps=deps)

    run_id = client.post("/api/backup/run").json()["run_id"]
    _wait_run(client, run_id)

    body = client.get("/api/tasklog").json()
    assert body["run_id"] == run_id
    texts = [line["text"] for line in body["lines"]]
    assert "INFO: creating vzdump" in texts and "VM 100: done" in texts
    assert all(line["source"] == "pve" and line["step"] == "backup" for line in body["lines"])

    # `after` the last id returns no further lines (incremental polling is a no-op when idle).
    last_id = body["lines"][-1]["id"]
    assert client.get(f"/api/tasklog?after={last_id}").json()["lines"] == []


# --- power + status enrichment + account (M6 backend additions) --------------


def test_power_on_sends_wol(app_ctx):
    client, app = app_ctx
    calls: list[int] = []
    _inject(app, wol=lambda _c: calls.append(1))
    assert client.post("/api/power/on").json() == {"ok": True}
    assert calls == [1]


def test_power_off_calls_poweroff(app_ctx):
    client, app = app_ctx
    _pve, _pbs, power = _inject(app)
    assert client.post("/api/power/off").json() == {"ok": True}
    assert power.powered_off is True


def test_power_off_conflict_when_busy(app_ctx):
    client, app = app_ctx

    class _Busy:
        is_running = True

    app.state.job_service = _Busy()
    assert client.post("/api/power/off").status_code == 409


def test_status_includes_datastore_and_load_when_online(app_ctx, monkeypatch):
    client, app = app_ctx
    _inject(app)
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: True)
    body = client.get("/api/status").json()
    assert body["pbs_online"] is True
    assert body["datastore"]["total"] == 8_000_000_000
    assert body["load"] == {"cpu": 7, "mem": 38, "uptime": 3600}


def test_status_omits_datastore_when_offline(app_ctx):
    client, _app = app_ctx  # fixture stubs reachability to False
    body = client.get("/api/status").json()
    assert body["datastore"] is None and body["load"] is None


def test_account_update_changes_username_and_password(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put("/api/account", json={"username": "newadmin", "password": "freshpass"})
    assert r.status_code == 200 and r.json() == {"username": "newadmin"}

    cfg = load_config(temp_config)
    assert cfg.app.auth.username == "newadmin"
    # New password takes effect for login.
    client.post("/api/logout")
    login = client.post("/api/login", json={"username": "newadmin", "password": "freshpass"})
    assert login.status_code == 200


def test_account_update_empty_password_keeps_current(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put("/api/account", json={"username": "admin2", "password": ""})
    assert r.status_code == 200
    client.post("/api/logout")
    # Old password still valid under the new username => password unchanged.
    login = client.post("/api/login", json={"username": "admin2", "password": "secret"})
    assert login.status_code == 200


def test_account_update_short_password_rejected(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put("/api/account", json={"username": "admin", "password": "ab"})
    assert r.status_code == 422
