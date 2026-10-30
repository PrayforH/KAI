// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HandoverStrip } from "../src/components/handover-strip";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const handovers = [
  { handoverId: "h1", specialistId: "s1", objectiveId: "o1", messageId: "m1", summary: "", status: "sent", createdAt: "2026-10-30T03:00:00Z", updatedAt: "2026-10-30T03:00:00Z" },
  { handoverId: "h2", specialistId: "s1", objectiveId: "o1", messageId: "m2", summary: "", status: "pending_input", createdAt: "2026-10-30T04:00:00Z", updatedAt: "2026-10-30T04:00:00Z" },
];
const specialists = [
  { specialistId: "s1", name: "费用监控", slug: "cost", description: "", agentName: "a", agentVersion: "1", status: "needs_input", revision: 1, config: {}, createdAt: "", updatedAt: "" },
];

let root: Root;
let host: HTMLDivElement;
const fetcher = vi.fn();

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  fetcher.mockReset();
  fetcher.mockImplementation(async (url: string) =>
    Response.json(url.endsWith("/handovers") ? handovers : specialists),
  );
  vi.stubGlobal("fetch", fetcher);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it("shows a centered transfer line per handover with the receiving bot name", async () => {
  await act(async () => root.render(<HandoverStrip />));
  await act(async () => { await Promise.resolve(); });
  const lines = [...host.querySelectorAll(".handover-line")].map((n) => n.textContent ?? "");
  expect(lines.length).toBe(2);
  // 已发送 / 待补充 必须区分，且带上承接该职责的 bot 名
  expect(lines[0]).toContain("已发送消息给");
  expect(lines[0]).toContain("费用监控");
  expect(lines[1]).toContain("待补充");
});

it("renders nothing when there are no handovers", async () => {
  fetcher.mockImplementation(async (url: string) =>
    Response.json(url.endsWith("/handovers") ? [] : specialists),
  );
  await act(async () => root.render(<HandoverStrip />));
  await act(async () => { await Promise.resolve(); });
  expect(host.querySelector(".handover-strip")).toBeNull();
});
