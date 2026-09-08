import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agentCoordinate,
  currentSystemAssistant,
  agentIdentity,
  agentItemKey,
  chatUsableAgents,
  findTaskAgent,
  loadTaskAgentCatalog,
  type TaskAgent,
} from "../src/lib/task-agent-catalog";

describe("task agent catalog", () => {
  it("hides internal collaborators and team releases, and reveals only internal personal agents when enabled", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
      url.endsWith("runtime-config") ? { name: "lead", version: "1.0.0" }
        : url.endsWith("/drafts") ? [{
          draftId: "child", agentId: "child-agent", parentDraftId: "parent",
          name: "helper", displayName: "内部核验", publishedVersion: "1.0.0", version: "1.0.1",
        }]
          : [
            { agent_id: "child-agent", name: "helper", version: "1.0.0", display_name: "内部核验",
              owner_user_id: "me", scope: "personal", domain: "general" },
            { agent_id: "another-agent", name: "helper", version: "1.0.0", display_name: "共享核验",
              owner_user_id: "another", scope: "team", space_id: "team", domain: "general" },
          ],
    ), { status: 200 })));
    const result = await loadTaskAgentCatalog("me");
    expect(result.agents.filter((agent) => agent.name === "helper")).toEqual([]);
    const revealed = await loadTaskAgentCatalog("me", true);
    expect(revealed.agents.filter((agent) => agent.name === "helper")).toEqual([
      expect.objectContaining({ agentId: "child-agent", internal: true }),
    ]);
    expect(revealed.hiddenAgents).toEqual([expect.objectContaining({agentId: "another-agent"})]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not resolve another user's personal Agent from a stale binding", () => {
    const agents = [
      {
        name: "public-opinion-agent",
        version: "0.3.11",
        displayName: "舆情分析",
        domain: "public-opinion",
        ownerUserId: "new-user",
        scope: "personal" as const,
      },
    ];

    expect(
      findTaskAgent(agents, {
        name: "public-opinion-agent",
        version: "0.3.11",
        ownerUserId: "previous-user",
      }),
    ).toBeUndefined();
  });

  it("never deduplicates or keys by name@version alone", () => {
    const personal = {
      name: "public-opinion-agent",
      version: "0.3.11",
      displayName: "舆情分析",
      domain: "public-opinion",
      ownerUserId: "user-a",
      scope: "personal" as const,
    };
    const team = {
      ...personal,
      ownerUserId: "user-b",
      scope: "team" as const,
      spaceId: "space-1",
      spaceName: "法务空间",
    };
    expect(agentItemKey(personal)).toBe("personal:-:user-a:public-opinion-agent@0.3.11");
    expect(agentItemKey(team)).toBe("team:space-1:user-b:public-opinion-agent@0.3.11");
    expect(agentItemKey(personal)).not.toBe(agentItemKey(team));
    // Same Agent, different versions -> same identity, different item keys.
    expect(agentIdentity(personal)).toBe(agentIdentity({ ...personal, version: "0.4.0" }));
    expect(agentItemKey(personal)).not.toBe(agentItemKey({ ...personal, version: "0.4.0" }));
    // A stable agentId wins over coordinates once the workspace model lands.
    const withId = { ...personal, agentId: "agent_abc" } as typeof personal & { agentId: string };
    expect(agentIdentity(withId)).toBe("agent_abc");
    expect(agentItemKey(withId)).toBe("agent_abc@0.3.11");
  });

  it("matches both stable Agent identity and the requested immutable version", () => {
    const agents: TaskAgent[] = [
      {
        agentId: "agent-1",
        name: "productivity-agent",
        version: "0.3.12",
        displayName: "生产力智能体",
        domain: "productivity",
      },
      {
        agentId: "agent-1",
        name: "productivity-agent",
        version: "0.3.13",
        displayName: "生产力智能体",
        domain: "productivity",
      },
    ];

    expect(findTaskAgent(agents, {
      agentId: "agent-1",
      name: "productivity-agent",
      version: "0.3.13",
    })?.version).toBe("0.3.13");
  });

  it("filters revoked and deleted historical Agents from the task selector", () => {
    const agents = [
      {
        name: "lead-agent",
        version: "1.0.0",
        displayName: "通用 Lead",
        domain: "general-assistant",
        canChat: true,
      },
      {
        name: "shared-agent",
        version: "1.2.0",
        displayName: "共享 Agent",
        domain: "shared",
        ownerUserId: "user-a",
        scope: "team" as const,
        spaceId: "space-1",
        canChat: false,
      },
      {
        name: "historical-agent",
        version: "0.9.0",
        displayName: "历史 Agent",
        domain: "historical",
        ownerUserId: "user-c",
        scope: "team" as const,
        spaceId: "space-2",
      },
    ];
    const usable = chatUsableAgents(agents);
    expect(usable.map((agent) => agent.name)).toEqual(["lead-agent"]);
  });

  it("keeps the neutral Lead default while offering business Agents explicitly", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("runtime-config")) {
          return Response.json({ name: "lead-agent", version: "1.0.0" });
        }
        if (url.includes("/api/harness/agents")) {
          return Response.json([
            {
              name: "lead-agent",
              version: "1.0.0",
              display_name: "通用 Lead Agent",
              domain: "general-assistant",
            },
            {
              name: "public-opinion-agent",
              version: "0.2.0",
              display_name: "public-opinion-agent",
              domain: "public-opinion",
            },
          ]);
        }
        return Response.json([
          {
            draftId: "draft-1",
            name: "public-opinion-agent",
            displayName: "舆情研判 Agent",
            domain: "public-opinion",
            version: "0.2.0",
            template: "orchestrator",
            revision: 4,
            updatedAt: "2026-07-17T00:00:00Z",
            publishedVersion: "0.2.0",
          },
          {
            draftId: "draft-2",
            name: "draft-only",
            displayName: "未发布 Agent",
            domain: "draft",
            version: "0.1.0",
            template: "analyst",
            revision: 1,
            updatedAt: "2026-07-17T00:00:00Z",
            publishedVersion: null,
          },
        ]);
      }),
    );

    const catalog = await loadTaskAgentCatalog();

    expect(catalog.defaultAgent.displayName).toBe("通用 Lead Agent");
    expect(catalog.agents).toHaveLength(2);
    expect(agentCoordinate(catalog.agents[0])).toBe(
      "lead-agent@1.0.0",
    );
    expect(agentCoordinate(catalog.agents[1])).toBe("public-opinion-agent@0.2.0");
  });

  it("loads runtime, registry, and Studio versions concurrently", async () => {
    const pending = new Map<string, (response: Response) => void>();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      return new Promise<Response>((resolve) => pending.set(url, resolve));
    });
    vi.stubGlobal("fetch", fetchMock);

    const catalogPromise = loadTaskAgentCatalog();
    await Promise.resolve();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    pending.get("/api/harness/runtime-config")?.(
      Response.json({ name: "lead-agent", version: "1.0.0" }),
    );
    pending.get("/api/harness/agents")?.(Response.json([]));
    pending.get("/api/studio/drafts")?.(Response.json([]));
    await expect(catalogPromise).resolves.toMatchObject({
      defaultAgent: { name: "lead-agent", version: "1.0.0" },
    });
  });

  it("falls back to the configured runtime when Studio is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).includes("runtime-config")
          ? Response.json({ name: "echo-agent", version: "0.4.0" })
          : new Response("offline", { status: 503 }),
      ),
    );

    const catalog = await loadTaskAgentCatalog();

    expect(catalog.agents).toEqual([
      {
        name: "echo-agent",
        version: "0.4.0",
        displayName: "echo-agent",
        domain: "default",
      },
    ]);
  });

  it("offers published runtime bundles even when they have no Studio draft", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("runtime-config")) {
          return Response.json({ name: "echo-agent", version: "0.4.0" });
        }
        if (url.includes("/api/harness/agents")) {
          return Response.json([
            {
              name: "echo-agent",
              version: "0.4.0",
              display_name: "echo-agent",
              domain: "harness-validation",
            },
            {
              name: "public-opinion-agent",
              version: "0.1.1",
              display_name: "public-opinion-agent",
              domain: "public-opinion",
            },
          ]);
        }
        return Response.json([]);
      }),
    );

    const catalog = await loadTaskAgentCatalog();

    expect(catalog.agents.map(agentCoordinate)).toEqual([
      "echo-agent@0.4.0",
      "public-opinion-agent@0.1.1",
    ]);
    expect(catalog.agents[1].displayName).toBe("舆情分析");
  });

  it("suppresses only the current user's duplicate Studio fallback release", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("runtime-config")) {
          return Response.json({ name: "lead-agent", version: "1.0.0" });
        }
        if (url.includes("/api/harness/agents")) {
          return Response.json([
            {
              agent_id: "agent-personal",
              name: "productivity-agent",
              version: "0.1.0",
              display_name: "生产力智能体",
              domain: "productivity",
              owner_user_id: "user-1",
              scope: "personal",
            },
            {
              agent_id: "agent-team",
              name: "productivity-agent",
              version: "0.1.0",
              display_name: "团队生产力智能体",
              domain: "productivity",
              owner_user_id: "user-2",
              scope: "team",
              space_id: "space-1",
              space_name: "产品团队",
            },
          ]);
        }
        return Response.json([
          {
            draftId: "draft-1",
            name: "productivity-agent",
            displayName: "生产力智能体",
            domain: "productivity",
            version: "0.1.0",
            template: "orchestrator",
            revision: 3,
            updatedAt: "2026-08-10T00:00:00Z",
            publishedVersion: "0.1.0",
          },
        ]);
      }),
    );

    const catalog = await loadTaskAgentCatalog("user-1");
    const matching = catalog.agents.filter(
      (agent) =>
        agent.name === "productivity-agent" && agent.version === "0.1.0",
    );

    expect(matching).toHaveLength(1);
    expect(matching.map(agentItemKey)).toEqual([
      "agent-personal@0.1.0",
    ]);
    expect(matching.some((agent) => !agent.ownerUserId)).toBe(false);
  });
});

 it("new system tasks use the platform version without changing team agents", () => {
  const current: TaskAgent = {name:"lead-agent",version:"1.0.2",displayName:"系统助手",domain:"general",scope:"personal",ownerUserId:"me"};
  expect(currentSystemAssistant({...current,version:"1.0.0"},current)).toBe(current);
  const shared: TaskAgent = {...current,version:"1.0.0",scope:"team",spaceId:"space"};
  expect(currentSystemAssistant(shared,current)).toBe(shared);
  const other: TaskAgent = {...current,ownerUserId:"someone"};
  expect(currentSystemAssistant(other,current)).toBe(other);
 });
