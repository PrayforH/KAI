"use client";

import { createContext, useContext } from "react";

/**
 * Whether this thread's stored conversation has arrived. Until it has, the
 * thread is legitimately empty, and painting the "start a new task" welcome for
 * that moment made opening a task flash the whole conversation area.
 */
const ThreadHistoryReadyContext = createContext(true);

export const ThreadHistoryReadyProvider = ThreadHistoryReadyContext.Provider;

export function useThreadHistoryReady() {
  return useContext(ThreadHistoryReadyContext);
}
