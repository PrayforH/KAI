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

test("video settings use popovers without querying or imposing plan quotas", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const host = await mount(true);
  expect(fetcher).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain("今日剩余");
  expect(host.querySelector("select")).toBeNull();
  await act(async () => (host.querySelector('[aria-label="视频时长"]') as HTMLButtonElement).click());
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(dialog.textContent).toContain("6秒");
  expect(dialog.textContent).toContain("10秒");
  await act(async () => ([...dialog.querySelectorAll("button")].find(x => x.textContent === "10秒")!).click());
  expect(controller.settings.seconds).toBe(10);
  await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(host.querySelector('[aria-label="视频时长"]'));
});

test("selecting HD synchronizes duration and sends the chosen resolution", async () => {
  const fetcher = vi.fn(async () => reply(ready)); vi.stubGlobal("fetch", fetcher);
  const host = await mount(true);
  await act(async () => controller.setSettings({ ...controller.settings, seconds: 10 }));
  await act(async () => (host.querySelector('[aria-label="画幅与分辨率"]') as HTMLButtonElement).click());
  await act(async () => ([...document.querySelectorAll('[role="dialog"] button')].find(x => x.textContent === "1080P高清") as HTMLButtonElement).click());
  expect(controller.settings.resolution).toBe("1080P");
  expect(controller.settings.seconds).toBe(6);
  await act(async () => (host.querySelector('[aria-label="视频时长"]') as HTMLButtonElement).click());
  expect(([...document.querySelectorAll('[role="dialog"] button')].find(x => x.textContent === "10秒") as HTMLButtonElement).disabled).toBe(true);
  await act(async () => controller.start(request));
  expect(fetcher).toHaveBeenCalledTimes(1);
  const body = JSON.parse((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
  expect(body.resolution).toBe("1080P");
  expect(body.seconds).toBe(6);
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
  vi.stubGlobal("fetch", vi.fn(async () => ++submissions === 1 ? reply(ready) : new Response(JSON.stringify({ error: { message: "账户积分不足" } }), { status: 409 })));
  const host = await mount();
  let firstId = "";
  await act(async () => { firstId = controller.start(request); });
  await act(async () => controller.retry(firstId));
  expect(host.querySelectorAll("video")).toHaveLength(1);
  expect(host.querySelectorAll('[aria-label="打开视频"]')).toHaveLength(1);
  expect(host.textContent).toContain("账户积分不足");
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

test("H3 popovers preserve aspect ratio, reference mode and all supported duration values", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host); roots.push(root);
  const render = (referenceCount: number, disabled = false) => root.render(<VideoGenerationProvider>
    <VideoGenerationControls label="H3 视频" routeId="h3" referenceCount={referenceCount} disabled={disabled} /><Probe />
  </VideoGenerationProvider>);
  await act(async () => render(0));
  await act(async () => (host.querySelector('[aria-label="画幅与分辨率"]') as HTMLButtonElement).click());
  expect(document.querySelectorAll('[role="dialog"] [aria-pressed]')).toHaveLength(6);
  await act(async () => (document.querySelector('[aria-label="画幅 9:16"]') as HTMLButtonElement).click());
  expect(controller.settings.aspectRatio).toBe("9:16");
  await act(async () => (host.querySelector('[aria-label="视频时长"]') as HTMLButtonElement).click());
  const slider = document.querySelector('input[type="range"]') as HTMLInputElement;
  expect([slider.min, slider.max, slider.step]).toEqual(["4", "15", "1"]);
  await act(async () => document.body.dispatchEvent(new Event("pointerdown", { bubbles: true })));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  await act(async () => render(1));
  expect(host.textContent).toContain("跟随首图");
  await act(async () => (host.querySelector('[aria-label="画幅与分辨率"]') as HTMLButtonElement).click());
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("输出画幅由图片比例决定");
  await act(async () => render(1, true));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect((host.querySelector('[aria-label="视频时长"]') as HTMLButtonElement).disabled).toBe(true);
});

test("official models switch duration and resolution without expanding the composer", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const fetcher = vi.fn(async (_url: unknown, _init?: RequestInit) => reply(ready)); vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host); roots.push(root);
  const render = (model: string) => root.render(<VideoGenerationProvider><VideoGenerationControls label={model} routeId={model} model={model} apiFormat="minimax_videos_v2" referenceCount={0} disabled={false} /><Probe /></VideoGenerationProvider>);
  await act(async () => render("MiniMax-H3"));
  expect(host.querySelector(".composer-video-settings")).toBeNull();
  await act(async () => (host.querySelector('[aria-label="视频时长"]') as HTMLButtonElement).click());
  expect([...document.querySelectorAll('[role="dialog"] button[aria-pressed]')].map(b => b.textContent)).toEqual(["5秒", "10秒", "15秒"]);
  await act(async () => (document.querySelectorAll('[role="dialog"] button[aria-pressed]')[2] as HTMLButtonElement).click());
  await act(async () => (host.querySelector('[aria-label="画幅与分辨率"]') as HTMLButtonElement).click());
  const resolution = [...document.querySelectorAll('[role="dialog"] button')].find(b => b.textContent?.startsWith("2K")) as HTMLButtonElement;
  await act(async () => resolution.click());
  expect(controller.settings.resolution).toBe("2K");
  await act(async () => controller.start({ ...request, model: "MiniMax-H3", apiFormat: "minimax_videos_v2" }));
  expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string)).toMatchObject({ seconds: 15, resolution: "2K" });
  await act(async () => render("MiniMax-H3-Max"));
  expect(controller.settings).toMatchObject({ seconds: 5, resolution: "768P" });
  await act(async () => (host.querySelector('[aria-label="画幅与分辨率"]') as HTMLButtonElement).click());
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("480P");
  expect(document.querySelector('[role="dialog"]')?.textContent).not.toContain("2K");
});
