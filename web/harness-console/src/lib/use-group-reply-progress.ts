"use client";

import { useEffect, useState } from "react";

import {
  GROUP_REPLY_PROGRESS_EMPTY,
  isTerminalEvent,
  nextGroupReplyProgress,
  takeSseFrames,
  type GroupReplyProgress,
} from "./group-reply-progress";

/** 事件流回放一次就结束，未到终态时隔一小段再按 Last-Event-ID 续读。 */
const RESUME_DELAY_MS = 1200;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function eventsUrl(runId: string) {
  return `/api/agui/runs/${encodeURIComponent(runId)}/events`;
}

/**
 * 跟随一条运行中的群回复。只读既有 run 事件流，不新建 Run、不写库。
 *
 * `runId` 变化（空回复自动重试会保留消息、换 runId）时整个订阅重建，
 * 用最新 runId 继续读。任何失败（断流、404/403、跨群切换）都静默停下：
 * 进度清回兜底文案，消息轮询照旧替换最终结果。
 */
export function useGroupReplyProgress(
  runId: string | null | undefined,
  active: boolean,
): GroupReplyProgress["stage"] {
  const [progress, setProgress] = useState<GroupReplyProgress>(GROUP_REPLY_PROGRESS_EMPTY);

  useEffect(() => {
    if (!active || !runId) {
      setProgress(GROUP_REPLY_PROGRESS_EMPTY);
      return;
    }
    setProgress(GROUP_REPLY_PROGRESS_EMPTY);
    const controller = new AbortController();
    let lastEventId: string | undefined;

    /** 订阅异常终止时退回兜底文案：占位不能停在过期的阶段上。 */
    const reset = () => {
      if (!controller.signal.aborted) setProgress(GROUP_REPLY_PROGRESS_EMPTY);
    };

    void (async () => {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(eventsUrl(runId), {
            signal: controller.signal,
            cache: "no-store",
            headers: {
              Accept: "text/event-stream",
              ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
            },
          });
          if (!response.ok || !response.body) {
            // 403/404/空响应体：不再续读，阶段退回兜底，等轮询替换最终消息。
            reset();
            return;
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let finished = false;
          try {
            while (!controller.signal.aborted) {
              const { value, done } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              const { frames, rest } = takeSseFrames(buffer);
              buffer = rest;
              for (const frame of frames) {
                if (frame.id) lastEventId = frame.id;
                let event: { type?: string } & Record<string, unknown>;
                try {
                  event = JSON.parse(frame.data) as { type?: string };
                } catch {
                  continue; // 心跳或非 JSON 帧不影响阶段。
                }
                if (isTerminalEvent(event)) {
                  finished = true;
                  break;
                }
                setProgress((current) => nextGroupReplyProgress(current, event));
              }
              if (finished) break;
            }
          } finally {
            await reader.cancel().catch(() => undefined);
          }
          if (finished || controller.signal.aborted) return;
        } catch {
          // 网络错误、读取中断：停止细化阶段并清回兜底，轮询仍然是权威来源。
          reset();
          return;
        }
        await sleep(RESUME_DELAY_MS);
      }
    })();

    return () => controller.abort();
  }, [active, runId]);

  return active ? progress.stage : undefined;
}
