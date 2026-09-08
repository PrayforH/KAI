import { requireAuthenticatedResponse } from "./client-auth";

export type ContextTrust = "safe" | "sensitive" | "untrusted";

export interface ContextDigestEntry {
  text: string;
  source_refs: string[];
  trust: ContextTrust;
}

export interface ContextDigestObjectRef {
  ref: string;
  content_hash: string;
  title: string;
  media_type?: string | null;
}

export interface SessionContextDigest {
  digest_id: string;
  version: number;
  source: {
    through_run_id: string;
    through_event_sequence: number;
    transcript_checkpoint_hash: string;
  };
  trust_high_watermark: ContextTrust;
  facts: ContextDigestEntry[];
  decisions: ContextDigestEntry[];
  open_tasks: ContextDigestEntry[];
  artifact_refs: ContextDigestObjectRef[];
  workspace_refs: ContextDigestObjectRef[];
  created_by: {
    route_id: string;
    model: string;
    prompt_revision: string;
  };
  created_at: string;
  content_hash: string;
}

export interface SessionContextState {
  revision: number;
  trust_high_watermark: ContextTrust;
  latest_digest_id?: string | null;
  latest_digest_version: number;
  updated_at: string;
}

export type ContextBudgetLevel = "green" | "watch" | "compact_ready" | "emergency";

export interface ContextWindowSnapshot {
  source_run_id: string;
  observed_at: string;
  phase: string;
  total_tokens: number;
  max_tokens: number;
  raw_max_tokens: number;
  headroom_tokens: number;
  percentage: number;
  model: string;
  auto_compact_enabled: boolean;
  auto_compact_threshold?: number | null;
  provider_threshold_percentage?: number | null;
  categories: Array<{ name: string; tokens: number }>;
  level: ContextBudgetLevel;
  soft_threshold_percentage: number;
  compact_ready_percentage: number;
  hard_threshold_percentage: number;
  recommended_action: "none" | "reduce_optional_context" | "consider_rebase" | "rebase_now";
}

export interface ContextWindowAvailability {
  status: "pending" | "available" | "unavailable";
  checked_at?: string | null;
  source_run_id?: string | null;
  reason?: "control_timeout" | "control_unavailable" | null;
}

export interface SessionContextOverview {
  session_id: string;
  state: SessionContextState | null;
  digests: SessionContextDigest[];
  next_before_version: number | null;
  previous_session_count?: number;
  rebase_supported?: boolean;
  rollback_supported?: boolean;
  window?: ContextWindowSnapshot | null;
  window_status?: ContextWindowAvailability;
}

export interface ContextRebaseResult {
  thread_id: string;
  previous_session_id: string;
  session_id: string;
  digest: SessionContextDigest;
}

export const contextTrustLabels: Record<ContextTrust, string> = {
  safe: "受信",
  sensitive: "含敏感信息",
  untrusted: "含不可信来源",
};

export function shortContextHash(value: string) {
  const hash = value.startsWith("sha256:") ? value.slice(7) : value;
  return hash.length > 12 ? `${hash.slice(0, 12)}…` : hash;
}

export function mergeContextPages(
  current: SessionContextOverview,
  next: SessionContextOverview,
): SessionContextOverview {
  const versions = new Set<number>();
  const digests = [...current.digests, ...next.digests].filter((digest) => {
    if (versions.has(digest.version)) return false;
    versions.add(digest.version);
    return true;
  });
  return {
    session_id: next.session_id,
    state: next.state ?? current.state,
    digests,
    next_before_version: next.next_before_version,
    previous_session_count: next.previous_session_count ?? current.previous_session_count,
    rebase_supported: next.rebase_supported ?? current.rebase_supported,
    rollback_supported: next.rollback_supported ?? current.rollback_supported,
    window: next.window ?? current.window,
    window_status: next.window_status ?? current.window_status,
  };
}

async function mutateThreadContext(
  threadId: string,
  operation: "rebase" | "rollback",
  fetcher: typeof fetch,
): Promise<ContextRebaseResult> {
  const suffix = operation === "rebase" ? "rebase" : "rebase/rollback";
  const response = requireAuthenticatedResponse(
    await fetcher(
      `/api/agui/threads/${encodeURIComponent(threadId)}/context/${suffix}`,
      { method: "POST" },
    ),
  );
  if (!response.ok) {
    const body = await response.text();
    try {
      const detail = (JSON.parse(body) as { detail?: string }).detail;
      throw new Error(detail || `HTTP ${response.status}`);
    } catch (cause) {
      if (cause instanceof SyntaxError) {
        throw new Error(body || `HTTP ${response.status}`);
      }
      throw cause;
    }
  }
  return response.json() as Promise<ContextRebaseResult>;
}

export function rebaseThreadContext(
  threadId: string,
  fetcher: typeof fetch = fetch,
) {
  return mutateThreadContext(threadId, "rebase", fetcher);
}

export function rollbackThreadContextRebase(
  threadId: string,
  fetcher: typeof fetch = fetch,
) {
  return mutateThreadContext(threadId, "rollback", fetcher);
}

export async function loadThreadContext(
  threadId: string,
  beforeVersion?: number,
  fetcher: typeof fetch = fetch,
): Promise<SessionContextOverview | null> {
  const query = new URLSearchParams({ limit: "10" });
  if (beforeVersion !== undefined) {
    query.set("before_version", String(beforeVersion));
  }
  const response = requireAuthenticatedResponse(
    await fetcher(
      `/api/agui/threads/${encodeURIComponent(threadId)}/context?${query}`,
      { cache: "no-store" },
    ),
  );
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error((await response.text()) || `HTTP ${response.status}`);
  }
  return response.json() as Promise<SessionContextOverview>;
}
