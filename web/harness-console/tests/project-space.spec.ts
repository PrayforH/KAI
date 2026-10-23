import { afterEach, expect, it, vi } from "vitest";
import { loadTaskSummary, loadProjectTasks } from "../src/lib/project-space";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("treats an unsaved task as a draft while still surfacing an inaccessible project", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(null, { status: 404 })),
  );
  expect(await loadTaskSummary("local-draft")).toBeNull();
  await expect(loadProjectTasks("missing-project")).rejects.toThrow(
    "没有访问权限",
  );
});
