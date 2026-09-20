# The lockfile and Next version match this existing Linux runtime. Build the
# standalone JS locally; retain the base's Linux native node_modules.
ARG BASE_IMAGE=kai/axis-web:deepagents-20260919-web
FROM ${BASE_IMAGE}
USER root
RUN rm -rf /app/.next
COPY --chown=nextjs:nodejs web-runtime/ /app/
USER nextjs
