#!/bin/sh
set -eu

if [ "${HARNESS_RUNTIME:-}" = "multi" ] && [ "${HARNESS_SANDBOX_PROVIDER:-}" = "local" ]; then
  # Parse the same validated configuration as the router. Invalid settings
  # must abort startup, never silently skip the sandbox check.
  codex_enabled="$(python -c 'from harness.config import Settings; print("yes" if "codex-app-server" in Settings().runtime_kernels else "no")')"
  if [ "$codex_enabled" = "yes" ]; then
    # Fail fast when the host blocks the nested Codex sandbox; never fall back
    # to full access. Deployments without Codex do not require its sandbox.
    /opt/codex/vendor/x86_64-unknown-linux-musl/codex-resources/bwrap \
      --unshare-user --uid 0 --gid 0 --ro-bind / / /bin/true
  fi
fi

exec harness-worker
