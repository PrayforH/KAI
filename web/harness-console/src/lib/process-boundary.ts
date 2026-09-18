// A later thinking block or action proves the preceding prose was progress.
export function isProcessBoundary(eventType: string): boolean {
  return eventType === "tool.request" || eventType === "subagent.started" ||
    eventType === "reasoning.delta" || eventType === "reasoning.summary.delta";
}

/**
 * Events that end the answer for good, mirroring the server's response
 * projection (`harness/agui/response.py`, `_RESPONSE_BOUNDARY_PREFIXES`): the
 * final answer is what follows the last tool, approval or subagent action.
 * Thinking is not one of them, so a thinking block never moves an already
 * streamed answer into the process log.
 */
const RESPONSE_BOUNDARY_PREFIXES = ["approval.", "subagent.", "tool."] as const;

export function isResponseBoundary(eventType: string): boolean {
  return RESPONSE_BOUNDARY_PREFIXES.some((prefix) => eventType.startsWith(prefix));
}
