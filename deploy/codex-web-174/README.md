# Codex Web 174 verification deployment

This deployment runs the unmodified `wangru8080/codex-web` 0.10.8 UI with
OpenAI Codex CLI 0.149.0 in a standalone container on port 3401. It is isolated
from the existing Agent Studio services on ports 3301 and 8800.

Runtime state lives under `/data/codex-web-174` on the host:

- `codex-home/config.toml`: Codex model/provider configuration.
- `codex-web.env`: Web login, session, and provider secrets (mode 0600).
- `web-state`: Codex Web application state.
- `workspace`: the only host workspace mounted into the container.

The verification provider is configured as a custom `axis` provider whose
`wire_api` is `responses`. The provider key is supplied via `AXIS_API_KEY`; it
must not be stored in `config.toml` or committed to source control.

Deployment:

```bash
docker load -i /data/codex-web-174/codex-web-image.tar
docker compose -f /data/codex-web-174/compose.yaml up -d
docker compose -f /data/codex-web-174/compose.yaml ps
curl -fsS http://127.0.0.1:3401/login >/dev/null
```

Rollback/removal leaves state intact:

```bash
docker compose -f /data/codex-web-174/compose.yaml down
```
