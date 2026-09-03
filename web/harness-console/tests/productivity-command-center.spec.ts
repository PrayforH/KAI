import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  productivityCommandKey,
  productivityCommandResults,
} from "../src/lib/productivity-command-center";
import type { TaskAgent } from "../src/lib/task-agent-catalog";
import type { TaskSummary } from "../src/lib/task-history";

const commandCenterComponent = readFileSync(
  join(process.cwd(), "src/components/productivity-command-center.tsx"),
  "utf8",
);

const tasks: TaskSummary[] = [
  {
    thread_id: "thread-old",
    session_id: "session-old",
    title: "整理周报",
    agent_name: "writer",
    agent_version: "1.0.0",
    agent_owner_user_id: "user-1",
    status: "succeeded",
    created_at: "2026-08-08T08:00:00Z",
    updated_at: "2026-08-08T08:00:00Z",
  },
  {
    thread_id: "thread-new",
    session_id: "session-new",
    title: "分析架构风险",
    agent_name: "architect",
    agent_version: "2.0.0",
    agent_owner_user_id: "user-1",
    status: "running",
    created_at: "2026-08-09T08:00:00Z",
    updated_at: "2026-08-10T08:00:00Z",
  },
];

const agents: TaskAgent[] = [
  {
    name: "writer",
    version: "1.0.0",
    displayName: "文档助手",
    domain: "writing",
    ownerUserId: "user-1",
    scope: "personal",
  },
  {
    name: "researcher",
    version: "1.2.0",
    displayName: "团队研究员",
    domain: "research",
    ownerUserId: "user-2",
    scope: "team",
    spaceId: "space-1",
    spaceName: "产品团队",
  },
];

describe("productivity command center", () => {
  it("combines actions, newest tasks and usable agents without data or operations routes", () => {
    const results = productivityCommandResults("", tasks, agents);
    expect(results[0]).toMatchObject({ kind: "action", id: "new-task" });
    expect(results[0]).not.toHaveProperty("shortcut");
    expect(results.find((item) => item.kind === "task")).toMatchObject({
      id: "thread-new",
    });
    expect(results.some((item) => item.kind === "agent" && item.title === "团队研究员")).toBe(true);
    expect(results.some((item) => item.title.includes("数据") || item.title.includes("运营"))).toBe(false);
  });

  it("searches Chinese labels, technical aliases and task status", () => {
    expect(productivityCommandResults("MCP", tasks, agents)).toEqual([]);
    expect(productivityCommandResults("智能体", tasks, agents)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "action", id: "studio-agents" }),
      ]),
    );
    expect(productivityCommandResults("运行中", tasks, agents)).toEqual([
      expect.objectContaining({ kind: "task", id: "thread-new" }),
    ]);
    expect(productivityCommandResults("产品团队", tasks, agents)).toEqual([
      expect.objectContaining({ kind: "agent", title: "团队研究员" }),
    ]);
  });

  it("uses scoped concrete agent coordinates as stable option keys", () => {
    const result = productivityCommandResults("团队研究员", tasks, agents)[0];
    expect(result).toBeDefined();
    expect(productivityCommandKey(result!)).toContain("team:space-1:user-2:researcher@1.2.0");
  });

  it("traps modal focus and restores the element that opened it", () => {
    expect(commandCenterComponent).toContain("previousFocusRef");
    expect(commandCenterComponent).toContain("dialogRef.current?.querySelectorAll");
    expect(commandCenterComponent).toContain('event.key === "Escape"');
    expect(commandCenterComponent).toContain("previous?.isConnected");
  });
});
