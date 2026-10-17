"use client";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { useAuiState } from "@assistant-ui/react";
import { conversationSecretInputs } from "../lib/conversation-secret-inputs";
import { collectConversationSecrets, redactConversationText } from "../lib/conversation-secrets";
const Redaction = createContext<(text: string) => string>(redactConversationText);
export function ConversationRedactionProvider({children}: {children: ReactNode}) {
  // A primitive selector changes only with authored user text, not every
  // assistant token. Values stay in memory and never enter browser storage.
  const serialized = useAuiState(state => JSON.stringify(conversationSecretInputs(state.thread.messages)));
  const secrets = useMemo(() => collectConversationSecrets(JSON.parse(serialized) as string[]), [serialized]);
  const redact = useCallback((text: string) => redactConversationText(text, secrets), [secrets]);
  return <Redaction.Provider value={redact}>{children}</Redaction.Provider>;
}
export function useConversationRedactor() { return useContext(Redaction); }
