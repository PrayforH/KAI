"use client";

import { useEffect, useState } from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { locateInChat as locateMessage } from "../lib/chat-locate";
import { loadFullThreadHistory } from "../lib/task-history";
import {
  historyEntries,
  type ArtifactRecord,
  type HistoryEntry,
  type HistoryFile,
  type HistoryMessage,
} from "../lib/history-files";
import { HistoryDrawer } from "./history-drawer";

// 分类与筛选逻辑放在共享模块里，单助理与群聊共用；这里继续对外导出，
// 既有的调用方和测试不必改 import 路径。
export {
  dayOf,
  fileGlyph,
  fileKindOf,
  filterEntries,
  filterFiles,
  historyEntries,
  isMedia,
  messageOwnsArtifact,
  senderOf,
  type HistoryEntry,
  type HistoryFile,
  type HistoryMessage,
  type HistoryTab,
} from "../lib/history-files";

function fileHref(file: HistoryFile, threadId: string): string {
  if (file.inlineHref) return file.inlineHref;
  const query = new URLSearchParams({ preview: "1", thread_id: threadId });
  return `/api/harness/artifacts/${encodeURIComponent(file.artifactId ?? "")}?${query}`;
}

function openFile(file: HistoryFile) {
  if (!file.artifactId || typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("harness:preview-artifact", {
    detail: {
      artifact_id: file.artifactId,
      name: file.name,
      media_type: file.mediaType,
      size_bytes: file.sizeBytes,
      thread_id: null,
    },
  }));
}

/** 聊天记录抽屉：tabs、搜索、发送人/类型/日期筛选，每条记录都能回到原消息。 */
export function AssistantHistoryPanel({ open, threadId, assistantName = "助理", onClose }: {
  open: boolean; threadId: string; assistantName?: string; onClose: () => void;
}) {
  const [data, setData] = useState<{ entries: HistoryEntry[]; files: HistoryFile[] } | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setData(null); setError("");
    const query = new URLSearchParams({ thread_id: threadId, limit: "200" });
    void Promise.all([
      loadFullThreadHistory(threadId),
      fetch(`/api/harness/artifacts?${query}`, { cache: "no-store", signal: controller.signal })
        .then(requireAuthenticatedResponse)
        .then(async response => {
          if (!response.ok) throw new Error("文件暂时无法读取");
          return await response.json() as ArtifactRecord[];
        }),
    ]).then(([messages, artifacts]) => {
      if (controller.signal.aborted) return;
      setData(historyEntries(messages as HistoryMessage[], artifacts.filter(item => item.thread_id === threadId)));
    }).catch(cause => {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error && cause.message ? cause.message : "聊天记录暂时无法读取");
    });
    return () => controller.abort();
  }, [open, threadId, reload]);

  if (!open) return null;

  return <HistoryDrawer
    ariaLabel="聊天记录"
    assistantName={assistantName}
    status={error ? "error" : data === null ? "loading" : "ready"}
    error={error}
    entries={data?.entries ?? []}
    files={data?.files ?? []}
    emptyEntriesText="还没有聊天记录"
    searchPlaceholder="搜索聊天记录"
    hrefFor={file => fileHref(file, threadId)}
    onOpenFile={openFile}
    onLocate={messageId => locateMessage(messageId, threadId)}
    onRetry={() => setReload(value => value + 1)}
    onClose={onClose}
  />;
}
