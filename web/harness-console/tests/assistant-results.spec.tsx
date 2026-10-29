// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { AssistantResults } from "../src/components/assistant-results";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";
import { PersonalAssistantEntry } from "../src/components/personal-assistant-context";
vi.mock("../src/components/rail-file-preview", () => ({ RailFilePreview: ({ target }: { target: { name: string } }) => <div data-preview>{target.name}</div> }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const host = document.createElement("div");
let root = createRoot(host);
afterEach(() => { act(() => root.unmount()); host.replaceChildren(); root = createRoot(host); });
const files = [{ artifact_id: "artifact-a", run_id: "run", thread_id: "personal thread", name: "报告.html", media_type: "text/html", size_bytes: 2048 }];

it("shows one owned file with working preview and download; survives history remount", () => {
  act(() => root.render(<AssistantResults artifacts={[...files, ...files]} />));
  expect(host.querySelectorAll("article")).toHaveLength(1);
  const download = host.querySelector("a")!;
  expect(download.getAttribute("href")).toBe("/api/harness/artifacts/artifact-a?thread_id=personal%20thread");
  expect(download.getAttribute("download")).toBe("报告.html");
  act(() => (host.querySelector("button") as HTMLButtonElement).click());
  expect(host.querySelector("[data-preview]")?.textContent).toBe("报告.html");
  act(() => root.render(<AssistantResults key="refresh" artifacts={files} />));
  expect(host.querySelector("a")?.getAttribute("href")).toBe(download.getAttribute("href"));
  expect(host.querySelector("[data-preview]")).toBeNull();
  expect(host.textContent).not.toContain("artifact-a");
});

it("keeps assistant process collapsed while allowing explicit inspection", () => {
  const activity = runActivitySchema.parse({ run_id: "compact-results-test", status: "running", started_at: "2026-10-29T00:00:00Z", items: [{ id: "one", kind: "tool", event_type: "tool.request", timestamp: "2026-10-29T00:00:00Z", sequence: 1, title: "读取内部文件", status: "running", metadata: { name: "Read", arguments: { file_path: "internal.txt" } } }] });
  act(() => root.render(<PersonalAssistantEntry><ActivitySummary activity={activity} /></PersonalAssistantEntry>));
  expect(host.querySelector("section")?.getAttribute("data-open")).toBe("false");
  act(() => (host.querySelector("button.execution-disclosure") as HTMLButtonElement).click());
  expect(host.querySelector("section")?.getAttribute("data-open")).toBe("true");
});
