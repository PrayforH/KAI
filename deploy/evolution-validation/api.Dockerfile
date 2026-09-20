ARG BASE_IMAGE=kai/axis-api:deepagents-20260919.9
FROM ${BASE_IMAGE}
USER root
RUN rm -rf /app/project/lib/python3.12/site-packages/harness /app/migrations
COPY --chown=harness:harness src/harness /app/project/lib/python3.12/site-packages/harness
COPY --chown=harness:harness migrations /app/migrations
COPY --chown=harness:harness deploy/docker/entrypoint-worker.sh /usr/local/bin/entrypoint-worker
RUN chmod +x /usr/local/bin/entrypoint-worker
USER harness
