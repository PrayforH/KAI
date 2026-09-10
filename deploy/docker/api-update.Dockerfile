# Targeted application/SDK update from an already verified operational image.
# Resolve and record BASE_IMAGE's image ID before use. The inherited OS, Codex,
# kubectl, entrypoints, non-SDK dependencies and healthcheck stay unchanged.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
ARG SDK_VERSION=0.2.152
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# Vendored office Skills generate .docx/.pptx through the Node libraries, so
# Node plus the two document packages ship in the image. Installing them at
# build time keeps runs free of npm-registry egress.
ARG NODE_VERSION=22.9.0
ARG NODE_LINUX_X64_SHA256=1bfae9ef21ab43c92d8274f1bd032bf61f42ea004192a18d4c64477508626142
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN /app/.venv/bin/pip install --no-cache-dir --no-deps --index-url "${PYPI_INDEX}" "claude-agent-sdk==${SDK_VERSION}" \
    && /app/.venv/bin/pip check \
    && python -c 'import os, urllib.request; v=os.environ["NODE_VERSION"]; r=os.environ["NPM_REGISTRY"].rstrip("/"); urllib.request.urlretrieve(f"{r}/-/binary/node/v{v}/node-v{v}-linux-x64.tar.xz", "/tmp/node.tar.xz")' \
    && printf '%s  %s\n' "${NODE_LINUX_X64_SHA256}" /tmp/node.tar.xz | sha256sum --check --strict \
    && tar -xJf /tmp/node.tar.xz --strip-components=1 -C /usr/local \
    && rm -f /tmp/node.tar.xz \
    && npm install --global --registry="${NPM_REGISTRY}" docx pptxgenjs \
    && npm cache clean --force \
    && node -e "require('docx'); require('pptxgenjs'); console.log('office npm libs ok')"
ENV NODE_PATH=/usr/local/lib/node_modules
COPY --chown=harness:harness src/harness /app/project/lib/python3.12/site-packages/harness
# The default conversation agent is seeded straight from this manifest;
# keep it in lockstep with application code on incremental updates.
COPY --chown=harness:harness agents/lead-agent /app/agents/lead-agent
# Vendored upstream Skills live outside the Python package so their
# multi-megabyte assets never enter the wheel or the request path.
COPY --chown=harness:harness platform-skills /app/platform-skills
USER harness
