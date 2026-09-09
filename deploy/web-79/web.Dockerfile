FROM kai/axis-web-build-deps:20260906-004236 AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY web/harness-console ./
RUN npm run build
FROM kai/axis-web:20260908-101112
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone /app
COPY --from=builder --chown=nextjs:nodejs /app/.next/static /app/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public /app/public

# The base image already runs as nextjs; restate it so the intent is explicit
# and the container-user check can see it without inspecting the base.
USER nextjs
