# Both upstreams are immutable. The Chainguard kubectl image is also verified
# with Cosign in CI before Docker is allowed to copy its binary into our image.
ARG KUBECTL_IMAGE=cgr.dev/chainguard/kubectl@sha256:1e1aa9dedf0d9008e5a3710b23f2072bc2ab83117146d503c689b5d2592add3d
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436
FROM ${KUBECTL_IMAGE} AS kubectl

FROM ${NODE_IMAGE} AS codex

ARG CODEX_VERSION=0.149.0
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
RUN npm install --global --registry="${NPM_CONFIG_REGISTRY}" \
      "@openai/codex@${CODEX_VERSION}" \
    && codex --version \
    && npm cache clean --force

FROM ${PYTHON_IMAGE} AS builder

WORKDIR /app

ARG UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
RUN python -m pip install --no-cache-dir \
    --index-url "${UV_DEFAULT_INDEX}" \
    "uv==0.11.28"
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX}

COPY pyproject.toml uv.lock README.md ./
RUN uv export \
    --frozen \
    --no-dev \
    --no-hashes \
    --no-emit-project \
    --output-file /tmp/requirements.txt \
    && python -m venv .venv \
    && .venv/bin/pip install --no-cache-dir \
        --index-url "${UV_DEFAULT_INDEX}" \
        --requirement /tmp/requirements.txt

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY agents ./agents
COPY scripts/seed_docker.py ./scripts/seed_docker.py
RUN .venv/bin/pip install --no-cache-dir \
    --index-url "${UV_DEFAULT_INDEX}" \
    --no-deps \
    --no-compile \
    --prefix /app/project \
    .

FROM ${PYTHON_IMAGE} AS runtime

ENV PATH="/app/project/bin:/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/project/lib/python3.12/site-packages" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The pinned Python image already carries CA certificates, and the healthcheck
# uses urllib. Keeping runtime setup package-manager-free makes rebuilds
# deterministic even when a Debian mirror is slow or unavailable.
RUN groupadd --system --gid 10001 harness \
    && useradd --system --uid 10001 --gid harness --home-dir /app harness \
    && mkdir -p /app/.codex \
    && chown -R harness:harness /app/.codex

WORKDIR /app
COPY --from=builder --chown=harness:harness /app/.venv /app/.venv
COPY --from=builder --chown=harness:harness /app/project /app/project
COPY --from=builder --chown=harness:harness /app/migrations /app/migrations
COPY --from=builder --chown=harness:harness /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=harness:harness /app/agents /app/agents
COPY --from=builder --chown=harness:harness /app/scripts /app/scripts
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY --from=codex /usr/local/bin/node /usr/local/bin/node
COPY --from=codex /usr/local/bin/codex /usr/local/bin/codex
COPY --from=codex /usr/local/lib/node_modules/@openai/codex \
    /usr/local/lib/node_modules/@openai/codex
COPY --chown=harness:harness deploy/docker/entrypoint-api.sh /usr/local/bin/entrypoint-api
COPY --chown=harness:harness deploy/docker/entrypoint-worker.sh /usr/local/bin/entrypoint-worker

USER harness
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

ENTRYPOINT ["entrypoint-api"]
