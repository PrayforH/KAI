import { describe, expect, it } from "vitest";
import { DEFAULT_STUDIO_DRAFT } from "../src/lib/agent-studio";
import {
  studioDraftToSpec,
  type ApiAgentDraft,
} from "../src/lib/studio-client";
import {
  LEGACY_STUDIO_DRAFT_KEY,
  LEGACY_STUDIO_MIGRATION_KEY,
  migrateLegacyStudioDraft,
} from "../src/lib/studio-migration";

class MemoryStorage {
  private readonly values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

function apiDraft(revision: number): ApiAgentDraft {
  return {
    draftId: "draft-imported",
    agentId: "agent-imported",
    spaceId: null,
    tenantId: "tenant-a",
    revision,
    spec: studioDraftToSpec({
      ...DEFAULT_STUDIO_DRAFT,
      id: "draft-imported",
      revision,
    }),
    createdBy: "builder-a",
    updatedBy: "builder-a",
    createdAt: "2026-07-16T00:00:00Z",
    updatedAt: "2026-07-16T00:00:00Z",
    publishedVersion: null,
    publishedHash: null,
    publishedPackageHash: null,
  };
}

describe("legacy Studio draft migration", () => {
  it("imports once, preserves the full draft, then records a durable marker", async () => {
    const storage = new MemoryStorage();
    storage.setItem(LEGACY_STUDIO_DRAFT_KEY, JSON.stringify(DEFAULT_STUDIO_DRAFT));
    let creates = 0;
    let replaces = 0;
    const client = {
      async getDraft() { return apiDraft(1); },
      async createDraft() { creates += 1; return apiDraft(1); },
      async replaceDraft() { replaces += 1; return apiDraft(2); },
    };

    const first = await migrateLegacyStudioDraft(storage, client, true);
    const second = await migrateLegacyStudioDraft(storage, client, true);

    expect(first.status).toBe("imported");
    expect(first.draft?.revision).toBe(2);
    expect(second.status).toBe("already-migrated");
    expect(creates).toBe(1);
    expect(replaces).toBe(1);
    expect(storage.getItem(LEGACY_STUDIO_DRAFT_KEY)).toBeNull();
    expect(storage.getItem(LEGACY_STUDIO_MIGRATION_KEY)).toBe("draft-imported");
  });

  it("keeps the browser source when the API is offline so reload can retry", async () => {
    const storage = new MemoryStorage();
    const serialized = JSON.stringify(DEFAULT_STUDIO_DRAFT);
    storage.setItem(LEGACY_STUDIO_DRAFT_KEY, serialized);
    const client = {
      async getDraft() { return apiDraft(1); },
      async createDraft() { throw new Error("offline"); },
      async replaceDraft() { return apiDraft(2); },
    };

    await expect(migrateLegacyStudioDraft(storage, client, true)).rejects.toThrow("offline");

    expect(storage.getItem(LEGACY_STUDIO_DRAFT_KEY)).toBe(serialized);
    expect(storage.getItem(LEGACY_STUDIO_MIGRATION_KEY)).toBeNull();
  });

  it("discards malformed legacy JSON once without calling the API", async () => {
    const storage = new MemoryStorage();
    storage.setItem(LEGACY_STUDIO_DRAFT_KEY, "not-json");
    let called = false;
    const client = {
      async getDraft() { called = true; return apiDraft(1); },
      async createDraft() { called = true; return apiDraft(1); },
      async replaceDraft() { called = true; return apiDraft(2); },
    };

    const result = await migrateLegacyStudioDraft(storage, client, true);

    expect(result.status).toBe("discarded");
    expect(called).toBe(false);
    expect(storage.getItem(LEGACY_STUDIO_DRAFT_KEY)).toBeNull();
    expect(storage.getItem(LEGACY_STUDIO_MIGRATION_KEY)).toBe("discarded-invalid");
  });

  it("resumes the same server draft when replacing the complete spec initially fails", async () => {
    const storage = new MemoryStorage();
    storage.setItem(LEGACY_STUDIO_DRAFT_KEY, JSON.stringify(DEFAULT_STUDIO_DRAFT));
    let creates = 0;
    let gets = 0;
    let replaces = 0;
    const client = {
      async getDraft(draftId: string) {
        gets += 1;
        expect(draftId).toBe("draft-imported");
        return apiDraft(1);
      },
      async createDraft() {
        creates += 1;
        return apiDraft(1);
      },
      async replaceDraft() {
        replaces += 1;
        if (replaces === 1) throw new Error("connection lost");
        return apiDraft(2);
      },
    };

    await expect(migrateLegacyStudioDraft(storage, client, true)).rejects.toThrow(
      "connection lost",
    );
    expect(storage.getItem(LEGACY_STUDIO_MIGRATION_KEY)).toBe(
      "pending:draft-imported",
    );
    expect(storage.getItem(LEGACY_STUDIO_DRAFT_KEY)).not.toBeNull();

    const result = await migrateLegacyStudioDraft(storage, client, true);

    expect(result.status).toBe("imported");
    expect(creates).toBe(1);
    expect(gets).toBe(1);
    expect(replaces).toBe(2);
    expect(storage.getItem(LEGACY_STUDIO_DRAFT_KEY)).toBeNull();
    expect(storage.getItem(LEGACY_STUDIO_MIGRATION_KEY)).toBe("draft-imported");
  });
});
