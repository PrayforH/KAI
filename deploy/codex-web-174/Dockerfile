FROM node:22-bookworm-slim

ARG CODEX_VERSION=0.149.0
ARG CODEX_WEB_PACKAGE=wangru8080-codex-web-0.10.8.tgz

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      jq \
      openssh-client \
      procps \
      python3 \
      ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY ${CODEX_WEB_PACKAGE} /tmp/codex-web.tgz

RUN npm install --global --omit=dev \
      /tmp/codex-web.tgz \
      "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && rm -f /tmp/codex-web.tgz \
    && codex-web --help >/dev/null \
    && codex --version

ENV NODE_ENV=production \
    CODEX_HOME=/root/.codex \
    CODEX_WEB_STATE=/var/lib/codex-web \
    CODEX_WEB_NEXT_HOST=0.0.0.0 \
    PORT=3001

WORKDIR /workspace

EXPOSE 3001

ENTRYPOINT ["codex-web"]
CMD ["serve", "--host", "0.0.0.0", "--port", "3001", "--codex-home", "/root/.codex"]
