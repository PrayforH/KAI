ARG NODE_IMAGE=harbor.shdata.com:5000/dependencies-ai/node:22-alpine
ARG RUNTIME_BASE=harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:knowledge-graph-3d

# Offline / cross-build fallback for the web image.
#
# web.Dockerfile pins node:22-alpine by digest on docker.io, which neither the
# build hosts nor 173 can reach; the mirror kept in harbor is the arm64 variant,
# fine for the $BUILDPLATFORM stages but wrong for an amd64 runtime (it builds
# an image that is labelled amd64 and fails with "exec format error" on 173).
#
# This variant therefore takes the runtime stage from an already-released amd64
# web image — same node:22-alpine runtime and same nextjs user — clears its
# bundle and lays down the freshly built one. Dependency install and compilation
# stay on the builder architecture, as in web.Dockerfile.

FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS dependencies
WORKDIR /app
ARG NPM_CONFIG_REGISTRY=https://registry.npmmirror.com
ARG NPM_VERSION=11.6.2
COPY web/harness-console/package.json web/harness-console/package-lock.json ./
RUN npm install --global --registry="${NPM_CONFIG_REGISTRY}" "npm@${NPM_VERSION}" \
    && npm ci --registry="${NPM_CONFIG_REGISTRY}"

FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY web/harness-console ./
RUN npm run build

FROM ${RUNTIME_BASE} AS runtime
# The base ends on USER nextjs, so the bundle swap needs root back first.
USER root
WORKDIR /
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# The base carries the previous release's bundle; drop it rather than layering
# over it, so a chunk that no longer exists cannot be served.
RUN rm -rf /app && mkdir -p /app && chown nextjs:nodejs /app
WORKDIR /app
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]
