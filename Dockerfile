# syntax=docker/dockerfile:1
#
# Joulenap image: build the SPA, then run FastAPI serving both the API and the built SPA.
# Build from the repo root:  docker build -t catubba/joulenap:latest .

# --- Stage 1: build the SPA (Vite -> dist) -----------------------------------
# Debian (not alpine/musl) to avoid esbuild native-binary edge cases; this stage is
# discarded, so it doesn't affect the final image size.
#
# Keep this major in sync with `node-version` in .github/workflows/ci.yml. The Node
# major picks the bundled npm (22 -> npm 10, 24 -> npm 11), and the two npm majors
# disagree on whether a violated *optional* peer dependency is fatal. With CI on 24
# and this stage on 22, `npm ci` could pass every CI job and still fail the image
# build on the very same lockfile.
FROM node:24-slim AS frontend
WORKDIR /build
# Install deps first (cached until the lockfile changes), then build.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # tsc --noEmit && vite build -> /build/dist

# --- Stage 2: runtime (Python + FastAPI) -------------------------------------
FROM python:3.12-slim AS runtime

# Pin the runtime paths to /app so they don't depend on where the package is installed
# (app/paths.py otherwise derives a fallback from __file__, which lands in site-packages).
# config.yaml lives *inside* the data dir so a single directory mount (-v ...:/app/data)
# persists config + history + ssh key together — no separate single-file config mount, and
# the container self-seeds config.yaml from config.example.yaml on first run.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JOULENAP_CONFIG=/app/data/config.yaml \
    JOULENAP_DATA_DIR=/app/data \
    JOULENAP_FRONTEND_DIR=/app/frontend/dist

WORKDIR /app

# Install the backend package and its dependencies from pyproject. hatchling reads the
# README referenced as "../README.md", so it must be present in the build context.
# The cryptography/bcrypt/psutil wheels are manylinux prebuilds, so no compiler is needed.
COPY backend/ /src/backend/
COPY README.md /src/README.md
RUN pip install /src/backend && rm -rf /src

# Runtime files at the paths app/paths.py expects: /app/data is mounted by the user and
# holds config.yaml + history + ssh key; config.example.yaml is the first-run seed that
# ConfigStore copies to /app/data/config.yaml when it's missing.
COPY config.example.yaml /app/config.example.yaml
COPY --from=frontend /build/dist /app/frontend/dist
RUN mkdir -p /app/data

# NOTE: runs as root by design — data/ is bind-mounted from the host and the app writes to
# it (wizard saves, secret_key seeding, generated SSH key, config.yaml itself), so a fixed
# non-root UID would hit host-ownership mismatches. The UI is LAN/VPN-only (see README).

# Documentation only; the real bind port is app.port in config.yaml (default 8080).
EXPOSE 8080

# Probes /api/health on the configured port (reads app.port itself).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

CMD ["joulenap"]
