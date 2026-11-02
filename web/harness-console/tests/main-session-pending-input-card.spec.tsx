// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HandoverInlineRows } from "../src/components/handover-row";
import { emitHandover } from "./helpers/handover";
const request = vi.hoisted(() => vi.fn());
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: request }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
let executed: boolean;
const detail = () => ({ specialist: { specialistId: "pending", name: "按需协同", status: "ready", revision: 3, config: {} },
  threadId: "original", primaryObjectiveId: "objective-one", duties: [{ objectiveId: "objective-one", hasExecutions: executed }] });
beforeEach(() => {
  executed = false; request.mockReset();
  request.mockImplementation(async (path: string, method = "GET") => {
    if (path === "handovers" || path === "specialists") return [];
    if (path === "specialists/pending" && method === "GET") return detail();
    if (path === "specialists/pending/config") return { ...detail().specialist, revision: 4 };
    if (path === "specialists/pending/status") return { ...detail().specialist, status: "active", revision: 5 };
    if (path === "objectives/objective-one/executions") { executed = true; return {}; }
    throw new Error(`Unexpected request ${method} ${path}`);
  });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ messages: [] })));
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function mount() {
  await act(async () => {
    emitHandover({ handover_id: "pending-handover", specialist_id: "pending", specialist_name: "按需协同", objective_id: "objective-one", status: "pending_input" });
    root.render(<HandoverInlineRows messageId="assistant-run-ui-test" />);
  });
}
it("keeps a genuine confirmation and starts only that one-off handover after the click", async () => {
  await mount();
  expect(host.querySelector('[aria-label="补齐子助理配置"]')).not.toBeNull();
  expect(host.querySelector(".specialist-setup-credentials")).toBeNull();
  expect(request.mock.calls.some(([path]) => path.endsWith("/executions"))).toBe(false);
  await act(async () => host.querySelector<HTMLButtonElement>(".specialist-setup-submit")!.click());
  const mutations = request.mock.calls.filter(([, method]) => method === "POST");
  expect(mutations.map(([path]) => path)).toEqual(["specialists/pending/config", "specialists/pending/status", "objectives/objective-one/executions"]);
  expect(mutations[0][2]).toEqual({ config: { notify_channel: "inbox" }, expectedRevision: 3 });
  expect(mutations[2][3]).toBe("first-handover:objective-one");
  expect(host.querySelector(".specialist-setup-card")).toBeNull();
  expect(host.textContent).toContain("已发送消息给");
});
it("availability alone cannot dismiss an unstarted handover confirmation", async () => {
  await mount();
  expect(host.textContent).toContain("已发送消息给");
  expect(host.querySelector(".specialist-setup-submit")).not.toBeNull();
});
it("an existing real execution settles a stale pending receipt without starting another", async () => {
  executed = true;
  await mount();
  expect(host.textContent).toContain("已发送消息给");
  expect(host.querySelector(".specialist-setup-card")).toBeNull();
  expect(request.mock.calls.some(([, method]) => method === "POST")).toBe(false);
});
