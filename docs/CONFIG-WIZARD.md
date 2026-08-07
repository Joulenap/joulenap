# Joulenap — Setup wizard & field discovery

How devices are configured. The goal: ask as little as possible by **discovering** most values automatically. The wizard may ask for **root credentials** of a PVE (and optionally of a PBS); with those it creates its own scoped tokens and installs its SSH key, then discards the passwords.

The wizard adds **devices**, one at a time — it never creates routes. Once a PVE and a PBS exist, you build routes between them from the homepage. Both flows open from **Settings → Devices → + Add** (or from the first-run banner).


## Flow A — Add a PVE

1. **Connect.** Host/IP, port (8006), and either an **API token** (id + secret) or **root credentials**. In root mode Joulenap creates a minimal-privilege token for itself and discards the password. TLS verification defaults off, because a stock PVE serves a self-signed certificate. You also pick the device **name** (its id, used everywhere in the UI); it is pre-filled and you can usually leave it.
2. **Discovery.** Joulenap reads the PVE's storage config and lists every **PBS-backed storage** on it, deriving each one's host, port, datastore and certificate fingerprint. Each storage falls into one of two groups:
   - it matches a PBS you already registered (same host + datastore) → it is **linked automatically**, filling that PVE's `storages` map so backup routes to that box become possible. Only storages that exist on the PVE *at this moment* are linked; register a backup server later and you complete the map with **Re-read from Proxmox** (below) rather than by re-running this wizard, which refuses a host it already knows;
   - it is a backup server Joulenap has never seen → you can **configure it now**, which folds flow B's PBS steps into this run. One per pass; add the rest from Settings → Devices → + Add.
   The number of nodes is reported here too — that is how a cluster is detected, and it is why a PVE has no `node` field to fill in.
3. **Configure the new PBS** (skipped when there is nothing new): the PBS half of flow B, below.
4. **Finish.** The devices are written and the report lists what was created and which storages were linked.


## Flow B — Add a PBS

1. **Connect.** Host/IP, port (8007), datastore, and an API token — or root credentials, from which Joulenap provisions a scoped token. The certificate **fingerprint is read from the box and filled in for you** on connect; from then on every API call is pinned to it. Also here: the device name, and **"Joulenap manages this box's power"**. Turn that off for an always-on PBS — steps 2 and 3 then do not apply and are skipped.
2. **Wake-up.** The **WoL broadcast interface** (leave it on `auto` unless you have several networks) and the **MAC address**, detected by opening a connection to the box and reading the ARP table — so the PBS must be *powered on* while you set it up. A **Test** button sends one real magic packet and names the broadcast address it used: Wake-on-LAN left un-armed in the BIOS is the single most common cause of a failed run, and finding that out at 04:00 is the worst way.
3. **Power-off.** Joulenap needs SSH to the box, because PBS has no power-off API.
   - **The key is get-or-create.** All managed PBSs share one `data/id_ed25519`, so the wizard returns the existing public key rather than generating a new one — regenerating would silently orphan the `authorized_keys` line on the box you added first, and nothing in the UI would tell you.
   - **The host key is confirmed first.** The wizard scans the PBS SSH host key, shows its SHA256 fingerprint, and only saves it to `data/known_hosts` once you confirm. Every later connection verifies against it. Automatic key installation refuses to run before this, because that is when a root password would be sent over the connection.
   - With root credentials the public key is installed for you; otherwise paste the shown line into `/root/.ssh/authorized_keys` on the PBS yourself. It is a **restricted** line with a forced command that only allows the power-off.
4. **Verification.** A report of what was configured, plus a warning if this PBS is not the target of any PVE storage — a box nothing can back up to yet.


## Field discovery

For each field: **auto** = discovered/derived, **manual** = entered.

| Field | | Notes |
|---|---|---|
| PVE host/IP, port | manual | the entry point |
| PVE TLS verify | auto | off when the certificate is self-signed |
| PVE auth | manual | a token, or root once → token created and the password discarded |
| PVE → PBS storage map | auto | from `/storage` filtered to `type=pbs`; you pick which to link or configure |
| PBS host/IP, port | auto | read from the PVE storage config (manual in flow B) |
| PBS datastore | auto | from the storage config |
| PBS fingerprint | auto | from the storage config, or read from the PBS certificate on connect |
| WoL broadcast interface | auto | the NIC with the route to the PBS subnet; override allowed |
| PBS MAC | auto | connect + read ARP, with the PBS powered on ("Detect MAC") |
| PBS API token | manual, or auto in root mode | see the privileges below |
| PBS SSH host key | auto | scanned, shown, saved to `data/known_hosts` on your confirmation |
| SSH user + key | mixed | user defaults to `root`; the key is Joulenap's own, installed for you in root mode |
| Wake-on-LAN in the PBS BIOS/OS | **manual, always** | `ethtool -s <nic> wol g`, made persistent |


## If you decline root credentials

Everything above is still discovered with a read-only token; only these become manual:

- **On the PVE**: create an API token whose role has `VM.Audit, VM.Backup, Datastore.Audit, Datastore.AllocateSpace, Datastore.Allocate` (the last is required for vzdump's retention/prune, which deletes old backups); copy the secret.
- **On the PBS**: create an API token with `DatastoreAdmin` on the datastore (status, GC, verify) **and `Audit` on `/system`** (node CPU/RAM for the dashboard); copy the secret. Name it per datastore — Joulenap's own wizard uses `joulenap-<datastore>` — so a second datastore on the same box can have its own. Add the `/remote` roles below if this box will take part in a sync route.
- **On the PBS**: install Joulenap's generated SSH public key into `/root/.ssh/authorized_keys`.
- **In Joulenap**: paste both tokens, confirm the key is installed, click "Detect MAC".

> **Tighter PBS privileges (optional):** root mode grants the built-in `DatastoreAdmin`, because PBS cannot create custom roles over the API. For a truly minimal token, create a role on the PBS host once and bind a token to it, then paste that token:
> ```sh
> proxmox-backup-manager role create Joulenap --privs "Datastore.Audit,Datastore.Modify"
> proxmox-backup-manager user generate-token root@pam joulenap-<datastore>
> proxmox-backup-manager acl update /datastore/<datastore> Joulenap --auth-id 'root@pam!joulenap-<datastore>'
> ```

> **Token names on a backup server carry the datastore** — `joulenap-backup`, `joulenap-offsite`.
> A backup server can hold several datastores, and each is its own device in Joulenap; naming both
> tokens the same would mean setting up the second one deleted and recreated the first one's token,
> leaving that device unable to connect. A Proxmox host is a single device, so its token stays
> plain `joulenap`.
>
> **Replacing a token also clears the permissions granted to it.** If you ever confirm a
> replacement, re-entering the new secret is not always enough: provisioning re-grants only
> `/datastore/<that device's datastore>`, so a hand-made setup where one token served several
> datastores needs the `acl update` above re-running as root for the others.


## Adding a box you already have

An API token belongs to one server: `root@pam!joulenap` on one machine has nothing to do with a
token of the same name on another. **Adding a second Proxmox host or a second backup server
therefore cannot disturb the ones you already have** — different box, different token.

The one thing that can go wrong is pointing a wizard at a box that *already* has a token by that
name, because a token's secret exists only at creation: the only way to get a usable one under a
name in use is to replace it, and every other holder of the old secret breaks silently.

Joulenap now heads that off from both sides:

- **The same box, already registered** is refused at the connection step, before any password or
  token is sent, naming the device you already have. Adding it again would have replaced its
  token and left the original entry unable to connect. Edit that device instead. (A backup
  server serving a *second datastore* is a legitimate second device and is still allowed — it
  shares a token with the first, so the warning below applies to it.)
- **The same box, used by something else**: replacing the token asks first, and the confirmation
  names what Joulenap can see would break — another of its own devices on that host, and any
  Proxmox host whose PBS storage entry authenticates with it. That last one is the dangerous
  case: the storage keeps its old secret and every backup through it starts failing with 401,
  with nothing pointing at the token as the cause.

Joulenap cannot see *every* holder of a token — a script of your own, another tool, a second
Proxmox host it does not manage. If you have any of those, use **Existing API token** and paste
the secret you already have, or give Joulenap a token of its own under a different name.


## A backup server added after its Proxmox host

`pves[].storages` — which PVE storage points at which backup server — is discovered, never
typed. Flow A fills it in from the PVE's storage config while the wizard is open, so a server
you register *afterwards* (all of flow B, by definition) starts out unlinked: nothing on that
PVE knows how to reach it, and **Backup routes onto it cannot be created**. Sync, Verify and
External routes are unaffected — they never go through a PVE storage.

Completing it takes two steps, in this order:

1. Add the storage on the Proxmox side (Datacenter → Storage → Add → Proxmox Backup Server),
   pointing at the same host and datastore as the Joulenap device.
2. **Settings → Devices → edit the PVE → Storage mapping → Re-read from Proxmox.**

Joulenap re-reads the host's storage list with the token it already has and rebuilds the map,
matching each storage to a registered server by host *and* datastore. The map is replaced, not
merged, so a storage you removed on the Proxmox side disappears here too — and if that would
leave an existing route without a mapping the save is refused and nothing changes.

Re-running the Add-PVE wizard is **not** the way to do this: it only ever creates, so it refuses a
host that is already registered. The refusal happens at the connection step, before any credentials
leave the browser and before anything is provisioned.


## Sync routes need one extra grant

A **sync** route makes Joulenap create a *remote* and a *sync job* on one of the two boxes, which needs privileges at `/remote` that a plain datastore token does not have. Root-mode provisioning grants them while it still holds the root ticket.

**PBS refuses ACL writes from an API token**, answering *400 Unprivileged API tokens can't set ACL items*. So a box whose token you pasted by hand — or one configured with a Joulenap older than 1.0 — does not have them, and its sync routes will fail until it does. Because only root can make the grant, adding it takes a root login one more time:

**Settings → Devices → edit the PBS → Sync routes → Grant sync permissions.** Enter the PBS root credentials and confirm. Joulenap logs in as root, adds the two roles to the token the device already uses, and discards the password — the token itself is untouched, so nothing else needs re-entering.

The equivalent on the PBS itself, if you would rather not hand the password over:

```sh
proxmox-backup-manager acl update /remote RemoteAdmin --auth-id 'root@pam!joulenap-<datastore>'
proxmox-backup-manager acl update /remote RemoteSyncPushOperator --auth-id 'root@pam!joulenap-<datastore>'
```

Both roles are needed: `RemoteAdmin` alone does not cover a *push* sync.


## Security

Transport security uses pinning and verification to protect credentials in transit:

- **PBS API (TLS pinning):** every PBS's API calls are pinned to that device's stored certificate fingerprint, captured on connect. If a certificate is renewed, re-run the connect step for that device to store the new fingerprint; calls fail with a clear "fingerprint changed" error until you do.
- **PBS SSH (host-key verification):** each PBS's SSH host key is confirmed once during setup and saved to `data/known_hosts`. All later connections verify against it.
- **PVE setup (residual):** a PVE root password used for provisioning is protected by that device's `verify_tls` only — no fingerprint is stored. Enable it if your PVE has a valid certificate, or keep the setup network trusted (isolated LAN/VPN).
- **Root credentials are never stored.** They are used for the one call that needs them (mint a token, install a key) and discarded with the modal.
