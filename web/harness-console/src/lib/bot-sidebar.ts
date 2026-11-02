import type { Specialist } from "./assistant-client";
export type BotSidebar = { pinned: boolean; hidden: boolean; unread: boolean; group: string };
export function botSidebar(bot: Pick<Specialist, "config">): BotSidebar {
  const raw = bot.config.sidebar;
  const value = raw && typeof raw === "object" ? raw as Partial<BotSidebar> : {};
  return { pinned: value.pinned === true, hidden: value.hidden === true, unread: value.unread === true,
    group: typeof value.group === "string" ? value.group.trim().slice(0, 40) : "" };
}

/** Creation order is stable under reads, replies, profile edits and polling. */
export function stableBotOrder(bots: readonly Specialist[]): Specialist[] {
  const time = (bot: Specialist) => Date.parse(bot.createdAt) || 0;
  return [...bots].sort((a, b) => time(a) - time(b) || a.specialistId.localeCompare(b.specialistId));
}

export function isPrimaryBot(bot: Pick<Specialist, "specialistId">): boolean {
  return bot.specialistId.startsWith("specialist_primary_");
}
export function botConversationHref(bot: Pick<Specialist, "specialistId">): string {
  return isPrimaryBot(bot) ? "/assistant" : `/assistant?bot=${encodeURIComponent(bot.specialistId)}`;
}
