// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { useSidebarProjects } from "../src/lib/sidebar-state";
import { invalidateClientReads } from "../src/lib/client-read-cache";
const { list } = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("../src/lib/studio-client", () => ({ projectClient: { list } }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let state: ReturnType<typeof useSidebarProjects>;
const project = { projectId: "p1", name: "Project" };
function Harness({ user = "a" }: { user?: string }) { state = useSidebarProjects(user); return <div>{state[0].map((p) => p.name).join(",")}</div>; }
beforeEach(() => { invalidateClientReads(); list.mockReset(); list.mockResolvedValue([project]); });
afterEach(() => invalidateClientReads());
it("keeps the project directory present across remounts and failed refreshes", async () => {
  const host = document.createElement("div"); let root = createRoot(host);
  try {
    await act(async () => root.render(<Harness />));
    expect(host.textContent).toBe("Project");
    await act(async () => root.unmount()); root = createRoot(host);
    await act(async () => root.render(<Harness />));
    expect(host.textContent).toBe("Project"); expect(list).toHaveBeenCalledTimes(1);
    list.mockRejectedValueOnce(new Error("offline"));
    await act(async () => state[1]());
    expect(host.textContent).toBe("Project");
  } finally { await act(async () => root.unmount()); }
});
it("does not display another account's directory", async () => {
  const host = document.createElement("div"); const root = createRoot(host);
  try {
    await act(async () => root.render(<Harness />));
    list.mockImplementationOnce(() => new Promise(() => {}));
    await act(async () => root.render(<Harness user="b" />));
    expect(host.textContent).toBe("");
  } finally { await act(async () => root.unmount()); }
});
