# Project resource cards — 2026-10-29

- Three resource cards use bounded heights. Long instructions and resource lists scroll inside each section, while headings and actions stay visible.
- All loaded connectors, files, links, skills, and knowledge entries can be reached through scrolling; removed first-two/three-item truncation.
- Hide the separate project results card. Preserve saved data, save/finalize behavior, and existing results deep links. No replacement entry was introduced.
- Preserve the October 27 memory release frontend features, backend, workers, and database. Only the Web images serving 3301 and 3501 were replaced; HTTPS uses 3501.

## Validation

- Project home tests: 14 passed on develop and the memory release frontend baseline.
- Next.js production builds and TypeScript checks passed.
- Browser checks ran against local, 174 preview, and live HTTPS bundles with mocked API responses. No production user records were created or changed.
- Both themes, five independent scroll regions, stable card heights and action positions, unclipped skill menu, and desktop/tablet/mobile widths passed. Eight saved results remained accessible through the existing deep link while the results card stayed hidden. No page errors.
- Both deployed Web containers are healthy with zero restarts. HTTP 3301, HTTP 3501, HTTPS, and upstream API health returned 200. Backend image IDs are unchanged.

Deployment metadata is in `deployment.json`; the release directory keeps private Compose backups for Web-only rollback.
