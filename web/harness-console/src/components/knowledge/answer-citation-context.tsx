"use client";
import { createContext, useContext, useState, type ReactNode } from "react";
import type { RunCitation } from "../../lib/run-view-model";

const AnswerCitationContext = createContext<{
  citations: readonly RunCitation[];
  requested: RunCitation | null;
  request: (citation: RunCitation | null) => void;
} | null>(null);

export function AnswerCitationProvider({ citations, children }: { citations: readonly RunCitation[]; children: ReactNode }) {
  const [requested, request] = useState<RunCitation | null>(null);
  return <AnswerCitationContext.Provider value={{ citations, requested, request }}>{children}</AnswerCitationContext.Provider>;
}
export function useAnswerCitations() { return useContext(AnswerCitationContext); }
