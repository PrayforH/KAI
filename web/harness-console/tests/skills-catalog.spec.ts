import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(
  join(process.cwd(), "src/app/studio/skills/page.tsx"),
  "utf8",
);
const layout = readFileSync(
  join(process.cwd(), "src/app/studio/layout.tsx"),
  "utf8",
);
const component = readFileSync(
  join(process.cwd(), "src/components/agent-studio/skills-catalog-page.tsx"),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/agent-studio/skills-catalog-page.module.css"),
  "utf8",
);
const navigation = readFileSync(
  join(process.cwd(), "src/components/workspace-navigation.tsx"),
  "utf8",
);
const sidebar = readFileSync(
  join(process.cwd(), "src/components/task-sidebar.tsx"),
  "utf8",
);

describe("Skills catalog page", () => {
  it("is a Studio route rendered through the unified manager", () => {
    expect(page).toContain("<StudioCapabilityManager />");
    expect(layout).toContain("<AuthProvider>");
    expect(component).toContain("<h1>技能</h1>");
  });

  it("combines platform packages with Skills already embedded in drafts", () => {
    expect(component).toContain("studioClient.listPlatformSkills");
    expect(component).toContain("studioClient.listAgentSkills");
    expect(component).not.toContain("studioClient.getDraft");
    expect(component).toContain("draft.skills");
    expect(component).toContain("暂无描述");
    expect(component).toContain("platform-package:");
  });

  it("installs an exact platform package revision as a draft snapshot", () => {
    expect(component).toContain("studioClient.installPlatformSkill");
    expect(component).toContain("selectedSkill.package.revision");
    expect(component).toContain("导入草稿快照");
    expect(component).toContain("已导入当前快照");
    expect(component).toContain("后续平台更新不会改变当前草稿快照");
    expect(component).toContain("contentHash");
    expect(component).toContain("license");
    expect(styles).toContain(".packageFacts");
    expect(styles).toContain(".installControls");
  });

  it("is discoverable from the unified workspace navigation", () => {
    expect(navigation).toContain('href: "/studio/skills"');
    expect(navigation).toContain('label: "技能"');
    expect(sidebar).toContain('active={activeNav}');
    expect(sidebar).toContain('visible={["knowledge", "agents", "automation", "skills"]}');
    expect(sidebar).not.toContain('capabilities');
  });

  it("supports search and a centered detail modal", () => {
    expect(component).toContain("搜索技能…");
    expect(component).toContain("useDialogFocus");
    expect(component).toContain("detailBackdrop");
    expect(component).toContain("detailDrawer");
    expect(component).toContain('aria-modal="true"');
    expect(styles).toMatch(/\.detailBackdrop\s*\{[^}]*place-items:\s*center/s);
    expect(component).toContain("用它创建 Skill");
    expect(component).toContain("前往智能体管理");
  });

  it("ships Skill Creator as the scoped conversational creation entry", () => {
    expect(component).toContain("DEFAULT_SKILL_CREATOR");
    expect(component).not.toContain("`platform:${DEFAULT_SKILL_CREATOR.name}`");
    expect(component).toContain('skillCreatorHref("personal")');
    expect(component).toContain('skillCreatorHref("platform")');
    expect(component).toContain('href="/studio/agents"');
    expect(component).toContain("个人 Skill");
    expect(component).toContain("平台 Skill");
    expect(component).toContain("Agent Skill");
  });

  it("uses the centered management-list layout", () => {
    expect(component).toContain("catalogToolbar");
    expect(component).toContain("刷新技能目录");
    expect(styles).toContain("width: min(1160px, 100%)");
    expect(styles).toMatch(/\.groups\s*\{[^}]*border:\s*1px solid/s);
  });

  it("right-aligns row actions and hides a row locally without claiming platform state", () => {
    // The toggle only writes a per-browser preference, so its labels must not read as
    // platform enablement ("已禁用"/"启用"): nothing here changes what the platform
    // serves. Wiring it to the catalog API means updating these labels too.
    expect(component).toContain("HIDDEN_SKILLS_STORAGE_KEY");
    expect(component).toContain("window.localStorage.setItem");
    expect(component).toContain("从本机列表隐藏");
    expect(component).toContain("（不改变平台启用状态）");
    expect(component).not.toContain('role="switch"');
    expect(styles).toMatch(/\.rowActions\s*\{[^}]*justify-content:\s*flex-end/s);
    expect(styles).toContain(".skillToggle[aria-pressed=\"true\"]");
  });

  it("loads agent skills in one request without per-draft reads", () => {
    expect(component).toContain("studioClient.listAgentSkills()");
    expect(component).not.toContain("forEachWithConcurrency");
    expect(component).toContain("loadGeneration.current");
  });

  it("renders the list in scroll pages", () => {
    expect(component).toContain("CATALOG_PAGE_SIZE");
    expect(component).toContain("IntersectionObserver");
    expect(component).toContain("pageSkills.map");
    expect(component).not.toContain("visibleSkills.map");
    expect(component).toContain("已显示");
    expect(component).toContain("加载更多");
    expect(styles).toContain(".loadMore");
    expect(styles).toContain(".loadMoreStatus");
  });
});
