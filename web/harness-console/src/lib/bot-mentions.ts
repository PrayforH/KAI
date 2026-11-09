"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "./assistant-client";
import { isPrimaryBot } from "./bot-sidebar";

/** 可被 @ 提及的助理：稳定 ID + 名字；同名靠 ID 区分。 */
export type MentionedAssistant = {
  specialistId: string;
  name: string;
  description?: string;
  appearance?: { color?: string; avatar?: string };
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
    let revision = 0;
    const load = async () => {
      const requested = ++revision;
      try {
        const list = await assistantRequest<Specialist[]>("specialists");
        if (requested !== revision) return;
        setItems(list
          .filter(item => item.status !== "archived" && !isPrimaryBot(item))
          .map(item => ({
            specialistId: item.specialistId,
            name: item.name,
            description: item.description || undefined,
            appearance: item.config?.appearance as MentionedAssistant["appearance"],
          })));
      } catch { /* Keep the last available list when a refresh fails. */ }
    };
    void load();
    window.addEventListener("kai:specialists-changed", load);
    return () => {
      revision++;
      window.removeEventListener("kai:specialists-changed", load);
    };
  }, [enabled]);
  return items;
}

function mentionTokenPattern(name: string): RegExp {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // 名字后面不能再跟文字或数字：@研究Bot 不该命中 @研究Bot2。
  return new RegExp(`@${escaped}(?![\\p{L}\\p{N}_])`, "gu");
}

/**
 * 从输入文本里解析被 @ 的助理：和普通聊天一样，@ 名字就写在文本行里，可以有多个。
 * 删掉文本里的 @ 名字，提及就跟着消失；不单独维护一份选中状态。
 */
export function parseMentions(
  text: string,
  assistants: readonly MentionedAssistant[],
): MentionedAssistant[] {
  if (!text.includes("@") || !assistants.length) return [];
  const found: MentionedAssistant[] = [];
  // 长名字先匹配，避免「研究Bot2」被误认成「研究Bot」。
  for (const bot of [...assistants].sort((a, b) => b.name.length - a.name.length)) {
    if (found.some((item) => item.specialistId === bot.specialistId)) continue;
    if (!mentionTokenPattern(bot.name).test(text)) continue;
    found.push(bot);
  }
  return found;
}

/** 把已送达的 @ 名字从文本里拿掉（局部失败时保留没送出去的那些，便于重试）。 */
export function removeMentionTokens(
  text: string,
  bots: readonly MentionedAssistant[],
): string {
  let next = text;
  for (const bot of bots) next = next.replace(mentionTokenPattern(bot.name), "");
  return next.replace(/ {2,}/g, " ").trim();
}

/** 幂等键：同一把钥匙重复提交只会留下一条回执。 */
export function mentionIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `mention-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 一次 @ 多个助理的投递结果：成功与失败分开记账，互不阻断。 */
export type MentionDeliveryResult = {
  delivered: string[];
  failed: Array<{ specialistId: string; name: string; reason: string }>;
};

/**
 * 逐个投递：某个目标失败不阻断其余目标。
 * 失败的目标留在提及里可以重试，已送达的照常发回执。
 */
export async function deliverMentions(
  mentions: readonly MentionedAssistant[],
  text: string,
  deliver: (bot: MentionedAssistant, text: string) => Promise<void>,
): Promise<MentionDeliveryResult> {
  const delivered: string[] = [];
  const failed: MentionDeliveryResult["failed"] = [];
  for (const bot of mentions) {
    try {
      await deliver(bot, text);
      delivered.push(bot.specialistId);
    } catch (error) {
      failed.push({
        specialistId: bot.specialistId,
        name: bot.name,
        reason: error instanceof Error ? error.message : "发送失败",
      });
    }
  }
  return { delivered, failed };
}
