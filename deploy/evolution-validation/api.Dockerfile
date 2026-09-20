ARG BASE_IMAGE=kai/axis-api:deepagents-20260919.9
FROM ${BASE_IMAGE}
USER root
COPY --chown=harness:harness src/harness /app/project/lib/python3.12/site-packages/harness
COPY --chown=harness:harness migrations /app/migrations
USER harness
