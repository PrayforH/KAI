# Codex Web upstream snapshot

This directory is an extracted source snapshot, not a Git submodule or clone.

- Upstream: <https://github.com/wangru8080/codex-web>
- Upstream version: `0.10.8`
- Upstream commit: `103f9ee9b47bc5300dc5e503a238e9b7530010e0`
- Upstream commit time: `2026-08-31T06:19:39Z`
- Imported on: `2026-09-01`
- Archive URL: <https://codeload.github.com/wangru8080/codex-web/zip/103f9ee9b47bc5300dc5e503a238e9b7530010e0>
- Archive SHA-256: `825d2d263ce7cae9132f2c97fc7d7981f3d24cd0a2955a4135ed88b2db6f2ab2`

The upstream `package.json` currently declares `UNLICENSED` and the GitHub
repository does not publish an SPDX license. Use of this source is based on a
separate authorization confirmed by the project owner; keep that authorization
with the project's legal records. This file does not grant or redefine any
license rights.

## Refresh procedure

1. Select and record an immutable upstream commit SHA.
2. Download the corresponding codeload ZIP and verify its SHA-256.
3. Extract it outside the repository and review the diff against `web/codex-web`.
4. Replace the snapshot only after preserving intentional local adaptations.
5. Update this file and run `npm ci`, `npm test`, and `npm run build` from
   `web/codex-web`.

Keep AXIS-specific integrations outside this snapshot where practical so that
future ZIP refreshes remain reviewable.
