import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");
const styles = readFileSync(join(process.cwd(), "src/app/styles.css"), "utf8");
const codexStyles = readFileSync(
  join(process.cwd(), "src/app/codex-theme.css"),
  "utf8",
);
const webCodexStyles = readFileSync(
  join(process.cwd(), "src/app/web-codex.css"),
  "utf8",
);
const loginStyles = readFileSync(
  join(process.cwd(), "src/app/login-kimi.css"),
  "utf8",
);
const taskSidebar = readFileSync(
  join(process.cwd(), "src/components/task-sidebar.tsx"),
  "utf8",
);
const studioSidebar = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/studio-sidebar.tsx",
  ),
  "utf8",
);
const studioSidebarStyles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/studio-sidebar.module.css",
  ),
  "utf8",
);
const workspaceNavigation = readFileSync(
  join(process.cwd(), "src/components/workspace-navigation.tsx"),
  "utf8",
);
const workspaceNavigationStyles = readFileSync(
  join(process.cwd(), "src/components/workspace-navigation.module.css"),
  "utf8",
);
const agentThread = readFileSync(
  join(process.cwd(), "src/components/agent-thread.tsx"),
  "utf8",
);
const videoGeneration = readFileSync(
  join(process.cwd(), "src/components/video-generation.tsx"),
  "utf8",
);
const activitySummary = readFileSync(
  join(process.cwd(), "src/components/activity-summary.tsx"),
  "utf8",
);
const commandCenter = readFileSync(
  join(process.cwd(), "src/components/productivity-command-center.tsx"),
  "utf8",
);
const contextRecovery = readFileSync(
  join(process.cwd(), "src/components/context-recovery-panel.tsx"),
  "utf8",
);
const login = readFileSync(
  join(process.cwd(), "src/app/login/page.tsx"),
  "utf8",
);

describe("full-page agent workbench", () => {
  it("keeps copy feedback visually hidden inside fixed-size message actions", () => {
    expect(styles).toMatch(
      /\.message-copy-status\s*\{[^}]*position:\s*absolute;[^}]*clip-path:\s*inset\(50%\);[^}]*white-space:\s*nowrap;/s,
    );
    expect(styles).toMatch(
      /\.assistant-message-copy\s*\{[^}]*min-width:\s*30px;[^}]*flex:\s*0 0 30px;/s,
    );
    expect(styles).toMatch(
      /\.user-message-action\s*\{[^}]*min-width:\s*28px;[^}]*flex:\s*0 0 28px;/s,
    );
  });

  it("keeps user edits aligned and replaces speech with durable answer feedback", () => {
    expect(agentThread).toContain('className="user-message-edit-shell"');
    expect(agentThread).not.toContain('className="user-message-edit-measure"');
    expect(styles).toMatch(
      /\.user-message-edit-shell\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*none;[^}]*align-self:\s*stretch;/s,
    );
    expect(styles).toMatch(/\.user-message-editor-input\s*\{[^}]*resize:\s*none;/s);
    expect(styles).toMatch(
      /textarea\.user-message-editor-input:focus-visible\s*\{[^}]*outline:\s*none;[^}]*outline-offset:\s*0;[^}]*border-color:\s*transparent;[^}]*box-shadow:\s*none;/s,
    );
    expect(agentThread).not.toContain("<AssistantActionBar.FeedbackPositive");
    expect(agentThread).not.toContain("<AssistantActionBar.FeedbackNegative");
    expect(agentThread).toContain("allowFeedbackPositive: false");
    expect(agentThread).toContain("allowFeedbackNegative: false");
    expect(agentThread).toContain('aria-label="赞同回答"');
    expect(agentThread).toContain('aria-label="不赞同回答"');
    expect(agentThread).toContain("/feedback`");
    expect(agentThread).toContain("allowSpeak: false");
    expect(agentThread).not.toContain("<AssistantActionBar.SpeechControl");
    expect(agentThread).not.toContain("朗读回答");
    expect(styles).toMatch(
      /\.assistant-feedback-actions\s*\{[^}]*align-items:\s*center;[^}]*gap:\s*2px;/s,
    );
    expect(styles).toMatch(
      /\.assistant-message-feedback\s*\{[^}]*width:\s*30px;[^}]*height:\s*30px;[^}]*place-items:\s*center;/s,
    );
  });

  it("uses a neutral Codex-style stop control and reserves recovery UI for failures", () => {
    expect(codexStyles).toMatch(
      /\.harness-composer-shell \.aui-composer-cancel\s*\{[^}]*width:\s*40px;[^}]*height:\s*40px;[^}]*margin:\s*0;[^}]*flex:\s*0 0 40px;[^}]*border-radius:\s*999px;[^}]*background:\s*var\(--codex-ink-soft\);[^}]*box-shadow:\s*none;/s,
    );
    expect(codexStyles).toMatch(
      /\.harness-composer-shell \.aui-composer-cancel svg\s*\{[^}]*width:\s*20px;[^}]*height:\s*20px;/s,
    );
    expect(codexStyles).not.toMatch(
      /\.harness-composer-shell \.aui-composer-cancel\s*\{[^}]*background:\s*var\(--codex-accent\);/s,
    );
    expect(codexStyles).toMatch(
      /\.harness-composer-shell \.aui-composer-cancel:hover\s*\{[^}]*color:\s*#ffffff;[^}]*background:\s*var\(--codex-danger\);/s,
    );
    expect(codexStyles).toMatch(
      /\.aui-message-error\s*\{[^}]*color:\s*var\(--codex-faint\);[^}]*background:\s*transparent;/s,
    );
    expect(codexStyles).toMatch(
      /\.aui-message-error \.run-retry-button\s*\{[^}]*border:\s*0;[^}]*background:\s*transparent;/s,
    );
  });

  it("uses a compact project icon language in the task tree", () => {
    expect(taskSidebar).toContain("function ProjectFolderIcon({ open = false }");
    expect(taskSidebar).toContain("<ProjectFolderIcon />");
    expect(taskSidebar).toContain('<ProjectFolderIcon open={!projectCollapsed} />');
    expect(webCodexStyles).toMatch(
      /\.task-project-heading svg,[\s\S]*?stroke-width:\s*1\.6;/s,
    );
  });

  it("presents a user task workspace instead of an internal validation console", () => {
    expect(taskSidebar).toContain("<ProductBrandMark");
    expect(taskSidebar).toContain("{PRODUCT_NAME}");
    expect(taskSidebar).toContain('className="task-sidebar-collapse"');
    expect(taskSidebar).not.toContain('className="task-rail-brand-text"');
    expect(page).not.toContain("<TaskAgentSwitcher");
    expect(agentThread).toContain("<TaskAgentSwitcher");
    expect(page).toContain('className="task-context-bar"');
    expect(page).toContain("<SidebarPanelToggle");
    expect(page).toContain("<HelpMenu />");
    expect(page).toContain("<WorkbenchRail");
    expect(page).not.toContain("EnvironmentBadge");
    expect(page).not.toContain("<ContextRecoveryPanel");
    expect(page).not.toContain('className="brand-lockup"');
    expect(page).toContain('className="task-content-shell"');
    expect(styles).toMatch(
      /\.task-content-shell\s*\{[^}]*grid-template-rows:\s*auto minmax\(0,\s*1fr\);/s,
    );
    expect(page).not.toContain("Agent Harness");
    expect(page).not.toContain('<span>新任务</span>');
    expect(page).not.toContain("LangfuseTraceLink");
    expect(page).not.toContain("Langfuse Trace");
    expect(page).not.toContain("<DeveloperDrawer");
    expect(page).not.toContain("交互验证台");
    expect(page).not.toContain("切换开发者信息");
    expect(page).not.toContain("developerMode");
    expect(agentThread).toContain("<h1>开始一个新任务</h1>");
    expect(agentThread).not.toContain("<h2>从一个任务开始</h2>");
    expect(styles).toContain(".user-task-intro h1");
    expect(styles).not.toContain(".user-task-intro h2");
    expect(codexStyles).toContain(".user-task-intro h1");
    expect(codexStyles).not.toContain(".user-task-intro h2");
  });

  it("keeps activity in the conversation without mounting the developer drawer", () => {
    expect(page).not.toContain("<RunDetailsProvider");
    expect(page).not.toContain("activity={inspectedActivity}");
    expect(page).not.toContain('inspectedActivity ? " inspector-open" : ""');
    expect(agentThread).toContain("<ActivitySummary");
    expect(agentThread).toContain("可打开“运行详情”查看原因");
    expect(activitySummary).toContain('className="execution-details-trigger"');
    expect(activitySummary).toContain("runDetails.open(activity)");
    expect(activitySummary).toContain('aria-controls="run-details-panel"');
    expect(styles).toContain(".execution-details-trigger");
    expect(page).not.toContain("<DeveloperDrawer");
  });

  it("keeps collapse in the rail and moves expand into the header", () => {
    expect(page).not.toContain('className="icon-button task-sidebar-toggle"');
    expect(page).toContain("collapsed={!taskSidebarOpen}");
    expect(taskSidebar).toContain('className="task-sidebar-rail"');
    expect(taskSidebar).not.toContain('task-rail-toggle');
    expect(taskSidebar).not.toContain('task-rail-action');
    expect(taskSidebar).not.toContain('task-rail-brand');
    expect(taskSidebar).not.toContain('task-rail-account');
    expect(taskSidebar).toContain("<SidebarCollapseIcon />");
    expect(taskSidebar).not.toContain('SidebarExpandIcon');
    expect(page).toContain("<SidebarExpandToggle");
    expect(page).toContain("!taskSidebarOpen && (");
    expect(styles).toMatch(
      /\.workspace-stage:not\(\.tasks-open\)\s*\{[^}]*grid-template-columns:\s*var\(--app-sidebar-collapsed-width,\s*52px\) minmax\(0,\s*1fr\);/s,
    );
    expect(codexStyles).toContain("--app-sidebar-expanded-width: 264px");
    expect(codexStyles).toContain(
      "grid-template-columns: var(--app-sidebar-expanded-width) minmax(0, 1fr)",
    );
  });

  it("treats the compact task navigator as a dismissible modal drawer", () => {
    expect(page).toContain(
      'const TASK_SIDEBAR_COMPACT_QUERY = "(max-width: 820px)"',
    );
    expect(page).toContain('compactViewport.addEventListener("change"');
    expect(page).toContain('compactViewport.removeEventListener("change"');
    expect(page).toContain('className="task-sidebar-scrim"');
    expect(page).toContain('aria-label="关闭任务列表"');
    expect(page).toContain('overlayOpen={compactTaskSidebar && taskSidebarOpen}');
    expect(page).toContain("closeCompactTaskSidebar();");
    expect(taskSidebar).toContain("useDialogFocus({");
    expect(taskSidebar).toContain('role={overlayOpen ? "dialog" : undefined}');
    expect(taskSidebar).toContain('aria-modal={overlayOpen ? true : undefined}');
    expect(taskSidebar).toContain('data-task-sidebar-overlay={overlayOpen ? "true" : undefined}');
    expect(taskSidebar).toContain("expandButtonRef.current?.focus()");
    expect(styles).toMatch(
      /@media \(max-width: 820px\)[\s\S]*?\.task-sidebar\s*\{[^}]*z-index:\s*71;[\s\S]*?\.task-sidebar-scrim\s*\{[^}]*z-index:\s*70;[^}]*display:\s*block;[^}]*background:\s*rgb\(8 12 10 \/ 42%\);/s,
    );
  });

  it("keeps agents and capabilities in the simplified task sidebar", () => {
    expect(taskSidebar).not.toContain("WorkspaceModeSwitcher");
    expect(studioSidebar).not.toContain("WorkspaceModeSwitcher");
    expect(taskSidebar).toContain('visible={["agents", "capabilities"]}');
    expect(taskSidebar).toContain('labelOverrides={{ capabilities: "技能 / MCP" }}');
    expect(taskSidebar).not.toContain('visible={["agents", "files"]}');
    expect(studioSidebar).toContain('visible={["tasks", "agents"]}');
    expect(studioSidebar).not.toContain('"capabilities", "knowledge", "spaces"]');
    expect(studioSidebar).not.toContain('"usage",');
    expect(studioSidebar).not.toContain('"data",');
    expect(workspaceNavigation).not.toContain('aria-label="工作模式"');
    for (const [href, label] of [
      ["/", "任务"],
      ["/studio/agents", "智能体"],
      ["/studio/capabilities", "MCP 能力"],
      ["/studio/knowledge", "知识库"],
      ["/studio/skills", "技能"],
    ]) {
      expect(workspaceNavigation).toContain(`href: "${href}"`);
      expect(workspaceNavigation).toContain(`label: "${label}"`);
    }
    expect(workspaceNavigation).not.toContain('href: "/studio/spaces"');
    expect(workspaceNavigation).not.toContain('label: "协作空间"');
    expect(workspaceNavigation).not.toContain('href: "/studio/usage"');
    expect(workspaceNavigation).not.toContain('href: "/studio/data"');
    expect(workspaceNavigation).not.toContain("workspaceGroupLabels");
    expect(workspaceNavigation).toContain("{items.map(renderWorkspaceLink)}");
    expect(workspaceNavigationStyles).toContain(".navigationActive");
    expect(workspaceNavigationStyles).not.toContain(".modeSwitcher");
    expect(workspaceNavigationStyles).not.toContain(".navigationGroup");
    expect(workspaceNavigationStyles).toContain(
      '[data-workspace-navigation="collapsed"]',
    );
  });

  it("keeps task creation and recent tasks in the task-mode context", () => {
    expect(taskSidebar).toContain('className="task-sidebar-primary"');
    expect(taskSidebar).toContain("<span>新建任务</span>");
    expect(taskSidebar).toContain('className="task-list-heading"');
    expect(taskSidebar).not.toContain('role="tablist" aria-label="任务范围"');
    expect(taskSidebar).not.toContain("已归档");
    expect(taskSidebar).not.toContain('aria-label="搜索最近任务"');
    expect(taskSidebar).toContain("project.tasks.slice(0, 5)");
    expect(taskSidebar).toContain('className="task-list-archive"');
    expect(taskSidebar).toContain("setTaskArchived");
    expect(taskSidebar).toContain("loadTasks(false)");
    expect(taskSidebar).not.toContain("恢复并打开任务");
    expect(taskSidebar).toContain('className="task-sidebar-brand"');
    expect(styles).toContain(".task-sidebar-primary");
    expect(styles).toContain(".task-list-heading");
    expect(styles).toContain(".task-list-search");
    expect(taskSidebar).toContain("正在读取任务");
    expect(taskSidebar).toContain("从第一个任务开始");
    expect(taskSidebar).toContain("重新加载");
  });

  it("keeps task search and context recovery readable in both color modes", () => {
    expect(styles).toMatch(
      /\.task-list-search\s*\{[^}]*border:\s*1px solid var\(--codex-line,[^}]*color:\s*var\(--codex-muted,[^}]*background:\s*var\(--codex-field,/s,
    );
    expect(styles).toMatch(
      /\.task-list-search input::placeholder\s*\{[^}]*color:\s*var\(--codex-muted,/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.task-list-state\s*\{[^}]*border-color:\s*var\(--codex-line\);[^}]*color:\s*var\(--codex-muted\);[^}]*background:\s*var\(--codex-surface-subtle\);/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.task-list-state strong\s*\{[^}]*color:\s*var\(--codex-ink-soft\);/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.task-list-state button\s*\{[^}]*border-color:\s*var\(--codex-line-strong\);[^}]*background:\s*var\(--codex-surface-raised\);/s,
    );
    expect(styles).toMatch(
      /\.context-recovery-panel\s*\{[^}]*--paper:\s*var\(--codex-surface,[^}]*--ink:\s*var\(--codex-ink,[^}]*color-scheme:\s*inherit;/s,
    );
    expect(styles).not.toMatch(
      /\.context-recovery-panel\s*\{[^}]*color-scheme:\s*light;/s,
    );
    expect(styles).toMatch(
      /\.context-window-card\s*\{[^}]*background:\s*var\(--surface\);/s,
    );
  });

  it("keeps the compact context trigger named and the modal focus-contained", () => {
    expect(contextRecovery).toContain('aria-label="上下文与恢复点"');
    expect(contextRecovery).toContain('aria-controls="context-recovery-panel"');
    expect(contextRecovery).toContain('id="context-recovery-panel"');
    expect(contextRecovery).toContain("useDialogFocus({");
    expect(contextRecovery).toContain("panelRef,");
    expect(contextRecovery).toContain("initialFocusRef: closeButtonRef");
    expect(contextRecovery).toContain("onEscape: () => setOpen(false)");
    expect(contextRecovery).not.toContain('window.addEventListener("keydown", closeOnEscape)');
  });

  it("projects task approvals into the composer surface", () => {
    expect(taskSidebar).toContain("approvalStore.reset(currentThreadId)");
    expect(taskSidebar).toContain(
      "approvalStore.show(selected.pending_approval, selected.thread_id)",
    );
    expect(taskSidebar).toContain('runView?.phase !== "waiting_approval"');
    expect(taskSidebar).toContain("[runView?.phase, selected]");
    expect(taskSidebar).not.toContain("task-approval-panel");
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.aui-thread-viewport-footer\s*\{[^}]*height:\s*auto;[^}]*flex:\s*0 0 auto;/s,
    );
  });

  it("keeps the conversation runtime mounted while task status changes", () => {
    expect(page).toContain(
      'key={`${threadId}:${agentItemKey(selectedAgent)}`}',
    );
    expect(page).not.toContain("refreshToken");
    expect(taskSidebar).not.toContain("onCurrentTaskStatusChange");
    expect(taskSidebar).not.toContain("currentStatusRef");
  });

  it("keeps an active task pinned while allowing another Agent to start a new task", () => {
    expect(page).toContain('runStream.status === "running"');
    expect(page).toContain('runView?.phase === "waiting_approval"');
    expect(page).toContain("taskAgentSwitchMode(selectedAgent, nextAgent)");
    expect(page).toContain('mode === "version" && currentTaskBusy');
    expect(page).toContain("currentTaskBusy={currentTaskBusy}");
  });

  it("does not orphan an unsent draft behind a task with no history", () => {
    expect(page).toContain('useState<TaskThreadState>("unknown")');
    expect(page).toContain("data-task-thread-state={currentThreadState}");
    expect(page).toContain('resolveTaskLaunchMode(currentThreadState, "new-task")');
    expect(page).toContain('resolveTaskLaunchMode(currentThreadState, "select-agent")');
    expect(page).toContain('document.querySelector<HTMLTextAreaElement>(".aui-composer-input")?.focus()');
    expect(page).toContain('setCurrentThreadState("durable")');
    expect(page).toContain('setCurrentThreadState("empty")');
    expect(page).toContain("startTaskWithAgent(nextAgent);");
  });

  it("restores the stored task and Agent before background catalog validation", () => {
    expect(page).toContain("const restoredBinding = hasRequestedAgent");
    expect(page).toContain("if (hasRequestedAgent && !requestedAgent)");
    expect(page).toContain("指定的智能体版本不可用");
    expect(page).toContain("loadThreadAgent(storage, initialThreadId)");
    expect(page).toContain("setSelectedAgent(restoredAgent)");
    expect(page).toContain("setTaskAgents([restoredAgent])");
    expect(page.indexOf("setSelectedAgent(restoredAgent)")).toBeLessThan(
      page.indexOf("async function loadAgentBinding()"),
    );
  });

  it("keeps run status disclosures visually inert on hover", () => {
    expect(codexStyles).toContain(
      ".execution-disclosure:is(:hover, :active)",
    );
    expect(codexStyles).toContain(
      ".aui-assistant-message-content:hover",
    );
    expect(codexStyles).toContain("transition: none");
  });

  it("removes the decorative live rail and hard-coded agent coordinate", () => {
    expect(page).not.toContain("run-rail");
    expect(page).not.toContain(">LIVE<");
    expect(page).not.toContain("echo-agent");
    expect(page).not.toContain("0.4.0");
  });

  it("uses a dense centered conversation and compact sticky controls", () => {
    expect(styles).toMatch(
      /\.chat-stage\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/s,
    );
    expect(styles).toMatch(
      /\.aui-thread-root\s*\{[^}]*--aui-thread-max-width:\s*50rem;/s,
    );
    expect(styles).toMatch(/\.console-header\s*\{[^}]*min-height:\s*52px;/s);
    expect(styles).toMatch(
      /\.aui-thread-viewport-footer\s*\{[^}]*position:\s*sticky;/s,
    );
    expect(styles).not.toContain("linear-gradient");
  });

  it("uses a quiet task welcome without suggestion cards or shortcuts", () => {
    expect(styles).toMatch(
      /\.user-task-welcome\s*\{[^}]*max-width:\s*46rem;/s,
    );
    expect(styles).toMatch(
      /\.user-task-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\);/s,
    );
    expect(styles).toMatch(
      /@media\s*\(max-width:\s*680px\)[\s\S]*?\.user-task-grid\s*\{[^}]*grid-template-columns:\s*1fr;/s,
    );
    expect(agentThread).not.toContain('aria-label="生产力快捷入口"');
    expect(agentThread).not.toContain("从团队空间选择智能体");
    expect(agentThread).not.toContain("创建或调整智能体");
  });

  it("presents approvals as an exceptional risk boundary", () => {
    expect(agentThread).not.toContain("常规操作自动完成");
    expect(agentThread).not.toContain("隔离执行 · 自动风险分级");
    expect(agentThread).not.toContain("支持人工审批");
    expect(login).toContain("数据与安全策略");
    expect(login).not.toContain("继续处理你的任务与审批");
  });

  it("uses a responsive workbench login frame", () => {
    expect(login).toContain('className="login-workspace-preview"');
    expect(login).toContain('className="login-system-status"');
    expect(login).toContain('className="login-card-banner"');
    expect(login).toContain('className="login-card-body"');
    expect(login).toContain('className="login-card-footer"');
    expect(login).toContain('import alpinePeak from "./assets/alpine-peak.jpg"');
    expect(login).toContain('aria-label="阿尔卑斯雪峰"');
    expect(login).toContain("服务条款");
    expect(login).toContain("隐私保护");
    expect(login).not.toContain('className="login-context"');
    expect(loginStyles).toMatch(
      /Kimi-inspired login:[\s\S]*?body\.codex-theme-v1 \.login-card,[\s\S]*?width:\s*min\(100%, 880px\);/s,
    );
    expect(loginStyles).toMatch(/@media \(max-width:\s*780px\)[\s\S]*?\.login-card-body\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/s);
  });

  it("keeps the run headline icon-free and rotates only active thought content", () => {
    expect(activitySummary).not.toContain('className="execution-summary-icon"');
    expect(activitySummary).toContain('data-active={active ? "true" : "false"}');
    expect(codexStyles).toMatch(
      /\.execution-commentary\[data-active="true"\][\s\S]*?\.execution-commentary-icon\s*\{[^}]*animation:\s*execution-thinking-spin 1\.35s linear infinite;/s,
    );
  });

  it("does not duplicate terminal status and existing navigation below answers", () => {
    expect(agentThread).not.toContain('aria-label="任务完成后的操作"');
    expect(agentThread).not.toContain("task-completion-panel");
    expect(styles).not.toContain(".task-completion-panel");
    expect(styles).not.toContain(".task-completion-actions");
  });

  it("provides a keyboard command center for core productivity paths", () => {
    expect(page).toContain("<ProductivityCommandCenter");
    expect(commandCenter).toContain('event.key.toLocaleLowerCase() === "k"');
    expect(commandCenter).toContain('role="dialog"');
    expect(commandCenter).toContain('role="combobox"');
    expect(commandCenter).toContain('role="listbox"');
    expect(commandCenter).toContain("onStartWithAgent");
    expect(commandCenter).not.toContain("/studio/data");
    expect(commandCenter).not.toContain("/studio/usage");
    expect(styles).toContain(".command-center-dialog");
  });

  it("preserves unsent task text across refreshes and task switches", () => {
    expect(page).toContain("<AgentThread");
    expect(page).toContain("threadId={threadId}");
    expect(agentThread).toContain("loadTaskComposerDraft");
    expect(agentThread).toContain("persistTaskComposerDraft");
    expect(agentThread).not.toContain("未发送内容已保存在当前浏览器");
    expect(agentThread).toContain("auiRef.current.composer().setText(saved)");
  });

  it("uses Codex neutrals and keeps the workbench strictly black, white and gray", () => {
    expect(webCodexStyles).toContain("body.codex-theme-v1:has(.console-shell)");
    expect(webCodexStyles).toContain("--codex-accent: #ececec");
    expect(webCodexStyles).toContain(".harness-composer-shell .task-agent-switcher");
    expect(webCodexStyles).toContain(".aui-thread-viewport:has(.user-task-welcome)");
    expect(webCodexStyles).toContain("width: 100%;\n  height: 100dvh;");
    expect(webCodexStyles).not.toContain("background-image: radial-gradient");
    expect(webCodexStyles).not.toContain("#339cff");
    expect(webCodexStyles).not.toContain("#16b364");
    expect(webCodexStyles).not.toContain("#f36d21");
    expect(webCodexStyles).not.toContain("#ff7d32");
  });

  it("runs managed video models through a dedicated conversation composer path", () => {
    expect(agentThread).toContain('route.modelType === "video_generation"');
    expect(videoGeneration).toContain("/videos`");
    expect(videoGeneration).toContain("inputArtifactIds");
    expect(videoGeneration).toContain("自定义时长（4–15 秒）");
    expect(videoGeneration).toContain("下载 MP4");
    expect(videoGeneration).toContain("重新生成");
    expect(videoGeneration).toContain("沿用参数");
    expect(videoGeneration).toContain("<video");
    expect(styles).toContain(".composer-video-settings");
    expect(styles).toContain(".video-answer-card");
    expect(styles).toContain(".aui-composer-video-send");
  });

  it("removes obsolete custom thread layout rules superseded by assistant-ui", () => {
    for (const selector of [
      ".aui-welcome {",
      ".aui-message-list {",
      ".aui-message {",
      ".aui-viewport-footer {",
      ".aui-composer {",
    ]) {
      expect(styles).not.toContain(selector);
    }
    expect(styles).toContain(".aui-thread-root");
    expect(styles).toContain(".aui-composer-root");
  });

  it("renders completed execution as a quiet Codex-style processed row", () => {
    expect(styles).toMatch(
      /\.execution-ribbon\.phase-completed\s*\{[^}]*border:\s*0;[^}]*background:\s*transparent;[^}]*opacity:\s*1;/s,
    );
    expect(styles).toMatch(
      /\.execution-ribbon\.phase-completed \.execution-state-mark,[\s\S]*?display:\s*none;/s,
    );
    expect(styles).toMatch(
      /\.execution-disclosure\s*\{[^}]*border-bottom:\s*1px solid #e2e4e1;/s,
    );
  });

  it("keeps the work log compact and gives each result a bounded scroll surface", () => {
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.execution-ribbon > \.execution-disclosure\s*\{[\s\S]*?min-height:\s*33px;[\s\S]*?box-sizing:\s*border-box;/s,
    );
    expect(codexStyles).toMatch(
      /\.execution-ribbon[\s\S]*?> \.execution-disclosure:is\(:hover, :active\)\s*\{[^}]*border-bottom:\s*1px solid var\(--codex-line\);[^}]*color:\s*var\(--codex-muted\);[^}]*background:\s*transparent;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.execution-phase\s*\{[^}]*font-size:\s*13px;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.execution-log\s*\{[^}]*gap:\s*11px;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.execution-action-summary,[\s\S]*?width:\s*100%;[\s\S]*?grid-template-columns:\s*16px minmax\(0, 1fr\) 8px;/s,
    );
    expect(codexStyles).toMatch(
      /\.execution-action:not\(\[open\]\)[\s\S]*?> \.execution-action-body\s*\{[^}]*display:\s*none;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.execution-action-result\s*\{[^}]*max-height:\s*9\.5rem;[^}]*overflow:\s*auto;[^}]*white-space:\s*pre-wrap;[^}]*word-break:\s*break-word;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.reasoning-card > div\s*\{[^}]*max-height:\s*12rem;[^}]*overflow:\s*auto;/s,
    );
  });

  it("keeps short user messages horizontal and the composer focus treatment neutral", () => {
    expect(styles).toMatch(
      /\.aui-user-message-root\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*flex-end;/s,
    );
    expect(styles).toMatch(
      /\.aui-user-message-content\s*\{[^}]*width:\s*auto;[^}]*flex:\s*0 1 auto;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-input:focus-visible\s*\{[^}]*outline:\s*none;/s,
    );
    expect(styles).not.toContain("border-color: #d9c5a7");
  });

  it("aligns user message actions beneath the right-aligned bubble", () => {
    expect(styles).toMatch(
      /\.harness-user-message\s*\{[^}]*position:\s*relative;[^}]*padding:\s*12px 0 0;/s,
    );
    expect(styles).toMatch(
      /\.harness-user-action-bar\s*\{[^}]*position:\s*absolute;[^}]*top:\s*calc\(100% \+ 1px\);[^}]*right:\s*2px;[^}]*gap:\s*2px;/s,
    );
    expect(styles).toMatch(
      /\.harness-user-action-bar\s*\{[^}]*opacity:\s*0;[^}]*pointer-events:\s*none;/s,
    );
    expect(styles).toMatch(
      /\.harness-user-message:hover \.harness-user-action-bar,[\s\S]*?opacity:\s*1;[\s\S]*?pointer-events:\s*auto;/s,
    );
    expect(styles).toMatch(
      /\.user-message-action\s*\{[^}]*width:\s*28px;[^}]*height:\s*28px;/s,
    );
    expect(styles).toMatch(
      /\.harness-user-message \+ \.harness-assistant-message\s*\{[^}]*padding-top:\s*35px;/s,
    );
    expect(styles).toMatch(
      /\.harness-user-message \+ \.pre-response-activity\s*\{[^}]*margin-top:\s*35px;/s,
    );
  });

  it("keeps the attachment control close to the composer text", () => {
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-root\s*\{[^}]*column-gap:\s*0;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-input\s*\{[^}]*padding:\s*8px 8px 8px 2px;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-attachments\s*\{[^}]*max-width:\s*100%;[^}]*padding:\s*6px;[^}]*flex:\s*1 0 100%;[^}]*overflow-x:\s*auto;[^}]*overflow-y:\s*hidden;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-attachments:empty\s*\{[^}]*display:\s*none;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-attachments \.aui-attachment-root\s*\{[^}]*flex:\s*0 0 10rem;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-attachments \.aui-attachment-remove\s*\{[^}]*top:\s*-4px;[^}]*right:\s*-4px;/s,
    );
  });

  it("keeps the expanded Task and Studio brand marks at one stable size", () => {
    expect(codexStyles).toContain("--app-sidebar-brand-mark-size: 36px");
    expect(styles).toMatch(
      /\.task-sidebar-brand-mark\s*\{[^}]*width:\s*var\(--app-sidebar-brand-mark-size, 36px\);[^}]*height:\s*var\(--app-sidebar-brand-mark-size, 36px\);[^}]*flex:\s*0 0 var\(--app-sidebar-brand-mark-size, 36px\);/s,
    );
    expect(studioSidebarStyles).toMatch(
      /\.brandMark\s*\{[^}]*width:\s*var\(--app-sidebar-brand-mark-size, 36px\);[^}]*height:\s*var\(--app-sidebar-brand-mark-size, 36px\);[^}]*flex:\s*0 0 var\(--app-sidebar-brand-mark-size, 36px\);/s,
    );
  });

  it("shows an explicit inline editor for message reruns", () => {
    expect(styles).toMatch(
      /\.user-message-edit-shell\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*none;[^}]*align-self:\s*stretch;/s,
    );
    expect(styles).toMatch(
      /\.user-message-editor\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*100%;[^}]*border-radius:\s*16px 16px 4px 16px;[^}]*background:\s*#eceeeb;/s,
    );
    expect(styles).toMatch(
      /\.user-message-editor-actions button:last-child\s*\{[^}]*background:\s*#202522;/s,
    );
  });

  it("keeps restored per-turn processing above the answer and collapsed", () => {
    expect(styles).toMatch(
      /\.turn-activity-summary\s*\{[^}]*order:\s*-10;[^}]*width:\s*100%;/s,
    );
  });

  it("keeps generated file cards after the response text", () => {
    expect(styles).toMatch(
      /\.aui-assistant-message-content > \.artifact-domain-card\s*\{[^}]*order:\s*50;/s,
    );
  });

  it("centers composer controls on one shared vertical axis", () => {
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-root\s*\{[^}]*min-height:\s*58px;[^}]*align-items:\s*center;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-input\s*\{[^}]*min-height:\s*40px;[^}]*padding:\s*8px 8px 8px 2px;[^}]*line-height:\s*24px;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-attach,[\s\S]*?width:\s*40px;[^}]*height:\s*40px;[^}]*margin:\s*0;[^}]*align-items:\s*center;[^}]*justify-content:\s*center;/s,
    );
    expect(styles).not.toContain(".composer-meta");
  });

  it("uses a single-column assistant flow so errors cannot occupy an avatar grid cell", () => {
    expect(styles).toMatch(
      /\.aui-assistant-message-root\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*align-items:\s*stretch;/s,
    );
    expect(styles).toMatch(
      /\.aui-assistant-message-content\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*none;[^}]*margin:\s*0;/s,
    );
  });

  it("keeps assistant actions below the answer instead of floating over activity", () => {
    expect(styles).toMatch(
      /\.harness-assistant-message\s*>\s*\.aui-assistant-message-content\s*\{[^}]*order:\s*1;/s,
    );
    expect(styles).toMatch(
      /\.harness-assistant-message\s*>\s*\.assistant-message-controls\s*\{[^}]*order:\s*3;/s,
    );
    expect(styles).toMatch(
      /\.assistant-message-controls\s*\{[^}]*min-height:\s*28px;[^}]*display:\s*flex;/s,
    );
    expect(styles).toMatch(
      /\.assistant-message-controls\s*>\s*\.aui-assistant-action-bar-root\[data-floating\]\s*\{[^}]*position:\s*static;[^}]*border:\s*0;[^}]*box-shadow:\s*none;/s,
    );
  });

  it("keeps wide Markdown tables inside a keyboard-scrollable region", () => {
    const markdown = readFileSync(
      join(process.cwd(), "src/components/markdown-text.tsx"),
      "utf8",
    );
    expect(markdown).toContain('className="aui-table-scroll"');
    expect(markdown).toContain('aria-label="表格，可横向滚动"');
    expect(markdown).toContain("table: ScrollableTable");
    expect(markdown).not.toContain("defer");
    expect(markdown).toContain("smooth={false}");
    expect(markdown).toContain("preprocess={normalizeMessageText}");
    expect(markdown).not.toContain("codexStreamSmoothing");
    expect(styles).toMatch(
      /\.aui-table-scroll\s*\{[^}]*overflow-x:\s*auto;/s,
    );
    expect(styles).toMatch(
      /\.aui-table-scroll table\s*\{[^}]*width:\s*max-content;[^}]*min-width:\s*100%;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.aui-table-scroll\s*\{[^}]*min-width:\s*0;[^}]*inline-size:\s*100%;[^}]*contain:\s*inline-size;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.execution-phase\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden;[^}]*text-overflow:\s*ellipsis;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.assistant-answer \.aui-md p\s*\{[^}]*margin-block:\s*0 8px;/s,
    );
  });

  it("renders Mermaid fences as stable, theme-aware task visualizations", () => {
    const markdown = readFileSync(
      join(process.cwd(), "src/components/markdown-text.tsx"),
      "utf8",
    );
    const mermaid = readFileSync(
      join(process.cwd(), "src/components/mermaid-diagram.tsx"),
      "utf8",
    );
    expect(markdown).toContain("componentsByLanguage");
    expect(markdown).toContain("SyntaxHighlighter: MermaidDiagram");
    expect(mermaid).toContain('securityLevel: "strict"');
    expect(mermaid).toContain('suppressErrorRendering: true');
    expect(mermaid).toContain('attributeFilter: ["data-color-mode"]');
    expect(mermaid).toContain("setRendered({ code: source, svg: result.svg })");
    expect(mermaid).toContain("下载 SVG");
    expect(codexStyles).toMatch(
      /\.mermaid-canvas\s*\{[^}]*overflow:\s*auto;/s,
    );
    expect(codexStyles).toMatch(
      /\.mermaid-card\s*\{[^}]*border:\s*1px solid var\(--codex-line\);/s,
    );
  });

  it("keeps composer shadow local instead of blurring the full-width footer", () => {
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.aui-thread-viewport-footer\s*\{[^}]*background:\s*transparent;[^}]*backdrop-filter:\s*none;/s,
    );
    expect(codexStyles).toMatch(
      /body\.codex-theme-v1 \.harness-composer-shell \.aui-composer-root\s*\{[^}]*box-shadow:\s*0 7px 22px rgb\(0 0 0 \/ 11%\)/s,
    );
  });

  it("does not mount run details in the simplified task shell", () => {
    expect(page).not.toContain('inspectedActivity ? " inspector-open" : ""');
    expect(page).not.toContain("<DeveloperDrawer");
    expect(page).not.toContain("<RunDetailsProvider");
  });

  it("shows the shared brand loading state during recovery", () => {
    expect(page).toContain('<ProductLoading label="正在打开任务…"');
    expect(page).toContain("aria-busy={!agentsError}");
    expect(styles).toContain(".chat-loading-skeleton");
    expect(styles).toContain(".chat-loading-line");
    expect(page).toContain("无法进入任务工作台");
    expect(page).toContain("重新连接");
  });

  it("keeps primary touch targets at least 40px high", () => {
    expect(styles).toMatch(
      /\.quiet-button,\s*\.icon-button\s*\{[^}]*min-height:\s*40px;/s,
    );
    expect(styles).toMatch(
      /\.inspector-close\s*\{[^}]*width:\s*40px;[^}]*height:\s*40px;/s,
    );
    expect(styles).toMatch(
      /\.harness-composer-shell \.aui-composer-attach,[\s\S]*?min-height:\s*40px;/s,
    );
    expect(styles).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?button,\s*summary\s*\{[^}]*min-height:\s*40px !important;/s,
    );
    expect(styles).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?\.preview-button,\s*\.download-button\s*\{[^}]*min-height:\s*40px;/s,
    );
  });
});
