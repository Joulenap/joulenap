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


def _recorder(existing: dict[str, list]):
    """A handler that records every call and answers ``/config/*`` listings from ``existing``."""
    calls: list[tuple[str, str, str]] = []  # (method, path, body + query)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path, request.content.decode() + request.url.query.decode()))
        for section, entries in existing.items():
            if request.method == "GET" and path.endswith(f"/config/{section}"):
                return json_data(entries)
        return json_data("UPID:pbs:sync::")

    return handler, calls


def test_ensure_remote_creates_when_absent():
    handler, calls = _recorder({"remote": []})

    make_client(handler).ensure_remote(
        "joulenap-r1", host="pbs2.local", auth_id="root@pam!jn", password="s", fingerprint="aa:bb"
    )

    assert [(m, p) for m, p, _b in calls] == [
        ("GET", "/api2/json/config/remote"),
        ("POST", "/api2/json/config/remote"),
    ]
    body = calls[-1][2]
    assert "name=joulenap-r1" in body
    assert "auth-id=root%40pam%21jn" in body  # PBS 4.x field name, form-encoded
    assert "fingerprint=aa%3Abb" in body
    assert "port" not in body  # the default is left out


def test_ensure_remote_replaces_an_existing_one():
    handler, calls = _recorder({"remote": [{"name": "joulenap-r1"}]})

    make_client(handler).ensure_remote(
        "joulenap-r1", host="pbs2.local", auth_id="root@pam!jn", password="s", port=8123
    )

    # Delete-then-create: a route whose peer or direction changed must not be patched.
    assert [(m, p) for m, p, _b in calls] == [
        ("GET", "/api2/json/config/remote"),
        ("DELETE", "/api2/json/config/remote/joulenap-r1"),
        ("POST", "/api2/json/config/remote"),
    ]
    assert "port=8123" in calls[-1][2]


def test_pull_sync_job_omits_the_direction():
    handler, calls = _recorder({"sync": []})

    make_client(handler).ensure_sync_job(
        "joulenap-r1",
        remote="joulenap-r1",
        remote_store="offsite",
        store="backup",
        owner="root@pam!jn",
    )

    body = calls[-1][2]
    assert "store=backup" in body and "remote-store=offsite" in body
    # Pull is PBS's default; omitting it keeps the call identical to what servers without
    # push support accept.
    assert "sync-direction" not in body
    # Without an owner PBS assigns the fetched groups to root@pam, and the sync then fails
    # per group against groups the token already owns.
    assert "owner=root%40pam%21jn" in body


def test_push_sync_job_never_sends_the_owner():
    handler, calls = _recorder({"sync": []})

    make_client(handler).ensure_sync_job(
        "joulenap-r1",
        remote="joulenap-r1",
        remote_store="offsite",
        store="backup",
        direction="push",
        owner="root@pam!jn",
    )

    # Pushed groups are owned by the remote's auth-id whatever the job says; the field would
    # only narrow which local groups the job may read.
    assert "owner" not in calls[-1][2]


def test_push_sync_job_sends_the_direction_only_in_the_job_body():
    handler, calls = _recorder({"sync": [{"id": "joulenap-r1"}]})
    client = make_client(handler)

    client.ensure_sync_job(
        "joulenap-r1",
        remote="joulenap-r1",
        remote_store="backup",
        store="offsite",
        direction="push",
    )
    upid = client.run_sync_job("joulenap-r1")

    # Verified against a live PBS 4.2: `sync-direction` describes the job, so only the create
    # takes it. The delete and the run answer 400 "schema does not allow additional
    # properties" if it is sent, and resolve the job from its id alone.
    assert calls[1][:2] == ("DELETE", "/api2/json/config/sync/joulenap-r1")
    assert "sync-direction" not in calls[1][2]
    assert "sync-direction=push" in calls[2][2]  # the POST that recreates it
    assert calls[-1][:2] == ("POST", "/api2/json/admin/sync/joulenap-r1/run")
    assert "sync-direction" not in calls[-1][2]
    assert upid.startswith("UPID:")


def test_sync_job_sends_transfer_last_and_remove_vanished_only_when_set():
    handler, calls = _recorder({"sync": []})
    client = make_client(handler)

    client.ensure_sync_job("j", remote="j", remote_store="a", store="b")
    bare = calls[-1][2]
    client.ensure_sync_job(
        "j", remote="j", remote_store="a", store="b", direction="push",
        transfer_last=3, remove_vanished=True,
    )
    full = calls[-1][2]

    # PBS's transfer-last has minimum 1, so 0 means "leave the parameter out"; and an
    # omitted remove-vanished is PBS's own default (false), so a bare job stays byte-for-byte
    # what it was before these options existed.
    assert "transfer-last" not in bare and "remove-vanished" not in bare
    assert "transfer-last=3" in full and "remove-vanished=1" in full
    assert "sync-direction=push" in full  # both fields ride the same job schema as push


def test_start_prune_maps_keep_counts_and_runs_as_a_task():
    handler, calls = _recorder({})

    upid = make_client(handler).start_prune(
        {"keep_last": 0, "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 0, "keep_yearly": 0}
    )

    assert calls[-1][:2] == ("POST", "/api2/json/admin/datastore/backup/prune-datastore")
    body = calls[-1][2]
    assert "keep-daily=7" in body and "keep-weekly=4" in body
    assert "keep-last" not in body and "keep-monthly" not in body  # zeros are omitted
    assert "use-task" not in body  # PBS 4.2 rejects it; prune-datastore is always a task
    assert upid.startswith("UPID:")


def test_start_prune_refuses_an_all_zero_retention():
    handler, calls = _recorder({})

    with pytest.raises(ValueError):
        make_client(handler).start_prune({"keep_daily": 0})
    assert calls == []


def test_delete_sync_job_removes_an_existing_job_in_either_direction():
    handler, calls = _recorder({"sync": [{"id": "joulenap-r1"}]})

    make_client(handler).delete_sync_job("joulenap-r1")

    assert [(m, p) for m, p, _b in calls] == [
        ("GET", "/api2/json/config/sync"),
        ("DELETE", "/api2/json/config/sync/joulenap-r1"),
    ]
    assert "sync-direction=all" in calls[0][2]  # or a push job is invisible here too


def test_delete_sync_job_is_a_no_op_when_there_is_none():
    handler, calls = _recorder({"sync": []})

    make_client(handler).delete_sync_job("joulenap-r1")

    assert [m for m, _p, _b in calls] == ["GET"]


def test_the_sync_existence_check_lists_both_directions():
    handler, calls = _recorder({"sync": [{"id": "joulenap-r1"}]})

    make_client(handler).ensure_sync_job(
        "joulenap-r1",
        remote="joulenap-r1",
        remote_store="backup",
        store="offsite",
        direction="push",
    )

    # PBS's default sync listing hides push jobs, so without `all` an existing push job is
    # never seen, never deleted, and the create that follows fails with "job already exists"
    # on every run after the first.
    assert calls[0][:2] == ("GET", "/api2/json/config/sync")
    assert "sync-direction=all" in calls[0][2]
    assert calls[1][0] == "DELETE"


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
        def getpeercert(self, binary_form=False):
            return der

        def sendall(self, data):  # the GET / (#44)
            pass

        def recv(self, n):
            return b""

        def close(self):
            pass

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

    monkeypatch.setattr("socket.create_connection", lambda *a, **k: FakeSock())
    monkeypatch.setattr(
        "ssl.SSLContext.wrap_socket", lambda self, sock, server_hostname=None: FakeTLS()
    )
    assert get_fingerprint("pbs.local") == expected


# --- node status --------------------------------------------------------------
#
# FakePbs hands the dashboard a canned NodeLoad, so the real call and its unit conversions
# had never run. PBS reports cpu as a 0-1 fraction and memory in bytes; the header shows
# both as whole percentages.


def test_node_status_converts_cpu_fraction_and_memory_bytes_to_percentages():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/nodes/localhost/status")
        return json_data(
            {
                "cpu": 0.073,  # 7.3% -> 7
                # 90%, deliberately not a round third: a percentage computed against the
                # wrong scale lands on a different integer here rather than rounding back
                # onto the right answer.
                "memory": {"total": 10_000_000_000, "used": 9_000_000_000},
                "uptime": 3600,
            }
        )

    load = make_client(handler).node_status()

    assert (load.cpu, load.mem, load.uptime) == (7, 90, 3600)


def test_node_status_survives_a_node_reporting_no_memory():
    # Dividing by a zero total would 500 the whole status endpoint over a cosmetic figure.
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_data({"cpu": 0.0, "memory": {}, "uptime": 12})

    load = make_client(handler).node_status()

    assert (load.cpu, load.mem, load.uptime) == (0, 0, 12)


def test_node_status_raises_when_the_node_returns_nothing():
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_data(None)

    with pytest.raises(ApiError, match="node status"):
        make_client(handler).node_status()
