import type { StudioPlatformSkillPackage } from "./studio-client";

type SearchableSkill = {
  name: string;
  displayName: string;
  description: string;
  agents: Array<{ label: string }>;
  package: Pick<StudioPlatformSkillPackage, "packageId" | "sourceUrl" | "tags"> | null;
};

export function matchesPlatformSkillQuery(pkg: Pick<StudioPlatformSkillPackage, "packageId" | "displayName" | "summary" | "sourceUrl" | "tags">, query: string): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const searchable = [pkg.packageId, pkg.displayName, pkg.summary, pkg.sourceUrl, ...pkg.tags].join(" ").toLowerCase();
  return terms.every((term) => searchable.includes(term));
}

export function matchesSkillQuery(skill: SearchableSkill, query: string): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const searchable = [
    skill.name,
    skill.displayName,
    skill.description,
    skill.package?.packageId,
    skill.package?.sourceUrl,
    ...(skill.package?.tags ?? []),
    ...skill.agents.map((agent) => agent.label),
  ].filter(Boolean).join(" ").toLowerCase();
  return terms.every((term) => searchable.includes(term));
}
