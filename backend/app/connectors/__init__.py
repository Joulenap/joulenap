"""Connectors: thin clients for the external systems Joulenap drives.

- ``wol``   — Wake-on-LAN magic packet on the LAN.
- ``power`` — SSH to PBS for poweroff (no API for that).
- ``pve``   — Proxmox VE API (list guests, trigger vzdump, task status).
- ``pbs``   — Proxmox Backup Server API (datastore status, garbage collection).
- ``net``   — small TCP reachability helper (used to wait for PBS to wake).

Each raises a subclass of :class:`errors.ConnectorError` so callers (the backup
cycle, the wizard, the API) can handle failures uniformly.
"""
