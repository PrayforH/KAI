ARG NODE_IMAGE=node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436
FROM ${NODE_IMAGE}

ARG CLAUDE_CODE_VERSION=2.1.206
ARG NPM_CONFIG_REGISTRY=https://registry.npmmirror.com

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl tar \
    && npm install --global --registry="${NPM_CONFIG_REGISTRY}" \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /workspace /tmp \
    && chown -R node:node /workspace /tmp

USER 1000:1000
WORKDIR /workspace
ENTRYPOINT ["sleep", "infinity"]
