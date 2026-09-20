import type { EvolutionJob } from "./evolution-client";
import type { StudioQualityScore } from "./studio-client";
export const AGENT_SECTIONS = [
  ["overview", "概览"], ["diagnostics", "运行与诊断"], ["experiments", "改进实验"],
  ["evaluation", "评测验收"], ["release", "发布与效果"], ["datasets", "评测集"], ["experience", "经验库"],
] as const;
export type AgentSection = typeof AGENT_SECTIONS[number][0];
export function agentSection(value?: string): AgentSection {
  return AGENT_SECTIONS.some(([id]) => id === value) ? value as AgentSection : "overview";
}
export function nextAgentAction(jobs: EvolutionJob[]): { section: AgentSection; label: string; count: number }[] {
  const candidates = jobs.filter(j => j.status === "active").flatMap(j => j.candidates);
  return [
    { section: "experiments", label: "候选等待实验", count: candidates.filter(c => c.status === "proposed").length },
    { section: "evaluation", label: "候选等待验收", count: candidates.filter(c => c.status === "review_pending").length },
    { section: "release", label: "候选等待发布", count: candidates.filter(c => ["approved", "releasing"].includes(c.status)).length },
    { section: "release", label: "版本已发布，可继续观察", count: candidates.filter(c => c.status === "released").length },
  ];
}
export function productionQuality(scores: StudioQualityScore[]) {
  return scores.filter(s => !s.evalRunId && !s.agentVersion.startsWith("evo-"));
}
export function qualityGroups(scores: StudioQualityScore[]) {
  const runs = new Map<string, StudioQualityScore[]>();
  for (const score of productionQuality(scores)) runs.set(score.runId, [...(runs.get(score.runId) ?? []), score]);
  return [...runs.entries()].sort(([, a], [, b]) => b[0].createdAt.localeCompare(a[0].createdAt));
}
