# Reuse an already verified receiver's ffmpeg and pinned SDK installation while
# replacing the application package with an identically built API release.
# Both inputs must be supplied as local release images or immutable digests.
ARG API_IMAGE
ARG RECEIVER_IMAGE
FROM ${API_IMAGE} AS application
FROM ${RECEIVER_IMAGE}
COPY --from=application --chown=10001:10001 /app/project /app/project
ENTRYPOINT ["/app/.venv/bin/python", "-m", "harness.feishu.manager"]
USER 10001:10001
