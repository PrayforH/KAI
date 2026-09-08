"use client";
import { useInternalAgentsPreference } from "../lib/interface-preferences";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AgentThread } from "../components/agent-thread";
import { AuthProvider, useAuth } from "../components/auth-provider";
import { AssistantRuntimeShell } from "../components/assistant-runtime-shell";
import { ProductivityCommandCenter } from "../components/productivity-command-center";
import {
  TaskAgentSwitcher,
  taskAgentSwitchMode,
} from "../components/task-agent-switcher";
import { TaskSidebar } from "../components/task-sidebar";
import { ProductBrandMark, ProductLoading, PRODUCT_NAME } from "../components/product-brand";
import { WorkbenchRail } from "../components/workbench-rail";
import { useRunViewModel } from "../lib/activity-store";
import { useRunStream } from "../lib/run-stream-store";
import {
  bindThreadAgent,
  createUserScopedStorage,
  createNewThread,
  loadOrCreateThread,
  loadThreadAgent,
  selectThread,
} from "../lib/thread-store";
import {
  agentItemKey,
  chatUsableAgents,
  currentSystemAssistant,
  findTaskAgent,
  loadTaskAgentCatalog,
  type TaskAgent,
} from "../lib/task-agent-catalog";
import { loadTasks, type TaskSummary } from "../lib/task-history";
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
import { persistTaskComposerDraft } from "../lib/task-composer-draft";
import {
  parseSkillCreatorLaunch,
  skillCreatorPrompt,
  type SkillCreatorLaunch,
} from "../lib/skill-creator-launch";

const TASK_SIDEBAR_COMPACT_QUERY = "(max-width: 820px)";

const HELP_MANUAL_URL = "https://my.feishu.cn/docx/DdiCdPFcroUpUXxOumNcQpIin1g";

function SidebarPanelIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2.75" y="4.25" width="14.5" height="11.5" rx="2.5" />
      <path d="M13.5 4.25v11.5" />
    </svg>
  );
}

function SidebarLeftIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2.75" y="4.25" width="14.5" height="11.5" rx="2.5" />
      <path d="M6.75 4.25v11.5" />
    </svg>
  );
}

function HelpIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="7.25" />
      <path d="M8.1 8.15a1.95 1.95 0 0 1 3.8.55c0 1.3-1.9 1.55-1.9 2.7" />
      <path d="M10 13.85h.01" />
    </svg>
  );
}

function BookIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 5.7C8.9 4.8 7.3 4.35 5.45 4.35c-.6 0-1.15.05-1.7.15v10.15c.55-.1 1.1-.15 1.7-.15 1.85 0 3.45.45 4.55 1.35 1.1-.9 2.7-1.35 4.55-1.35.6 0 1.15.05 1.7.15V4.5c-.55-.1-1.1-.15-1.7-.15-1.85 0-3.45.45-4.55 1.35Z" />
      <path d="M10 5.7v10.15" />
    </svg>
  );
}

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
      <SidebarLeftIcon />
    </button>
  );
}

function HelpMenu() {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function closeOnOutsideClick(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="header-help" ref={menuRef}>
      <button
        type="button"
        className="header-icon-button"
        aria-label="帮助"
        aria-expanded={open}
        title="帮助"
        onClick={() => setOpen((value) => !value)}
      >
        <HelpIcon />
      </button>
      {open && (
        <div className="help-popover" role="dialog" aria-label="帮助">
          <a
            className="help-popover-link"
            href={HELP_MANUAL_URL}
            target="_blank"
            rel="noreferrer"
          >
            <BookIcon />
            产品使用手册
          </a>
        </div>
      )}
    </div>
  );
}

function HeaderUtilities({
  taskRailOpen,
  onToggleTaskRail,
}: {
  taskRailOpen: boolean;
  onToggleTaskRail: () => void;
}) {
  return (
    <div className="header-utilities">
      <HelpMenu />
      <SidebarPanelToggle expanded={taskRailOpen} onToggle={onToggleTaskRail} />
    </div>
  );
}

function TaskContextBar({
  taskTitle,
  agent,
}: {
  taskTitle: string;
  agent: TaskAgent | null;
}) {
  return (
    <div className="task-context-bar" aria-label="当前任务、项目与版本">
      <strong className="task-context-title">{taskTitle}</strong>
      <span className="task-context-chip task-context-project">
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="M2.75 5.75a2 2 0 0 1 2-2h3.1l1.7 1.9h5.7a2 2 0 0 1 2 2v6.5a2 2 0 0 1-2 2H4.75a2 2 0 0 1-2-2Z" />
        </svg>
        {agent?.displayName ?? "agent-studio"}
      </span>

    </div>
  );
}

export default function Home() {
  return (
    <AuthProvider>
      <AuthenticatedHome />
    </AuthProvider>
  );
}

function AuthenticatedHome() {
  const { user } = useAuth();
  const [showInternalAgents] = useInternalAgentsPreference();
  const [threadId, setThreadId] = useState("");
  const [taskAgents, setTaskAgents] = useState<TaskAgent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<TaskAgent | null>(null);
  const [systemAssistant, setSystemAssistant] = useState<TaskAgent | null>(null);
  const [modelRoutes, setModelRoutes] = useState<TaskModelRoute[]>([]);
  const [modelRouteOverride, setModelRouteOverride] = useState<string | null>(null);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [agentsError, setAgentsError] = useState("");
  const [catalogRefreshKey, setCatalogRefreshKey] = useState(0);
  const [taskSidebarOpen, setTaskSidebarOpen] = useState(true);
  const [taskRailOpen, setTaskRailOpen] = useState(false);
  const [compactTaskSidebar, setCompactTaskSidebar] = useState(false);
  const [currentTaskTitle, setCurrentTaskTitle] = useState("新任务");
  const [currentThreadState, setCurrentThreadState] =
    useState<TaskThreadState>("unknown");
  const [activeSkillLaunch, setActiveSkillLaunch] =
    useState<SkillCreatorLaunch | null>(null);
  const runView = useRunViewModel();
  const runStream = useRunStream();
  const currentTaskBusy = runStream.status === "running" || (
    runView?.phase === "queued" ||
    runView?.phase === "running" ||
    runView?.phase === "waiting_approval"
  );

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
    const initialSearch = new URLSearchParams(window.location.search);
    const requestedSkillLaunch = parseSkillCreatorLaunch(initialSearch);
    const requestedThreadId = initialSearch.get("thread");
    const initialThreadId = requestedThreadId
      ? selectThread(storage, requestedThreadId)
      : storedThreadId;
    const hasRequestedAgent = Boolean(
      initialSearch.get("agent") &&
      initialSearch.get("version") &&
      (initialSearch.get("space") || initialSearch.get("owner")),
    );
    setThreadId(initialThreadId);
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
        displayName: restoredBinding.displayName ?? restoredBinding.name,
        domain: restoredBinding.domain ?? "restored",
      };
      setSelectedAgent(restoredAgent);
      setTaskAgents([restoredAgent]);
    }
    async function loadAgentBinding() {
      setAgentsLoading(true);
      setAgentsError("");
      try {
        const [catalog, routes, taskHistory] = await Promise.all([
          loadTaskAgentCatalog(user.user_id, showInternalAgents),
          loadTaskModelRoutes().catch(() => []),
          loadTasks()
            .then((tasks) => ({ available: true as const, tasks }))
            .catch(() => ({ available: false as const, tasks: [] as TaskSummary[] })),
        ]);
        if (!active) return;
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
          setThreadId(currentThreadId);
          window.history.replaceState({}, "", "/");
        }
        const currentTask = taskHistory.tasks.find(
          (task) => task.thread_id === currentThreadId,
        );
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
                displayName: coordinates.name,
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
        if (!active) return;
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
  }, [catalogRefreshKey, user.user_id]);

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
    void refreshAgentCatalog();
    const refresh = () => { if (document.visibilityState === "visible") void refreshAgentCatalog(); };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [refreshAgentCatalog]);

  const availableTaskAgents = useMemo(() => taskAgents, [taskAgents]);

  useEffect(() => {
    if (runStream.threadId === threadId && runStream.runId) {
      setCurrentThreadState("durable");
    }
  }, [runStream.runId, runStream.threadId, threadId]);

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
    const storage = createUserScopedStorage(window.localStorage, user.user_id);
    const nextThreadId = createNewThread(storage);
    bindThreadAgent(storage, nextThreadId, nextAgent);
    setSelectedAgent(nextAgent);
    setThreadId(nextThreadId);
    setCurrentTaskTitle("新任务");
    setCurrentThreadState("empty");
    setModelRouteOverride(null);
    setActiveSkillLaunch(null);
    closeCompactTaskSidebar();
  }, [closeCompactTaskSidebar, user.user_id]);

  const startTaskWithAgent = useCallback((nextAgent: TaskAgent) => {
    const launchMode = resolveTaskLaunchMode(currentThreadState, "select-agent");
    if (launchMode === "reuse-current") {
      const storage = createUserScopedStorage(window.localStorage, user.user_id);
      bindThreadAgent(storage, threadId, nextAgent);
      setSelectedAgent(nextAgent);
      setModelRouteOverride(null);
      focusTaskComposer();
      return;
    }
    createTaskWithAgent(nextAgent);
  }, [createTaskWithAgent, currentThreadState, focusTaskComposer, threadId, user.user_id]);

  useEffect(() => {
    if (!threadId || !runView?.runId) return;
    let active = true;
    void loadTasks().then((tasks) => {
      const current = tasks.find((task) => task.thread_id === threadId);
      if (active && current) setCurrentTaskTitle(current.title);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [threadId, runView?.runId, runView?.phase]);

  const startTaskInProject = useCallback((projectTask: TaskSummary) => {
    const projectAgent =
      taskAgents.find(
        (agent) =>
          agent.name === projectTask.agent_name &&
          agent.version === projectTask.agent_version &&
          agent.ownerUserId === projectTask.agent_owner_user_id &&
          agent.spaceId === (projectTask.space_id ?? undefined),
      ) ?? {
        name: projectTask.agent_name,
        version: projectTask.agent_version,
        displayName: projectTask.agent_name,
        domain: "historical" as const,
        ownerUserId: projectTask.agent_owner_user_id,
        scope: projectTask.space_id
          ? ("team" as const)
          : ("personal" as const),
        spaceId: projectTask.space_id ?? undefined,
      };
    startTaskWithAgent(currentSystemAssistant(projectAgent, systemAssistant) ?? projectAgent);
  }, [startTaskWithAgent, taskAgents, systemAssistant]);

  const startNewTask = useCallback(() => {
    const candidate = selectedAgent && taskAgents.some(
      (agent) => agentItemKey(agent) === agentItemKey(selectedAgent),
    )
      ? selectedAgent
      : taskAgents[0];
    const nextAgent = currentSystemAssistant(candidate ?? null, systemAssistant);
    if (!nextAgent) return;
    const launchMode = resolveTaskLaunchMode(currentThreadState, "new-task");
    if (launchMode === "focus-current") {
      if (selectedAgent && nextAgent.version !== selectedAgent.version) {
        bindThreadAgent(taskStorage(), threadId, nextAgent);
        setSelectedAgent(nextAgent);
      }
      focusTaskComposer();
      return;
    }
    createTaskWithAgent(nextAgent);
  }, [createTaskWithAgent, currentThreadState, focusTaskComposer, selectedAgent, taskAgents, systemAssistant, threadId]);

  function switchTask(task: TaskSummary) {
    const nextAgent =
      taskAgents.find(
        (agent) =>
          agent.name === task.agent_name && agent.version === task.agent_version &&
          agent.ownerUserId === task.agent_owner_user_id &&
          agent.spaceId === (task.space_id ?? undefined),
      ) ?? {
        name: task.agent_name,
        version: task.agent_version,
        displayName: task.agent_name,
        domain: "historical",
        ownerUserId: task.agent_owner_user_id,
        scope: task.space_id ? "team" : "personal",
        spaceId: task.space_id ?? undefined,
      };
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
    setActiveSkillLaunch(null);
    setThreadId(selectThread(storage, task.thread_id));
    closeCompactTaskSidebar();
  }

  function switchAgent(nextAgent: TaskAgent) {
    const mode = taskAgentSwitchMode(selectedAgent, nextAgent);
    if (mode === "current" || (mode === "version" && currentTaskBusy)) return;
    if (mode === "version") {
      bindThreadAgent(taskStorage(), threadId, nextAgent);
      setSelectedAgent(nextAgent);
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
      className={`console-shell${taskRailOpen ? " is-rail-open" : ""}`}
      id="main-content"
      data-task-thread-state={currentThreadState}
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
          currentThreadId={threadId}
          collapsed={!taskSidebarOpen}
          overlayOpen={compactTaskSidebar && taskSidebarOpen}
          onToggle={() => {
            setTaskSidebarOpen((current) => !current);
          }}
          onSelect={switchTask}
          onNewTask={startNewTask}
          onNewTaskWithProject={startTaskInProject}
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
              <TaskContextBar taskTitle={currentTaskTitle} agent={selectedAgent} />
              {selectedAgent && selectedAgent.name !== "lead-agent" && (
                <TaskAgentSwitcher kind="version" agents={availableTaskAgents} selected={selectedAgent} loading={agentsLoading}
                  currentTaskBusy={currentTaskBusy} onChange={switchAgent} onRefresh={refreshAgentCatalog} />
              )}
            </div>
            <HeaderUtilities
              taskRailOpen={taskRailOpen}
              onToggleTaskRail={() => {
                setTaskRailOpen((current) => !current);
              }}
            />
          </header>
          <section className="chat-stage" aria-label="Agent 任务对话">
            <div className="chat-surface">
              {selectedAgent && systemAssistant && currentSystemAssistant(selectedAgent, systemAssistant) === systemAssistant
                && selectedAgent.version !== systemAssistant.version && (
                <div className="system-assistant-upgrade" role="status">
                  <span>当前对话使用旧版系统助手。新版支持公开联网和平台技能。</span>
                  <button type="button" disabled={currentTaskBusy} onClick={() => {
                    bindThreadAgent(taskStorage(), threadId, systemAssistant);
                    setSelectedAgent(systemAssistant);
                  }}>升级并继续此对话</button>
                </div>
              )}

              {threadId && selectedAgent ? (
                <AssistantRuntimeShell
                  key={`${threadId}:${agentItemKey(selectedAgent)}`}
                  threadId={threadId}
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
                    agents={availableTaskAgents}
                    selectedAgent={selectedAgent}
                    agentsLoading={agentsLoading}
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
            </div>
          </section>
        </div>
        <WorkbenchRail
          key={threadId}
          open={taskRailOpen}
          onClose={() => setTaskRailOpen(false)}
          taskTitle={currentTaskTitle}
          agentDisplay={selectedAgent?.displayName ?? selectedAgent?.name ?? "—"}
          agentKey={selectedAgent ? `${selectedAgent.name}@${selectedAgent.version}` : "—"}
          agentScope={selectedAgent?.scope ?? "personal"}
          modelRoute={modelRouteOverride ?? selectedAgent?.modelRoute ?? null}
          runPhase={runView?.phase ?? null}
          threadId={threadId}
        />
      </div>
    </main>
  );
}
