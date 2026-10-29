import { expect, it } from "vitest";
import { builderModelRoutes, preferredBuilderModel } from "../src/lib/builder-model-selection";
import type { TaskModelRoute } from "../src/lib/task-model-catalog";
const routes: TaskModelRoute[] = [
  { id: "other", label: "Other", model: "other", provider: "test", capabilities: [], modelType: "chat", apiFormat: "openai_compatible" },
  { id: "deepseek-v4-flash", label: "Flash", model: "flash", provider: "test", capabilities: [], modelType: "chat", apiFormat: "anthropic_compatible" },
  { id: "video", label: "Video", model: "video", provider: "test", capabilities: [], modelType: "video_generation" },
];
it("shares enabled task models while excluding video and incompatible execution formats", () => {
  expect(builderModelRoutes(routes).map(r => r.id)).toEqual(["other", "deepseek-v4-flash"]);
  expect(builderModelRoutes(routes, "codex-app-server").map(r => r.id)).toEqual(["other"]);
  expect(builderModelRoutes(routes, "deepagents").map(r => r.id)).toEqual(["other", "deepseek-v4-flash"]);
  expect(builderModelRoutes(routes, "claude-agent-sdk").map(r => r.id)).toEqual(["deepseek-v4-flash"]);
});
it("keeps the user's model and picks a builder default independently of the agent", () => {
  expect(preferredBuilderModel(routes, "other")).toBe("other");
  expect(preferredBuilderModel(routes, null)).toBe("deepseek-v4-flash");
  expect(preferredBuilderModel([], "retired")).toBeNull();
});
