# Final answer recovery — 174, 2026-09-21

## Problem and evidence

The reported comparison task showed completed process rows and feedback controls but no final answer. The server had a successful run, a 1,748-character final answer, and published report artifacts. Replaying its events verified that the full answer was transmitted.

The UI could keep a hidden or empty terminal `liveResponseStore` as the owner of the answer slot, suppressing the text imported from durable history. Separately, a completion callback could trigger the one-shot history refresh while assistant-ui still reported `isRunning`; the import was skipped with no retry on the transition to idle. These paths explain how completed work could appear to stop at the process log.

## Change

- Defer durable-history refresh while the runtime is running, and retry when it settles. Never import an older response over a newer active run.
- After a safe history import, release the live response slot so the persisted answer and file projections own rendering.
- A hidden or empty terminal live response no longer suppresses native answer text. Copy uses the visible answer rather than a hidden tool preface.
- Preserve the activity compaction and collapsed-reasoning performance fix already deployed in `kai/axis-web:news-stream-20260920`. That existing frontend patch is now included in develop so this release does not regress long research runs.

## Validation

- Frontend: 116 test files, 754 passed, 1 skipped.
- TypeScript check and linux/amd64 production Docker build passed.
- Regression tests cover completion before runtime settlement, hidden terminal stream recovery, stale callbacks after a new run, and terminal native-text fallback.
- Chrome replay using the actual task event stream: normal completion, restored history, and an interrupted-answer fixture with complete durable history all display the final answer and the report entry. The interrupted-answer fixture reproduced the missing-answer behavior before the fix. The real private trace and screenshots remain outside the repository.

## Release

- Image: `kai/axis-web:final-response-20260921`.
- Container: `axis-web-final-response-20260921`, port 3501.
- Chrome check against the deployed 3501 frontend with real 174 read-only API data: 3 answers, the reported final answer visible, 2 artifact rows, no browser exceptions or failed API reads. The browser test supplied the authorized owner identity through request interception; it did not exercise login.
- Preview port 3598 and production health checks passed; no active runs at switch-over.
- Previous frontend retained as `axis-web-news-stream-20260920-rollback-final-response`.
- API and workers remain on the previously verified WebFetch proxy fix; no task data or report content was changed.
- Release assets: `/data/final-response-20260921` on 174. Private environment and inspection backups must not be copied into source control.
