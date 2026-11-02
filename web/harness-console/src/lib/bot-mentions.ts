"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "./assistant-client";
import { isPrimaryBot } from "./bot-sidebar";

/** 可被 @ 提及的助理：稳定 ID + 名字；同名靠 ID 区分。 */
export type MentionedAssistant = {
  specialistId: string;
  name: string;
  description?: string;
};

export const MENTION_PREFIX = "@assistant:";

/** @ 候选只看本人可访问、且没有归档的助理；主助理就是当前会话，不列自己。 */
export function useMentionableAssistants(enabled: boolean): MentionedAssistant[] {
  const [items, setItems] = useState<MentionedAssistant[]>([]);
  useEffect(() => {
    if (!enabled) {
      setItems([]);
      return;
    }
    let alive = true;
    void assistantRequest<Specialist[]>("specialists")
      .then(list => {
        if (!alive) return;
        setItems(
          list
            .filter(item => item.status !== "archived" && !isPrimaryBot(item))
            .map(item => ({
              specialistId: item.specialistId,
              name: item.name,
              description: item.description || undefined,
            })),
        );
      })
      .catch(() => {
        /* 候选读不到时不猜：没有候选就保持普通输入。 */
      });
    return () => {
      alive = false;
    };
  }, [enabled]);
  return items;
}

/** 幂等键：同一把钥匙重复提交只会留下一条回执。 */
export function mentionIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `mention-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
