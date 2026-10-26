import { ExportedMessageRepository, type ThreadMessage } from "@assistant-ui/core";
import type { LiveResponseSnapshot } from "./live-response-store";

type Repository = ReturnType<typeof ExportedMessageRepository.fromArray>;

/** AG-UI supplies a fresh createdAt when importing messages without dates. */
export function historyContentSignature(repository: Repository): string {
  return JSON.stringify(repository.messages.map(({ parentId, message }) => {
    const { createdAt: _createdAt, ...content } = message;
    return { parentId, message: content };
  }));
}

/** A lagging history page must never remove or shorten the answer on screen. */
export function canHandoffResponse(repository: Repository, live: LiveResponseSnapshot): boolean {
  if (!live.runId) return true;
  const prefix = `assistant-${live.runId}`;
  const answer = repository.messages.find(({ message }) => message.role === "assistant" &&
    (message.id === live.messageId || message.id === prefix || message.id.startsWith(`${prefix}-`)))?.message;
  if (!answer) return false;
  const text = answer.content.flatMap(part => part.type === "text" ? [part.text] : []).join("\n");
  return !live.visible || !live.text.trim() || text.includes(live.text.trim());
}

/** Refresh the current page without discarding earlier turns already loaded. */
export function retainEarlierTurns(repository: Repository, current: readonly ThreadMessage[]): Repository {
  const firstId = repository.messages[0]?.message.id;
  const offset = current.findIndex(message => message.id === firstId);
  if (offset <= 0) return repository;
  return ExportedMessageRepository.fromArray([
    ...current.slice(0, offset), ...repository.messages.map(item => item.message),
  ]);
}
