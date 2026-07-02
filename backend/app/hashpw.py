"""Generate a bcrypt password hash for ``app.auth.password_hash`` in config.yaml.

Run it when you want to pre-seed the admin password without going through the
first-run UI:

    python -m app.hashpw

It prompts (twice, hidden) and prints the ``$2b$...`` hash to paste into config.
For non-interactive use (CI, scripting) the password can be piped on stdin:

    echo -n 'secret' | python -m app.hashpw --stdin

Uses the same ``hash_password`` helper as the running app, so bcrypt's 72-byte
truncation behaves identically to logins.
"""

from __future__ import annotations

import getpass
import sys

from .core.security import hash_password


def _read_password() -> str:
    """Prompt twice (hidden) and return the password once both entries match."""
    first = getpass.getpass("Password: ")
    if not first:
        print("Password must not be empty.", file=sys.stderr)
        raise SystemExit(1)
    second = getpass.getpass("Confirm password: ")
    if first != second:
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    return first


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if "--stdin" in argv:
        # Read exactly one password from stdin; strip a single trailing newline
        # but preserve any other whitespace the user intentionally included.
        password = sys.stdin.readline().rstrip("\n").rstrip("\r")
        if not password:
            print("No password received on stdin.", file=sys.stderr)
            return 1
    else:
        password = _read_password()

    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
