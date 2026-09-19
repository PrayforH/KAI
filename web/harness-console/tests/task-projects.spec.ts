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
    expect(sidebar).toContain("byRecency.filter((task) => !task.project_id)");
    expect(sidebar).toContain('data-project-id={project.projectId}');
    expect(sidebar).toContain('aria-label="任务"');
  });

  it("offers project creation from the sidebar", () => {
    expect(sidebar).toContain('aria-label="新建项目"');
    expect(sidebar).toContain("onCreateProject?.()");
    expect(page).toContain("projectClient.create(");
  });

  it("moves tasks in and out of projects from the task menu", () => {
    expect(headerActions).toContain("moveToProject");
    expect(headerActions).toContain("移入「");
    expect(headerActions).toContain("移出项目");
    expect(headerActions).toContain("setTaskProject(task.thread_id, projectId)");
    expect(client).toContain("export async function setTaskProject");
    expect(client).toContain("projectId");
  });

  it("refreshes the list and the project counts after a move", () => {
    expect(page).toMatch(/refreshProjects\(\);\s*\n\s*window\.dispatchEvent\(new CustomEvent\("harness:task-list-changed"\)\);/);
  });
});
