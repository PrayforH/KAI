# Sidebar expand control placement — 2026-10-22

The r63 title/action spacing change applied `grid-area: 1 / 1 / 2 / -1` to all `.task-list-item` buttons. The expand/collapse control shares that class and is a direct child of the project-items grid, so it was explicitly placed before the five automatically positioned task rows despite being last in DOM order.

The shared button style now retains normal flex layout. Only `.task-list-row > .task-list-item` receives the subgrid and explicit grid area. The expand control follows normal grid placement and stays below the visible tasks. This also preserves the title/action separation from r63.

Verification:

- 25 existing sidebar, action-menu and sidebar-state tests passed; locked production build passed.
- A browser fixture reproduced r63: expand control at y=280, before task rows at y=311–465.
- The regression fixture checks both project and ungrouped tasks in light/dark mode. It asserts actual geometry, not only DOM order: five tasks then expand; all 15 project / 8 ungrouped tasks then collapse; five tasks again after collapsing. It also verifies title/action spacing.
- Browser API responses are fixtures and do not change real user records.

Source: `36579083`. Deployment image: `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:sidebar-expand-20261022-r64`, image ID `sha256:f8225c2bab9426528d81e26d741bdb6f99d70803b9b55da92a5316b1bddcd0fe`.

Release and configuration backups: `/data/agent-studio/releases/sidebar-expand-20261022-r64/` on 174. Web targets are the canonical HTTPS/3301 entry and the 3501 preview; API, workers and schema remain unchanged.

Local and Linux-canary regression checks passed all 12 expand/collapse states, with each control geometrically below every visible task. The r63 title/action regression also passed all 30 width/theme/status combinations on the r64 canary, retaining at least 4 px separation, along with pin, menu, keyboard, task selection and touch checks. There were no browser page errors.

Both Web containers run the r64 image, healthy with zero restarts. HTTPS, 3301 and 3501 return HTTP 200. The API image remains `sha256:77d7f469686061a2e59a922013e49e7e00de201af0be36ddb1b6b22258a517c9`; all three r45 workers remain healthy. The temporary canary was stopped and removed.

The deployed HTTPS entry passed all 12 expand/collapse geometry states and retained title/action separation, with no browser page errors. Layout checks use mocked browser API responses; container and HTTP health checks use the actual services.
