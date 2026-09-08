export type MemoryStatus = "pending" | "active" | "rejected" | "deleted" | "expired" | "superseded";
export type MemorySensitivity = "personal" | "sensitive" | "prohibited";

export interface MemorySource {
  sourceId: string;
  kind: "user" | "agent" | "import";
  label: string;
  runId: string | null;
  sessionId: string | null;
  capturedAt: string;
  evidence?: string;
  extractionJobId?: string | null;
}

export interface MemoryEntry {
  tenantId: string;
  userId: string;
  agentName: string;
  entryId: string;
  content: string;
  contentHash: string;
  sensitivity: MemorySensitivity;
  status: MemoryStatus;
  version: number;
  confidence: number;
  source: MemorySource;
  consentId: string | null;
  createdAt: string;
  updatedAt: string;
  expiresAt: string | null;
  deletedAt: string | null;
  agentOwnerUserId?: string | null;
  memoryType?: "preference" | "fact" | "entity" | "decision";
  topic?: string;
  conditions?: string;
  supersedes?: string | null;
}

export interface MemoryConsent {
  agentName: string;
  allowAgentPersonal: boolean;
  version: number;
  updatedAt: string;
}

export interface MemoryRetention {
  agentName: string;
  defaultDays: number;
  maxDays: number;
  version: number;
  updatedAt: string;
}

export interface MemoryPolicy {
  consent: MemoryConsent | null;
  retention: MemoryRetention | null;
}

function errorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = payload.error;
    if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
      return error.message;
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/memory-bank/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    let payload: unknown;
    try { payload = await response.json(); } catch { payload = null; }
    throw new Error(errorMessage(payload, `记忆服务请求失败（${response.status}）`));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export interface MemorySearchHit { entry: MemoryEntry; score: number; matchedTerms: string[] }

export const memoryClient = {
  search(agentName: string, query: string, agentOwnerUserId?: string) {
    return request<MemorySearchHit[]>("search", {
      method: "POST", body: JSON.stringify({ agentName, query, agentOwnerUserId, limit: 8 }),
    });
  },
  propose(agentName: string, content: string, agentOwnerUserId?: string) {
    return request<MemoryEntry>("proposals", {
      method: "POST", body: JSON.stringify({ agentName, content, agentOwnerUserId, sourceKind: "user" }),
    });
  },
  list(agentName?: string) {
    const query = agentName ? `?agentName=${encodeURIComponent(agentName)}` : "";
    return request<MemoryEntry[]>(`entries${query}`);
  },
  confirm(entry: MemoryEntry) {
    return request<MemoryEntry>(`entries/${encodeURIComponent(entry.entryId)}/confirm`, {
      method: "POST", body: JSON.stringify({ expectedVersion: entry.version }),
    });
  },
  reject(entry: MemoryEntry) {
    return request<MemoryEntry>(`entries/${encodeURIComponent(entry.entryId)}/reject`, {
      method: "POST", body: JSON.stringify({ expectedVersion: entry.version }),
    });
  },
  update(entry: MemoryEntry, content: string) {
    return request<MemoryEntry>(`entries/${encodeURIComponent(entry.entryId)}`, {
      method: "PUT", body: JSON.stringify({ expectedVersion: entry.version, content }),
    });
  },
  remove(entry: MemoryEntry) {
    return request<void>(`entries/${encodeURIComponent(entry.entryId)}?expectedVersion=${entry.version}`, { method: "DELETE" });
  },
  policy(agentName: string, ownerId?: string) {
    return request<MemoryPolicy>(`agents/${encodeURIComponent(agentName)}/policy${ownerId ? `?agentOwnerUserId=${encodeURIComponent(ownerId)}` : ""}`);
  },
  saveConsent(agentName: string, policy: MemoryPolicy, allowAgentPersonal: boolean, ownerId?: string) {
    return request<MemoryConsent>(`agents/${encodeURIComponent(agentName)}/consent${ownerId ? `?agentOwnerUserId=${encodeURIComponent(ownerId)}` : ""}`, {
      method: "PUT",
      body: JSON.stringify({ expectedVersion: policy.consent?.version ?? 0, allowAgentPersonal }),
    });
  },
  saveRetention(agentName: string, policy: MemoryPolicy, defaultDays: number, maxDays: number, ownerId?: string) {
    return request<MemoryRetention>(`agents/${encodeURIComponent(agentName)}/retention${ownerId ? `?agentOwnerUserId=${encodeURIComponent(ownerId)}` : ""}`, {
      method: "PUT",
      body: JSON.stringify({ expectedVersion: policy.retention?.version ?? 0, defaultDays, maxDays }),
    });
  },
};
