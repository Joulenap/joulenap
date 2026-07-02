"""PBS client tests using an in-memory httpx MockTransport (no real PBS)."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from app.connectors.errors import ApiError, TaskError
from app.connectors.pbs import DatastoreStatus, PbsClient, get_fingerprint


def make_client(handler, **kwargs) -> PbsClient:
    transport = httpx.MockTransport(handler)
    return PbsClient(
        host="pbs.local",
        datastore="backup",
        token_id="root@pam!joulenap",
        token_secret="secret",
        transport=transport,
        **kwargs,
    )


def json_data(payload):
    return httpx.Response(200, json={"data": payload})


def test_task_log_parses_lines():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/tasks/UPID:gc/log")
        return json_data([{"n": 1, "t": "GC starting"}, {"n": 2, "t": "removed 3 chunks"}])

    lines = make_client(handler).task_log("UPID:gc")
    assert lines == [(1, "GC starting"), (2, "removed 3 chunks")]


def test_pbs_auth_header_uses_colon():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return json_data({"version": "3.2"})

    make_client(handler).version()
    # PBS separates token id and secret with a colon (PVE uses '=').
    assert seen["auth"] == "PBSAPIToken=root@pam!joulenap:secret"


def test_datastore_status_computes_pct():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/admin/datastore/backup/status")
        return json_data({"total": 8_000, "used": 2_000, "avail": 6_000})

    status = make_client(handler).datastore_status()
    assert isinstance(status, DatastoreStatus)
    assert status.used_pct == 25.0


def test_datastore_status_zero_total_safe():
    status = DatastoreStatus(total=0, used=0, avail=0)
    assert status.used_pct == 0.0


def test_start_gc_returns_upid():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/admin/datastore/backup/gc")
        return json_data("UPID:pbs:00:garbage_collection::")

    assert make_client(handler).start_gc().startswith("UPID:")


def test_wait_task_success_and_failure():
    def ok(request: httpx.Request) -> httpx.Response:
        return json_data({"status": "stopped", "exitstatus": "OK"})

    res = make_client(ok).wait_task("UPID:x", poll_interval=0, sleep=lambda _s: None)
    assert res["exitstatus"] == "OK"

    def fail(request: httpx.Request) -> httpx.Response:
        return json_data({"status": "stopped", "exitstatus": "error"})

    with pytest.raises(TaskError):
        make_client(fail).wait_task("UPID:x", poll_interval=0, sleep=lambda _s: None)


def test_start_verify_incremental_sends_window():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/admin/datastore/backup/verify")
        seen["body"] = request.content.decode()
        return json_data("UPID:pbs:verify::")

    make_client(handler).start_verify(ignore_verified=True, outdated_after=30)
    assert "ignore-verified=1" in seen["body"]
    assert "outdated-after=30" in seen["body"]


def test_start_verify_full_omits_window():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return json_data("UPID:pbs:verify::")

    make_client(handler).start_verify(ignore_verified=False)
    assert "ignore-verified=0" in seen["body"]
    assert "outdated-after" not in seen["body"]


def test_start_verify_new_only_omits_window():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return json_data("UPID:pbs:verify::")

    make_client(handler).start_verify(ignore_verified=True, outdated_after=None)
    assert "ignore-verified=1" in seen["body"]
    assert "outdated-after" not in seen["body"]


def test_latest_backups_keeps_max_time_per_guest():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/admin/datastore/backup/snapshots")
        return json_data(
            [
                {"backup-type": "ct", "backup-id": "100", "backup-time": 1000},
                {"backup-type": "ct", "backup-id": "100", "backup-time": 3000},  # newer
                {"backup-type": "vm", "backup-id": "101", "backup-time": 2000},
                {"backup-type": "host", "backup-id": "pbs", "backup-time": 9000},  # not a guest
                {"backup-type": "ct", "backup-id": "bogus", "backup-time": 5000},  # non-numeric id
            ]
        )

    latest = make_client(handler).latest_backups()
    assert latest == {100: 3000, 101: 2000}


def test_latest_backups_empty_datastore():
    assert make_client(lambda _r: json_data([])).latest_backups() == {}


def test_active_tasks_filters_running():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/nodes/localhost/tasks")
        assert request.url.params.get("running") == "1"
        return json_data([{"upid": "UPID:verify", "type": "verify"}])

    tasks = make_client(handler).active_tasks()
    assert tasks[0]["type"] == "verify"


def test_wait_until_idle_polls_until_clear():
    # Busy on the first poll, idle on the second -> returns True after one sleep.
    responses = iter([[{"upid": "UPID:gc"}], []])

    def handler(request: httpx.Request) -> httpx.Response:
        return json_data(next(responses))

    assert make_client(handler).wait_until_idle(timeout=10, interval=0, sleep=lambda _s: None)


def test_wait_until_idle_times_out_while_busy():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_data([{"upid": "UPID:gc"}])  # never clears

    client = make_client(handler)
    assert client.wait_until_idle(timeout=0, interval=0, sleep=lambda _s: None) is False


def test_http_error_becomes_apierror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    with pytest.raises(ApiError) as exc:
        make_client(handler).datastore_status()
    assert exc.value.status == 403


def test_get_fingerprint(monkeypatch):
    # Fake the TLS handshake so no real network is touched.
    der = b"\x01\x02\x03certbytes"
    expected = hashlib.sha256(der).hexdigest().upper()
    expected = ":".join(expected[i : i + 2] for i in range(0, len(expected), 2))

    class FakeTLS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getpeercert(self, binary_form=False):
            return der

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("socket.create_connection", lambda *a, **k: FakeSock())
    monkeypatch.setattr(
        "ssl.SSLContext.wrap_socket", lambda self, sock, server_hostname=None: FakeTLS()
    )
    assert get_fingerprint("pbs.local") == expected
