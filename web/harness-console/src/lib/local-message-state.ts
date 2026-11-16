"use client";

import { useEffect, useState } from "react";

/**
 * 单助理会话的表情目前没有对应的服务端接口（群聊才持久化）。
 * 为了让助理层拥有同一套消息操作，这里按线程把选择存在本机：
 * 刷新后仍保留，但换设备/清缓存会丢失。接口补齐后可直接替换本模块。
 * 单条消息删除已统一取消；旧版本存下的 hidden 列表直接忽略，消息恢复可见。
 */

type LocalMessageState = {
  reactions: Record<string, string[]>;
};

const EMPTY: LocalMessageState = { reactions: {} };
const EVENT = "kai:local-message-state";
const keyOf = (threadId: string) => `kai:assistant-message-local:${threadId}`;

function read(threadId: string): LocalMessageState {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(keyOf(threadId)) ?? "{}");
    if (!raw || typeof raw !== "object") return EMPTY;
    const record = raw as Partial<LocalMessageState>;
    const reactions: Record<string, string[]> = {};
    if (record.reactions && typeof record.reactions === "object") {
      for (const [messageId, emojis] of Object.entries(record.reactions)) {
        if (Array.isArray(emojis)) {
          reactions[messageId] = emojis.filter((item): item is string => typeof item === "string");
        }
      }
    }
    // 旧版记录里的 hidden 不再读取：被本机隐藏的消息统一恢复可见。
    return { reactions };
  } catch {
    return EMPTY;
  }
}

function write(threadId: string, next: LocalMessageState) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(keyOf(threadId), JSON.stringify(next));
  } catch {
    // 存储不可用时仍保留当前页面内的表现，不阻塞消息操作。
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: threadId }));
}

export function toggleLocalReaction(threadId: string, messageId: string, emoji: string) {
  if (!threadId) return;
  const current = read(threadId);
  const existing = current.reactions[messageId] ?? [];
  const next = existing.includes(emoji)
    ? existing.filter((item) => item !== emoji)
    : [...existing, emoji];
  write(threadId, {
    ...current,
    reactions: { ...current.reactions, [messageId]: next },
  });
}

export function useLocalMessageState(threadId: string | undefined, messageId: string) {
  const [state, setState] = useState<LocalMessageState>(EMPTY);

  useEffect(() => {
    if (!threadId) {
      setState(EMPTY);
      return;
    }
    const sync = (event: Event) => {
      if (event instanceof CustomEvent && event.detail && event.detail !== threadId) return;
      setState(read(threadId));
    };
    setState(read(threadId));
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, [threadId]);

  if (!threadId) {
    return {
      reactions: [] as string[],
      toggleReaction: (_emoji: string) => undefined,
    };
  }

  return {
    reactions: state.reactions[messageId] ?? [],
    toggleReaction: (emoji: string) => toggleLocalReaction(threadId, messageId, emoji),
  };
}
