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

describe("project-grouped task list", () => {
  it("renders 项目 above 任务 and drops the recency view", () => {
    const projectsIndex = sidebar.indexOf("<ProjectFolderIcon />\n                项目");
    const tasksIndex = sidebar.indexOf('>任务</span>');
    expect(projectsIndex).toBeGreaterThan(-1);
    expect(tasksIndex).toBeGreaterThan(projectsIndex);
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
    expect(css).toMatch(/\.task-project-heading\s*\{[^}]*padding:\s*4px 30px 4px 8px;/);
    expect(css).toMatch(/\.task-list-item,\s*[\s\S]*?\.task-list-item\.is-active\s*\{[^}]*padding:\s*4px 6px 4px 34px;/);
  });
});
