import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const navigation = readFileSync(
  join(process.cwd(), "src/components/workspace-navigation.tsx"),
  "utf8",
);
const taskSidebar = readFileSync(
  join(process.cwd(), "src/components/task-sidebar.tsx"),
  "utf8",
);

describe("workspace navigation ordering", () => {
  it("renders the visible allowlist in the caller's declared order", () => {
    // `visible` is ordered: filtering the canonical list would always render
    // 智能体 before 知识库, which is not what the task sidebar asks for.
    expect(navigation).toContain("visible.flatMap");
    expect(navigation).not.toContain("visible.includes(workspace.id)");
  });

  it("puts the knowledge base directly under 新建任务 in the task sidebar", () => {
    expect(taskSidebar).toContain(
      'visible={["knowledge", "agents", "capabilities"]}',
    );
    // 新建任务 stays its own primary action above the workspace navigation.
    expect(taskSidebar).toContain("task-sidebar-create");
    expect(taskSidebar.indexOf("task-sidebar-create")).toBeLessThan(
      taskSidebar.indexOf("<WorkspaceNavigation"),
    );
  });
});
