# Offline operational-image update. Stage the reviewed archives under
# node-runtime/ in the build context; use api-update.Dockerfile for online builds.
# BASE_IMAGE must already have the required Python/SDK dependencies installed.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
ARG SDK_VERSION=0.2.152
ARG NODE_ARCHIVE_SHA256=db88285eb4c8c5351a9948bd1b0075ef525387dcdb708f8f3dc1d203d7ec9a52
ARG OFFICE_ARCHIVE_SHA256=0ad0b0c29e0a895156e226eb275411513b090dbfc4b683f296e38c910723e541
RUN /app/.venv/bin/python -c "import importlib.metadata as m; assert m.version('claude-agent-sdk') == '${SDK_VERSION}'"
COPY node-runtime/node.tar.gz /tmp/node.tar.gz
COPY node-runtime/office-node-modules.tar.gz /tmp/office-node-modules.tar.gz
RUN printf '%s  %s\n' "${NODE_ARCHIVE_SHA256}" /tmp/node.tar.gz \
        "${OFFICE_ARCHIVE_SHA256}" /tmp/office-node-modules.tar.gz | sha256sum --check --strict \
    && tar -xzf /tmp/node.tar.gz --strip-components=1 -C /usr/local \
    && mkdir -p /node_modules /tmp/office-modules /tmp/office-probe \
    && tar -xzf /tmp/office-node-modules.tar.gz -C /tmp/office-modules \
    && cp -R /tmp/office-modules/node_modules/. /node_modules/ \
    && rm -rf /tmp/node.tar.gz /tmp/office-node-modules.tar.gz /tmp/office-modules \
    && cd /tmp/office-probe \
    && env -u NODE_PATH node -e "require('docx'); require('pptxgenjs')" \
    && rmdir /tmp/office-probe
ENV NODE_PATH=/node_modules
COPY --chown=harness:harness src/harness /app/project/lib/python3.12/site-packages/harness
COPY --chown=harness:harness agents/lead-agent /app/agents/lead-agent
COPY --chown=harness:harness platform-skills /app/platform-skills
USER harness
