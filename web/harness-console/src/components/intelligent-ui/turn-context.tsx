"use client";

import { createContext, useContext, type ReactNode } from "react";

/** How many model-authored answer blocks this turn actually published.
 *
 * The raw fences are hidden only for those, so a block the server rejected stays
 * visible as code instead of leaving a hole. It defaults to none, which is also
 * the correct answer for Markdown rendered outside an assistant turn. */
const PublishedBlocksContext = createContext(0);

export function PublishedBlocksProvider({
  count,
  children,
}: {
  count: number;
  children: ReactNode;
}) {
  return <PublishedBlocksContext.Provider value={count}>{children}</PublishedBlocksContext.Provider>;
}

export function usePublishedBlocks(): number {
  return useContext(PublishedBlocksContext);
}
