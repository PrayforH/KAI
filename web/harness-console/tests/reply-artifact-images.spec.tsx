// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { ReplyArtifactImages, replyImageArtifacts } from "../src/components/reply-artifact-images";
import type { ArtifactDetails } from "../src/components/artifact-list";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const image: ArtifactDetails = { artifact_id: "图/1", run_id: "run-1", thread_id: "task/1", name: "成果/对比图.png", media_type: "image/png" };
let host: HTMLDivElement;
let root: Root;
beforeEach(() => { host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });
it("renders final images inline and asks for an expanded preview of the clicked file", () => {
  const listener = vi.fn(); window.addEventListener("harness:preview-artifact", listener);
  try {
    act(() => root.render(<ReplyArtifactImages artifacts={[image]} answer="图片已生成。" />));
    const element = host.querySelector("img")!;
    expect(element.alt).toBe("对比图.png");
    expect(element.getAttribute("src")).toBe("/api/harness/artifacts/%E5%9B%BE%2F1?preview=1&thread_id=task%2F1");
    act(() => host.querySelector("button")!.click());
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener.mock.calls[0][0].detail).toEqual({ ...image, name: image.name, expanded: true });
  } finally { window.removeEventListener("harness:preview-artifact", listener); }
});
it("filters extracted pages and ordinary files and deduplicates image events and existing Markdown images", () => {
  const other = { ...image, artifact_id: "photo", name: "photo.JPG", media_type: "application/octet-stream" };
  const files = [image, image, other, { ...image, artifact_id: "temp", name: "rendered-pages/page1.png" }, { ...image, artifact_id: "csv", name: "report.csv", media_type: "text/csv" }];
  expect(replyImageArtifacts(files, "").map(file => file.artifact_id)).toEqual([image.artifact_id, "photo"]);
  expect(replyImageArtifacts(files, "![现有图片](/api/harness/artifacts/photo?preview=1)").map(file => file.artifact_id)).toEqual([image.artifact_id]);
  expect(replyImageArtifacts([image], "链接：[下载](/api/harness/artifacts/%E5%9B%BE%2F1)")).toHaveLength(1);
});
it("keeps a download action for a failed image and confines scoped previews to their own workspace", () => {
  const onPreview = vi.fn();
  act(() => root.render(<ReplyArtifactImages artifacts={[image]} answer="" onPreview={onPreview} />));
  act(() => host.querySelector("button")!.click()); expect(onPreview).toHaveBeenCalledTimes(1);
  act(() => host.querySelector("img")!.dispatchEvent(new Event("error")));
  expect(host.querySelector("img")).toBeNull();
  expect(host.querySelector("a")!.download).toBe("对比图.png");
  expect(host.querySelector("a")!.textContent).toBe("下载 对比图.png");
});
it("leaves a text-only reply and all already embedded images without a second gallery", () => {
  act(() => root.render(<ReplyArtifactImages artifacts={[image]} answer="![对比](</api/harness/artifacts/%E5%9B%BE%2F1?preview=1>)" />));
  expect(host.childElementCount).toBe(0);
});
