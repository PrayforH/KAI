# Codex WebUI upstream snapshot

This directory is an extracted source snapshot, not a Git submodule or clone.

- Upstream: <https://github.com/LimLLL/codex-webui>
- Upstream tag: `codex-0.151.0`
- Upstream commit: `310307d43780cd76ebe84fed3ef3a1cb04632d3f`
- Upstream commit time: `2026-09-01T03:05:55Z`
- Imported on: `2026-09-01`
- Archive URL: <https://codeload.github.com/LimLLL/codex-webui/zip/310307d43780cd76ebe84fed3ef3a1cb04632d3f>
- Archive SHA-256: `db87a3f4aeab306d29fd0274e4d269cb5065043336da8294c1aadc8894fad00d`
- Upstream license: `AGPL-3.0-or-later`

The upstream license is preserved in `web/codex-webui/LICENSE`. The project
owner has also confirmed a separate private communication with the upstream
author. Keep any additional authorization or commercial terms with the
project's legal records; this file does not grant or redefine those rights.

## Refresh procedure

1. Select and record an immutable upstream commit SHA and matching Codex tag.
2. Download the corresponding codeload ZIP and verify its SHA-256.
3. Extract it outside the repository and review the diff against
   `web/codex-webui`.
4. Replace the snapshot only after preserving intentional local adaptations.
5. Update this file and run `pnpm install --frozen-lockfile`, `pnpm test`,
   `pnpm build`, `pnpm --dir web test`, and `pnpm --dir web build`.

Keep AXIS-specific integrations outside the snapshot where practical so that
future ZIP refreshes remain reviewable.
