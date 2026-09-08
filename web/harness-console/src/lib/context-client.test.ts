import { describe, expect, it, vi } from "vitest";

import {
  contextTrustLabels,
  loadThreadContext,
  mergeContextPages,
  rebaseThreadContext,
  shortContextHash,
  type SessionContextOverview,
} from "./context-client";

function overview(versions: number[], next: number | null): SessionContextOverview {
  return {
    session_id: "session-a",
    state: null,
    digests: versions.map((version) => ({
      digest_id: `digest-${version}`,
      version,
      source: {
        through_run_id: `run-${version}`,
        through_event_sequence: version,
        transcript_checkpoint_hash: `sha256:${"a".repeat(64)}`,
      },
      trust_high_watermark: "safe",
      facts: [],
      decisions: [],
      open_tasks: [],
      artifact_refs: [],
      workspace_refs: [],
      created_by: {
        route_id: "context-digest-v1",
        model: "deterministic",
        prompt_revision: "v1",
      },
      created_at: "2026-08-09T00:00:00Z",
      content_hash: `sha256:${"b".repeat(64)}`,
    })),
    next_before_version: next,
  };
}

describe("context recovery client", () => {
  it("merges cursor pages without duplicate recovery versions", () => {
    const current = overview([4, 3], 3);
    current.window = {
      source_run_id: "run-4",
      observed_at: "2026-08-09T00:00:00Z",
      phase: "after",
      total_tokens: 135_000,
      max_tokens: 180_000,
      raw_max_tokens: 200_000,
      headroom_tokens: 45_000,
      percentage: 75,
      model: "claude-sonnet",
      auto_compact_enabled: true,
      auto_compact_threshold: 175_000,
      provider_threshold_percentage: 97.22,
      categories: [],
      level: "compact_ready",
      soft_threshold_percentage: 65,
      compact_ready_percentage: 75,
      hard_threshold_percentage: 85,
      recommended_action: "consider_rebase",
    };
    current.window_status = {
      status: "available",
      checked_at: "2026-08-09T00:00:00Z",
      source_run_id: "run-4",
    };
    const merged = mergeContextPages(current, overview([3, 2], 2));
    expect(merged.digests.map((item) => item.version)).toEqual([4, 3, 2]);
    expect(merged.next_before_version).toBe(2);
    expect(merged.window?.level).toBe("compact_ready");
    expect(merged.window_status?.status).toBe("available");
  });

  it("keeps trust and hashes concise for the task header", () => {
    expect(contextTrustLabels.untrusted).toBe("含不可信来源");
    expect(shortContextHash(`sha256:${"a".repeat(64)}`)).toBe("aaaaaaaaaaaa…");
  });

  it("treats a not-yet-created thread as no context instead of an error", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("", { status: 404 }));
    await expect(loadThreadContext("new/thread", undefined, fetcher)).resolves.toBeNull();
    expect(fetcher).toHaveBeenCalledWith(
      "/api/agui/threads/new%2Fthread/context?limit=10",
      { cache: "no-store" },
    );
  });

  it("rebases through an authenticated same-origin mutation", async () => {
    const result = {
      thread_id: "thread-a",
      previous_session_id: "session-a",
      session_id: "session_ctx_b",
      digest: overview([1], null).digests[0],
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(result));

    await expect(rebaseThreadContext("thread/a", fetcher)).resolves.toEqual(result);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/agui/threads/thread%2Fa/context/rebase",
      { method: "POST" },
    );
  });

  it("surfaces the API conflict detail for an active Run", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      Response.json(
        { detail: "cannot rebase context while this task has an active Run" },
        { status: 409 },
      ),
    );
    await expect(rebaseThreadContext("thread-a", fetcher)).rejects.toThrow(
      "cannot rebase context while this task has an active Run",
    );
  });
});
