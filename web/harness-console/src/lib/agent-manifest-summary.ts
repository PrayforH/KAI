import { studioClient, type ApiAgentDraft } from "./studio-client";

/**
 * System-prompt summary for the trace console's 系统 node. The durable run
 * events do not carry the prompt itself; it lives on the versioned draft, so
 * the console resolves it once per agent version through the studio API the
 * builder already uses. Viewing permissions stay as-is for now and can be
 * tightened later; any failure degrades to the runtime-facts-only variant.
 */
export interface AgentManifestSummary {
  systemPrompt?: string;
  entries: Array<{ name: string; description: string }>;
}

const cache = new Map<string, Promise<AgentManifestSummary | null>>();

export function agentManifestKey(name: string, version: string): string {
  return `${name}@${version}`;
}

function summaryOf(draft: ApiAgentDraft): AgentManifestSummary {
  return {
    systemPrompt: draft.spec.systemPrompt,
    entries: draft.spec.skills.map((skill) => ({
      name: skill.name,
      description: skill.description ?? "",
    })),
  };
}

export function fetchAgentManifestSummary(
  name: string,
  version: string,
): Promise<AgentManifestSummary | null> {
  const key = agentManifestKey(name, version);
  const cached = cache.get(key);
  if (cached) return cached;
  const pending = (async () => {
    try {
      const drafts = await studioClient.listAccessibleDrafts();
      const draftSummary = drafts.find(
        (item) =>
          item.name === name &&
          (item.publishedVersion === version || item.version === version),
      );
      if (!draftSummary) return null;
      const draft = await studioClient.getDraft(draftSummary.draftId);
      return summaryOf(draft);
    } catch {
      return null;
    }
  })();
  cache.set(key, pending);
  return pending;
}
