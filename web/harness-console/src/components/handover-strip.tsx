"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { assistantRequest, type Handover, type Specialist } from "../lib/assistant-client";

const STATUS_TEXT: Record<string, string> = {
  sent: "已发送消息给",
  received: "已接收",
  applied: "已安排执行",
  executing: "执行中",
  pending_input: "待补充",
  failed: "转交失败",
};

/**
 * 主对话里的转交状态行：对齐客户端「已发送消息给 X」的居中细状态。
 * 点开可进入承接该职责的 bot 会话——派活后还能回到主对话继续聊。
 */
export function HandoverStrip() {
  const [rows, setRows] = useState<Array<{ handover: Handover; name: string; threadId: string | null }>>([]);

  const load = useCallback(async () => {
    try {
      const [handovers, specialists] = await Promise.all([
        assistantRequest<Handover[]>("handovers"),
        assistantRequest<Specialist[]>("specialists"),
      ]);
      const byId = new Map(specialists.map((item) => [item.specialistId, item]));
      const recent = handovers.slice(0, 3).map((handover) => ({
        handover,
        name: byId.get(handover.specialistId)?.name ?? "专职子助理",
        threadId: null as string | null,
      }));
      setRows(recent);
    } catch {
      // 状态行是增强信息；读不到就不显示，不打断主对话。
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (!document.hidden) void load();
    }, 20000);
    return () => clearInterval(timer);
  }, [load]);

  if (!rows.length) return null;
  return (
    <div className="handover-strip" aria-label="转交状态">
      {rows.map(({ handover, name }) => {
        const label = STATUS_TEXT[handover.status] ?? handover.status;
        return (
          <p className="handover-line" key={handover.handoverId}>
            <span>{label}</span>
            <span className="handover-target">{name}</span>
            {handover.objectiveId && (
              <Link
                className="handover-link"
                href={`/studio/assistant`}
                title="查看这个子助理的配置与转交记录"
              >
                查看
              </Link>
            )}
          </p>
        );
      })}
    </div>
  );
}
