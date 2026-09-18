// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { RailFilePreview, parseCsvRows, previewKindFor } from "../src/components/rail-file-preview";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement | undefined;

afterEach(() => {
  if (host) { act(() => { host!.remove(); }); host = undefined; }
  vi.unstubAllGlobals();
});

async function render(target: Parameters<typeof RailFilePreview>[0]["target"]) {
  host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<RailFilePreview target={target} />);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  return host;
}

const target = (name: string, media_type: string, extra: Record<string, unknown> = {}) => ({
  artifact_id: "a1", name, media_type, size_bytes: 100, thread_id: "t1", ...extra,
});

it("routes each file type to the right preview", () => {
  expect(previewKindFor("text/markdown", "report.md")).toBe("markdown");
  expect(previewKindFor("text/csv", "rows.csv")).toBe("csv");
  expect(previewKindFor("application/json", "data.json")).toBe("json");
  expect(previewKindFor("text/plain", "notes.txt")).toBe("code");
  expect(previewKindFor("text/x-python", "build_flow.py")).toBe("code");
  expect(previewKindFor("image/png", "chart.png")).toBe("image");
  expect(previewKindFor("application/pdf", "deck.pdf")).toBe("pdf");
  expect(previewKindFor("text/html", "flow.html")).toBe("html");
  expect(previewKindFor("application/octet-stream", "flow.htm")).toBe("html");
  expect(previewKindFor("application/zip", "bundle.zip")).toBe("none");
  // A .sqlite file shipped as octet-stream still falls back to download.
  expect(previewKindFor("application/octet-stream", "db.sqlite")).toBe("none");
});

it("parses quoted csv rows without losing fields", () => {
  const rows = parseCsvRows('name,note\n"a,b","say ""hi"""\nplain,line\n');
  expect(rows[0]).toEqual(["name", "note"]);
  expect(rows[1]).toEqual(["a,b", 'say "hi"']);
  expect(rows[2]).toEqual(["plain", "line"]);
});

it("renders markdown content fetched from the artifact route", async () => {
  const fetcher = vi.fn(async (_input: RequestInfo | URL) =>
    new Response("# 标题\n\n正文", { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const container = await render(target("report.md", "text/markdown"));
  expect(fetcher.mock.calls[0][0]).toContain("/api/harness/artifacts/a1");
  expect(fetcher.mock.calls[0][0]).toContain("preview=1");
  expect(container.textContent).toContain("标题");
  expect(container.querySelector(".rail-preview-download")?.getAttribute("href")).toContain("/api/harness/artifacts/a1");
});

it("does not fetch a file it cannot preview and offers the download instead", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const container = await render(target("bundle.zip", "application/zip"));
  expect(fetcher).not.toHaveBeenCalled();
  expect(container.textContent).toContain("暂不支持在线预览");
  expect(container.querySelector(".rail-preview-download")).not.toBeNull();
});

it("renders a generated page in the rail without downloading or trusting it", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const container = await render(target("flow.html", "text/html"));
  const frame = container.querySelector("iframe.rail-preview-html");

  expect(fetcher).not.toHaveBeenCalled();
  expect(frame?.getAttribute("src")).toContain("/api/harness/artifacts/a1");
  expect(frame?.getAttribute("title")).toBe("flow.html");
  // The page runs without the console origin, so its scripts cannot reach the session.
  expect(frame?.getAttribute("sandbox")).toContain("allow-scripts");
  expect(frame?.getAttribute("sandbox")).not.toContain("allow-same-origin");
});

it("shows oversized files without pulling them into the panel", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const container = await render(target("huge.txt", "text/plain", { size_bytes: 9_000_000 }));
  expect(fetcher).not.toHaveBeenCalled();
  expect(container.textContent).toContain("文件较大");
});

it("keeps the file rows unfilled until they are hovered or open", () => {
  // The row became a button: without an explicit base fill the browser paints its
  // own grey, which made every file look selected at once.
  const experience = readFileSync(
    join(process.cwd(), "src/app/conversation-experience.css"),
    "utf8",
  );
  const rule = /\.rail-file-row \.workbench-rail-file \{([^}]*)\}/.exec(experience)?.[1];

  expect(rule).toContain("background: transparent");
  expect(rule).toContain("border: 0");
});
