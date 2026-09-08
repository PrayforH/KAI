# Keep the existing PostgreSQL 18.1 server and data format; add only pgvector.
FROM harbor.shdata.com:5000/dependencies-ai/pgvector/pgvector:0.8.6-pg18 AS extension
FROM harbor.shdata.com:5000/dependencies-ai/postgres:18.1
COPY --from=extension /usr/lib/postgresql/18/lib/vector.so /usr/lib/postgresql/18/lib/vector.so
COPY --from=extension /usr/share/postgresql/18/extension/vector* /usr/share/postgresql/18/extension/
