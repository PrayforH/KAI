"use client";

import { createContext, useContext, type ReactNode } from "react";

/** Exact model text accepted by the server. Rejected candidates stay visible. */
const PublishedBlocksContext = createContext<readonly string[]>([]);

export function PublishedBlocksProvider({
  sources,
  children,
}: {
  sources: readonly string[];
  children: ReactNode;
}) {
  return (
    <PublishedBlocksContext.Provider value={sources}>
      {children}
    </PublishedBlocksContext.Provider>
  );
}

export function usePublishedBlocks(): readonly string[] {
  return useContext(PublishedBlocksContext);
}
