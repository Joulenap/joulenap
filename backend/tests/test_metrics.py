"""GET /metrics — Prometheus exposition (11.11): auth, format, and value mapping."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fakes import make_deps
from fastapi.testclient import TestClient

from app.db import session_scope
from app.db.guest_backups import upsert_last_backups
from app.db.models import Run, RunKind, RunStatus, RunTrigger
from app.jobs import JobService
from app.main import create_app

KEY = "metrics-key-123"

# name{label="v",...} value  — the exposition format's sample line.
SAMPLE = re.compile(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? -?[0-9.eE+-]+$')


@pytest.fixture
def client(temp_config, temp_db, monkeypatch):
    monkeypatch.setattr("app.connectors.net.tcp_reachable", lambda *a, **k: False)
    app = create_app()
    app.state.config_store.update(lambda c: setattr(c.app, "api_key", KEY))
    deps, _pve, _pbs, _power = make_deps()
    app.state.job_service = JobService(app.state.config_store, deps=deps)
    with TestClient(app) as c:
        yield c


def _scrape(client) -> str:
    r = client.get("/metrics", params={"key": KEY})
    assert r.status_code == 200
    return r.text


def _value(text: str, name: str) -> float | None:
    """The value of the first sample of ``name`` (exact family match), or None if absent."""
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        head, _, value = line.rpartition(" ")
        if head == name or head.startswith(name + "{"):
            return float(value)
    return None


def _add_run(**kw) -> int:
    with session_scope() as session:
        run = Run(
            kind=kw.get("kind", RunKind.CYCLE),
            trigger=RunTrigger.SCHEDULED,
            status=kw.get("status", RunStatus.SUCCESS),
            started_at=kw.get("started_at", datetime(2026, 6, 28, 4, 0, tzinfo=UTC)),
            finished_at=kw.get("finished_at", datetime(2026, 6, 28, 4, 1, 23, tzinfo=UTC)),
            guests_ok=kw.get("guests_ok", 4),
        )
        session.add(run)
        session.flush()
        return run.id


# --- auth --------------------------------------------------------------------


def test_403_when_no_api_key_is_configured(client):
    # Same contract as /api/dashboard: 403 = the integration is off, not a bad credential.
    client.app.state.config_store.update(lambda c: setattr(c.app, "api_key", ""))
    assert client.get("/metrics").status_code == 403


def test_401_without_a_key(client):
    assert client.get("/metrics").status_code == 401


def test_401_with_the_wrong_key(client):
    assert client.get("/metrics", params={"key": "nope"}).status_code == 401
    assert client.get("/metrics", headers={"X-API-Key": "nope"}).status_code == 401


def test_accepts_the_key_as_a_header_too(client):
    # Prometheus scrape configs use ?key= (params:), dashboards tend to use the header.
    assert client.get("/metrics", headers={"X-API-Key": KEY}).status_code == 200


# --- format ------------------------------------------------------------------


def test_content_type_is_the_prometheus_text_format(client):
    r = client.get("/metrics", params={"key": KEY})
    assert r.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in r.headers["content-type"]


def test_every_line_is_a_valid_help_type_or_sample(client):
    _add_run()
    upsert_last_backups_now({101: datetime(2026, 6, 28, 4, 1, tzinfo=UTC)})
    text = _scrape(client)

    families = set()
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("# HELP ") or line.startswith("# TYPE "):
            families.add(line.split()[2])
            continue
        assert SAMPLE.match(line), f"not a valid sample line: {line!r}"
        assert line.startswith("joulenap_"), f"unprefixed metric: {line!r}"
    assert families, "no metric families emitted"


def test_every_sample_family_is_declared(client):
    _add_run()
    text = _scrape(client)
    declared = {ln.split()[2] for ln in text.splitlines() if ln.startswith("# TYPE ")}
    for line in text.splitlines():
        if line.startswith("#") or not line:
            continue
        name = line.split("{")[0].split(" ")[0]
        assert name in declared, f"{name} emitted without a # TYPE line"


# --- values ------------------------------------------------------------------


def upsert_last_backups_now(latest: dict[int, datetime]) -> None:
    with session_scope() as session:
        upsert_last_backups(session, {v: int(ts.timestamp()) for v, ts in latest.items()})


def test_reports_build_info_and_state(client):
    text = _scrape(client)
    assert "joulenap_build_info{version=" in text
    assert _value(text, "joulenap_pbs_online") == 0  # stubbed unreachable
    assert _value(text, "joulenap_scheduler_enabled") == 1
    assert _value(text, "joulenap_job_running") == 0


def test_last_run_series_track_the_run_history(client):
    _add_run()
    text = _scrape(client)
    assert _value(text, "joulenap_last_run_success") == 1
    assert _value(text, "joulenap_last_run_duration_seconds") == 83
    assert _value(text, "joulenap_last_run_guests") == 4
    assert _value(text, "joulenap_last_run_timestamp_seconds") == datetime(
        2026, 6, 28, 4, 0, tzinfo=UTC
    ).timestamp()


def test_timestamps_keep_full_precision(client):
    # %g formatting would round a 10-digit Unix timestamp to the nearest ~1000s, so every
    # *_timestamp_seconds series would drift by up to a quarter of an hour.
    started = datetime(2026, 6, 28, 4, 0, 43, tzinfo=UTC)
    _add_run(started_at=started, finished_at=started + timedelta(seconds=83))
    text = _scrape(client)
    assert _value(text, "joulenap_last_run_timestamp_seconds") == started.timestamp()
    assert f"joulenap_last_run_timestamp_seconds {int(started.timestamp())}" in text


def test_a_failed_last_run_reports_zero_not_absent(client):
    _add_run(status=RunStatus.FAILURE, guests_ok=None)
    text = _scrape(client)
    assert _value(text, "joulenap_last_run_success") == 0
    # guests_ok is null on a failed run -> the series is omitted rather than reported as 0.
    assert _value(text, "joulenap_last_run_guests") is None


def test_absent_values_are_omitted_not_zeroed(client):
    # With no history at all, publishing 0 would graph the last backup as January 1970.
    text = _scrape(client)
    for name in (
        "joulenap_last_run_timestamp_seconds",
        "joulenap_last_run_success",
        "joulenap_last_run_duration_seconds",
        "joulenap_datastore_used_bytes",
        "joulenap_guest_last_backup_timestamp_seconds",
    ):
        assert _value(text, name) is None, f"{name} should be absent, not zero"


def test_per_guest_series_come_from_the_backup_cache(client):
    stamp = datetime(2026, 6, 28, 2, 30, tzinfo=UTC)
    upsert_last_backups_now({101: stamp, 102: stamp - timedelta(days=3)})
    text = _scrape(client)
    assert (
        f'joulenap_guest_last_backup_timestamp_seconds{{vmid="101"}} {int(stamp.timestamp())}'
        in text
    )
    assert 'joulenap_guest_last_backup_timestamp_seconds{vmid="102"}' in text


def test_run_counts_are_grouped_by_kind_and_status(client):
    _add_run()
    _add_run()
    _add_run(kind=RunKind.GC, status=RunStatus.FAILURE)
    text = _scrape(client)
    assert 'joulenap_runs_recent{kind="cycle",status="success"} 2' in text
    assert 'joulenap_runs_recent{kind="gc",status="failure"} 1' in text


def test_in_flight_runs_are_not_counted_as_history(client):
    # A RUNNING row has no outcome yet; counting it would show a phantom status bucket.
    _add_run(status=RunStatus.RUNNING, finished_at=None)
    text = _scrape(client)
    assert "status=\"running\"" not in text
