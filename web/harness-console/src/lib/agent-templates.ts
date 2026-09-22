import type { StudioDraft } from "./agent-studio";
import { apiDraftToStudioDraft, studioClient } from "./studio-client";

export type AgentTemplate = {
  id: string;
  name: string;
  category: string;
  description: string;
  input: string;
  output: string;
  steps: string[];
  example: string;
};
// Original task playbooks; Agenta's template gallery and setup flow are the UX reference.
export const AGENT_TEMPLATES: AgentTemplate[] = [
  {
    id: "pr-reviewer",
    name: "PR 审查助手",
    category: "工程",
    description: "阅读变更，定位缺陷和遗漏的测试，生成可核验的审查意见。",
    input: "Diff、相关代码与验收要求",
    output: "按严重程度排序的问题、文件位置、原因与修复建议",
    steps: [
      "确认变更目标，读取差异与关联上下文。",
      "分析边界条件、权限和回归风险；检查测试覆盖。",
      "仅报告有证据的问题，引用文件与位置。",
    ],
    example: "请审查我附加的代码变更，重点检查边界条件和测试遗漏。",
  },
  {
    id: "changelog-writer",
    name: "发布说明助手",
    category: "工程",
    description: "把提交记录与合并请求整理成面向用户的发布说明。",
    input: "提交记录、PR 列表与版本号",
    output: "功能、修复、迁移事项与来源链接",
    steps: [
      "确认发布范围及版本，读取提供的提交或 PR。",
      "按功能、修复与兼容性变化归类。",
      "说明用户可感知的变化，并保留来源。",
    ],
    example: "根据以下变更记录生成本次版本的发布说明。",
  },
  {
    id: "support-assistant",
    name: "客户支持助手",
    category: "支持",
    description: "从提供的产品资料找依据，给出清晰的排查与回复建议。",
    input: "客户问题、产品文档与已尝试的步骤",
    output: "问题判断、可执行步骤、依据与需升级的事项",
    steps: [
      "确认用户环境、问题表现和已尝试的步骤。",
      "仅使用当前资料及已连接工具核对产品能力。",
      "给出逐步操作，标明未知项与升级条件。",
    ],
    example: "客户反馈无法登录，请根据附加的帮助文档给出排查步骤。",
  },
  {
    id: "meeting-notes",
    name: "会议纪要助手",
    category: "运营",
    description: "将会议记录整理为结论、分歧与有责任人的行动项。",
    input: "会议逐字稿或记录",
    output: "摘要、已决事项、行动项表与待确认问题",
    steps: [
      "读取完整会议材料，识别议题与已确认结论。",
      "抽取负责人和截止时间，未提及时标记待确认。",
      "区分决议、建议与分歧，不自行补充承诺。",
    ],
    example: "请整理这份会议记录，列出结论、负责人和下一步。",
  },
  {
    id: "research-analyst",
    name: "资料研究助手",
    category: "知识",
    description: "围绕一个问题比较资料，给出带出处的分析和证据缺口。",
    input: "研究问题、资料与时间范围",
    output: "结论、对比、来源与未解决的问题",
    steps: [
      "确认研究问题和范围，检查材料时效性。",
      "交叉比对来源，区分事实、推断与相互冲突的信息。",
      "输出可追溯结论，明确资料不足之处。",
    ],
    example: "对比附加资料中的三个方案，说明适用条件和证据。",
  },
  {
    id: "data-analyst",
    name: "数据分析助手",
    category: "知识",
    description: "检查表格数据，解释趋势与异常并给出可复核的方法。",
    input: "数据文件、指标定义与分析目标",
    output: "数据质量说明、关键发现、计算口径与后续建议",
    steps: [
      "检查字段、样本范围、缺失值与口径。",
      "按明确方法计算并验证指标，避免把相关性解释为因果。",
      "展示发现、计算依据与局限。",
    ],
    example: "分析附加表格中的趋势和异常，先说明字段与计算口径。",
  },
  {
    id: "sales-brief",
    name: "客户研究助手",
    category: "销售",
    description: "整理客户背景和沟通材料，为下一次沟通准备简报。",
    input: "客户提供的资料、沟通记录与会议目标",
    output: "客户概况、已知需求、待验证假设与沟通提纲",
    steps: [
      "读取客户资料，注明信息时间和来源。",
      "区分已确认需求与假设，整理相关产品材料。",
      "给出具体的沟通问题与下一步建议。",
    ],
    example: "根据这份客户资料，准备下一次需求沟通的简报。",
  },
  {
    id: "weekly-report",
    name: "工作周报助手",
    category: "运营",
    description: "汇总进展、风险和计划，减少重复的状态整理。",
    input: "本周任务记录、进展与阻塞事项",
    output: "成果摘要、风险、待协作事项与下周计划",
    steps: [
      "确认报告周期，归并重复的工作记录。",
      "区分已完成、进行中和阻塞；引用原始任务。",
      "输出面向读者的成果及明确的待协作事项。",
    ],
    example: "把这些工作记录整理成本周周报，突出成果和阻塞。",
  },
];
export function applyAgentTemplate(
  draft: StudioDraft,
  template: AgentTemplate,
): StudioDraft {
  return {
    ...draft,
    displayName: template.name,
    description: template.description,
    taskContract: {
      ...draft.taskContract,
      audience: "使用此工作流的用户",
      examples: [template.example],
      goal: template.description,
      inputs: [template.input],
      outputs: [template.output],
      constraints: ["仅使用提供的资料及已授权的工具；缺失信息需说明。"],
    },
    systemPrompt: `# ${template.name}\n\n## Mission\n${template.description}\n\n## Operating workflow\n${template.steps.map((step, index) => `${index + 1}. ${step}`).join("\n")}\n\n## Evidence and tool use\n输入：${template.input}。先读取实际材料，引用来源。只调用当前可用的工具，不假装已连接外部服务。\n\n## Safety boundaries\n不编造信息或执行未授权的外部变更。缺失关键材料时询问用户，涉及敏感信息时仅使用完成任务所需的内容。\n\n## Output contract\n${template.output}。说明事实依据、未知项和验收方法。`,
    evalCases: draft.evalCases.map((item, index) =>
      index === 0 ? { ...item, prompt: template.example } : item,
    ),
  };
}
export async function createAgentFromTemplate(
  template: AgentTemplate,
  name: string,
  client = studioClient,
) {
  const created = apiDraftToStudioDraft(
    await client.createDraft({
      name,
      displayName: template.name,
      domain: template.id,
      description: template.description,
      template: "analyst",
    }),
  );
  const configured = applyAgentTemplate(created, template);
  try {
    return {
      draft: apiDraftToStudioDraft(await client.replaceDraft(configured)),
      saved: true,
      error: "",
    };
  } catch (reason) {
    return {
      draft: configured,
      saved: false,
      error: reason instanceof Error ? reason.message : "模板配置保存失败",
    };
  }
}
