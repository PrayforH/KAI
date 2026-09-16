// A later thinking block or action proves the preceding prose was progress.
export function isProcessBoundary(eventType: string): boolean {
  return eventType === "tool.request" || eventType === "subagent.started" ||
    eventType === "reasoning.delta" || eventType === "reasoning.summary.delta";
}
