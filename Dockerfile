FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ .

RUN npm run build

FROM python:3.12-slim@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3

RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system kanchi && useradd --system --gid kanchi --no-create-home kanchi

WORKDIR /app

COPY agent/pyproject.toml agent/uv.lock ./agent/
RUN pip install --no-cache-dir uv && \
    cd agent && \
    uv export --format requirements-txt --no-dev > requirements.txt && \
    pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/

COPY --from=frontend-builder /app/frontend/.output ./frontend/.output
COPY --from=frontend-builder /app/frontend/package*.json ./frontend/

WORKDIR /app/frontend
RUN npm ci --production

WORKDIR /app

ENV WS_HOST=0.0.0.0
ENV WS_PORT=8765
ENV LOG_LEVEL=INFO
ENV NITRO_PORT=3000
ENV NITRO_HOST=0.0.0.0
ENV NUXT_PUBLIC_WS_URL=ws://localhost:8765/ws

EXPOSE 8765 3000

RUN echo '#!/bin/bash\n\
cd /app/agent && python main.py &\n\
cd /app/frontend && npm run preview &\n\
wait' > /app/start.sh && chmod +x /app/start.sh

RUN chown -R kanchi:kanchi /app
USER kanchi

CMD ["/app/start.sh"]
