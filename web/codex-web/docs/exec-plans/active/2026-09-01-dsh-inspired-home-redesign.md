# DSH-inspired Codex Web home redesign

Status: in progress

## Goal

Rebuild the Codex Web new-chat home around the proven DSH Web/Desktop
information architecture while retaining Codex app-server as the only runtime
and preserving every existing composer capability.

## Reference decision

- DeepSeek Harness is MIT-licensed, but its browser UI is coupled to the DSH
  Host RPC, connection generation, and slot/plugin contracts.
- DSH Desktop is a native shell around the official DSH Web rather than an
  independent chat frontend.
- Therefore reuse the visual and interaction model, not the DSH runtime code:
  compact workspace-first sidebar, prominent new-session action, quiet dark
  canvas, centered new-task hero, project/mode context row, and one dominant
  composer surface.

## Scope

- Redesign `/chat` and the empty-session state.
- Restyle the chat sidebar to match the DSH hierarchy without removing Codex
  functionality.
- Keep Codex app-server data, thread actions, project selection, model/effort,
  permission, attachment, goal, plan, skill, plugin, and send paths unchanged.
- Do not copy DSH branding or the whale mark.

## Checklist

- [x] Audit the current homepage and sidebar.
- [x] Inspect official DSH architecture, licensing, and Desktop packaging.
- [x] Capture a current DSH Desktop new-session visual reference.
- [ ] Implement the DSH-inspired shell, sidebar, hero, context row, and composer.
- [ ] Add targeted structural tests.
- [ ] Run typecheck/unit/build and real browser smoke.
- [ ] Deploy the rebuilt image to 174 and verify a real Axis Responses turn.
- [ ] Record rollback and move this plan to completed.

## Smoke ledger

- Pending implementation.

## Rollback

Re-deploy image `axis/codex-web:0.10.8-codex-0.149.0` with the existing
`/data/codex-web-174/compose.yaml`; persistent Codex state remains mounted.
