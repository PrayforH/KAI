"use client";

import type { DataMessagePartProps } from "@assistant-ui/react";
import { useMemo } from "react";
import type { RunActivity } from "../lib/activity-schema";
import { ActivitySummary } from "./activity-summary";

export const AUTHORING_PROGRESS_PART_NAME = "authoring-progress";

export type AuthoringProgress = {
  id: string;
  startedAt: string;
  completedAt?: string;
  summary?: string;
  responseStarted?: boolean;
};

// Authoring has a local request lifecycle, not a sandbox Run. Adapt it only
// for the shared presentation; never expose backend run-details actions.
export function AuthoringProgressPart({ data }: DataMessagePartProps<AuthoringProgress>) {
  const activity = useMemo<RunActivity>(() => ({
    run_id: `authoring-${data.id}`,
    started_at: data.startedAt,
    status: data.completedAt ? "succeeded" : "running",
    metrics: {},
    items: [{
      id: `${data.id}-state`,
      event_type: data.completedAt ? "run.succeeded" : "run.running",
      kind: "run",
      status: data.completedAt ? "succeeded" : "running",
      title: data.completedAt ? "处理完成" : "正在处理",
      timestamp: data.completedAt || data.startedAt,
      sequence: 1,
      metadata: {},
    }],
  }), [data.id, data.startedAt, data.completedAt]);
  return <ActivitySummary activity={activity} detailsAvailable={false}
    responseStarted={data.responseStarted} progressText={data.summary} />;
}
