import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_STUDIO_DRAFT } from "../src/lib/agent-studio";
import { TEAM_COLLABORATION_ENABLED } from "../src/lib/agent-visibility";
import {
  apiDraftToStudioDraft,
  StudioApiError,
  studioClient,
  studioDraftToSpec,
  type ApiAgentDraft,
  type StudioEnvironmentResourcePolicy,
} from "../src/lib/studio-client";

function apiDraft(): ApiAgentDraft {
  const spec = studioDraftToSpec({
    ...DEFAULT_STUDIO_DRAFT,
    id: "draft-api",
    revision: 3,
  });
  return {
    draftId: "draft-api",
    agentId: "agent-api",
    spaceId: null,
    tenantId: "tenant-a",
    revision: 3,
    spec,
    createdBy: "builder-a",
    updatedBy: "builder-b",
    createdAt: "2026-07-16T00:00:00Z",
    updatedAt: "2026-07-16T00:01:00Z",
    publishedVersion: null,
    publishedHash: null,
    publishedPackageHash: null,
  };
}

function environmentPolicy(): StudioEnvironmentResourcePolicy {
  return {
    executionProfileId: "isolated-default",
    executionProfileVersion: 1,
    networkProfileId: "registered-mcp-only",
    networkProfileVersion: 1,
    networkAccess: ["none", "internal", "external"],
    allowedModelRoutes: ["new-api-default"],
    capabilityCatalogRevision: 1,
    allowedMcpReferences: ["tavily-readonly"],
    allowedKnowledgeReferences: [],
    credentialScopes: ["user", "team", "workload"],
    quota: {
      maxRunBudgetUsd: 1,
      maxModelTokens: 200_000,
      maxArtifactBytes: 26_214_400,
    },
  };
}

describe("Studio typed API mapping", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("round-trips the editable server Draft without losing revision or files", () => {
    const source = apiDraft();
    source.spec.skills[0].files = [{ path: "references/rules.md", content: "rules" }];
    source.spec.pythonTools = [{
      name: "score_signal",
      description: "Score a normalized signal inside the sandbox.",
      inputSchema: {
        type: "object",
        properties: { value: { type: "number" } },
        required: ["value"],
      },
      code: "def run(arguments):\n    return {\"score\": arguments[\"value\"]}",
    }];

    const draft = apiDraftToStudioDraft(source);
    const saved = studioDraftToSpec(draft);

    expect(draft.id).toBe("draft-api");
    expect(draft.revision).toBe(3);
    expect(draft.executionProfile).toBe("isolated-default");
    expect(draft.maxModelTokens).toBeNull();
    expect(saved.skills[0].files).toEqual([
      { path: "references/rules.md", content: "rules" },
    ]);
    expect(saved.pythonTools).toEqual(source.spec.pythonTools);
    expect(saved.model.requiredCapabilities).toEqual(["streaming", "tool_use"]);
    expect(saved.toolExposureMode).toBe(DEFAULT_STUDIO_DRAFT.toolExposureMode);
    expect(saved.limits.maxModelTokens).toBeNull();
  });

  it("prefetches a draft once and reuses it when the selected revision matches", async () => {
    const prefetched = {
      ...apiDraft(),
      draftId: "draft-prefetch",
      revision: 7,
    };
    const fetchMock = vi.fn().mockResolvedValue(Response.json(prefetched));
    vi.stubGlobal("fetch", fetchMock);

    await studioClient.prefetchDraft(prefetched.draftId, prefetched.revision);
    const selected = await studioClient.getDraft(prefetched.draftId, {
      expectedRevision: prefetched.revision,
    });

    expect(selected).toEqual(prefetched);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

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

  it.skipIf(!TEAM_COLLABORATION_ENABLED)(
    "combines personal and accessible workspace drafts for the Studio list",
    async () => {
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
        if (url === "/api/studio/drafts") return Response.json([personal]);
        if (url === "/api/spaces") {
          return Response.json([{ space: { spaceId: "space-team" } }]);
        }
        if (url === "/api/studio/drafts?spaceId=space-team") {
          return Response.json([shared]);
        }
        return new Response("not found", { status: 404 });
      });
      vi.stubGlobal("fetch", fetchMock);

      const drafts = await studioClient.listAccessibleDrafts();

      expect(drafts.map((item) => item.draftId)).toEqual([
        "draft-shared",
        "draft-personal",
      ]);
      expect(fetchMock).toHaveBeenCalledTimes(3);
    },
  );

  it("falls back to the stable draft endpoint when task-first creation is unavailable", async () => {
    const created = apiDraft();
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      if (String(input).endsWith("/drafts/from-task")) {
        return Response.json({ detail: "Method Not Allowed" }, { status: 405 });
      }
      return Response.json(created);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await studioClient.createDraftFromTask({
      task: "股票投资助手",
      runtimePreference: "auto",
    });

    expect(result).toEqual({ draft: created, recommendation: null });
    expect(calls).toHaveLength(2);
    expect(calls[0]).toMatchObject({
      url: "/api/studio/drafts/from-task",
      method: "POST",
      body: { task: "股票投资助手", runtimePreference: "auto" },
    });
    expect(calls[1]).toMatchObject({
      url: "/api/studio/drafts",
      method: "POST",
      body: {
        domain: "general",
        displayName: "股票投资助手",
        description: "股票投资助手",
        template: "analyst",
      },
    });
    expect((calls[1].body as { name: string }).name).toMatch(/^task-agent-[a-f0-9]{8}$/);
  });

  it("imports a ZIP bundle without converting it to JSON", async () => {
    let captured: { url: string; contentType: string | null; body: BodyInit | null } | null = null;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      captured = {
        url: String(input),
        contentType: new Headers(init?.headers).get("Content-Type"),
        body: init?.body ?? null,
      };
      return Response.json({
        draft: apiDraft(),
        sourceContentHash: "a".repeat(64),
        sourcePackageHash: "b".repeat(64),
        lossless: true,
        roundTripVerified: true,
        warnings: [],
      });
    });
    const bundle = new Blob(["agent-bundle"], { type: "application/zip" });

    const imported = await studioClient.importBundle(bundle);

    expect(captured).toEqual({
      url: "/api/studio/drafts/import",
      contentType: "application/zip",
      body: bundle,
    });
    expect(imported.lossless).toBe(true);
    expect(imported.roundTripVerified).toBe(true);
  });

  it("uploads a Skill archive directly and preserves its filename", async () => {
    let captured: { url: string; contentType: string | null; body: BodyInit | null } | null = null;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      captured = {
        url: String(input),
        contentType: new Headers(init?.headers).get("Content-Type"),
        body: init?.body ?? null,
      };
      return Response.json({
        skill: {
          name: "ppt-master",
          description: "Build presentations.",
          instructions: "Create the deck.",
          files: [],
        },
        sourceContentHash: "a".repeat(64),
        riskLevel: "low",
        findings: [],
        warnings: [],
      });
    });
    const skill = new File(["skill"], "ppt master.zip", { type: "application/zip" });

    const imported = await studioClient.importSkill(skill);

    expect(captured).toEqual({
      url: "/api/studio/skills/import?filename=ppt%20master.zip",
      contentType: "application/zip",
      body: skill,
    });
    expect(imported.skill.name).toBe("ppt-master");
  });

  it("lists platform Skill packages and installs an exact revision", async () => {
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      if (!init?.method) return Response.json({ revision: 1, packages: [] });
      return Response.json({
        draft: apiDraft(),
        skillName: "evidence-reporting",
        sourceContentHash: "a".repeat(64),
        riskLevel: "low",
        findings: [],
        warnings: [],
        fileCount: 1,
        binaryFileCount: 0,
      });
    });

    await studioClient.listPlatformSkills();
    await studioClient.installPlatformSkill("draft-1", 4, "evidence-reporting", 2);

    expect(calls).toEqual([
      { url: "/api/studio/skills/catalog", method: "GET", body: null },
      {
        url: "/api/studio/drafts/draft-1/skills/catalog/evidence-reporting/install",
        method: "POST",
        body: { expectedRevision: 4, packageRevision: 2 },
      },
    ]);
  });

  it("reads usage and replaces quota policy with revision CAS", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
      if (!init?.method) return Response.json({ policies: [], counters: [], activeReservations: [], unknownCostEntries: 0 });
      return Response.json({ policyId: "tenant-default", revision: 2 });
    });

    await studioClient.quotaUsage();
    await studioClient.replaceQuotaPolicy("tenant-default", 1, { concurrent_runs: 12 });

    expect(calls).toEqual([
      { url: "/api/studio/quotas", body: null },
      { url: "/api/studio/quotas/tenant-default", body: { expectedRevision: 1, scope: { agentName: null, environment: null }, limits: { concurrent_runs: 12 } } },
    ]);
  });

  it("sends the current Skill and conversation to the model authoring endpoint", async () => {
    let captured: { url: string; body: unknown } | null = null;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      captured = {
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
      };
      return Response.json({
        status: "clarifying",
        reply: "请提供一个真实请求示例。",
        skill: null,
        followUpQuestions: ["用户会怎样提出请求？"],
      });
    });
    const skill = DEFAULT_STUDIO_DRAFT.skills[0];

    const reply = await studioClient.continueSkillConversation({
      modelRoute: DEFAULT_STUDIO_DRAFT.modelRoute,
      context: {
        agentName: DEFAULT_STUDIO_DRAFT.name,
        displayName: DEFAULT_STUDIO_DRAFT.displayName,
        domain: DEFAULT_STUDIO_DRAFT.domain,
        description: DEFAULT_STUDIO_DRAFT.description,
        currentSkill: skill,
      },
      messages: [{ role: "user", content: "帮我补全这个 Skill" }],
    });

    expect(captured).toEqual({
      url: "/api/studio/skills/conversation",
      body: {
        modelRoute: DEFAULT_STUDIO_DRAFT.modelRoute,
        context: {
          agentName: DEFAULT_STUDIO_DRAFT.name,
          displayName: DEFAULT_STUDIO_DRAFT.displayName,
          domain: DEFAULT_STUDIO_DRAFT.domain,
          description: DEFAULT_STUDIO_DRAFT.description,
          currentSkill: skill,
        },
        messages: [{ role: "user", content: "帮我补全这个 Skill" }],
      },
    });
    expect(reply.status).toBe("clarifying");
    expect(reply.followUpQuestions).toEqual(["用户会怎样提出请求？"]);
  });

  it("writes and disables MCP catalog entries with revision CAS", async () => {
    const calls: Array<{ url: string; method: string | undefined; body: unknown }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return Response.json({
        record: { revision: 8, catalog: { mcpServers: [] } },
        impact: { resourceType: "mcp", resourceId: "company-search", draftIds: [] },
      });
    });

    const resource = {
      reference: "company-search",
      ownerUserId: null,
      allowedExecutionProfileIds: ["local-development"],
      category: "tool" as const,
      serverName: "company",
      label: "企业搜索",
      description: "查询企业内部资料",
      endpointUrl: "https://mcp.example.com/mcp",
      transport: "http" as const,
      customHeaders: {},
      tools: ["mcp__company__search"],
      risk: "medium" as const,
      networkAccess: "internal" as const,
      sendsUserData: true,
      readOnly: true,
      credentialManaged: true,
      executionLocation: "external-mcp",
      preflightRequired: true,
      credentialReference: "COMPANY_MCP_TOKEN",
      authMode: "bearer" as const,
      authName: null,
      authKey: "authorization",
      version: 1,
      enabled: true,
    };
    await studioClient.upsertMcp(
      resource.reference,
      7,
      resource,
      ["local-development"],
    );
    await studioClient.disableMcp(resource.reference, 8);

    expect(calls).toEqual([
      {
        url: "/api/studio/catalog/mcp/company-search",
        method: "PUT",
        body: {
          expectedRevision: 7,
          resource,
          allowedExecutionProfileIds: ["local-development"],
        },
      },
      {
        url: "/api/studio/catalog/mcp/company-search?expected_revision=8",
        method: "DELETE",
        body: null,
      },
    ]);
  });

  it("surfaces FastAPI MCP discovery detail instead of a generic status", async () => {
    vi.stubGlobal("fetch", async () => Response.json(
      {
        detail: {
          code: "mcp_unreachable",
          message: "MCP initialize / tools/list failed",
        },
      },
      { status: 422 },
    ));

    await expect(studioClient.discoverMcp({
      reference: "company-search",
      serverName: "company",
      endpointUrl: "http://company-mcp:4174/mcp",
      networkAccess: "internal",
      customHeaders: {},
      authMode: "none",
      authName: null,
      authKey: "authorization",
    })).rejects.toMatchObject({
      status: 422,
      code: "mcp_unreachable",
      message: "MCP initialize / tools/list failed",
    } satisfies Partial<StudioApiError>);
  });

  it("discovers MCP tools through the server boundary", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({
        endpointUrl: "https://mcp.example.com/mcp",
        serverName: "company",
        serverTitle: "Company MCP",
        serverVersion: "1.0.0",
        latencyMs: 24,
        tools: [
          {
            name: "search",
            canonicalName: "mcp__company__search",
            title: "Search",
            description: "Search documents",
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetcher);

    await studioClient.discoverMcp({
      reference: "company-search",
      serverName: "company",
      endpointUrl: "https://mcp.example.com/mcp",
      networkAccess: "external",
      customHeaders: {},
      authMode: "none",
      authName: null,
      authKey: "authorization",
    });

    expect(fetcher).toHaveBeenCalledWith(
      "/api/studio/mcp/discover",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          reference: "company-search",
          serverName: "company",
          endpointUrl: "https://mcp.example.com/mcp",
          networkAccess: "external",
          customHeaders: {},
          authMode: "none",
          authName: null,
          authKey: "authorization",
        }),
      }),
    );
  });

  it("maps server eval tags into the editor and restores them on save", () => {
    const source = apiDraft();
    source.spec.evaluationCases[0].tags = ["safety", "public-opinion"];

    const draft = apiDraftToStudioDraft(source);

    expect(draft.evalCases[0].tag).toBe("safety");
    expect(studioDraftToSpec(draft).evaluationCases[0].tags).toContain("safety");
  });

  it("preserves 409 as a typed revision conflict", async () => {
    vi.stubGlobal("fetch", async () => Response.json(
      { error: { code: "draft_conflict", message: "revision changed" } },
      { status: 409 },
    ));

    await expect(studioClient.replaceDraft(DEFAULT_STUDIO_DRAFT)).rejects.toMatchObject({
      status: 409,
      code: "draft_conflict",
    } satisfies Partial<StudioApiError>);
  });

  it("deletes a draft with revision protection and invalidates its cache", async () => {
    const source = apiDraft();
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method ?? "GET" });
      if ((init?.method ?? "GET") === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return Response.json(source);
    });

    await studioClient.getDraft(source.draftId, { maxAgeMs: 0 });
    await studioClient.deleteDraft(source.draftId, source.revision);
    await studioClient.getDraft(source.draftId);

    expect(calls).toEqual([
      { url: "/api/studio/drafts/draft-api", method: "GET" },
      {
        url: "/api/studio/drafts/draft-api?expectedRevision=3",
        method: "DELETE",
      },
      { url: "/api/studio/drafts/draft-api", method: "GET" },
    ]);
  });

  it("publishes the exact server revision and preserves a version conflict", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({ expectedRevision: 7 });
      return Response.json(
        { error: { code: "version_conflict", message: "immutable release exists" } },
        { status: 409 },
      );
    });
    vi.stubGlobal("fetch", fetcher);

    await expect(studioClient.publishDraft("draft-api", 7)).rejects.toMatchObject({
      status: 409,
      code: "version_conflict",
    } satisfies Partial<StudioApiError>);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/studio/drafts/draft-api/publish",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("lists and promotes a personal immutable Agent version through the BFF", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), method: init?.method ?? "GET" });
      return Response.json({
        agent_id: "agent-api",
        name: "productivity-agent",
        version: "0.1.0",
        display_name: "生产力智能体",
        manifest_hash: "a".repeat(64),
        package_hash: "b".repeat(64),
        created_at: "2026-08-11T00:00:00Z",
        current_version: "0.1.0",
      });
    });

    await studioClient.promotePersonalAgentVersion("agent-api", "0.1.0");

    expect(calls).toEqual([{
      url: "/api/harness/agents/agent-api/versions/0.1.0/promote",
      method: "POST",
    }]);
  });

  it("creates a Preview bound to the exact Draft revision and stable key", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({
        draftId: "draft-api",
        expectedRevision: 7,
        idempotencyKey: "preview:draft-api:r7:hash",
        ttlSeconds: 3600,
      });
      return Response.json({ previewId: "preview-one", status: "queued" });
    });
    vi.stubGlobal("fetch", fetcher);

    await studioClient.createPreview(
      "draft-api",
      7,
      "preview:draft-api:r7:hash",
    );

    expect(fetcher).toHaveBeenCalledWith(
      "/api/studio/previews",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("creates an immutable Dataset Version and a version-pinned Eval Run", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ url, body });
      if (url.endsWith("eval-datasets")) {
        return Response.json({
          datasetId: "dataset-one",
          version: 2,
          agentName: "policy-researcher",
        });
      }
      return Response.json({ run: { evalRunId: "eval-one", status: "queued" } });
    });
    vi.stubGlobal("fetch", fetcher);

    const dataset = await studioClient.createEvalDataset(
      "draft-api",
      7,
      "发布必测集",
      "dataset-one",
    );
    await studioClient.createEvalRun(
      dataset,
      "1.2.3",
      "eval:dataset-one:v2:1.2.3",
      "preview-one",
    );

    expect(calls).toEqual([
      {
        url: "/api/studio/eval-datasets",
        body: {
          draftId: "draft-api",
          expectedRevision: 7,
          name: "发布必测集",
          datasetId: "dataset-one",
          required: true,
        },
      },
      {
        url: "/api/studio/eval-runs",
        body: {
          datasetId: "dataset-one",
          datasetVersion: 2,
          agentName: "policy-researcher",
          agentVersion: "1.2.3",
          idempotencyKey: "eval:dataset-one:v2:1.2.3",
          previewId: "preview-one",
        },
      },
    ]);
  });

  it("promotes with environment CAS and rolls back to a verified snapshot", async () => {
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    vi.stubGlobal("crypto", { randomUUID: () => "stable-operation" });
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      return Response.json({ deployment: { status: "queued" } });
    });
    const environment = {
      tenantId: "tenant-a",
      agentName: "policy-researcher",
      name: "production" as const,
      revision: 4,
      policyRevision: 2,
      policyHash: "f".repeat(64),
      resourcePolicy: environmentPolicy(),
      routes: [],
      healthySnapshotId: "snapshot-current",
      updatedAt: "2026-07-16T00:00:00Z",
    };

    await studioClient.promoteDeployment(
      "policy-researcher",
      "1.2.3",
      environment,
      "a".repeat(64),
      "isolated-default",
      10,
    );
    await studioClient.rollbackDeployment(
      "policy-researcher",
      environment,
      "snapshot-previous",
    );

    expect(calls[0]).toMatchObject({
      url: "/api/studio/deployments/promote",
      body: {
        agentName: "policy-researcher",
        agentVersion: "1.2.3",
        environment: "production",
        expectedEnvironmentRevision: 4,
        canaryPercent: 10,
        imageDigest: `sha256:${"a".repeat(64)}`,
      },
    });
    expect(calls[1]).toMatchObject({
      url: "/api/studio/agents/policy-researcher/environments/production/rollback",
      body: {
        snapshotId: "snapshot-previous",
        expectedEnvironmentRevision: 4,
      },
    });
  });

  it("rebases an Environment policy on the current catalog revision", async () => {
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      if (!init?.method) {
        return Response.json({ revision: 7, catalog: {} });
      }
      return Response.json({ revision: 5, policyRevision: 3 });
    });
    const policy = environmentPolicy();
    const environment = {
      tenantId: "tenant-a",
      agentName: "policy-researcher",
      name: "production" as const,
      revision: 4,
      policyRevision: 2,
      policyHash: "f".repeat(64),
      resourcePolicy: policy,
      routes: [],
      healthySnapshotId: "snapshot-current",
      updatedAt: "2026-07-16T00:00:00Z",
    };

    await studioClient.replaceEnvironmentPolicy(
      "policy-researcher",
      environment,
      policy,
    );

    expect(calls).toEqual([
      { url: "/api/studio/catalog", method: "GET", body: null },
      {
        url: "/api/studio/agents/policy-researcher/environments/production/policy",
        method: "PUT",
        body: {
          expectedEnvironmentRevision: 4,
          policy: {
            ...policy,
            capabilityCatalogRevision: 7,
          },
        },
      },
    ]);
  });

  it("creates and rotates a revision-fenced external trigger", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return Response.json({
        trigger: {
          triggerId: "trigger-one",
          revision: calls.length,
          name: "Webhook",
          enabled: true,
        },
        secret: "one-time-secret",
      });
    });

    const created = await studioClient.createTrigger(
      "policy-researcher",
      "Webhook",
      "production",
    );
    await studioClient.rotateTriggerSecret({
      ...created.trigger,
      tenantId: "tenant-a",
      kind: "webhook",
      agentName: "policy-researcher",
      environment: "production",
      enabled: true,
      revision: 1,
      createdBy: "admin-a",
      createdAt: "2026-07-19T00:00:00Z",
      updatedAt: "2026-07-19T00:00:00Z",
      lastInvokedAt: null,
    });

    expect(calls).toEqual([
      {
        url: "/api/studio/agents/policy-researcher/triggers",
        body: { name: "Webhook", environment: "production" },
      },
      {
        url: "/api/studio/triggers/trigger-one/rotate-secret",
        body: { expectedRevision: 1 },
      },
    ]);
  });
});
