// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, expect, it } from "vitest";
import { ReplyArtifactVideos, replyVideoArtifacts } from "../src/components/reply-artifact-videos";
import type { ArtifactDetails } from "../src/components/artifact-list";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const video: ArtifactDetails = { artifact_id: "视频/1", run_id: "run-1", thread_id: "task/1", name: "成果/海上日出.mp4", media_type: "video/mp4" };
let host: HTMLDivElement;
let root: Root;
beforeEach(() => { host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });

it("plays a published video through its authenticated, thread-scoped artifact URL and offers a download", () => {
  act(() => root.render(<ReplyArtifactVideos artifacts={[video]} />));
  const player = host.querySelector("video")!;
  expect(player.getAttribute("src")).toBe("/api/harness/artifacts/%E8%A7%86%E9%A2%91%2F1?preview=1&thread_id=task%2F1");
  expect(player.controls).toBe(true);
  expect(player.playsInline).toBe(true);
  expect(player.preload).toBe("metadata");
  expect(player.autoplay).toBe(false);
  expect(player.getAttribute("aria-label")).toBe("播放 海上日出.mp4");
  const download = host.querySelector("a")!;
  expect(download.download).toBe("海上日出.mp4");
  expect(download.getAttribute("href")).toBe("/api/harness/artifacts/%E8%A7%86%E9%A2%91%2F1?thread_id=task%2F1");
  expect(download.getAttribute("aria-label")).toBe("下载 海上日出.mp4");
});

it("excludes inputs and intermediate clips, deduplicates events, and recognizes generic MIME video files", () => {
  const files = [video, video,
    { ...video, artifact_id: "webm", name: "deliverables/demo.WEBM", media_type: "application/octet-stream" },
    { ...video, artifact_id: "input", name: "inputs/original/source.mp4" },
    { ...video, artifact_id: "temp", name: "scratch/segment.mp4" },
    { ...video, artifact_id: "image", name: "poster.png", media_type: "image/png" },
    { ...video, artifact_id: "source:video" },
    { ...video, artifact_id: "" },
  ];
  expect(replyVideoArtifacts(files).map(file => file.artifact_id)).toEqual([video.artifact_id, "webm"]);
});

it("retains download access when a codec or media request cannot be played", () => {
  act(() => root.render(<ReplyArtifactVideos artifacts={[video]} />));
  act(() => host.querySelector("video")!.dispatchEvent(new Event("error")));
  expect(host.querySelector('[role="status"]')!.textContent).toContain("可下载视频后查看");
  expect(host.querySelector("a")!.download).toBe("海上日出.mp4");
  act(() => host.querySelector("video")!.dispatchEvent(new Event("loadeddata")));
  expect(host.querySelector('[role="status"]')).toBeNull();
});

it("does not create a gallery for a reply without final video artifacts", () => {
  act(() => root.render(<ReplyArtifactVideos artifacts={[]} />));
  expect(host.childElementCount).toBe(0);
});
