# Sidebar title / action rail — 2026-10-22

## Reference and cause

Inspected the installed Codex client bundle `app-initial-90eb42395b7d.js`, extracted locally as `/tmp/codex-local-app-initial-90eb42395b7d.js`. Its `CEc` / `JEc` thread row limits title width (`min-w-0`, truncation or a hover-scrolling viewport) and uses a separate trailing rail: timestamp metadata hides on hover/keyboard focus; the quick-action rail reserves width according to the actions (including a two-action `min-w-14` branch).

Our pin and More buttons were absolutely positioned over a flex title. Hiding the timestamp preserved only its narrower width, so the title extended into the 53 px action area. A browser fixture against r62 reproduced an approximately 12 px overlap. Menu focus also moves to a portal, allowing the timestamp to reappear unless the expanded menu state is considered.

## Change

- A shared two-column grid sizes the trailing column from both the timestamp and the actual buttons. The full-row task button inherits those tracks with `subgrid`; title and status stay in the first column, and long-title scrolling is clipped there.
- Pin and More are laid out in the trailing column rather than absolutely placed over text. Hover does not change title width.
- The entire action rail shares visibility and hit-testing. It appears on hover, keyboard focus, while its menu is open, and on touch devices. The timestamp hides in the same states.
- Existing 24 px pin / 28 px More buttons, outline icons, task sorting and project grouping remain in use.

## Verification

- 25 existing sidebar, action-menu and sidebar-state tests passed; production Next build passed.
- Browser fixtures cover 220 / 264 / 380 px sidebars in light and dark modes, Chinese / Latin long titles, completed / waiting-input / failed tasks, pinned tasks, project tasks, hover scrolling, keyboard focus, menu-open state, pin mutation, task selection and touch.
- API responses are mocked in the browser for layout checks; no real user records are changed.

Local build and Linux canary each passed all 30 layout combinations, with at least 4 px between the title/status content and the action rail. Pin update, task selection, keyboard/menu-open behavior and touch visibility also passed with no page errors.

## Deployment

- Source commit: `729e5c2f`.
- Web image: `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:sidebar-layout-20261022-r63`.
- Image ID: `sha256:1a76ffb7d3e834bff16c69aecce7be25795301665643d3f40e78fd30eebfe1d5`.
- Release and private configuration backups: `/data/agent-studio/releases/sidebar-layout-20261022-r63/` on 174.
- Deployment targets: the canonical Web on 3301 / HTTPS and the preview Web on 3501. API, workers and schema are unchanged.

Both Web containers are healthy with zero restarts and the same r63 image. The API image remains `sha256:77d7f469686061a2e59a922013e49e7e00de201af0be36ddb1b6b22258a517c9`, and the three workers remain on `kai/projects-worker:sidebar-20261020-r45`. HTTPS, 3301 and 3501 return HTTP 200. The temporary canary was stopped and removed after rollout.

Post-deploy verification at `https://172.20.109.174` passed the same 30 layout cases (minimum 4 px separation), pin update, task selection, keyboard/menu-open and touch checks, with no browser page errors. Browser APIs remained fixtures; deployment health checks used the actual containers and HTTP endpoints.
