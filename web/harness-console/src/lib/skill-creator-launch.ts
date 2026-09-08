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
  description: "通过对话创建或更新 Skill，并按目标作用域生成可审阅的 SKILL.md。",
  instructions: [
    "先确认 Skill 的目标、触发场景和一个真实请求示例。",
    "只补问会实质改变结果的缺失信息。",
    "生成简洁的 SKILL.md，并根据需要拆分 references、scripts 或 assets。",
    "明确安装到个人、平台或指定 Agent；不得混淆作用域。",
    "写入前展示结果并等待用户确认。",
  ].join("\n"),
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
