ARG BASE_IMAGE=kai/axis-api:20260908-webfix3
FROM ${BASE_IMAGE}
USER root
RUN /app/.venv/bin/pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple pgvector==0.4.2 numpy==2.5.3 \
    && /app/.venv/bin/pip check
COPY --chown=harness:harness src/harness /app/project/lib/python3.12/site-packages/harness
COPY --chown=harness:harness migrations /app/migrations
USER harness
