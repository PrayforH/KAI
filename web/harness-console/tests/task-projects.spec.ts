import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const sidebar = readFileSync(
  join(process.cwd(), "src/components/task-sidebar.tsx"),
  "utf8",
);
const headerActions = readFileSync(
  join(process.cwd(), "src/components/task-header-actions.tsx"),
  "utf8",
);
const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");
const client = readFileSync(join(process.cwd(), "src/lib/studio-client.ts"), "utf8");
const workbench = readFileSync(
  join(process.cwd(), "src/components/workbench-rail.tsx"),
  "utf8",
);
const projectPanel = readFileSync(
  join(process.cwd(), "src/components/project-resources-panel.tsx"),
  "utf8",
);
const projectRoute = readFileSync(
  join(process.cwd(), "src/app/studio/projects/[projectId]/page.tsx"),
  "utf8",
);

describe("project-grouped task list", () => {
  it("drops the recency view", () => {
    // 最近/今天/昨天 buckets were replaced by the project containers.
    expect(sidebar).not.toContain("TASK_TIME_BUCKET_LABELS");
    expect(sidebar).not.toContain("recentBuckets");
  });

  it("groups tasks by project and keeps everything else in 任务", () => {
    expect(sidebar).toContain("task.project_id === project.projectId");
    expect(sidebar).toContain("!task.project_id || !known.has(task.project_id)");
    expect(sidebar).toContain('data-project-id={project.projectId}');
    expect(sidebar).toContain('aria-label="任务"');
  });

  it("triggers a project task, pins and removes from the project menu", () => {
    expect(sidebar).toContain('aria-label={`项目操作 ${project.name}`}');
    expect(sidebar).toContain("触发任务");
    expect(sidebar).toContain("置顶");
    expect(sidebar).toContain("移除项目");
    expect(sidebar).not.toContain("<span>查看项目</span>");
    expect(sidebar).not.toContain("编辑项目");
    expect(sidebar).toContain("useConfirmationDialog");
    expect(sidebar).toContain("查看 ${project.name} 的文件");
    expect(sidebar).toContain("<ProjectFilesDialog");
    expect(projectRoute).toContain('redirect("/")');
    expect(page).toContain("projectId={activeProjectId}");
    expect(workbench).toContain("projectId");
    expect(workbench).toContain("<ProjectResourcesPanel projectId={projectId} />");
  });

  it("shows project knowledge, files and skills beside the task conversation", () => {
    expect(projectPanel).toContain('aria-label="项目资源"');
    expect(projectPanel).toContain("知识库");
    expect(projectPanel).toContain("文件和资源");
    expect(projectPanel).toContain("技能");
    expect(projectPanel).not.toContain("记忆");
    expect(projectPanel).toContain("<ProjectKnowledgeDialog");
    expect(projectPanel).toContain("<ProjectFilesDialog");
    expect(projectPanel).toContain("<ProjectSkillsDialog");
  });

  it("opens a dedicated dialog for each project resource card", () => {
    const dialogs = readFileSync(
      join(process.cwd(), "src/components/project-resource-dialogs.tsx"),
      "utf8",
    );
    expect(dialogs).toContain("export function ProjectKnowledgeDialog");
    expect(dialogs).toContain("export function ProjectSkillsDialog");
    expect(dialogs).toContain("export function ProjectFilesDialog");
    expect(dialogs).toContain("搜索文件和资源");
    expect(dialogs).toContain("搜索技能、标签或来源");
    expect(dialogs).toContain("添加网页");
    expect(dialogs).toContain("project_id=${encodeURIComponent(project.projectId)}");
    expect(dialogs).toContain("任务产物");
    expect(dialogs).toContain("/content?inline=1");
    expect(client).toContain("knowledgeReferences?: string[]");
    expect(client).toContain("addLink(projectId: string");
    expect(client).toContain("deleteLink(projectId: string");
  });

  it("keeps tasks visible when their project disappears", () => {
    // A deleted project must not hide its tasks from both sections.
    expect(sidebar).toContain("const known = new Set(projects.map((project) => project.projectId))");
  });

  it("offers project creation from the sidebar", () => {
    expect(sidebar).toContain('aria-label="新建项目"');
    expect(sidebar).toContain("<ProjectCreateDialog");
    expect(page).not.toContain("window.prompt");
  });

  it("moves tasks in and out of projects from the task menu", () => {
    expect(headerActions).toContain("moveToProject");
    expect(headerActions).toContain('NestedMenu label="移入项目"');
    expect(headerActions).toContain("移出当前项目");
    expect(headerActions).toContain("setTaskProject(task.thread_id, projectId)");
    expect(client).toContain("export async function setTaskProject");
    expect(client).toContain("projectId");
  });

  it("refreshes the list after a move", () => {
    expect(page).toMatch(/refreshProjects\(\);\s*\n\s*window\.dispatchEvent\(new CustomEvent\("harness:task-list-changed"\)\);/);
  });
});

describe("project and task sections share one column", () => {
  const css = readFileSync(join(process.cwd(), "src/app/web-codex.css"), "utf8");

  it("scrolls 项目 and 任务 together instead of leaving a gap", () => {
    // The sidebar is a fixed-row grid: a second flexible child would stretch the
    // project list and strand the task list at the bottom.
    expect(sidebar).toContain('className="task-list-scroll"');
    expect(css).toMatch(/\.task-list-scroll\s*\{[^}]*overflow-y:\s*auto;/);
    expect(css).toMatch(/\.task-list-scroll\s*\{[^}]*min-height:\s*0;/);
    const opens = (sidebar.match(/className="task-list-scroll"/g) ?? []).length;
    expect(opens).toBe(1);
  });

  it("uses compact project padding and a small indent for child tasks", () => {
    expect(css).toMatch(/\.task-project-heading\s*\{[^}]*padding:\s*4px 90px 4px 8px;/);
    expect(css).toMatch(/\.task-list-item,\s*[\s\S]*?\.task-list-item\.is-active\s*\{[^}]*padding:\s*4px 6px 4px 34px;/);
  });
});

describe("sidebar rows match the sidebar children", () => {
  const appStyles = readFileSync(join(process.cwd(), "src/app/styles.css"), "utf8");

  it("keeps the account block in the last row so it stays at the bottom", () => {
    // brand · 新建任务 · nav · the scrolling 项目/任务 column · account
    expect(appStyles).toMatch(
      /\.task-sidebar\s*\{[^}]*grid-template-rows:\s*auto auto auto minmax\(0,\s*1fr\) auto;/,
    );
    expect(appStyles).not.toMatch(
      /\.task-sidebar\s*\{[^}]*grid-template-rows:\s*auto auto auto auto minmax\(0,\s*1fr\) auto;/,
    );
    // The list region and the account block are both direct children of it.
    expect(sidebar).toContain('className="task-list-scroll"');
    expect(sidebar).toContain('className="task-sidebar-account"');
  });
});
