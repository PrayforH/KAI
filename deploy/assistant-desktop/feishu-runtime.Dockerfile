# Desktop receiver, sharing the application's unprivileged local runtime.
# No host workspace, Docker socket or inbound ports are required.
ARG API_IMAGE=kai/assistant-desktop-api:20261023-r3
FROM ${API_IMAGE}
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN /app/.venv/bin/pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
    'lark-oapi==1.7.5' 'websockets==15.0.1'
ENV PATH="/app/project/bin:/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/project/lib/python3.12/site-packages" \
    PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "-m", "harness.feishu.manager"]
HEALTHCHECK NONE
