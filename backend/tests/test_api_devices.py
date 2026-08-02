"""/api/devices — the Settings > Devices tab: CRUD, the removal guard, test, power, GC."""

from __future__ import annotations

import pytest
from fakes import FakeBox, FakePbs, FakePve, UnreachablePve, make_deps
from fastapi.testclient import TestClient

from app.config import REDACTED, load_config
from app.connectors.errors import ConnectorError, WolError
from app.connectors.pve import Guest
from app.jobs import JobService
from app.main import create_app

NEW_PBS = {
    "id": "pbs-03",
    "host": "192.0.2.30",
    "datastore": "archive",
    "api_token_id": "root@pam!joulenap",
    "api_token_secret": "third-secret",
    "managed_power": False,
}


@pytest.fixture
def app_ctx(temp_config, temp_db, monkeypatch):
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        _inject(app)
        yield client, app


def _inject(app, box: FakeBox | None = None, **deps_kwargs):
    deps, *_ = make_deps(**deps_kwargs)
    app.state.job_service = JobService(
        app.state.config_store, deps=deps, lease_deps=(box or FakeBox()).deps()
    )
    return app.state.job_service


def _drain(app, timeout: float = 5) -> None:
    import time

    service = app.state.job_service
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.current() is None and not service.pending() and not service.is_running:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")


# --- read ---------------------------------------------------------------------


def test_list_returns_both_kinds_with_secrets_masked(app_ctx):
    client, _app = app_ctx
    body = client.get("/api/devices").json()
    assert [d["id"] for d in body["pves"]] == ["pve-alpha", "pve-beta"]
    assert [d["id"] for d in body["pbss"]] == ["pbs-01", "pbs-02"]
    assert body["pves"][0]["api_token_secret"] == REDACTED
    assert body["pbss"][0]["api_token_secret"] == REDACTED


# --- create / update ----------------------------------------------------------


def test_create_persists_the_device(app_ctx, temp_config):
    client, _app = app_ctx
    r = client.post("/api/devices/pbss", json=NEW_PBS)
    assert r.status_code == 201
    assert r.json()["api_token_secret"] == REDACTED  # never echoed back in the clear
    assert [d.id for d in load_config(temp_config).pbss] == ["pbs-01", "pbs-02", "pbs-03"]


def test_create_rejects_a_duplicate_id(app_ctx):
    client, _app = app_ctx
    assert client.post("/api/devices/pbss", json={**NEW_PBS, "id": "pbs-01"}).status_code == 409


def test_create_rejects_managed_power_without_the_means_to_use_it(app_ctx):
    # managed_power with no MAC/SSH key would fail silently at wake time instead.
    client, _app = app_ctx
    r = client.post("/api/devices/pbss", json={**NEW_PBS, "managed_power": True, "mac": ""})
    assert r.status_code == 422


def test_create_rejects_an_unknown_kind(app_ctx):
    client, _app = app_ctx
    assert client.post("/api/devices/nases", json=NEW_PBS).status_code == 404


def test_update_keeps_a_secret_the_client_echoed_back(app_ctx, temp_config):
    client, _app = app_ctx
    device = client.get("/api/devices").json()["pbss"][0]
    device["wait_timeout"] = 240  # change something else; the secret stays REDACTED

    assert client.put("/api/devices/pbss/pbs-01", json=device).status_code == 200
    saved = load_config(temp_config).pbss[0]
    assert saved.wait_timeout == 240
    assert saved.api_token_secret == "test-pbs-secret"


def test_update_resolves_the_secret_of_the_edited_device_not_its_position(app_ctx, temp_config):
    # Every device carries an id, so a reordered or shortened list can never hand one
    # device's stored token to another.
    client, _app = app_ctx
    device = client.get("/api/devices").json()["pbss"][1]  # pbs-02
    device["wait_timeout"] = 300

    assert client.put("/api/devices/pbss/pbs-02", json=device).status_code == 200
    saved = {d.id: d.api_token_secret for d in load_config(temp_config).pbss}
    assert saved == {"pbs-01": "test-pbs-secret", "pbs-02": "test-pbs2-secret"}


def test_update_sets_a_new_secret(app_ctx, temp_config):
    client, _app = app_ctx
    device = client.get("/api/devices").json()["pbss"][0]
    device["api_token_secret"] = "rotated"

    assert client.put("/api/devices/pbss/pbs-01", json=device).status_code == 200
    assert load_config(temp_config).pbss[0].api_token_secret == "rotated"


def test_update_404s_on_an_unknown_device(app_ctx):
    client, _app = app_ctx
    assert client.put("/api/devices/pbss/nope", json=NEW_PBS).status_code == 404


# --- the removal guard --------------------------------------------------------


def test_deleting_a_pbs_in_use_409s_and_names_the_routes(app_ctx, temp_config):
    # Never a silent cascade: the user is told exactly which routes stand in the way.
    client, _app = app_ctx
    r = client.delete("/api/devices/pbss/pbs-01")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert sorted(detail["routes"]) == ["Nightly", "Offsite sync"]  # target + sync source
    assert len(load_config(temp_config).pbss) == 2  # nothing removed


def test_deleting_a_pve_in_use_409s_and_names_the_routes(app_ctx):
    client, _app = app_ctx
    r = client.delete("/api/devices/pves/pve-beta")
    assert r.status_code == 409
    assert r.json()["detail"]["routes"] == ["Nightly"]


def test_an_unused_device_can_be_deleted(app_ctx, temp_config):
    client, _app = app_ctx
    client.post("/api/devices/pbss", json=NEW_PBS)
    assert client.delete("/api/devices/pbss/pbs-03").status_code == 204
    assert [d.id for d in load_config(temp_config).pbss] == ["pbs-01", "pbs-02"]


def test_delete_404s_on_an_unknown_device(app_ctx):
    client, _app = app_ctx
    assert client.delete("/api/devices/pbss/nope").status_code == 404


def test_a_validation_failure_does_not_echo_the_config_back(app_ctx):
    """A config-level cross-check raises at loc=(), so pydantic attaches the *whole*
    validated config as the error's ``input`` — every API token, the secret key, the
    password hash, the SMTP and bot tokens — and the 422 shipped it to the browser."""
    client, app = app_ctx
    client.post(
        "/api/routes",
        json={"id": "watch", "kind": "external", "target": "pbs-02", "schedule": {"time": "03:00"}},
    )
    # An External route watches a box Joulenap wakes, so making that box unmanaged is
    # exactly the cross-check that fires.
    r = client.put("/api/devices/pbss/pbs-02", json={"id": "pbs-02", "host": "192.0.2.21",
                                                    "datastore": "offsite",
                                                    "api_token_id": "root@pam!joulenap",
                                                    "api_token_secret": REDACTED,
                                                    "managed_power": False})
    assert r.status_code == 422
    assert "managed_power" in r.text  # the reason still reaches the user
    stored = app.state.config_store.config
    for secret in (
        stored.app.secret_key,
        stored.app.auth.password_hash,
        *[d.api_token_secret for d in stored.pves],
        *[d.api_token_secret for d in stored.pbss],
    ):
        assert secret and secret not in r.text


def test_create_rejects_the_redaction_placeholder(app_ctx):
    """A body copy-pasted from GET /api/devices carries ***REDACTED*** as the token. Storing
    it verbatim is invisible — GET re-masks it (it is non-empty) — until a connection test
    fails with a 502 that gives no hint the stored token is the placeholder text itself."""
    client, app = app_ctx
    r = client.post("/api/devices/pbss", json={**NEW_PBS, "api_token_secret": REDACTED})
    assert r.status_code == 422
    assert "api_token_secret" in r.text
    assert [d.id for d in app.state.config_store.config.pbss] == ["pbs-01", "pbs-02"]


# --- test ---------------------------------------------------------------------


def test_test_reports_what_it_found_on_a_pve(app_ctx):
    client, app = app_ctx
    _inject(
        app,
        pves={
            "pve-alpha": FakePve(
                guests=[Guest(vmid=100, name="ct", type="lxc", status="running", node="n1")]
            )
        },
    )
    body = client.post("/api/devices/pves/pve-alpha/test").json()
    assert body["ok"] is True and "1 guest" in body["detail"]


def test_test_reports_datastore_usage_on_a_pbs(app_ctx):
    client, app = app_ctx
    _inject(app, pbss={"pbs-01": FakePbs()})
    body = client.post("/api/devices/pbss/pbs-01/test").json()
    assert body["ok"] is True and "25.0%" in body["detail"]


def test_test_502s_when_the_device_cannot_be_reached(app_ctx):
    # A failure is an upstream problem, not a 200 with ok:false — the UI shows the reason.
    client, app = app_ctx
    _inject(app, pves={"pve-alpha": UnreachablePve()})
    assert client.post("/api/devices/pves/pve-alpha/test").status_code == 502


# --- power --------------------------------------------------------------------


def test_power_on_sends_a_magic_packet(app_ctx):
    client, app = app_ctx
    box = FakeBox()
    _inject(app, box=box)
    assert client.post("/api/devices/pbss/pbs-01/power", json={"action": "wake"}).json() == {
        "ok": True
    }
    assert box.wol == ["pbs-01"]


def test_power_off_shuts_the_box_down(app_ctx):
    client, app = app_ctx
    box = FakeBox()
    _inject(app, box=box)
    assert client.post("/api/devices/pbss/pbs-01/power", json={"action": "poweroff"}).json() == {
        "ok": True
    }
    assert box.poweroffs == ["pbs-01"]


def test_power_off_409s_while_a_run_holds_the_box(app_ctx):
    # The topology's ⏻ is disabled during a run; the backend enforces it too, or a click
    # landing at the wrong moment would cut a vzdump off at the knees.
    client, app = app_ctx
    service = _inject(app)
    device = next(p for p in app.state.config_store.config.pbss if p.id == "pbs-01")
    service.lease.acquire(device)

    r = client.post("/api/devices/pbss/pbs-01/power", json={"action": "poweroff"})
    assert r.status_code == 409
    assert "pbs-01" in r.json()["detail"]


def test_power_off_409s_while_a_run_holds_the_single_run_lock(app_ctx):
    """The lease check alone was a check-then-act: a scheduled route could start in the gap,
    find the box still up (so no wake), and begin vzdump — then the SSH poweroff, which has
    no idle-wait and no refcount check, cut it off mid-backup. Holding the same lock a run
    holds for its whole life is what closes that window."""
    client, app = app_ctx
    box = FakeBox()
    service = _inject(app, box)
    service._lock.acquire()  # stand in for a run in flight; no lease taken yet
    try:
        r = client.post("/api/devices/pbss/pbs-01/power", json={"action": "poweroff"})
    finally:
        service._lock.release()

    assert r.status_code == 409
    assert "in progress" in r.json()["detail"]
    assert box.poweroffs == []  # the SSH command never ran


def test_power_409s_on_an_unmanaged_device(app_ctx):
    client, app = app_ctx
    app.state.config_store.update(
        lambda c: setattr(c.pbss[1], "managed_power", False)
    )
    r = client.post("/api/devices/pbss/pbs-02/power", json={"action": "wake"})
    assert r.status_code == 409
    assert "managed_power" in r.json()["detail"]


def test_power_on_400s_without_a_mac(app_ctx):
    client, app = app_ctx
    app.state.config_store.update(lambda c: setattr(c.pbss[0], "mac", ""))
    r = client.post("/api/devices/pbss/pbs-01/power", json={"action": "wake"})
    assert r.status_code == 400


def test_power_on_502s_when_the_packet_cannot_be_sent(app_ctx):
    client, app = app_ctx
    service = _inject(app)
    service.lease._deps.send_wol = lambda _d: (_ for _ in ()).throw(WolError("no route to host"))
    r = client.post("/api/devices/pbss/pbs-01/power", json={"action": "wake"})
    assert r.status_code == 502


def test_power_off_502s_when_the_ssh_command_fails(app_ctx):
    # A manual action that silently does nothing is worse than an error message.
    client, app = app_ctx
    service = _inject(app)
    service.lease._deps.poweroff = lambda _d: (_ for _ in ()).throw(ConnectorError("ssh refused"))
    r = client.post("/api/devices/pbss/pbs-01/power", json={"action": "poweroff"})
    assert r.status_code == 502


def test_power_404s_on_an_unknown_device(app_ctx):
    client, _app = app_ctx
    r = client.post("/api/devices/pbss/nope/power", json={"action": "wake"})
    assert r.status_code == 404


# --- ad-hoc maintenance -------------------------------------------------------


def test_gc_queues_a_route_less_run(app_ctx):
    client, app = app_ctx
    pbs = FakePbs()
    _inject(app, pbss={"pbs-01": pbs})

    r = client.post("/api/devices/pbss/pbs-01/gc")
    assert r.status_code == 202
    assert r.json() == {"pbs_id": "pbs-01", "action": "gc", "queued": 0}
    _drain(app)

    assert pbs.gc_started is True
    run = client.get("/api/runs").json()[0]
    assert run["kind"] == "gc" and run["route_id"] is None


def test_verify_queues_a_verify_run(app_ctx):
    client, app = app_ctx
    pbs = FakePbs()
    _inject(app, pbss={"pbs-02": pbs})

    assert client.post("/api/devices/pbss/pbs-02/verify").status_code == 202
    _drain(app)

    assert pbs.verify_started is True
    # An ad-hoc verify checks everything, rather than pacing itself like a Verify route.
    # "Everything" reaches PBS as ignore-verified: 0. The bug was asking for
    # outdated_after=None, which means "only never-verified" — skipping precisely the old
    # snapshots the user clicked the button about.
    assert pbs.verify_args["ignore_verified"] is False


def test_maintenance_works_on_an_always_on_box(app_ctx):
    # No managed_power guard here: GC and verify are valid on a box Joulenap never wakes.
    client, app = app_ctx
    app.state.config_store.update(lambda c: setattr(c.pbss[1], "managed_power", False))
    assert client.post("/api/devices/pbss/pbs-02/gc").status_code == 202
    _drain(app)


def test_maintenance_404s_on_an_unknown_device_or_action(app_ctx):
    client, _app = app_ctx
    assert client.post("/api/devices/pbss/nope/gc").status_code == 404
    assert client.post("/api/devices/pbss/pbs-01/reboot").status_code == 404


def test_keep_on_leaves_the_box_awake(app_ctx):
    client, app = app_ctx
    box = FakeBox()
    _inject(app, box=box)

    client.post("/api/devices/pbss/pbs-01/gc", json={"keep_on": True})
    _drain(app)

    assert box.poweroffs == []
