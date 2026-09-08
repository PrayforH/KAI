/** Team collaboration is temporarily unavailable in the Web product. */
export const TEAM_COLLABORATION_ENABLED = false;

export function isAgentVisible(
  agent: { name?: string; scope?: string; spaceId?: string | null; parentDraftId?: string | null; internal?: boolean },
  showInternal = false,
) {
  if (!TEAM_COLLABORATION_ENABLED && (agent.scope === "team" || agent.spaceId)) return false;
  return showInternal || !(agent.internal || agent.parentDraftId || ["echo-agent", "helper-agent"].includes(agent.name ?? ""));
}
