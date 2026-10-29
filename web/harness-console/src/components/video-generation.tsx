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
import { invalidateThreadHistory, notifyTaskListChanged } from "../lib/task-history";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { VideoGenerationResult } from "./video-generation-result";
import { VideoSettingsToolbar } from "./video-settings-toolbar";
import controlStyles from "./video-generation-controls.module.css";

export const VIDEO_GENERATION_PART_NAME = "harness.video-generation";

export type VideoAspectRatio = "21:9" | "16:9" | "4:3" | "1:1" | "3:4" | "9:16";
export type VideoDuration = number;

export interface VideoGenerationSettings {
  mode: "auto" | "ref2va";
  seconds: VideoDuration;
  resolution?: "480P" | "768P" | "1080P" | "2K";
  aspectRatio: VideoAspectRatio;
  seed: string;
  negativePrompt: string;
}

interface VideoTaskContext {
  threadId: string; agentName: string; agentVersion: string;
  agentOwnerUserId?: string; spaceId?: string;
}

interface VideoGenerationRequest {
  routeId: string;
  routeLabel: string;
  apiFormat?: string;
  model?: string;
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
  downloadUrl?: string;
  error?: string;
  elapsedSeconds?: number;
  jobId?: string;
  cancellationSupported?: boolean;
}

interface ServerVideoJob {
  jobId: string;
  status: "queued" | "in_progress" | "completed" | "failed" | "cancelled";
  progress: number;
  error?: string | null;
  inferenceTimeSeconds?: number | null;
  videoUrl?: string | null;
  cancellationSupported?: boolean;
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
  restore: (data: Record<string, unknown>) => string | null;
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
    const aborted = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => { signal.removeEventListener("abort", aborted); resolve(); }, 2_000);
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted();
  });
}

export function VideoGenerationProvider({ children, task }: { children: ReactNode; task?: VideoTaskContext }) {
  const aui = useAui();
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [entries, setEntries] = useState<Record<string, VideoGenerationEntry>>({});
  const aborts = useRef(new Map<string, AbortController>());
  const jobs = useRef(new Map<string, { routeId: string; jobId: string; cancellationSupported: boolean }>());
  const cancellationRequests = useRef(new Set<string>());
  const restoredJobs = useRef(new Set<string>());
  const taskRef = useRef(task); taskRef.current = task;
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

  const execute = useCallback(async (id: string, request: VideoGenerationRequest, existingJobId?: string) => {
    request = {
      routeId: request.routeId, routeLabel: request.routeLabel, apiFormat: request.apiFormat, model: request.model,
      prompt: request.prompt, inputArtifactIds: request.inputArtifactIds,
      attachments: request.attachments, settings: request.settings,
    };
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
      [id]: { ...request, id, status: "generating", jobId: existingJobId, cancellationSupported: !request.apiFormat?.startsWith("minimax_videos") },
    }));
    try {
      const seed = request.settings.seed.trim();
      const negativePrompt = request.settings.negativePrompt.trim();
      const response = requireAuthenticatedResponse(
        await fetch(
          `/api/studio/models/${encodeURIComponent(request.routeId)}/videos${existingJobId ? `/${encodeURIComponent(existingJobId)}` : ""}`,
          existingJobId ? { signal: controller.signal } : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              prompt: request.prompt,
              mode: request.settings.mode,
              aspectRatio: request.settings.aspectRatio,
              seconds: request.settings.seconds,
              ...(request.apiFormat?.startsWith("minimax_videos") ? { resolution: request.settings.resolution ?? "768P" } : {}),
              inputArtifactIds: request.inputArtifactIds,
              ...(taskRef.current ? { task: taskRef.current } : {}),
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
      if (taskRef.current) {
        invalidateThreadHistory(taskRef.current.threadId);
        notifyTaskListChanged();
      }
      const cancellationSupported = job.cancellationSupported !== false;
      jobs.current.set(id, { routeId: request.routeId, jobId: job.jobId, cancellationSupported });
      setEntries((current) => ({ ...current, [id]: { ...current[id], jobId: job.jobId, cancellationSupported } }));
      if (cancellationRequests.current.has(id)) {
        await cancelRemoteJob(request.routeId, job.jobId);
        if (cancellationSupported) jobs.current.delete(id);
        setEntries((current) => ({
          ...current,
          [id]: { ...request, id, jobId: job.jobId, cancellationSupported, status: "cancelled", error: cancellationSupported ? "视频生成任务已取消。" : "已停止等待。官方任务仍在生成，可继续查看。" },
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
            jobId: job.jobId,
            cancellationSupported,
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
      if (controller.signal.aborted) throw new DOMException("Aborted", "AbortError");
      if (job.status === "cancelled") {
        setEntries((current) => ({ ...current, [id]: { ...current[id], status: "cancelled", error: "视频生成任务已取消。" } }));
        return;
      }
      if (job.status === "failed") {
        jobs.current.delete(id);
        setEntries((current) => ({ ...current, [id]: {
          ...request, id, status: "failed",
          error: job.error || "视频模型未能完成本次生成。",
        } }));
        return;
      }
      if (job.videoUrl) {
        const url = new URL(job.videoUrl);
        if (url.protocol !== "https:" || url.username || url.password) throw new Error("视频链接无效。");
        setEntries((current) => ({ ...current, [id]: {
          ...request, id, jobId: job.jobId, cancellationSupported,
          status: "succeeded", url: job.videoUrl!, progress: 100,
          downloadUrl: `/api/studio/models/${encodeURIComponent(request.routeId)}/videos/${encodeURIComponent(job.jobId)}/content`,
          elapsedSeconds: existingJobId ? undefined : (performance.now() - startedAt) / 1_000,
        } }));
        return;
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
      if (controller.signal.aborted && cancellationRequests.current.has(id) && !request.apiFormat?.startsWith("minimax_videos")) return;
      setEntries((current) => ({
        ...current,
        [id]: {
          ...request,
          ...current[id],
          id,
          status: controller.signal.aborted ? "cancelled" : "failed",
          error: controller.signal.aborted || cancellationRequests.current.has(id)
            ? current[id]?.cancellationSupported === false ? "已停止等待。官方任务仍在生成，可继续查看。" : "视频生成任务已取消。"
            : error instanceof Error
              ? error.message
              : "视频生成失败，请稍后重试。",
        },
      }));
    } finally {
      if (taskRef.current && jobs.current.has(id)) {
        invalidateThreadHistory(taskRef.current.threadId);
        notifyTaskListChanged();
      }
      if (aborts.current.get(id) === controller) aborts.current.delete(id);
      cancellationRequests.current.delete(id);
    }
  }, [cancelRemoteJob]);

  const start = useCallback((request: Omit<VideoGenerationRequest, "settings">) => {
    const active = aborts.current.keys().next().value as string | undefined;
    if (active) return active;
    const id = generationId();
    const snapshot: VideoGenerationRequest = { ...request, settings: request.apiFormat === "minimax_videos" ? {
      ...DEFAULT_SETTINGS, seconds: settings.resolution === "1080P" ? 6 : settings.seconds === 10 ? 10 : 6,
      resolution: settings.resolution ?? "768P",
    } : request.apiFormat === "minimax_videos_v2" ? { ...DEFAULT_SETTINGS, seconds: [5, 10, 15].includes(settings.seconds) ? settings.seconds : 5, aspectRatio: settings.aspectRatio, resolution: request.model === "MiniMax-H3-Max" ? (settings.resolution === "480P" ? "480P" : "768P") : (settings.resolution === "2K" ? "2K" : "768P") } : { ...settings } };
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
    if (!entry || entry.status === "generating" || aborts.current.size) return;
    if (entry.status === "succeeded") {
      // Keep the completed video available even if the next generation fails.
      // A rapid second click must not submit another quota-consuming task.
      const nextId = generationId();
      const thread = aui.thread();
      thread.append({
        parentId: thread.getState().messages.at(-1)?.id ?? null,
        sourceId: null,
        role: "assistant",
        content: [{ type: "data", name: VIDEO_GENERATION_PART_NAME, data: { generationId: nextId } }],
        startRun: false,
      });
      void execute(nextId, entry);
      return;
    }
    const resume = entry.jobId && (entry.status === "failed" || (entry.status === "cancelled" && entry.cancellationSupported === false));
    void execute(id, entry, resume ? entry.jobId : undefined);
  }, [aui, entries, execute]);

  const cancel = useCallback((id: string) => {
    cancellationRequests.current.add(id);
    aborts.current.get(id)?.abort();
    setEntries((current) => {
      const entry = current[id];
      if (!entry || entry.status !== "generating") return current;
      return {
        ...current,
        [id]: { ...entry, status: entry.cancellationSupported === false ? "cancelled" : "generating", error: entry.cancellationSupported === false ? "已停止等待。官方任务仍在生成，可继续查看。" : "正在取消视频生成任务…" },
      };
    });
    const job = jobs.current.get(id);
    if (!job) return;
    void cancelRemoteJob(job.routeId, job.jobId).then(() => {
      if (job.cancellationSupported) jobs.current.delete(id);
      setEntries((current) => {
        const entry = current[id];
        if (!entry) return current;
        return {
          ...current,
        [id]: { ...entry, status: "cancelled", error: job.cancellationSupported ? "视频生成任务已取消。" : "已停止等待。官方任务仍在生成，可继续查看。" },
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

  const restore = useCallback((data: Record<string, unknown>) => {
    const request = data.request as Record<string, unknown> | undefined;
    const job = data.job as ServerVideoJob | undefined;
    if (!request || !job?.jobId || typeof request.prompt !== "string" || typeof data.routeId !== "string") return null;
    const id = job.jobId;
    if (!restoredJobs.current.has(id)) {
      restoredJobs.current.add(id);
      const ids = Array.isArray(request.inputArtifactIds) ? request.inputArtifactIds.filter((item): item is string => typeof item === "string") : [];
      const snapshot: VideoGenerationRequest = {
        routeId: data.routeId, routeLabel: typeof data.routeLabel === "string" ? data.routeLabel : "视频",
        apiFormat: typeof data.apiFormat === "string" ? data.apiFormat : undefined,
        model: typeof data.model === "string" ? data.model : undefined,
        prompt: request.prompt, inputArtifactIds: ids,
        attachments: ids.map(artifactId => ({ id: artifactId, type: "image", name: "参考图片", contentType: "image/png", status: { type: "complete" },
          content: [{ type: "file", data: artifactId, mimeType: "image/png", filename: "参考图片" }] })),
        settings: { mode: request.mode === "ref2va" ? "ref2va" : "auto", seconds: typeof request.seconds === "number" ? request.seconds : 6,
          resolution: request.resolution === "1080P" || request.resolution === "480P" || request.resolution === "2K" ? request.resolution : "768P",
          aspectRatio: (request.aspectRatio as VideoAspectRatio) ?? "16:9", seed: typeof request.seed === "number" ? String(request.seed) : "",
          negativePrompt: typeof request.negativePrompt === "string" ? request.negativePrompt : "" },
      };
      if (job.status === "failed" || job.status === "cancelled") {
        setEntries(current => ({ ...current, [id]: { ...snapshot, id, status: job.status === "failed" ? "failed" : "cancelled", error: job.error ?? undefined } }));
      } else void execute(id, snapshot, job.jobId);
    }
    return id;
  }, [execute]);

  const value = useMemo<VideoGenerationController>(() => ({
    settings,
    setSettings,
    entries,
    generating: Object.values(entries).some((entry) => entry.status === "generating"),
    start,
    retry,
    cancel,
    reuse,
    restore,
  }), [cancel, entries, retry, reuse, restore, settings, start]);

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
  routeId,
  apiFormat,
  model,
  referenceCount,
  disabled,
}: {
  label: string;
  routeId?: string;
  apiFormat?: string;
  model?: string;
  referenceCount: number;
  disabled: boolean;
}) {
  const { settings, setSettings } = useVideoGeneration();
  const official = apiFormat === "minimax_videos";
  const h3 = apiFormat === "minimax_videos_v2";
  useEffect(() => {
    setSettings(official ? { ...DEFAULT_SETTINGS, seconds: 6, resolution: "768P" } : h3 ? { ...DEFAULT_SETTINGS, resolution: "768P" } : DEFAULT_SETTINGS);
  }, [official, h3, model, setSettings]);
  const renderControls = ({ duration, format }: { duration: ReactNode; format: ReactNode }) => {
    if (official || h3) return <section className={controlStyles.settings} aria-label="视频生成设置">
      <div className={controlStyles.row}>
        <span>视频</span>
        {duration}
        {format}
      </div>
    </section>;
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
            {duration}
          </fieldset>
          <div className="video-ratio-control">
            <span>画面比例</span>
            {format}
          </div>
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
  };
  return <VideoSettingsToolbar routeId={routeId} official={official} h3={h3} model={model}
    referenceCount={referenceCount} disabled={disabled} settings={settings} onChange={setSettings}
    renderControls={renderControls} />;
}

export function VideoGenerationMessagePart({
  data,
}: DataMessagePartProps<{ generationId?: string }>) {
  const { entries, retry, cancel, reuse, generating } = useVideoGeneration();
  const entry = data.generationId ? entries[data.generationId] : undefined;
  if (!entry) return null;
  return <VideoGenerationResult entry={entry} busy={generating} onRetry={() => retry(entry.id)}
    onCancel={() => cancel(entry.id)} onReuse={() => void reuse(entry.id)} />;
}

export function DurableVideoGenerationPart({ data }: { data: Record<string, unknown> }) {
  const { restore } = useVideoGeneration();
  const [id, setId] = useState<string | null>(null);
  const serialized = JSON.stringify(data);
  useEffect(() => { setId(restore(JSON.parse(serialized) as Record<string, unknown>)); }, [restore, serialized]);
  return id ? <VideoGenerationMessagePart type="data" name={VIDEO_GENERATION_PART_NAME} status={{ type: "complete" }} data={{ generationId: id }} /> : null;
}
