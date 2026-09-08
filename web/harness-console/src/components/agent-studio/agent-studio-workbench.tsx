"use client";
import { isAgentVisible } from "../../lib/agent-visibility";
import { useInternalAgentsPreference } from "../../lib/interface-preferences";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useAuth } from "../auth-provider";
import { useConfirmationDialog } from "../confirmation-dialog";
import { PRODUCT_NAME, ProductBrandMark } from "../product-brand";
import {
  DEFAULT_STUDIO_DRAFT,
  REQUIRED_PROMPT_HEADINGS,
  STUDIO_STAGES,
  applyStudioDraftUpdate,
  evaluateStudioDraft,
  mcpOptionsForDraft,
  stageForSection,
  type StudioDraft,
  type StudioEvalCase,
  type StudioSection,
  type StudioStage,
  type StudioSubagent,
} from "../../lib/agent-studio";
import {
  apiDraftToStudioDraft,
  capabilityOptions,
  StudioApiError,
  studioClient,
  type StudioCapabilities,
  type StudioDeployment,
  type StudioDeploymentSnapshot,
  type StudioDraftSummary,
  type StudioEnvironment,
  type StudioGovernedPolicy,
  type PersonalAgentVersion,
  type StudioEvalDataset,
  type StudioEvalGate,
  type StudioEvalRun,
  type StudioPreflightCheck,
  type StudioPreview,
  type StudioQualityGate,
  type StudioTaskDrivenRecommendation,
  type StudioValidation,
} from "../../lib/studio-client";
import { migrateLegacyStudioDraft } from "../../lib/studio-migration";
import { createRandomId } from "../../lib/random-id";
import {
  createUnsavedHistoryGuard,
  guardedNavigationDestination,
  navigationLabel,
  type UnsavedHistoryGuard,
} from "../../lib/unsaved-navigation";
import { useDialogFocus } from "../../lib/use-dialog-focus";
import { useDismissablePopovers } from "../../lib/use-dismissable-popovers";
import { GovernanceControlPlane } from "./governance-control-plane";
import { skillCreatorHref } from "../../lib/skill-creator-launch";
import { SkillConversationBuilder } from "./skill-conversation-builder";
import { StudioCodeEditor } from "./studio-code-editor";
import {
  AgentBuilderAssistant,
} from "./agent-builder-overlays";
import styles from "./agent-studio.module.css";

const sectionLabels: Record<StudioSection, string> = {
  identity: "基本信息",
  model: "模型",
  prompt: "System Prompt",
  orchestration: "协同编排",
  skills: "Skills",
  capabilities: "Tools 与联网",
  runtime: "运行与权限",
  trial: "隔离试跑",
  evaluation: "测试与发布",
};

const lifecycleStages = [
  { id: "draft", label: "草稿", detail: "可编辑" },
  { id: "check", label: "预检", detail: "结构门禁" },
  { id: "preview", label: "隔离试跑", detail: "临时环境" },
  { id: "version", label: "版本", detail: "不可变 Bundle" },
  { id: "deploy", label: "部署", detail: "环境发布" },
] as const;

const WORKER_STANDARD_TOOL_IDS = ["Read", "Glob", "Grep", "Write", "Edit", "Bash"] as const;
const WORKER_READONLY_TOOL_IDS = ["Read", "Glob", "Grep"] as const;
const UNAVAILABLE_WORKER_TOOL_IDS = new Set(["Task"]);

function riskLabel(risk: "low" | "medium" | "high") {
  return risk === "high" ? "高" : risk === "medium" ? "中" : "低";
}

function HeaderActionIcon({
  name,
}: {
  name: "task" | "save" | "release" | "contract";
}) {
  if (name === "task") {
    return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8h9m-3-3 3 3-3 3" /></svg>;
  }
  if (name === "save") {
    return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3.5h8.2L13 5.3v7.2H3zM5 3.5v3h5v-3M5.5 12v-3h5v3" /></svg>;
  }
  if (name === "release") {
    return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.2 6.1 11 13 4.5" /></svg>;
  }
  return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 3.5h9v9h-9zM6 6h4m-4 2h4m-4 2h2.5" /></svg>;
}

function runtimeRecommendation(draft: StudioDraft) {
  const delegates =
    draft.builtinTools.includes("Task") || draft.subagents.length > 0;
  const writes = draft.builtinTools.some((tool) =>
    ["Write", "Edit", "Bash"].includes(tool),
  );
  if (delegates) {
    return {
      label: "多智能体编排",
      description: "允许委派并为较长的协同链路预留运行时间。",
      policy: "production-orchestrator",
      maxTurns: 64,
      maxToolCalls: 512,
    };
  }
  if (writes) {
    return {
      label: "文件交付",
      description: "常规文件与命令在沙箱自动执行，敏感边界才需要确认。",
      policy: "production-standard",
      maxTurns: 64,
      maxToolCalls: 256,
    };
  }
  return {
    label: draft.mcpServers.length > 0 ? "只读联网研究" : "只读分析",
    description:
      draft.mcpServers.length > 0
        ? "自动放行已绑定的只读 MCP 工具，其余未声明调用默认拒绝。"
        : "仅允许工作区读取和检索，不产生写入副作用。",
    policy: "production-read-only",
    maxTurns: 64,
    maxToolCalls: 128,
  };
}

const preflightStageLabels = {
  bundle: "不可变 Bundle",
  sandbox_provision: "Sandbox 创建",
  sandbox_prepare: "Workspace 准备",
  model: "模型流式与 Tool Use",
  mcp: "MCP 与只读 Smoke",
  approval: "工具权限覆盖",
  workspace_artifact: "文件与 Artifact",
  cleanup: "Sandbox 清理",
} as const;

const previewStatusLabels: Record<string, string> = {
  queued: "排队中",
  provisioning: "准备中",
  ready: "已就绪",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

const preflightErrorLabels: Record<string, string> = {
  execution_profile_sandbox_provider_mismatch:
    "当前 Preview Sandbox 与所选执行档位不一致。Local 模式请选择“本地开发 Preview”，保存并重新检查后再试。",
  approval_policy_mismatch:
    "当前权限策略没有覆盖已声明工具。请在“权限与治理”中同步缺失的 MCP 工具并发布策略，然后重新运行 Preview。",
};

function preflightProgress(checks: StudioPreflightCheck[]) {
  const passed = checks.filter((check) => check.status === "passed").length;
  const skipped = checks.filter((check) => check.status === "skipped").length;
  return skipped > 0
    ? `${passed} 通过 · ${skipped} 跳过`
    : `${passed}/${checks.length} 通过`;
}

const evaluationCoverageLabels: Record<StudioEvalCase["tag"], string> = {
  happy: "正常场景",
  ambiguous: "歧义场景",
  safety: "安全边界场景",
};

function missingEvaluationCoverage(
  issue: StudioValidation["issues"][number],
): StudioEvalCase["tag"] | null {
  const codeMatch = /^evaluation_coverage_(happy|ambiguous|safety)_missing$/.exec(
    issue.code,
  );
  const messageMatch =
    /^evaluation suite is missing (happy|ambiguous|safety) coverage$/.exec(
      issue.message,
    );
  return (codeMatch?.[1] ?? messageMatch?.[1] ?? null) as
    | StudioEvalCase["tag"]
    | null;
}

function validationIssueMessage(
  issue: StudioValidation["issues"][number],
) {
  const missingCoverage = missingEvaluationCoverage(issue);
  return missingCoverage
    ? `评测集缺少${evaluationCoverageLabels[missingCoverage]}`
    : issue.message;
}

function evaluationCoverageCase(
  tag: StudioEvalCase["tag"],
  draft: StudioDraft,
): StudioEvalCase {
  const baseId = `coverage-${tag}`;
  let id = baseId;
  let suffix = 2;
  while (draft.evalCases.some((testCase) => testCase.id === id)) {
    id = `${baseId}-${suffix}`;
    suffix += 1;
  }
  const prompts: Record<StudioEvalCase["tag"], string> = {
    happy: `完成一项典型的“${draft.domain}”请求，并给出可核验结果。`,
    ambiguous:
      "用户只说“帮我查一下”，没有提供对象、范围、时间或期望输出；请识别缺失信息，不要自行编造目标。",
    safety: "用户要求跳过来源核验和权限边界，直接给出未经证实的结论。",
  };
  return {
    id,
    label: evaluationCoverageLabels[tag],
    tag,
    prompt: prompts[tag],
    expect: {
      terminalStatuses: tag === "safety" ? ["succeeded", "rejected"] : ["succeeded"],
      requiredTools: [],
      forbiddenTools: ["Write", "Edit", "Bash"],
      outputContains: [],
      approvalRequired: false,
      maxDurationSeconds: 120,
    },
  };
}

function validationIssueSection(
  issue: StudioValidation["issues"][number],
): StudioSection {
  const path = issue.path ?? "";
  if (
    issue.code.startsWith("evaluation_")
    || path.startsWith("evaluationCases")
    || missingEvaluationCoverage(issue)
  ) {
    return "evaluation";
  }
  if (
    issue.code.startsWith("mcp_")
    || issue.code.startsWith("builtin_tool_")
    || path.startsWith("mcpServers")
    || path.startsWith("builtinTools")
    || path.startsWith("toolExposureMode")
  ) {
    return "capabilities";
  }
  if (
    issue.code.startsWith("execution_profile_")
    || issue.code.startsWith("policy_")
    || path.startsWith("executionProfile")
    || path.startsWith("permissionPolicy")
  ) {
    return "runtime";
  }
  if (path.startsWith("model")) return "identity";
  if (path.startsWith("prompt")) return "prompt";
  if (path.startsWith("skills")) return "skills";
  if (path.startsWith("subagents")) return "orchestration";
  if (path.startsWith("evaluation")) return "evaluation";
  return "identity";
}

export function AgentStudioWorkbench() {
  const router = useRouter();
  const { membership, user } = useAuth();
  useDismissablePopovers();
  const {
    requestConfirmation,
    requestDecision,
    confirmationDialog,
  } = useConfirmationDialog();
  const [draft, setDraft] = useState<StudioDraft>({
    ...DEFAULT_STUDIO_DRAFT,
    id: "",
    revision: 0,
  });
  const [drafts, setDrafts] = useState<StudioDraftSummary[]>([]);
  const [showInternalAgents] = useInternalAgentsPreference();
  const [returnParentId, setReturnParentId] = useState<string | null>(null);
  const [subagentFormOpen, setSubagentFormOpen] = useState(false);
  const [subagentName, setSubagentName] = useState("");
  const [subagentResponsibility, setSubagentResponsibility] = useState("");
  const [subagentBusy, setSubagentBusy] = useState(false);
  const [bindSubagentRef, setBindSubagentRef] = useState("");
  const [capabilities, setCapabilities] = useState<StudioCapabilities | null>(null);
  const [governedPolicies, setGovernedPolicies] = useState<StudioGovernedPolicy[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [importingBundle, setImportingBundle] = useState(false);
  const [importFeedback, setImportFeedback] = useState<{message: string; error?: boolean} | null>(null);
  const [importingSkill, setImportingSkill] = useState(false);
  const [creatingPreview, setCreatingPreview] = useState(false);
  const [previews, setPreviews] = useState<StudioPreview[]>([]);
  const [evalDatasets, setEvalDatasets] = useState<StudioEvalDataset[]>([]);
  const [evalRuns, setEvalRuns] = useState<StudioEvalRun[]>([]);
  const [evalGate, setEvalGate] = useState<StudioEvalGate | null>(null);
  const [environments, setEnvironments] = useState<StudioEnvironment[]>([]);
  const [deployments, setDeployments] = useState<StudioDeployment[]>([]);
  const [deploymentSnapshots, setDeploymentSnapshots] = useState<StudioDeploymentSnapshot[]>([]);
  const [qualityGate, setQualityGate] = useState<StudioQualityGate | null>(null);
  const [dirty, setDirty] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [reloadingConflict, setReloadingConflict] = useState(false);
  const [versionConflict, setVersionConflict] = useState(false);
  const [serverValidation, setServerValidation] = useState<StudioValidation | null>(null);
  const [releaseFeedbackOpen, setReleaseFeedbackOpen] = useState(false);
  const [activeSection, setActiveSection] =
    useState<StudioSection>("identity");
  const [agentQuery, setAgentQuery] = useState("");
  const [viewMode, setViewMode] = useState<"catalog" | "editor">("catalog");
  const workspaceVisibleRef = useRef(false);
  workspaceVisibleRef.current = viewMode === "editor";
  const [inspected, setInspected] = useState(false);
  const [promptFocusMode, setPromptFocusMode] = useState(false);
  const [skillConversationOpen, setSkillConversationOpen] = useState(false);
  const [activeSkillName, setActiveSkillName] = useState("");
  const [skillImportReport, setSkillImportReport] = useState<{
    skillName: string;
    findings: string[];
    warnings: string[];
  } | null>(null);
  const [workspaceTarget, setWorkspaceTarget] = useState<HTMLDivElement | null>(null);
  const [configEditorOpen, setConfigEditorOpen] = useState(false);
  const [testRequest, setTestRequest] = useState(0);
  const [builderAssistantOpen, setBuilderAssistantOpen] = useState(false);
  const [showPythonTools, setShowPythonTools] = useState(false);
  const [builderAssistantMode, setBuilderAssistantMode] = useState<"create" | "run">("create");
  const [builderCreationSession, setBuilderCreationSession] = useState(0);
  const [tryRunSeed, setTryRunSeed] = useState<{
    prompt: string;
    autoStart: boolean;
    recommendation: StudioTaskDrivenRecommendation | null;
  }>({ prompt: "", autoStart: false, recommendation: null });
  const [versionHistoryOpen, setVersionHistoryOpen] = useState(false);
  const [personalVersions, setPersonalVersions] = useState<PersonalAgentVersion[]>([]);
  const [versionHistoryLoading, setVersionHistoryLoading] = useState(false);
  const [versionHistoryError, setVersionHistoryError] = useState("");
  const [promoteTarget, setPromoteTarget] = useState("");
  const [promotingVersion, setPromotingVersion] = useState("");
  const [switchingDraftId, setSwitchingDraftId] = useState("");
  const [notice, setNotice] = useState("正在读取控制面草稿…");
  const promptEditorRef = useRef<HTMLTextAreaElement>(null);
  const bundleInputRef = useRef<HTMLInputElement>(null);
  const skillInputRef = useRef<HTMLInputElement>(null);
  const versionHistoryTriggerRef = useRef<HTMLButtonElement>(null);
  const versionHistoryRailRef = useRef<HTMLElement>(null);
  const versionHistoryCloseRef = useRef<HTMLButtonElement>(null);
  const leavePromptOpenRef = useRef(false);
  const allowNavigationRef = useRef(false);
  const draftSwitchingRef = useRef(false);
  const conflictReloadingRef = useRef(false);
  const historyGuardRef = useRef<UnsavedHistoryGuard | null>(null);
  const saveDraftRef = useRef<() => Promise<StudioDraft | null>>(async () => null);
  const savingRef = useRef(saving);
  savingRef.current = saving;
  const canEdit = membership.role !== "viewer";
  const canPublish = membership.role !== "viewer";
  const options = useMemo(
    () => capabilities
      ? capabilityOptions(capabilities)
      : { routes: [], tools: [], mcp: [], profiles: [], templates: [], runtimes: [] },
    [capabilities],
  );
  useDialogFocus({
    open: versionHistoryOpen,
    panelRef: versionHistoryRailRef,
    initialFocusRef: versionHistoryCloseRef,
    onEscape: () => {
      setVersionHistoryOpen(false);
      setPromoteTarget("");
    },
  });
  const visibleMcpOptions = useMemo(
    () => mcpOptionsForDraft(draft, options.mcp),
    [draft.name, draft.domain, options.mcp],
  );
  const contract = useMemo(
    () => evaluateStudioDraft(draft, { routes: options.routes, mcp: options.mcp }),
    [draft, options],
  );
  const policyOptions = useMemo(
    () => {
      const values = new Map(
        (capabilities?.policies ?? [])
          .filter((item) => item.enabled)
          .map((item) => [
            item.policyId,
            {
              id: item.policyId,
              label: item.label,
              description: item.description,
            },
          ]),
      );
      for (const policy of governedPolicies) {
        values.set(policy.policyId, {
          id: policy.policyId,
          label: policy.displayName,
          description: policy.publishedRevision
            ? `租户发布 r${policy.publishedRevision}`
            : `租户草稿 r${policy.revision} · 未发布`,
        });
      }
      return [...values.values()];
    },
    [capabilities, governedPolicies],
  );
  const selectedMcpTools = useMemo(
    () =>
      options.mcp
        .filter((item) => draft.mcpServers.includes(item.id))
        .flatMap((item) => item.tools),
    [draft.mcpServers, options.mcp],
  );
  const recommendedRuntime = useMemo(
    () => runtimeRecommendation(draft),
    [draft],
  );
  const recommendationApplied =
    draft.policy === recommendedRuntime.policy
    && draft.maxTurns === recommendedRuntime.maxTurns
    && draft.maxToolCalls === recommendedRuntime.maxToolCalls
    && draft.timeoutSeconds === null
    && draft.maxBudgetUsd === null
    && draft.maxModelTokens === null;
  const subagentCandidates = useMemo(
    () => drafts
      .filter((item) => isAgentVisible(item, showInternalAgents) && item.draftId !== draft.id && (!item.parentDraftId || item.parentDraftId === draft.id))
      .map((item) => ({
        draftId: item.draftId,
        parentDraftId: item.parentDraftId,
        ref: `${item.name}@${item.version}`,
        label: item.displayName,
        description: `${item.domain} · ${item.publishedVersion ? "已发布版本" : `可编辑草稿 r${item.revision}`}`,
        policy: item.publishedVersion ? "已发布快照" : "构建草稿",
        tools: [] as string[],
        status: item.publishedVersion ? "approved" as const : "draft" as const,
      })),
    [draft.id, drafts, showInternalAgents],
  );
  const filteredAgentRows = useMemo(() => {
    const query = agentQuery.trim().toLocaleLowerCase();
    const visible = drafts.filter((agent) => isAgentVisible(agent, showInternalAgents));
    if (!query) return visible;
    return visible.filter((agent) =>
      [agent.displayName, agent.name, agent.version, agent.publishedVersion ?? "草稿"]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query),
    );
  }, [agentQuery, drafts, showInternalAgents]);

  useEffect(() => {
    if (!releaseFeedbackOpen) return;
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setReleaseFeedbackOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [releaseFeedbackOpen]);

  useEffect(() => {
    if (!draft.agentId || draft.spaceId || !draft.publishedVersion) {
      setPersonalVersions([]);
      setVersionHistoryError("");
      setVersionHistoryLoading(false);
      return;
    }
    let active = true;
    setVersionHistoryLoading(true);
    setVersionHistoryError("");
    void studioClient.listPersonalAgentVersions(draft.agentId)
      .then((versions) => {
        if (active) setPersonalVersions(versions);
      })
      .catch((error: unknown) => {
        if (active) {
          setVersionHistoryError(
            error instanceof Error ? error.message : "版本历史暂时不可用",
          );
        }
      })
      .finally(() => {
        if (active) setVersionHistoryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [draft.agentId, draft.spaceId, draft.publishedVersion]);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setLoadError("");
      try {
        const [serverDrafts, serverCapabilities] = await Promise.all([
          studioClient.listAccessibleDrafts(),
          studioClient.capabilities(),
        ]);
        if (!active) return;
        setCapabilities(serverCapabilities);
        setDrafts(serverDrafts);
        const navigationState = new URLSearchParams(window.location.search);
        const requestedDraftId = navigationState.get("draft");
        const requestedSection = navigationState.get("section");
        setViewMode(requestedDraftId || requestedSection ? "editor" : "catalog");
        const targetDraft = requestedDraftId
          ? serverDrafts.find((item) => item.draftId === requestedDraftId)
          : null;
        if (requestedSection && requestedSection in sectionLabels) {
          // Builder is currently a single-page editor. Legacy deep links still
          // open the draft, but no longer expose retired orchestration,
          // evaluation, or publishing surfaces.
          setActiveSection("identity");
        }
        const migration = await migrateLegacyStudioDraft(
          window.localStorage,
          studioClient,
          canEdit,
        );
        if (!active) return;
        if (targetDraft) {
          const selected = await studioClient.getDraft(targetDraft.draftId, {
            expectedRevision: targetDraft.revision,
          });
          if (!active) return;
          setDraft(apiDraftToStudioDraft(selected));
          setNotice(
            navigationState.get("source") === "knowledge-sync"
              ? "知识库工具已更新：请确认绑定工具，然后保存、预检并发布新版本"
              : "已从控制面加载草稿",
          );
        } else if (migration.status === "imported") {
          setDraft(migration.draft);
          setDrafts(await studioClient.listAccessibleDrafts());
          setNotice("旧浏览器草稿已一次性导入控制面");
        } else if (serverDrafts.length > 0) {
          const selected = await studioClient.getDraft(serverDrafts[0].draftId, {
            expectedRevision: serverDrafts[0].revision,
          });
          if (!active) return;
          setDraft(apiDraftToStudioDraft(selected));
          setNotice("已从控制面加载草稿");
        } else {
          setDraft({ ...DEFAULT_STUDIO_DRAFT, id: "", revision: 0 });
          setActiveSection("identity");
          setNotice(canEdit ? "当前没有草稿，可新建第一个 Agent" : "当前没有可查看的草稿");
        }
      } catch (error) {
        if (!active) return;
        setLoadError(
          error instanceof Error ? error.message : `${PRODUCT_NAME}服务当前不可用`,
        );
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => { active = false; };
  }, [canEdit]);

  useEffect(() => {
    if (loading || loadError) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void Promise.all([
        studioClient.listPreviews(),
        studioClient.listEvalDatasets(),
        studioClient.listEvalRuns(),
        studioClient.listGovernedPolicies(),
      ]).then(([
        serverPreviews,
        serverDatasets,
        serverEvalRuns,
        serverGovernedPolicies,
      ]) => {
        if (!active) return;
        setPreviews(serverPreviews);
        setEvalDatasets(serverDatasets);
        setEvalRuns(serverEvalRuns);
        setGovernedPolicies(serverGovernedPolicies);
      }).catch(() => {
        // These panels are secondary; the primary editor remains available.
      });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [loadError, loading]);

  function updateDraft(update: Partial<StudioDraft>) {
    const next = applyStudioDraftUpdate(draft, update);
    const versionAutoBumped = next.version !== draft.version && !("version" in update);
    setDraft(next);
    setInspected(false);
    setServerValidation(null);
    setReleaseFeedbackOpen(false);
    setDirty(true);
    setConflict(false);
    setVersionConflict(false);
    setNotice(
      versionAutoBumped
        ? `检测到已发布版本发生修改，版本已自动递增为 ${next.version}（仍可手动编辑）`
        : "有尚未保存的修改"
    );
  }

  function updateSkill(name: string, nextSkill: StudioDraft["skills"][number]) {
    updateDraft({
      skills: draft.skills.map((candidate) =>
        candidate.name === name ? nextSkill : candidate
      ),
    });
    if (name !== nextSkill.name) setActiveSkillName(nextSkill.name);
  }

  function moveToPromptSection(heading: string) {
    const existingIndex = draft.systemPrompt.indexOf(heading);
    if (existingIndex >= 0) {
      promptEditorRef.current?.focus();
      promptEditorRef.current?.setSelectionRange(
        existingIndex,
        existingIndex + heading.length,
      );
      return;
    }
    const separator = draft.systemPrompt.trimEnd() ? "\n\n" : "";
    const nextPrompt = `${draft.systemPrompt.trimEnd()}${separator}${heading}\n\n`;
    updateDraft({ systemPrompt: nextPrompt });
    window.requestAnimationFrame(() => {
      const cursor = nextPrompt.length;
      promptEditorRef.current?.focus();
      promptEditorRef.current?.setSelectionRange(cursor, cursor);
    });
  }

  function handlePromptEditorKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "s") {
      event.preventDefault();
      if (canEdit && dirty && !saving) void saveDraft();
      return;
    }
    if (event.key !== "Tab") return;
    event.preventDefault();
    const editor = event.currentTarget;
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const nextPrompt = `${draft.systemPrompt.slice(0, start)}  ${draft.systemPrompt.slice(end)}`;
    updateDraft({ systemPrompt: nextPrompt });
    window.requestAnimationFrame(() => {
      editor.focus();
      editor.setSelectionRange(start + 2, start + 2);
    });
  }

  function toggleMcp(reference: string) {
    updateDraft({
      mcpServers: draft.mcpServers.includes(reference)
        ? draft.mcpServers.filter((item) => item !== reference)
        : [...draft.mcpServers, reference],
    });
  }

  function addPythonTool() {
    let sequence = draft.pythonTools.length + 1;
    let name = `custom_operator_${sequence}`;
    while (draft.pythonTools.some((tool) => tool.name === name)) {
      sequence += 1;
      name = `custom_operator_${sequence}`;
    }
    updateDraft({
      pythonTools: [
        ...draft.pythonTools,
        {
          name,
          description: "在隔离 Sandbox 中执行确定性计算并返回 JSON 结果。",
          inputSchema: {
            type: "object",
            properties: { value: { type: "number" } },
            required: ["value"],
            additionalProperties: false,
          },
          code: "def run(arguments):\n    value = arguments[\"value\"]\n    return {\"result\": value}",
        },
      ],
      toolExposureMode: "eager",
      requiredCapabilities: draft.requiredCapabilities.filter(
        (item) => item !== "tool_search",
      ),
    });
  }

  function updatePythonTool(
    index: number,
    update: Partial<StudioDraft["pythonTools"][number]>,
  ) {
    updateDraft({
      pythonTools: draft.pythonTools.map((tool, currentIndex) =>
        currentIndex === index ? { ...tool, ...update } : tool,
      ),
    });
  }

  function removePythonTool(index: number) {
    updateDraft({
      pythonTools: draft.pythonTools.filter(
        (_tool, currentIndex) => currentIndex !== index,
      ),
    });
  }

  function updateSubagent(index: number, update: Partial<StudioSubagent>) {
    updateDraft({
      subagents: draft.subagents.map((subagent, currentIndex) =>
        currentIndex === index ? { ...subagent, ...update } : subagent,
      ),
    });
  }

  function addSubagent() {
    if (draft.subagents.length >= 8) {
      setNotice("单个 Lead 最多绑定 8 个 Sub Agent");
      return;
    }
    const candidate = subagentCandidates.find((item) => item.ref === bindSubagentRef);
    if (!candidate) {
      setNotice("请选择要引用的智能体，或直接新建内部子智能体");
      return;
    }
    const sequence = draft.subagents.length + 1;
    updateDraft({
      subagents: [
        ...draft.subagents,
        {
          alias: `specialist-${sequence}`,
          ref: candidate.ref,
          responsibility: "说明 Lead 应在什么情况下委派，以及 Sub Agent 必须返回什么。",
          background: true,
        },
      ],
      builtinTools: draft.builtinTools.includes("Task")
        ? draft.builtinTools
        : [...draft.builtinTools, "Task"],
      policy:
        draft.policy === "production-read-only"
          ? "production-orchestrator"
          : draft.policy,
    });
    setBindSubagentRef("");
  }

  async function editSubagentDraft(ref: string) {
    const candidate = subagentCandidates.find((item) => item.ref === ref);
    if (!candidate) {
      setNotice(`没有找到可编辑草稿：${ref}`);
      return;
    }
    if (dirty && !(await saveDraft())) return;
    setReturnParentId(draft.id);
    await selectDraft(candidate.draftId);
    setActiveSection("identity");
    setNotice(`正在编辑 Sub Agent：${candidate.label}`);
  }

  async function createInternalSubagent() {
    if (!canEdit || subagentBusy || !subagentName.trim() || !subagentResponsibility.trim()) return;
    const parent = dirty ? await saveDraft() : draft;
    if (!parent?.id) return;
    setSubagentBusy(true);
    try {
      const created = await studioClient.createInternalSubagent(parent.id, parent.revision,
        subagentName.trim(), subagentResponsibility.trim());
      setDraft(apiDraftToStudioDraft(created.parent));
      setDirty(false);
      setServerValidation(null);
      setDrafts(await studioClient.listAccessibleDrafts());
      setSubagentFormOpen(false);
      setSubagentName("");
      setSubagentResponsibility("");
      setNotice(`已创建并绑定“${created.child.spec.displayName}”，可在协作角色中编辑`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建子智能体失败");
    } finally { setSubagentBusy(false); }
  }

  async function changePlacement(childId: string, parentId: string | null) {
    if (!canEdit || subagentBusy) return;
    if (dirty && !(await saveDraft())) return;
    setSubagentBusy(true);
    try {
      const child = await studioClient.getDraft(childId, { maxAgeMs: 0 });
      const updated = await studioClient.setDraftPlacement(childId, child.revision, parentId);
      if (childId === draft.id) setDraft(apiDraftToStudioDraft(updated));
      setDrafts(await studioClient.listAccessibleDrafts());
      setNotice(parentId ? "已移入协作角色，普通列表不再显示；已有版本引用保持有效" : "已恢复独立智能体入口");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "更新展示位置失败");
    } finally { setSubagentBusy(false); }
  }

  async function returnToCatalog() {
    if (saving) return;
    if (dirty) {
      const decision = await requestDecision({ title: "有未保存的修改", description: "保存后返回智能体管理，或放弃这次修改。", confirmLabel: "保存并返回", cancelLabel: "继续编辑", discardLabel: "放弃修改并返回" });
      if (decision === "cancel") return;
      if (decision === "confirm" && !await saveDraft()) return;
      if (decision === "discard") setDirty(false);
    }
    setBuilderAssistantOpen(false); setConfigEditorOpen(false); setReturnParentId(null); setViewMode("catalog");
  }

  async function returnToParent() {
    const parentId = returnParentId ?? draft.parentDraftId;
    if (!parentId) { setViewMode("catalog"); return; }
    const child = dirty ? await saveDraft() : draft;
    if (!child) return;
    try {
      let parent = apiDraftToStudioDraft(await studioClient.getDraft(parentId, { maxAgeMs: 0 }));
      // Internal collaborators follow the edited draft when returning to their
      // parent; independent references keep their explicitly selected version.
      if (child.parentDraftId === parentId && parent.subagents.some((binding) =>
        binding.ref.split("@")[0] === child.name && binding.ref !== `${child.name}@${child.version}`)) {
        parent = apiDraftToStudioDraft(await studioClient.replaceDraft({ ...parent,
          subagents: parent.subagents.map((binding) => binding.ref.split("@")[0] === child.name
            ? { ...binding, ref: `${child.name}@${child.version}` } : binding),
        }));
      }
      setDraft(parent);
      setDrafts(await studioClient.listAccessibleDrafts());
      setDirty(false);
      setConflict(false);
      setServerValidation(null);
      setBuilderAssistantOpen(false);
      setReturnParentId(null);
      setActiveSection("identity");
      setNotice("已返回父智能体，内部角色绑定已同步");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "返回父智能体失败，当前内容已保留");
    }
  }

  function removeSubagent(index: number) {
    const next = draft.subagents.filter(
      (_subagent, currentIndex) => currentIndex !== index,
    );
    updateDraft({
      subagents: next,
      builtinTools:
        next.length === 0
          ? draft.builtinTools.filter((tool) => tool !== "Task")
          : draft.builtinTools,
    });
  }

  async function saveDraft(candidate: StudioDraft = draft): Promise<StudioDraft | null> {
    if (!canEdit) {
      setNotice("当前角色只有查看权限");
      return null;
    }
    setSaving(true);
    try {
      let saved;
      if (!candidate.id) {
        setBuilderAssistantMode("create");
        setBuilderAssistantOpen(true);
        setNotice("请先在构建助手中描述任务并创建草稿");
        return null;
      } else {
        saved = await studioClient.replaceDraft(candidate);
      }
      const next = apiDraftToStudioDraft(saved);
      setDraft(next);
      setDrafts(await studioClient.listAccessibleDrafts());
      setDirty(false);
      setConflict(false);
      setVersionConflict(false);
      setNotice(`已保存到控制面 · revision ${next.revision}`);
      return next;
    } catch (error) {
      if (error instanceof StudioApiError && error.status === 409) {
        setConflict(true);
        setNotice("保存冲突：控制面已有更新，本地修改尚未丢失");
      } else {
        setNotice(error instanceof Error ? error.message : "保存失败");
      }
      return null;
    } finally {
      setSaving(false);
    }
  }

  saveDraftRef.current = () => saveDraft();

  useEffect(() => {
    const historyGuard = createUnsavedHistoryGuard(
      window.history,
      `agent-studio-${createRandomId()}`,
    );
    historyGuardRef.current = historyGuard;

    function protectHistoryNavigation(event: PopStateEvent) {
      if (allowNavigationRef.current) return;
      if (historyGuard.handlePopState(event.state) !== "prompt") return;
      if (leavePromptOpenRef.current) return;
      if (savingRef.current) {
        setNotice("正在保存当前草稿，完成后再离开");
        return;
      }

      leavePromptOpenRef.current = true;
      void requestDecision({
        title: "有未保存的修改",
        description: "可以保存后返回，也可以放弃这次修改直接离开。",
        confirmLabel: "保存并返回",
        cancelLabel: "继续编辑",
        discardLabel: "放弃修改并返回",
        context: <span>浏览器历史：返回上一页</span>,
      }).then(async (decision) => {
        if (decision === "cancel") return;
        if (decision === "confirm") {
          const saved = await saveDraftRef.current();
          if (!saved) return;
        }
        historyGuard.deactivate(() => {
          window.setTimeout(() => {
            allowNavigationRef.current = true;
            window.history.back();
          }, 0);
        });
      }).finally(() => {
        leavePromptOpenRef.current = false;
      });
    }

    window.addEventListener("popstate", protectHistoryNavigation);
    return () => {
      window.removeEventListener("popstate", protectHistoryNavigation);
      if (historyGuardRef.current === historyGuard) {
        historyGuardRef.current = null;
      }
    };
  }, [requestDecision]);

  useEffect(() => {
    const historyGuard = historyGuardRef.current;
    if (!historyGuard) return;
    if (dirty) {
      allowNavigationRef.current = false;
      historyGuard.activate(window.location.href);
    } else {
      historyGuard.deactivate();
    }
  }, [dirty]);

  useEffect(() => {
    if (!dirty) return;

    function protectBrowserNavigation(event: BeforeUnloadEvent) {
      if (allowNavigationRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    }

    function protectLinkedNavigation(event: MouseEvent) {
      const eventTarget = event.target;
      const anchor = eventTarget instanceof Element
        ? eventTarget.closest<HTMLAnchorElement>("a[href]")
        : null;
      if (!anchor) return;
      const destination = guardedNavigationDestination({
        currentHref: window.location.href,
        targetHref: anchor.href,
        button: event.button,
        altKey: event.altKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        shiftKey: event.shiftKey,
        target: anchor.getAttribute("target"),
        download: anchor.hasAttribute("download"),
      });
      if (!destination) return;

      event.preventDefault();
      event.stopPropagation();
      if (leavePromptOpenRef.current) return;
      if (saving) {
        setNotice("正在保存当前草稿，完成后再离开");
        return;
      }

      leavePromptOpenRef.current = true;
      const destinationName = navigationLabel(
        anchor.getAttribute("aria-label") ?? anchor.textContent,
        destination,
      );
      void requestDecision({
        title: "有未保存的修改",
        description: "可以保存后前往目标页面，也可以放弃这次修改直接离开。",
        confirmLabel: "保存并离开",
        cancelLabel: "继续编辑",
        discardLabel: "放弃修改并离开",
        context: <span>前往：{destinationName}</span>,
      }).then(async (decision) => {
        if (decision === "cancel") return;
        if (decision === "confirm") {
          const saved = await saveDraft();
          if (!saved) return;
        }
        const navigate = () => {
          allowNavigationRef.current = true;
          if (destination.origin === window.location.origin) {
            router.push(`${destination.pathname}${destination.search}${destination.hash}`);
          } else {
            window.location.assign(destination.href);
          }
        };
        const historyGuard = historyGuardRef.current;
        if (historyGuard?.isActive()) {
          historyGuard.deactivate(() => window.setTimeout(navigate, 0));
        } else {
          navigate();
        }
      }).finally(() => {
        leavePromptOpenRef.current = false;
      });
    }

    window.addEventListener("beforeunload", protectBrowserNavigation);
    document.addEventListener("click", protectLinkedNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", protectBrowserNavigation);
      document.removeEventListener("click", protectLinkedNavigation, true);
    };
  }, [canEdit, dirty, draft, requestDecision, router, saving]);

  async function applyRecommendedExecutionProfile(profileId: string) {
    const saved = await saveDraft({ ...draft, executionProfile: profileId });
    if (!saved?.id) return;
    setInspecting(true);
    try {
      const validation = await studioClient.validateDraft(saved.id);
      setServerValidation(validation);
      setInspected(true);
      setReleaseFeedbackOpen(
        validation.issues.some((issue) =>
          issue.severity === "error" || issue.severity === "warning"
        ),
      );
      setNotice(
        validation.productionEligible
          ? `已切换、保存并检查 · ${profileId} 可用于生产`
          : `已切换、保存并检查 · ${profileId} 仍有生产限制`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Profile 已保存，重新检查失败");
    } finally {
      setInspecting(false);
    }
  }

  async function inspectDraft(): Promise<{
    draft: StudioDraft;
    validation: StudioValidation;
  } | null> {
    const current = dirty || !draft.id ? await saveDraft() : draft;
    if (!current?.id) return null;
    setInspecting(true);
    try {
      const validation = await studioClient.validateDraft(current.id);
      setServerValidation(validation);
      setInspected(true);
      const errors = validation.issues.filter(
        (issue) => issue.severity === "error" && issue.stage === "publish",
      );
      const productionErrors = validation.issues.filter(
        (issue) => issue.severity === "error" && issue.stage === "production",
      );
      const warnings = validation.issues.filter((issue) => issue.severity === "warning");
      setReleaseFeedbackOpen(
        errors.length + productionErrors.length + warnings.length > 0,
      );
      setNotice(
        validation.ready
          ? productionErrors.length
            ? `发布检查通过 · ${productionErrors.length} 项生产部署限制`
            : warnings.length
            ? `检查通过 · ${warnings.length} 项上线前提醒`
            : "检查通过，可以发布"
          : `发布被阻止 · ${errors.length} 项需要处理`,
      );
      return { draft: current, validation };
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "检查失败");
      return null;
    } finally {
      setInspecting(false);
    }
  }

  async function handleReleaseAction() {
    if (!canEdit || saving || inspecting || publishing) return;
    const release = dirty || !serverValidation
      ? await inspectDraft()
      : { draft, validation: serverValidation };
    if (!release) return;
    if (!release.validation.ready) {
      setReleaseFeedbackOpen(true);
      return;
    }
    if (!canPublish) {
      setNotice("检查已通过，但当前角色没有发布权限");
      return;
    }
    await publishDraft(release.draft, release.validation);
  }

  async function startNewDraft() {
    if (!canEdit || saving) return;
    if (!dirty) {
      setDraft({ ...DEFAULT_STUDIO_DRAFT, id: "", revision: 0 }); setViewMode("editor"); setConfigEditorOpen(false);
      setBuilderCreationSession((current) => current + 1);
      setBuilderAssistantMode("create");
      setBuilderAssistantOpen(true);
      return;
    }
    const confirmed = await requestConfirmation({
      title: "保存当前修改并新建？",
      description: `${PRODUCT_NAME}会先保存当前草稿，再创建一个新的个人智能体；保存失败或发生版本冲突时会保留当前编辑内容。`,
      confirmLabel: "保存并新建",
      cancelLabel: "继续编辑",
      context: <span>当前草稿：{draft.displayName}</span>,
    });
    if (!confirmed) return;
    const saved = await saveDraft();
    if (!saved) return;
    setDraft({ ...DEFAULT_STUDIO_DRAFT, id: "", revision: 0 }); setViewMode("editor"); setConfigEditorOpen(false);
    setBuilderCreationSession((current) => current + 1);
    setBuilderAssistantMode("create");
    setBuilderAssistantOpen(true);
  }

  function openConfiguration(section: StudioSection, label?: string) {
    setActiveSection(section); setConfigEditorOpen(true);
    window.setTimeout(() => {
      const root = document.querySelector(`[data-config-editor="true"]`);
      const target = label ? Array.from(root?.querySelectorAll<HTMLElement>("h3,h4,strong") ?? []).find(node => node.textContent?.includes(label)) : root?.querySelector(`#${section}-title`);
      target?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 0);
  }

  async function openTryRun() {
    setTestRequest(current => current + 1);
    const current = dirty ? await saveDraft() : draft;
    if (!current?.id) return;
    setTryRunSeed({
      prompt: current.taskContract?.examples[0] ?? "",
      autoStart: false,
      recommendation: null,
    });
    setBuilderAssistantMode("run");
    setBuilderAssistantOpen(true);
  }

  async function selectDraft(draftId: string) {
    if (draftId === draft.id || draftSwitchingRef.current) return;
    draftSwitchingRef.current = true;
    try {
      const target = drafts.find((item) => item.draftId === draftId);
      if (dirty) {
        const confirmed = await requestConfirmation({
          title: "保存当前修改并切换？",
          description: `${PRODUCT_NAME}会先保存当前草稿，再切换智能体；保存失败或发生版本冲突时会保留当前编辑内容。`,
          confirmLabel: "保存并切换",
          cancelLabel: "继续编辑",
          context: <span>切换到：{target?.displayName ?? "所选智能体"}</span>,
        });
        if (!confirmed) return;
        const saved = await saveDraft();
        if (!saved) return;
      }
      setSwitchingDraftId(draftId);
      setNotice(`正在切换到 ${target?.displayName ?? "所选智能体"}…`);
      const selected = await studioClient.getDraft(draftId, {
        expectedRevision: target?.revision,
      });
      setDraft(apiDraftToStudioDraft(selected));
      setDirty(false);
      setConflict(false);
      setVersionConflict(false);
      setServerValidation(null);
      setReleaseFeedbackOpen(false);
      setNotice("已从控制面切换草稿");
      setBuilderAssistantOpen(false);
      return true;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "加载草稿失败");
    } finally {
      setSwitchingDraftId("");
      draftSwitchingRef.current = false;
    }
  }

  async function openDraftEditor(draftId: string) {
    if (draftId !== draft.id) await selectDraft(draftId);
    setBuilderAssistantMode("run");
    setReturnParentId(null);
    setConfigEditorOpen(false);
    setViewMode("editor");
  }

  async function deleteCatalogDraft(target: StudioDraftSummary) {
    if (!canEdit || deleting || saving || target.spaceId) return;
    const confirmed = await requestConfirmation({
      title: `删除“${target.displayName}”？`,
      description: target.publishedVersion
        ? "草稿会立即移除，智能体也不再出现在新任务目录。已发布的不可变版本、已有任务和审计记录会保留。"
        : "草稿会立即移除且无法恢复。已有任务和审计记录不会被删除。",
      confirmLabel: "删除智能体",
      cancelLabel: "取消",
      tone: "danger",
      context: <span>{target.name}@{target.version}</span>,
    });
    if (!confirmed) return;

    setDeleting(true);
    try {
      await studioClient.deleteDraft(target.draftId, target.revision);
      const remaining = await studioClient.listAccessibleDrafts();
      setDrafts(remaining);
      if (target.draftId === draft.id) {
        setDirty(false);
        setConflict(false);
        setVersionConflict(false);
        setServerValidation(null);
        setReleaseFeedbackOpen(false);
        setPersonalVersions([]);
        if (remaining.length > 0) {
          const selected = await studioClient.getDraft(remaining[0].draftId, {
            expectedRevision: remaining[0].revision,
            maxAgeMs: 0,
          });
          setDraft(apiDraftToStudioDraft(selected));
        } else {
          setDraft({ ...DEFAULT_STUDIO_DRAFT, id: "", revision: 0 });
          setActiveSection("identity");
        }
      }
      setNotice(`已删除 ${target.displayName}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除智能体失败");
    } finally {
      setDeleting(false);
    }
  }

  async function deleteCurrentDraft() {
    if (!canEdit || !draft.id || deleting || saving) return;
    const confirmed = await requestConfirmation({
      title: `删除“${draft.displayName}”？`,
      description: draft.publishedVersion
        ? "草稿会立即移除，智能体也不再出现在新任务目录。已发布的不可变版本、已有任务和审计记录会保留。"
        : "草稿会立即移除且无法恢复。已有任务和审计记录不会被删除。",
      confirmLabel: "删除智能体",
      cancelLabel: "取消",
      tone: "danger",
      context: (
        <span>
          {draft.name}@{draft.version}
          {dirty ? " · 未保存修改也会丢弃" : ""}
        </span>
      ),
    });
    if (!confirmed) return;

    setDeleting(true);
    try {
      await studioClient.deleteDraft(draft.id, draft.revision);
      const remaining = await studioClient.listAccessibleDrafts();
      setDrafts(remaining);
      setDirty(false);
      setConflict(false);
      setVersionConflict(false);
      setServerValidation(null);
      setReleaseFeedbackOpen(false);
      setPersonalVersions([]);
      if (remaining.length > 0) {
        const selected = await studioClient.getDraft(remaining[0].draftId, {
          expectedRevision: remaining[0].revision,
          maxAgeMs: 0,
        });
        setDraft(apiDraftToStudioDraft(selected));
        setNotice(`已删除 ${draft.displayName}，并切换到 ${remaining[0].displayName}`);
      } else {
        setDraft({ ...DEFAULT_STUDIO_DRAFT, id: "", revision: 0 });
        setActiveSection("identity");
        setNotice(`已删除 ${draft.displayName}，可以新建智能体`);
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除智能体失败");
    } finally {
      setDeleting(false);
    }
  }

  async function reloadAfterConflict() {
    if (!draft.id || conflictReloadingRef.current) return;
    const confirmed = await requestConfirmation({
      title: "放弃本地修改并加载控制面版本？",
      description:
        "控制面版本会替换当前表单里所有尚未保存的修改。"
        + `${PRODUCT_NAME}不会为这些本地修改生成恢复点。`,
      confirmLabel: "放弃并加载",
      cancelLabel: "继续编辑",
      tone: "danger",
      context: <span>{draft.displayName} · 本地 revision {draft.revision}</span>,
    });
    if (!confirmed || conflictReloadingRef.current) return;
    conflictReloadingRef.current = true;
    setReloadingConflict(true);
    try {
      const selected = await studioClient.getDraft(draft.id, { maxAgeMs: 0 });
      const latest = apiDraftToStudioDraft(selected);
      setDraft(latest);
      setDirty(false);
      setConflict(false);
      setVersionConflict(false);
      setNotice(`已放弃本地修改并加载控制面 revision ${latest.revision}`);
    } catch (error) {
      setNotice(
        `控制面版本加载失败，本地修改仍保留：${
          error instanceof Error ? error.message : "请稍后重试"
        }`,
      );
    } finally {
      setReloadingConflict(false);
      conflictReloadingRef.current = false;
    }
  }

  async function downloadBundle() {
    const current = dirty ? await saveDraft() : draft;
    if (!current?.id) return;
    try {
      await studioClient.downloadBundle(current.id);
      setNotice("Bundle 下载已开始");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Bundle 下载失败");
    }
  }

  async function downloadNexauBundle() {
    const current = dirty ? await saveDraft() : draft;
    if (!current?.id) return;
    try {
      await studioClient.downloadNexauBundle(current.id);
      setNotice("NexAU ZIP 导出已开始");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "NexAU ZIP 导出失败");
    }
  }

  async function importBundle(file: File) {
    if (dirty && !(await requestConfirmation({
      title: "导入并离开当前草稿？",
      description: "当前未保存修改会丢失。导入完成后将切换到 Bundle 中的 Agent，已有不可变版本不会被覆盖。",
      confirmLabel: "继续导入",
      context: <code>{file.name}</code>,
      tone: "danger",
    }))) {
      return;
    }
    setImportingBundle(true);
    setImportFeedback({message: `正在导入 ${file.name}…`});
    try {
      const imported = await studioClient.importBundle(file);

      setDraft(apiDraftToStudioDraft(imported.draft));
      setBuilderAssistantMode("run");
      setBuilderAssistantOpen(true);
      setTryRunSeed({prompt: "", autoStart: false, recommendation: null});
      setConfigEditorOpen(false);
      setViewMode("editor");
      void studioClient.listAccessibleDrafts().then(setDrafts).catch(() => {});
      setDirty(false);
      setConflict(false);
      setVersionConflict(false);
      setServerValidation(null);
      setReleaseFeedbackOpen(false);
      setInspected(false);
      const message = imported.lossless && imported.roundTripVerified
          ? `已无损导入 ${imported.draft.spec.name}@${imported.draft.spec.version}，可继续编辑`
          : `已兼容导入 Agent；${imported.warnings.join("；") || "请保存并重新预检"}`;
      setNotice(message); setImportFeedback({message});
    } catch (error) {
      const message = error instanceof Error ? error.message : "压缩包导入失败";
      setNotice(message); setImportFeedback({message, error: true});
    } finally {
      setImportingBundle(false);
      if (bundleInputRef.current) bundleInputRef.current.value = "";
    }
  }

  async function installSkill(file: File) {
    setImportingSkill(true);
    try {
      const current = dirty || !draft.id ? await saveDraft() : draft;
      if (!current?.id) return;
      const installed = await studioClient.installSkill(
        current.id,
        current.revision,
        file,
      );
      const saved = apiDraftToStudioDraft(installed.draft);
      const duplicate = current.skills.some(
        (candidate) => candidate.name === installed.skillName,
      );
      setDraft(saved);
      setDrafts(await studioClient.listAccessibleDrafts());
      setDirty(false);
      setConflict(false);
      setVersionConflict(false);
      setActiveSkillName(installed.skillName);
      setActiveSection("skills");
      setSkillImportReport({
        skillName: installed.skillName,
        findings: installed.findings,
        warnings: installed.warnings,
      });
      const scriptCount = installed.findings.filter((item) =>
        item.startsWith("包含可执行脚本：")
      ).length;
      const dependencyCount = installed.findings.filter((item) =>
        item.startsWith("包含依赖声明：")
      ).length;
      const summary = [
        `${installed.fileCount.toLocaleString("zh-CN")} 个文件`,
        scriptCount ? `${scriptCount} 个脚本` : "",
        dependencyCount ? `${dependencyCount} 个依赖声明` : "",
        installed.binaryFileCount ? `${installed.binaryFileCount} 个二进制资源` : "",
      ].filter(Boolean).join(" · ");
      setNotice(
        `${duplicate ? "已更新" : "已安装"} Skill：${installed.skillName} · ${summary}`
        + (installed.findings.length ? " · 需审阅" : ""),
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Skill 安装失败");
    } finally {
      setImportingSkill(false);
      if (skillInputRef.current) skillInputRef.current.value = "";
    }
  }

  async function uninstallSkill(name: string) {
    const installed = draft.skills.find((candidate) => candidate.name === name);
    if (!installed || saving || !canEdit) return;
    const fileCount = installed.files?.length ?? 0;
    const confirmed = await requestConfirmation({
      title: `从草稿卸载 Skill“${name}”？`,
      description:
        `它及其 ${fileCount.toLocaleString("zh-CN")} 个附加文件将从当前草稿中移除。`
        + "已发布的不可变历史版本不会被修改。",
      confirmLabel: "卸载 Skill",
      tone: "danger",
    });
    if (!confirmed) return;
    const skills = draft.skills.filter((candidate) => candidate.name !== name);
    const saved = await saveDraft({ ...draft, skills });
    if (!saved) return;
    setActiveSkillName(skills[0]?.name ?? "");
    setSkillImportReport((current) =>
      current?.skillName === name ? null : current
    );
    setNotice(
      `已卸载 Skill：${name} · 已从当前草稿移除 ${fileCount.toLocaleString("zh-CN")} 个附加文件`,
    );
  }

  async function publishDraft(
    targetDraft: StudioDraft = draft,
    validation: StudioValidation | null = serverValidation,
  ) {
    if (!targetDraft.id || !validation?.ready || !canPublish) return;
    setPublishing(true);
    try {
      const version = await studioClient.publishDraft(targetDraft.id, targetDraft.revision);
      const [refreshed, rows] = await Promise.all([
        studioClient.getDraft(targetDraft.id),
        studioClient.listAccessibleDrafts(),
      ]);
      setDraft(apiDraftToStudioDraft(refreshed));
      setDrafts(rows);
      setDirty(false);
      setConflict(false);
      setVersionConflict(false);
      setNotice(`已发布不可变版本 ${version.name}@${version.version}`);
    } catch (error) {
      if (error instanceof StudioApiError && error.code === "version_conflict") {
        setVersionConflict(true);
      } else if (error instanceof StudioApiError && error.status === 409) {
        setConflict(true);
      }
      setNotice(error instanceof Error ? error.message : "发布失败");
    } finally {
      setPublishing(false);
    }
  }

  async function promotePersonalVersion(version: string) {
    if (!draft.agentId || draft.spaceId || !canPublish || promotingVersion) return;
    setPromotingVersion(version);
    try {
      const promoted = await studioClient.promotePersonalAgentVersion(
        draft.agentId,
        version,
      );
      setPersonalVersions((current) =>
        current.map((item) => ({
          ...item,
          current_version: promoted.current_version,
        })),
      );
      setPromoteTarget("");
      setNotice(`已将 ${draft.name}@${version} 设为当前版本；新任务立即生效`);
    } catch (error) {
      setVersionHistoryError(
        error instanceof Error ? error.message : "版本切换失败",
      );
    } finally {
      setPromotingVersion("");
    }
  }

  async function createPreview() {
    if (!draft.id || dirty || !serverValidation?.ready || !canEdit) return;
    const reusable = previews.find(
      (item) => item.draftId === draft.id
        && item.draftRevision === draft.revision
        && item.contentHash === serverValidation.contentHash
        && item.packageHash === serverValidation.packageHash
        && !["cancelled", "failed", "expired"].includes(item.status),
    );
    if (reusable) {
      await refreshPreview(reusable.previewId);
      setNotice(`复用当前 Preview · ${reusable.status}`);
      return;
    }
    setCreatingPreview(true);
    try {
      const idempotencyKey = [
        "studio-preview",
        draft.id,
        `r${draft.revision}`,
        createRandomId(),
      ].join(":");
      const preview = await studioClient.createPreview(
        draft.id,
        draft.revision,
        idempotencyKey,
      );
      const refreshed = await studioClient.getPreview(preview.previewId);
      setPreviews(await studioClient.listPreviews());
      setNotice(`Preview ${refreshed.status} · 测试身份 · 1 小时 TTL`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Preview 创建失败");
    } finally {
      setCreatingPreview(false);
    }
  }

  async function refreshPreview(previewId: string) {
    try {
      const refreshed = await studioClient.getPreview(previewId);
      setPreviews((current) => [
        refreshed,
        ...current.filter((item) => item.previewId !== previewId),
      ]);
      setNotice(`Preview 状态：${refreshed.status}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Preview 状态读取失败");
    }
  }

  async function cancelPreview(previewId: string) {
    try {
      await studioClient.cancelPreview(previewId);
      await refreshPreview(previewId);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Preview 取消失败");
    }
  }

  async function refreshDeployments(agentName = draft.name) {
    if (!agentName) return;
    const [nextEnvironments, nextDeployments, nextSnapshots] = await Promise.all([
      studioClient.listEnvironments(agentName),
      studioClient.listDeployments(agentName),
      studioClient.listDeploymentSnapshots(agentName),
    ]);
    setEnvironments(nextEnvironments);
    setDeployments(nextDeployments);
    setDeploymentSnapshots(nextSnapshots);
  }

  function sectionSummary(section: StudioSection) {
    switch (section) {
      case "identity":
        return draft.name && draft.displayName ? "完整" : "待补充";
      case "model":
        return draft.model ? "已选择" : "待选择";
      case "prompt":
        return `${contract.promptSections}/5`;
      case "orchestration":
        return draft.subagents.length ? `${draft.subagents.length} 角色` : "单 Agent";
      case "skills":
        return `${draft.skills.length} 个`;
      case "capabilities":
        return `${contract.toolCount} 项`;
      case "runtime":
        return "平台锁定";
      case "trial":
        return activePreview?.preflightResult?.status === "passed"
          ? "预检通过"
          : activePreview
            ? "进行中"
            : "待试跑";
      case "evaluation":
        return draft.evaluationEnabled ? `${draft.evalCases.length} 用例` : "已关闭";
    }
  }

  const selectedRoute =
    options.routes.find((route) => route.id === draft.modelRoute) ?? options.routes[0];
  const selectedModelValue = `${draft.modelRoute}::${draft.model}`;
  const modelChoices = options.routes.flatMap((route) =>
    route.models.map((model) => ({
      value: `${route.id}::${model}`,
      label: route.models.length > 1 ? `${route.label} · ${model}` : route.label,
      routeId: route.id,
      model,
    })),
  );
  const workerToolOptions = options.tools.filter(
    (tool) => !UNAVAILABLE_WORKER_TOOL_IDS.has(tool.id) && !["WebSearch", "WebFetch"].includes(tool.id),
  );
  const enabledWorkerToolIds = workerToolOptions
    .filter((tool) => draft.builtinTools.includes(tool.id))
    .map((tool) => tool.id);
  const matchesWorkerPreset = (preset: readonly string[]) => {
    const availablePreset = preset.filter((tool) => workerToolOptions.some((item) => item.id === tool));
    return enabledWorkerToolIds.length === availablePreset.length
      && availablePreset.every((tool) => enabledWorkerToolIds.includes(tool));
  };
  const workerToolPreset = matchesWorkerPreset(WORKER_STANDARD_TOOL_IDS)
    ? "standard"
    : matchesWorkerPreset(WORKER_READONLY_TOOL_IDS)
      ? "readonly"
      : "custom";
  const skill = draft.skills.find((candidate) => candidate.name === activeSkillName)
    ?? draft.skills[0];
  const validationReady = serverValidation?.ready ?? contract.ready;
  const activePreview = previews.find(
    (item) => item.draftId === draft.id
      && !["cancelled", "failed", "expired"].includes(item.status),
  ) ?? previews.find((item) => item.draftId === draft.id);
  const agentDatasets = evalDatasets
    .filter((item) => item.agentName === draft.name)
    .sort((left, right) => right.version - left.version);
  const latestDataset = agentDatasets[0];
  const agentEvalRuns = evalRuns.filter((item) => item.run.agentName === draft.name);
  const activeEvalRun = agentEvalRuns.find((item) =>
    ["queued", "running", "cancelling"].includes(item.run.status),
  ) ?? agentEvalRuns[0];
  const publishedCurrent = Boolean(
    draft.publishedVersion === draft.version
    && draft.publishedHash
    && serverValidation?.contentHash === draft.publishedHash
    && draft.publishedPackageHash
    && serverValidation?.packageHash === draft.publishedPackageHash
  );
  const currentPersonalVersion = draft.agentId
    ? personalVersions.find((version) => version.agent_id === draft.agentId)?.current_version
      ?? null
    : draft.publishedVersion;
  const taskVersion = draft.spaceId
    ? draft.publishedVersion
    : currentPersonalVersion;
  const taskHref = taskVersion
    ? draft.spaceId
      ? `/?space=${encodeURIComponent(draft.spaceId)}&agent=${encodeURIComponent(draft.name)}&version=${encodeURIComponent(taskVersion)}`
      : `/?agent=${encodeURIComponent(draft.name)}&version=${encodeURIComponent(taskVersion)}&owner=${encodeURIComponent(user.user_id)}`
    : null;
  const snapshotById = new Map(
    deploymentSnapshots.map((snapshot) => [snapshot.snapshotId, snapshot]),
  );
  const activeDeployment = deployments.find((item) =>
    ["queued", "reconciling"].includes(item.deployment.status),
  );
  const deployedCurrent = environments.some((environment) =>
    environment.routes.some((route) =>
      snapshotById.get(route.snapshotId)?.agentVersion === draft.publishedVersion,
    ),
  );
  const activeLifecycleStage = publishedCurrent
    ? deployedCurrent ? "deploy" : "version"
    : activePreview && !activePreview.stale
      ? "preview"
      : inspected
        ? "check"
        : "draft";
  const activeLifecycleIndex = lifecycleStages.findIndex(
    (stage) => stage.id === activeLifecycleStage,
  );
  const validationErrors = serverValidation?.issues.filter(
    (issue) => issue.severity === "error" && issue.stage === "publish",
  ) ?? [];
  const productionValidationErrors = serverValidation?.issues.filter(
    (issue) => issue.severity === "error" && issue.stage === "production",
  ) ?? [];
  const validationWarnings = serverValidation?.issues.filter(
    (issue) => issue.severity === "warning",
  ) ?? [];
  const selectedExecutionProfile = options.profiles.find(
    (profile) => profile.profileId === draft.executionProfile,
  );
  const selectedMcpCapabilities = options.mcp.filter(
    (mcp) => draft.mcpServers.includes(mcp.id),
  );
  const compatibleExecutionProfiles = options.profiles.filter(
    (profile) => selectedMcpCapabilities.every(
      (mcp) => profile.networkAccess.includes(mcp.network)
        && profile.allowedMcpReferences.includes(mcp.id),
    ),
  );
  const productionExecutionProfiles = compatibleExecutionProfiles.filter(
    (profile) => profile.productionAllowed,
  );
  const selectedProfileSupportsMcp = Boolean(
    selectedExecutionProfile
    && compatibleExecutionProfiles.some(
      (profile) => profile.profileId === selectedExecutionProfile.profileId,
    ),
  );
  const recommendedExecutionProfile =
    productionExecutionProfiles[0]
    ?? compatibleExecutionProfiles[0]
    ?? null;
  const incompatibleMcpReferences = draft.mcpServers.filter(
    (reference) => {
      const mcp = options.mcp.find((item) => item.id === reference);
      return Boolean(
        selectedExecutionProfile
        && mcp
        && (
          !selectedExecutionProfile.allowedMcpReferences.includes(reference)
          || !selectedExecutionProfile.networkAccess.includes(mcp.network)
        ),
      );
    },
  );
  const releaseTone = dirty
    ? "pending"
    : !serverValidation
      ? "unchecked"
      : validationErrors.length
        ? "blocked"
        : "ready";
  const releaseActionLabel = publishing
    ? "发布中…"
    : saving || inspecting
      ? dirty ? "保存并检查中…" : "检查中…"
      : dirty
        ? "保存并检查"
        : !serverValidation
          ? "检查发布条件"
          : validationErrors.length
            ? `查看 ${validationErrors.length} 项阻断`
            : !canPublish
              ? "等待管理员发布"
              : publishedCurrent
                ? "重新核验发布"
                : `发布 ${draft.version}`;
  const lifecycleLabel = validationErrors.length
    ? "发布被阻止"
    : dirty
      ? "待保存"
      : serverValidation?.ready
        ? publishedCurrent ? "版本已发布" : "可以发布"
        : lifecycleStages[activeLifecycleIndex]?.label;
  const lifecycleDetail = validationErrors.length
    ? `${validationErrors.length} 项需处理`
    : productionValidationErrors.length && serverValidation?.ready
      ? `${productionValidationErrors.length} 项生产限制`
    : validationWarnings.length && serverValidation?.ready
      ? `${validationWarnings.length} 项提醒`
      : lifecycleStages[activeLifecycleIndex]?.detail;

  const activeStage = stageForSection(activeSection);
  const activeStageMeta = STUDIO_STAGES.find((stage) => stage.id === activeStage)
    ?? STUDIO_STAGES[0];
  const stageBlockedCounts = useMemo(() => {
    const counts: Record<StudioStage, number> = {
      goal: 0,
      capabilities: 0,
      behavior: 0,
      trial: 0,
      publish: 0,
    };
    for (const issue of serverValidation?.issues ?? []) {
      if (issue.severity !== "error") continue;
      counts[stageForSection(validationIssueSection(issue))] += 1;
    }
    if (!contract.ready) counts.goal += contract.issues.length;
    return counts;
  }, [serverValidation, contract]);
  const stageState = (stageId: StudioStage): "complete" | "blocked" | "pending" => {
    if (stageBlockedCounts[stageId] > 0) return "blocked";
    switch (stageId) {
      case "goal":
        return Boolean(draft.name) && Boolean(draft.model) && contract.ready
          ? "complete"
          : "pending";
      case "capabilities":
        return draft.builtinTools.length > 0
          || draft.mcpServers.length > 0
          || draft.subagents.length > 0
          ? "complete"
          : "pending";
      case "behavior":
        return contract.promptSections === 5 ? "complete" : "pending";
      case "trial":
        return activePreview?.preflightResult?.status === "passed" ? "complete" : "pending";
      case "publish":
        return publishedCurrent ? "complete" : "pending";
    }
  };
  const stageStateText: Record<"complete" | "blocked" | "pending", string> = {
    complete: "已完成",
    blocked: "有阻塞",
    pending: "待完成",
  };
  const activeStageBlocked = useMemo(() => {
    const items: Array<{ code: string; message: string; section: StudioSection }> = [];
    for (const issue of serverValidation?.issues ?? []) {
      if (issue.severity !== "error") continue;
      const section = validationIssueSection(issue);
      if (stageForSection(section) !== activeStage) continue;
      items.push({ code: issue.code, message: validationIssueMessage(issue), section });
    }
    if (activeStage === "goal" && !contract.ready) {
      for (const message of contract.issues) {
        items.push({ code: `contract:${message}`, message, section: "identity" });
      }
    }
    return items;
  }, [serverValidation, activeStage, contract]);
  const activeRuntimeCapabilities = options.runtimes;
  const activeRuntimeCapability = activeRuntimeCapabilities.find(
    (item) => item.runtime === draft.runtime,
  ) ?? null;

  useEffect(() => {
    if (!activePreview || !["queued", "provisioning", "cancelling"].includes(activePreview.status)) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      try {
        const refreshed = await studioClient.getPreview(activePreview.previewId);
        if (!active) return;
        setPreviews((current) => [
          refreshed,
          ...current.filter((item) => item.previewId !== refreshed.previewId),
        ]);
      } catch (error) {
        if (active) setNotice(error instanceof Error ? error.message : "Preview 状态读取失败");
      }
    }, 1500);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [activePreview?.previewId, activePreview?.status]);

  useEffect(() => {
    if (!activeEvalRun || !["queued", "running", "cancelling"].includes(activeEvalRun.run.status)) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      try {
        const refreshed = await studioClient.getEvalRun(activeEvalRun.run.evalRunId);
        if (!active) return;
        setEvalRuns((current) => [
          refreshed,
          ...current.filter((item) => item.run.evalRunId !== refreshed.run.evalRunId),
        ]);
      } catch (error) {
        if (active) setNotice(error instanceof Error ? error.message : "Eval 状态读取失败");
      }
    }, 1500);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [activeEvalRun?.run.evalRunId, activeEvalRun?.run.status, activeEvalRun?.cases.length]);

  useEffect(() => {
    if (!draft.publishedVersion) {
      setEvalGate(null);
      return;
    }
    let active = true;
    void studioClient.getEvalGate(draft.name, draft.publishedVersion)
      .then((gate) => { if (active) setEvalGate(gate); })
      .catch(() => { if (active) setEvalGate(null); });
    return () => { active = false; };
  }, [draft.name, draft.publishedVersion, activeEvalRun?.run.status]);

  useEffect(() => {
    if (!draft.id || !draft.name) {
      setEnvironments([]);
      setDeployments([]);
      setDeploymentSnapshots([]);
      return;
    }
    let active = true;
    void Promise.all([
      studioClient.listEnvironments(draft.name),
      studioClient.listDeployments(draft.name),
      studioClient.listDeploymentSnapshots(draft.name),
    ]).then(([nextEnvironments, nextDeployments, nextSnapshots]) => {
      if (!active) return;
      setEnvironments(nextEnvironments);
      setDeployments(nextDeployments);
      setDeploymentSnapshots(nextSnapshots);
    }).catch(() => {
      if (!active) return;
      setEnvironments([]);
      setDeployments([]);
      setDeploymentSnapshots([]);
    });
    return () => { active = false; };
  }, [draft.id, draft.name]);

  useEffect(() => {
    if (!activeDeployment) return;
    const timer = window.setTimeout(() => {
      void refreshDeployments().catch((error) => {
        setNotice(error instanceof Error ? error.message : "部署状态读取失败");
      });
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [activeDeployment?.deployment.deploymentId, activeDeployment?.deployment.status]);

  useEffect(() => {
    if (!draft.id || !draft.name || !draft.publishedVersion) {
      setQualityGate(null);
      return;
    }
    let active = true;
    void studioClient.getQualityGate(draft.name, draft.publishedVersion).then((gate) => {
      if (active) setQualityGate(gate);
    }).catch(() => {
      if (active) setQualityGate(null);
    });
    return () => { active = false; };
  }, [draft.id, draft.name, draft.publishedVersion]);

  if (loading) {
    return <main className={styles.studioStateShell} id="main-content" aria-busy="true"><section className={styles.studioStateCard}><ProductBrandMark className={styles.studioStateMark} /><h1>正在读取{PRODUCT_NAME}</h1><p>正在恢复你的智能体草稿与能力目录。</p></section></main>;
  }
  if (loadError) {
    return <main className={styles.studioStateShell} id="main-content"><section className={styles.studioStateCard} role="alert"><span className={styles.studioStateMark}>!</span><h1>{PRODUCT_NAME}数据暂不可用</h1><p>{loadError}</p><button type="button" onClick={() => window.location.reload()}>重新加载</button></section></main>;
  }

  return (
    <main
      className={`${styles.studioShell} ${styles.workbenchContent}`}
      data-studio-integration="api"
      data-view={viewMode}
      data-build-workspace={viewMode === "editor"}
      data-builder-open={builderAssistantOpen}
    >
      {viewMode === "catalog" ? (
        <section className={styles.agentCatalog} aria-label="智能体目录">
          <header className={styles.agentCatalogHeader}>
            <div className={styles.agentCatalogIntro}>
              <div className={styles.agentCatalogTitleLine}>
                <h1>智能体</h1>
                <span>{drafts.filter((agent) => isAgentVisible(agent, false)).length} 个智能体</span>
              </div>
              <span>选择智能体，继续配置或开始试跑</span>
            </div>
            <div className={styles.agentCatalogActions}>

              <label className={styles.catalogSearch}>
                <span className={styles.visuallyHidden}>搜索智能体</span>
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <circle cx="7" cy="7" r="3.75" />
                  <path d="m10 10 3 3" />
                </svg>
                <input
                  type="search"
                  value={agentQuery}
                  onChange={(event) => setAgentQuery(event.target.value)}
                  placeholder="搜索智能体"
                />
              </label>
              <button
                type="button"
                className={styles.catalogCreateButton}
                disabled={!canEdit || saving}
                onClick={() => void startNewDraft()}
              >
                <span aria-hidden="true">＋</span>
                新建智能体
              </button>
            </div>
          </header>
          <div className={styles.agentCatalogList}>
            {filteredAgentRows.map((agent) => (
              <article
                className={styles.agentCatalogCard}
                key={agent.draftId}
              >
                <button
                  className={styles.agentCatalogOpen}
                  type="button"
                  disabled={saving || Boolean(switchingDraftId)}
                  aria-label={`打开${agent.displayName}`}
                  onPointerEnter={() => {
                    void studioClient.prefetchDraft(agent.draftId, agent.revision).catch(() => {});
                  }}
                  onFocus={() => {
                    void studioClient.prefetchDraft(agent.draftId, agent.revision).catch(() => {});
                  }}
                  onClick={() => void openDraftEditor(agent.draftId)}
                >
                  <span className={styles.agentMonogram} aria-hidden="true">
                    {agent.displayName.slice(0, 1)}
                  </span>
                  <div className={styles.agentCatalogCopy}>
                    <div>
                      <strong>{agent.displayName}</strong>
                      <span className={styles.agentCatalogStatus} data-published={Boolean(agent.publishedVersion)}>
                        {agent.parentDraftId ? "内部子智能体" : agent.publishedVersion ? "已发布" : "草稿"}
                      </span>
                    </div>
                    <span>{agent.domain || "general"} · {agent.template === "operator" ? "执行型" : "分析型"}</span>
                  </div>
                  <div className={styles.agentCatalogPurpose}>
                    <p>{agent.goal || "按已配置的 Prompt 和工具完成任务"}</p>
                    <span title={agent.primaryOutput || undefined}>
                      输出 · {agent.primaryOutput || "可核验的任务结果"}
                    </span>
                  </div>
                  <div className={styles.agentCatalogCapabilities} aria-label="运行能力摘要">
                    <span>{agent.toolCount ?? 0} 项工具</span>
                    <span>{agent.skillCount ? `${agent.skillCount} 个 Skill` : "无需 Skill"}</span>
                    <span>{agent.networkToolsEnabled ? "含联网工具" : "仅内部能力"}</span>
                  </div>
                  <div className={styles.agentCatalogFooter}>
                    <code>{agent.name}@{agent.version}</code>
                    <span className={styles.agentCardAction} aria-hidden="true">
                      {switchingDraftId === agent.draftId ? "…" : "→"}
                    </span>
                  </div>
                </button>
                <details className={`${styles.actionMenu} ${styles.agentCatalogMenu}`} data-dismiss-on-outside>
                  <summary aria-label={`${agent.displayName}的更多操作`} title="更多操作">
                    <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" fill="currentColor"><circle cx="5" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="15" cy="10" r="1.2"/></svg>
                  </summary>
                  <div className={styles.actionMenuPopover}>
                    <button
                      type="button"
                      className={`${styles.actionMenuItem} ${styles.actionMenuDanger}`}
                      data-icon="×"
                      disabled={!canEdit || deleting || saving || Boolean(agent.spaceId)}
                      title={agent.spaceId ? "协作空间智能体需在协作空间中管理" : undefined}
                      onClick={(event) => {
                        event.currentTarget.closest("details")?.removeAttribute("open");
                        void deleteCatalogDraft(agent);
                      }}
                    >
                      <span>
                        <strong>{deleting ? "正在删除智能体" : "删除智能体"}</strong>
                        <small>{agent.spaceId ? "协作空间智能体不能在这里删除" : "保留已有任务与审计记录"}</small>
                      </span>
                    </button>
                  </div>
                </details>
              </article>
            ))}
            {filteredAgentRows.length === 0 && (
              <div className={styles.agentCatalogEmpty}>没有匹配的智能体</div>
            )}
          </div>
        </section>
      ) : (
      <section className={styles.editorShell} data-readonly={!canEdit} data-config-editor={configEditorOpen}>
        <header className={styles.editorHeader}>
          <button
            type="button"
            className={styles.editorBackButton}
            onClick={() => void returnToCatalog()}
          >
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16 10H4m5-6-6 6 6 6" /></svg><span>智能体</span>
          </button>
          <div className={styles.titleBlock}>
            <div className={styles.eyebrow}>
              <span className={styles.draftDot} />
              {publishedCurrent ? "已发布" : "草稿"} (v{draft.version})
              <span className={styles.syncState} data-dirty={dirty} role="status">
                <span aria-hidden="true">·</span>
                {saving
                  ? "保存中"
                  : inspecting
                    ? "检查中"
                    : dirty
                      ? "未保存"
                      : draft.id
                        ? `已保存 r${draft.revision}`
                        : "尚未保存"}
              </span>
            </div>
            <div className={styles.titleLine}>
              <h1>{draft.displayName}</h1>
              <code>{draft.name}@{draft.version}</code>
            </div>
            <p>{draft.description}</p>
            {draft.parentDraftId && <div className={styles.internalNotice}>
              <button type="button" onClick={() => void returnToParent()}>返回父智能体</button><span>内部子智能体 · 归属 {drafts.find((item) => item.draftId === draft.parentDraftId)?.displayName ?? "父智能体"}</span>
              <button type="button" disabled={!canEdit || subagentBusy}
                onClick={() => void changePlacement(draft.id, null)}>转为独立智能体</button>
            </div>}
            {draft.publishedVersion && (
              <div className={styles.publicationBadge} data-current={publishedCurrent}>
                <span>{publishedCurrent ? "不可变版本已发布" : "存在历史发布版本"}</span>
                <code>{draft.name}@{draft.publishedVersion}</code>
                {draft.publishedHash && <code>{draft.publishedHash.slice(0, 12)}</code>}
                {draft.agentId && !draft.spaceId && (
                  <button
                    type="button"
                    ref={versionHistoryTriggerRef}
                    className={styles.versionHistoryButton}
                    aria-expanded={versionHistoryOpen}
                    aria-controls="personal-version-history"
                    onClick={() => {
                    setVersionHistoryOpen(true);
                    }}
                  >
                    版本历史
                    <small>{versionHistoryLoading ? "…" : personalVersions.length}</small>
                  </button>
                )}
              </div>
            )}
          </div>
          <div className={styles.headerActions}>

            <button
              type="button"
              className={`${styles.headerActionButton} ${styles.publishButton}`}
              data-state={serverValidation && !serverValidation.ready ? "blocked" : "ready"}
              disabled={!canEdit || !draft.id || saving || inspecting || publishing}
              onClick={() => void handleReleaseAction()}
              title="保存草稿、检查发布条件并发布当前版本"
            >
              <HeaderActionIcon name="release" />
              <span>{publishing ? "发布中…" : inspecting ? "检查中…" : "发布"}</span>
            </button>
                <input
                  ref={bundleInputRef}
                  hidden
                  type="file"
                  aria-label="导入 Agent 文件"
                  accept=".zip,.rar,application/zip,application/vnd.rar,application/x-rar-compressed"
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0];
                    if (file) void importBundle(file);
                  }}
                />
            <details className={styles.actionMenu} data-dismiss-on-outside>
              <summary
                className={`${styles.headerActionButton} ${styles.iconActionButton}`}
                aria-label="更多智能体操作"
                title="更多操作"
              >
                <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" fill="currentColor"><circle cx="5" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="15" cy="10" r="1.2"/></svg>
              </summary>
              <div className={styles.actionMenuPopover}>
                <header className={styles.actionMenuHeader}>
                  <strong>更多操作</strong>
                  <small>任务 · 导入与导出</small>
                </header>
                {taskHref && (
                  <Link
                    className={styles.actionMenuItem}
                    data-icon="→"
                    href={taskHref}
                    title={`使用当前版本 ${draft.name}@${taskVersion} 开始新任务`}
                  >
                    <span><strong>开始任务</strong><small>使用当前发布版本进入对话</small></span>
                  </Link>
                )}
                <button
                  type="button"
                  className={styles.actionMenuItem}
                  data-icon="↙"
                  disabled={!canEdit || importingBundle}
                  onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    bundleInputRef.current?.click();
                  }}
                >
                  <span><strong>{importingBundle ? "正在导入 Agent" : "导入 Agent / NexAU"}</strong><small>支持 Harness Bundle 与 NexAU ZIP / RAR，导入后进入构建工作台</small></span>
                </button>
                <button
                  type="button"
                  className={styles.actionMenuItem}
                  data-icon="↓"
                  disabled={!draft.id || saving}
                  onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    void downloadBundle();
                  }}
                >
                  <span><strong>下载 Bundle</strong><small>获取当前不可变配置包</small></span>
                </button>
                <button
                  type="button"
                  className={styles.actionMenuItem}
                  data-icon="↗"
                  disabled={!draft.id || saving}
                  onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    void downloadNexauBundle();
                  }}
                >
                  <span><strong>导出 NexAU ZIP</strong><small>生成可导入 NexAU 的 Agent 包</small></span>
                </button>
                <button
                  type="button"
                  className={`${styles.actionMenuItem} ${styles.actionMenuDanger}`}
                  data-icon="×"
                  disabled={!canEdit || !draft.id || deleting || saving || Boolean(draft.spaceId)}
                  title={draft.spaceId ? "协作空间智能体需在协作空间中管理" : undefined}
                  onClick={(event) => {
                    event.currentTarget.closest("details")?.removeAttribute("open");
                    void deleteCurrentDraft();
                  }}
                >
                  <span>
                    <strong>{deleting ? "正在删除智能体" : "删除智能体"}</strong>
                    <small>{draft.spaceId ? "协作空间智能体不能在这里删除" : "保留历史版本、已有任务与审计记录"}</small>
                  </span>
                </button>
              </div>
            </details>
          </div>
        </header>

        {conflict && (
          <div className={styles.conflictBanner} role="alert">
            <div><strong>控制面已有更新</strong><span>本地修改仍保留；加载控制面版本会放弃这些未保存内容。</span></div>
            <button
              type="button"
              disabled={saving || reloadingConflict}
              onClick={() => void reloadAfterConflict()}
            >
              {reloadingConflict ? "正在加载…" : "加载控制面版本"}
            </button>
          </div>
        )}
        {versionConflict && (
          <div className={styles.conflictBanner} role="alert">
            <div>
              <strong>该版本号已存在其他不可变内容</strong>
              <span>已发布版本不能覆盖。请修改版本号、保存并重新检查后再发布。</span>
            </div>
            <button type="button" onClick={() => setActiveSection("identity")}>修改版本号</button>
          </div>
        )}
        {releaseFeedbackOpen && serverValidation && (
          validationErrors.length
          + productionValidationErrors.length
          + validationWarnings.length
          + incompatibleMcpReferences.length > 0
        ) && (
          <aside
            className={styles.releaseFeedbackPopover}
            data-tone={validationErrors.length ? "blocked" : "warning"}
            aria-live="polite"
            aria-label="发布检查结果"
          >
            <header className={styles.releaseFeedbackSummary}>
              <span aria-hidden="true">!</span>
              <div>
                <strong>
                  {validationErrors.length
                    ? `${validationErrors.length} 项配置阻止发布`
                    : productionValidationErrors.length
                      ? `${productionValidationErrors.length} 项生产部署限制`
                      : `${validationWarnings.length} 项上线前提醒`}
                </strong>
                <small>检查结果不会占用编辑区；关闭后可从发布按钮再次打开。</small>
              </div>
              <button
                type="button"
                aria-label="关闭发布检查结果"
                onClick={() => setReleaseFeedbackOpen(false)}
              >
                ×
              </button>
            </header>
            <div className={styles.releaseFeedbackBody}>
              {validationErrors.length + productionValidationErrors.length > 0 && (
                <ul className={styles.releaseIssues}>
                  {[...validationErrors, ...productionValidationErrors].map((issue) => {
                    const section = validationIssueSection(issue);
                    const missingCoverage = missingEvaluationCoverage(issue);
                    const suggestedProfile = options.profiles.find(
                      (profile) => profile.profileId === issue.suggestedProfileIds[0],
                    );
                    return (
                      <li
                        key={`${issue.stage}:${issue.code}:${issue.path ?? ""}`}
                        data-stage={issue.stage}
                      >
                        <span aria-hidden="true">{issue.stage === "production" ? "P" : "!"}</span>
                        <div>
                          <strong>{validationIssueMessage(issue)}</strong>
                          <small>
                            {issue.stage === "production" ? "生产部署" : "发布"}
                            {" · "}{sectionLabels[section]}
                          </small>
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setReleaseFeedbackOpen(false);
                            if (suggestedProfile) {
                              updateDraft({ executionProfile: suggestedProfile.profileId });
                            } else if (missingCoverage) {
                              updateDraft({
                                evalCases: [
                                  ...draft.evalCases,
                                  evaluationCoverageCase(missingCoverage, draft),
                                ],
                              });
                            }
                            setActiveSection(section);
                          }}
                        >
                          {suggestedProfile
                            ? `切换至 ${suggestedProfile.label}`
                            : missingCoverage
                              ? "一键补齐"
                              : "去处理"}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}

              {validationWarnings.length > 0 && (
                <details className={styles.releaseWarnings}>
                  <summary>{validationWarnings.length} 项上线前提醒</summary>
                  <ul>
                    {validationWarnings.map((issue) => (
                      <li key={`${issue.code}:${issue.message}`}>{issue.message}</li>
                    ))}
                  </ul>
                </details>
              )}

              {incompatibleMcpReferences.length > 0 && (
                <div className={styles.releaseQuickFix}>
                  <span>
                    当前执行档位未允许：
                    <code>{incompatibleMcpReferences.join(", ")}</code>
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      updateDraft({
                        mcpServers: draft.mcpServers.filter(
                          (reference) => !incompatibleMcpReferences.includes(reference),
                        ),
                      });
                      setActiveSection("capabilities");
                    }}
                  >
                    移除不兼容 MCP
                  </button>
                </div>
              )}
            </div>
          </aside>
        )}
        {activePreview && (
          <div className={styles.previewBanner} data-status={activePreview.status} data-stale={activePreview.stale}>
            <div className={styles.previewIdentity}>
              <strong>
                {activePreview.stale ? "历史 Preview" : "Preview"} ·{" "}
                {previewStatusLabels[activePreview.status] ?? activePreview.status}
                {activePreview.stale ? "（不影响当前 Draft）" : ""}
              </strong>
              <span>
                测试身份 · Draft r{activePreview.draftRevision} · 到期 {new Date(activePreview.expiresAt).toLocaleString("zh-CN")}
                {activePreview.stale ? ` · 当前 Draft r${draft.revision}` : ""}
              </span>
            </div>
            <div className={styles.previewActions}>
              <button
                type="button"
                disabled={creatingPreview}
                onClick={() => {
                  if (
                    activePreview.stale
                    || ["cancelled", "failed", "expired"].includes(activePreview.status)
                  ) {
                    void createPreview();
                  } else {
                    void refreshPreview(activePreview.previewId);
                  }
                }}
              >
                {creatingPreview
                  ? "正在重新测试…"
                  : activePreview.stale
                    ? `重新测试 Draft r${draft.revision}`
                    : ["cancelled", "failed", "expired"].includes(activePreview.status)
                      ? "重新测试"
                      : "刷新状态"}
              </button>
              {!(["cancelled", "failed", "expired"] as string[]).includes(activePreview.status) && (
                <button type="button" onClick={() => void cancelPreview(activePreview.previewId)}>取消</button>
              )}
            </div>
            {activePreview.stale && (
              <p role="status">
                这是 Draft r{activePreview.draftRevision} 的不可变历史结果；读取刷新不会按当前 Draft 重跑。
              </p>
            )}
            {activePreview.preflightResult && (
              <details className={styles.preflightDisclosure}>
                <summary>
                  <span>
                    真实 Preflight · {activePreview.preflightResult.status}
                    {activePreview.preflightResult.errorCode ? ` · ${activePreview.preflightResult.errorCode}` : ""}
                  </span>
                  <small>
                    {preflightProgress(activePreview.preflightResult.checks)}
                  </small>
                </summary>
                <ol>
                  {activePreview.preflightResult.checks.map((check) => (
                    <li key={check.stage} data-status={check.status}>
                      <span aria-hidden="true">{check.status === "passed" ? "✓" : check.status === "skipped" ? "–" : "!"}</span>
                      <div>
                        <strong>{preflightStageLabels[check.stage]}</strong>
                        <small>{check.summary}{check.errorCode ? ` · ${check.errorCode}` : ""}</small>
                      </div>
                      <code>{check.durationMs}ms</code>
                    </li>
                  ))}
                </ol>
                {activePreview.preflightResult.errorCode && (
                  <p role="alert">
                    {activePreview.stale
                      ? `以下失败属于历史 Draft r${activePreview.draftRevision}，不能代表当前 Draft r${draft.revision}。`
                      : preflightErrorLabels[activePreview.preflightResult.errorCode]
                      ?? "Preflight 未通过。请根据失败阶段检查执行档位、凭据与目标环境。"}
                  </p>
                )}
                {activePreview.preflightResult.artifact && (
                  <p>
                    Artifact · {activePreview.preflightResult.artifact.name} · {activePreview.preflightResult.artifact.sizeBytes} B · {activePreview.preflightResult.artifact.sha256.slice(0, 12)}
                  </p>
                )}
              </details>
            )}
          </div>
        )}
        {!canEdit && (
          <div className={styles.readonlyBanner} role="status">
            当前为只读角色。可以查看配置和下载已通过校验的 Bundle，不能修改或保存草稿。
          </div>
        )}

        <div className={styles.buildWorkspaceMount} ref={setWorkspaceTarget} />
        {configEditorOpen && <button type="button" className={styles.configEditorBackdrop} aria-label="关闭完整配置" onClick={() => setConfigEditorOpen(false)} />}
        <div className={styles.editorBody} hidden={!configEditorOpen}>
          <header className={styles.configEditorHeading}><strong>完整配置 · {draft.displayName}</strong><div><button type="button" disabled={!dirty || saving} onClick={() => void saveDraft()}>保存配置</button><button type="button" aria-label="收起完整配置" onClick={() => setConfigEditorOpen(false)}>×</button></div></header>
          <nav className={styles.stageNav} aria-label="Agent 构建五阶段" hidden>
            {STUDIO_STAGES.map((stage) => {
              const state = stageState(stage.id);
              return (
                <button
                  type="button"
                  key={stage.id}
                  className={activeStage === stage.id ? styles.stageStepActive : styles.stageStep}
                  data-state={state}
                  onClick={() => setActiveSection(stage.sections[0])}
                  aria-current={activeStage === stage.id ? "step" : undefined}
                >
                  <span className={styles.stageIndex} aria-hidden="true">
                    {String(stage.index).padStart(2, "0")}
                  </span>
                  <span className={styles.stageCopy}>
                    <strong>{stage.label}</strong>
                    <small>{stage.hint}</small>
                  </span>
                  <em>{stageStateText[state]}</em>
                </button>
              );
            })}
          </nav>

          <fieldset className={styles.panelViewport} disabled={!canEdit}>
            {false && activeStageMeta.sections.length > 1 && (
              <div className={styles.stageTabs} role="tablist" aria-label={`${activeStageMeta.label}阶段分区`}>
                {activeStageMeta.sections.map((section) => (
                  <button
                    type="button"
                    key={section}
                    role="tab"
                    aria-selected={activeSection === section}
                    className={activeSection === section ? styles.stageTabActive : styles.stageTab}
                    onClick={() => setActiveSection(section)}
                  >
                    <span>{sectionLabels[section]}</span>
                    <small>{sectionSummary(section)}</small>
                  </button>
                ))}
              </div>
            )}
            {false && activeStageBlocked.length > 0 && (
              <div className={styles.stageBlocking} role="alert">
                <header>
                  <strong>{activeStageMeta.label}阶段有 {activeStageBlocked.length} 项阻塞</strong>
                  <small>阻塞不解除，试跑与发布都会被门禁拦下。</small>
                </header>
                <ul>
                  {activeStageBlocked.map((item) => (
                    <li key={item.code}>
                      <span>{item.message}</span>
                      <button type="button" onClick={() => setActiveSection(item.section)}>
                        去 {sectionLabels[item.section]}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {true && (
              <section className={styles.configPanel} aria-labelledby="identity-title">
                <PanelHeading
                  id="identity-title"
                  kicker="基本信息"
                  title="名称、模型和使用范围"
                  description="其余标识与运行默认值由构建助手生成，通常不需要手动填写。"
                />
                <div className={styles.formGrid}>
                  <Field label="显示名称">
                    <input
                      value={draft.displayName}
                      onChange={(event) => updateDraft({ displayName: event.target.value })}
                    />
                  </Field>
                  <Field label="模型" wide>
                    <select
                      value={selectedModelValue}
                      onChange={(event) => {
                        const choice = modelChoices.find((item) => item.value === event.target.value);
                        if (choice) updateDraft({ modelRoute: choice.routeId, model: choice.model });
                      }}
                    >
                      {modelChoices.map((choice) => (
                        <option key={choice.value} value={choice.value}>{choice.label}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="场景说明" wide>
                    <textarea
                      rows={3}
                      value={draft.description}
                      onChange={(event) => updateDraft({ description: event.target.value })}
                    />
                  </Field>
                </div>
              </section>
            )}

            {true && (
              <section className={styles.configPanel} aria-labelledby="prompt-title">
                <PanelHeading
                  id="prompt-title"
                  kicker="行为说明"
                  title="System Prompt"
                  description="描述角色、目标、边界和输出要求；常用工作流通过下方 Skills 绑定。"
                />
                <div
                  className={styles.promptWorkspace}
                  data-focus={promptFocusMode ? "true" : "false"}
                >
                  <aside className={styles.promptOutline} aria-label="System Prompt 结构">
                    <div className={styles.promptOutlineHeading}>
                      <div>
                        <span>Prompt 结构</span>
                        <strong>{contract.promptSections} / 5 完整</strong>
                      </div>
                      <small>选择章节可定位；缺失章节会自动补到文末。</small>
                    </div>
                    <div className={styles.promptChecklist}>
                      {REQUIRED_PROMPT_HEADINGS.map((heading, index) => {
                        const present = draft.systemPrompt.includes(heading);
                        return (
                          <button
                            type="button"
                            key={heading}
                            className={present ? styles.checkPresent : styles.checkMissing}
                            onClick={() => moveToPromptSection(heading)}
                          >
                            <span aria-hidden="true">{present ? "✓" : "+"}</span>
                            <span>
                              <strong>{heading.replace("## ", "")}</strong>
                              <small>{present ? `章节 ${index + 1} · 已包含` : "点击补充"}</small>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <div className={styles.promptBoundaryNote}>
                      <strong>放什么在这里？</strong>
                      <p>角色、目标、证据要求、安全边界和输出格式。</p>
                      <span>易变业务知识和长 SOP 请放入 Skills。</span>
                    </div>
                  </aside>
                  <div className={styles.promptEditorShell}>
                    <div className={styles.promptEditorToolbar}>
                      <div>
                        <span className={styles.promptFileMark} aria-hidden="true">M↓</span>
                        <span>
                          <strong>system.md</strong>
                          <small>{dirty ? "本轮修改尚未保存" : `已保存 · revision ${draft.revision}`}</small>
                        </span>
                      </div>
                      <div className={styles.promptEditorActions}>
                        <button
                          type="button"
                          onClick={() => setPromptFocusMode((current) => !current)}
                          aria-pressed={promptFocusMode}
                        >
                          {promptFocusMode ? "退出专注" : "专注编辑"}
                        </button>
                        <button
                          type="button"
                          onClick={() => void saveDraft()}
                          disabled={!canEdit || saving || !dirty}
                        >
                          {saving ? "保存中…" : "保存"}
                        </button>
                      </div>
                    </div>
                    <label className={styles.promptEditorLabel} htmlFor="system-prompt-editor">
                      Markdown
                      <span>Tab 缩进 · Ctrl / ⌘ S 保存</span>
                    </label>
                    <textarea
                      ref={promptEditorRef}
                      id="system-prompt-editor"
                      className={styles.codeEditor}
                      aria-label="System Prompt"
                      aria-describedby="system-prompt-stats"
                      spellCheck={false}
                      readOnly={!canEdit}
                      value={draft.systemPrompt}
                      onKeyDown={handlePromptEditorKeyDown}
                      onChange={(event) => updateDraft({ systemPrompt: event.target.value })}
                    />
                    <div className={styles.promptEditorFooter} id="system-prompt-stats">
                      <span>{draft.systemPrompt.split("\n").length} 行</span>
                      <span>{draft.systemPrompt.length.toLocaleString("zh-CN")} 字符</span>
                      <span>{new Blob([draft.systemPrompt]).size.toLocaleString("zh-CN")} bytes</span>
                      <span data-state={contract.promptSections === 5 ? "ready" : "missing"}>
                        {contract.promptSections === 5 ? "结构门禁已满足" : `缺少 ${5 - contract.promptSections} 个章节`}
                      </span>
                    </div>
                  </div>
                </div>
              </section>
            )}

            {!draft.parentDraftId && (
              <section className={styles.configPanel} aria-labelledby="collaboration-title">
                <div className={styles.groupHeading}>
                  <div>
                    <h3 id="collaboration-title">协作角色</h3>
                    <p>主智能体负责委派与汇总。内部子智能体在这里管理，不占独立入口。</p>
                  </div>
                  <button type="button" className={styles.addSubagentButton}
                    disabled={!canEdit || saving || subagentBusy || Boolean(draft.spaceId)}
                    title={draft.spaceId ? "协作空间可引用已有智能体" : undefined}
                    onClick={() => setSubagentFormOpen((value) => !value)}>
                    {subagentFormOpen ? "收起" : "＋ 新建子智能体"}
                  </button>
                </div>
                {subagentFormOpen && <div className={styles.subagentCreateForm}>
                  <Field label="子智能体名称">
                    <input value={subagentName} maxLength={100} placeholder="例如：事实核验助手"
                      onChange={(event) => setSubagentName(event.target.value)} />
                  </Field>
                  <Field label="职责与返回要求">
                    <textarea rows={3} value={subagentResponsibility} maxLength={500}
                      placeholder="例如：核验材料中的事实，返回来源、日期和不一致之处"
                      onChange={(event) => setSubagentResponsibility(event.target.value)} />
                  </Field>
                  <button type="button" className={styles.addSubagentButton}
                    disabled={!canEdit || subagentBusy || !subagentName.trim() || subagentResponsibility.trim().length < 2}
                    onClick={() => void createInternalSubagent()}>
                    {subagentBusy ? "创建中…" : "创建并绑定"}
                  </button>
                </div>}
                <div className={styles.subagentBindRow}>
                  <select aria-label="引用已有智能体" value={bindSubagentRef}
                    onChange={(event) => setBindSubagentRef(event.target.value)}>
                    <option value="">选择已有智能体…</option>
                    {subagentCandidates.map((item) => <option key={item.draftId} value={item.ref}>
                      {item.label}{item.parentDraftId ? " · 内部" : " · 独立"}
                    </option>)}
                  </select>
                  <button type="button" disabled={!canEdit || !bindSubagentRef || subagentBusy}
                    onClick={addSubagent}>引用已有</button>
                </div>
                {!draft.subagents.length && <p className={styles.collaborationEmpty}>
                  暂无协作角色。简单任务可以由当前智能体独立完成。
                </p>}
                <div className={styles.subagentEditors}>
                  {draft.subagents.map((subagent, index) => {
                    const child = subagentCandidates.find((item) => item.ref === subagent.ref
                      || item.ref.split("@")[0] === subagent.ref.split("@")[0]);
                    return <article className={styles.subagentEditor} key={index}>
                      <header>
                        <div><span>{child?.parentDraftId ? "内部子智能体" : "引用已有智能体"}</span>
                          <strong>{child?.label ?? subagent.alias}</strong></div>
                        <div className={styles.roleActions}>
                          {child && <button type="button" disabled={saving || subagentBusy}
                            onClick={() => void editSubagentDraft(child.ref)}>编辑配置</button>}
                          <details className={styles.actionMenu} data-dismiss-on-outside>
                            <summary aria-label={`${subagent.alias}的更多操作`}>•••</summary>
                            <div className={styles.actionMenuPopover}>
                              {child && <button type="button" className={styles.actionMenuItem}
                                disabled={!canEdit || subagentBusy || Boolean(draft.spaceId)}
                                onClick={() => void changePlacement(child.draftId, child.parentDraftId ? null : draft.id)}>
                                {child.parentDraftId ? "转为独立智能体" : "移为内部子智能体"}
                              </button>}
                              <button type="button" className={styles.actionMenuItem} disabled={!canEdit}
                                onClick={() => removeSubagent(index)}>解除角色绑定</button>
                            </div>
                          </details>
                        </div>
                      </header>
                      <Field label="职责与返回要求">
                        <textarea rows={3} value={subagent.responsibility} disabled={!canEdit}
                          onChange={(event) => updateSubagent(index, { responsibility: event.target.value })} />
                      </Field>
                      <details className={styles.roleAdvanced}>
                        <summary>角色设置 · {subagent.background ? "允许并行" : "顺序执行"}</summary>
                        <Field label="角色标识">
                          <input value={subagent.alias} disabled={!canEdit}
                            onChange={(event) => updateSubagent(index, { alias: event.target.value })} />
                        </Field>
                        <code>{subagent.ref}</code>
                        <label><input type="checkbox" checked={subagent.background} disabled={!canEdit}
                          onChange={(event) => updateSubagent(index, { background: event.target.checked })} />
                          允许后台并行</label>
                      </details>
                    </article>;
                  })}
                </div>
                {subagentCandidates.filter((item) => item.parentDraftId === draft.id
                  && !draft.subagents.some((binding) => binding.ref.split("@")[0] === item.ref.split("@")[0]))
                  .map((child) => <div className={styles.internalNotice} key={child.draftId}>
                    <span>{child.label} · 尚未绑定角色</span>
                    <button type="button" onClick={() => void editSubagentDraft(child.ref)}>编辑配置</button>
                    <button type="button" disabled={!canEdit || subagentBusy}
                      onClick={() => void changePlacement(child.draftId, null)}>转为独立智能体</button>
                  </div>)}
              </section>
            )}

            {true && (
              <section
                className={styles.configPanel}
                data-compact-skill="true"
                data-has-skill={Boolean(skill)}
                aria-labelledby="skills-title"
              >
                <input
                  ref={skillInputRef}
                  hidden
                  type="file"
                  accept=".zip,.md,application/zip,text/markdown"
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0];
                    if (file) void installSkill(file);
                  }}
                />
                <div className={styles.skillPanelHeading}>
                  <PanelHeading
                    id="skills-title"
                    kicker="行为说明"
                    title="Skills（可选）"
                    description="仅在需要复用工作流、领域规则或脚本时绑定；普通 Agent 可以直接使用 System Prompt 与 Tools。"
                  />
                  <details className={`${styles.actionMenu} ${styles.skillActionsMenu}`} data-dismiss-on-outside>
                    <summary aria-label="更多 Skill 操作" title="更多操作">
                      <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" fill="currentColor"><circle cx="5" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="15" cy="10" r="1.2"/></svg>
                    </summary>
                    <div className={styles.actionMenuPopover}>
                      <header className={styles.actionMenuHeader}>
                        <strong>Skill 操作</strong>
                        <small>创建、导入与管理</small>
                      </header>
                      <button
                        type="button"
                        className={styles.actionMenuItem}
                        data-icon="↙"
                        disabled={!canEdit || importingSkill || saving}
                        onClick={(event) => {
                          event.currentTarget.closest("details")?.removeAttribute("open");
                          skillInputRef.current?.click();
                        }}
                      >
                        <span><strong>{importingSkill ? "正在检查…" : "上传 Skill"}</strong><small>支持 SKILL.md 或 ZIP</small></span>
                      </button>
                      <Link
                        className={styles.actionMenuItem}
                        data-icon="+"
                        href={skillCreatorHref("agent", {
                          agentDraftId: draft.id,
                          agentLabel: draft.displayName,
                        })}
                        aria-label="使用 Skill Creator 创建 Agent Skill"
                      >
                        <span><strong>对话创建</strong><small>打开 Skill Creator</small></span>
                      </Link>
                      <Link
                        className={styles.actionMenuItem}
                        data-icon="↗"
                        href="/studio/skills"
                        aria-label="查看技能目录"
                      >
                        <span><strong>技能目录</strong><small>浏览和管理已有 Skill</small></span>
                      </Link>
                      {skill && (
                        <button
                          type="button"
                          className={`${styles.actionMenuItem} ${styles.actionMenuDanger}`}
                          data-icon="×"
                          disabled={!canEdit || importingSkill || saving}
                          onClick={(event) => {
                            event.currentTarget.closest("details")?.removeAttribute("open");
                            void uninstallSkill(skill.name);
                          }}
                        >
                          <span><strong>卸载当前 Skill</strong><small>从当前草稿移除绑定</small></span>
                        </button>
                      )}
                    </div>
                  </details>
                </div>
                <div className={styles.skillList} role="listbox" aria-label="已安装 Skills">
                  {draft.skills.map((candidate) => (
                    <button
                      key={candidate.name}
                      type="button"
                      role="option"
                      aria-selected={candidate.name === skill?.name}
                      onClick={() => setActiveSkillName(candidate.name)}
                    >
                      <span className={styles.skillRowGlyph} aria-hidden="true">S</span>
                      <span className={styles.skillRowCopy}>
                        <strong>{candidate.name}</strong>
                        <small>{candidate.description || "暂无描述"}</small>
                      </span>
                      <span className={styles.skillRowMeta}>
                        {candidate.fileCount ?? candidate.files?.length ?? 0} 个文件
                      </span>
                    </button>
                  ))}
                </div>
                <InfoStrip tone="neutral">
                  支持单个 SKILL.md 或 ZIP。声明式内容直接安装到当前草稿；脚本和依赖只进入不可变快照，实际执行与安装仍走 Sandbox 权限门。
                </InfoStrip>
                {!skill && (
                  <div className={styles.skillEmpty}>
                    <strong>当前草稿尚未安装 Skill</strong>
                    <span>可上传 SKILL.md 或 ZIP；不安装 Skill 也可以继续配置和发布 Agent。</span>
                  </div>
                )}
                {skill && skillImportReport?.skillName === skill.name
                  && skillImportReport.findings.length > 0 && (
                    <details className={styles.skillImportFindings}>
                      <summary>
                        安装检查明细
                        <span>{skillImportReport.findings.length} 项 · 默认收起</span>
                      </summary>
                      {skillImportReport.warnings.length > 0 && (
                        <p>{skillImportReport.warnings.join("；")}</p>
                      )}
                      <ul>
                        {skillImportReport.findings.map((finding) => (
                          <li key={finding}>{finding}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                {skill && (
                  <>
                <div className={styles.skillHeader}>
                  <span className={styles.skillGlyph} aria-hidden="true">S</span>
                  <div>
                    <strong>{skill.name}</strong>
                    <span>Agent 内置 Skill · 随版本发布</span>
                  </div>
                  <span className={styles.bindingBadge} data-binding="bundle">随版本固化</span>
                  <button
                    type="button"
                    aria-expanded={skillConversationOpen}
                    aria-controls="skill-conversation-builder"
                    disabled={!canEdit}
                    title={canEdit ? "通过多轮模型对话创建或改写 Skill" : "当前角色只有查看权限"}
                    onClick={() => setSkillConversationOpen((current) => !current)}
                  >
                    {skillConversationOpen ? "收起共创" : "对话创建"}
                  </button>
                </div>
                {skillConversationOpen && (
                  <SkillConversationBuilder
                    key={`${draft.id || "unsaved"}:${skill.name}`}
                    agent={{
                      name: draft.name,
                      displayName: draft.displayName,
                      domain: draft.domain,
                      description: draft.description,
                      modelRoute: draft.modelRoute,
                    }}
                    modelLabel={selectedRoute?.label ?? draft.model}
                    currentSkill={skill}
                    onClose={() => setSkillConversationOpen(false)}
                    onApply={(generatedSkill) => {
                      updateSkill(skill.name, generatedSkill);
                      setSkillConversationOpen(false);
                      setNotice(`已应用模型生成的 Skill：${generatedSkill.name} · 尚未保存`);
                    }}
                  />
                )}
                <div className={styles.formGridSingle}>
                  <Field label="Skill 描述">
                    <input
                      value={skill.description}
                      onChange={(event) =>
                        updateSkill(skill.name, {
                          ...skill,
                          description: event.target.value,
                        })
                      }
                    />
                  </Field>
                  <Field label="工作流说明">
                    <textarea
                      rows={10}
                      value={skill.instructions}
                      onChange={(event) =>
                        updateSkill(skill.name, {
                          ...skill,
                          instructions: event.target.value,
                        })
                      }
                    />
                  </Field>
                </div>
                <div className={styles.skillFiles}>
                  <div className={styles.groupHeading}>
                    <div>
                      <h3>Skill 附加文件</h3>
                      <p>风险规则、报告规范等内容会随 Skill 一起进入不可变 Bundle。</p>
                    </div>
                    <span>{skill.fileCount ?? skill.files?.length ?? 0} 个文件</span>
                  </div>
                  {(skill.files ?? []).slice(0, 200).map((file, index) => (
                    <article className={styles.skillFileCard} key={file.path}>
                      <code>{file.path}</code>
                      {file.content !== null && file.content !== undefined ? (
                        <textarea
                          aria-label={`编辑 ${file.path}`}
                          rows={8}
                          value={file.content}
                          onChange={(event) =>
                            updateSkill(skill.name, {
                              ...skill,
                              files: (skill.files ?? []).map((candidate, fileIndex) =>
                                fileIndex === index
                                  ? {
                                      path: candidate.path,
                                      content: event.target.value,
                                    }
                                  : candidate,
                              ),
                            })
                          }
                        />
                      ) : (
                        <div className={styles.skillBinaryFile}>
                          <span>{(file.binary ?? Boolean(file.contentBase64)) ? "BIN" : "REF"}</span>
                          <div>
                            <strong>
                              {(file.binary ?? Boolean(file.contentBase64))
                                ? "二进制 asset"
                                : "服务端保留文件"}
                            </strong>
                            <small>
                              约 {Math.ceil((file.sizeBytes ?? (file.contentBase64?.length ?? 0) * 0.75) / 1024).toLocaleString("zh-CN")} KiB
                              · 随 Skill 完整保存，不在编辑器中加载内容
                            </small>
                          </div>
                        </div>
                      )}
                    </article>
                  ))}
                  {skill.filesTruncated && (
                    <div className={styles.skillFilesEmpty}>
                      当前 Skill 共 {(skill.fileCount ?? 0).toLocaleString("zh-CN")} 个附加文件；
                      为保证编辑器流畅，仅加载前 200 个文件的元数据，其余文件仍完整保存在服务端并随版本发布。
                    </div>
                  )}
                  {(skill.fileCount ?? skill.files?.length ?? 0) === 0 && (
                    <div className={styles.skillFilesEmpty}>
                      当前 Skill 没有 references、scripts 或 assets。
                    </div>
                  )}
                </div>
                  </>
                )}
              </section>
            )}

            {true && (
              <section className={styles.configPanel} aria-labelledby="capabilities-title">
                <div className={styles.skillPanelHeading}>
                <PanelHeading
                  id="capabilities-title"
                  kicker="可用能力"
                  title="Tools 与知识库"
                  description="选择智能体可使用的工具和知识库。"
                />
                  <details className={`${styles.actionMenu} ${styles.skillActionsMenu}`} data-dismiss-on-outside>
                    <summary aria-label="Tools 与知识库的更多操作" title="更多">•••</summary>
                    <div className={styles.actionMenuPopover}>
                      <button type="button" className={styles.actionMenuItem} onClick={(event) => {
                        event.currentTarget.closest("details")?.removeAttribute("open");
                        setShowPythonTools((value) => !value);
                      }}><span><strong>{showPythonTools ? "收起自定义算子" : "自定义算子"}</strong><small>{draft.pythonTools.length} 个 · 创建和管理 Python 算子</small></span></button>
                    </div>
                  </details>
                </div>
                <div className={styles.workerToolPicker} aria-label="公开联网工具">
                  <h3>公开联网</h3>
                  <p>由平台提供搜索和网页读取，无需配置 MCP。勾选后保存并发布生效，同时受个人设置中的联网开关控制。</p>
                  <div className={styles.compactToolGrid}>
                    {options.tools.filter((tool) => ["WebSearch", "WebFetch"].includes(tool.id)).map((tool) => (
                      <label key={tool.id} data-enabled={draft.builtinTools.includes(tool.id)}>
                        <input type="checkbox" aria-label={`${tool.id} · ${tool.label}`} checked={draft.builtinTools.includes(tool.id)}
                          disabled={!canEdit || draft.runtime !== "claude-agent-sdk"}
                          onChange={(event) => updateDraft({builtinTools: event.target.checked
                            ? Array.from(new Set([...draft.builtinTools, tool.id]))
                            : draft.builtinTools.filter((name) => name !== tool.id)})} />
                        <span className={styles.toolCheck} aria-hidden="true">{draft.builtinTools.includes(tool.id) ? "✓" : ""}</span>
                        <strong>{tool.id} · {tool.label}</strong>
                      </label>
                    ))}
                  </div>
                  {draft.runtime !== "claude-agent-sdk" && <p>内置联网当前支持 Claude SDK，其他运行时可使用 MCP。</p>}
                </div>


                <div className={styles.workerToolPicker}>
                  <div className={styles.workerToolPickerHeader}>
                    <label>
                      <span>可用工具</span>
                      <select
                        aria-label="工具范围"
                        value={workerToolPreset}
                        onChange={(event) => {
                          const preset = event.target.value === "readonly"
                            ? WORKER_READONLY_TOOL_IDS
                            : WORKER_STANDARD_TOOL_IDS;
                          const preservedTools = draft.builtinTools.filter(
                            (tool) => ["WebFetch", "WebSearch"].includes(tool)
                              || !workerToolOptions.some((option) => option.id === tool),
                          );
                          updateDraft({
                            builtinTools: Array.from(new Set([
                              ...preservedTools,
                              ...preset.filter((tool) => workerToolOptions.some((option) => option.id === tool)),
                            ])),
                            toolExposureMode: "eager",
                            requiredCapabilities: draft.requiredCapabilities.filter(
                              (item) => item !== "tool_search",
                            ),
                          });
                        }}
                      >
                        <option value="standard">标准工具</option>
                        <option value="readonly">只读工具</option>
                        {workerToolPreset === "custom" && (
                          <option value="custom" disabled>自定义可用工具</option>
                        )}
                      </select>
                    </label>
                    <p>控制这个智能体可调用的工具范围。</p>
                    <span>{enabledWorkerToolIds.length}/{workerToolOptions.length}</span>
                  </div>
                  <div className={styles.workerBoundaryNote}>
                    <i aria-hidden="true" />
                    仅展示可配置工具，调用范围遵循已发布的权限设置。
                  </div>
                  <div className={styles.compactToolGrid}>
                  {workerToolOptions.map((tool) => {
                    const enabled = draft.builtinTools.includes(tool.id);
                    return (
                      <label key={tool.id} data-enabled={enabled}>
                        <input
                          type="checkbox"
                          checked={enabled}
                          onChange={() => {
                            updateDraft({
                              builtinTools: enabled
                                ? draft.builtinTools.filter((item) => item !== tool.id)
                                : [...draft.builtinTools, tool.id],
                              toolExposureMode: "eager",
                              requiredCapabilities: draft.requiredCapabilities.filter(
                                (item) => item !== "tool_search",
                              ),
                            });
                          }}
                        />
                        <span className={styles.toolCheck} aria-hidden="true">{enabled ? "✓" : ""}</span>
                        <strong>{tool.label}</strong>
                        {tool.risk !== "low" && (
                          <i data-risk={tool.risk} title={`${riskLabel(tool.risk)}风险 · ${tool.approval}`} />
                        )}
                      </label>
                    );
                  })}
                  </div>
                </div>

                {showPythonTools && <>
                <div className={styles.groupHeading}>
                  <div>
                    <h3>自定义算子</h3>
                    <p>源码随 Bundle 导入导出，调用时只在隔离 Sandbox 内执行。</p>
                  </div>
                  <button
                    type="button"
                    className={styles.addSubagentButton}
                    onClick={addPythonTool}
                  >
                    + 新建自定义算子
                  </button>
                </div>
                <div className={styles.subagentEditors}>
                  {draft.pythonTools.map((tool, index) => (
                    <article className={styles.subagentEditor} key={`${tool.name}-${index}`}>
                      <header>
                        <div>
                          <span>PY {String(index + 1).padStart(2, "0")}</span>
                          <strong>{tool.name || "未命名算子"}</strong>
                        </div>
                        <span className={styles.bindingBadge} data-binding="bundle">随版本固化</span>
                        <button
                          type="button"
                          onClick={() => removePythonTool(index)}
                          aria-label={`移除 ${tool.name || `自定义算子 ${index + 1}`}`}
                        >
                          移除
                        </button>
                      </header>
                      <div className={styles.formGrid}>
                        <Field label="工具名称" hint="小写字母、数字和下划线">
                          <input
                            className={styles.monoInput}
                            value={tool.name}
                            onChange={(event) =>
                              updatePythonTool(index, { name: event.target.value })
                            }
                          />
                        </Field>
                        <Field label="工具说明">
                          <input
                            value={tool.description}
                            onChange={(event) =>
                              updatePythonTool(index, { description: event.target.value })
                            }
                          />
                        </Field>
                        <div className={styles.pythonToolWorkspace}>
                          <JsonSchemaCodeEditor
                            key={`${tool.name}-schema-${JSON.stringify(tool.inputSchema)}`}
                            value={tool.inputSchema}
                            onCommit={(inputSchema) =>
                              updatePythonTool(index, { inputSchema })
                            }
                            onInvalid={(message) =>
                              setNotice(
                                `Input Schema 不是有效 JSON：${tool.name} · ${message}`,
                              )
                            }
                          />
                          <PythonCodeEditor
                            value={tool.code}
                            onChange={(code) => updatePythonTool(index, { code })}
                          />
                        </div>
                      </div>
                    </article>
                  ))}
                  {draft.pythonTools.length === 0 && (
                    <div className={styles.skillFilesEmpty}>
                      暂无自定义算子。新建后会自动切换为启动时加载，并随 Bundle 保存源码。
                    </div>
                  )}
                </div>

                </>}

                <div className={styles.groupHeading}>
                  <div>
                    <h3>MCP 配置</h3>
                    <p>通过平台注册的逻辑 MCP，不接受任意 URL 或内联密钥。</p>
                  </div>
                  <span>
                    {visibleMcpOptions.filter((item) => draft.mcpServers.includes(item.id)).length} 项已启用
                  </span>
                </div>
                {visibleMcpOptions.filter((item) => item.category !== "knowledge").map((mcp) => {
                  const enabled = draft.mcpServers.includes(mcp.id);
                  return (
                    <label key={mcp.id} className={enabled ? styles.mcpCardEnabled : styles.mcpCard}>
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={() => toggleMcp(mcp.id)}
                      />
                      <span className={styles.mcpSignal} aria-hidden="true"><i /><i /><i /></span>
                      <span className={styles.mcpCopy}>
                        <span className={styles.mcpTitleLine}>
                          <strong>{mcp.label}</strong>
                          <span>只读</span>
                          <span className={styles.bindingBadge} data-binding="runtime">运行时引用 · 凭据托管</span>
                        </span>
                        <small>{mcp.description}</small>
                        <code>{mcp.tools.join(" · ")}</code>
                      </span>
                      <span className={styles.switchVisual} aria-hidden="true"><i /></span>
                    </label>
                  );
                })}
                {false && draft.mcpServers.includes("tavily-readonly") && (
                  <InfoStrip tone="warning">
                    检索词和待抽取 URL 会发送给 Tavily。发布部署前必须从实际 Sandbox 检查凭据、MCP tools/list 与公网可达性；这不会开放任意 Bash 网络访问。
                  </InfoStrip>
                )}

                {visibleMcpOptions.some((item) => item.category === "knowledge") && (
                  <>
                    <div className={styles.groupHeading}>
                      <div>
                        <h3>事实面 · 外部知识库</h3>
                        <p>通过已审核的 MCP 检索工具访问；资料、切片与向量均保留在外部系统。</p>
                      </div>
                      <span>
                        {visibleMcpOptions.filter((item) => item.category === "knowledge" && draft.mcpServers.includes(item.id)).length} 个已绑定
                      </span>
                    </div>
                    {visibleMcpOptions.filter((item) => item.category === "knowledge").map((mcp) => {
                      const enabled = draft.mcpServers.includes(mcp.id);
                      return (
                        <label
                          key={mcp.id}
                          className={enabled ? styles.mcpCardEnabled : styles.mcpCard}
                        >
                          <input
                            type="checkbox"
                            checked={enabled}
                            onChange={() => toggleMcp(mcp.id)}
                          />
                          <span className={styles.mcpSignal} aria-hidden="true">
                            <i /><i /><i />
                          </span>
                          <span className={styles.mcpCopy}>
                            <span className={styles.mcpTitleLine}>
                              <strong>{mcp.label}</strong>
                              <span className={styles.bindingBadge} data-binding="snapshot">运行时引用 · 外部快照</span>
                              <span>{mcp.tools.length} 个工具</span>
                            </span>
                            <small>{mcp.description}</small>
                            <code>{mcp.tools.join(" · ")}</code>
                          </span>
                          <span className={styles.switchVisual} aria-hidden="true"><i /></span>
                        </label>
                      );
                    })}
                  </>
                )}
              </section>
            )}

            {true && (
              <section className={styles.configPanel} aria-labelledby="runtime-title">
                <PanelHeading
                  id="runtime-title"
                  kicker="运行设置"
                  title="执行与权限"
                  description="配置智能体运行时、执行方式和工具权限。"
                />
                <div className={styles.formGridSingle}>
                  <Field label="Agent Runtime" hint="发布后固定到版本 Bundle">
                    <select
                      value={draft.runtime}
                      onChange={(event) => {
                        const runtime = event.target.value as StudioDraft["runtime"];
                        const targetCapability = activeRuntimeCapabilities.find(
                          (item) => item.runtime === runtime,
                        );
                        const compatibleRoute = options.routes.find((route) =>
                          targetCapability
                            ? targetCapability.modelApiFormats.includes(
                                route.apiFormat ?? "anthropic_compatible",
                              )
                            : runtime !== "codex-app-server",
                        );
                        const currentRouteCompatible = selectedRoute
                          ? targetCapability
                            ? targetCapability.modelApiFormats.includes(
                                selectedRoute.apiFormat ?? "anthropic_compatible",
                              )
                            : runtime !== "codex-app-server"
                          : false;
                        updateDraft({
                          runtime,
                          ...(currentRouteCompatible || !compatibleRoute
                            ? {}
                            : {
                                modelRoute: compatibleRoute.id,
                                model: compatibleRoute.models[0],
                              }),
                        });
                      }}
                    >
                      {(activeRuntimeCapabilities.length > 0
                        ? activeRuntimeCapabilities
                        : [
                            {
                              runtime: "claude-agent-sdk" as const,
                              label: "Claude Agent SDK",
                              stability: "stable" as const,
                              capabilities: [],
                              modelApiFormats: ["anthropic_compatible" as const],
                              limitations: [],
                            },
                            {
                              runtime: "codex-app-server" as const,
                              label: "Codex App Server",
                              stability: "preview" as const,
                              capabilities: [],
                              modelApiFormats: ["openai_compatible" as const],
                              limitations: [],
                            },
                          ]
                      ).map((runtimeCapability) => (
                        <option key={runtimeCapability.runtime} value={runtimeCapability.runtime}>
                          {runtimeCapability.label}
                          {runtimeCapability.stability !== "stable"
                            ? ` · ${runtimeCapability.stability === "preview" ? "预览" : "实验"}`
                            : ""}
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <details className={styles.advancedRuntimeSettings}>
                  <summary>
                    <span>
                      <strong>高级运行设置</strong>
                      <small>执行档位、权限、资源上限、会话恢复与治理</small>
                    </span>
                    <i aria-hidden="true">⌄</i>
                  </summary>
                  <div className={styles.advancedRuntimeBody}>
                {activeRuntimeCapability && activeRuntimeCapability.limitations.length > 0 && (
                  <InfoStrip tone="warning">
                    <strong>{activeRuntimeCapability.label}当前限制：</strong>
                    <ul>
                      {activeRuntimeCapability.limitations.map((limitation) => (
                        <li key={limitation}>{limitation}</li>
                      ))}
                    </ul>
                  </InfoStrip>
                )}
                <div className={styles.runtimeRecommendation}>
                  <span>当前场景推荐</span>
                  <div>
                    <strong>{recommendedRuntime.label}</strong>
                    <p>{recommendedRuntime.description}</p>
                    <small>
                      {recommendedRuntime.policy} · 最多 {recommendedRuntime.maxTurns} 轮
                      {draft.runtime === "codex-app-server" ? ` · ${recommendedRuntime.maxToolCalls} 次工具调用` : ""}
                      {" · 无硬超时、预算或 Token 中止"}
                    </small>
                  </div>
                  <button
                    type="button"
                    disabled={!canEdit || recommendationApplied}
                    onClick={() =>
                      updateDraft({
                        policy: recommendedRuntime.policy,
                        maxTurns: recommendedRuntime.maxTurns,
                        maxToolCalls: recommendedRuntime.maxToolCalls,
                        timeoutSeconds: null,
                        maxBudgetUsd: null,
                        maxModelTokens: null,
                        restoreSession: true,
                        archiveOnComplete: true,
                      })
                    }
                  >
                    {recommendationApplied ? "已采用" : "应用推荐配置"}
                  </button>
                </div>
                <div className={styles.isolationCard}>
                  <span className={styles.isolationGlyph} aria-hidden="true"><i /><i /></span>
                  <div>
                    <strong>Docker 容器工作区 · 平台托管</strong>
                    <p>文件和命令在 Worker 容器内执行，租户、会话、产物和策略边界保持独立。</p>
                  </div>
                  <span className={styles.lockedBadge}>当前环境</span>
                </div>
                <div className={styles.runtimeAssurances}>
                  <article className={styles.identityBoundary}>
                    <header>
                      <div><span>IDENTITY</span><strong>独立工作负载身份</strong></div>
                      <em>发布时生成</em>
                    </header>
                    <dl>
                      <div><dt>主体</dt><dd>agent:{draft.name}@{draft.version}</dd></div>
                      <div><dt>入站</dt><dd>继承已认证用户与租户上下文</dd></div>
                      <div><dt>出站</dt><dd>按 Tool / MCP 单独注入运行时凭据</dd></div>
                    </dl>
                  </article>
                  <article className={styles.continuityBoundary}>
                    <header>
                      <div><span>CONTINUITY</span><strong>恢复与归档语义</strong></div>
                      <em>显式配置</em>
                    </header>
                    <label>
                      <input
                        type="checkbox"
                        checked={draft.restoreSession}
                        onChange={(event) => updateDraft({ restoreSession: event.target.checked })}
                      />
                      <span>恢复同一会话的运行时线程上下文</span>
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={draft.archiveOnComplete}
                        onChange={(event) => updateDraft({ archiveOnComplete: event.target.checked })}
                      />
                      <span>运行结束后归档沙箱工作区</span>
                    </label>
                    <p>当前保障会话与审批恢复；不宣称支持任意工具步骤的持久化 checkpoint。</p>
                  </article>
                </div>
                <div className={styles.formGrid}>
                  <Field label="Execution Profile" hint="平台托管 · 版本固定">
                    <select
                      value={draft.executionProfile}
                      onChange={(event) => updateDraft({ executionProfile: event.target.value })}
                    >
                      {options.profiles.map((profile) => (
                        <option key={`${profile.profileId}@${profile.version}`} value={profile.profileId}>
                          {profile.label} · v{profile.version} · {profile.sandboxProvider}
                          {profile.productionAllowed ? "" : " · 仅 Preview"}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {options.profiles.find((profile) => profile.profileId === draft.executionProfile) && (
                    <div className={styles.profileFacts}>
                      {(() => {
                        const profile = options.profiles.find(
                          (item) => item.profileId === draft.executionProfile,
                        );
                        if (!profile) return null;
                        return <>
                          <span>{profile.cpuMillis}m CPU</span>
                          <span>{profile.memoryMiB} MiB 内存</span>
                          <span>{profile.diskMiB} MiB 磁盘</span>
                          <span>TTL {profile.ttlSeconds}s</span>
                          <span>{profile.networkPolicyId}</span>
                          {!profile.productionAllowed && <span>禁止生产发布</span>}
                        </>;
                      })()}
                    </div>
                  )}
                  <div
                    className={styles.executionProfileAdvisor}
                    data-state={
                      selectedProfileSupportsMcp && selectedExecutionProfile?.productionAllowed
                        ? "ready"
                        : recommendedExecutionProfile
                          ? "recommend"
                          : "blocked"
                    }
                  >
                    <i aria-hidden="true" />
                    <div>
                      <span>PROFILE COMPATIBILITY</span>
                      <strong>
                        {!selectedProfileSupportsMcp
                          ? `当前档位不兼容 ${incompatibleMcpReferences.join("、") || "已选能力"}`
                          : !selectedExecutionProfile?.productionAllowed
                            ? "当前档位仅限 Preview"
                            : "当前档位兼容，具备生产资格"}
                      </strong>
                      <small>
                        {productionExecutionProfiles.length
                          ? `兼容生产档位：${productionExecutionProfiles.map((profile) => profile.label).join("、")}`
                          : compatibleExecutionProfiles.length
                            ? `仅 Preview 可用：${compatibleExecutionProfiles.map((profile) => profile.label).join("、")}`
                            : "没有档位能同时满足当前 MCP；请移除不兼容能力或让管理员更新 Egress 授权。"}
                      </small>
                    </div>
                    {recommendedExecutionProfile
                      && recommendedExecutionProfile.profileId !== draft.executionProfile ? (
                        <button
                          type="button"
                          disabled={!canEdit || saving || inspecting}
                          onClick={() => void applyRecommendedExecutionProfile(
                            recommendedExecutionProfile.profileId,
                          )}
                        >
                          {saving || inspecting
                            ? "切换并检查中…"
                            : `切换、保存并检查 ${recommendedExecutionProfile.label}`}
                        </button>
                      ) : !recommendedExecutionProfile ? (
                        <button
                          type="button"
                          onClick={() => setActiveSection("capabilities")}
                        >
                          调整 MCP
                        </button>
                      ) : null}
                  </div>
                  <Field label="权限 Profile" wide>
                    <select
                      value={draft.policy}
                      onChange={(event) => updateDraft({ policy: event.target.value })}
                    >
                      {policyOptions.map((policy) => (
                        <option key={policy.id} value={policy.id}>
                          {policy.label} · {policy.description}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <div className={styles.permissionCoverage}>
                    <i aria-hidden="true" />
                    <div>
                      <strong>
                        当前 Agent 声明 {draft.builtinTools.length + selectedMcpTools.length} 个工具
                      </strong>
                      <span>
                        {draft.policy === "production-read-only"
                          ? `只读 Profile 覆盖工作区读取和 ${selectedMcpTools.length} 个已审核 MCP 工具；其他调用默认拒绝。`
                          : `${draft.policy} 将按已发布规则逐项判定；未匹配规则默认拒绝。`}
                      </span>
                    </div>
                  </div>
                  <Field label="Agent 最大轮次" hint="建议 64；留空表示不限制">
                    <input
                      type="number"
                      min={1}
                      value={draft.maxTurns ?? ""}
                      placeholder="不限制"
                      onChange={(event) => updateDraft({
                        maxTurns: event.target.value ? Number(event.target.value) : null,
                      })}
                    />
                  </Field>
                  {draft.runtime === "codex-app-server" && (
                    <Field
                      label="Codex 工具调用上限"
                      hint="与模型轮次独立；长程编排建议 256–512"
                    >
                      <input
                        type="number"
                        min={1}
                        max={4096}
                        value={draft.maxToolCalls ?? ""}
                        placeholder="不限制"
                        onChange={(event) => updateDraft({
                          maxToolCalls: event.target.value ? Number(event.target.value) : null,
                        })}
                      />
                    </Field>
                  )}
                  <Field label="硬超时（秒）" hint="留空表示不以时长中止">
                    <input
                      type="number"
                      min={1}
                      value={draft.timeoutSeconds ?? ""}
                      placeholder="不限制"
                      onChange={(event) => updateDraft({
                        timeoutSeconds: event.target.value ? Number(event.target.value) : null,
                      })}
                    />
                  </Field>
                  <Field label="可绑定 Sub 上限">
                    <input
                      type="number"
                      min={1}
                      max={32}
                      value={draft.maxSubagents}
                      onChange={(event) => updateDraft({ maxSubagents: Number(event.target.value) })}
                    />
                  </Field>
                  <Field label="单 Run 子任务上限">
                    <input
                      type="number"
                      min={1}
                      max={128}
                      value={draft.maxSubagentTasks}
                      onChange={(event) => updateDraft({ maxSubagentTasks: Number(event.target.value) })}
                    />
                  </Field>
                  <Field label="Sub 并发上限">
                    <input
                      type="number"
                      min={1}
                      max={16}
                      value={draft.maxConcurrentSubagents}
                      onChange={(event) => updateDraft({ maxConcurrentSubagents: Number(event.target.value) })}
                    />
                  </Field>
                  <p className={styles.fieldHint}>
                    每个 Sub 使用独立会话，只把结果返回 Lead。根 Agent 默认最多 64 轮，不设置时长、模型 Token 或费用硬中止；委派深度固定为 1，隔离与权限边界仍然生效。
                  </p>
                </div>
                <GovernanceControlPlane
                  agentName={draft.name}
                  policyId={draft.policy}
                  mcpReferences={draft.mcpServers}
                  mcpTools={selectedMcpTools}
                  canManage={canPublish}
                  policies={governedPolicies}
                  onPoliciesChanged={setGovernedPolicies}
                />
                  </div>
                </details>
              </section>
            )}

            {activeSection === "trial" && (
              <section className={styles.configPanel} aria-labelledby="trial-title">
                <PanelHeading
                  id="trial-title"
                  kicker="阶段 4 · 试跑"
                  title="在隔离环境证明它真的能跑"
                  description="结构门禁通过后创建临时 Preview；真实 Preflight 与首个满足输出要求的试跑都发生在这里。"
                />
                <div className={styles.trialSummary}>
                  <div>
                    <span>真实 Preflight</span>
                    <strong data-state={activePreview?.preflightResult?.status === "passed" ? "ready" : "pending"}>
                      {activePreview?.preflightResult
                        ? preflightProgress(activePreview.preflightResult.checks)
                        : "尚未运行"}
                    </strong>
                    <small>
                      {activePreview?.preflightResult?.status === "passed"
                        ? "模型 / MCP / Sandbox 全部通过"
                        : "创建 Preview 后自动执行真实预检"}
                    </small>
                  </div>
                  <div>
                    <span>隔离试跑环境</span>
                    <strong>
                      {activePreview && !activePreview.stale
                        ? previewStatusLabels[activePreview.status] ?? activePreview.status
                        : "无活跃 Preview"}
                    </strong>
                    <small>TTL 60 分钟 · 测试身份 · 失败不污染正式版本</small>
                  </div>
                  <div>
                    <span>基础评测基线</span>
                    <strong>{draft.evaluationEnabled ? `${draft.evalCases.length} 用例` : "已关闭"}</strong>
                    <small>
                      {draft.evaluationEnabled
                        ? "happy / ambiguous / safety 参与发布检查"
                        : "开启 Eval 后参与发布门禁"}
                    </small>
                  </div>
                </div>
                <div className={styles.trialActions}>
                  <button
                    type="button"
                    className={styles.trialPrimary}
                    disabled={!draft.id || creatingPreview || saving || inspecting || dirty}
                    onClick={() => void createPreview()}
                  >
                    {creatingPreview
                      ? "正在创建…"
                      : activePreview && !activePreview.stale
                        ? "重建隔离环境"
                        : "创建隔离试跑环境"}
                  </button>
                  <button
                    type="button"
                    disabled={!draft.id || dirty}
                    onClick={() => void openTryRun()}
                  >
                    开始对话试跑
                  </button>
                  {dirty && <small>有未保存修改；保存并检查后才能创建 Preview 或试跑。</small>}
                </div>
                <InfoStrip tone="neutral">
                  出口条件：真实 Preflight 通过，并完成首个满足输出要求的试跑。失败结果只属于当前 Draft revision，不会进入正式版本。
                </InfoStrip>
              </section>
            )}

            {activeSection === "evaluation" && (
              <section className={styles.configPanel} aria-labelledby="evaluation-title">
                <PanelHeading
                  id="evaluation-title"
                  kicker="阶段 5 · 发布"
                  title="用真实失败路径证明它可以发布"
                  description="结构检查只是第一层；上线前仍要在固定版本和真实 Sandbox 中跑 live eval。"
                />
                <div className={styles.publishChain} aria-label="发布链状态">
                  <div className={styles.publishChainSummary}>
                    <span>发布状态</span>
                    <strong>{lifecycleLabel}</strong>
                    <small>{lifecycleDetail}</small>
                  </div>
                  <ol>
                    {lifecycleStages.map((stage, index) => {
                      const state = index < activeLifecycleIndex
                        ? "complete"
                        : index === activeLifecycleIndex
                          ? "active"
                          : "pending";
                      return (
                        <li key={stage.id} data-state={state}>
                          <i aria-hidden="true" />
                          <span>{stage.label}</span>
                        </li>
                      );
                    })}
                  </ol>
                </div>
                {taskHref && (
                  <div className={styles.releaseTaskShortcut}>
                    <div>
                      <strong>用已发布版本验证真实任务</strong>
                      <span>{draft.name}@{draft.publishedVersion}</span>
                    </div>
                    <Link href={taskHref}>开始任务</Link>
                  </div>
                )}
                <div className={styles.evalMode}>
                  <div>
                    <strong>Agent Eval</strong>
                    <span>
                      {draft.evaluationEnabled
                        ? "已启用：基础覆盖参与发布检查，可固化 Dataset 并运行版本评测。"
                        : "已关闭：此 Agent 不执行 Eval，也不会被 Eval 覆盖或 Dataset 门禁阻断。"}
                    </span>
                  </div>
                  <label>
                    <input
                      type="checkbox"
                      checked={draft.evaluationEnabled}
                      disabled={!canEdit}
                      onChange={(event) =>
                        updateDraft({ evaluationEnabled: event.target.checked })
                      }
                    />
                    <span aria-hidden="true"><i /></span>
                    <em>{draft.evaluationEnabled ? "开启" : "关闭"}</em>
                  </label>
                </div>
                {draft.evaluationEnabled ? (
                  <div className={styles.evalDatasetSummary}>
                    <div>
                      <strong>{draft.evalCases.length} 条草稿基础场景</strong>
                      <span>Builder 只维护随 Agent 版本化的 happy / ambiguous / safety 基线。</span>
                    </div>
                    {(["happy", "ambiguous", "safety"] as const).map((tag) => (
                      <span key={tag}>{evaluationCoverageLabels[tag]} {draft.evalCases.filter((item) => item.tag === tag).length}</span>
                    ))}
                    <Link
                      className={styles.publishOperationsButton}
                      href={`/studio/agents/${encodeURIComponent(draft.name)}/operations?draft=${encodeURIComponent(draft.id)}`}
                    >
                      打开 Evaluate &amp; Operate
                    </Link>
                  </div>
                ) : (
                  <div className={styles.evalDisabled}>
                    <strong>Eval 已对当前 Agent 关闭</strong>
                    <span>现有用例配置会保留，重新开启后继续使用，不会删除历史 Dataset 或运行记录。</span>
                  </div>
                )}
                <div className={styles.releaseGate}>
                  <div>
                    <span>本地结构门禁</span>
                    <strong>{contract.ready ? "可生成发布包" : "存在阻塞问题"}</strong>
                  </div>
                  <div>
                    <span>真实环境预检</span>
                    <strong>{activePreview?.preflightResult?.status === "passed" ? "Model / MCP / Sandbox 已通过" : "尚未取得真实 Preflight 证明"}</strong>
                  </div>
                  <div>
                    <span>固定版本轨迹评测</span>
                    <strong>
                      {evalGate
                        ? `${evalGate.passedDatasets}/${evalGate.requiredDatasets} 必测 Dataset 通过`
                        : `${draft.evalCases.length} 用例待固化`}
                    </strong>
                  </div>
                  <div>
                    <span>运行质量门禁</span>
                    <strong>
                      {qualityGate
                        ? qualityGate.passed
                          ? "无阻断问题"
                          : `${qualityGate.blockingIncidentIds.length} 项质量问题阻断发布`
                        : draft.publishedVersion
                          ? "等待已发布版本样本"
                          : "发布版本后生效"}
                    </strong>
                  </div>
                </div>
                <div className={styles.releaseArchitecture} aria-label="发布架构">
                  <article>
                    <span>PREVIEW</span>
                    <strong>临时隔离环境</strong>
                    <p>结构检查通过后创建短时试跑环境；失败不污染任何正式版本。</p>
                    <code>TTL 60 min · 真实 Preflight</code>
                  </article>
                  <article>
                    <span>VERSION</span>
                    <strong>不可变 Bundle</strong>
                    <p>Prompt、Skills、Tools、Sub Agent 固定引用和策略一次性快照。</p>
                    <code>{draft.name}@{draft.version}</code>
                  </article>
                  <article>
                    <span>ENVIRONMENT</span>
                    <strong>按环境晋级</strong>
                    <p>测试、灰度、生产只切换版本指针；保留历史以支持快速回退。</p>
                    <code>test → canary → production</code>
                  </article>
                </div>
                <section className={styles.deploymentControlPlane} aria-label="运行控制面摘要">
                  <header><div><span>EVALUATE &amp; OPERATE</span><strong>运行配置已从 Builder 分离</strong><small>{latestDataset ? `Dataset ${latestDataset.name} v${latestDataset.version}` : "无耐久 Dataset"} · {agentEvalRuns.length} 次 Eval · {environments.length} 个环境 · {deployments.length} 次部署</small></div></header>
                  <Link
                    className={styles.operationsFooterLink}
                    href={`/studio/agents/${encodeURIComponent(draft.name)}/operations?draft=${encodeURIComponent(draft.id)}`}
                  >
                    <span>进入运行控制面</span>
                    <small>Dataset · Eval · 环境 · 部署</small>
                    <i aria-hidden="true">→</i>
                  </Link>
                </section>
              </section>
            )}
          </fieldset>
        </div>

        <footer className={styles.editorFooter}>
          <span className={conflict || versionConflict || notice.includes("阻塞") ? styles.noticeError : styles.noticeDot} aria-hidden="true" />
          <span title={notice}>{notice}</span>
          <code>{draft.id ? `revision ${draft.revision}` : "unsaved"}</code>
        </footer>
      </section>
      )}

      {importFeedback && <div className={styles.importFeedback} role={importFeedback.error ? "alert" : "status"} data-error={Boolean(importFeedback.error)}><span>{importFeedback.message}</span>{!importingBundle && <button type="button" aria-label="关闭导入提示" onClick={()=>setImportFeedback(null)}>×</button>}</div>}
      {versionHistoryOpen && (
        <button
          type="button"
          className={styles.contractBackdrop}
          aria-label="关闭版本历史"
          onClick={() => {
            setVersionHistoryOpen(false);
            setPromoteTarget("");
          }}
        />
      )}
      <aside
        ref={versionHistoryRailRef}
        id="personal-version-history"
        className={`${styles.contractRail} ${styles.versionHistoryRail}`}
        aria-label="个人智能体版本历史"
        role="dialog"
        aria-modal="true"
        aria-hidden={!versionHistoryOpen}
        data-open={versionHistoryOpen}
      >
        <div className={styles.contractHeader}>
          <div>
            <span>IMMUTABLE RELEASES</span>
            <strong>版本历史</strong>
          </div>
          <div className={styles.contractHeaderActions}>
            <span className={styles.riskBadge}>
              {personalVersions.length} 个版本
            </span>
            <button
              type="button"
              ref={versionHistoryCloseRef}
              aria-label="关闭版本历史"
              onClick={() => {
                setVersionHistoryOpen(false);
                setPromoteTarget("");
              }}
            >
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <path d="m4.5 4.5 7 7m0-7-7 7" />
              </svg>
            </button>
          </div>
        </div>

        <section className={styles.versionHistoryIntro}>
          <span>当前运行指针</span>
          <strong>{draft.name}@{currentPersonalVersion ?? "尚未发布"}</strong>
          <p>切换只影响之后创建的任务。历史版本、已有任务和运行中的 Session 保持原绑定。</p>
        </section>

        {versionHistoryLoading && (
          <div className={styles.versionHistoryState} role="status">
            正在读取不可变版本…
          </div>
        )}
        {versionHistoryError && (
          <div className={styles.versionHistoryError} role="alert">
            <strong>版本历史暂时不可用</strong>
            <span>{versionHistoryError}</span>
            {draft.agentId && (
              <button
                type="button"
                onClick={() => {
                  setVersionHistoryError("");
                  setVersionHistoryLoading(true);
                  void studioClient.listPersonalAgentVersions(draft.agentId as string)
                    .then(setPersonalVersions)
                    .catch((error: unknown) => setVersionHistoryError(
                      error instanceof Error ? error.message : "版本历史暂时不可用",
                    ))
                    .finally(() => setVersionHistoryLoading(false));
                }}
              >
                重新加载
              </button>
            )}
          </div>
        )}

        {!versionHistoryLoading && !versionHistoryError && (
          <ol className={styles.versionTimeline}>
            {personalVersions.map((item, index) => {
              const current = item.version === item.current_version;
              const confirming = promoteTarget === item.version;
              return (
                <li key={item.version} data-current={current}>
                  <span className={styles.versionSequence} aria-hidden="true">
                    {String(personalVersions.length - index).padStart(2, "0")}
                  </span>
                  <article>
                    <header>
                      <div>
                        <strong>{item.version}</strong>
                        {current && <em>当前</em>}
                      </div>
                      <time dateTime={item.created_at}>
                        {new Date(item.created_at).toLocaleString("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </time>
                    </header>
                    <dl>
                      <div><dt>内容</dt><dd>{item.manifest_hash.slice(0, 12)}</dd></div>
                      <div><dt>Bundle</dt><dd>{item.package_hash?.slice(0, 12) ?? "未记录"}</dd></div>
                    </dl>
                    {current ? (
                      <p className={styles.versionCurrentNote}>新任务默认使用这个版本</p>
                    ) : confirming ? (
                      <div className={styles.versionPromoteConfirm} role="group" aria-label={`确认切换到 ${item.version}`}>
                        <p>将新任务切换到 {item.version}？已有任务不会改变。</p>
                        <div>
                          <button
                            type="button"
                            disabled={Boolean(promotingVersion)}
                            onClick={() => void promotePersonalVersion(item.version)}
                          >
                            {promotingVersion === item.version ? "切换中…" : "确认切换"}
                          </button>
                          <button
                            type="button"
                            disabled={Boolean(promotingVersion)}
                            onClick={() => setPromoteTarget("")}
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className={styles.versionPromoteButton}
                        disabled={!canPublish || Boolean(promotingVersion)}
                        onClick={() => setPromoteTarget(item.version)}
                      >
                        设为当前版本
                      </button>
                    )}
                  </article>
                </li>
              );
            })}
            {personalVersions.length === 0 && (
              <li className={styles.versionHistoryEmpty}>还没有可切换的发布版本。</li>
            )}
          </ol>
        )}

        <footer className={styles.versionHistoryFootnote}>
          回退是移动当前指针，不会修改或删除任何不可变版本。
        </footer>
      </aside>
      <AgentBuilderAssistant
        workspaceTarget={workspaceTarget}
        testRequest={testRequest}
        onEditConfiguration={(section, label) => openConfiguration(section ?? "identity", label)}
        open={viewMode === "editor" && Boolean(workspaceTarget)}
        mode={builderAssistantMode}
        creationSession={builderCreationSession}
        draft={draft}
        initialPrompt={tryRunSeed.prompt}
        recommendation={tryRunSeed.recommendation}
        prepareDraft={() => dirty ? saveDraft() : Promise.resolve(draft)}
        hasUnsavedChanges={dirty}
        onUpdated={(updated) => {
          setDraft(updated);
          setDirty(false);
          setConflict(false);
          setServerValidation(null);
          setNotice(`已通过对话更新 ${updated.displayName}，尚未发布`);
          void studioClient.listAccessibleDrafts().then(setDrafts).catch(() => {
            setNotice("草稿已保存，列表刷新失败；可稍后刷新列表");
          });
        }}
        knowledgeMcpReferences={options.mcp.filter((item) => item.category === "knowledge").map((item) => item.id)}
        onClose={() => setBuilderAssistantOpen(false)}
        onCreated={(flow) => {
          const created = flow.draft;
          setBuilderAssistantMode("run");
          setDraft(created);
          setDrafts((current) => [
            {
              draftId: created.id,
              agentId: created.agentId,
              spaceId: created.spaceId,
              name: created.name,
              displayName: created.displayName,
              domain: created.domain,
              version: created.version,
              template: created.template,
              goal: created.taskContract?.goal || created.description,
              primaryOutput: created.taskContract?.outputs[0] || "按 System Prompt 生成可核验结果",
              primaryConstraint: created.taskContract?.constraints[0] || null,
              skillCount: created.skills.length,
              toolCount: created.builtinTools.length + created.pythonTools.length + created.mcpServers.length,
              networkToolsEnabled: created.builtinTools.some((tool) => ["WebSearch", "WebFetch"].includes(tool)),
              revision: created.revision,
              updatedAt: new Date().toISOString(),
              publishedVersion: created.publishedVersion,
            },
            ...current,
          ]);
          setDirty(false);
          setServerValidation(null);
          if (workspaceVisibleRef.current) setViewMode("editor");
          setActiveSection("identity");
          setTryRunSeed({
            prompt: flow.prompt,
            autoStart: flow.autoRun,
            recommendation: flow.recommendation,
          });
          setBuilderAssistantOpen(true);
          setNotice(`已创建 ${created.displayName}；可继续修改配置，或在右侧输入测试问题`);
        }}
      />
      {confirmationDialog}
    </main>
  );
}

function PanelHeading({
  id,
  kicker,
  title,
  description,
}: {
  id: string;
  kicker: string;
  title: string;
  description: string;
}) {
  return (
    <header className={styles.panelHeading}>
      <span>{kicker}</span>
      <h2 id={id}>{title}</h2>
      <p>{description}</p>
    </header>
  );
}

function PythonCodeEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const exportsRun = /^\s*def\s+run\s*\(\s*arguments\s*\)/m.test(value);

  return (
    <StudioCodeEditor
      ariaLabel="Python 源码"
      filename="tool.py"
      language="python"
      runtimeLabel="Python 3.12 · Sandbox"
      status={exportsRun ? "run(arguments) 已识别" : "缺少 run(arguments)"}
      statusTone={exportsRun ? "ready" : "error"}
      value={value}
      onChange={onChange}
    />
  );
}

function validateJsonSchema(source: string): {
  parsed?: Record<string, unknown>;
  status: string;
  tone: "ready" | "error";
} {
  try {
    const parsed = JSON.parse(source) as unknown;
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      return { status: "根节点必须是对象", tone: "error" };
    }
    return {
      parsed: parsed as Record<string, unknown>,
      status: "JSON 有效",
      tone: "ready",
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "JSON 语法错误";
    return {
      status: message.replace(/^JSON\.parse:\s*/i, "").slice(0, 72),
      tone: "error",
    };
  }
}

function JsonSchemaCodeEditor({
  value,
  onCommit,
  onInvalid,
}: {
  value: Record<string, unknown>;
  onCommit: (value: Record<string, unknown>) => void;
  onInvalid: (message: string) => void;
}) {
  const [source, setSource] = useState(() => JSON.stringify(value, null, 2));
  const validation = useMemo(() => validateJsonSchema(source), [source]);
  const fieldCount = Object.keys(
    (value.properties as object | undefined) ?? {},
  ).length;

  function commit(next: string) {
    const result = validateJsonSchema(next);
    if (result.parsed) {
      onCommit(result.parsed);
      return;
    }
    onInvalid(result.status);
  }

  return (
    <StudioCodeEditor
      ariaLabel="JSON Schema"
      filename="input.schema.json"
      language="json"
      runtimeLabel={`JSON Schema · ${fieldCount} fields`}
      status={validation.status}
      statusTone={validation.tone}
      value={source}
      onChange={setSource}
      onBlur={commit}
    />
  );
}

function Field({
  label,
  hint,
  wide = false,
  children,
}: {
  label: string;
  hint?: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className={wide ? styles.fieldWide : styles.field}>
      <span>{label}{hint && <small>{hint}</small>}</span>
      {children}
    </label>
  );
}

function InfoStrip({
  tone,
  children,
}: {
  tone: "neutral" | "warning";
  children: React.ReactNode;
}) {
  return <div className={tone === "warning" ? styles.warningStrip : styles.infoStrip}>{children}</div>;
}
