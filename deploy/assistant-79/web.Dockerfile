FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY web/harness-console/package.json web/harness-console/package-lock.json ./
RUN npm ci --registry=https://registry.npmjs.org --no-audit --no-fund
COPY web/harness-console ./
RUN npm run build
# Build each release from a clean runtime, never the previous application image.
# Repeated bundle swaps otherwise retain all deleted bundles and exhaust overlay depth.
FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
RUN addgroup --system --gid 10001 nodejs \
    && adduser --system --uid 10001 --ingroup nodejs nextjs \
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
