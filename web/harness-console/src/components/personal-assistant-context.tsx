"use client";

import { createContext, useContext, type ReactNode } from "react";

// AG-UI bindings are scoped by tenant and authenticated user on the server.
// This stable coordinate resumes the same personal conversation across devices.
export const PERSONAL_ASSISTANT_THREAD = "kai-personal-assistant-v1";
const PersonalAssistantContext = createContext(false);
export function PersonalAssistantEntry({ children }: { children: ReactNode }) {
  return <PersonalAssistantContext.Provider value>{children}</PersonalAssistantContext.Provider>;
}
export function usePersonalAssistantEntry() { return useContext(PersonalAssistantContext); }
