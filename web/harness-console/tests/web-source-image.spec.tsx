// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { WebSourceImage } from "../src/components/web-source-image";

let root: Root;
let host: HTMLDivElement;
beforeEach(() => { host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
it("previews a source image and closes with Escape", async () => {
  await act(async () => root.render(<WebSourceImage src="https://cdn.example.com/news.jpg" alt="新闻现场" />));
  const image = host.querySelector("img")!;
  expect(image.getAttribute("loading")).toBe("lazy");
  expect(image.getAttribute("referrerpolicy")).toBe("no-referrer");
  await act(async () => host.querySelector("button")!.click());
  const dialog = document.body.querySelector('[role="dialog"]')!;
  expect(dialog.getAttribute("aria-label")).toBe("新闻现场");
  expect(dialog.querySelector("a")!.href).toBe("https://cdn.example.com/news.jpg");
  await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(document.body.querySelector('[role="dialog"]')).toBeNull();
});
it("keeps a source link when remote hotlinking fails", async () => {
  await act(async () => root.render(<WebSourceImage src="https://cdn.example.com/news.jpg" alt="新闻现场" />));
  await act(async () => host.querySelector("img")!.dispatchEvent(new Event("error")));
  expect(host.querySelector("img")).toBeNull();
  expect(host.querySelector("a")!.href).toBe("https://cdn.example.com/news.jpg");
  expect(host.textContent).toContain("图片无法加载");
});
