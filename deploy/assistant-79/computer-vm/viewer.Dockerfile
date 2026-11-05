# Verify this local tag's immutable image ID before building (r21 d310cd79...).
ARG BASE_IMAGE=kai/assistant-api:20261027-r21-computer-transfer
FROM ${BASE_IMAGE}
USER root
RUN command -v ssh >/dev/null || (apt-get -o Acquire::Retries=2 update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*)
# This service imports no new database models and can authenticate against r21.
COPY src/harness/computer/viewer.py src/harness/computer/vm_seed.py \
     src/harness/computer/vm_keys.py src/harness/computer/vm-network.json /app/project/lib/python3.12/site-packages/harness/computer/
ENTRYPOINT ["python3", "-m", "harness.computer.viewer"]
HEALTHCHECK NONE
