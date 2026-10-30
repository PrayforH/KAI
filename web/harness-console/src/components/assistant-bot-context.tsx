"use client";

import { createContext, useContext, type ReactNode } from "react";

/** 当前会话所属的机器人；主助理为空。用于标题、输入框占位与面板入口。 */
export type AssistantBot = {
  specialistId: string;
  name: string;
  description: string;
  status: string;
  threadId: string | null;
  objectiveId: string | null;
};

const AssistantBotContext = createContext<AssistantBot | null>(null);

export function AssistantBotProvider({
  bot,
  children,
}: {
  bot: AssistantBot | null;
  children: ReactNode;
}) {
  return <AssistantBotContext.Provider value={bot}>{children}</AssistantBotContext.Provider>;
}

export function useAssistantBot() {
  return useContext(AssistantBotContext);
}
