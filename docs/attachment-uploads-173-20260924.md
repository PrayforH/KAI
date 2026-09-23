# Conversation attachment uploads — 173 validation, 2026-09-24

## Cause

The reported failed file, `组合 2.pdf`, is 26,603,594 bytes (25.37 MiB), above the
previous hard-coded 25 MiB input attachment limit. The other files are
21,539,928 and 21,011,375 bytes. Three synthetic payloads with those exact sizes
reproduced two HTTP 201 responses and one HTTP 413 on the old deployment.
The composer paste handler replaced the API's specific error with a generic
attachment failure, concealing the actual reason.

The Web proxy also buffered the entire multipart request before forwarding to
API. Authentication refresh happened after this wait, exposing slow concurrent
uploads to reuse of a rotating refresh token. The size limit is the verified
cause of this incident; the refresh race is a separately corrected weakness.

## Behavior

- Default limits: 50 MiB per file, 10 files and 100 MiB per message/run.
- Runtime configuration: `HARNESS_INPUT_ARTIFACT_MAX_FILE_BYTES`,
  `HARNESS_INPUT_ARTIFACT_MAX_FILES_PER_RUN`,
  `HARNESS_INPUT_ARTIFACT_MAX_TOTAL_BYTES` (positive integer bytes/count).
- Authenticated `/v1/input-artifacts/limits` and same-origin
  `/api/input-artifacts/limits` expose the effective server limits.
- The attachment adapter shares one limits request across a concurrent batch,
  checks size/count/total before transmitting file bytes, and releases reserved
  capacity on removal, failure or send. The API still validates actual bytes and
  run totals independently of client hints.
- The Web proxy authenticates before reading upload contents, then forwards the
  body with backpressure instead of buffering/cloning. It never blindly replays
  a transmitted upload. Other proxy routes retain existing replay behavior.
- Paste errors include the file name and real failure reason. Upload progress,
  cancellation, opaque artifact IDs and owner isolation remain supported.

Reference: DeerFlow's current upload router defaults to the same 50/10/100 limits
and exposes configurable limits to the frontend:
https://github.com/bytedance/deer-flow/blob/main/backend/app/gateway/routers/uploads.py
Its 100 MiB total is per upload request; this project uploads each file separately
and applies the aggregate limit to message/run attachments.

## Validation

- 37 focused frontend tests: adapter, transport, proxy and authentication.
- 15 backend tests: upload API, artifact service and configuration.
- Ruff and focused Pyright pass; Next production build and TypeScript pass.
- Live test used a separate temporary QA account and synthetic bytes matching
  the three real file sizes, through the public Web endpoint. No model runs,
  original document contents, user login sessions or user attachments changed.
- Old deployment: HTTP 201/201/413, 21.016 s for the batch.
- New deployment: HTTP 201/201/201, 23.630 s for the batch. Each result's size and
  SHA-256 matched the sent content. This one network sample does not establish
  an end-to-end speedup; streaming removes buffering but does not increase link
  bandwidth.
- Server-local Web → API → MinIO test of the same concurrent batch: all three
  HTTP 201, 1.224 s total. Compared with 23.630 s from the workstation, transfer
  between the workstation and 173 dominates this sample.
- Live limit response: 52,428,800 bytes / 10 files / 104,857,600 bytes.
- Oversize preflight returned HTTP 413 with the 50 MB explanation in 0.421 s.

The eight QA objects/attachment records and the temporary account/session records
were removed after verification; local QA credentials were also deleted.

## Deployment and rollback

Commit: `7a472fa9` on `auto/agent-evolution`.
API/Worker: `kai/axis-api:evolution-builder-7a472fa9`.
Web: `kai/axis-web:evolution-builder-7a472fa9`.
All three health checks passed; Web login returned HTTP 200. No schema migration.

Backup: `/data/agent-studio-evolution-20260920/backups/builder-uploads-7a472fa9/`.
Restore the backed-up compose and source revision files to roll back to the
previous skill-blob-aware API/Worker (`2dad60f6`) and Web (`21ec4575`). Roll back
Web first so it stops depending on the new limits endpoint before rolling back
API. Do not roll back beyond the preceding Skill storage migration without its
separate inline migration procedure.
