import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  groupTaskAgents,
  taskAgentSwitchMode,
  type TaskAgentGroup,
} from "../src/components/task-agent-switcher";
import type { TaskAgent } from "../src/lib/task-agent-catalog";

const component = readFileSync(
  join(process.cwd(), "src/components/task-agent-switcher.tsx"),
  "utf8",
);

const agents: TaskAgent[] = [
  {
    name: "research",
    version: "0.1.2",
    displayName: "联网研究",
    domain: "research",
  },
  {
    name: "writing",
    version: "0.1.0",
    displayName: "公文写作",
    domain: "writing",
  },
  {
    name: "research",
    version: "0.1.10",
    displayName: "联网研究",
    domain: "research",
  },
];

describe("task agent switcher", () => {
  it("groups many published versions under one agent and sorts versions newest first", () => {
    const groups: TaskAgentGroup[] = groupTaskAgents(agents);
    const research = groups.find((group) => group.name === "research");

    expect(groups).toHaveLength(2);
    expect(research?.agents.map((agent) => agent.version)).toEqual([
      "0.1.10",
      "0.1.2",
    ]);
  });

  it("defaults to the newest published version even when the current pointer is older", () => {
    const groups = groupTaskAgents([
      { ...agents[0], version: "0.2.0", currentVersion: "0.1.10" },
      { ...agents[0], version: "0.1.10", currentVersion: "0.1.10" },
    ]);

    expect(groups[0]?.agents.map((agent) => agent.version)).toEqual([
      "0.2.0",
      "0.1.10",
    ]);
  });

  it("searches display name, coordinate, version and domain", () => {
    expect(groupTaskAgents(agents, "公文")).toHaveLength(1);
    expect(groupTaskAgents(agents, "0.1.10")[0]?.name).toBe("research");
    expect(groupTaskAgents(agents, "research")[0]?.agents).toHaveLength(2);
    expect(groupTaskAgents(agents, "missing")).toEqual([]);
  });

  it("distinguishes current, in-thread version and new-task Agent switches", () => {
    expect(taskAgentSwitchMode(agents[0], agents[0])).toBe("current");
    expect(taskAgentSwitchMode(agents[0], agents[2])).toBe("version");
    expect(taskAgentSwitchMode(agents[0], agents[1])).toBe("new-task");
    expect(taskAgentSwitchMode(null, agents[0])).toBe("new-task");
  });

  it("separates compact agent and version lists and refreshes on open", () => {
    expect(component).toContain('aria-haspopup="dialog"');
    expect(component).toContain('role="dialog"');
    expect(component).toContain('aria-pressed={groupActive}');
    expect(component).toContain('type="search"');
    expect(component).toContain('kind === "version"');
    expect(component).toContain('const preferred = group.agents[0]');
    expect(component).toContain('onRefresh?.()');
    expect(component).toContain('style={{height: menuHeight, left: mobileLeft}}');
    expect(component).not.toContain('<select');
    expect(component).toContain('document.addEventListener("focusin", closeFromFocus)');
    expect(component).toContain('closeMenu(true)');
    expect(component).toContain('disabled={currentTaskBusy}');
  });
});
