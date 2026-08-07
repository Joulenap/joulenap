"""The liveness stamp behind the missed-run check (G3-6)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from app.core import heartbeat


def test_last_seen_is_none_before_the_first_touch(tmp_path, monkeypatch):
    monkeypatch.setenv("JOULENAP_DATA_DIR", str(tmp_path))
    assert heartbeat.last_seen() is None


def test_touch_records_now(tmp_path, monkeypatch):
    monkeypatch.setenv("JOULENAP_DATA_DIR", str(tmp_path))
    before = datetime.now(UTC)

    heartbeat.touch()

    seen = heartbeat.last_seen()
    assert seen is not None
    assert seen.tzinfo is not None  # compared against aware fire times
    # Filesystem timestamps can round down a little; a second of slack is plenty.
    assert abs((seen - before).total_seconds()) < 60


def test_touch_advances_a_stale_stamp(tmp_path, monkeypatch):
    # The stamp a crashed process left behind must not keep reading as "long ago" once the
    # app is up again, or every restart would re-report the same window as downtime.
    monkeypatch.setenv("JOULENAP_DATA_DIR", str(tmp_path))
    heartbeat.touch()
    os.utime(tmp_path / ".heartbeat", (0, 0))  # pretend a process died in 1970
    stale = heartbeat.last_seen()
    assert stale is not None and stale.year == 1970

    heartbeat.touch()

    fresh = heartbeat.last_seen()
    assert fresh is not None and fresh.year > 2000


def test_an_unwritable_data_dir_does_not_raise(tmp_path, monkeypatch):
    # A read-only mount must cost the missed-run check, not the whole app.
    monkeypatch.setenv("JOULENAP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.core.heartbeat.paths.heartbeat_path",
        lambda: tmp_path / "no-such-dir" / ".heartbeat",
    )

    heartbeat.touch()  # must not raise

    assert heartbeat.last_seen() is None
