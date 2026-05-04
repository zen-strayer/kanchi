# Dockerfile Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Dockerfile into four stages to eliminate dead work, maximize layer cache reuse, and shrink the final runtime image.

**Architecture:** A `frontend-builder` stage (unchanged) produces the Nuxt `.output/`. A new `node-provider` stage (node:20-slim) donates the Node binary. A new `python-builder` stage installs all Python deps including optional DB extras with build-time tools present. The `runtime` stage (python:3.12-slim) copies only what it needs: the Node binary, site-packages, app source, and Nuxt output.

**Tech Stack:** Docker multi-stage builds, Node 20, Python 3.12, uv, Nuxt 3 / Nitro, SQLAlchemy with psycopg2-binary / psycopg[binary] / asyncpg / PyMySQL / mysqlclient extras.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `.dockerignore` | Create | Keep build context tight; currently absent |
| `start.sh` | Create | Startup script committed to repo (replaces inline `RUN echo` heredoc) |
| `Dockerfile` | Rewrite | Four-stage build |

---

### Task 1: Add `.dockerignore`

**Files:**
- Create: `.dockerignore`

- [ ] **Step 1: Create `.dockerignore`**

Create the file at the repo root:

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

- [ ] **Step 2: Verify it parses cleanly**

Run from the repo root:
```bash
docker build --dry-run . 2>&1 | head -3
```

If `--dry-run` is not supported by your Docker version, just move on — the build in Task 3 will catch any issues.

- [ ] **Step 3: Commit**

```bash
git add .dockerignore
git commit -m "chore(docker): add .dockerignore to reduce build context [NOJIRA]"
```

---

### Task 2: Create `start.sh`

**Files:**
- Create: `start.sh`

- [ ] **Step 1: Create `start.sh`**

Create `start.sh` at the repo root:

```sh
#!/bin/sh
set -e
cd /app/agent && python main.py &
node /app/frontend/.output/server/index.mjs &
wait
```

`#!/bin/sh` is required — `python:3.12-slim` ships `dash`, not `bash`. `node` is invoked directly because npm is not present in the runtime image.

- [ ] **Step 2: Make it executable**

```bash
chmod +x start.sh
```

- [ ] **Step 3: Verify shell syntax**

```bash
sh -n start.sh
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add start.sh
git commit -m "chore(docker): add start.sh entrypoint script [NOJIRA]"
```

---

### Task 3: Rewrite Dockerfile

**Files:**
- Modify: `Dockerfile` (full rewrite)

- [ ] **Step 1: Get the `node:20-slim` SHA digest**

```bash
docker pull node:20-slim
docker inspect --format='{{index .RepoDigests 0}}' node:20-slim
```

The output will look like:
```
docker.io/library/node@sha256:abcdef1234...
```

Copy the `sha256:...` portion — you will use it in the next step.

- [ ] **Step 2: Rewrite `Dockerfile`**

Replace the entire contents of `Dockerfile` with the following, substituting `<NODE_20_SLIM_SHA>` with the digest from Step 1:

```dockerfile
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build

# Donor stage: supplies the Node binary to the runtime stage without
# requiring nodesource curl installation. node:20-slim and python:3.12-slim
# both use debian:bookworm-slim so the glibc binary is directly portable.
FROM node:20-slim@<NODE_20_SLIM_SHA> AS node-provider

FROM python:3.12-slim@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3 AS python-builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy lockfiles separately so this layer caches until deps change.
COPY agent/pyproject.toml agent/uv.lock ./

RUN pip install --no-cache-dir uv && \
    uv export --format requirements-txt --no-dev \
      --extra db-mysql-native --extra db-postgres-async > requirements.txt && \
    pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3 AS runtime

# libmysqlclient21 is the runtime-only MySQL shared library.
# The build-time dev headers (default-libmysqlclient-dev) stay in python-builder.
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
COPY --chown=kanchi:kanchi start.sh ./start.sh

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

- [ ] **Step 3: Build the image**

```bash
docker build -t kanchi:test .
```

Expected: build completes with no errors. All four stages should appear in the output. If the build fails with a Python import error during `pip install`, check that `uv export` produced a non-empty `requirements.txt` — run the builder stage interactively: `docker build --target python-builder -t kanchi:builder-debug . && docker run --rm kanchi:builder-debug cat /build/requirements.txt`.

- [ ] **Step 4: Verify the Node binary is present**

```bash
docker run --rm kanchi:test node --version
```

Expected: `v20.x.x`

- [ ] **Step 5: Verify all DB drivers are importable**

```bash
docker run --rm kanchi:test python -c "
import MySQLdb
import asyncpg
import psycopg
import psycopg2
import pymysql
print('all drivers ok')
"
```

Expected: `all drivers ok`

If any import fails, the most likely cause is that the extras weren't exported correctly. Debug with:
```bash
docker build --target python-builder -t kanchi:builder-debug .
docker run --rm kanchi:builder-debug cat /build/requirements.txt | grep -E "asyncpg|mysqlclient"
```
Both should appear in the output.

- [ ] **Step 6: Check final image size**

```bash
docker images kanchi:test
```

Note the SIZE column. For reference, the pre-optimization image included a full Node.js install via nodesource plus npm modules — expect a meaningful reduction. If the image is larger than ~800MB something is wrong (likely npm modules were accidentally included).

- [ ] **Step 7: Commit**

```bash
git add Dockerfile
git commit -m "perf(docker): four-stage build with node-provider and python-builder stages [NOJIRA]"
```
