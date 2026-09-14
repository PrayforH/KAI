export type SkillInstallScope = "personal" | "platform" | "agent";

export interface SkillCreatorLaunch {
  name: "skill-creator";
  displayName: "Skill Creator";
  scope: SkillInstallScope;
  agentDraftId?: string;
  agentLabel?: string;
}

export const SKILL_SCOPE_LABELS: Record<SkillInstallScope, string> = {
  personal: "个人 Skill",
  platform: "平台 Skill",
  agent: "Agent Skill",
};

export const DEFAULT_SKILL_CREATOR = {
  name: "skill-creator" as const,
  displayName: "Skill Creator" as const,

};

export function parseSkillCreatorLaunch(
  search: URLSearchParams,
): SkillCreatorLaunch | null {
  if (search.get("skill") !== DEFAULT_SKILL_CREATOR.name) return null;
  const requestedScope = search.get("skillScope");
  const scope: SkillInstallScope =
    requestedScope === "platform" || requestedScope === "agent"
      ? requestedScope
      : "personal";
  const agentDraftId = search.get("agentDraft")?.trim() || undefined;
  const agentLabel = search.get("agentLabel")?.trim() || undefined;
  return {
    name: DEFAULT_SKILL_CREATOR.name,
    displayName: DEFAULT_SKILL_CREATOR.displayName,
    scope,
    agentDraftId,
    agentLabel,
  };
}

export function skillCreatorPrompt(launch: SkillCreatorLaunch) {
  const target = launch.scope === "agent" && launch.agentLabel
    ? `${SKILL_SCOPE_LABELS.agent}（${launch.agentLabel}）`
    : SKILL_SCOPE_LABELS[launch.scope];
  return `请帮我创建一个${target}：`;
}

export function skillCreatorHref(
  scope: SkillInstallScope,
  options: { agentDraftId?: string; agentLabel?: string } = {},
) {
  const search = new URLSearchParams({
    skill: DEFAULT_SKILL_CREATOR.name,
    skillScope: scope,
  });
  if (options.agentDraftId) search.set("agentDraft", options.agentDraftId);
  if (options.agentLabel) search.set("agentLabel", options.agentLabel);
  return `/?${search.toString()}`;
}
