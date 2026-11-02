import type { Specialist } from "./assistant-client";
export type BotSidebar = { pinned: boolean; hidden: boolean; unread: boolean; group: string };
export function botSidebar(bot: Pick<Specialist, "config">): BotSidebar {
  const raw = bot.config.sidebar;
  const value = raw && typeof raw === "object" ? raw as Partial<BotSidebar> : {};
  return { pinned: value.pinned === true, hidden: value.hidden === true, unread: value.unread === true,
    group: typeof value.group === "string" ? value.group.trim().slice(0, 40) : "" };
}
