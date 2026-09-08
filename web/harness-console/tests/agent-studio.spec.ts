import { describe, expect, it } from "vitest";
import {
  BUILTIN_TOOLS,
  DEFAULT_STUDIO_DRAFT,
  MODEL_ROUTES,
  POLICY_OPTIONS,
  applyStudioDraftUpdate,
  createPersonalStudioDraft,
  evaluateStudioDraft,
  mcpOptionsForDraft,
  restoreStudioDraft,
  type McpOption,
} from "../src/lib/agent-studio";

describe("Agent Studio effective contract", () => {
  const specialists = [
    {
      alias: "researcher",
      ref: "helper-agent@1.0.0",
      responsibility: "核验事实并返回依据。",
      background: true,
    },
    {
      alias: "reviewer",
      ref: "helper-agent@1.0.0",
      responsibility: "复核结论和遗漏。",
      background: true,
    },
    {
      alias: "writer",
      ref: "helper-agent@1.0.0",
      responsibility: "整理最终交付。",
      background: true,
    },
  ];

  it("creates a unique personal draft without colliding with the runtime Lead", () => {
    expect(createPersonalStudioDraft()).toMatchObject({
      name: "productivity-agent",
      displayName: "生产力智能体",
      version: "0.1.0",
      domain: "productivity",
      id: "",
      revision: 0,
      publishedVersion: null,
    });
    expect(
      createPersonalStudioDraft([
        "productivity-agent",
        "productivity-agent-2",
      ]),
    ).toMatchObject({
      name: "productivity-agent-3",
      displayName: "生产力智能体 3",
    });
    expect(DEFAULT_STUDIO_DRAFT.name).toBe("lead-agent");
  });

  it("auto-increments the patch version on the first edit after publish", () => {
    const published = {
      ...DEFAULT_STUDIO_DRAFT,
      version: "1.4.7",
      publishedVersion: "1.4.7",
    };

    const edited = applyStudioDraftUpdate(published, { description: "新说明" });
    const editedAgain = applyStudioDraftUpdate(edited, { domain: "new-domain" });
    const manual = applyStudioDraftUpdate(editedAgain, { version: "2.0.0" });

    expect(edited.version).toBe("1.4.8");
    expect(editedAgain.version).toBe("1.4.8");
    expect(manual.version).toBe("2.0.0");
  });

  it("offers DeepSeek V4 models as distinct executable routes", () => {
    const flash = MODEL_ROUTES.find((item) => item.id === "deepseek-v4-flash");
    const pro = MODEL_ROUTES.find((item) => item.id === "deepseek-v4-pro");

    expect(flash?.models).toEqual(["deepseek-v4-flash"]);
    expect(pro?.models).toEqual(["deepseek-v4-pro"]);
    expect(MODEL_ROUTES.some((item) => item.id === "anthropic-official")).toBe(false);
  });

  it("uses a neutral general Lead instead of a business Agent template", () => {
    const skill = DEFAULT_STUDIO_DRAFT.skills[0];

    expect(DEFAULT_STUDIO_DRAFT.name).toBe("lead-agent");
    expect(DEFAULT_STUDIO_DRAFT.domain).toBe("general-assistant");
    expect(DEFAULT_STUDIO_DRAFT.version).toBe("1.0.0");
    expect(DEFAULT_STUDIO_DRAFT.model).toBe("deepseek-v4-pro");
    expect(DEFAULT_STUDIO_DRAFT.systemPrompt).toContain("通用任务入口");
    expect(DEFAULT_STUDIO_DRAFT.systemPrompt).not.toContain("专用舆情 MCP");
    expect(skill.name).toBe("general-task-orchestration");
    expect(skill.instructions).toContain("专用流程");
    expect(DEFAULT_STUDIO_DRAFT.mcpServers).toEqual([]);
    expect(DEFAULT_STUDIO_DRAFT.subagents).toEqual([]);
  });

  it("keeps the default Lead isolated and offline until users add capabilities", () => {
    const contract = evaluateStudioDraft(DEFAULT_STUDIO_DRAFT);

    expect(contract.ready).toBe(true);
    expect(contract.network).toBe("none");
    expect(contract.networkLabel).toBe("不联网");
    expect(contract.sandboxLabel).toBe("隔离执行 · 平台托管");
    expect(contract.risk).toBe("high");
    expect(contract.collaborationLabel).toBe("单 Agent");
    expect(contract.subagentCount).toBe(0);
  });

  it("treats Skills as an optional workflow enhancement", () => {
    const contract = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      skills: [],
    });

    expect(contract.ready).toBe(true);
    expect(contract.skillCount).toBe(0);
    expect(contract.issues).not.toContain("至少需要一个 Skill");
  });

  it("describes routine commands as automatic without a retired provider exception", () => {
    const bash = BUILTIN_TOOLS.find((item) => item.id === "Bash");
    const standard = POLICY_OPTIONS.find((item) => item.id === "production-standard");
    const gateway = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      modelRoute: "deepseek-v4-pro",
      model: "deepseek-v4-pro",
    });

    expect(bash?.approval).toBe("自动风险分级，必要时确认");
    expect(bash?.approval).not.toContain("默认人工审批");
    expect(standard?.description).toContain("策略允许的命令自动执行");
    expect(standard?.description).not.toContain("命令默认审批");
    expect(gateway.approvalLabel).toBe("安全 Bash 自动执行 · 高风险才确认");
  });

  it("does not offer business MCP or knowledge services to the personal Lead", () => {
    const options: McpOption[] = [
      {
        id: "tavily-readonly",
        category: "tool",
        label: "公网搜索",
        description: "通用公网检索",
        tools: ["search"],
        network: "external",
        sendsUserData: true,
      },
      {
        id: "sentiment-query",
        category: "tool",
        label: "涉非舆情研判查询",
        description: "业务查询",
        tools: ["query"],
        network: "external",
        sendsUserData: true,
      },
      {
        id: "weknora-judicial",
        category: "knowledge",
        label: "司法案例外部知识库",
        description: "业务知识库",
        tools: ["hybrid_search"],
        network: "external",
        sendsUserData: true,
      },
    ];
    const visible = mcpOptionsForDraft(DEFAULT_STUDIO_DRAFT, [...options]);

    expect(visible.map((item) => item.id)).toEqual(["tavily-readonly"]);
    expect(mcpOptionsForDraft(
      { ...DEFAULT_STUDIO_DRAFT, name: "public-opinion-agent", domain: "public-opinion" },
      [...options],
    )).toEqual(options);
  });

  it("fails closed when prompt, subagent, policy and eval coverage disagree", () => {
    const contract = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      systemPrompt: "Only a short prompt",
      builtinTools: [...DEFAULT_STUDIO_DRAFT.builtinTools, "Task"],
      subagents: [],
      policy: "production-read-only",
      evalCases: DEFAULT_STUDIO_DRAFT.evalCases.filter(
        (testCase) => testCase.tag === "happy",
      ),
    });

    expect(contract.ready).toBe(false);
    expect(contract.issues).toContain("System Prompt 缺少必需章节");
    expect(contract.issues).toContain("Task 工具需要固定版本子 Agent");
    expect(contract.issues).toContain("只读权限不能包含写入或命令工具");
    expect(contract.issues).toContain("评测集缺少 ambiguous 场景");
    expect(contract.issues).toContain("评测集缺少 safety 场景");
  });

  it("does not treat no MCP as permission for arbitrary network", () => {
    const contract = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      mcpServers: [],
    });

    expect(contract.network).toBe("none");
    expect(contract.networkLabel).toBe("不联网");
  });

  it("fails closed when on-demand discovery is selected on an unreviewed route", () => {
    const unsupported = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      toolExposureMode: "on_demand",
    });
    const supported = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      modelRoute: "on-demand-test",
      toolExposureMode: "on_demand",
      requiredCapabilities: [
        ...DEFAULT_STUDIO_DRAFT.requiredCapabilities,
        "tool_search",
      ],
    }, {
      routes: [{
        id: "on-demand-test",
        label: "On-demand test route",
        provider: "test",
        models: [DEFAULT_STUDIO_DRAFT.model],
        capabilities: ["streaming", "tool_use", "tool_search"],
      }],
    });

    expect(unsupported.ready).toBe(false);
    expect(unsupported.issues).toContain("当前模型路由不支持按需工具加载");
    expect(supported.issues).not.toContain("当前模型路由不支持按需工具加载");
  });

  it("fails closed for duplicate roles and floating subagent references", () => {
    const contract = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      subagents: [
        specialists[0],
        {
          ...specialists[1],
          alias: specialists[0].alias,
          ref: "helper-agent",
        },
      ],
    });

    expect(contract.ready).toBe(false);
    expect(contract.issues).toContain("Sub Agent 角色别名不能重复");
    expect(contract.issues).toContain(
      "Sub Agent 必须固定 name@version：helper-agent",
    );
  });

  it("does not bind business or helper Sub Agents in the default template", () => {
    expect(DEFAULT_STUDIO_DRAFT.subagents).toEqual([]);
    expect(DEFAULT_STUDIO_DRAFT.builtinTools).not.toContain("Task");
  });

  it("fails closed when the Studio collaboration graph exceeds runtime limits", () => {
    const contract = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      subagents: specialists,
      maxSubagents: 2,
      maxConcurrentSubagents: 3,
    });

    expect(contract.ready).toBe(false);
    expect(contract.issues).toContain("当前角色数超过运行上限 2");
    expect(contract.issues).toContain("并发 Sub 上限不能高于可绑定 Sub 上限");
  });

  it("fails closed when an eval trajectory contradicts the runtime contract", () => {
    const contract = evaluateStudioDraft({
      ...DEFAULT_STUDIO_DRAFT,
      evalCases: [
        {
          ...DEFAULT_STUDIO_DRAFT.evalCases[0],
          expect: {
            ...DEFAULT_STUDIO_DRAFT.evalCases[0].expect,
            requiredTools: ["Bash", "UnknownTool"],
            forbiddenTools: ["Bash"],
          },
        },
        ...DEFAULT_STUDIO_DRAFT.evalCases.slice(1),
      ],
    });

    expect(contract.ready).toBe(false);
    expect(contract.issues).toContain(
      "评测 general-readonly-task 的必需与禁止工具冲突：Bash",
    );
    expect(contract.issues).toContain(
      "评测 general-readonly-task 要求未启用工具：UnknownTool",
    );
  });

  it("migrates browser drafts saved before role and trajectory contracts existed", () => {
    const legacy = {
      ...DEFAULT_STUDIO_DRAFT,
      restoreSession: undefined,
      archiveOnComplete: undefined,
      toolExposureMode: undefined,
      builtinTools: [...DEFAULT_STUDIO_DRAFT.builtinTools, "Task"],
      policy: "production-orchestrator",
      subagents: ["helper-agent@1.0.0"],
      evalCases: DEFAULT_STUDIO_DRAFT.evalCases.map(({ expect: _expect, ...testCase }) =>
        testCase,
      ),
    };

    const restored = restoreStudioDraft(legacy);

    expect(restored).not.toBeNull();
    expect(restored?.restoreSession).toBe(true);
    expect(restored?.archiveOnComplete).toBe(true);
    expect(restored?.toolExposureMode).toBe("eager");
    expect(restored?.subagents[0]).toMatchObject({
      ref: "helper-agent@1.0.0",
      alias: "specialist-1",
    });
    expect(restored?.evalCases[0].expect.requiredTools).toEqual([]);
    expect(evaluateStudioDraft(restored as typeof DEFAULT_STUDIO_DRAFT).ready).toBe(true);
  });
});
