import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const component = readFileSync(
  join(process.cwd(), "src/components/team-spaces/team-spaces.tsx"),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/team-spaces/team-spaces.module.css"),
  "utf8",
);
const taskPage = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");

describe("collaboration workspace product surface", () => {
  it("exposes workspace resources as focused, countable views", () => {
    expect(component).toContain("协作空间");
    expect(component).toContain('aria-label="空间资源"');
    expect(component).toContain('setActiveView("agents")');
    expect(component).toContain('setActiveView("knowledge")');
    expect(component).toContain('setActiveView("members")');
    expect(component).toContain("workspaceAgents.length");
    expect(component).toContain("sharedKnowledge.length");
    expect(component).toContain("members.length");
  });

  it("starts with members before resources because membership gates sharing", () => {
    const tabs = component.indexOf('className={styles.resourceTabs}');
    const members = component.indexOf('<span>成员</span>', tabs);
    const agents = component.indexOf('<span>智能体</span>', tabs);
    const knowledge = component.indexOf('<span>知识库</span>', tabs);
    expect(component).toContain('useState<SpaceView>("members")');
    expect(component).toContain('setSelectedId(item.space.spaceId); setActiveView("members")');
    expect(tabs).toBeGreaterThan(-1);
    expect(members).toBeGreaterThan(tabs);
    expect(agents).toBeGreaterThan(members);
    expect(knowledge).toBeGreaterThan(agents);
  });

  it("starts a new task from the selected workspace agent", () => {
    expect(component).toContain("开始任务");
    expect(component).toContain("item.can_chat && agent.currentVersion");
    expect(component).toContain("item.can_publish");
    expect(component).toContain("item.can_manage");
    expect(component).toContain("currentRelease?.agent.display_name");
    expect(component).not.toContain("item.canChat");
    expect(component).not.toContain("item.canPublish");
    expect(component).not.toContain("item.canManage");
    expect(component).toContain("/?space=${encodeURIComponent(selectedId)}");
    expect(taskPage).toContain('search.get("space")');
    expect(taskPage).toContain('search.get("agent")');
    expect(taskPage).toContain('search.get("version")');
    expect(taskPage).toContain("requestedAgent || requestedSkillLaunch");
    expect(taskPage).toContain("createNewThread(storage)");
    expect(taskPage).toContain('window.history.replaceState({}, "", "/")');
  });

  it("uses the shared collapsible Studio layout and finished states", () => {
    expect(styles).toMatch(
      /\.shell\s*\{[\s\S]*?grid-template-columns:\s*var\(--studio-sidebar-expanded-width\) minmax\(0, 1fr\);/,
    );
    expect(styles).toContain('.shell:has(> [data-studio-sidebar="collapsed"])');
    expect(styles).toContain(".spaceListLoading");
    expect(styles).toContain(".resourceLoading");
    expect(styles).toContain(".resourceTabs");
    expect(styles).toContain(".runLink");
    expect(component).toContain("创建第一个协作空间");
    expect(styles).toContain("var(--studio-page-content-max, 1320px)");
    expect(styles).toContain("var(--studio-page-header-divider-gap, 20px)");
  });

  it("creates spaces in the shared right-side authoring pattern", () => {
    expect(component).toContain("useDialogFocus");
    expect(component).toContain("initialFocusRef: createNameRef");
    expect(component).toContain('aria-labelledby="create-space-title"');
    expect(component).toContain('aria-modal="true"');
    expect(component).toContain('role="dialog"');
    expect(component).toContain("event.target === event.currentTarget");
    expect(component).toContain("setShowCreate(false)");
    expect(component).toContain("createError");
    expect(component).not.toContain('<details id="new-space"');
    expect(styles).toContain(".createBackdrop");
    expect(styles).toContain(".createDrawer");
    expect(styles).toMatch(/\.createButton\s*\{[^}]*width:\s*fit-content;/s);
    expect(styles).toMatch(/\.createButton\s*\{[^}]*justify-self:\s*end;/s);
    expect(styles).toMatch(/\.createDrawer\s*\{[\s\S]*?height:\s*100dvh;/);
    expect(styles).toMatch(/@media \(max-width: 660px\)[\s\S]*?\.createDrawer\s*\{\s*width:\s*100vw;/);
  });

  it("commits one space payload atomically and ignores stale switches", () => {
    expect(component).toContain("spaceLoadSequence");
    expect(component).toContain("sequence !== spaceLoadSequence.current");
    expect(component).toContain("setWorkspaceAgents([])");
    expect(component).toContain("正在切换协作空间");
    expect(component).toContain("aria-busy={spaceLoading}");
    expect(component).toContain("/workspace`");
    expect(component).toContain("workspace.releases_by_agent");
    expect(component).not.toContain("Promise.all(\n          agents.map");
  });

  it("removes members through an explicit inline confirmation", () => {
    expect(component).toContain("async function removeMember");
    expect(component).toContain('method: "DELETE"');
    expect(component).toContain("确认移除");
    expect(component).toContain("其个人任务记录不受影响");
    expect(component).toContain("member.userId === user.user_id");
    expect(styles).toContain(".memberActions");
  });

  it("requires confirmation before revoking shared capabilities", () => {
    expect(component).toContain("confirmingAction");
    expect(component).toContain("release:${agent.agentId}:${entry.release.version}");
    expect(component).toContain("acl:${agent.agentId}:${acl.granteeId}:${acl.permission}");
    expect(component).toContain("knowledge:${item.knowledgeBaseReference}");
    expect(component).toContain("Release 授权已撤销；既有任务仍保留其历史快照和记录");
    expect(component).toContain("知识库空间授权已撤销；历史任务不会被转移或公开");
  });

  it("only offers current space members for Agent ACL grants", () => {
    expect(component).toContain("spaceMemberDirectory(directory, members)");
    expect(component).toContain("spaceMemberDirectory(workspace.directory, workspace.members)");
    expect(component).toContain("aclCandidates.map");
    expect(component).not.toContain("{directory.map((entry) => <option");
  });

  it("preflights MCP credentials and knowledge grants in the Release flow", () => {
    expect(component).toContain("发布依赖");
    expect(component).toContain("MCP：成员个人凭据");
    expect(component).toContain("MCP：空间共享凭据");
    expect(component).toContain("share_knowledge_references");
    expect(component).toContain("mcpCredentialInputs");
    expect(component).toContain("dependenciesReady");
  });
});
