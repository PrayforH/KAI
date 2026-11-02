"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { canonicalConversationHref, taskThreadHref } from "../lib/conversation-navigation";
import { PERSONAL_ASSISTANT_THREAD, usePersonalAssistantEntry } from "../components/personal-assistant-context";
import { loadTaskSummary, projectHref } from "../lib/project-space";
import { agentDisplayName } from "../lib/agent-display-name";
import { useInternalAgentsPreference } from "../lib/interface-preferences";

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { AgentThread } from "../components/agent-thread";
import { usePrimaryBot } from "../components/use-primary-bot";
import { BotEditDialog } from "../components/bot-edit-dialog";
import { AuthProvider, useAuth } from "../components/auth-provider";
import { AssistantChannelStatus } from "../components/assistant-channel-status";
import { assistantRequest } from "../lib/assistant-client";
import {
  AssistantBotProvider,
  ConversationSurfaceProvider,
  type AssistantBot,
} from "../components/assistant-bot-context";
import { AssistantHeaderAvatar, BOT_ACCENTS, botAccent } from "../components/bot-rail";
import { AssistantComputerLink } from "../components/assistant-computer-link";
import { DesktopOccupancyChip } from "../components/desktop-occupancy-chip";
import { AssistantComputerPane } from "../components/assistant-computer-pane";
import { AssistantRuntimeShell } from "../components/assistant-runtime-shell";
import { ProductivityCommandCenter } from "../components/productivity-command-center";
import { RunTraceConsole } from "../components/run-trace-console";
import {
  TaskAgentSwitcher,
  taskAgentSwitchMode,
} from "../components/task-agent-switcher";
import {
  TaskDetailsPopover,
  TaskHeaderActions,
} from "../components/task-header-actions";
import { SidebarIcon } from "../components/sidebar-icon";
import { TaskSidebar } from "../components/task-sidebar";
import { createProjectTask } from "../lib/project-task";
import { ApiProject } from "../lib/studio-client";
import { useSidebarProjects } from "../lib/sidebar-state";
import { ProductBrandMark, ProductLoading, PRODUCT_NAME } from "../components/product-brand";
import { SidebarPanelIcon } from "../components/panel-icons";
import { WorkbenchRail } from "../components/workbench-rail";
import type { PreviewTarget } from "../components/rail-file-preview";
import { useRunActivity, useRunViewModel } from "../lib/activity-store";
import { persistTaskVersion, taskVersionBusy } from "../lib/task-version";
import { useRunStream } from "../lib/run-stream-store";
import {
  bindThreadAgent,
  createUserScopedStorage,
  createNewThread,
  loadOrCreateThread,
  isLocalThread,
  markThreadPersisted,
  loadThreadAgent,
  selectThread,
} from "../lib/thread-store";
import {
  agentItemKey,
  chatUsableAgents,
  currentSystemAssistant,
  findTaskAgent,
  runtimeAgentKey,
  loadTaskAgentCatalog,
  type TaskAgent,
} from "../lib/task-agent-catalog";
import { peekCachedTasks, type TaskSummary } from "../lib/task-history";
import {
  loadTaskModelOverride,
  loadTaskModelRoutes,
  saveTaskModelOverride,
  type TaskModelRoute,
} from "../lib/task-model-catalog";
import {
  resolveTaskLaunchMode,
  type TaskThreadState,
} from "../lib/task-launch";
import { persistTaskComposerDraft, rememberNewTaskDraft, pendingNewTaskDraft } from "../lib/task-composer-draft";
import {
  parseSkillCreatorLaunch,
  skillCreatorPrompt,
  type SkillCreatorLaunch,
} from "../lib/skill-creator-launch";

const TASK_SIDEBAR_COMPACT_QUERY = "(max-width: 820px)";

function SidebarPanelToggle({
  expanded,
  onToggle,
}: {
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className="header-icon-button"
      aria-label={expanded ? "收起任务上下文" : "打开任务上下文"}
      aria-expanded={expanded}
      title={expanded ? "收起任务上下文" : "打开任务上下文"}
      onClick={onToggle}
    >
      <SidebarPanelIcon />
    </button>
  );
}

function SidebarExpandToggle({ onToggle }: { onToggle: () => void }) {
  return (
    <button
      type="button"
      className="header-icon-button header-sidebar-toggle"
      aria-label="展开任务列表"
      aria-expanded="false"
      title="展开任务列表"
      onClick={onToggle}
    >
      <SidebarIcon name="panel" />
    </button>
  );
}

function HeaderUtilities({
  computerControl,
  taskRailOpen,
  onToggleTaskRail,
}: {
  computerControl?: ReactNode;
  taskRailOpen: boolean;
  onToggleTaskRail: () => void;
}) {
  return (
    <div className="header-utilities">
      {computerControl}
      <SidebarPanelToggle expanded={taskRailOpen} onToggle={onToggleTaskRail} />
    </div>
  );
}

function TaskContextBar({
  taskTitle,
  task,
  needsInput = false,
  switcher,
  observabilityHref,
  onOpenTrace,
  onRenamed,
  onArchived,
  onForked,
  projects = [],
  onProjectChanged,
}: {
  taskTitle: string;
  task: TaskSummary | null;
  needsInput?: boolean;
  /** Version switcher for switchable agents, folded into the folder popover. */
  switcher?: ReactNode;
  observabilityHref?: string | null;
  onOpenTrace?: () => void;
  onRenamed: (title: string) => void;
  onArchived: () => void;
  onForked: (task: TaskSummary) => void;
  projects?: readonly ApiProject[];
  onProjectChanged?: () => void;
}) {
  return (
    <div className="task-context-bar" aria-label="当前任务、项目与版本">
      {task ? (
        <TaskDetailsPopover task={task} switcher={switcher} />
      ) : (
        <span className="task-folder-static" aria-hidden="true">
          <svg viewBox="0 0 20 20">
            <path d="M2.75 5.75a2 2 0 0 1 2-2h3.1l1.7 1.9h5.7a2 2 0 0 1 2 2v6.5a2 2 0 0 1-2 2H4.75a2 2 0 0 1-2-2Z" />
          </svg>
        </span>
      )}
      <strong className="task-context-title">{taskTitle}</strong>
      {needsInput && <span className="task-input-badge" role="status">需要输入</span>}
      {task && (
        <TaskHeaderActions
          task={task}
          projects={projects}
          observabilityHref={observabilityHref}
          onOpenTrace={onOpenTrace}
          onRenamed={onRenamed}
          onArchived={onArchived}
          onForked={onForked}
          onProjectChanged={() => onProjectChanged?.()}
        />
      )}
    </div>
  );
}

export default function Home() {
  return (
    <AuthProvider>
      <Suspense fallback={<ProductLoading />}><AuthenticatedHome /></Suspense>
    </AuthProvider>
  );
}

function AuthenticatedHome() {
  const assistantEntry = usePersonalAssistantEntry();
  const router = useRouter();
  const searchParams = useSearchParams();
  const botParam = assistantEntry ? searchParams.get("bot") : null;
  const routeRunId = searchParams.get("run");
  const routeTrace = searchParams.get("trace") === "1";
  const leaveAssistantForTask = useCallback((nextThreadId: string) => {
    if (!assistantEntry) return false;
    router.push(taskThreadHref(nextThreadId));
    return true;
  }, [assistantEntry, router]);
  const { user, membership } = useAuth();
  const primaryProfile = usePrimaryBot(user.user_id, membership?.tenant_id);
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const projectId = query.get("project");
    if (projectId && !query.has("thread")) {
      const tab = query.get("tab");
      router.replace(`${projectHref(projectId)}${tab ? `?tab=${encodeURIComponent(tab)}` : ""}`);
    }
  }, [router]);
  const [showInternalAgents] = useInternalAgentsPreference();
  const [threadId, setThreadId] = useState("");
  const [loadedBot, setBot] = useState<AssistantBot | null>(null);
  const bot = useMemo<AssistantBot | null>(() => botParam
    ? loadedBot?.specialistId === botParam ? loadedBot : null
    : assistantEntry && primaryProfile ? {
      specialistId: primaryProfile.specialistId, name: primaryProfile.name,
      description: primaryProfile.description, agentName: primaryProfile.agentName,
      agentVersion: primaryProfile.agentVersion, status: primaryProfile.status,
      threadId: PERSONAL_ASSISTANT_THREAD, objectiveId: null,
      appearance: primaryProfile.config.appearance as {color?: string; avatar?: string} | undefined,
    } : null, [assistantEntry, botParam, loadedBot, primaryProfile]);
  const personalAssistant = assistantEntry && threadId === PERSONAL_ASSISTANT_THREAD;
  // 助理入口一律用对话形态；选中机器人时标题与输入框跟着它变。
  const [computerOpen, setComputerOpen] = useState(false);
  useEffect(() => {
    try {
      setComputerOpen(new URLSearchParams(window.location.search).get("computer") === "open"
        || window.localStorage.getItem(`kai:computer-pane:${user.user_id}`) === "open");
    } catch { /* A disabled browser storage still allows opening the pane. */ }
  }, [user.user_id]);
  const showComputer = (open: boolean) => {
    setComputerOpen(open);
    if (open) { setTaskRailOpen(false); setRailExpanded(false); }
    try {
      window.localStorage.setItem(`kai:computer-pane:${user.user_id}`, open ? "open" : "closed");
      const url = new URL(window.location.href);
      if (url.searchParams.has("computer")) { url.searchParams.delete("computer"); window.history.replaceState(null, "", url.href); }
    } catch { /* optional view preference */ }
  };
  // 每次进入某个 Bot 会话只上报一次"查看"（botRevision 刷新不重复上报）。
  const botViewedRef = useRef<string | null>(null);
  const [botEditOpen, setBotEditOpen] = useState(false);
  const [botRevision, setBotRevision] = useState(0);


  // 只有「主助理线程 / 某个 bot 的会话」才是助理面。
  // 在助理路由下点侧栏的普通任务时，线程会被换成任务线程，但路由仍是 /assistant；
  // 这时必须切回任务面，否则旧任务会被套上助理头部与聊天气泡。
  const conversationSurface = assistantEntry &&
    (!threadId || threadId === PERSONAL_ASSISTANT_THREAD || botParam !== null)
    ? "assistant" : "task";
  const assistantView = conversationSurface === "assistant";

  useEffect(() => {
    if (!assistantEntry) { botViewedRef.current = null; return; }
    const botId = botParam;
    if (!botId) { setBot(null); botViewedRef.current = null; return; }
    let active = true;
    void assistantRequest<{
      specialist: {
        specialistId: string;
        name: string;
        description: string;
        agentName: string;
        agentVersion: string;
        status: string;
        config?: { appearance?: { color?: string; avatar?: string } };
      };
      threadId: string | null;
      primaryObjectiveId: string | null;
    }>(`specialists/${encodeURIComponent(botId)}`)
      .then((data) => {
        if (!active) return;
        if (data.threadId === PERSONAL_ASSISTANT_THREAD) {
          const query = new URLSearchParams(window.location.search);
          query.delete("bot"); query.delete("thread");
          router.replace(`/assistant${query.size ? `?${query}` : ""}`, {scroll: false});
          return;
        }
        setBot({
          specialistId: data.specialist.specialistId,
          name: data.specialist.name,
          description: data.specialist.description,
          agentName: data.specialist.agentName,
          agentVersion: data.specialist.agentVersion,
          status: data.specialist.status,
          threadId: data.threadId,
          objectiveId: data.primaryObjectiveId,
          appearance: data.specialist.config?.appearance,
        });
        // 子助理没有替身会话：直接进入承接职责的原线程，结果就在这条对话里。
        if (data.threadId) {
          setThreadId(data.threadId);
          // 进入该 Bot 的会话＝一次显式查看：清掉"无人查看已暂停"，闸门恢复。
          // 只打开「…」编辑弹窗不算查看（详情 GET 是纯读）。
          if (botViewedRef.current !== botId) {
            botViewedRef.current = botId;
            void assistantRequest(
              `specialists/${encodeURIComponent(botId)}/viewed`,
              "POST",
            )
              // 服务端已清掉 routinePausedReason：让侧栏立刻重拉列表，
              // 不必等 30s 轮询（失败保持旧列表，轮询兜底）。
              .then(() => {
                window.dispatchEvent(
                  new CustomEvent("kai:specialists-changed", {
                    detail: { specialistId: botId, reason: "viewed" },
                  }),
                );
              })
              .catch(() => { /* 查看上报失败不影响会话 */ });
          }
        }
      })
      .catch(() => { if (active) setBot(null); });
    return () => { active = false; };
  }, [assistantEntry, user.user_id, botParam, botRevision, router]);

  const [taskAgents, setTaskAgents] = useState<TaskAgent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<TaskAgent | null>(null);
  useEffect(() => {
    if (!botParam || !bot?.agentName || !bot.agentVersion) return;
    if (selectedAgent?.name === bot.agentName && selectedAgent.version === bot.agentVersion
      && selectedAgent.ownerUserId === user.user_id && !selectedAgent.spaceId) return;
    setSelectedAgent({
      name: bot.agentName, version: bot.agentVersion, displayName: bot.name,
      ownerUserId: user.user_id, scope: "personal", domain: "assistant",
    });
  }, [bot, botParam, selectedAgent, user.user_id]);

  const [systemAssistant, setSystemAssistant] = useState<TaskAgent | null>(null);
  const [modelRoutes, setModelRoutes] = useState<TaskModelRoute[]>([]);
  const [modelRouteOverride, setModelRouteOverride] = useState<string | null>(null);
  useEffect(() => {
    if (!assistantEntry || botParam) return;
    setBot(null);
    setThreadId(PERSONAL_ASSISTANT_THREAD);
    setCurrentTask(null);
    setCurrentThreadState("durable");
    const stored = loadThreadAgent(createUserScopedStorage(window.localStorage, user.user_id), PERSONAL_ASSISTANT_THREAD);
    if (stored) setSelectedAgent({...stored, displayName: agentDisplayName(stored.name, stored.displayName), domain: stored.domain ?? "restored"});
    else if (systemAssistant) setSelectedAgent(systemAssistant);
  }, [assistantEntry, botParam, systemAssistant, user.user_id]);
  useEffect(() => {
    if (!assistantEntry || botParam) return;
    if (primaryProfile && botViewedRef.current !== primaryProfile.specialistId) {
      botViewedRef.current = primaryProfile.specialistId;
      void assistantRequest(`specialists/${encodeURIComponent(primaryProfile.specialistId)}/viewed`, "POST")
        .then(() => window.dispatchEvent(new CustomEvent("kai:specialists-changed"))).catch(() => {});
    }
  }, [assistantEntry, botParam, primaryProfile]);
  useEffect(() => { taskSelectionRevision.current += 1; }, [botParam]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [agentsError, setAgentsError] = useState("");
  const [versionSwitchPending, setVersionSwitchPending] = useState(false);
  const [versionSwitchError, setVersionSwitchError] = useState("");
  const versionSwitchInFlight = useRef(false);
  const visibleThreadRef = useRef(threadId);
  visibleThreadRef.current = threadId;
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0);
  const [taskSidebarOpen, setTaskSidebarOpen] = useState(true);
  const [taskRailOpen, setTaskRailOpen] = useState(false);
  const [railExpanded, setRailExpanded] = useState(false);
  const [railPreview, setRailPreview] = useState<(PreviewTarget & { nonce: number }) | null>(null);
  const previewNonce = useRef(0);
  // Artifact cards in the transcript ask the rail to show that file.
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as (PreviewTarget & { expanded?: boolean }) | undefined;
      if (!detail?.artifact_id) return;
      setComputerOpen(false);
      previewNonce.current += 1;
      setRailPreview({ ...detail, nonce: previewNonce.current });
      setTaskRailOpen(true);
      if (detail.expanded) setRailExpanded(true);
    };
    window.addEventListener("harness:preview-artifact", handler);
    return () => window.removeEventListener("harness:preview-artifact", handler);
  }, []);
  const [compactTaskSidebar, setCompactTaskSidebar] = useState(false);
  const taskSelectionRevision = useRef(0);
  const [localThreadId, setLocalThreadId] = useState<string | null>(null);
  const [currentTaskTitle, setCurrentTaskTitle] = useState("新任务");
  const [currentTask, setCurrentTask] = useState<TaskSummary | null>(null);
  const activeProjectId = currentTask?.project_id ?? null;
  const [currentThreadState, setCurrentThreadState] =
    useState<TaskThreadState>("unknown");
  const [activeSkillLaunch, setActiveSkillLaunch] =
    useState<SkillCreatorLaunch | null>(null);
  const runView = useRunViewModel();
  const runActivity = useRunActivity();
  const [stageView, setStageView] = useState<"conversation" | "trace">(
    "conversation",
  );
  // 失败回放入口会带 trace=1 与 run（及可选 checkpoint）；轨迹页据此选中失败节点。
  // 在 effect 里读 URL，避免服务端首屏与客户端水合不一致。
  const [focusRunId, setFocusRunId] = useState<string | null>(null);
  useEffect(() => {
    setStageView(routeTrace ? "trace" : "conversation");
    setFocusRunId(routeRunId);
    setBotEditOpen(false);
  }, [routeRunId, routeTrace, botParam]);
  useEffect(() => {
    const openTrace = () => setStageView("trace");
    window.addEventListener("harness:open-trace", openTrace);
    return () => window.removeEventListener("harness:open-trace", openTrace);
  }, []);
  // A new task starts a clean thread: the trace view has nothing to show yet.
  useEffect(() => {
    if (localThreadId === threadId) setStageView("conversation");
  }, [threadId, localThreadId]);
  // One click into Langfuse for the run on screen, or nothing before it exists.
  const observabilityHref = runActivity?.run_id
    ? `/api/harness/observability?run_id=${encodeURIComponent(runActivity.run_id)}&trace_id=${encodeURIComponent(runActivity.trace_id ?? "")}`
    : null;
  const runStream = useRunStream();
  const currentTaskBusy = versionSwitchPending || taskVersionBusy(runStream, runView);

  useEffect(() => {
    const compactViewport = window.matchMedia(TASK_SIDEBAR_COMPACT_QUERY);
    const syncCompactViewport = (matches: boolean) => {
      setCompactTaskSidebar(matches);
      if (matches) setTaskSidebarOpen(false);
    };
    syncCompactViewport(compactViewport.matches);
    const handleViewportChange = (event: MediaQueryListEvent) => {
      syncCompactViewport(event.matches);
    };
    compactViewport.addEventListener("change", handleViewportChange);
    return () => compactViewport.removeEventListener("change", handleViewportChange);
  }, []);

  useEffect(() => {
    let active = true;
    const storage = createUserScopedStorage(window.localStorage, user.user_id);
    const storedThreadId = loadOrCreateThread(storage);
    const selectionRevision = taskSelectionRevision.current;
    const initialSearch = new URLSearchParams(window.location.search);
    const requestedSkillLaunch = parseSkillCreatorLaunch(initialSearch);
    const canonicalHref = canonicalConversationHref(assistantEntry, initialSearch);
    if (canonicalHref) {
      router.replace(canonicalHref);
      if (assistantEntry !== canonicalHref.startsWith("/assistant")) return;
    }
    const requestedThreadId = assistantEntry ? PERSONAL_ASSISTANT_THREAD : initialSearch.get("thread");
    // Opening an assistant must not overwrite the last ordinary task selection.
    const initialThreadId = assistantEntry ? PERSONAL_ASSISTANT_THREAD : requestedThreadId
      ? selectThread(storage, requestedThreadId)
      : storedThreadId === PERSONAL_ASSISTANT_THREAD ? createNewThread(storage) : storedThreadId;
    const hasRequestedAgent = Boolean(
      initialSearch.get("agent") &&
      initialSearch.get("version") &&
      (initialSearch.get("space") || initialSearch.get("owner")),
    );
    setLocalThreadId(isLocalThread(storage, initialThreadId) ? initialThreadId : null);
    if (!assistantEntry || !initialSearch.get("bot")) setThreadId(initialThreadId);
    // The concrete thread-to-Agent binding is already durable in this
    // browser. Restore it immediately so returning from Studio can mount the
    // conversation/history while the authoritative catalogs revalidate in
    // the background. Deep links deliberately wait for catalog authorization.
    const restoredBinding = hasRequestedAgent
      ? null
      : loadThreadAgent(storage, initialThreadId);
    if (restoredBinding) {
      const restoredAgent: TaskAgent = {
        ...restoredBinding,
        displayName: agentDisplayName(restoredBinding.name, restoredBinding.displayName),
        domain: restoredBinding.domain ?? "restored",
      };
      setSelectedAgent(restoredAgent);
      setTaskAgents([restoredAgent]);
    }
    async function loadAgentBinding() {
      setAgentsLoading(true);
      setAgentsError("");
      try {
        const summary = loadTaskSummary(initialThreadId)
          .then((task) => {
            // A durable thread's own authorized binding is enough to replay
            // it. Do not put history behind the complete registry/draft list.
            if (task && !restoredBinding && !hasRequestedAgent && !requestedSkillLaunch &&
                active && taskSelectionRevision.current === selectionRevision) {
              setSelectedAgent({
                name: task.agent_name,
                version: task.agent_version,
                ownerUserId: task.agent_owner_user_id,
                spaceId: task.space_id ?? undefined,
                scope: task.space_id ? "team" : "personal",
                displayName: agentDisplayName(task.agent_name),
                domain: "historical",
              });
              setCurrentTask(task);
              setCurrentTaskTitle(task.title);
              setCurrentThreadState("durable");
              markThreadPersisted(storage, initialThreadId);
              setLocalThreadId(null);
            }
            return { available: true as const, tasks: task ? [task] : [] };
          })
          .catch(() => ({ available: false as const, tasks: [] as TaskSummary[] }));
        const [catalog, routes, taskHistory] = await Promise.all([
          loadTaskAgentCatalog(user.user_id, showInternalAgents),
          loadTaskModelRoutes().catch(() => []),
          summary,
        ]);
        if (!active) return;
        // Catalog data can still refresh, but an older load cannot select a task
        // after the user has clicked New Task or chosen another conversation.
        if (taskSelectionRevision.current !== selectionRevision) {
          setTaskAgents(chatUsableAgents(catalog.agents));
          setSystemAssistant(catalog.defaultAgent);
          setModelRoutes(routes);
          return;
        }
        const search = initialSearch;
        const requestedName = search.get("agent");
        const requestedVersion = search.get("version");
        const requestedSpaceId = search.get("space");
        const requestedOwnerUserId = search.get("owner");
        const requestedAgent = requestedName && requestedVersion &&
          (requestedSpaceId || requestedOwnerUserId)
          ? findTaskAgent(catalog.agents, {
              name: requestedName,
              version: requestedVersion,
              spaceId: requestedSpaceId ?? undefined,
              ownerUserId: requestedOwnerUserId ?? undefined,
            })
          : undefined;
        if (hasRequestedAgent && !requestedAgent) {
          throw new Error(
            `指定的智能体版本不可用：${requestedName}@${requestedVersion}。请返回智能体中心重新选择当前版本。`,
          );
        }
        const currentThreadId = requestedAgent || requestedSkillLaunch
          ? createNewThread(storage)
          : initialThreadId;
        if (requestedSkillLaunch) {
          persistTaskComposerDraft(
            storage,
            user.user_id,
            currentThreadId,
            skillCreatorPrompt(requestedSkillLaunch),
          );
          setActiveSkillLaunch(requestedSkillLaunch);
        } else {
          setActiveSkillLaunch(null);
        }
        if (requestedAgent || requestedSkillLaunch) {
          setLocalThreadId(currentThreadId);
          setThreadId(currentThreadId);
          window.history.replaceState({}, "", "/");
        }
        const currentTask = taskHistory.tasks.find(
          (task) => task.thread_id === currentThreadId,
        );
        if (taskHistory.available) setCurrentTask(currentTask ?? null);
        if (currentTask) { markThreadPersisted(storage, currentThreadId); setLocalThreadId(null); }
        else if (assistantEntry && taskHistory.available) setLocalThreadId(currentThreadId);
        setCurrentTaskTitle(currentTask?.title ?? "新任务");
        setCurrentThreadState(
          currentTask ? "durable" : taskHistory.available ? "empty" : "unknown",
        );
        const stored = loadThreadAgent(storage, currentThreadId);
        const storedAgent = stored
          ? findTaskAgent(catalog.agents, stored)
          : undefined;
        const coordinates = requestedAgent ?? (currentTask
          ? {
              name: currentTask.agent_name,
              version: currentTask.agent_version,
              ownerUserId: currentTask.agent_owner_user_id,
              scope: currentTask.space_id ? "team" as const : "personal" as const,
              spaceId: currentTask.space_id ?? undefined,
            }
          : storedAgent ?? catalog.defaultAgent);
        const selected = findTaskAgent(catalog.agents, coordinates) ??
          (currentTask
            ? {
                name: coordinates.name,
                version: coordinates.version,
                displayName: agentDisplayName(coordinates.name),
                domain: "historical",
                ownerUserId: coordinates.ownerUserId,
                scope: coordinates.scope,
                spaceId: coordinates.spaceId,
              }
            : catalog.defaultAgent);
        // Historical coordinates remain selected for replay, but deleted or
        // revoked Agents never return to the new-task/version selector.
        const chatUsable = chatUsableAgents(catalog.agents);
        setTaskAgents(chatUsable);
        setSystemAssistant(catalog.defaultAgent);
        setModelRoutes(routes);
        setSelectedAgent(selected);
        const storedModelRoute = loadTaskModelOverride(
          storage,
          currentThreadId,
        );
        setModelRouteOverride(
          routes.some((route) => route.id === "deepseek-v4-flash")
            ? "deepseek-v4-flash"
            : routes.some((route) => route.id === storedModelRoute)
              ? storedModelRoute
              : null,
        );
        bindThreadAgent(storage, currentThreadId, selected);
        setAgentsError("");
      } catch (error) {
        if (!active || taskSelectionRevision.current !== selectionRevision) return;
        setAgentsError(
          error instanceof Error ? error.message : "智能体目录暂不可用",
        );
      } finally {
        if (active) setAgentsLoading(false);
      }
    }
    void loadAgentBinding();
    return () => {
      active = false;
    };
  }, [catalogRefreshKey, user.user_id, showInternalAgents, assistantEntry, router]);

  const refreshingCatalog = useRef(false);
  const refreshAgentCatalog = useCallback(async () => {
    if (refreshingCatalog.current) return;
    refreshingCatalog.current = true;
    try {
      const catalog = await loadTaskAgentCatalog(user.user_id, showInternalAgents);
      setTaskAgents(chatUsableAgents(catalog.agents));
      setSystemAssistant(catalog.defaultAgent);
      setSelectedAgent((current) => current ? findTaskAgent(catalog.agents, current) ?? current : current);
    } catch { /* Keep the current conversation available while retrying. */ }
    finally { refreshingCatalog.current = false; }
  }, [user.user_id, showInternalAgents]);
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === "visible") void refreshAgentCatalog(); };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [refreshAgentCatalog]);

  const availableTaskAgents = useMemo(() => taskAgents, [taskAgents]);
  // The sidebar groups tasks by the agent that ran them, so the browser view
  // shows the same display labels the header and switcher use.
  const [projects, refreshProjects] = useSidebarProjects(user.user_id);
  const agentDisplayLabels = useMemo(
    () =>
      Object.fromEntries(
        taskAgents.map((agent) => [
          agent.name,
          agentDisplayName(agent.name, agent.displayName),
        ]),
      ),
    [taskAgents],
  );

  function taskStorage() {
    return createUserScopedStorage(window.localStorage, user.user_id);
  }

  const closeCompactTaskSidebar = useCallback(() => {
    if (compactTaskSidebar) setTaskSidebarOpen(false);
  }, [compactTaskSidebar]);

  const focusTaskComposer = useCallback(() => {
    closeCompactTaskSidebar();
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>(".aui-composer-input")?.focus();
    });
  }, [closeCompactTaskSidebar]);

  const createTaskWithAgent = useCallback((nextAgent: TaskAgent) => {
    taskSelectionRevision.current += 1;
    const storage = createUserScopedStorage(window.localStorage, user.user_id);
    const nextThreadId = createNewThread(storage);
    rememberNewTaskDraft(window.localStorage, user.user_id, nextThreadId);
    bindThreadAgent(storage, nextThreadId, nextAgent);
    if (leaveAssistantForTask(nextThreadId)) return;
    window.history.replaceState(window.history.state, "", taskThreadHref(nextThreadId));
    setSelectedAgent(nextAgent);
    setLocalThreadId(nextThreadId);
    setThreadId(nextThreadId);
    setCurrentTaskTitle("新任务");
    setCurrentTask(null);
    setCurrentThreadState("empty");
    setModelRouteOverride(null);
    setActiveSkillLaunch(null);
    closeCompactTaskSidebar();
  }, [closeCompactTaskSidebar, user.user_id, leaveAssistantForTask]);

  const startTaskWithAgent = useCallback(async (nextAgent: TaskAgent) => {
    if (currentTask?.project_id) {
      if (versionSwitchInFlight.current) return;
      versionSwitchInFlight.current = true;
      setVersionSwitchPending(true);
      const revision = ++taskSelectionRevision.current;
      try {
        const task = await createProjectTask(currentTask.project_id, nextAgent);
        if (revision === taskSelectionRevision.current) { switchTask(task); focusTaskComposer(); }
      } catch (error) {
        if (revision === taskSelectionRevision.current) setVersionSwitchError(error instanceof Error ? error.message : "新建项目任务失败");
      } finally {
        versionSwitchInFlight.current = false;
        setVersionSwitchPending(false);
      }
      return;
    }
    const launchMode = resolveTaskLaunchMode(currentThreadState, "select-agent");
    if (!assistantEntry && launchMode === "reuse-current") {
      const storage = createUserScopedStorage(window.localStorage, user.user_id);
      bindThreadAgent(storage, threadId, nextAgent);
      setSelectedAgent(nextAgent);
      setModelRouteOverride(null);
      focusTaskComposer();
      return;
    }
    createTaskWithAgent(nextAgent);
  }, [createTaskWithAgent, currentTask?.project_id, currentThreadState, focusTaskComposer, threadId, user.user_id, taskAgents, modelRoutes, assistantEntry]);

  // Keep the header's task context (title + details popover data) synced with
  // the durable task list: on thread switches, run phase changes, and after
  // pin/rename/archive actions anywhere in the app.
  useEffect(() => {
    if (!threadId) return;
    let active = true;
    const sync = () => {
      void loadTaskSummary(threadId).then((current) => {
        if (!active) return;
        setCurrentTask(current);
        if (current) { markThreadPersisted(taskStorage(), threadId); setLocalThreadId(null); setCurrentTaskTitle(current.title); setCurrentThreadState("durable"); }
      }).catch(() => undefined);
    };
    sync();
    window.addEventListener("harness:task-list-changed", sync);
    return () => {
      active = false;
      window.removeEventListener("harness:task-list-changed", sync);
    };
  }, [threadId, runView?.runId, runView?.phase]);

  async function startTaskInProject(project: ApiProject) {
    const selectionRevision = ++taskSelectionRevision.current;
    const task = await createProjectTask(project.projectId, systemAssistant);
    if (taskSelectionRevision.current !== selectionRevision) return;
    switchTask(task);
    focusTaskComposer();
  }

  const startNewTask = useCallback(() => {
    const candidate = selectedAgent && taskAgents.some(
      (agent) => agentItemKey(agent) === agentItemKey(selectedAgent),
    )
      ? selectedAgent
      : taskAgents[0];
    const nextAgent = currentSystemAssistant(candidate ?? null, systemAssistant);
    if (!nextAgent) return;
    if (!assistantEntry && currentThreadState === "empty") {
      if (selectedAgent && nextAgent.version !== selectedAgent.version) {
        bindThreadAgent(taskStorage(), threadId, nextAgent);
        setSelectedAgent(nextAgent);
      }
      focusTaskComposer();
      return;
    }
    const pendingThreadId = pendingNewTaskDraft(window.localStorage, user.user_id, new Set(peekCachedTasks()?.map(task => task.thread_id)));
    if (pendingThreadId) {
      const storedAgent = loadThreadAgent(taskStorage(), pendingThreadId);
      if (storedAgent) {
        const draftAgent = findTaskAgent(taskAgents, storedAgent);
        if (draftAgent) {
          taskSelectionRevision.current += 1;
          if (leaveAssistantForTask(pendingThreadId)) return;
          window.history.replaceState(window.history.state, "", taskThreadHref(pendingThreadId));
          setSelectedAgent(draftAgent);
          setLocalThreadId(pendingThreadId);
          setThreadId(selectThread(taskStorage(), pendingThreadId));
          setCurrentTask(null); setCurrentTaskTitle("新任务"); setCurrentThreadState("empty");
          setModelRouteOverride(loadTaskModelOverride(taskStorage(), pendingThreadId));
          setActiveSkillLaunch(null); focusTaskComposer(); return;
        }
      }
    }
    createTaskWithAgent(nextAgent);
  }, [createTaskWithAgent, currentThreadState, focusTaskComposer, selectedAgent, taskAgents, systemAssistant, threadId, assistantEntry, leaveAssistantForTask]);

  function switchTask(task: TaskSummary) {
    taskSelectionRevision.current += 1;
    if (leaveAssistantForTask(task.thread_id)) return;
    window.history.replaceState(window.history.state, "", taskThreadHref(task.thread_id));
    const nextAgent =
      taskAgents.find(
        (agent) =>
          agent.name === task.agent_name && agent.version === task.agent_version &&
          agent.ownerUserId === task.agent_owner_user_id &&
          agent.spaceId === (task.space_id ?? undefined),
      ) ?? {
        name: task.agent_name,
        version: task.agent_version,
        displayName: agentDisplayName(task.agent_name),
        domain: "historical",
        ownerUserId: task.agent_owner_user_id,
        scope: task.space_id ? "team" : "personal",
        spaceId: task.space_id ?? undefined,
      };
    if (currentThreadState === "empty") rememberNewTaskDraft(window.localStorage, user.user_id, threadId);
    const storage = taskStorage();
    bindThreadAgent(storage, task.thread_id, nextAgent);
    setSelectedAgent(nextAgent);
    const storedModelRoute = loadTaskModelOverride(
      storage,
      task.thread_id,
    );
    setModelRouteOverride(
      modelRoutes.some((route) => route.id === "deepseek-v4-flash")
        ? "deepseek-v4-flash"
        : modelRoutes.some((route) => route.id === storedModelRoute)
          ? storedModelRoute
          : null,
    );
    setCurrentThreadState("durable");
    setCurrentTaskTitle(task.title);
    setCurrentTask(task);
    setActiveSkillLaunch(null);
    markThreadPersisted(storage, task.thread_id);
    setLocalThreadId(null);
    const resolvedThreadId = selectThread(storage, task.thread_id);
    setThreadId(resolvedThreadId);
    closeCompactTaskSidebar();
  }

  async function switchAgent(nextAgent: TaskAgent) {
    const mode = taskAgentSwitchMode(selectedAgent, nextAgent);
    if (versionSwitchInFlight.current || mode === "current" || (mode === "version" && currentTaskBusy)) return;
    setVersionSwitchError("");
    if (mode === "version") {
      versionSwitchInFlight.current = true;
      setVersionSwitchPending(true);
      const switchingThreadId = threadId;
      try {
        if (currentThreadState !== "empty") {
          await persistTaskVersion(switchingThreadId, nextAgent.version);
        }
        bindThreadAgent(taskStorage(), switchingThreadId, nextAgent);
        if (visibleThreadRef.current === switchingThreadId) setSelectedAgent(nextAgent);
      } catch (error) {
        if (visibleThreadRef.current === switchingThreadId) {
          setVersionSwitchError(error instanceof Error ? error.message : "版本切换失败，请重试");
        }
      } finally {
        versionSwitchInFlight.current = false;
        setVersionSwitchPending(false);
      }
      return;
    }
    startTaskWithAgent(nextAgent);
  }

  useEffect(() => {
    const openFiles = () => setTaskRailOpen(true);
    const newTask = () => startNewTask();
    window.addEventListener("harness:open-files", openFiles);
    window.addEventListener("harness:new-task", newTask);
    return () => { window.removeEventListener("harness:open-files", openFiles); window.removeEventListener("harness:new-task", newTask); };
  }, [startNewTask]);

  return (
    <main
      className={`console-shell${taskRailOpen ? " is-rail-open" : ""}${taskRailOpen && railExpanded ? " is-rail-expanded" : ""}`}
      id="main-content"
      data-conversation-surface={conversationSurface}
      data-task-thread-state={currentThreadState}
      data-project-task={activeProjectId ? "true" : undefined}
    >
      <div
        className={`workspace-stage ${taskSidebarOpen ? "tasks-open" : ""}`}
      >
        {compactTaskSidebar && taskSidebarOpen && (
          <button
            className="task-sidebar-scrim"
            type="button"
            aria-label="关闭任务列表"
            tabIndex={-1}
            onClick={() => setTaskSidebarOpen(false)}
          />
        )}
        <TaskSidebar
          activeNav={assistantView ? "assistant" : "tasks"}
          currentThreadId={threadId}
          activeBotId={botParam}
          collapsed={!taskSidebarOpen}
          overlayOpen={compactTaskSidebar && taskSidebarOpen}
          onToggle={() => {
            setTaskSidebarOpen((current) => !current);
          }}
          onSelect={switchTask}
          onNewTask={startNewTask}
          onNewTaskWithProject={startTaskInProject}
          agentLabels={agentDisplayLabels}
          projects={projects}
          onProjectsChanged={refreshProjects}
          searchControl={(
            <ProductivityCommandCenter
              agents={availableTaskAgents}
              onNewTask={startNewTask}
              onSelectTask={switchTask}
              onStartWithAgent={startTaskWithAgent}
            />
          )}
        />
        <div
          className="task-content-shell"
          aria-hidden={compactTaskSidebar && taskSidebarOpen ? true : undefined}
        >
          <header className="console-header">
            <div className="header-leading">
              {!taskSidebarOpen && (
                <>
                  <span className="header-product-logo" role="img" aria-label={PRODUCT_NAME}><ProductBrandMark /></span>
                  <SidebarExpandToggle onToggle={() => setTaskSidebarOpen(true)} />
                </>
              )}
              {activeProjectId && <Link className="task-project-breadcrumb" href={projectHref(activeProjectId)}>
                <span className="task-project-label">{projects.find((project) => project.projectId === activeProjectId)?.name ?? "返回项目"}</span>
                <span className="task-project-separator" aria-hidden="true">/</span>
              </Link>}
              {assistantView ? <div className="task-context-bar">
                {/* 顶栏身份标记与 rail 同源：子助理用保存的色+形，主助理固定 K。 */}
                <AssistantHeaderAvatar bot={bot} />
                <strong className="task-context-title">{bot ? bot.name : "我的助理"}</strong>
                {bot ? (
                  <>
                    <span className="task-context-bot-hint">{bot.description || "专职子助理"}</span>
                    <button
                      type="button"
                      className="task-context-bot-more"
                      aria-label="编辑这个 Bot"
                      title="编辑这个 Bot"
                      onClick={() => setBotEditOpen(true)}
                    >
                      …
                    </button>
                  </>
                ) : (
                  <AssistantChannelStatus />
                )}
              </div> : <TaskContextBar
                taskTitle={currentTaskTitle}
                onForked={switchTask}
                task={currentTask}
                needsInput={Boolean(currentTask?.pending_input && !currentTaskBusy)}
                observabilityHref={observabilityHref}
                onOpenTrace={() => setStageView("trace")}
                projects={projects}
                onProjectChanged={() => {
                  // The task moved between sections: reload both the list and
                  // the per-project counts.
                  refreshProjects();
                  window.dispatchEvent(new CustomEvent("harness:task-list-changed"));
                }}
                switcher={selectedAgent && selectedAgent.name !== "lead-agent" ? (
                  <TaskAgentSwitcher
                    kind="version"
                    agents={availableTaskAgents}
                    selected={selectedAgent}
                    loading={agentsLoading || versionSwitchPending}
                    currentTaskBusy={currentTaskBusy}
                    onChange={switchAgent}
                    onRefresh={refreshAgentCatalog}
                  />
                ) : undefined}
                onRenamed={setCurrentTaskTitle}
                onArchived={() => {
                  setCurrentTask(null);
                  setCurrentTaskTitle("新任务");
                  startNewTask();
                }}
              />}
            </div>
            <HeaderUtilities
              computerControl={personalAssistant ? (
                <>
                  <DesktopOccupancyChip />
                  <AssistantComputerLink open={computerOpen} onToggle={() => showComputer(!computerOpen)} />
                </>
              ) : undefined}
              taskRailOpen={taskRailOpen}
              onToggleTaskRail={() => {
                showComputer(false);
                setTaskRailOpen((current) => !current);
                setRailExpanded(false);
              }}
            />
          </header>
          <AssistantComputerPane open={personalAssistant && computerOpen} onClose={() => showComputer(false)}>
          <section
            className="chat-stage"
            data-surface={conversationSurface}
            style={{ ["--kai-bot-accent" as string]: bot ? (bot.appearance?.color ?? botAccent(bot.specialistId)) : BOT_ACCENTS[0] }}
            aria-label={personalAssistant ? "我的助理对话" : "Agent 任务对话"}
          >
            {stageView === "trace" ? (
              <RunTraceConsole
                threadId={threadId}
                liveActivity={runActivity}
                runBusy={currentTaskBusy}
                focusRunId={focusRunId}
                onBack={() => setStageView("conversation")}
                agentName={selectedAgent?.name}
                agentVersion={selectedAgent?.version}
                traceHref={observabilityHref}
              />
            ) : (
            <div className="chat-surface">
              <ConversationSurfaceProvider surface={conversationSurface}>
              <AssistantBotProvider bot={bot}>
              {bot && botEditOpen && (
                <BotEditDialog
                  specialistId={bot.specialistId}
                  onClose={() => setBotEditOpen(false)}
                  onSaved={() => {
                    setBotEditOpen(false);
                    setBotRevision((current) => current + 1);
                    window.dispatchEvent(new CustomEvent("kai:specialists-changed"));
                  }}
                />
              )}
              {versionSwitchError && <p role="alert">{versionSwitchError}</p>}
              {assistantEntry && (botParam ? (!bot?.threadId || threadId !== bot.threadId ||
                selectedAgent?.name !== bot.agentName || selectedAgent?.version !== bot.agentVersion)
                : threadId !== PERSONAL_ASSISTANT_THREAD) ? (
                <div className="bot-empty-state">
                  <p className="bot-empty-title">{bot ? `${bot.name} 还没有职责` : "正在打开这个 Bot…"}</p>
                  {bot && (
                    <>
                      <p className="bot-empty-hint">
                        去主助理那儿给它派一件事，它就会有一条自己的会话；结果也会回到那条会话里。
                      </p>
                      <button
                        type="button"
                        className="bot-empty-action"
                        onClick={() => router.push("/assistant", {scroll: false})}
                      >
                        找主助理派活
                      </button>
                    </>
                  )}
                </div>
              ) : threadId && selectedAgent ? (
                <AssistantRuntimeShell
                  key={`${threadId}:${runtimeAgentKey(selectedAgent)}`}
                  threadId={threadId}
                  localOnly={localThreadId === threadId}
                  conversationSurface={conversationSurface}
                  agentName={selectedAgent.name}
                  agentVersion={selectedAgent.version}
                  agentOwnerUserId={selectedAgent.ownerUserId}
                  spaceId={selectedAgent.spaceId}
                  agentDefaultModelRoute={selectedAgent.modelRoute ?? null}
                  modelRoutes={modelRoutes}
                  modelRouteOverride={modelRouteOverride}
                  onModelRouteOverrideChange={(routeId) => {
                    saveTaskModelOverride(taskStorage(), threadId, routeId);
                    setModelRouteOverride(routeId);
                  }}
                >
                  <AgentThread
                    userId={user.user_id}
                    threadId={threadId}
                    onForked={switchTask}
                    agents={availableTaskAgents}
                    selectedAgent={selectedAgent}
                    agentsLoading={agentsLoading || versionSwitchPending}
                    currentTaskBusy={currentTaskBusy}
                    activeSkillLaunch={activeSkillLaunch}
                    onDismissSkillLaunch={() => setActiveSkillLaunch(null)}
                    onAgentChange={switchAgent}
                    onRefreshAgents={refreshAgentCatalog}
                  />
                </AssistantRuntimeShell>
              ) : (
                <div
                  className={`chat-loading${agentsError ? " is-error" : ""}`}
                  role={agentsError ? "alert" : "status"}
                  aria-busy={!agentsError}
                >
                  {agentsError ? (
                    <div className="chat-loading-error">
                      <strong>无法进入任务工作台</strong>
                      <span>{agentsError}</span>
                      <button
                        type="button"
                        onClick={() => setCatalogRefreshKey((current) => current + 1)}
                      >
                        重新连接
                      </button>
                    </div>
                  ) : (
                    <ProductLoading label="正在打开任务…" />
                  )}
                </div>
              )}
              </AssistantBotProvider>
              </ConversationSurfaceProvider>
            </div>
            )}
          </section>
          </AssistantComputerPane>
        </div>
        <WorkbenchRail
          key={threadId}
          open={taskRailOpen}
          onClose={() => { setTaskRailOpen(false); setRailExpanded(false); }}
          expanded={railExpanded}
          onToggleExpanded={() => setRailExpanded((current) => !current)}
          runPhase={runView?.phase ?? null}
          threadId={threadId}
          projectId={activeProjectId}
          previewRequest={railPreview}
        />
      </div>
    </main>
  );
}
