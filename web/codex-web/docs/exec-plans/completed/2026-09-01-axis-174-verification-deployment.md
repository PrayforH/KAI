# AXIS 174 verification deployment

Status: completed

## Goal

Deploy the unmodified `wangru8080/codex-web` 0.10.8 UI to the 174 validation
host, backed by Codex app-server and the AXIS OpenAI Responses-compatible model
route, without changing the existing Agent Studio deployment.

## Decisions

- Use a standalone Docker container because the CentOS 7 host has Docker but
  does not have Node.js or Codex installed.
- Publish on port 3401; existing ports 3301, 8800, and 4000 remain untouched.
- Use single-user web authentication for the first validation build.
- Mount only `/data/codex-web-174/workspace` as the working directory.
- Keep the provider key in a host-only `0600` environment file and reference it
  from `config.toml` with `env_key = "AXIS_API_KEY"`.
- Use `wire_api = "responses"` explicitly.

## Checklist

- [x] Record upstream source version and immutable commit.
- [x] Run typecheck/unit suite and record upstream failures.
- [x] Produce a successful production and CLI build.
- [x] Verify the AXIS Responses endpoint with a minimal real request.
- [x] Build the linux/amd64 deployment image.
- [x] Transfer and start the image on 174.
- [x] Verify login, bridge initialization, model list, and a real turn.
- [x] Record rollback command and final smoke ledger.

## Smoke ledger

- `npm run build`: passed on 2026-09-01.
- `npm run build:cli`: passed on 2026-09-01.
- `TMPDIR=/tmp npm test`: 984/985 tests passed; the remaining upstream watcher
  test fails because an unrelated directory event invokes the callback once.
- AXIS `/v1/models`: HTTP 200 using the deployment token.
- AXIS `/v1/responses`: HTTP 200 and `output_text = "OK"` for
  `deepseek-v4-flash`.
- Deployment image: `axis/codex-web:0.10.8-codex-0.149.0` for linux/amd64.
- Container health check: `healthy`; external `/login`: HTTP 200 on port 3401.
- Web authentication: login HTTP 200; authenticated `/chat` HTTP 200 while an
  anonymous request redirects to `/login`; authenticated bridge URL HTTP 200
  while an anonymous request is rejected with HTTP 401.
- Codex CLI end-to-end turn: exit 0 and final response `OK` through the AXIS
  Responses provider.
- Existing Agent Studio web and API checks remain HTTP 200 on ports 3301 and
  8800 respectively.

## Rollback

Run `docker compose -f /data/codex-web-174/compose.yaml down`. Persistent state
is retained under `/data/codex-web-174` for diagnosis or a later restart.
