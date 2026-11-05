# Incremental application and media dependencies; preserve all private config.
ARG API_IMAGE
FROM ${API_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY release-codec/silk-wasm-3.7.1.tgz /tmp/silk-wasm.tgz
COPY deploy/docker/weixin-codec/install.py /tmp/install-weixin-codec.py
RUN KAI_WEIXIN_SILK_ARCHIVE=/tmp/silk-wasm.tgz python /tmp/install-weixin-codec.py && rm /tmp/silk-wasm.tgz /tmp/install-weixin-codec.py
COPY release-wheels/claude_agent_harness-*.whl /tmp/kai-application.whl
RUN mv /tmp/kai-application.whl /tmp/claude_agent_harness-0.3.1-py3-none-any.whl \
    && /app/.venv/bin/pip install --no-deps --no-index --ignore-installed --no-compile --prefix /app/project /tmp/claude_agent_harness-0.3.1-py3-none-any.whl \
    && rm /tmp/claude_agent_harness-0.3.1-py3-none-any.whl
COPY --chown=10001:10001 migrations /app/migrations
USER harness
