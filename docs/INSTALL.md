# Installing Joulenap

Joulenap runs as a small always-on container (or service) on your LAN. It wakes your normally-off
Proxmox Backup Servers, runs the backups, and powers them back down — so it needs to reach every
Proxmox VE host and backup server you want it to drive, and to send a Wake-on-LAN packet on each
backup server's network.

**Where does Joulenap run?** *Not* on the Proxmox VE host itself — you don't install it onto the PVE
node. It runs *beside* Proxmox as its own lightweight thing (an LXC container is the natural choice
on a Proxmox box) and talks to PVE and PBS over the network. This keeps the design promise of
touching nothing on the Proxmox host.

Pick the path that fits you:

- [Prerequisites](#prerequisites) — read this first (Wake-on-LAN especially)
- **[Option A — Proxmox LXC + Docker](#option-a--proxmox-lxc--docker-recommended)** *(recommended; every step, from scratch)*
- [Option B — Docker Compose](#option-b--docker-compose) *(existing Docker host, or to pre-edit config)*
- [Option C — Native install, no Docker](#option-c--native-install-no-docker) *(advanced)*
- [First run: create the account, add your devices, draw a route](#first-run) — same for every path
- [Updating](#updating)
- [Timezone](#timezone)

## Prerequisites

- At least one **Proxmox VE** host and one **Proxmox Backup Server**, reachable from where Joulenap
  runs. More of either is fine — that's what routes are for.
- **Wake-on-LAN enabled** on each backup server's network card — usually a BIOS/UEFI setting (often
  "Power On By PCIe/PCI" or "Wake on LAN") and, on some NICs, `ethtool -s <iface> wol g` on the PBS
  itself. Without it, Joulenap can't wake the box. **Confirm it actually wakes from a magic packet
  before relying on it** — the wizard has a Test button that sends one. (A backup server you keep
  powered on all the time needs none of this: turn its "Joulenap manages this box's power" switch
  off and it's treated as always available.)
- Joulenap on the **same LAN/broadcast domain** as the backup servers (so the WoL packet reaches
  them), on a host that stays on. Wake-on-LAN is a layer-2 broadcast — it does **not** cross subnets
  or routers.
- Keep the UI on your **LAN/VPN** and behind its login — it's not meant to face the internet.
- It's tiny: **1 vCPU, 512 MB RAM, 1–2 GB disk** is plenty.

You don't need to prepare API tokens or SSH keys by hand — the built-in **wizards** can create
scoped tokens and install the poweroff SSH key for you (see [First run](#first-run)).

---

## Option A — Proxmox LXC + Docker (recommended)

The simplest reliable path: a small Debian LXC on your Proxmox host, Docker inside it, then **one
command** to run Joulenap. No files to download, no config to edit by hand — the container creates
its own config and you fill it in through the web UI.

### 1. Create the LXC

In the Proxmox VE web UI:

1. If you don't have a Debian template yet: select your node → **local (storage)** → **CT Templates**
   → **Templates**, and download **debian-12-standard**.
2. Click **Create CT** (top right) and set:
   - **Hostname**: e.g. `joulenap`
   - **Password / SSH key**: set a root password you'll use for the container console
   - **Template**: the debian-12-standard you just downloaded
   - **Disk**: 2 GB · **Cores**: 1 · **Memory**: 512 MB
   - **Network**: bridge the LXC onto the **same VLAN/subnet as your PBS**, with an IPv4 address
     (DHCP is fine). This is the most important setting — if Joulenap can't broadcast onto the PBS's
     network, it can never wake it.
3. Finish the wizard but **don't start it yet** — do step 2 first.

### 2. Enable nesting (so Docker can run in the LXC)

Select the new container → **Options** → **Features** → **Edit** → tick **nesting** → **OK**.
Then **Start** the container.

### 3. Open a shell in the container

Select the container → **Console**, and log in as `root` with the password you set. (Or SSH into the
container's IP.)

### 4. Install Docker

Docker publishes an official one-line installer. In the container's shell:

```bash
apt update && apt install -y curl
curl -fsSL https://get.docker.com | sh
```

> **Seeing `perl: warning: Setting locale failed` messages?** Fresh Debian LXCs ship without a
> generated locale — the warnings are harmless and the install still succeeds. To silence them:
>
> ```bash
> update-locale LANG=C.UTF-8            # permanent, picked up by future logins
> export LANG=C.UTF-8 LC_ALL=C.UTF-8    # applies to the current shell right away
> ```

That's it — Docker now runs inside your LXC.

### 5. Run Joulenap

One command. It pulls the image from Docker Hub and starts it; the container seeds its own config on
first boot:

```bash
mkdir -p /opt/joulenap/data

docker run -d --name joulenap \
  --restart unless-stopped \
  --network host \
  -e TZ=Etc/UTC \
  -v /opt/joulenap/data:/app/data \
  catubba/joulenap:latest
```

What each line does:

- `--restart unless-stopped` — comes back automatically after a reboot.
- `--network host` — lets Joulenap send the Wake-on-LAN broadcast on your LAN (required).
- `-e TZ=Etc/UTC` — a neutral default; you'll pick your **actual** timezone on the first-run screen
  (it's pre-detected from your browser). Leave this as-is. See [Timezone](#timezone).
- `-v /opt/joulenap/data:/app/data` — the single folder that keeps **everything** that must survive
  updates: `config.yaml` (created automatically here on first run), the SQLite history, logs, and
  the generated SSH key.
- `catubba/joulenap:latest` — the image Docker pulls from Docker Hub. *This* is Joulenap; you never
  download it by hand.

### 6. Open the UI

Browse to `http://<container-ip>:8080` and continue at [First run](#first-run) below.

> **Prefer Compose over a long `docker run`?** See [Option B](#option-b--docker-compose) — same
> result, just written as a compose file.

---

## Option B — Docker Compose

Use this if you already run Docker somewhere, or you want to **pre-edit `config.yaml`** instead of
using the wizards. (On a fresh Proxmox host, do steps 1–4 of [Option A](#option-a--proxmox-lxc--docker-recommended)
first to get an LXC with Docker.)

```bash
mkdir -p joulenap && cd joulenap

# grab just the compose file (the app supplies its own config on first run)
curl -fsSL -o docker-compose.yml \
  https://raw.githubusercontent.com/Joulenap/joulenap/main/docker-compose.example.yml

docker compose up -d
# then open http://<host-ip>:8080
```

The [example compose](../docker-compose.example.yml) uses **host networking** so the Wake-on-LAN
broadcast reaches your LAN, and mounts a single writable **`./data`** directory that holds
`config.yaml`, the SQLite history, logs, and the SSH key. There is **no separate config file to
create** — it's seeded into `./data` on first run.

**Want to pre-fill config instead of using the wizards?** Start the stack once so it seeds
`./data/config.yaml`, stop it (`docker compose down`), edit `./data/config.yaml` (every field is
documented in [`config.example.yaml`](../config.example.yaml)), then bring it back up. You can also
pre-hash the admin password (see [First run](#first-run)).

---

## Option C — Native install, no Docker

For those who'd rather not use Docker — run Joulenap directly as a Python service. This is more
manual: you build the frontend once and run the backend under systemd. A small Debian/Ubuntu LXC (or
any always-on Linux host on the PBS's LAN) works well.

**You need:** Python **3.12+**, `git`, and Node.js **24** (only to build the web UI once — 24 is what
CI and the container image build with, so it's the combination that's actually tested).

```bash
# 1. get the source (into /opt/joulenap so the paths below line up)
mkdir -p /opt/joulenap && cd /opt/joulenap
git clone https://github.com/Joulenap/joulenap.git
cd joulenap

# 2. build the web UI (produces frontend/dist)
cd frontend
npm ci
npm run build
cd ..

# 3. install the backend into a virtualenv
#    (editable install: the package references ../README.md, which only resolves from an
#    in-tree build — a plain `pip install ./backend` fails under pip's isolated build)
python3 -m venv .venv
.venv/bin/pip install -e ./backend

# 4. pick a data directory (holds config.yaml, history, logs, ssh key)
mkdir -p /opt/joulenap/data
```

Run it once by hand to check it boots (Ctrl-C to stop):

```bash
JOULENAP_DATA_DIR=/opt/joulenap/data \
JOULENAP_CONFIG=/opt/joulenap/data/config.yaml \
JOULENAP_FRONTEND_DIR="$PWD/frontend/dist" \
.venv/bin/joulenap
```

Then open `http://<host-ip>:8080`. Once it works, install it as a service so it starts on boot.
Create `/etc/systemd/system/joulenap.service`:

```ini
[Unit]
Description=Joulenap
After=network-online.target
Wants=network-online.target

[Service]
# Paths assume the repo was cloned to /opt/joulenap/joulenap (step 1 above).
WorkingDirectory=/opt/joulenap/joulenap
Environment=JOULENAP_DATA_DIR=/opt/joulenap/data
Environment=JOULENAP_CONFIG=/opt/joulenap/data/config.yaml
Environment=JOULENAP_FRONTEND_DIR=/opt/joulenap/joulenap/frontend/dist
Environment=TZ=Etc/UTC
ExecStart=/opt/joulenap/joulenap/.venv/bin/joulenap
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now joulenap
```

> Wake-on-LAN sends a broadcast, so run this on a host with direct LAN broadcast access to the PBS's
> subnet. `network_mode: host` isn't a concept here — a native process already uses the host network.

---

## First run

Once the UI is up at `http://<host>:8080` (same for every install path):

1. **Create the admin account** (username + a password of **at least 8 characters**) and **confirm
   your timezone**. This is a one-time registration. The timezone dropdown is pre-filled from your
   browser, so usually you just leave it — it sets `app.timezone` so your backup schedule runs in
   your local time (you can change it later under **Settings → Account**).
   - *Prefer to pre-seed the account?* Generate a bcrypt hash and put it in `app.auth.password_hash` in
     `config.yaml`:
     ```bash
     # Docker:
     docker exec -it joulenap python -m app.hashpw
     # Native: from the repo, with the venv active:
     python -m app.hashpw
     ```
2. **Add your devices.** Go to **Settings → Devices → + Add** (the banner on a fresh install links
   straight there) and run one of the two wizards:
   - **Add a Proxmox VE**: connect, and Joulenap reads that host's storage config to find the
     backup servers it already knows about. Any that you've already registered are linked for you;
     a new one can be configured inline, which folds the PBS wizard into the same run.
   - **Add a Proxmox Backup Server**: connect (the certificate fingerprint is filled in for you),
     then set up wake-up (WoL interface, MAC detection, a Test button that sends a real magic
     packet) and power-off (the SSH key, and confirming the box's SSH host key).

   Both default to **API-token mode** — you paste scoped tokens and no credentials leave your
   server. Both also offer a **root mode** that provisions the tokens and installs the SSH key for
   you, using the password once and never storing it. See
   [`CONFIG-WIZARD.md`](CONFIG-WIZARD.md) for the full field-by-field breakdown.
3. **Create a route** from the homepage: pick the source host(s) and the target backup server, a
   time and the days, which guests to include, and the retention. That's what actually gets
   scheduled. Configure **notifications** (Telegram / ntfy / email / Discord) under Settings if you
   want them, then use the route's **Run now** to test the whole wake → backup → power-off cycle
   end-to-end while you're watching.

## Around the interface

Day-to-day you'll live on the **homepage**: the topology of your hosts and backup servers, the
route strip (where you create, edit, pause and manually run routes), what's coming up next, the run
history with its per-step timeline and live task output, and the per-server power and GC/verify
buttons. Everything else sits behind **Settings**:

- **Devices** — every Proxmox host and backup server as a card, with the two **+ Add** wizards, an
  edit modal for each, and a connection **Test**. Removing a device that a route still uses is
  refused, naming the routes. Re-run a device's connect step whenever its certificate is renewed or
  its address changes.
- **Account** — the admin username and password (changing the password signs out every existing
  session immediately), plus interface language and the timezone your schedules are interpreted in.
- **Notifications** — Telegram, ntfy, email (SMTP) and Discord with friendly forms, plus a
  catch-all list for any other [Apprise](https://github.com/caronc/apprise) URL or plain webhook.
  Choose whether to notify on success, on failure, or both; **Send test** reports per channel.
- **Integrations** — generate the read-only API key for the dashboard endpoint and copy a
  ready-made snippet for Homepage / Homarr / Dashy / Glance, plus the Prometheus scrape config
  (details in [`INTEGRATIONS.md`](INTEGRATIONS.md)).
- **Advanced** — the global **pause switch** for every route, the application settings (web port,
  session lifetime, how long run history is kept, and the opt-in update check — off by default, and
  while it's off Joulenap makes no outbound internet call at all; the port and session ones need a
  restart), and a **`config.yaml` editor** with syntax highlighting. The editor saves through
  exactly the same validation as the forms, so a bad value is rejected instead of persisted, and
  **Export** downloads the whole config with the secrets redacted — handy for a bug report, though
  not a restorable backup for the same reason.

The per-route knobs — backup mode (snapshot / suspend / stop), a vzdump bandwidth cap, the
minimum-free-space check that aborts rather than backing up onto a nearly-full datastore, garbage
collection and verification after the run — live in the **Advanced section of the route editor**,
because each route sets them for itself. Wake timeout, Wake-on-LAN retries and the external-watch
timeouts belong to a backup server, and live on its device card.

## Updating

Your `config.yaml` and data live in the mounted `data/` directory (or your native data dir), so they
carry over across updates.

```bash
# Option A (docker run)
docker pull catubba/joulenap:latest
docker rm -f joulenap
# then re-run the same `docker run ...` command from Option A step 5

# Option B (compose)
docker compose pull && docker compose up -d

# Option C (native)
cd /opt/joulenap/joulenap && git pull
cd frontend && npm ci && npm run build && cd ..
.venv/bin/pip install -e ./backend
systemctl restart joulenap
```

## Timezone

Your backup schedule is interpreted in a specific timezone — so "02:00" runs at 02:00 *there*. The
easy path: **the first-run screen detects your timezone from your browser and sets it for you**
(saved as `app.timezone`), so you don't need to touch anything. You can change it any time under
**Settings → Account**.

If you'd rather set it outside the UI, the order of precedence is:

1. `app.timezone` in `config.yaml` (what the first-run screen and Localization page write) — wins.
2. the `TZ` environment variable on the container (e.g. `TZ=Europe/Rome`) — the fallback the example
   commands leave at `Etc/UTC`.
3. **UTC**, if neither is set.

Use full IANA names (e.g. `Europe/Rome`, `America/New_York`); an unrecognized name falls back to UTC.
