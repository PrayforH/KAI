# Existing 79 runtime dependencies stay pinned in a verified local image.
# The release script verifies its immutable ID before this source-only update.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
COPY src/harness /app/project/lib/python3.12/site-packages/harness
COPY migrations /app/migrations
USER harness
