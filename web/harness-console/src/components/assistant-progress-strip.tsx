"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { assistantRequest, type Notice } from "../lib/assistant-client";

const KIND_LABEL: Record<string, { label: string; tone: "done" | "working" | "attention" }> = {
  succeeded: { label: "已交付", tone: "done" },
  failed: { label: "需要你处理", tone: "attention" },
  cancelled: { label: "已停止", tone: "attention" },
  pending: { label: "正在推进", tone: "working" },
  running: { label: "正在推进", tone: "working" },
};

/**
 * 8.5 结果优先：主助理页只突出「需要你处理、正在推进、已交付」。
 * 执行记录与完整过程仍在原任务；这里的卡片是引用，可追溯到原会话。
 */
export function AssistantProgressStrip() {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async () => {
    try {
      const list = await assistantRequest<Notice[]>("inbox");
      setNotices(list.slice(0, 6));
    } catch {
      // 后台进展是增强信息；读取失败不打断主对话。
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (!document.hidden) void load();
    }, 15000);
    return () => clearInterval(timer);
  }, [load]);

  const visible = notices.filter((item) => !dismissed.has(item.itemId));
  if (!visible.length) return null;
  const shown = expanded ? visible : visible.slice(0, 3);

  return (
    <aside className="assistant-progress-strip" aria-label="后台任务进展">
      <div className="assistant-progress-head">
        <span>后台任务进展</span>
        {visible.length > 3 && (
          <button type="button" onClick={() => setExpanded((value) => !value)}>
            {expanded ? "收起" : `展开全部 ${visible.length} 项`}
          </button>
        )}
      </div>
      <ul>
        {shown.map((item) => {
          const tone = KIND_LABEL[item.kind]?.tone ?? "working";
          return (
            <li key={item.itemId} data-tone={tone}>
              <span className="assistant-progress-kind">{KIND_LABEL[item.kind]?.label ?? item.kind}</span>
              <span className="assistant-progress-title">{item.title}</span>
              <span className="assistant-progress-excerpt">{item.content}</span>
              {item.threadId && (
                <Link
                  className="assistant-progress-link"
                  href={`/?thread=${encodeURIComponent(item.threadId)}`}
                  onClick={() => setDismissed((current) => new Set(current).add(item.itemId))}
                >
                  打开原任务 →
                </Link>
              )}
              <button
                type="button"
                aria-label="隐藏这条进展"
                onClick={() => setDismissed((current) => new Set(current).add(item.itemId))}
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
