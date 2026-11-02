"use client";

import { createContext, useContext, type ReactNode } from "react";

/** 当前会话所属的机器人；主助理为空。用于标题、输入框占位与面板入口。 */
export type AssistantBot = {
  specialistId: string;
  name: string;
  description: string;
  agentName?: string;
  agentVersion?: string;
  status: string;
  threadId: string | null;
  objectiveId: string | null;
  appearance?: { color?: string; avatar?: string };
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

const AssistantSurfaceContext = createContext(false);

/** 是否处在"聊天形态"的助理面（任务页为 false，保留其完整工具条）。 */
export function AssistantSurfaceProvider({
  value,
  children,
}: {
  value: boolean;
  children: ReactNode;
}) {
  return (
    <AssistantSurfaceContext.Provider value={value}>{children}</AssistantSurfaceContext.Provider>
  );
}

export function useAssistantSurface() {
  return useContext(AssistantSurfaceContext);
}
