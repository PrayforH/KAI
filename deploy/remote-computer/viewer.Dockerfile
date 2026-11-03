ARG API_IMAGE
FROM ${API_IMAGE}
USER root
ENTRYPOINT ["python3", "-m", "harness.computer.viewer"]
HEALTHCHECK NONE
