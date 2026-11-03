# Code-only release over the verified 79 dependency image, one new filesystem layer.
ARG API_IMAGE=kai/superagent-api:20261103-bot-autonomy-6b9c8354
FROM ${API_IMAGE}
COPY --chown=10001:10001 src/harness /app/project/lib/python3.12/site-packages/harness
