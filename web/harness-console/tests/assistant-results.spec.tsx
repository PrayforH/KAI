// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ConversationSurfaceProvider } from "../src/components/assistant-bot-context";
import { AssistantResults } from "../src/components/assistant-results";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";
import { PersonalAssistantEntry } from "../src/components/personal-assistant-context";
vi.mock("../src/components/rail-file-preview", () => ({
  RailFilePreview: ({ target }: { target: { name: string } }) => <div data-preview>{target.name}</div>,
  artifactContentUrl: () => "/api/harness/artifacts/artifact-a?preview=1",
  artifactDownloadUrl: () => "/api/harness/artifacts/artifact-a",
}));
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
  // The preview is a centered dialog outside the message, not an inline card.
  const dialog = document.querySelector("[role='dialog']");
  expect(dialog?.getAttribute("aria-label")).toBe("预览 报告.html");
  expect(dialog?.querySelector("[data-preview]")?.textContent).toBe("报告.html");
  act(() => (dialog?.querySelector("[aria-label='关闭预览']") as HTMLButtonElement).click());
  expect(document.querySelector("[role='dialog']")).toBeNull();
  act(() => (host.querySelector("button") as HTMLButtonElement).click());
  expect(document.querySelector("[role='dialog'] [data-preview]")?.textContent).toBe("报告.html");
  act(() => root.render(<AssistantResults key="refresh" artifacts={files} />));
  expect(document.querySelector("[role='dialog']")).toBeNull();
  expect(host.querySelector("a")?.getAttribute("href")).toBe(download.getAttribute("href"));
  expect(host.textContent).not.toContain("artifact-a");
});

it("keeps assistant process collapsed while allowing explicit inspection", () => {
  const activity = runActivitySchema.parse({ run_id: "compact-results-test", status: "running", started_at: "2026-10-29T00:00:00Z", items: [{ id: "one", kind: "tool", event_type: "tool.request", timestamp: "2026-10-29T00:00:00Z", sequence: 1, title: "读取内部文件", status: "running", metadata: { name: "Read", arguments: { file_path: "internal.txt" } } }] });
  act(() => root.render(<PersonalAssistantEntry><ConversationSurfaceProvider surface="assistant"><ActivitySummary activity={activity} /></ConversationSurfaceProvider></PersonalAssistantEntry>));
  expect(host.querySelector("section")?.getAttribute("data-open")).toBe("false");
  act(() => (host.querySelector("button.execution-disclosure") as HTMLButtonElement).click());
  expect(host.querySelector("section")?.getAttribute("data-open")).toBe("true");
});

it("merges identical historical publications and preserves the first preview target on refresh", () => {
  const first = { ...files[0], sha256: "a".repeat(64) };
  const duplicate = { ...first, artifact_id: "artifact-collected-copy" };
  act(() => root.render(<AssistantResults artifacts={[first, duplicate]} />));
  expect(host.querySelectorAll("article")).toHaveLength(1);
  expect(host.querySelector("a")?.getAttribute("href")).toContain("/artifact-a?");
  act(() => root.render(<AssistantResults key="refresh" artifacts={[first, duplicate]} />));
  expect(host.querySelectorAll("article")).toHaveLength(1);
  expect(host.querySelector("a")?.getAttribute("href")).toContain("/artifact-a?");
});

it.each([
  { sha256: "b".repeat(64) },
  { sha256: undefined },
  { sha256: "not-a-hash" },
  { name: "另一份报告.html" },
  { media_type: "text/plain" },
  { run_id: "another-run" },
  { thread_id: "another-thread" },
])("keeps different or unverifiable deliverables visible: %j", (difference) => {
  const first = { ...files[0], sha256: "a".repeat(64) };
  act(() => root.render(<AssistantResults artifacts={[
    first, { ...first, ...difference, artifact_id: "artifact-other" },
  ]} />));
  expect(host.querySelectorAll("article")).toHaveLength(2);
});

it("opens the file in the shared dialog instead of an inline card", () => {
  const source = readFileSync(join(process.cwd(), "src/components/assistant-results.tsx"), "utf8");
  expect(source).toContain("<PreviewDialog");
  expect(source).toContain("onClose={() => setSelected(null)}");
  expect(source).not.toContain("收起预览");
  expect(source).not.toContain("<RailFilePreview");
  const css = readFileSync(join(process.cwd(), "src/components/assistant-results.module.css"), "utf8");
  expect(css).not.toContain("rail-preview");
  const dialog = readFileSync(join(process.cwd(), "src/components/preview-dialog.module.css"), "utf8");
  // The panel is centered, capped and scrolls only its own body.
  expect(dialog).toContain("height: min(86vh, 920px)");
  expect(dialog).toContain("border-radius: 20px");
  expect(dialog).toContain("position: fixed");
});
