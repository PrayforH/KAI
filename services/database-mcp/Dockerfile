ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
USER root
WORKDIR /opt/kai-database-mcp
COPY pyproject.toml uv.lock requirements.lock ./
COPY kai_database_mcp ./kai_database_mcp
RUN python -m venv /opt/database-venv && /opt/database-venv/bin/pip install --no-cache-dir -r requirements.lock && /opt/database-venv/bin/pip install --no-cache-dir --no-deps .
ENV LD_LIBRARY_PATH=/opt/database-venv/lib/python3.12/site-packages/dmssl
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=18865 KAI_DB_CONFIG=/config/sources.json KAI_DB_SQLITE_ROOT=/data/sqlite
USER 10001:10001
EXPOSE 18865
ENTRYPOINT ["/opt/database-venv/bin/python", "-m", "kai_database_mcp"]
