# Natural project resource cards — 2026-10-29

Replace the earlier viewport-filling card heights with content-sized cards. Each file, skill, and knowledge list grows naturally up to five 32px rows, then scrolls internally. Instructions show up to five text lines; connector icons wrap up to five icon rows. The outer rail keeps its own scroll, and max-content tracks prevent the viewport from compressing card contents or clipping actions.

Connector management uses icon buttons and name tooltips, with a connection action in the heading and an edit control beside saved connectors. Known service categories have corresponding glyphs; unknown services retain the MCP mark. No external logo or favicon requests were added.

The file section has bottom upload and add-webpage actions. The upload dropdown invokes the real existing upload/attach flow, and the webpage action opens the existing URL form directly. The skill add dropdown now lives in its heading. Cards, rows and icons have hover feedback and keyboard focus states. The results card remains hidden; saved data and existing deep links are retained.

## Validation and rollout

- 17 project-home tests passed, including upload success/failure, direct URL entry, MCP binding confirmation, task launch, and saved results.
- Production builds and TypeScript checks passed. Native Linux build preserves the current memory release source and dependency lock; only four frontend source files were overlaid.
- Preview and live browser checks passed: one/five/eight resources, exact five-row cap, natural card growth, no clipped section actions, five inner scroll regions, hover states, connector tooltips, upload and skill menus, direct upload and URL addition, saved results deep link, both themes, and desktop/tablet/mobile widths.
- Browser API responses were mocked, including upload and link mutations; no production user records were changed by these checks. The screenshot uses fixture data.
- Both Web containers on 3301/3501 are healthy with zero restarts. Both HTTP ports, HTTPS, and upstream API health returned 200. Backend/worker image IDs are unchanged.
- Web-only rollback uses the private Compose backups in the release directory recorded in deployment.json.
