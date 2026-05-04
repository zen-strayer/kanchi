ARG PYTHON_IMAGE=python:3.12-slim@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build

# Donor stage: supplies the Node binary to the runtime stage without
# requiring nodesource curl installation. node:20-slim (bookworm) binaries
# are glibc-forward-compatible with python:3.12-slim (trixie).
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS node-provider

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS python-builder

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

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS runtime

# libmariadb3 is the runtime-only MySQL/MariaDB shared library (replaces libmysqlclient21 in Debian trixie).
# The build-time dev headers (default-libmysqlclient-dev) stay in python-builder.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmariadb3 \
    curl \
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
