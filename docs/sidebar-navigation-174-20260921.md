# Sidebar navigation and new-task fixes — 2026-09-21

## Behavior

- Expanded project folders and each group's “show more” setting survive navigation between tasks, plugins, agents and other workspaces. A deliberately collapsed current project remains collapsed on remount. Preferences are scoped to the signed-in user.
- Both workspace shells initialize projects from a shared, user-scoped snapshot. Background reads do not erase the directory on transient failures, and concurrent mounts coalesce list requests.
- The project folder icon uses a rounded outline and a full top seam, consistent with the supplied Codex reference.
- New Task in Studio now allocates a fresh task ID and navigates to its URL. The task page also creates a fresh ID for explicit New Task clicks, including while previous history is unresolved.
- Creating or selecting a task updates the URL. A delayed catalog or project-creation response cannot overwrite a more recent task selection.

## Verification

Browser checks use the real read-only 174 API data with an authorized owner identity supplied by request interception; they do not exercise login or generate new model runs. Checks cover expansion across plugin navigation, New Task from both workspaces, reload of the newly created task, and a delayed catalog arriving after another new-task click. No browser exceptions or failed API reads occurred.

Unit regressions cover group expansion/remount, deliberate collapse/remount, project snapshot reuse, failed refresh preservation, account isolation, and explicit new-task creation before history resolves. The full frontend suite passed 758 tests (1 skipped); the two additional Studio navigation regressions also passed. TypeScript and the linux/amd64 production build passed.

## Release

Frontend image: `kai/axis-web:sidebar-navigation-20260921` (linux/amd64).
Previous frontend: `kai/axis-web:final-response-20260921`; API and workers are unchanged.

Deployed container: `axis-web-sidebar-navigation-20260921`, port 3501. Preview and production health checks passed; no active runs were present at switchover. The same Chrome navigation checks passed against the deployed frontend. Rollback container: `axis-web-final-response-20260921-rollback-sidebar`.
