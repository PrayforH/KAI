import { describe, expect, it, vi } from "vitest";
import { studioClient } from "../src/lib/studio-client";

/**
 * The rest of the Studio client spec mocks team collaboration on to exercise
 * the shared-workspace paths. This file keeps the real flag so the personal-only
 * behaviour is covered too.
 */
describe("Studio draft listing while team collaboration is disabled", () => {
    it("returns only personal drafts while team collaboration is disabled", async () => {
      const personal = {
        draftId: "draft-personal",
        agentId: "agent-personal",
        spaceId: null,
        name: "personal-agent",
        displayName: "个人智能体",
        domain: "general",
        version: "0.1.0",
        template: "analyst" as const,
        revision: 2,
        updatedAt: "2026-08-12T01:00:00Z",
        publishedVersion: "0.1.0",
      };
      const shared = {
        ...personal,
        draftId: "draft-shared",
        agentId: "agent-shared",
        spaceId: "space-team",
        name: "shared-agent",
        displayName: "协作智能体",
        updatedAt: "2026-08-12T02:00:00Z",
      };
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/studio/drafts") return Response.json([personal, shared]);
        return new Response("not found", { status: 404 });
      });
      vi.stubGlobal("fetch", fetchMock);

      const drafts = await studioClient.listAccessibleDrafts();

      expect(drafts.map((item) => item.draftId)).toEqual(["draft-personal"]);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
});
