"""Milestone 4 REST routers: status, config, guests, scheduler, jobs, wol, logs."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakePve, UnreachablePve, make_deps
from fastapi.testclient import TestClient

from app.config import load_config
from app.connectors.pve import Guest
from app.db import session_scope
from app.db.models import Run, RunKind, RunStatus, RunTrigger
from app.jobs import AlreadyRunningError, JobService
from app.main import create_app


@pytest.fixture
def app_ctx(temp_config, temp_db, monkeypatch):
    """Authenticated TestClient + app. PBS reachability is stubbed off by default so
    status/cycle tests don't touch the network; inject fake job deps per test."""
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
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
        for path in ("/api/status", "/api/config", "/api/guests", "/api/runs",
                     "/api/logs", "/api/tasklog"):
            assert client.get(path).status_code == 401, path
        for path in ("/api/backup/run", "/api/gc/run", "/api/power/on", "/api/power/off",
                     "/api/notify/test", "/api/scheduler/toggle", "/api/wol/test"):
            assert client.post(path).status_code == 401, path


def test_login_locks_out_after_repeated_failures(app_ctx):
    client, _app = app_ctx
    for _ in range(5):
        r = client.post("/api/login", json={"username": "admin", "password": "wrong-xxxx"})
        assert r.status_code == 401
    r = client.post("/api/login", json={"username": "admin", "password": "wrong-xxxx"})
    assert r.status_code == 429


# --- status ------------------------------------------------------------------


def test_status_shape(app_ctx):
    client, _app = app_ctx
    body = client.get("/api/status").json()
    assert body["scheduler_enabled"] is True  # example config enables backups
    assert body["job_running"] is False
    assert body["running_kind"] is None  # nothing in flight
    assert body["pbs_online"] is False
    assert body["last_run"] is None
    assert "next_run" in body and "schedule" in body


def test_status_running_kind_reflects_in_progress_run(app_ctx):
    """A RUNNING run surfaces its kind so the header pill can label GC/verify
    correctly instead of always saying 'Backup running' (UX-6)."""
    client, _app = app_ctx
    with session_scope() as s:
        s.add(Run(kind=RunKind.GC, trigger=RunTrigger.MANUAL, status=RunStatus.RUNNING,
                  started_at=datetime.now(UTC)))

    body = client.get("/api/status").json()
    assert body["running_kind"] == "gc"


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


def test_config_put_rejects_invalid_backup_schedule(app_ctx, temp_config):
    # A newly-set unparseable cron must 422 before persisting, not 500 the rearm and then
    # brick the next startup (BE-B1).
    client, _app = app_ctx
    before = load_config(temp_config).backup.schedule
    r = client.put("/api/config", json={"backup": {"schedule": "0 4 * *"}})  # 4 fields
    assert r.status_code == 422
    # Nothing was written: the old (valid) schedule is intact on disk.
    assert load_config(temp_config).backup.schedule == before


def test_config_put_rejects_invalid_mac(app_ctx, temp_config):
    # A newly-set malformed WoL MAC must 422 before persisting, not fail silently at wake
    # time (BE-C2). "00:11:22:33:44" is only 5 octets.
    client, _app = app_ctx
    before = load_config(temp_config).pbs.mac
    r = client.put("/api/config", json={"pbs": {"mac": "00:11:22:33:44"}})
    assert r.status_code == 422
    assert "pbs.mac" in str(r.json()["detail"])
    assert load_config(temp_config).pbs.mac == before  # nothing written


def test_config_put_accepts_valid_mac(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put("/api/config", json={"pbs": {"mac": "aa-bb-cc-dd-ee-ff"}})
    assert r.status_code == 200
    assert load_config(temp_config).pbs.mac == "aa-bb-cc-dd-ee-ff"


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


# --- api-key management -------------------------------------------------------


def test_generate_api_key_returns_and_persists(app_ctx):
    client, app = app_ctx
    r = client.post("/api/config/api-key")
    assert r.status_code == 200
    key = r.json()["api_key"]
    assert key and len(key) >= 20
    assert app.state.config_store.config.app.api_key == key


def test_regenerate_api_key_replaces_old(app_ctx):
    client, app = app_ctx
    first = client.post("/api/config/api-key").json()["api_key"]
    second = client.post("/api/config/api-key").json()["api_key"]
    assert first != second
    assert app.state.config_store.config.app.api_key == second


def test_delete_api_key_clears_it(app_ctx):
    client, app = app_ctx
    client.post("/api/config/api-key")
    r = client.delete("/api/config/api-key")
    assert r.status_code == 204
    assert app.state.config_store.config.app.api_key == ""


def test_api_key_management_requires_auth(temp_config, temp_db):
    with TestClient(create_app()) as client:
        assert client.post("/api/config/api-key").status_code == 401
        assert client.delete("/api/config/api-key").status_code == 401


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

    def busy(_trigger, *, power_off=True):
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


def test_backup_run_keep_on_leaves_pbs_up(app_ctx):
    client, app = app_ctx
    _pve, _pbs, power = _inject(app)
    r = client.post("/api/backup/run", json={"keep_on": True})
    assert r.status_code == 202
    body = _wait_run(client, r.json()["run_id"])
    assert body["status"] == "success"
    assert power.powered_off is False


def test_backup_run_default_powers_off(app_ctx):
    client, app = app_ctx
    _pve, _pbs, power = _inject(app)
    r = client.post("/api/backup/run")  # no body → keep_on defaults false
    assert r.status_code == 202
    _wait_run(client, r.json()["run_id"])
    assert power.powered_off is True


def test_gc_run_keep_on_leaves_pbs_up(app_ctx):
    client, app = app_ctx
    _pve, _pbs, power = _inject(app)
    r = client.post("/api/gc/run", json={"keep_on": True})
    assert r.status_code == 202
    body = _wait_run(client, r.json()["run_id"])
    assert body["status"] == "success"
    assert power.powered_off is False


def test_power_off_calls_poweroff(app_ctx):
    client, app = app_ctx
    _pve, _pbs, power = _inject(app)
    assert client.post("/api/power/off").json() == {"ok": True}
    assert power.powered_off is True


def test_power_off_conflict_when_busy(app_ctx):
    client, app = app_ctx

    class _Busy:
        def exclusive(self):
            # A run holds the lock: entering the guard raises, mapping to 409.
            raise AlreadyRunningError("A backup or GC run is already in progress")

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


def test_probe_pbs_offline_returns_no_datastore():
    from app.api._probe import probe_pbs
    from app.config import Config

    cfg = Config()
    cfg.pbs.host = ""  # no host => never probes
    online, datastore, load = probe_pbs(cfg, build_pbs=lambda c: None)
    assert online is False
    assert datastore is None
    assert load is None


def test_resolve_datastore_live_upserts_and_returns_live(temp_db):
    from app.api._probe import resolve_datastore
    from app.connectors.pbs import DatastoreStatus
    from app.db import session_scope
    from app.db.datastore_stats import get_datastore_stat

    view = resolve_datastore("backup", DatastoreStatus(total=10, used=4, avail=6))
    assert (view.total, view.used) == (10, 4)
    with session_scope() as s:
        row = get_datastore_stat(s, "backup")
    assert row is not None and row.used == 4  # live reading was persisted


def test_resolve_datastore_offline_uses_cache(temp_db):
    from app.api._probe import resolve_datastore
    from app.db import session_scope
    from app.db.datastore_stats import upsert_datastore_stat

    with session_scope() as s:
        upsert_datastore_stat(s, "backup", 8, 2)
    view = resolve_datastore("backup", None)
    assert (view.total, view.used, view.used_pct) == (8, 2, 25.0)


def test_resolve_datastore_none_when_no_live_no_cache(temp_db):
    from app.api._probe import resolve_datastore

    assert resolve_datastore("backup", None) is None


def test_status_datastore_from_cache_when_offline(app_ctx):
    client, _app = app_ctx
    with session_scope() as s:
        from app.db.datastore_stats import upsert_datastore_stat
        upsert_datastore_stat(s, "backup", 8_000_000_000, 2_000_000_000)

    body = client.get("/api/status").json()
    assert body["datastore"] is not None
    assert body["datastore"]["used_pct"] == 25.0
    assert body["datastore"]["used"] == 2_000_000_000
    assert body["datastore"]["total"] == 8_000_000_000
    assert body["load"] is None  # live-only, stays null when PBS offline


def test_account_update_changes_username_and_password(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put(
        "/api/account",
        json={"current_password": "secret12", "username": "newadmin", "password": "freshpass"},
    )
    assert r.status_code == 200 and r.json() == {"username": "newadmin"}

    cfg = load_config(temp_config)
    assert cfg.app.auth.username == "newadmin"
    # New password takes effect for login.
    client.post("/api/logout")
    login = client.post("/api/login", json={"username": "newadmin", "password": "freshpass"})
    assert login.status_code == 200


def test_account_update_empty_password_keeps_current(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put(
        "/api/account", json={"current_password": "secret12", "username": "admin2", "password": ""}
    )
    assert r.status_code == 200
    client.post("/api/logout")
    # Old password still valid under the new username => password unchanged.
    login = client.post("/api/login", json={"username": "admin2", "password": "secret12"})
    assert login.status_code == 200


def test_account_update_omitted_password_keeps_current(app_ctx, temp_config):
    client, _app = app_ctx
    # Password key entirely absent (not just "") also means "keep current".
    r = client.put("/api/account", json={"current_password": "secret12", "username": "admin4"})
    assert r.status_code == 200
    client.post("/api/logout")
    login = client.post("/api/login", json={"username": "admin4", "password": "secret12"})
    assert login.status_code == 200


def test_account_update_short_password_rejected(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put(
        "/api/account",
        json={"current_password": "secret12", "username": "admin", "password": "ab"},
    )
    assert r.status_code == 422


def test_account_update_wrong_current_password_rejected(app_ctx, temp_config):
    client, _app = app_ctx
    # A valid session alone must not be enough to rotate credentials (BE-S9).
    r = client.put(
        "/api/account",
        json={"current_password": "wrong-pass", "username": "hacker", "password": "takeover1"},
    )
    assert r.status_code == 401
    # Nothing changed: the original credentials still work.
    cfg = load_config(temp_config)
    assert cfg.app.auth.username == "admin"
    client.post("/api/logout")
    login = client.post("/api/login", json={"username": "admin", "password": "secret12"})
    assert login.status_code == 200


def test_account_update_missing_current_password_rejected(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.put("/api/account", json={"username": "admin", "password": "newpass-88"})
    assert r.status_code == 422


def test_password_change_keeps_acting_session_but_revokes_others(app_ctx):
    client, app = app_ctx
    # A second, independent session established before the change.
    with TestClient(app) as other:
        login = other.post("/api/login", json={"username": "admin", "password": "secret12"})
        assert login.status_code == 200
        assert other.get("/api/auth/me").status_code == 200
        # Acting client changes the password.
        r = client.put(
            "/api/account",
            json={"current_password": "secret12", "username": "admin", "password": "newpass-88"},
        )
        assert r.status_code == 200
        # Acting session is kept alive (cookie re-issued with the new hash).
        assert client.get("/api/auth/me").status_code == 200
        # The other pre-existing session is revoked (its pwv no longer matches the new hash).
        assert other.get("/api/auth/me").status_code == 401


# --- dashboard integration ---------------------------------------------------


def _enable_api_key(app, key="dash-key-123"):
    app.state.config_store.update(lambda c: setattr(c.app, "api_key", key))
    return key


def test_dashboard_403_when_no_key_configured(app_ctx):
    client, app = app_ctx
    app.state.config_store.update(lambda c: setattr(c.app, "api_key", ""))
    assert client.get("/api/dashboard").status_code == 403


def test_dashboard_401_without_header(app_ctx):
    client, app = app_ctx
    _enable_api_key(app)
    assert client.get("/api/dashboard").status_code == 401


def test_dashboard_401_with_wrong_key(app_ctx):
    client, app = app_ctx
    _enable_api_key(app, "right-key")
    r = client.get("/api/dashboard", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401


def test_dashboard_200_with_header_key(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    r = client.get("/api/dashboard", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "pbs_state", "next_run", "last_run_status", "last_run_time",
        "datastore_used_pct", "datastore_used_bytes", "datastore_total_bytes",
    }
    # PBS stubbed offline, no runs yet:
    assert body["pbs_state"] == "sleeping"
    assert body["last_run_status"] == "never"
    assert body["last_run_time"] is None
    assert body["datastore_used_pct"] is None
    assert body["datastore_used_bytes"] is None
    assert body["datastore_total_bytes"] is None


def test_dashboard_datastore_from_cache_when_offline(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    with session_scope() as s:  # session_scope already imported at top of test_api.py
        from app.db.datastore_stats import upsert_datastore_stat
        upsert_datastore_stat(s, "backup", 8_000_000_000, 2_000_000_000)

    body = client.get("/api/dashboard", headers={"X-API-Key": key}).json()
    assert body["datastore_used_pct"] == 25.0
    assert body["datastore_used_bytes"] == 2_000_000_000
    assert body["datastore_total_bytes"] == 8_000_000_000


def test_dashboard_upserts_and_returns_live_when_pbs_online(app_ctx, monkeypatch):
    client, app = app_ctx
    key = _enable_api_key(app)
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: True)
    _inject(app)  # deps.build_pbs -> FakePbs (datastore 8e9/2e9)

    body = client.get("/api/dashboard", headers={"X-API-Key": key}).json()
    assert body["pbs_state"] == "online"
    assert body["datastore_used_pct"] == 25.0
    assert body["datastore_used_bytes"] == 2_000_000_000
    assert body["datastore_total_bytes"] == 8_000_000_000

    # the live reading was persisted to the cache (write-on-GET)
    from app.db.datastore_stats import get_datastore_stat
    with session_scope() as s:
        row = get_datastore_stat(s, "backup")
    assert row is not None and row.used == 2_000_000_000


def test_dashboard_200_with_query_param_key(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    r = client.get(f"/api/dashboard?key={key}")
    assert r.status_code == 200
    assert r.json()["pbs_state"] == "sleeping"


def test_dashboard_401_with_non_ascii_key(app_ctx):
    client, app = app_ctx
    _enable_api_key(app)
    r = client.get("/api/dashboard?key=%C3%A9")
    assert r.status_code == 401


def _add_cycle_run(session, status: RunStatus, *, started_at: datetime) -> Run:
    run = Run(kind=RunKind.CYCLE, trigger=RunTrigger.SCHEDULED, status=status,
               started_at=started_at)
    session.add(run)
    session.flush()
    return run


def test_dashboard_last_run_reflects_last_finished_cycle_not_in_progress_one(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    now = datetime.now(UTC)
    with session_scope() as s:
        _add_cycle_run(s, RunStatus.SUCCESS, started_at=now - timedelta(hours=1))
        _add_cycle_run(s, RunStatus.RUNNING, started_at=now)

    r = client.get("/api/dashboard", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert body["last_run_status"] == "success"
    assert datetime.fromisoformat(body["last_run_time"]) == now - timedelta(hours=1)


def test_dashboard_last_run_never_when_only_running_cycle(app_ctx):
    client, app = app_ctx
    key = _enable_api_key(app)
    with session_scope() as s:
        _add_cycle_run(s, RunStatus.RUNNING, started_at=datetime.now(UTC))

    r = client.get("/api/dashboard", headers={"X-API-Key": key})
    assert r.status_code == 200
    body = r.json()
    assert body["last_run_status"] == "never"
    assert body["last_run_time"] is None
