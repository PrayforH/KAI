# A separate optional receiver image keeps SDK websocket constraints out of the
# already deployed API/Worker dependency environment.
ARG API_IMAGE=kai/assistant-api:20261022-v2r1
FROM ${API_IMAGE}
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN /app/.venv/bin/pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
    'lark-oapi==1.7.5' 'websockets==15.0.1'
HEALTHCHECK NONE
# The manager loads encrypted tenant configuration and owns exactly one SDK
# child per application; starting main directly only reads legacy environment.
ENTRYPOINT ["/app/.venv/bin/python", "-m", "harness.feishu.manager"]
