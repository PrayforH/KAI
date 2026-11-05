# Package a Linux build produced with npm 11.6.2 from the locked source tree.
FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
ARG APK_MIRROR=https://dl-cdn.alpinelinux.org/alpine
RUN sed -i "s|https://dl-cdn.alpinelinux.org/alpine|${APK_MIRROR}|g" /etc/apk/repositories \
    && apk upgrade --no-cache libcrypto3 libssl3 openssl \
    && addgroup --system --gid 10001 nodejs \
    && adduser --system --uid 10001 --ingroup nodejs nextjs \
    && rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx
COPY --chown=nextjs:nodejs .next/standalone ./
COPY --chown=nextjs:nodejs .next/static ./.next/static
COPY --chown=nextjs:nodejs public ./public
USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 CMD ["node", "-e", "fetch('http://127.0.0.1:3000').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
