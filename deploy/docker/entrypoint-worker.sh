#!/bin/sh
set -eu

if [ "${HARNESS_RUNTIME:-}" = "multi" ] && [ "${HARNESS_SANDBOX_PROVIDER:-}" = "local" ]; then
  # Fail fast when the Docker host blocks the user namespace required by the
  # nested Codex workspace sandbox. This is safer than discovering it after a
  # task has already started and prevents a silent full-access fallback.
  /opt/codex/vendor/x86_64-unknown-linux-musl/codex-resources/bwrap \
    --unshare-user --uid 0 --gid 0 --ro-bind / / /bin/true
fi

exec harness-worker
