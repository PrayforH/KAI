# public-opinion-agent

`public-opinion-agent@0.3.22` is the Codex edition of the evidence-backed Chinese
public-opinion Agent. Its Studio display name is `涉非舆情分析（Codex）`.

This package was not emitted unchanged by `harness agent init`. It is the worked
reference produced by adapting the earlier `public-opinion` behavior to the Codex loop.
The current version restores the reviewed `sentiment_query_mcp` toolset through Codex
native MCP while retaining workspace builtins. Knowledge Base references remain empty.
Three governed helper roles remain available for explicitly requested bounded parallel work.

Version notes:

- `0.3.13`: previous Claude Agent SDK release and rollback version; it has no Knowledge
  Base binding and keeps its Anthropic-compatible route.
- `0.3.14`: initial Codex release without external MCP.
- `0.3.15`: withdrawn corrective attempt; its Studio draft conversion lost the Codex
  runtime field and the published snapshot therefore remained on Claude SDK.
- `0.3.16`: previous Codex release with eight reviewed sentiment MCP tools; Knowledge
  Base references remain empty.
- `0.3.18`: long-running Codex release with independent model-turn and tool-call
  budgets, a two-hour runtime envelope, and three governed helper roles.
- `0.3.19`: depth-restoration release. Published Skills are mirrored into Codex's
  repository-native `.agents/skills` discovery root, while `.claude/skills` remains
  the compatibility source. Query, risk, report, pagination and final completeness
  checks are mandatory; Knowledge Base and public web search remain disabled.
- `0.3.20`: artifact-naming release. Final reports use a safe name derived from the
  confirmed analysis subject or event instead of the fixed `public-opinion-report`
  basename. Existing 0.3.19 sessions remain immutable.
- `0.3.21`: fast-start release. The Agent pins Codex reasoning effort to `low` and
  requires an immediate first tool action when the task target is already clear;
  report depth, long-run budgets and artifact naming remain unchanged.
- `0.3.22`: terminology-hardening release. In this Agent, `涉非` is normalized only
  to illegal fundraising and related illegal-finance risk; Africa-related query
  expansion is forbidden unless the user explicitly asks for it.

Validate and package:

```bash
uv run harness agent check agents/public-opinion-agent/agent.yaml --environment production
uv run harness agent pack agents/public-opinion-agent/agent.yaml
```

The published Codex Manifest pins `codex-deepseek-v4-flash`; the legacy Agent-wide
Studio binding remains on the Claude-compatible `deepseek-v4-flash` route so 0.3.13 and
0.3.22 can coexist. Existing Sessions stay pinned to their version and route snapshot;
create a new Session after a version change.
