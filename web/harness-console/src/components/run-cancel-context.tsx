"use client";
import { createContext, useContext } from "react";
const RunCancelContext = createContext<(() => Promise<void>) | null>(null);
export const RunCancelProvider = RunCancelContext.Provider;
export function useRunCancel() { return useContext(RunCancelContext); }
