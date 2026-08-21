# Both upstreams are immutable. The Chainguard kubectl image is also verified
# with Cosign in CI before Docker is allowed to copy its binary into our image.
ARG KUBECTL_IMAGE=cgr.dev/chainguard/kubectl@sha256:1e1aa9dedf0d9008e5a3710b23f2072bc2ab83117146d503c689b5d2592add3d
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
FROM ${KUBECTL_IMAGE} AS kubectl

FROM ${PYTHON_IMAGE} AS builder

WORKDIR /app

ARG UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
RUN python -m pip install --no-cache-dir \
    --index-url "${UV_DEFAULT_INDEX}" \
    "uv==0.11.28"
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX}

# The official platform package is a standalone musl binary plus its sandbox
# resources. Verify the immutable npm artifact and avoid shipping Node/npm in
# the Python runtime image.
ARG CODEX_VERSION=0.149.0
ARG CODEX_LINUX_X64_SHA256=e06f3d106fe8bb058a6bfd30075d89ea17deaee7c8425e0c5d23072df0fdd0e7
ARG CODEX_NPM_REGISTRY=https://registry.npmmirror.com
RUN python -c 'import os, urllib.request; version=os.environ["CODEX_VERSION"]; registry=os.environ["CODEX_NPM_REGISTRY"].rstrip("/"); urllib.request.urlretrieve(f"{registry}/@openai/codex/-/codex-{version}-linux-x64.tgz", "/tmp/codex.tgz")' \
    && printf '%s  %s\n' "${CODEX_LINUX_X64_SHA256}" /tmp/codex.tgz | sha256sum --check --strict \
    && mkdir -p /opt/codex \
    && tar -xzf /tmp/codex.tgz --strip-components=1 -C /opt/codex \
    && /opt/codex/vendor/x86_64-unknown-linux-musl/bin/codex --version

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
COPY --from=builder /opt/codex /opt/codex
COPY --chown=harness:harness deploy/docker/entrypoint-api.sh /usr/local/bin/entrypoint-api
COPY --chown=harness:harness deploy/docker/entrypoint-worker.sh /usr/local/bin/entrypoint-worker

RUN ln -s /opt/codex/vendor/x86_64-unknown-linux-musl/bin/codex /usr/local/bin/codex \
    && codex --version

USER harness
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

ENTRYPOINT ["entrypoint-api"]
