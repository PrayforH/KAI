import { afterEach, expect, it, vi } from "vitest";
const { attach } = vi.hoisted(() => ({ attach: vi.fn() }));
vi.mock("../src/lib/studio-client", () => ({
  projectClient: { attachDocument: attach },
}));
import {
  addResultToProject,
  loadTaskSummary,
  loadProjectTasks,
  type ProjectArtifact,
} from "../src/lib/project-space";

const artifact: ProjectArtifact = {
  artifact_id: "result-1",
  name: "报告.txt",
  media_type: "text/plain",
  size_bytes: 6,
  thread_id: "source-task",
  thread_title: "历史任务",
  agent_name: "analyst",
  created_at: "2026-10-18",
};
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

it("copies an authorized output through the existing download and input upload routes before attaching", async () => {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url === "/api/harness/artifacts/result-1")
      return new Response("report", {
        headers: { "Content-Type": "text/plain" },
      });
    if (url === "/api/input-artifacts" && options?.method === "POST") {
      const file = (options.body as FormData).get("file") as File;
      expect(file.name).toBe("报告.txt");
      expect(await file.text()).toBe("report");
      return Response.json({ input_artifact_id: "collected-input" });
    }
    return new Response(null, { status: 404 });
  });
  vi.stubGlobal("fetch", fetcher);
  await addResultToProject("project-a", artifact);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(attach).toHaveBeenCalledWith("project-a", "collected-input");
});

it("leaves the project unchanged if the output cannot be read or uploaded", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(null, { status: 404 })),
  );
  await expect(addResultToProject("project-a", artifact)).rejects.toThrow(
    "无法读取",
  );
  expect(attach).not.toHaveBeenCalled();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(new Response("report"))
      .mockResolvedValueOnce(
        Response.json({ error: { message: "资料空间已满" } }, { status: 409 }),
      ),
  );
  await expect(addResultToProject("project-a", artifact)).rejects.toThrow(
    "资料空间已满",
  );
  expect(attach).not.toHaveBeenCalled();
});
