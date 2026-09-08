import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const files = readFileSync(
  join(process.cwd(), "src/components/my-files/my-files.tsx"),
  "utf8",
);
const account = readFileSync(
  join(process.cwd(), "src/components/account-menu.tsx"),
  "utf8",
);
const taskSidebar = readFileSync(
  join(process.cwd(), "src/components/task-sidebar.tsx"),
  "utf8",
);
const workspaceNavigation = readFileSync(
  join(process.cwd(), "src/components/workspace-navigation.tsx"),
  "utf8",
);
const route = readFileSync(
  join(process.cwd(), "src/app/api/harness/artifacts/route.ts"),
  "utf8",
);

describe("my files", () => {
  it("indexes generated deliverables with search, type filters and task provenance", () => {
    expect(files).toContain("我的文件");
    expect(files).toContain('fetch("/api/harness/artifacts?limit=500"');
    expect(files).toContain("搜索文件名、任务或智能体");
    expect(files).toContain("task_archived");
    expect(files).toContain("打开任务");
    expect(files).toContain("download");
  });

  it("removes the files entry from the task shell while preserving its authenticated direct route", () => {
    expect(taskSidebar).toContain('visible={["knowledge", "agents", "capabilities"]}');
    expect(taskSidebar).not.toContain('visible={["agents", "files"]}');
    expect(workspaceNavigation.indexOf('id: "files"')).toBeGreaterThan(
      workspaceNavigation.indexOf('id: "agents"'),
    );
    expect(account).not.toContain('href: "/studio/files"');
    expect(route).toContain("listArtifacts(request)");
    expect(files).toContain("/api/harness/artifacts/${encodeURIComponent(file.artifact_id)}");
  });
});
