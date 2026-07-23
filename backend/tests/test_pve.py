"""PVE client tests using an in-memory httpx MockTransport (no real PVE)."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.connectors.errors import ApiError, TaskCancelled, TaskError
from app.connectors.pve import PveClient, build_prune_string


def make_client(handler, **kwargs) -> PveClient:
    transport = httpx.MockTransport(handler)
    return PveClient(
        host="pve.local",
        node="pve",
        token_id="root@pam!joulenap",
        token_secret="secret",
        transport=transport,
        **kwargs,
    )


def json_data(payload):
    return httpx.Response(200, json={"data": payload})


def test_build_prune_string():
    assert build_prune_string({"keep_daily": 7, "keep_weekly": 4, "keep_monthly": 6}) == (
        "keep-daily=7,keep-weekly=4,keep-monthly=6"
    )
    assert build_prune_string({"keep_last": 0, "keep_daily": 0}) is None


def test_auth_header_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return json_data({"version": "8.2.4"})

    make_client(handler).version()
    assert seen["auth"] == "PVEAPIToken=root@pam!joulenap=secret"


def test_list_guests_merges_and_sorts():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/qemu"):
            return json_data([
                {"vmid": 201, "name": "win-server", "status": "running"},
                {"vmid": 200, "name": "home-assistant", "status": "running"},
            ])
        if request.url.path.endswith("/lxc"):
            return json_data([{"vmid": 100, "name": "docker-host", "status": "running"}])
        return httpx.Response(404)

    guests = make_client(handler).list_guests()
    assert [g.vmid for g in guests] == [100, 200, 201]
    assert guests[0].type == "lxc" and guests[0].is_ct
    assert guests[1].type == "qemu" and not guests[1].is_ct


def test_vzdump_builds_params_and_returns_upid():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = parse_qs(request.content.decode())
        return json_data("UPID:pve:0001:vzdump::")

    upid = make_client(handler).vzdump(
        storage="pbs",
        vmids=[100, 200],
        mode="snapshot",
        prune_backups="keep-daily=7",
        bwlimit=51200,
    )
    assert upid.startswith("UPID:")
    body = captured["body"]
    assert body["storage"] == ["pbs"]
    assert body["vmid"] == ["100,200"]
    assert body["mode"] == ["snapshot"]
    assert body["prune-backups"] == ["keep-daily=7"]
    assert body["bwlimit"] == ["51200"]


def test_vzdump_all_guests():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = parse_qs(request.content.decode())
        return json_data("UPID:pve:0002:vzdump::")

    make_client(handler).vzdump(storage="pbs", all_guests=True)
    assert captured["body"]["all"] == ["1"]
    assert "vmid" not in captured["body"]


def test_wait_task_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_data({"status": "stopped", "exitstatus": "OK"})

    status = make_client(handler).wait_task("UPID:x", poll_interval=0, sleep=lambda _s: None)
    assert status["exitstatus"] == "OK"


def test_wait_task_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_data({"status": "stopped", "exitstatus": "job errors"})

    with pytest.raises(TaskError):
        make_client(handler).wait_task("UPID:x", poll_interval=0, sleep=lambda _s: None)


def test_wait_task_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_data({"status": "running"})

    with pytest.raises(TaskError):
        make_client(handler).wait_task(
            "UPID:x", poll_interval=0, timeout=0, sleep=lambda _s: None
        )


def test_wait_task_cancels_without_waiting_for_the_task():
    """A cancel flag breaks the wait even though the task is still running (11.2)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_data({"status": "running"})

    with pytest.raises(TaskCancelled):
        make_client(handler).wait_task(
            "UPID:x",
            poll_interval=0,
            sleep=lambda _s: None,
            should_cancel=lambda: True,
        )


def test_wait_task_keeps_waiting_while_cancel_is_false():
    # The probe must be consulted per poll, not once — a False mustn't end the wait.
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        polls["n"] += 1
        if polls["n"] < 3:
            return json_data({"status": "running"})
        return json_data({"status": "stopped", "exitstatus": "OK"})

    status = make_client(handler).wait_task(
        "UPID:x", poll_interval=0, sleep=lambda _s: None, should_cancel=lambda: False
    )
    assert status["exitstatus"] == "OK"
    assert polls["n"] == 3


def test_stop_task_deletes_the_task():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return json_data(None)

    make_client(handler).stop_task("UPID:x")
    assert seen["method"] == "DELETE"
    assert seen["path"].endswith("/nodes/pve/tasks/UPID:x")


def test_task_log_parses_offset_lines():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/tasks/UPID:x/log")
        seen["qs"] = parse_qs(request.url.query.decode())
        return json_data([{"n": 3, "t": "third"}, {"n": 4, "t": "fourth"}])

    lines = make_client(handler).task_log("UPID:x", start=2)
    assert lines == [(3, "third"), (4, "fourth")]
    assert seen["qs"]["start"] == ["2"]


def test_wait_task_tails_log_across_ticks():
    """The tailer drains new lines each poll (advancing the offset) and catches the tail."""
    full = ["INFO: start", "VM 100: 50%", "VM 100: done", "INFO: finished"]
    state = {"status_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/status"):
            state["status_calls"] += 1
            stopped = state["status_calls"] >= 3
            body = {"status": "stopped", "exitstatus": "OK"} if stopped else {"status": "running"}
            return json_data(body)
        if path.endswith("/log"):
            start = int(parse_qs(request.url.query.decode())["start"][0])
            # The log grows as the task runs: two more lines revealed per status poll.
            avail = min(len(full), state["status_calls"] * 2)
            return json_data([{"n": i + 1, "t": full[i]} for i in range(start, avail)])
        return httpx.Response(404)

    collected: list[tuple[int, str]] = []
    make_client(handler).wait_task(
        "UPID:x",
        poll_interval=0,
        sleep=lambda _s: None,
        on_log=lambda batch: collected.extend(batch),
    )
    # Every line captured once, in order, with no duplicates.
    assert [n for n, _ in collected] == [1, 2, 3, 4]
    assert [t for _, t in collected] == full


def test_http_error_becomes_apierror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid token")

    with pytest.raises(ApiError) as exc:
        make_client(handler).version()
    assert exc.value.status == 401
