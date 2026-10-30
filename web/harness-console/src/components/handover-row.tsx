"use client";

import { useEffect } from "react";
import Link from "next/link";
import { handoverStore, useHandoverNotices } from "../lib/handover-store";
import { statusLead } from "../lib/handover-store";

/**
 * 主对话流内的转交状态行（对齐 Grok「已发送给 X」）：居中、细字、
 * 随消息滚动（非 sticky/置顶条），点击进入目标 bot 会话。
 * 数据源：派活工具的 ui.handover 自定义事件（即时）+ 页面加载时的一次性
 * handovers 投影（刷新后仍在）；无轮询。
 */
export function HandoverInlineRows() {
  const notices = useHandoverNotices();
  useEffect(() => {
    void handoverStore.seed();
    const onHandover = (event: Event) => handoverStore.ingest((event as CustomEvent).detail);
    window.addEventListener("kai:handover", onHandover);
    return () => window.removeEventListener("kai:handover", onHandover);
  }, []);
  if (!notices.length) return null;
  return (
    <div className="handover-inline" aria-label="转交状态">
      {notices.map((notice) => (
        <p className="handover-inline-line" key={notice.specialistId}>
          <Link href={`/assistant?bot=${encodeURIComponent(notice.specialistId)}`}>
            {statusLead(notice.status)}
            <span className="handover-inline-target">「{notice.specialistName}」</span>
          </Link>
        </p>
      ))}
    </div>
  );
}
