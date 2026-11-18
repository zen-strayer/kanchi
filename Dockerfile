ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

FROM node:24-alpine@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5 AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build

# Donor stage: supplies the Node binary to the runtime stage without
# requiring nodesource curl installation. node:24-slim (bookworm) binaries
# are glibc-forward-compatible with python:3.12-slim (trixie).
FROM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS node-provider

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

# App deps go into their own venv so the runtime stage copies only them,
# without the builder's pip and uv.
RUN pip install --no-cache-dir uv && \
    uv export --format requirements-txt --no-dev \
      --extra db-mysql-native --extra db-postgres-async > requirements.txt && \
    uv venv /opt/venv && \
    uv pip install --no-cache --python /opt/venv/bin/python -r requirements.txt

# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS runtime

# apt-get upgrade picks up Debian security fixes released after the base
# image digest was built.
# libmariadb3 is the runtime-only MySQL/MariaDB shared library (replaces libmysqlclient21 in Debian trixie).
# The build-time dev headers (default-libmysqlclient-dev) stay in python-builder.
# The app runs from /opt/venv, so the base image's pip is not needed at runtime.
# hadolint ignore=DL3005
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    libmariadb3 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip

RUN groupadd --system kanchi && useradd --system --gid kanchi --no-create-home kanchi

WORKDIR /app

COPY --from=node-provider /usr/local/bin/node /usr/local/bin/node
COPY --from=python-builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

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
