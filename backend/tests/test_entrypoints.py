"""The two module entry points the image and the docs actually invoke.

Neither is imported by the app, so neither had a single line of coverage, and both are
shipped: ``python -m app.healthcheck`` is the Docker HEALTHCHECK, and
``python -m app.hashpw`` is what INSTALL.md tells users to run to pre-seed a password.
A broken healthcheck marks a working container unhealthy; a broken hashpw prints a hash
that will not log in.
"""

from __future__ import annotations

import io
import sys
from contextlib import contextmanager

import pytest

from app import hashpw, healthcheck
from app.core.security import verify_password

# --- healthcheck --------------------------------------------------------------


class _Resp:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@contextmanager
def _urlopen(monkeypatch, result):
    """Stand in for urllib.request.urlopen; ``result`` is a status or an exception."""
    seen: dict = {}

    def fake(url, timeout=None):
        seen.update(url=url, timeout=timeout)
        if isinstance(result, Exception):
            raise result
        return _Resp(result)

    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", fake)
    yield seen


def test_a_200_from_the_local_api_is_healthy(monkeypatch):
    monkeypatch.setattr(healthcheck, "_port", lambda: 8080)
    with _urlopen(monkeypatch, 200) as seen:
        assert healthcheck.main() == 0
    # Localhost on purpose: the probe runs inside the container, and must not depend on
    # whatever host or reverse proxy the user put in front of it.
    assert seen["url"] == "http://127.0.0.1:8080/api/health"


def test_a_non_200_is_unhealthy(monkeypatch):
    monkeypatch.setattr(healthcheck, "_port", lambda: 8080)
    with _urlopen(monkeypatch, 503):
        assert healthcheck.main() == 1


def test_an_unreachable_api_is_unhealthy_rather_than_a_traceback(monkeypatch):
    # Docker reads the exit code; an exception escaping here would look like a crashed
    # probe rather than an unhealthy container.
    monkeypatch.setattr(healthcheck, "_port", lambda: 8080)
    with _urlopen(monkeypatch, OSError("connection refused")):
        assert healthcheck.main() == 1


def test_the_probe_follows_the_configured_port(monkeypatch, temp_config):
    from app.config import load_config, save_config

    cfg = load_config(temp_config)
    cfg.app.port = 9099
    save_config(cfg, temp_config)

    with _urlopen(monkeypatch, 200) as seen:
        assert healthcheck.main() == 0
    assert ":9099/" in seen["url"]


def test_an_unreadable_config_falls_back_to_the_default_port(monkeypatch, tmp_path):
    # A config the app cannot parse still leaves a container that may be serving on 8080,
    # so the probe guesses rather than failing outright.
    monkeypatch.setenv("JOULENAP_CONFIG", str(tmp_path / "missing.yaml"))
    assert healthcheck._port() == 8080


# --- hashpw -------------------------------------------------------------------


def test_stdin_mode_prints_a_hash_that_actually_verifies(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("hunter22\n"))

    assert hashpw.main(["--stdin"]) == 0

    printed = capsys.readouterr().out.strip()
    assert printed.startswith("$2b$")
    # The point of the tool: the hash it prints has to be one login accepts.
    assert verify_password("hunter22", printed) is True
    assert verify_password("wrong", printed) is False


def test_stdin_mode_keeps_whitespace_that_is_part_of_the_password(monkeypatch, capsys):
    # Only the line ending is stripped: a password with a trailing space is a valid one,
    # and silently trimming it would print a hash the user can never log in with.
    monkeypatch.setattr(sys, "stdin", io.StringIO("  spaced pass  \r\n"))

    assert hashpw.main(["--stdin"]) == 0

    assert verify_password("  spaced pass  ", capsys.readouterr().out.strip()) is True


def test_stdin_mode_refuses_an_empty_password(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))

    assert hashpw.main(["--stdin"]) == 1

    assert "No password" in capsys.readouterr().err


def test_the_prompt_path_requires_both_entries_to_match(monkeypatch):
    entries = iter(["secret12", "secret13"])
    monkeypatch.setattr(hashpw.getpass, "getpass", lambda _prompt: next(entries))

    with pytest.raises(SystemExit) as exit_info:
        hashpw.main([])

    assert exit_info.value.code == 1


def test_the_prompt_path_refuses_an_empty_password(monkeypatch):
    monkeypatch.setattr(hashpw.getpass, "getpass", lambda _prompt: "")

    with pytest.raises(SystemExit) as exit_info:
        hashpw.main([])

    assert exit_info.value.code == 1


def test_the_prompt_path_prints_a_verifying_hash(monkeypatch, capsys):
    monkeypatch.setattr(hashpw.getpass, "getpass", lambda _prompt: "secret12")

    assert hashpw.main([]) == 0

    assert verify_password("secret12", capsys.readouterr().out.strip()) is True
