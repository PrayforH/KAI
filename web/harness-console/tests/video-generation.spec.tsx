// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, test, vi } from "vitest";
import { VideoGenerationProvider, VideoGenerationControls, VideoGenerationMessagePart, DurableVideoGenerationPart, useVideoGeneration } from "../src/components/video-generation";
import { VideoGenerationResult, type VideoResult } from "../src/components/video-generation-result";

const aui = vi.hoisted(() => ({ append: vi.fn(), reset: vi.fn(), getState: vi.fn(() => ({ messages: [] })), setText: vi.fn(), addAttachment: vi.fn() }));
vi.mock("@assistant-ui/react", () => ({ useAui: () => ({ thread: () => aui, composer: () => aui }) }));
const roots: Root[] = [];
afterEach(async () => { for (const root of roots.splice(0)) await act(async () => root.unmount()); vi.useRealTimers(); vi.clearAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = ""; });
let controller: ReturnType<typeof useVideoGeneration>;
function Probe() { controller = useVideoGeneration(); return <>{Object.keys(controller.entries).map(id => <VideoGenerationMessagePart key={id} type="data" name="harness.video-generation" status={{ type: "complete" }} data={{ generationId: id }} />)}</>; }
async function mount(controls = false) {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host); roots.push(root);
  await act(async () => root.render(<VideoGenerationProvider>{controls ? <VideoGenerationControls label="MiniMax 视频" routeId="minimax-video" apiFormat="minimax_videos" referenceCount={0} disabled={false} /> : null}<Probe /></VideoGenerationProvider>));
  return host;
}
const request = { routeId: "minimax-video", routeLabel: "MiniMax 视频", apiFormat: "minimax_videos", prompt: "霓虹街道", inputArtifactIds: [], attachments: [] };
const ready = { jobId: "video_job_123", status: "completed", progress: 100, cancellationSupported: false, videoUrl: "https://cdn.example.test/clip.mp4?signature=abc" };
const reply = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

test("opening official video controls only queries quota and offers supported durations", async () => {
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => reply({ dailyTotal: 3, dailyRemaining: 2 })); vi.stubGlobal("fetch", fetcher);
  const host = await mount(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toContain("/video-quota");
  expect(host.textContent).toContain("今日剩余 2/3 条");
  expect([...host.querySelectorAll("option")].map(x => x.value)).toEqual(["6", "10"]);
  expect(host.textContent).not.toContain("Ref2VA");
});

test("successful native generation plays the signed URL without fetching the video blob", async () => {
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => reply(ready)); vi.stubGlobal("fetch", fetcher);
  const host = await mount();
  await act(async () => controller.start(request));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string).seconds).toBe(6);
  expect(host.querySelector("video")?.getAttribute("src")).toBe(ready.videoUrl);
  expect(host.querySelector("video")?.hasAttribute("autoplay")).toBe(false);
  expect(host.querySelector('[aria-label="打开视频"]')?.getAttribute("href")).toBe(ready.videoUrl);
});

test("status failure retries the same job without another generation POST", async () => {
  vi.useFakeTimers();
  let queries = 0;
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") return reply({ ...ready, status: "queued", videoUrl: null });
    queries++;
    return queries === 1 ? new Response(JSON.stringify({ error: { message: "状态暂不可用" } }), { status: 503 }) : reply(ready);
  }); vi.stubGlobal("fetch", fetcher);
  const host = await mount();
  await act(async () => controller.start(request));
  await act(async () => vi.advanceTimersByTimeAsync(2_000));
  expect(host.querySelector('[aria-label="继续查看"]')).not.toBeNull();
  await act(async () => (host.querySelector('[aria-label="继续查看"]') as HTMLButtonElement).click());
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(host.querySelector("video")?.getAttribute("src")).toBe(ready.videoUrl);
});

test("stopping official waiting stays honest and resumes the original task", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => reply(init?.method === "POST" ? { ...ready, status: "queued", videoUrl: null } : init?.method === "DELETE" ? { ...ready, status: "cancelled", videoUrl: null } : ready));
  vi.stubGlobal("fetch", fetcher);
  const host = await mount();
  await act(async () => controller.start(request));
  await act(async () => (host.querySelector('[aria-label="停止等待"]') as HTMLButtonElement).click());
  expect(host.textContent).toContain("官方任务仍在生成");
  expect(host.textContent).not.toContain("任务已取消");
  await act(async () => (host.querySelector('[aria-label="继续查看"]') as HTMLButtonElement).click());
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(host.querySelector("video")).not.toBeNull();
});

test("result controls have accessible labels and never autoplay", () => {
  const entry: VideoResult = { ...request, id: "video-1", status: "succeeded", url: ready.videoUrl, settings: { seconds: 6 } };
  const html = renderToStaticMarkup(<VideoGenerationResult entry={entry} onRetry={() => {}} onCancel={() => {}} onReuse={() => {}} />);
  expect(html).toContain('aria-label="下载 MP4"');
  expect(html).toContain('aria-label="沿用参数"');
  expect(html).toContain('aria-label="重新生成"');
  expect(html).toContain('preload="metadata"');
  expect(html).not.toContain("autoplay");
});

test("regeneration keeps the first result and ignores a rapid duplicate click", async () => {
  let submissions = 0;
  const fetcher = vi.fn(async () => reply({ ...ready, videoUrl: `https://cdn.example.test/clip-${++submissions}.mp4` }));
  vi.stubGlobal("fetch", fetcher);
  const host = await mount();
  let firstId = "";
  await act(async () => { firstId = controller.start(request); });
  await act(async () => { controller.retry(firstId); controller.retry(firstId); });
  expect(submissions).toBe(2);
  expect([...host.querySelectorAll("video")].map(x => x.getAttribute("src"))).toEqual([
    "https://cdn.example.test/clip-1.mp4", "https://cdn.example.test/clip-2.mp4",
  ]);
});

test("a failed regeneration preserves the earlier video without reusing its URL on the error", async () => {
  let submissions = 0;
  vi.stubGlobal("fetch", vi.fn(async () => ++submissions === 1 ? reply(ready) : new Response(JSON.stringify({ error: { message: "今日额度已用完" } }), { status: 409 })));
  const host = await mount();
  let firstId = "";
  await act(async () => { firstId = controller.start(request); });
  await act(async () => controller.retry(firstId));
  expect(host.querySelectorAll("video")).toHaveLength(1);
  expect(host.querySelectorAll('[aria-label="打开视频"]')).toHaveLength(1);
  expect(host.textContent).toContain("今日额度已用完");
  expect(controller.entries[firstId].url).toBe(ready.videoUrl);
  expect(Object.values(controller.entries).find(entry => entry.status === "failed")?.url).toBeUndefined();
});

test("rapid duplicate submission creates only one user message and one video task", async () => {
  const fetcher = vi.fn(async () => reply(ready)); vi.stubGlobal("fetch", fetcher);
  await mount();
  await act(async () => { const first = controller.start(request); expect(controller.start(request)).toBe(first); });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(aui.append).toHaveBeenCalledTimes(2);
});

test("a terminal provider failure offers a new generation instead of polling a failed task forever", async () => {
  let submissions = 0;
  const fetcher = vi.fn(async () => reply(++submissions === 1 ? { ...ready, status: "failed", videoUrl: null, error: "模型未能完成生成" } : ready));
  vi.stubGlobal("fetch", fetcher);
  const host = await mount();
  await act(async () => controller.start(request));
  expect(host.querySelector('[aria-label="继续查看"]')).toBeNull();
  await act(async () => (host.querySelector('[aria-label="重试"]') as HTMLButtonElement).click());
  expect(submissions).toBe(2);
  expect(host.querySelector("video")).not.toBeNull();
});


test("durable media history refreshes the original job URL without submitting another generation", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => reply(ready)); vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host); roots.push(root);
  const data = { routeId: request.routeId, routeLabel: request.routeLabel, apiFormat: request.apiFormat,
    request: { prompt: request.prompt, inputArtifactIds: [], seconds: 6 }, job: ready };
  await act(async () => root.render(<VideoGenerationProvider task={{ threadId: "durable-video", agentName: "lead-agent", agentVersion: "1" }}><DurableVideoGenerationPart data={data} /></VideoGenerationProvider>));
  expect(fetcher.mock.calls[0][0]).toContain("/videos/" + ready.jobId);
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  expect(host.querySelector("video")?.getAttribute("src")).toBe(ready.videoUrl);
});
