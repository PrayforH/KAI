ARG NODE_IMAGE=node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32

# The Next.js bundle is architecture-independent. Keep dependency installation
# and compilation on the builder architecture so cross-platform releases do
# not execute Node under QEMU; only the runtime stage targets the requested
# deployment platform.
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

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

RUN apk upgrade --no-cache libcrypto3 libssl3 openssl

RUN addgroup --system --gid 10001 nodejs \
    && adduser --system --uid 10001 --ingroup nodejs nextjs \
    # npm and its transitive build toolchain are not needed by the standalone
    # Next.js server. Removing them also removes executable attack surface.
    && rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]

CMD ["node", "server.js"]
