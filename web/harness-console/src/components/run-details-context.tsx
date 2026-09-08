"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { RunActivity } from "../lib/activity-schema";

interface RunDetailsControl {
  selectedRunId: string | null;
  open: (activity: RunActivity) => void;
}

const RunDetailsContext = createContext<RunDetailsControl | null>(null);

export function RunDetailsProvider({
  selectedRunId,
  onOpen,
  children,
}: {
  selectedRunId: string | null;
  onOpen: (activity: RunActivity) => void;
  children: ReactNode;
}) {
  return (
    <RunDetailsContext.Provider value={{ selectedRunId, open: onOpen }}>
      {children}
    </RunDetailsContext.Provider>
  );
}

export function useRunDetails() {
  return useContext(RunDetailsContext);
}
