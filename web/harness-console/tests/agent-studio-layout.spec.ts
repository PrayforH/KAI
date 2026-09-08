import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  join(process.cwd(), "src/app/studio/agents/page.tsx"),
  "utf8",
);
const studioLayout = readFileSync(
  join(process.cwd(), "src/app/studio/layout.tsx"),
  "utf8",
);
const workbench = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-studio-workbench.tsx",
  ),
  "utf8",
);
const operationsWorkspace = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-operations-workspace.tsx",
  ),
  "utf8",
);
const builderOverlays = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-builder-overlays.tsx",
  ),
  "utf8",
);
const builderOverlayStyles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-builder-overlays.module.css",
  ),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/agent-studio/agent-studio.module.css"),
  "utf8",
);
const appStyles = readFileSync(
  join(process.cwd(), "src/app/styles.css"),
  "utf8",
);
const codeEditor = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/studio-code-editor.tsx",
  ),
  "utf8",
);
const codeEditorStyles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/studio-code-editor.module.css",
  ),
  "utf8",
);
const skillConversationBuilder = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/skill-conversation-builder.tsx",
  ),
  "utf8",
);
const skillConversationStyles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/skill-conversation-builder.module.css",
  ),
  "utf8",
);
const skillsCatalog = readFileSync(
  join(process.cwd(), "src/components/agent-studio/skills-catalog-page.tsx"),
  "utf8",
);
const skillCreatorLaunch = readFileSync(
  join(process.cwd(), "src/lib/skill-creator-launch.ts"),
  "utf8",
);
const homePage = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");
const agentThread = readFileSync(
  join(process.cwd(), "src/components/agent-thread.tsx"),
  "utf8",
);
const sidebar = readFileSync(
  join(process.cwd(), "src/components/agent-studio/studio-sidebar.tsx"),
  "utf8",
);
const productBrand = readFileSync(
  join(process.cwd(), "src/components/product-brand.tsx"),
  "utf8",
);
const sidebarStyles = readFileSync(
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
const studioConfig = readFileSync(
  join(process.cwd(), "src/lib/agent-studio.ts"),
  "utf8",
);
const studioClient = readFileSync(
  join(process.cwd(), "src/lib/studio-client.ts"),
  "utf8",
);
const triggerControlPlane = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-trigger-control-plane.tsx",
  ),
  "utf8",
);
const triggerStyles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-trigger-control-plane.module.css",
  ),
  "utf8",
);
const a2aPage = readFileSync(
  join(process.cwd(), "src/app/studio/agents/[agentName]/a2a/page.tsx"),
  "utf8",
);
const a2aWorkspace = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-a2a-workspace.tsx",
  ),
  "utf8",
);
const a2aStyles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-a2a-workspace.module.css",
  ),
  "utf8",
);
const mcpCatalogStyles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/mcp-catalog-control-plane.module.css",
  ),
  "utf8",
);
const codexTheme = readFileSync(
  join(process.cwd(), "src/app/codex-theme.css"),
  "utf8",
);
const nextConfig = readFileSync(join(process.cwd(), "next.config.ts"), "utf8");
const environmentPolicyControlPlane = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/environment-policy-control-plane.tsx",
  ),
  "utf8",
);
const environmentPolicyStyles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/environment-policy-control-plane.module.css",
  ),
  "utf8",
);
const governanceControlPlane = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/governance-control-plane.tsx",
  ),
  "utf8",
);
const errorBoundary = readFileSync(
  join(process.cwd(), "src/app/studio/agents/error.tsx"),
  "utf8",
);
const loadingBoundary = readFileSync(
  join(process.cwd(), "src/app/studio/agents/loading.tsx"),
  "utf8",
);

describe("Agent Studio management page", () => {
  it("exports the current draft as a NexAU ZIP package", () => {
    expect(workbench).toContain("studioClient.downloadNexauBundle");
    expect(workbench).toContain("导出 NexAU ZIP");
    expect(workbench).toContain("导入与导出");
    expect(workbench).not.toContain("在隔离环境中完成真实预检");
    expect(studioClient).toContain("/nexau-bundle");
  });

  it("keeps release controls on one row and retains a focused version-history drawer", () => {
    expect(styles).toMatch(/\.headerActions\s*\{[^}]*display:\s*flex;/s);
    expect(styles).toMatch(
      /\.studioShell\s*\{[^}]*grid-template-columns:\s*var\(--studio-sidebar-expanded-width\) minmax\(680px, 1fr\);/s,
    );
    expect(styles).toMatch(/\.contractRail\s*\{[^}]*position:\s*fixed;/s);
    expect(styles).toContain('.contractRail[data-open="true"]');
    expect(styles).toMatch(
      /\.contractRail\s*\{[^}]*visibility\s+0s linear 220ms;/s,
    );
    expect(styles).toMatch(
      /\.contractRail\[data-open="true"\]\s*\{[^}]*visibility\s+0s linear 0s;/s,
    );
    expect(workbench).not.toContain('aria-controls="effective-contract-drawer"');
    expect(workbench).toContain("className={styles.contractBackdrop}");
    expect(workbench).toContain("useDialogFocus({");
    expect(workbench).toContain("panelRef: versionHistoryRailRef");
    expect(workbench).toContain("initialFocusRef: versionHistoryCloseRef");
    expect(workbench).toContain('role="dialog"');
    expect(workbench).toContain('aria-modal="true"');
  });

  it("exposes personal immutable version history and pointer rollback in a right drawer", () => {
    expect(workbench).toContain('aria-controls="personal-version-history"');
    expect(workbench).toContain("studioClient.listPersonalAgentVersions");
    expect(workbench).toContain("studioClient.promotePersonalAgentVersion");
    expect(workbench).toContain("切换只影响之后创建的任务");
    expect(workbench).toContain("已有任务不会改变");
    expect(workbench).toContain("回退是移动当前指针");
    expect(workbench).toContain("panelRef: versionHistoryRailRef");
    expect(workbench).toContain("initialFocusRef: versionHistoryCloseRef");
    expect(workbench).not.toContain("versionHistoryCloseRef.current?.focus()");
    expect(styles).toMatch(/\.versionHistoryRail\s*\{[^}]*width:\s*min\(520px,/s);
    expect(styles).toContain(".versionTimeline::before");
    expect(styles).toContain('.versionTimeline > li[data-current="true"]');
  });

  it("uses one sidebar width contract across Studio surfaces", () => {
    expect(codexTheme).toContain("--app-sidebar-expanded-width: 264px");
    expect(codexTheme).toContain("--app-sidebar-collapsed-width: 52px");
    expect(codexTheme).toContain("--studio-page-content-max: 1320px");
    expect(codexTheme).toContain("--studio-page-header-divider-gap: 20px");
    expect(codexTheme).toContain(
      "--studio-sidebar-expanded-width: var(--app-sidebar-expanded-width)",
    );
    expect(mcpCatalogStyles).toContain(
      "grid-template-columns:var(--studio-sidebar-expanded-width) minmax(0,1fr)",
    );
    expect(mcpCatalogStyles).toContain(
      'shell:has(> [data-studio-sidebar="collapsed"])',
    );
    expect(mcpCatalogStyles).toMatch(
      /\.catalogActions \.primary\s*\{[^}]*color:\s*var\(--codex-on-accent,\s*#07111a\)/s,
    );
    expect(mcpCatalogStyles).toMatch(/\.resourceRoot\s*\{[^}]*--accent:/s);
    expect(a2aStyles).toContain(
      "grid-template-columns: var(--studio-sidebar-expanded-width) minmax(0, 1fr)",
    );
    expect(sidebarStyles).toContain("var(--studio-sidebar-mobile-max-width)");
  });

  it("is an independent control-plane route", () => {
    expect(page).toContain("AgentStudioWorkbench");
    expect(studioLayout).toContain("<StudioUnifiedShell>");
    expect(workbench).not.toContain("<StudioUnifiedShell");
    expect(workbench).not.toContain("有效运行契约");
    expect(workbench).toContain('className={styles.agentCatalogPurpose}');
    expect(workbench).toContain('aria-label="运行能力摘要"');
    expect(workbench).toContain('"无需 Skill"');
    expect(workbench).toContain("activeStageBlocked");
    expect(workbench).toContain("阶段有 {activeStageBlocked.length} 项阻塞");
    expect(sidebar).toContain("<ProductBrandMark");
    expect(sidebar).toContain("<ProductBrandCopy");
    expect(productBrand).toContain('PRODUCT_NAME = "KAI WORKBENCH"');
    expect(sidebar).toContain("<WorkspaceNavigation");
    expect(workspaceNavigation).toContain('aria-label="工作区"');
    expect(workspaceNavigation).toContain('href: "/"');
    expect(workspaceNavigation).toContain('href: "/studio/agents"');
    expect(studioLayout).toContain("AuthProvider");
    expect(workbench).toContain('data-studio-integration="api"');
  });

  it("renders the primary Studio editor before secondary control-plane data", () => {
    expect(workbench).toContain("const [serverDrafts, serverCapabilities] = await Promise.all([");
    expect(workbench).toContain("if (loading || loadError) return;");
    expect(workbench).toContain("studioClient.listPreviews()");
    expect(workbench).toContain("These panels are secondary; the primary editor remains available.");
    expect(studioClient).toContain("const [personal, response] = await Promise.all([");
  });

  it("keeps version-history drawer status and close actions compact and aligned", () => {
    expect(styles).toContain(".contractHeader > .contractHeaderActions");
    expect(styles).toMatch(/\.contractHeader\s*>\s*\.contractHeaderActions\s*\{[^}]*display:\s*flex;/s);
    expect(styles).toMatch(/\.riskBadge\s*\{[^}]*min-height:\s*24px;[^}]*border-radius:\s*6px;/s);
    expect(workbench.match(/m4\.5 4\.5 7 7m0-7-7 7/g)).toHaveLength(1);
  });

  it("keeps Studio focused and exposes the published Agent as a task action", () => {
    expect(workbench).not.toContain("<StudioJourney");
    expect(workbench).not.toContain("从想法到可运行");
    expect(workbench).toContain("const taskVersion = draft.spaceId");
    expect(workbench).toContain("version.agent_id === draft.agentId");
    expect(workbench).toContain("const taskHref = taskVersion");
    expect(workbench).toContain("owner=${encodeURIComponent(user.user_id)}");
    expect(workbench).toContain("href={taskHref}");
    expect(workbench).toContain("className={styles.releaseTaskShortcut}");
    expect(workbench.match(/开始任务/g)).toHaveLength(2);
    expect(styles).toContain(".startTaskButton");
    expect(styles).toContain(".releaseTaskShortcut");
  });

  it("uses one action-button contract and structured overflow menu states", () => {
    expect(workbench.match(/styles\.headerActionButton/g)).toHaveLength(2);
    expect(workbench.match(/className=\{styles\.actionMenuItem\}/g)).toHaveLength(10);
    expect(workbench).not.toContain("copilot-drawer");
    expect(workbench).not.toContain("CopilotDrawer");
    expect(workbench).toContain("<HeaderActionIcon name=\"release\"");
    expect(workbench).toContain("任务 · 导入与导出");
    expect(workbench).toContain("导入与导出");
    expect(styles).toContain(".headerActions .headerActionButton");
    expect(styles).toContain(".actionMenuItem:disabled");
    expect(workbench).toContain("useDismissablePopovers()");
    expect(workbench).toContain("data-dismiss-on-outside");
  });

  it("keeps synchronization state with draft identity instead of between actions", () => {
    const titleBlock = workbench.slice(
      workbench.indexOf('<div className={styles.titleBlock}>'),
      workbench.indexOf('<div className={styles.headerActions}>'),
    );
    expect(titleBlock).toContain("className={styles.syncState}");
    expect(titleBlock).toContain("已保存 r${draft.revision}");
    expect(titleBlock).not.toContain("已同步 r${draft.revision}");
    expect(styles).toContain(".syncState");
  });

  it("saves unsaved work before creating another personal Agent", () => {
    expect(workbench).toContain("async function startNewDraft()");
    expect(workbench).toContain('title: "保存当前修改并新建？"');
    expect(workbench).toContain('confirmLabel: "保存并新建"');
    expect(workbench).toContain('cancelLabel: "继续编辑"');
    expect(workbench).toContain("const saved = await saveDraft()");
    expect(workbench).toContain("if (!saved) return");
    expect(workbench).toContain('setBuilderAssistantMode("create")');
    expect(workbench).toContain("<AgentBuilderAssistant");
    expect(builderOverlays).toContain("描述你想创建的智能体");
    expect(builderOverlays).toContain("在右侧测试效果");
    expect(builderOverlays).toContain("studioClient.createDraftFromTask");
    expect(builderOverlays).toContain("只使用 Worker 运行；不访问外部网络");
    expect(builderOverlays).toContain('aria-label="发送消息"');
    expect(workbench).toContain("setBuilderAssistantOpen(true)");
    expect(workbench).toContain('data-builder-open={builderAssistantOpen}');
    expect(builderOverlays).not.toContain("AgentBuilderCopilot");
    expect(workbench).not.toContain("createPersonalStudioDraft");
    expect(workbench).toContain("disabled={!canEdit || saving}");
    expect(workbench).toContain("onClick={() => void startNewDraft()}");
    expect(workbench).toContain(': "尚未保存"');
  });

  it("uses a direct side conversation with expandable run evidence", () => {
    expect(builderOverlays).not.toContain("执行计划");
    expect(builderOverlays).not.toContain("result.loop.map");
    expect(builderOverlays).toContain("PreviewRunResponse");
    expect(builderOverlays).toContain("studioClient.createTryRun");
    expect(builderOverlays).toContain("studioClient.cancelTryRun");
    expect(builderOverlays).not.toContain("studioClient.solidifyTryRun");
    expect(builderOverlays).not.toContain("固化版本");
    expect(builderOverlays).toContain('aria-label="收起构建助手"');
    expect(builderOverlays).toContain("Enter 发送 · Shift + Enter 换行");
    expect(builderOverlayStyles).toContain(".transcript");
    expect(builderOverlayStyles).toContain(".composer");
    expect(builderOverlays).not.toContain("BuilderAssistantLauncher");
    expect(builderOverlayStyles).not.toContain(".assistantLauncher");
    expect(builderOverlayStyles).toContain("@media (max-width: 1199px)");
    expect(styles).toContain('data-builder-open="true"');
  });

  it("aligns the new-task action with workspace navigation columns", () => {
    expect(appStyles).toContain(".task-sidebar-primary button");
    expect(appStyles).toContain("padding: 0 12px");
    expect(appStyles).toContain("grid-template-columns: 20px minmax(0, 1fr)");
    expect(appStyles).toContain("gap: 9px");
    expect(appStyles).toContain("font-family: inherit");
    expect(appStyles).toContain("font-size: 12.5px");
    expect(appStyles).toContain("font-weight: 590");
    expect(appStyles).toContain("text-align: left");
  });

  it("allows discarding unsaved edits when leaving Studio", () => {
    expect(workbench).toContain("requestDecision");
    expect(workbench).toContain('discardLabel: "放弃修改并离开"');
    expect(workbench).toContain('discardLabel: "放弃修改并返回"');
    expect(workbench).toContain('if (decision === "cancel") return');
    expect(workbench).toContain('if (decision === "confirm")');
  });

  it("saves unsaved work before switching Agents and serializes draft loads", () => {
    expect(workbench).toContain("if (draftId === draft.id || draftSwitchingRef.current) return");
    expect(workbench).toContain('title: "保存当前修改并切换？"');
    expect(workbench).toContain('confirmLabel: "保存并切换"');
    expect(workbench).toContain('cancelLabel: "继续编辑"');
    expect(workbench).toContain("const saved = await saveDraft()");
    expect(workbench).toContain("if (!saved) return");
    expect(workbench).toContain("setSwitchingDraftId(draftId)");
    expect(workbench).toContain("draftSwitchingRef.current = false");
    expect(workbench).toContain("disabled={saving || Boolean(switchingDraftId)}");
    expect(workbench).toContain('? "…" : "→"');
  });

  it("requires explicit destructive confirmation before replacing a conflicted local draft", () => {
    expect(workbench).toContain("async function reloadAfterConflict()");
    expect(workbench).toContain('title: "放弃本地修改并加载控制面版本？"');
    expect(workbench).toContain('confirmLabel: "放弃并加载"');
    expect(workbench).toContain('cancelLabel: "继续编辑"');
    expect(workbench).toContain('tone: "danger"');
    expect(workbench).toContain("conflictReloadingRef.current = true");
    expect(workbench).toContain("控制面版本加载失败，本地修改仍保留");
    expect(workbench).toContain("disabled={saving || reloadingConflict}");
    expect(workbench).toContain('? "正在加载…" : "加载控制面版本"');
  });

  it("keeps the A2A implementation available while hiding its product entry", () => {
    expect(a2aPage).toContain("AgentA2AWorkspace");
    expect(studioLayout).toContain("AuthProvider");
    expect(a2aWorkspace).toContain('kindFilter="a2a"');
    expect(a2aWorkspace).toContain("Agent Card + message:send");
    expect(triggerControlPlane).not.toContain("打开 A2A 控制台");
    expect(triggerControlPlane).not.toContain("Agent 间协议接入");
    expect(triggerControlPlane).toContain("/message:send");
    expect(triggerControlPlane).toContain("/agent-card.json");
    expect(triggerControlPlane).toContain('item.kind !== "a2a"');
    expect(triggerControlPlane).not.toContain('<option value="a2a">');
    expect(triggerStyles).toMatch(
      /\.triggerList \.actions\s*\{[^}]*grid-column:\s*1 \/ -1;/s,
    );
  });

  it("hides the Next.js development badge from the product navigation", () => {
    expect(nextConfig).toContain("devIndicators: false");
  });

  it("shares a persistent task-style collapsible control-plane rail", () => {
    expect(sidebar).toContain("agent-studio-sidebar-collapsed");
    expect(sidebar).toContain("data-studio-sidebar");
    expect(sidebar).toContain("`收起${PRODUCT_NAME}侧栏`");
    expect(sidebar).toContain("`展开${PRODUCT_NAME}侧栏`");
    expect(sidebar).toContain("aria-expanded={!collapsed}");
    expect(sidebar).not.toContain("<ThemeToggle");
    expect(sidebar).toContain("<AccountMenu");
    expect(sidebarStyles).toContain(
      '.sidebar[data-studio-sidebar="collapsed"]',
    );
    expect(sidebarStyles).toContain("width: var(--studio-sidebar-collapsed-width)");
    expect(sidebarStyles).toContain("@media (max-width: 980px)");
    expect(sidebar).toContain('const COMPACT_MEDIA_QUERY = "(max-width: 980px)"');
    expect(sidebar).toContain("if (compactViewport.matches)");
    expect(sidebar).toContain('setCollapsed(true)');
    expect(sidebar).toContain('compactViewport.addEventListener("change"');
    expect(sidebar).toContain('compactViewport.removeEventListener("change"');
    expect(sidebar).toContain('if (!window.matchMedia(COMPACT_MEDIA_QUERY).matches)');
    expect(workspaceNavigationStyles).toContain(".navigationActive");
    expect(workspaceNavigationStyles).toContain(
      '[data-workspace-navigation="collapsed"]',
    );
    expect(styles).toMatch(
      /@media \(max-width: 900px\)[\s\S]*?\.studioShell:has\(> \[data-studio-sidebar="collapsed"\]\)[\s\S]*?grid-template-columns: var\(--studio-sidebar-collapsed-width\) minmax\(0, 1fr\)/,
    );
    expect(styles).toMatch(
      /@media \(max-width: 620px\)[\s\S]*?\.headerActions[\s\S]*?flex-wrap: wrap/,
    );
  });

  it("keeps authoring direct and moves the essential runtime summary to Agent cards", () => {
    for (const label of [
      "基本信息",
      "System Prompt",
      "协同编排",
      "Skills",
      "Tools 与联网",
      "运行与权限",
      "测试与发布",
    ]) {
      expect(workbench).toContain(label);
    }
    expect(workbench).not.toContain("有效运行契约");
    expect(workbench).toContain("agent.primaryOutput");
    expect(workbench).toContain("agent.toolCount");
    expect(workbench).toContain("agent.skillCount");
    expect(workbench).toContain("agent.networkToolsEnabled");
    expect(styles).toContain(".agentCatalogCapabilities");
  });

  it("opens knowledge sync links on the affected agent and capability section", () => {
    expect(workbench).toContain('new URLSearchParams(window.location.search)');
    expect(workbench).toContain('navigationState.get("draft")');
    expect(workbench).toContain('navigationState.get("section")');
    expect(workbench).toContain('navigationState.get("source") === "knowledge-sync"');
    expect(workbench).toContain("知识库工具已更新：请确认绑定工具");
  });

  it("provides a structured prompt editor instead of an undifferentiated textarea", () => {
    expect(workbench).toContain('aria-label="System Prompt 结构"');
    expect(workbench).toContain("选择章节可定位");
    expect(workbench).toContain("专注编辑");
    expect(workbench).toContain("Ctrl / ⌘ S 保存");
    expect(workbench).toContain("moveToPromptSection");
    expect(workbench).toContain("handlePromptEditorKeyDown");
    expect(styles).toContain(".promptWorkspace");
    expect(styles).toContain(".promptEditorToolbar");
    expect(styles).toContain(".promptEditorFooter");
  });

  it("uses one rich code workbench for Python and JSON authoring", () => {
    expect(workbench).toContain("function PythonCodeEditor");
    expect(workbench).toContain("function JsonSchemaCodeEditor");
    expect(workbench).toContain("<StudioCodeEditor");
    expect(workbench).toContain('ariaLabel="Python 源码"');
    expect(workbench).toContain('ariaLabel="JSON Schema"');
    expect(codeEditor).toContain("Spaces: 4");
    expect(workbench).toContain("Python 3.12 · Sandbox");
    expect(workbench).toContain("JSON 有效");
    expect(codeEditor).toContain("lineNumbers()");
    expect(codeEditor).toContain("highlightActiveLine()");
    expect(codeEditor).toContain("bracketMatching()");
    expect(codeEditor).toContain("highlightSelectionMatches()");
    expect(codeEditor).toContain("indentWithTab");
    expect(codeEditor).toContain('language === "python" ? python() : json()');
    expect(codeEditor).toContain('".cm-activeLine"');
    expect(codeEditorStyles).toContain(".cm-search");
    expect(styles).toContain(".pythonToolWorkspace");
    expect(workbench).not.toContain("schemaCodeEditor");
    expect(workbench).not.toContain("pythonSourceEditor");
  });

  it("uses one control height and radius across the authoring workbench", () => {
    expect(styles).toContain(
      "--studio-control-height: var(--codex-control-height, 40px)",
    );
    expect(styles).toContain(
      "--studio-control-radius: var(--codex-control-radius, 8px)",
    );
    expect(styles).toMatch(
      /\.field input,[\s\S]*?min-height: var\(--studio-control-height\);/,
    );
  });

  it("launches Skill creation from the home conversation with an explicit scope", () => {
    expect(workbench).toContain('skillCreatorHref("agent"');
    expect(skillsCatalog).toContain('skillCreatorHref("personal")');
    expect(skillsCatalog).toContain('skillCreatorHref("platform")');
    expect(skillsCatalog).toContain("SKILL_SCOPE_LABELS");
    expect(skillCreatorLaunch).toContain('name: "skill-creator"');
    expect(skillCreatorLaunch).toContain("个人 Skill");
    expect(skillCreatorLaunch).toContain("平台 Skill");
    expect(skillCreatorLaunch).toContain("Agent Skill");
    expect(homePage).toContain("parseSkillCreatorLaunch");
    expect(homePage).toContain("persistTaskComposerDraft");
    expect(agentThread).toContain('className="composer-skill-context"');
    expect(agentThread).toContain('aria-label={`移除技能 ${selectedSkill}`}');
    expect(agentThread).toContain("seededSkillLaunchRef");
    expect(agentThread).toContain("aui.composer().setText(skillCreatorPrompt(activeSkillLaunch))");
    expect(skillConversationBuilder).toContain('aria-label="Skill 对话共创"');
    expect(skillConversationBuilder).toContain("continueSkillConversation");
    expect(skillConversationBuilder).toContain("currentSkill: proposal ?? currentSkill");
    expect(skillConversationBuilder).toContain("应用到当前 Skill");
    expect(skillConversationBuilder).toContain("生成结果需确认后才会写入当前草稿");
    expect(skillConversationStyles).toContain(".workspaceWithProposal");
    expect(skillConversationStyles).toContain("@media (prefers-reduced-motion: reduce)");
  });

  it("manages internal collaborators inline with a return path to the parent", () => {
    expect(workbench).toContain('id="collaboration-title"');
    expect(workbench).toContain("创建并绑定");
    expect(workbench).toContain("返回父智能体");
    expect(workbench).toContain("允许后台并行");
    expect(workbench).toContain("引用已有");
    expect(workbench).toContain("编辑配置");
    expect(workbench).toContain("useInternalAgentsPreference()");
    expect(workbench).not.toContain("setShowInternalAgents");
    expect(workbench).toContain("subagentCandidates");
    expect(studioClient).toContain("publishedVersion");
    expect(styles).toContain(".orchestrationGraph");
    expect(styles).toContain(".subagentTopology");
    expect(styles).toContain(".leadAgentCard");
  });

  it("keeps sandbox mandatory and exposes platform web tools with MCP retained", () => {
    expect(workbench).toContain("执行与权限");
    expect(workbench).toContain("配置智能体运行时、执行方式和工具权限");
    expect(workbench).toContain('<details className={styles.advancedRuntimeSettings}>');
    expect(workbench).toContain("高级运行设置");
    expect(workbench).toContain("执行档位、权限、资源上限、会话恢复与治理");
    expect(styles).toContain(".advancedRuntimeSettings[open]");
    expect(workbench).toContain("调用范围遵循已发布的权限设置");
    expect(workbench).not.toContain('type="checkbox" checked={sandbox');
    expect(studioConfig).toContain("公网搜索（Tavily）");
    expect(workbench).toContain("仅展示可配置工具");
    expect(workbench).toContain('new Set(["Task"])');
    expect(builderOverlays).toContain("builtinTools: generatedDraft.builtinTools");
    expect(workbench).toContain("独立工作负载身份");
    expect(workbench).toContain("恢复同一会话的运行时线程上下文");
    expect(workbench).toContain("不宣称支持任意工具步骤的持久化 checkpoint");
  });

  it("offers compact Worker tool presets and inline selection without a modal", () => {
    expect(workbench).toContain('aria-label="工具范围"');
    expect(workbench).toContain("标准工具");
    expect(workbench).toContain("只读工具");
    expect(workbench).toContain("自定义可用工具");
    expect(workbench).toContain("workerToolOptions.map");
    expect(workbench).not.toContain('aria-label="工具加载方式"');
    expect(studioConfig).toContain('toolExposureMode: "eager"');
    expect(studioClient).toContain("toolExposureMode");
    expect(styles).toContain(".workerToolPicker");
    expect(styles).toContain(".compactToolGrid");
    expect(styles).not.toContain(".toolExposureModal");
  });

  it("drives runtime choices and Codex capability copy from server runtime capabilities", () => {
    expect(workbench).toContain("activeRuntimeCapabilities");
    expect(workbench).toContain("activeRuntimeCapability");
    expect(workbench).toContain("targetCapability.modelApiFormats.includes");
    expect(workbench).not.toContain('option value="codex-app-server"');
    expect(workbench).not.toContain("Codex P0 已支持 Responses、线程续接");
    expect(workbench).toContain("activeRuntimeCapability.limitations");
  });

  it("keeps runtime and permission surfaces on shared light/dark theme tokens", () => {
    expect(workbench).toContain("当前场景推荐");
    expect(workbench).toContain("应用推荐配置");
    expect(workbench).toContain("permissionCoverage");
    expect(workbench).toContain("mcpTools={selectedMcpTools}");
    expect(governanceControlPlane).toContain("defaultCallRules(policyId, mcpTools)");
    expect(governanceControlPlane).toContain("个 MCP 工具已纳入模板");
    expect(governanceControlPlane).toContain("同步当前 Agent 工具");
    expect(governanceControlPlane).toContain("保存并发布后才会影响真实 Run");
    expect(styles).toContain(".runtimeRecommendation");
    expect(styles).toContain(".permissionCoverage");
    expect(styles).toMatch(
      /\.profileFacts span \{[\s\S]*?background: var\(--studio-panel-subtle\);/,
    );
    expect(styles).toMatch(
      /\.isolationCard \{[\s\S]*?background: var\(--studio-panel-subtle\);/,
    );
    expect(styles).toMatch(
      /\.identityBoundary,[\s\S]*?\.continuityBoundary \{[\s\S]*?background: var\(--studio-panel\);/,
    );
    expect(styles).toMatch(
      /\.identityBoundary header em,[\s\S]*?\.continuityBoundary header em \{[\s\S]*?background: var\(--studio-panel-subtle\);/,
    );
  });

  it("can uninstall an embedded Skill without hiding the empty Skills workspace", () => {
    expect(workbench).toContain("async function uninstallSkill");
    expect(workbench).toContain("卸载当前 Skill");
    expect(workbench).toContain("已发布的不可变历史版本不会被修改");
    expect(workbench).toContain('aria-labelledby="skills-title"');
    expect(workbench).toContain("当前草稿尚未安装 Skill");
    expect(styles).toContain(".skillUninstallButton");
    expect(styles).toContain(".skillEmpty");
  });

  it("keeps publication permissioned while making every disabled reason actionable", () => {
    expect(workbench).toContain("membership.role");
    expect(workbench).toContain("studioClient.publishDraft");
    expect(workbench).toContain("handleReleaseAction");
    expect(workbench).toContain("保存并检查");
    expect(workbench).toContain("检查发布条件");
    expect(workbench).toContain("等待管理员发布");
    expect(workbench).not.toContain('aria-label="发布准备"');
    expect(workbench).toContain('aria-label="发布检查结果"');
    expect(workbench).toContain("检查结果不会占用编辑区");
    expect(workbench).toContain('aria-label="关闭发布检查结果"');
    expect(workbench).toContain("setReleaseFeedbackOpen(false)");
    expect(workbench).toContain("validationIssueSection");
    expect(workbench).toContain("去处理");
    expect(workbench).toContain("移除不兼容 MCP");
    expect(workbench).toContain("productionValidationErrors");
    expect(workbench).toContain("suggestedProfileIds");
    expect(workbench).toContain("切换至 ${suggestedProfile.label}");
    expect(workbench).toContain("PROFILE COMPATIBILITY");
    expect(workbench).toContain("compatibleExecutionProfiles");
    expect(workbench).toContain("applyRecommendedExecutionProfile");
    expect(workbench).toContain("切换、保存并检查");
    expect(styles).toContain(".releaseFeedbackPopover");
    expect(styles).toMatch(/\.releaseFeedbackPopover\s*\{[^}]*position:\s*fixed;/s);
    expect(styles).not.toContain(".releaseAssistant");
    expect(styles).toContain(".releaseIssues");
    expect(styles).toContain(".executionProfileAdvisor");
    expect(styles).toContain('.releaseIssues li[data-stage="production"]');
    expect(styles).toContain(".publicationBadge");
  });

  it("keeps advanced lifecycle services while hiding them from the simplified builder", () => {
    expect(workbench).toContain('aria-label="Agent 构建五阶段" hidden');
    expect(workbench).toContain("stageForSection");
    expect(styles).toMatch(/\.stageNav,[\s\S]*?\.previewBanner\s*\{\s*display:\s*none;/);
    expect(styles).toContain(".configPanel + .configPanel");
    expect(workbench).toContain("<HeaderActionIcon name=\"release\"");
    expect(workbench).toContain('setActiveSection("identity")');
    expect(workbench).toContain('!draft.parentDraftId && (');
    expect(workbench).toContain('activeSection === "evaluation"');
    expect(workbench).toContain('activeSection === "trial"');
    expect(operationsWorkspace).toContain("耐久 Dataset 与固定版本评测");
    expect(operationsWorkspace).toContain("导入题库");
    expect(operationsWorkspace).toContain("importEvalDataset");
    expect(operationsWorkspace).toContain("通过率");
    expect(operationsWorkspace).toContain("用例级结果分析");
    expect(operationsWorkspace).toContain("caseStatusLabel");
    expect(operationsWorkspace).toContain("caseAnalysis.counts.passed");
    expect(operationsWorkspace).toContain("item.failures.join");
    expect(operationsWorkspace).toContain("studioClient.createEvalRun");
    expect(workbench).toContain("studioClient.getQualityGate");
    expect(operationsWorkspace).toContain("环境指针与部署历史");
    expect(operationsWorkspace).toContain("studioClient.promoteDeployment");
    expect(errorBoundary).toContain("{PRODUCT_NAME}没有正常加载");
    expect(errorBoundary).toContain("重新加载");
    expect(loadingBoundary).toContain("ProductLoading");
    expect(styles).toContain(".studioStateShell");
    expect(styles).toContain(".deploymentControlPlane");
  });

  it("turns a deployed Agent into a governed external service", () => {
    expect(workbench).not.toContain("<AgentTriggerControlPlane");
    expect(operationsWorkspace).toContain("<AgentTriggerControlPlane");
    expect(triggerControlPlane).toContain("外部触发器");
    expect(triggerControlPlane).toContain("studioClient.createTrigger");
    expect(triggerControlPlane).toContain("studioClient.updateTrigger");
    expect(triggerControlPlane).toContain("studioClient.rotateTriggerSecret");
    expect(triggerControlPlane).toContain("Idempotency-Key");
    expect(triggerControlPlane).toContain("只显示一次");
    expect(triggerControlPlane).toContain("/webhooks/agent-triggers/");
    expect(triggerStyles).not.toContain("linear-gradient");
    expect(triggerStyles).toContain("var(--codex-accent");
  });

  it("makes Environment a versioned runtime boundary instead of a route label", () => {
    expect(workbench).not.toContain("<EnvironmentPolicyControlPlane");
    expect(operationsWorkspace).toContain("<EnvironmentPolicyControlPlane");
    expect(environmentPolicyControlPlane).toContain("每个新会话固定一份不可变策略快照");
    expect(environmentPolicyControlPlane).toContain("studioClient.replaceEnvironmentPolicy");
    expect(environmentPolicyControlPlane).toContain("allowedModelRoutes");
    expect(environmentPolicyControlPlane).toContain("allowedMcpReferences");
    expect(environmentPolicyControlPlane).toContain("外部知识边界");
    expect(environmentPolicyControlPlane).toContain("随 MCP 资源统一授权");
    expect(environmentPolicyControlPlane).not.toContain("Phase 4");
    expect(environmentPolicyControlPlane).not.toContain("setKnowledgeText");
    expect(environmentPolicyControlPlane).toContain("credentialScopes");
    expect(environmentPolicyControlPlane).not.toContain("maxRunBudgetUsd");
    expect(environmentPolicyControlPlane).toContain("费用与 Token 不设执行额度");
    expect(environmentPolicyControlPlane).toContain("maxArtifactBytes");
    expect(environmentPolicyStyles).toContain("grid-template-columns: 0.72fr 1.35fr");
    expect(environmentPolicyStyles).not.toContain("linear-gradient");
    expect(environmentPolicyStyles).toContain(":focus-visible");
    expect(environmentPolicyStyles).toContain(".knowledgeBoundary");
  });

  it("creates a hash-bound Preview and renders real Preflight facts", () => {
    expect(workbench).toContain("studioClient.createPreview");
    expect(workbench).toContain("studioClient.cancelPreview");
    expect(workbench).toContain("createRandomId()");
    expect(workbench).toContain('["cancelled", "failed", "expired"]');
    expect(workbench).toContain("测试身份 · Draft r");
    expect(workbench).toContain("真实 Preflight · {activePreview.preflightResult.status}");
    expect(workbench).toContain("preflightStageLabels[check.stage]");
    expect(workbench).toContain("preflightProgress(activePreview.preflightResult.checks)");
    expect(workbench).toContain("execution_profile_sandbox_provider_mismatch");
    expect(workbench).toContain("本地开发 Preview");
    expect(workbench).toContain("禁止生产发布");
    expect(workbench).toContain("workspace_artifact");
    expect(workbench).toContain('activePreview.stale ? "历史 Preview" : "Preview"');
    expect(workbench).toContain(`重新测试 Draft r\${draft.revision}`);
    expect(workbench).toContain("读取刷新不会按当前 Draft 重跑");
    expect(styles).toContain(".previewBanner");
    expect(styles).toContain(".preflightDisclosure");
    expect(styles).toContain(
      "background: color-mix(in srgb, var(--studio-panel) 88%, var(--studio-green) 12%)",
    );
    expect(styles).not.toContain(
      "background: color-mix(in srgb, #fff 72%, transparent)",
    );
  });

  it("renders only tenant API rows instead of invented live agents", () => {
    expect(sidebar).toContain("<ProductBrandCopy");
    expect(workbench).toContain("studioClient.listAccessibleDrafts");
    expect(workbench).toContain("studioClient.getDraft");
    expect(workbench).not.toContain("helper-agent-1.0.0");
    expect(workbench).not.toContain("echo-agent-0.4.0");
    expect(workbench).not.toContain("合同审查助手");
    expect(workbench).not.toContain("工单分诊助手");
  });

  it("offers protected personal Agent deletion without erasing history", () => {
    expect(workbench).toContain("studioClient.deleteDraft");
    expect(workbench).toContain("删除智能体");
    expect(workbench).toContain("已发布的不可变版本、已有任务和审计记录会保留");
    expect(workbench).toContain("协作空间智能体不能在这里删除");
    expect(styles).toContain(".actionMenuDanger");
  });

  it("uses a quiet registry palette with no decorative gradient", () => {
    expect(styles).not.toContain("linear-gradient");
    expect(styles).not.toContain("radial-gradient");
    expect(styles).toContain("--studio-green: var(--codex-accent)");
    expect(styles).toContain("--studio-violet: var(--codex-skill)");
    expect(styles).not.toContain("#2e7058");
    expect(styles).toMatch(/@media \(max-width: 900px\)/);
    expect(styles).toMatch(/@media \(prefers-reduced-motion: no-preference\)/);
  });

  it("opens on a clickable two-column Agent catalog before the single-column editor", () => {
    expect(workbench).toContain('useState<"catalog" | "editor">("catalog")');
    expect(workbench).not.toContain("StudioSectionNavigation");
    expect(workbench).toContain('viewMode === "catalog"');
    expect(workbench).toContain('aria-label="智能体目录"');
    expect(workbench).toContain("openDraftEditor");
    expect(workbench).toContain("打开${agent.displayName}");
    expect(workbench).toContain('returnToCatalog');
    expect(workbench).toContain('open={viewMode === "editor" && Boolean(workspaceTarget)}');
    expect(styles).toContain(".agentCatalogCard");
    expect(styles).toMatch(/\.agentCatalogList\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/s);
    expect(styles).toMatch(/\.editorBody\s*\{[^}]*display:\s*block;/s);
    expect(styles).toMatch(/\.stageNav\s*\{[^}]*flex-direction:\s*row;/s);
  });

  it("keeps the empty Agent catalog quiet until the user explicitly creates one", () => {
    expect(workbench).not.toContain("if (canEdit) setNewAgentOpen(true)");
    expect(workbench).toContain('setNotice(canEdit ? "当前没有草稿，可新建第一个 Agent"');
    expect(workbench).toContain('className={`${styles.studioShell} ${styles.workbenchContent}`}');
  });

  it("uses compact gray cards and a constrained editor canvas", () => {
    expect(styles).toMatch(/\.agentCatalogCard\s*\{[^}]*var\(--codex-surface-raised, #242424\)/s);
    expect(styles).toMatch(/\.catalogCreateButton\s*\{[^}]*white-space:\s*nowrap;/s);
    expect(styles).toContain(".agentCatalogMenu");
    expect(workbench).toContain("deleteCatalogDraft(agent)");
    expect(styles).toMatch(/\.codeEditor\s*\{[^}]*min-height:\s*250px;/s);
    expect(styles).toMatch(/\.editorHeader\s*\{[^}]*width:\s*min\(1080px, 100%\);/s);
    expect(styles).toMatch(/\.configPanel\s*\{[^}]*width:\s*min\(100%, 900px\);/s);
  });

  it("uses one model select inside basic information instead of a model page", () => {
    expect(workbench).toContain('<Field label="模型" wide>');
    expect(workbench).toContain("selectedModelValue");
    expect(workbench).not.toContain('activeSection === "model"');
    expect(workbench).not.toContain("styles.routeCards");
    expect(studioConfig).toContain('sections: ["identity"]');
  });
});
