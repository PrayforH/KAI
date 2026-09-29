import { ExportedMessageRepository } from "@assistant-ui/core";
import { expect, it } from "vitest";
import { canHandoffResponse, retainEarlierTurns } from "../src/lib/response-history-handoff";
import { liveResponseStore } from "../src/lib/live-response-store";
const repository = (id: string, text: string) => ExportedMessageRepository.fromArray([
  { id, role: "assistant", content: [{ type: "text", text }] },
]);
it("rejects the previous run and truncated final text while a stop is being persisted", () => {
  const live = { ...liveResponseStore.getSnapshot(), runId: "new", messageId: "assistant-new", visible: true, text: "Complete answer" };
  expect(canHandoffResponse(repository("assistant-old", "Previous answer"), live)).toBe(false);
  expect(canHandoffResponse(repository("assistant-new", "Complete"), live)).toBe(false);
  expect(canHandoffResponse(repository("assistant-new", "Complete answer"), live)).toBe(true);
});
it("retains earlier turns across a paginated terminal refresh", () => {
  const old = repository("assistant-old", "Earlier answer").messages[0].message;
  const recent = repository("assistant-new", "Latest answer");
  const current = [old, recent.messages[0].message];
  expect(retainEarlierTurns(recent, current).messages.map(item => item.message.id)).toEqual(["assistant-old", "assistant-new"]);
});
