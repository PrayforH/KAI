"use client";

import { useEffect } from "react";
import { handoverStore, useHandoverNotices } from "../lib/handover-store";
import { statusLead } from "../lib/handover-store";

/**
 * 转交回执属于主助理会话，而不是当前打开的任何任务线程。
 * /assistant 入口会给整页包上 PersonalAssistantEntry；任务面切回来时它仍为
 * true，所以还要结合助理面和 bot 绑定，避免回执落进历史任务消息流。
 */
export function shouldShowHandoverRows(
  personalAssistant: boolean,
  assistantSurface: boolean,
  hasBot: boolean,
): boolean {
  return personalAssistant && assistantSurface && !hasBot;
}

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
          {/* 与 bot-rail 同判：软导航不重挂载页面，旧线程会盖住新 bot。 */}
          <a
            href={`/assistant?bot=${encodeURIComponent(notice.specialistId)}`}
            onClick={(event) => {
              event.preventDefault();
              window.location.assign(`/assistant?bot=${encodeURIComponent(notice.specialistId)}`);
            }}
          >
            {statusLead(notice.status)}
            <span className="handover-inline-target">「{notice.specialistName}」</span>
          </a>
        </p>
      ))}
    </div>
  );
}
