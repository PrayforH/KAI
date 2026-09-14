# Application-code-only update on top of an already verified operational image.
#
# Same intent as api-update.Dockerfile, minus the SDK/dependency layers: use it
# when the change is pure application code and the base image already carries
# the required SDK and native dependencies. Nothing is resolved from a package
# index, so the build works on hosts without egress and cannot silently drift
# the dependency set.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
COPY --chown=harness:harness src/harness /app/project/lib/python3.12/site-packages/harness
USER harness
