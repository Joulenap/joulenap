"""/api/routes — CRUD for the route strip's editor modal, plus "run this one now"."""

from __future__ import annotations

import pytest
from fakes import FakeBox, FakePve, make_deps
from fastapi.testclient import TestClient

from app.config import load_config
from app.connectors.pve import Guest
from app.jobs import JobService
from app.main import create_app

NEW_ROUTE = {
    "id": "weekly",
    "name": "Weekly",
    "kind": "backup",
    "sources": [{"pve": "pve-alpha"}],
    "target": "pbs-01",
    "schedule": {"time": "01:00", "days": [False] * 6 + [True]},
}


@pytest.fixture
def app_ctx(temp_config, temp_db, monkeypatch):
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    with TestClient(app) as client:
        client.post("/api/auth/setup", json={"username": "admin", "password": "secret12"})
        deps, *_ = make_deps()
        app.state.job_service = JobService(
            app.state.config_store, deps=deps, lease_deps=FakeBox().deps()
        )
        yield client, app


def _armed(app) -> set[str]:
    return set(app.state.scheduler.armed_route_ids())


def _route(config, route_id: str):
    return next(r for r in config.routes if r.id == route_id)


# --- read ---------------------------------------------------------------------


def test_list_returns_the_configured_routes(app_ctx):
    client, _app = app_ctx
    routes = client.get("/api/routes").json()
    assert [r["id"] for r in routes] == ["nightly", "lab", "offsite"]
    # Verbatim config: a route holds no secrets, so nothing is redacted away.
    assert routes[0]["sources"] == [
        {"pve": "pve-alpha", "guests": {"mode": "all", "list": []}},
        {"pve": "pve-beta", "guests": {"mode": "all", "list": []}},
    ]


# --- create -------------------------------------------------------------------


def test_create_persists_and_arms_the_route(app_ctx, temp_config):
    client, app = app_ctx
    r = client.post("/api/routes", json=NEW_ROUTE)
    assert r.status_code == 201
    assert [x.id for x in load_config(temp_config).routes][-1] == "weekly"
    assert "weekly" in _armed(app)  # the schedule takes effect without a restart


def test_create_rejects_a_duplicate_id(app_ctx):
    client, _app = app_ctx
    r = client.post("/api/routes", json={**NEW_ROUTE, "id": "nightly"})
    assert r.status_code == 409


def test_create_rejects_a_route_pointing_at_an_unknown_device(app_ctx, temp_config):
    # The cross-reference check lives on the whole Config, so it only fires if the endpoint
    # re-validates the document rather than appending to the live model.
    client, _app = app_ctx
    r = client.post("/api/routes", json={**NEW_ROUTE, "target": "pbs-99"})
    assert r.status_code == 422
    assert len(load_config(temp_config).routes) == 3  # nothing written


def test_create_rejects_a_backup_route_with_no_storage_mapping(app_ctx):
    # pve-beta has no storage for pbs-02, so a route between them could never run vzdump.
    client, _app = app_ctx
    r = client.post(
        "/api/routes",
        json={**NEW_ROUTE, "sources": [{"pve": "pve-beta"}], "target": "pbs-02"},
    )
    assert r.status_code == 422
    assert "storage" in str(r.json()["detail"])


def test_create_rejects_a_cron_the_model_cannot_catch(app_ctx, temp_config):
    """It wouldn't crash arming — but the route would silently never fire, which is worse.

    ``0 4 * * 8`` is deliberately *well-formed*: five fields, so ``RouteSchedule`` accepts it
    and only ``check_route_crons`` (BE-B1) can reject it. A 4-field string never reaches the
    guard, so testing with one proves the model validator and nothing else.
    """
    client, _app = app_ctx
    body = {**NEW_ROUTE, "schedule": {"cron": "0 4 * * 8"}}  # day-of-week 8 does not exist
    r = client.post("/api/routes", json=body)
    assert r.status_code == 422
    detail = str(r.json()["detail"])
    assert "invalid schedule.cron" in detail  # the guard's own wording, not pydantic's
    assert "0 4 * * 8" in detail
    assert len(load_config(temp_config).routes) == 3


def test_create_still_rejects_a_cron_with_the_wrong_field_count(app_ctx, temp_config):
    # The model validator's half of the same contract, kept separate so neither can mask
    # the other going missing.
    client, _app = app_ctx
    r = client.post("/api/routes", json={**NEW_ROUTE, "schedule": {"cron": "0 4 * *"}})
    assert r.status_code == 422
    assert "schedule.cron" in str(r.json()["detail"])
    assert len(load_config(temp_config).routes) == 3


def test_an_unchanged_bad_cron_does_not_block_an_unrelated_edit(app_ctx, temp_config):
    """The "changed-only" half of the guard: a legacy string already on disk must not lock
    the user out of Settings. Written straight to the store because the API would refuse it."""
    client, app = app_ctx
    app.state.config_store.update(
        lambda c: setattr(_route(c, "nightly").schedule, "cron", "0 4 * * 8")
    )

    body = client.get("/api/routes").json()[0]
    body["name"] = "Nightly renamed"
    assert client.put("/api/routes/nightly", json=body).status_code == 200

    saved = _route(load_config(temp_config), "nightly")
    assert saved.name == "Nightly renamed"
    assert saved.schedule.cron == "0 4 * * 8"  # carried through, still saveable


def test_changing_a_bad_cron_to_another_bad_one_is_rejected(app_ctx):
    # Unchanged is grandfathered; *edited* is not, or the escape hatch would become a hole.
    client, app = app_ctx
    app.state.config_store.update(
        lambda c: setattr(_route(c, "nightly").schedule, "cron", "0 4 * * 8")
    )

    body = client.get("/api/routes").json()[0]
    body["schedule"]["cron"] = "0 99 * * *"
    r = client.put("/api/routes/nightly", json=body)
    assert r.status_code == 422
    assert "invalid schedule.cron" in str(r.json()["detail"])


# --- update -------------------------------------------------------------------


def test_update_replaces_the_route_and_rearms(app_ctx, temp_config):
    client, app = app_ctx
    body = client.get("/api/routes").json()[0]
    body["schedule"]["time"] = "23:15"
    assert client.put("/api/routes/nightly", json=body).status_code == 200

    saved = load_config(temp_config).routes[0]
    assert saved.schedule.time == "23:15"
    job = app.state.scheduler.route_job("nightly")
    assert "23" in str(job.trigger)  # re-armed on the new time, not the old one


def test_disabling_a_route_disarms_it_but_keeps_it(app_ctx, temp_config):
    client, app = app_ctx
    body = client.get("/api/routes").json()[0]
    body["enabled"] = False
    assert client.put("/api/routes/nightly", json=body).status_code == 200

    assert "nightly" not in _armed(app)
    assert [r.id for r in load_config(temp_config).routes] == ["nightly", "lab", "offsite"]


def test_update_404s_on_an_unknown_route(app_ctx):
    client, _app = app_ctx
    assert client.put("/api/routes/nope", json=NEW_ROUTE).status_code == 404


# --- delete -------------------------------------------------------------------


def test_delete_removes_the_route_and_its_job(app_ctx, temp_config):
    client, app = app_ctx
    assert client.delete("/api/routes/lab").status_code == 204
    assert [r.id for r in load_config(temp_config).routes] == ["nightly", "offsite"]
    assert "lab" not in _armed(app)


def test_delete_404s_on_an_unknown_route(app_ctx):
    client, _app = app_ctx
    assert client.delete("/api/routes/nope").status_code == 404


# --- run now ------------------------------------------------------------------


def test_run_queues_the_route(app_ctx):
    client, app = app_ctx
    r = client.post("/api/routes/nightly/run")
    assert r.status_code == 202
    assert r.json() == {"route_id": "nightly", "queued": 0}
    _drain(app)


def _inject_box(app) -> FakeBox:
    """Re-wire the job service onto a FakeBox we keep a handle on, to read poweroffs.

    Both sources get a guest: a source with nothing to back up fails, and a failed run
    deliberately leaves the box on, which would mask what these tests are asking about.
    """
    box = FakeBox()
    guest = Guest(vmid=100, name="ct", type="lxc", status="running", node="n1")
    deps, *_ = make_deps(
        pves={"pve-alpha": FakePve(guests=[guest]), "pve-beta": FakePve(guests=[guest])}
    )
    app.state.job_service = JobService(
        app.state.config_store, deps=deps, lease_deps=box.deps()
    )
    return box


def test_a_run_with_no_body_powers_the_pbs_back_off(app_ctx):
    """The default half of ``keep_on``. Posting *no body* is what the dashboard's Run
    button does, so this is the path that decides whether the box goes back to sleep, and
    nothing pinned it: flipping ``keep_on`` to True left every manual run's target awake
    and the suite stayed green."""
    client, app = app_ctx
    box = _inject_box(app)

    assert client.post("/api/routes/nightly/run").status_code == 202
    _drain(app)

    assert box.poweroffs == ["pbs-01"]


def test_a_run_asking_to_keep_the_pbs_on_leaves_it_awake(app_ctx):
    client, app = app_ctx
    box = _inject_box(app)

    assert client.post("/api/routes/nightly/run", json={"keep_on": True}).status_code == 202
    _drain(app)

    assert box.poweroffs == []


def test_run_404s_on_an_unknown_route(app_ctx):
    client, _app = app_ctx
    assert client.post("/api/routes/nope/run").status_code == 404


def test_running_the_same_route_twice_conflicts(app_ctx):
    client, app = app_ctx
    service = app.state.job_service
    gate = _park(service, "nightly")
    try:
        assert client.post("/api/routes/nightly/run").status_code == 409
    finally:
        gate.set()
    _drain(app)


def test_a_second_route_queues_rather_than_conflicting(app_ctx):
    # Per-route schedules mean two routes fire minutes apart; the second waits its turn.
    client, app = app_ctx
    service = app.state.job_service
    gate = _park(service, "nightly")
    try:
        assert client.post("/api/routes/offsite/run").json()["queued"] == 1
    finally:
        gate.set()
    _drain(app)


def test_keep_on_reaches_the_lease(app_ctx):
    client, app = app_ctx
    box = FakeBox()
    app.state.job_service = JobService(
        app.state.config_store, deps=make_deps()[0], lease_deps=box.deps()
    )
    client.post("/api/routes/offsite/run", json={"keep_on": True})
    _drain(app)
    assert box.poweroffs == []


# --- helpers ------------------------------------------------------------------


def _park(service, route_id: str):
    """Start a run that parks, so the queue has something in flight. Returns its release."""
    import threading

    from app.db.models import RunKind, RunStatus, RunTrigger
    from app.jobs.service import QueuedRun

    started, release = threading.Event(), threading.Event()

    def job(_c, _s, recorder, _d):
        started.set()
        release.wait(timeout=5)
        recorder.finish(RunStatus.SUCCESS)

    service.enqueue(
        QueuedRun(
            key=route_id,
            route_id=route_id,
            trigger=RunTrigger.MANUAL,
            kind=RunKind.CYCLE,
            job=job,
        )
    )
    assert started.wait(timeout=5)
    return release


def _drain(app, timeout: float = 5) -> None:
    import time

    service = app.state.job_service
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.current() is None and not service.pending() and not service.is_running:
            return
        time.sleep(0.01)
    raise AssertionError("queue did not drain")
