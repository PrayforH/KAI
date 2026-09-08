FROM kai/axis-api:20260908-101112
USER root
COPY --chown=harness:harness src/harness /app/project/lib/python3.12/site-packages/harness
COPY --chown=harness:harness agents/lead-agent /app/agents/lead-agent
COPY migrations/versions /app/migrations/versions
USER harness
