"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useAuiState } from "@assistant-ui/react";
import { PERSONAL_ASSISTANT_THREAD } from "./personal-assistant-context";
import {
  getReplyStoreServerVersion,
  getReplyStoreVersion,
  locateReplyOriginal,
  lookupReply,
  markReplyUnavailable,
  subscribeReplyReferences,
} from "../lib/reply-references";
import type { ReplyReference } from "../lib/reply-references";
import { assistantQuoteCopy } from "../lib/assistant-copy";

function quoteLabel(text: string | null | undefined, threadId: string) {
  return threadId === PERSONAL_ASSISTANT_THREAD
    ? assistantQuoteCopy(text ?? "")
    : text;
}

export type { ReplyReference };

/**
 * The composer-level reply state. `reply` is the reference shown in the quote
 * bar; the send path moves it into the outgoing run's forwardedProps.
 */
type ComposerReplyController = {
  threadId: string;
  reply: ReplyReference | null;
  startReply: (reply: ReplyReference) => void;
  cancelReply: () => void;
};

const ComposerReplyContext = createContext<ComposerReplyController | null>(null);

export function useComposerReply() {
  return useContext(ComposerReplyContext);
}

export function ComposerReplyProvider({
  threadId,
  children,
}: {
  threadId: string;
  children: ReactNode;
}) {
  const [reply, setReply] = useState<ReplyReference | null>(null);
  // Switching conversations never carries a reference across threads.
  useEffect(() => {
    setReply(null);
  }, [threadId]);
  const controller = useMemo<ComposerReplyController>(
    () => ({
      threadId,
      reply,
      startReply: setReply,
      cancelReply: () => setReply(null),
    }),
    [reply, threadId],
  );
  return (
    <ComposerReplyContext.Provider value={controller}>{children}</ComposerReplyContext.Provider>
  );
}

/** Provided by the runtime shell: where the outgoing reference is attached. */
export const ReplySendContext = createContext<(reply: ReplyReference | null) => void>(
  () => undefined,
);

function ReplyGlyph() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M8 5 3.5 9.5 8 14" />
      <path d="M3.5 9.5h8A5 5 0 0 1 16.5 14v1" />
    </svg>
  );
}

function focusComposerInput() {
  document
    .querySelector<HTMLTextAreaElement>(".harness-composer-shell .aui-composer-input")
    ?.focus();
}

/** Reply action for message action bars; quotes the message it renders on. */
export function MessageReplyButton({ className, label, excerptOverride }: {
  className?: string; label: string; excerptOverride?: string;
}) {
  const controller = useComposerReply();
  const messageId = useAuiState((state) => state.message.id);
  const text = useAuiState((state) =>
    state.message.content
      .map((part) => (part.type === "text" ? part.text : ""))
      .filter(Boolean)
      .join("\n"),
  );
  if (!controller) return null;
  const excerpt = (excerptOverride ?? text).trim().replace(/\s+/g, " ").slice(0, 160);
  return (
    <button
      type="button"
      className={className ?? "user-message-action"}
      aria-label={label}
      title={label}
      onClick={() => {
        controller.startReply({ messageId, excerpt: excerpt || null });
        focusComposerInput();
      }}
    >
      <ReplyGlyph />
    </button>
  );
}

/** Quote bar inside the composer: icon, one-line excerpt, cancel; thin divider below. */
export function ReplyComposerBar() {
  const controller = useComposerReply();
  if (!controller?.reply) return null;
  const { reply, cancelReply } = controller;
  return (
    <>
      <div className="composer-reply-bar">
        <ReplyGlyph />
        <span className="composer-reply-excerpt" title={quoteLabel(reply.excerpt, controller.threadId) ?? undefined}>
          {quoteLabel(reply.excerpt, controller.threadId) || "引用原消息"}
        </span>
        <button
          type="button"
          className="composer-reply-cancel"
          aria-label="取消引用"
          onClick={cancelReply}
        >
          ×
        </button>
      </div>
      <div className="composer-reply-divider" role="presentation" />
    </>
  );
}

/**
 * Reply header above a quoted user message. Click locates the original in
 * view, or fetches its context by id; an unresolvable original degrades to
 * 原消息不可用 without re-pointing anywhere else.
 */
export function UserReplyHeader({ isLatestUserMessage }: { isLatestUserMessage: boolean }) {
  const controller = useComposerReply();
  const messageId = useAuiState((state) => state.message.id);
  useSyncExternalStore(subscribeReplyReferences, getReplyStoreVersion, getReplyStoreServerVersion);
  const lookup = controller
    ? lookupReply(controller.threadId, messageId, isLatestUserMessage)
    : null;
  const [original, setOriginal] = useState<string | null>(null);
  if (!controller || !lookup) return null;
  const { reply, unavailable } = lookup;

  async function open() {
    if (unavailable) return;
    const outcome = await locateReplyOriginal(controller!.threadId, reply);
    if (outcome.kind === "unavailable") {
      markReplyUnavailable(controller!.threadId, reply.messageId);
      return;
    }
    if (outcome.kind === "original") {
      setOriginal(outcome.excerpt);
    }
  }

  return (
    <>
      <button
        type="button"
        className={`user-reply-header${unavailable ? " unavailable" : ""}`}
        onClick={() => void open()}
        aria-label={unavailable ? "原消息不可用" : "定位被引用的消息"}
      >
        <ReplyGlyph />
        <span className="user-reply-excerpt">
          {unavailable ? "原消息不可用" : quoteLabel(reply.excerpt, controller.threadId) || "引用原消息"}
        </span>
      </button>
      {original ? (
        <div className="user-reply-original" role="note">
          {quoteLabel(original, controller.threadId)}
        </div>
      ) : null}
    </>
  );
}

/** Composer placeholder per the assistant persona (8.12): 消息 KAI. */
export function replyAwareComposerPlaceholder(
  threadId: string | undefined,
  busy: boolean,
  scoped: string | undefined,
) {
  if (scoped) return scoped;
  if (threadId === PERSONAL_ASSISTANT_THREAD) return busy ? "继续告诉 KAI…" : "消息 KAI";
  return busy ? "继续补充…" : "输入消息…";
}
