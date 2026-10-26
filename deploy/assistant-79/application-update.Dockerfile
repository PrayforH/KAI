# Incremental release over a verified local API image. The release context must
# contain a locally built application wheel and the hash-pinned QR dependency.
ARG API_IMAGE
FROM ${API_IMAGE}
USER root
COPY release-wheels/*.whl /tmp/kai-wheels/
RUN printf '%s  %s\n' '16e64e0716c14960108e85d853062c9e8bba5ca8252c0b4d0231b9df4060ff4f' /tmp/kai-wheels/qrcode-8.2-py3-none-any.whl | sha256sum --check --strict \
    && /app/.venv/bin/pip install --no-deps --no-index /tmp/kai-wheels/qrcode-8.2-py3-none-any.whl \
    && /app/.venv/bin/pip install --no-deps --no-index --ignore-installed --no-compile --prefix /app/project /tmp/kai-wheels/claude_agent_harness-*.whl \
    && rm -rf /tmp/kai-wheels
COPY --chown=10001:10001 migrations /app/migrations
USER harness
