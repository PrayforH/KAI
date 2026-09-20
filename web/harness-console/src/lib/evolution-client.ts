import { requireAuthenticatedResponse } from "./client-auth";

export type EvolutionCandidate = {
  candidateId: string; candidateHash: string; packageHash: string; status: string; diff: string; rationale: string;
  spec: { version: string }; releasedVersion: string | null;
  trials: { trialId: string; baselineRunId: string | null; candidateRunId: string | null }[];
  comparison: null | {
    status: string; reportHash: string; caseCount: number; improved: string[]; regressed: string[];
    unresolved: string[]; unknownCostCount: number; baselineCost: number | null;
    candidateCost: number | null; conclusion: string;
  };
  review: null | { reviewer: string; reason: string; decision: string; expiresAt: string };
};
export type EvolutionJob = {
  jobId: string; revision: number; agentName: string; objective: string; status: string;
  baseline: { version: string }; allowedTargets: string[]; expiresAt: string;
  dataset: { datasetId: string; version: number }; budget: { maxCostUsd: number; maxTrials: number };
  candidates: EvolutionCandidate[];
  experiences: { experienceId: string; content: string; conditions: string; status: string; version: number }[];
  evidence: { sourceId: string; code: string; detail: string }[];
  observations: { totalRuns: number; succeededRuns: number; unknownCostRuns: number; feedbackCount: number; conclusion: string; observedAt: string }[];
  history: { action: string; actor: string; at: string }[];
};
export async function evolutionRequest<T>(path = "", body?: unknown): Promise<T> {
  const response = requireAuthenticatedResponse(await fetch(`/api/studio/evolution${path}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }));
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : error.error?.message ?? "操作失败，请刷新后重试");
  }
  return response.json();
}
