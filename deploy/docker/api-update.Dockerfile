# Targeted application/SDK update from an already verified operational image.
# Resolve and record BASE_IMAGE's image ID before use. The inherited OS, Codex,
# kubectl, entrypoints, non-SDK dependencies and healthcheck stay unchanged.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
ARG SDK_VERSION=0.2.152
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
RUN /app/.venv/bin/pip install --no-cache-dir --no-deps --index-url "${PYPI_INDEX}" "claude-agent-sdk==${SDK_VERSION}" \
    && /app/.venv/bin/pip check
COPY --chown=harness:harness src/harness /app/project/lib/python3.12/site-packages/harness
# The default conversation agent is seeded straight from this manifest;
# keep it in lockstep with application code on incremental updates.
COPY --chown=harness:harness agents/lead-agent /app/agents/lead-agent
USER harness
