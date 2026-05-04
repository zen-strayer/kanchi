# Dockerfile Optimization Design

## Goal

Reduce CI build times and final image size by restructuring the Dockerfile into four stages that maximize cache reuse, eliminate dead work, and keep build-time tooling out of the runtime image.

## Architecture

Four build stages:

1. **`frontend-builder`** (`node:20-alpine`) — installs npm deps and runs the Nuxt build. Produces `.output/`. Unchanged from current.
2. **`node-provider`** (`node:20-slim`) — a donor-only stage. Exists solely to supply the Node.js binary without nodesource curl gymnastics.
3. **`python-builder`** (`python:3.12-slim`) — installs build-time system packages (`gcc`, `default-libmysqlclient-dev`, `pkg-config`) and all Python dependencies including optional DB extras.
4. **`runtime`** (`python:3.12-slim`) — the final image. Receives the Node binary, site-packages, app code, and Nuxt output. Contains only runtime system packages (`libmysqlclient21`).

## Per-Stage Details

### Stage 1: `frontend-builder`

No changes. Copies `package*.json`, runs `npm ci`, copies source, runs `npm run build`.

### Stage 2: `node-provider`

```dockerfile
FROM node:20-slim@<sha> AS node-provider
```

Nothing to run. `node:20-slim` (Debian bookworm) and `python:3.12-slim` (Debian bookworm) share glibc, so the Node binary is directly portable via `COPY --from`.

### Stage 3: `python-builder`

```dockerfile
FROM python:3.12-slim@<sha> AS python-builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
COPY agent/pyproject.toml agent/uv.lock ./
RUN pip install --no-cache-dir uv && \
    uv export --format requirements-txt --no-dev \
      --extra db-mysql-native --extra db-postgres-async > requirements.txt && \
    pip install --no-cache-dir -r requirements.txt
```

DB driver scope:
- SQLite: stdlib, no extra needed
- PostgreSQL sync: `psycopg2-binary` (binary wheel, no build deps)
- PostgreSQL async: `asyncpg` via `--extra db-postgres-async` (binary wheel)
- MySQL pure Python: `PyMySQL` (base dep, no build deps)
- MySQL native: `mysqlclient` via `--extra db-mysql-native` (requires `libmysqlclient-dev` at build, `libmysqlclient21` at runtime)

### Stage 4: `runtime`

```dockerfile
FROM python:3.12-slim@<sha> AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libmysqlclient21 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system kanchi && useradd --system --gid kanchi --no-create-home kanchi

WORKDIR /app
COPY --from=node-provider /usr/local/bin/node /usr/local/bin/node
COPY --from=python-builder /usr/local/lib/python3.12/site-packages \
     /usr/local/lib/python3.12/site-packages
COPY --chown=kanchi:kanchi agent/ ./agent/
COPY --chown=kanchi:kanchi --from=frontend-builder /app/frontend/.output ./frontend/.output

COPY --chown=kanchi:kanchi start.sh /app/start.sh

ENV WS_HOST=0.0.0.0
ENV WS_PORT=8765
ENV LOG_LEVEL=INFO
ENV NITRO_PORT=3000
ENV NITRO_HOST=0.0.0.0
ENV NUXT_PUBLIC_WS_URL=ws://localhost:8765/ws

EXPOSE 8765 3000
USER kanchi
CMD ["/app/start.sh"]
```

Eliminations vs. current:
- `npm ci --production` in final stage — dead work, Nitro bundles all server deps into `.output/server/index.mjs`
- nodesource curl install — replaced by `COPY --from=node-provider`
- Two separate `apt-get` blocks — merged (the second block only existed to install Node.js)
- Inline `RUN echo '...'` heredoc for start.sh — replaced with a committed script file
- `RUN chown -R kanchi:kanchi /app` — replaced with `--chown` on each `COPY`

## Entrypoint

`start.sh` committed to the repo root:

```sh
#!/bin/sh
set -e
cd /app/agent && python main.py &
node /app/frontend/.output/server/index.mjs &
wait
```

- `#!/bin/sh` — slim images ship dash, not bash
- `node ... index.mjs` directly — no npm subprocess, no npm in the final image
- `set -e` — startup failures surface immediately

## .dockerignore

```
.git
.worktrees
node_modules
frontend/node_modules
frontend/.nuxt
frontend/.output
**/__pycache__
**/*.pyc
.venv
agent/.venv
docs/
*.md
.github/
```

No `.dockerignore` currently exists, so every build ships `.git` history and any `node_modules` trees to the daemon.

## Cache Behavior

Layer ordering is designed so that the most stable layers (base image, apt packages, Python deps) come before the most volatile ones (app source code). In a typical dev → CI cycle:

- Nuxt build layer (`npm ci`) reuses cache unless `package-lock.json` changes
- Python deps layer reuses cache unless `pyproject.toml` or `uv.lock` changes
- App source copy always re-runs (expected)

## Testing

After applying changes, verify:

1. `docker build -t kanchi:test .` completes without errors
2. `docker run --rm kanchi:test node --version` prints the Node version (confirms binary copy)
3. `docker run -e ... kanchi:test` starts both services (Python agent on 8765, Nuxt on 3000)
4. `docker images kanchi:test` shows a materially smaller image than the pre-optimization baseline
