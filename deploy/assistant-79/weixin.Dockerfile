# Same application and dependencies as API/Worker; no desktop or OpenClaw runtime.
ARG API_IMAGE
FROM ${API_IMAGE}
ENTRYPOINT ["/app/.venv/bin/python", "-m", "harness.weixin.main"]
CMD []
