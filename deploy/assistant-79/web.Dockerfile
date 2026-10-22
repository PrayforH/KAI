FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY web/harness-console/package.json web/harness-console/package-lock.json ./
RUN npm ci --registry=https://registry.npmjs.org --no-audit --no-fund
COPY web/harness-console ./
RUN npm run build
FROM harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:header-alignment-20261022-r60
USER root
RUN rm -rf /app && mkdir -p /app && chown nextjs:nodejs /app
WORKDIR /app
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
USER nextjs
