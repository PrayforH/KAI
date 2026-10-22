# Baseline image is verified by ID in the release script before building.
FROM kai/projects-api:builder-model-20261022-r54
COPY src/harness /app/project/lib/python3.12/site-packages/harness
COPY migrations /app/migrations
