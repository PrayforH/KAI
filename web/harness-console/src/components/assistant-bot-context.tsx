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

export type ConversationSurface = "task" | "assistant";
const ConversationSurfaceContext = createContext<ConversationSurface>("task");

/** Presentation belongs to the current conversation, never its entry route. */
export function ConversationSurfaceProvider({
  surface,
  children,
}: {
  surface: ConversationSurface;
  children: ReactNode;
}) {
  return <ConversationSurfaceContext.Provider value={surface}>{children}</ConversationSurfaceContext.Provider>;
}

export function useAssistantSurface() {
  return useContext(ConversationSurfaceContext) === "assistant";
}
