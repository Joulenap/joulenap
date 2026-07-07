"""Power-off the PBS over SSH.

PBS has no API for shutdown, so Joulenap runs a single command over SSH using a
dedicated key (ideally restricted to a forced poweroff command on the PBS side).
This module only connects and issues the command; the key is generated/installed by
the setup wizard (milestone 5).
"""

from __future__ import annotations

import logging

import paramiko

from .errors import PowerError

log = logging.getLogger("joulenap.power")

# The single command Joulenap runs on the PBS. It's both the default command this client
# sends and the forced command the SSH key is locked to (see sshkey._FORCED_COMMAND), so it
# lives here once and both paths stay in sync.
POWEROFF_COMMAND = "systemctl poweroff"


class PbsPower:
    def __init__(
        self,
        host: str,
        user: str = "root",
        key_path: str | None = None,
        port: int = 22,
        timeout: float = 10.0,
        poweroff_command: str = POWEROFF_COMMAND,
    ):
        self.host = host
        self.user = user
        self.key_path = key_path
        self.port = port
        self.timeout = timeout
        self.poweroff_command = poweroff_command

    def _connect(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        # Homelab PBS: accept the host key on first contact. (Host-key pinning could
        # be added later via a known_hosts file in the data dir.)
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                key_filename=self.key_path,
                timeout=self.timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except Exception as exc:  # paramiko/socket raise a variety of types
            client.close()
            raise PowerError(f"SSH connection to {self.user}@{self.host} failed: {exc}") from exc
        return client

    def test_connection(self) -> None:
        """Verify the key is accepted by opening (and closing) an authenticated SSH session.

        Deliberately does **not** run a command: the key is installed with a forced
        ``poweroff`` command, so any exec would shut the PBS down. A successful
        authentication is the most we can check without actually powering it off.
        Raises :class:`PowerError` on failure."""
        client = self._connect()
        client.close()

    def poweroff(self) -> None:
        """Issue the poweroff command. Fire-and-forget: the host drops the connection
        as it shuts down, so we don't wait for an exit status."""
        client = self._connect()
        try:
            client.exec_command(self.poweroff_command, timeout=self.timeout)
            log.info("Sent poweroff command to %s", self.host)
        except Exception as exc:
            raise PowerError(f"Poweroff command on {self.host} failed: {exc}") from exc
        finally:
            client.close()
