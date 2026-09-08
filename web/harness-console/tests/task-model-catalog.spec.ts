import { afterEach, describe, expect, it, vi } from "vitest";
import {
  loadTaskModelRoutes,
  loadTaskModelOverride,
  saveTaskModelOverride,
} from "../src/lib/task-model-catalog";

afterEach(() => vi.unstubAllGlobals());

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

describe("task model selection", () => {
  it("exposes DeepSeek Pro and Flash as separate task routes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      modelRoutes: [
        {
          routeId: "new-api-default",
          label: "DeepSeek V4（兼容路由）",
          provider: "deepseek",
          models: ["deepseek-v4-pro"],
          capabilities: ["streaming", "tool_use"],
          enabled: true,
        },
        {
          routeId: "deepseek-v4-flash",
          label: "DeepSeek V4 Flash",
          provider: "deepseek",
          models: ["deepseek-v4-flash"],
          capabilities: ["streaming", "tool_use"],
          enabled: true,
        },
        {
          routeId: "deepseek-v4-pro",
          label: "DeepSeek V4 Pro",
          provider: "deepseek",
          models: ["deepseek-v4-pro"],
          capabilities: ["streaming", "tool_use"],
          enabled: true,
        },
        {
          routeId: "anthropic-official",
          label: "Anthropic official",
          provider: "anthropic",
          models: ["claude-sonnet-4-6"],
          capabilities: ["streaming", "tool_use", "tool_search"],
          enabled: true,
        },
      ],
    }), { status: 200 })));

    await expect(loadTaskModelRoutes()).resolves.toEqual([
      expect.objectContaining({ id: "deepseek-v4-flash", model: "deepseek-v4-flash" }),
      expect.objectContaining({ id: "deepseek-v4-pro", model: "deepseek-v4-pro" }),
    ]);
  });

  it("persists a task-scoped route without changing the Agent", () => {
    const storage = memoryStorage();

    saveTaskModelOverride(storage, "thread-1", "minimax-m3");

    expect(loadTaskModelOverride(storage, "thread-1")).toBe("minimax-m3");
    expect(loadTaskModelOverride(storage, "thread-2")).toBeNull();
  });

  it("exposes video generation in the task composer while keeping image routes out", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      modelRoutes: [
        {
          routeId: "vision-primary",
          label: "视觉主模型",
          provider: "example",
          models: ["vision-1"],
          modelType: "vision",
          capabilities: ["streaming", "tool_use", "vision"],
          enabled: true,
        },
        {
          routeId: "image-primary",
          label: "图像生成",
          provider: "example",
          models: ["image-1"],
          modelType: "image_generation",
          capabilities: ["image_generation"],
          enabled: true,
        },
        {
          routeId: "video-primary",
          label: "视频生成",
          provider: "MiniMax",
          models: ["/model"],
          modelType: "video_generation",
          capabilities: ["video_generation"],
          enabled: true,
        },
      ],
    }), { status: 200 })));

    await expect(loadTaskModelRoutes()).resolves.toEqual([
      expect.objectContaining({ id: "vision-primary", modelType: "vision" }),
      expect.objectContaining({ id: "video-primary", modelType: "video_generation" }),
    ]);
  });

  it("clears follow-Agent selection and rejects malformed stored routes", () => {
    const storage = memoryStorage();
    storage.setItem("agent-studio.task-model:thread-1", "../../secret");
    expect(loadTaskModelOverride(storage, "thread-1")).toBeNull();

    saveTaskModelOverride(storage, "thread-1", "minimax-m3");
    saveTaskModelOverride(storage, "thread-1", null);
    expect(loadTaskModelOverride(storage, "thread-1")).toBeNull();
  });
});
