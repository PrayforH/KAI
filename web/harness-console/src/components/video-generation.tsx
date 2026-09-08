"use client";

import {
  useAui,
  type CompleteAttachment,
  type DataMessagePartProps,
} from "@assistant-ui/react";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";

export const VIDEO_GENERATION_PART_NAME = "harness.video-generation";

export type VideoAspectRatio = "21:9" | "16:9" | "4:3" | "1:1" | "3:4" | "9:16";
export type VideoDuration = number;

export interface VideoGenerationSettings {
  mode: "auto" | "ref2va";
  seconds: VideoDuration;
  aspectRatio: VideoAspectRatio;
  seed: string;
  negativePrompt: string;
}

interface VideoGenerationRequest {
  routeId: string;
  routeLabel: string;
  prompt: string;
  inputArtifactIds: string[];
  attachments: CompleteAttachment[];
  settings: VideoGenerationSettings;
}

interface VideoGenerationEntry extends VideoGenerationRequest {
  id: string;
  status: "generating" | "succeeded" | "failed" | "cancelled";
  phase?: "queued" | "in_progress";
  progress?: number;
  url?: string;
  error?: string;
  elapsedSeconds?: number;
}

interface ServerVideoJob {
  jobId: string;
  status: "queued" | "in_progress" | "completed" | "failed" | "cancelled";
  progress: number;
  error?: string | null;
  inferenceTimeSeconds?: number | null;
}

interface VideoGenerationController {
  settings: VideoGenerationSettings;
  setSettings: (next: VideoGenerationSettings) => void;
  entries: Readonly<Record<string, VideoGenerationEntry>>;
  generating: boolean;
  start: (request: Omit<VideoGenerationRequest, "settings">) => string;
  retry: (generationId: string) => void;
  cancel: (generationId: string) => void;
  reuse: (generationId: string) => Promise<void>;
}

const DEFAULT_SETTINGS: VideoGenerationSettings = {
  mode: "auto",
  seconds: 5,
  aspectRatio: "16:9",
  seed: "",
  negativePrompt: "",
};

const VideoGenerationContext = createContext<VideoGenerationController | null>(null);

function generationId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `video-${crypto.randomUUID()}`;
  }
  return `video-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function responseError(raw: string, status: number) {
  let message = raw || `视频生成失败（HTTP ${status}）`;
  try {
    const parsed = JSON.parse(raw) as { error?: { message?: string }; detail?: string };
    message = parsed.error?.message ?? parsed.detail ?? message;
  } catch {
    // Preserve the provider response when it is not JSON.
  }
  return message;
}

function waitForNextPoll(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, 2_000);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

export function VideoGenerationProvider({ children }: { children: ReactNode }) {
  const aui = useAui();
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [entries, setEntries] = useState<Record<string, VideoGenerationEntry>>({});
  const aborts = useRef(new Map<string, AbortController>());
  const jobs = useRef(new Map<string, { routeId: string; jobId: string }>());
  const cancellationRequests = useRef(new Set<string>());
  const urls = useRef(new Map<string, string>());

  useEffect(() => () => {
    for (const controller of aborts.current.values()) controller.abort();
    for (const url of urls.current.values()) URL.revokeObjectURL(url);
  }, []);

  const cancelRemoteJob = useCallback(async (routeId: string, jobId: string) => {
    const response = requireAuthenticatedResponse(await fetch(
      `/api/studio/models/${encodeURIComponent(routeId)}/videos/${encodeURIComponent(jobId)}`,
      { method: "DELETE" },
    ));
    if (!response.ok) {
      throw new Error(responseError(await response.text(), response.status));
    }
  }, []);

  const execute = useCallback(async (id: string, request: VideoGenerationRequest) => {
    const previous = urls.current.get(id);
    if (previous) {
      URL.revokeObjectURL(previous);
      urls.current.delete(id);
    }
    const controller = new AbortController();
    aborts.current.set(id, controller);
    cancellationRequests.current.delete(id);
    jobs.current.delete(id);
    const startedAt = performance.now();
    setEntries((current) => ({
      ...current,
      [id]: { ...request, id, status: "generating" },
    }));
    try {
      const seed = request.settings.seed.trim();
      const negativePrompt = request.settings.negativePrompt.trim();
      const response = requireAuthenticatedResponse(
        await fetch(
          `/api/studio/models/${encodeURIComponent(request.routeId)}/videos`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              prompt: request.prompt,
              mode: request.settings.mode,
              aspectRatio: request.settings.aspectRatio,
              seconds: request.settings.seconds,
              inputArtifactIds: request.inputArtifactIds,
              ...(seed ? { seed: Number(seed) } : {}),
              ...(negativePrompt ? { negativePrompt } : {}),
            }),
          },
        ),
      );
      if (!response.ok) {
        throw new Error(responseError(await response.text(), response.status));
      }
      let job = await response.json() as ServerVideoJob;
      if (!job.jobId) throw new Error("服务没有返回有效的视频任务。");
      jobs.current.set(id, { routeId: request.routeId, jobId: job.jobId });
      if (cancellationRequests.current.has(id)) {
        await cancelRemoteJob(request.routeId, job.jobId);
        jobs.current.delete(id);
        setEntries((current) => ({
          ...current,
          [id]: { ...request, id, status: "cancelled", error: "视频生成任务已取消。" },
        }));
        return;
      }
      while (job.status === "queued" || job.status === "in_progress") {
        const phase = job.status;
        const progress = job.progress;
        setEntries((current) => ({
          ...current,
          [id]: {
            ...request,
            id,
            status: "generating",
            phase,
            progress,
          },
        }));
        await waitForNextPoll(controller.signal);
        const statusResponse = requireAuthenticatedResponse(await fetch(
          `/api/studio/models/${encodeURIComponent(request.routeId)}/videos/${encodeURIComponent(job.jobId)}`,
          { signal: controller.signal },
        ));
        if (!statusResponse.ok) {
          throw new Error(responseError(await statusResponse.text(), statusResponse.status));
        }
        job = await statusResponse.json() as ServerVideoJob;
      }
      if (job.status === "cancelled") return;
      if (job.status === "failed") {
        throw new Error(job.error || "视频模型未能完成本次生成。");
      }
      const contentResponse = requireAuthenticatedResponse(await fetch(
        `/api/studio/models/${encodeURIComponent(request.routeId)}/videos/${encodeURIComponent(job.jobId)}/content`,
        { signal: controller.signal },
      ));
      if (!contentResponse.ok) {
        throw new Error(responseError(await contentResponse.text(), contentResponse.status));
      }
      const blob = await contentResponse.blob();
      if (!blob.type.startsWith("video/")) throw new Error("服务返回的不是有效视频。");
      const url = URL.createObjectURL(blob);
      urls.current.set(id, url);
      const providerElapsed = job.inferenceTimeSeconds;
      setEntries((current) => ({
        ...current,
        [id]: {
          ...request,
          id,
          status: "succeeded",
          url,
          progress: 100,
          elapsedSeconds: typeof providerElapsed === "number" && Number.isFinite(providerElapsed)
            ? providerElapsed
            : (performance.now() - startedAt) / 1_000,
        },
      }));
    } catch (error) {
      setEntries((current) => ({
        ...current,
        [id]: {
          ...request,
          id,
          status: controller.signal.aborted ? "cancelled" : "failed",
          error: controller.signal.aborted || cancellationRequests.current.has(id)
            ? "视频生成任务已取消。"
            : error instanceof Error
              ? error.message
              : "视频生成失败，请稍后重试。",
        },
      }));
    } finally {
      if (aborts.current.get(id) === controller) aborts.current.delete(id);
      cancellationRequests.current.delete(id);
    }
  }, [cancelRemoteJob]);

  const start = useCallback((request: Omit<VideoGenerationRequest, "settings">) => {
    const id = generationId();
    const snapshot: VideoGenerationRequest = { ...request, settings: { ...settings } };
    const thread = aui.thread();
    const parentId = thread.getState().messages.at(-1)?.id ?? null;
    thread.append({
      parentId,
      sourceId: null,
      role: "user",
      content: [{ type: "text", text: request.prompt }],
      attachments: request.attachments,
      startRun: false,
    });
    const userMessageId = thread.getState().messages.at(-1)?.id ?? parentId;
    thread.append({
      parentId: userMessageId,
      sourceId: null,
      role: "assistant",
      content: [
        {
          type: "data",
          name: VIDEO_GENERATION_PART_NAME,
          data: { generationId: id },
        },
      ],
      startRun: false,
    });
    void aui.composer().reset();
    void execute(id, snapshot);
    return id;
  }, [aui, execute, settings]);

  const retry = useCallback((id: string) => {
    const entry = entries[id];
    if (!entry || entry.status === "generating") return;
    void execute(id, entry);
  }, [entries, execute]);

  const cancel = useCallback((id: string) => {
    cancellationRequests.current.add(id);
    aborts.current.get(id)?.abort();
    setEntries((current) => {
      const entry = current[id];
      if (!entry || entry.status !== "generating") return current;
      return {
        ...current,
        [id]: { ...entry, status: "cancelled", error: "正在取消视频生成任务…" },
      };
    });
    const job = jobs.current.get(id);
    if (!job) return;
    void cancelRemoteJob(job.routeId, job.jobId).then(() => {
      jobs.current.delete(id);
      setEntries((current) => {
        const entry = current[id];
        if (!entry) return current;
        return {
          ...current,
          [id]: { ...entry, status: "cancelled", error: "视频生成任务已取消。" },
        };
      });
    }).catch((error: unknown) => {
      setEntries((current) => {
        const entry = current[id];
        if (!entry) return current;
        return {
          ...current,
          [id]: {
            ...entry,
            status: "failed",
            error: error instanceof Error ? `停止任务失败：${error.message}` : "停止任务失败。",
          },
        };
      });
    });
  }, [cancelRemoteJob]);

  const reuse = useCallback(async (id: string) => {
    const entry = entries[id];
    if (!entry) return;
    const composer = aui.composer();
    await composer.reset();
    setSettings({ ...entry.settings });
    composer.setText(entry.prompt);
    for (const attachment of entry.attachments) {
      await composer.addAttachment({
        id: attachment.id,
        type: attachment.type,
        name: attachment.name,
        contentType: attachment.contentType,
        content: attachment.content,
      });
    }
  }, [aui, entries]);

  const value = useMemo<VideoGenerationController>(() => ({
    settings,
    setSettings,
    entries,
    generating: Object.values(entries).some((entry) => entry.status === "generating"),
    start,
    retry,
    cancel,
    reuse,
  }), [cancel, entries, retry, reuse, settings, start]);

  return (
    <VideoGenerationContext.Provider value={value}>
      {children}
    </VideoGenerationContext.Provider>
  );
}

export function useVideoGeneration() {
  const controller = useContext(VideoGenerationContext);
  if (!controller) throw new Error("Video generation must be used inside its provider");
  return controller;
}

export function VideoGenerationControls({
  label,
  referenceCount,
  disabled,
}: {
  label: string;
  referenceCount: number;
  disabled: boolean;
}) {
  const { settings, setSettings } = useVideoGeneration();
  const followsReference = referenceCount > 0;
  const resolvedMode = settings.mode === "ref2va"
    ? "Ref2VA"
    : referenceCount > 0
      ? "FL2VA"
      : "T2VA";
  return (
    <section className="composer-video-settings" aria-label="视频生成设置">
      <div className="video-mode-control">
        <span>
          <strong>生成模式</strong>
          <small>
            {settings.mode === "ref2va"
              ? "参考图片控制人物、场景或风格"
              : "根据当前素材自动选择文本或图片驱动"}
          </small>
        </span>
        <fieldset disabled={disabled}>
          <legend className="video-mode-legend">选择视频生成模式</legend>
          <button
            type="button"
            aria-pressed={settings.mode === "auto"}
            onClick={() => setSettings({ ...settings, mode: "auto" })}
          >
            自动
            <small>{referenceCount ? "FL2VA" : "T2VA"}</small>
          </button>
          <button
            type="button"
            aria-pressed={settings.mode === "ref2va"}
            onClick={() => setSettings({ ...settings, mode: "ref2va" })}
          >
            Ref2VA
            <small>1–9 张参考图</small>
          </button>
        </fieldset>
      </div>
      <div className="composer-video-settings-primary">
        <span className="composer-video-model">
          <strong>{label}</strong>
          <small>
            {resolvedMode} · {referenceCount ? `${referenceCount} 张参考图` : "文本生成视频"}
          </small>
        </span>
        <fieldset className="video-duration-control" disabled={disabled}>
          <legend>时长</legend>
          {[5, 10, 15].map((seconds) => (
            <button
              key={seconds}
              type="button"
              aria-pressed={settings.seconds === seconds}
              onClick={() => setSettings({ ...settings, seconds })}
            >
              {seconds}s
            </button>
          ))}
        </fieldset>
        <label className="video-ratio-control">
          <span>画面比例</span>
          {followsReference ? (
            <strong title="有参考图时，H3 根据第一张图片确定输出比例">跟随首图</strong>
          ) : (
            <select
              value={settings.aspectRatio}
              disabled={disabled}
              onChange={(event) => setSettings({
                ...settings,
                aspectRatio: event.target.value as VideoAspectRatio,
              })}
            >
              {(["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"] as const).map(
                (ratio) => <option key={ratio} value={ratio}>{ratio}</option>,
              )}
            </select>
          )}
        </label>
      </div>
      {settings.mode === "ref2va" ? (
        <p
          className={referenceCount ? "composer-video-mode-note" : "composer-video-mode-warning"}
          role="status"
        >
          {referenceCount
            ? `将使用 ${referenceCount} 张图片作为 Ref2VA 参考素材。`
            : "Ref2VA 至少需要添加 1 张参考图片。"}
        </p>
      ) : null}
      {settings.seconds > 5 ? (
        <p className="composer-video-duration-note" role="status">
          10–15 秒视频生成耗时较长，提交后可查看实时状态或取消任务。
        </p>
      ) : null}
      <details className="composer-video-advanced">
        <summary>高级设置</summary>
        <div>
          <label>
            <span>自定义时长（4–15 秒）</span>
            <select
              value={settings.seconds}
              disabled={disabled}
              onChange={(event) => setSettings({
                ...settings,
                seconds: Number(event.target.value),
              })}
            >
              {Array.from({ length: 12 }, (_, index) => index + 4).map((seconds) => (
                <option key={seconds} value={seconds}>{seconds} 秒</option>
              ))}
            </select>
          </label>
          <label>
            <span>随机种子</span>
            <input
              type="number"
              min="0"
              step="1"
              inputMode="numeric"
              value={settings.seed}
              disabled={disabled}
              placeholder="自动"
              onChange={(event) => setSettings({ ...settings, seed: event.target.value })}
            />
          </label>
          <label className="video-negative-prompt">
            <span>负向提示词</span>
            <input
              type="text"
              maxLength={4_000}
              value={settings.negativePrompt}
              disabled={disabled}
              placeholder="例如：画面抖动、变形、文字水印"
              onChange={(event) => setSettings({
                ...settings,
                negativePrompt: event.target.value,
              })}
            />
          </label>
        </div>
      </details>
    </section>
  );
}

export function VideoGenerationMessagePart({
  data,
}: DataMessagePartProps<{ generationId?: string }>) {
  const { entries, retry, cancel, reuse } = useVideoGeneration();
  const entry = data.generationId ? entries[data.generationId] : undefined;
  if (!entry) return null;
  const ratio = entry.inputArtifactIds.length ? "跟随首图" : entry.settings.aspectRatio;
  const mode = entry.settings.mode === "ref2va"
    ? "Ref2VA"
    : entry.inputArtifactIds.length
      ? "FL2VA"
      : "T2VA";
  return (
    <article className="video-answer-card" data-status={entry.status}>
      <header>
        <span>
          <strong>{entry.routeLabel}</strong>
          <small>
            {mode} · {entry.settings.seconds} 秒 · {ratio} · MP4
            {entry.inputArtifactIds.length
              ? ` · ${entry.inputArtifactIds.length} 张参考图`
              : ""}
          </small>
        </span>
        <em>
          {entry.status === "generating"
            ? "生成中"
            : entry.status === "succeeded"
              ? "已完成"
              : entry.status === "cancelled"
                ? "已取消"
                : "生成失败"}
        </em>
      </header>
      {entry.status === "generating" ? (
        <div className="video-answer-progress" role="status" aria-live="polite">
          <span className="video-generation-motion" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <strong>{entry.phase === "queued" ? "任务排队中" : "正在生成视频"}</strong>
          <small>
            {typeof entry.progress === "number" && entry.progress > 0
              ? `模型进度 ${entry.progress}% · 完成后可直接播放`
              : "可能需要数分钟，完成后可直接播放"}
          </small>
          <span
            className="video-generation-progress-track"
            aria-label={typeof entry.progress === "number" ? `生成进度 ${entry.progress}%` : undefined}
          >
            <i
              data-indeterminate={!entry.progress || undefined}
              style={entry.progress ? { width: `${entry.progress}%` } : undefined}
            />
          </span>
        </div>
      ) : null}
      {entry.status === "succeeded" && entry.url ? (
        <div className="video-answer-player">
          <video
            src={entry.url}
            controls
            playsInline
            preload="metadata"
            aria-label={entry.prompt}
          />
        </div>
      ) : null}
      {entry.error ? <p role="alert">{entry.error}</p> : null}
      <footer>
        <span title={entry.prompt}>
          {entry.elapsedSeconds ? `生成用时 ${entry.elapsedSeconds.toFixed(1)} 秒` : entry.prompt}
        </span>
        <nav aria-label="视频操作">
          {entry.status === "generating" ? (
            <button type="button" onClick={() => cancel(entry.id)}>取消任务</button>
          ) : (
            <button type="button" onClick={() => retry(entry.id)}>重新生成</button>
          )}
          <button type="button" onClick={() => void reuse(entry.id)}>沿用参数</button>
          {entry.status === "succeeded" && entry.url ? (
            <a href={entry.url} download={`${entry.routeId}.mp4`}>下载 MP4</a>
          ) : null}
        </nav>
      </footer>
    </article>
  );
}
