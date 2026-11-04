"use client";

import { useSyncExternalStore } from "react";

export type HandoverNotice = {
  handoverId: string;
  sourceRunId: string | null;
  specialistId: string;
  specialistName: string;
  appearance?: { color?: string; avatar?: string };
  objectiveId?: string | null;
  status: string;
  at: number;
  createdAt: number;
  /** 用户明确 @某个 Bot 发出的消息：不挂在某条助手回复下，单独成行。 */
  mention?: boolean;
  sourceThreadId?: string | null;
  targetThreadId?: string | null;
  runId?: string | null;
  /** 用户 @ 发出的原文：在发送所在的对话区按用户消息展示。 */
  text?: string;
};

/** 用户 @ 某个 Bot 的持久回执前缀；与派活工具的键空间分开。 */
export const MENTION_MESSAGE_PREFIX = "mention-specialist:";

const STATUS_TEXT: Record<string, string> = {
  sent: "已发送消息给",
  received: "已发送消息给",
  applied: "已发送消息给",
  executing: "已发送消息给",
  pending_input: "已发送消息给",
  failed: "转交未完成 ·",
};

type IdentityListing = { specialistId: string; name: string; updatedAt?: string;
  config?: Record<string, unknown> };
const identities = new Map<string, {name: string; at: number; appearance: HandoverNotice["appearance"]}>();

const listeners = new Set<() => void>();
let entries: HandoverNotice[] = [];
let seeded = false;

function emitChange() {
  for (const listener of listeners) listener();
}

/** One durable handover per receipt; different requests to the same bot stay distinct. */
function upsert(notice: HandoverNotice) {
  const current = entries.find((item) => item.handoverId === notice.handoverId);
  // A slow history response must not replace a live receipt or a newer config result.
  if (current && current.at > notice.at) return;
  entries = [
    notice,
    ...entries.filter((item) => item.handoverId !== notice.handoverId),
  ].sort((a, b) => a.at - b.at);
  emitChange();
}

export const handoverStore = {
  /** Reuse the existing contact-list read; never fetch one profile per receipt. */
  updateSpecialists(specialists: readonly IdentityListing[]) {
    for (const item of specialists) {
      const raw = item.config?.appearance;
      const appearance = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
      identities.set(item.specialistId, {name: item.name,
        at: receiptTime(item.updatedAt, 0), appearance: {
          color: typeof appearance.color === "string" ? appearance.color : undefined,
          avatar: typeof appearance.avatar === "string" ? appearance.avatar : undefined,
        }});
    }
    let changed = false;
    const updated = entries.map(notice => {
      const identity = identities.get(notice.specialistId);
      if (!identity) return notice;
      // A delayed list without a newer identity timestamp cannot replace a
      // live receipt's name. Identity updates never change delivery status/time.
      const name = identity.at > notice.createdAt ? identity.name : notice.specialistName;
      if (name === notice.specialistName && identity.appearance?.color === notice.appearance?.color &&
          identity.appearance?.avatar === notice.appearance?.avatar) return notice;
      changed = true;
      return {...notice, specialistName: name, appearance: identity.appearance};
    });
    if (changed) { entries = updated; emitChange(); }
  },
  /** 派活工具的 ui.handover 事件（即时，免轮询）。 */
  ingest(payload: unknown) {
    if (typeof payload !== "object" || payload === null) return;
    const raw = payload as Record<string, unknown>;
    const specialistId = typeof raw.specialist_id === "string" ? raw.specialist_id : "";
    const specialistName = typeof raw.specialist_name === "string" ? raw.specialist_name : "";
    const handoverId = typeof raw.handover_id === "string" ? raw.handover_id : "";
    const sourceRunId = typeof raw.run_id === "string" ? raw.run_id : "";
    if (!handoverId || !sourceRunId || !specialistId || !specialistName) return;
    const createdAt = receiptTime(raw.created_at, Date.now());
    upsert({
      handoverId,
      sourceRunId,
      specialistId,
      specialistName,
      appearance: identities.get(specialistId)?.appearance,
      objectiveId: typeof raw.objective_id === "string" ? raw.objective_id : null,
      status: typeof raw.status === "string" ? raw.status : "sent",
      at: receiptTime(raw.updated_at, createdAt),
      createdAt,
    });
  },
  /** 用户 @某个 Bot 后的即时回执；刷新后由 seed() 从持久 handover 还原。 */
  ingestDelivery(payload: unknown) {
    if (typeof payload !== "object" || payload === null) return;
    const raw = payload as Record<string, unknown>;
    const required = ["handoverId", "specialistId", "specialistName", "sourceThreadId", "targetThreadId", "runId"];
    if (required.some(key => typeof raw[key] !== "string" || !raw[key])) return;
    handoverStore.ingestMention({
      handoverId: raw.handoverId as string,
      specialistId: raw.specialistId as string,
      specialistName: raw.specialistName as string,
      sourceThreadId: raw.sourceThreadId as string,
      targetThreadId: raw.targetThreadId as string,
      runId: raw.runId as string,
      createdAt: typeof raw.createdAt === "string" ? raw.createdAt : undefined,
    });
  },
  ingestMention(payload: {
    handoverId: string;
    specialistId: string;
    specialistName: string;
    createdAt?: string;
    sourceThreadId?: string | null;
    targetThreadId?: string | null;
    runId?: string | null;
    text?: string;
  }) {
    if (!payload.handoverId || !payload.specialistId) return;
    const createdAt = receiptTime(payload.createdAt, Date.now());
    upsert({
      handoverId: payload.handoverId,
      sourceRunId: null,
      specialistId: payload.specialistId,
      specialistName: payload.specialistName,
      appearance: identities.get(payload.specialistId)?.appearance,
      objectiveId: null,
      status: "sent",
      at: createdAt,
      createdAt,
      mention: true,
      sourceThreadId: payload.sourceThreadId,
      targetThreadId: payload.targetThreadId,
      runId: payload.runId,
      text: payload.text?.trim() || undefined,
    });
  },
  /** 页面加载的一次性投影：把持久 handovers 落成状态行（无轮询）。 */
  async seed() {
    if (seeded) return;
    seeded = true;
    try {
      const { assistantRequest } = await import("./assistant-client");
      const handovers = await assistantRequest<
        Array<{ handoverId: string; messageId: string; specialistId: string; objectiveId?: string | null; status: string; createdAt: string; updatedAt?: string; sourceThreadId?: string | null; targetThreadId?: string | null; runId?: string | null }>
      >("handovers");
      const specialists = await assistantRequest<
        IdentityListing[]
      >("specialists");
      handoverStore.updateSpecialists(specialists);
      const names = new Map(specialists.map((item) => [item.specialistId, item.name]));
      for (const handover of handovers) {
        const mention = handover.messageId.startsWith(MENTION_MESSAGE_PREFIX);
        upsert({
          handoverId: handover.handoverId,
          sourceRunId: mention ? null : sourceRunId(handover.messageId),
          specialistId: handover.specialistId,
          objectiveId: handover.objectiveId,
          specialistName: names.get(handover.specialistId) ?? "专职子助理",
          appearance: identities.get(handover.specialistId)?.appearance,
          status: handover.status,
          at: receiptTime(handover.updatedAt, receiptTime(handover.createdAt, 0)),
          createdAt: receiptTime(handover.createdAt, 0),
          mention,
          sourceThreadId: handover.sourceThreadId,
          targetThreadId: handover.targetThreadId,
          runId: handover.runId,
        });
      }
    } catch {
      seeded = false;
    }
  },
  /** Confirm one specific handover locally; never settle its siblings. */
  markConfigured(handoverId: string) {
    const current = entries.find((item) => item.handoverId === handoverId);
    if (!current) return;
    upsert({ ...current, status: "applied", at: Math.max(current.at, Date.now()) });
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  snapshot() {
    return entries;
  },
};

/** Legacy durable delegation keys already carry the originating Run; no migration. */
export function sourceRunId(messageId: string): string | null {
  return /^delegate-specialist:([^:]+):.+$/u.exec(messageId)?.[1] ?? null;
}

export function handoversForMessage(
  notices: readonly HandoverNotice[], messageId: string, liveRunId?: string,
): HandoverNotice[] {
  return notices.filter((notice) => {
    if (!notice.sourceRunId) return false;
    const prefix = `assistant-${notice.sourceRunId}`;
    return messageId === prefix || messageId.startsWith(`${prefix}-`) ||
      (messageId.startsWith("__optimistic__") && liveRunId === notice.sourceRunId);
  });
}

/** Unattributed legacy receipts stay hidden; never guess their originating chat. */
export function mentionsForThread(notices: readonly HandoverNotice[], threadId: string) {
  return notices.filter(notice => notice.mention && notice.sourceThreadId === threadId && Boolean(notice.runId));
}

function receiptTime(value: unknown, fallback: number): number {
  const time = typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? time : fallback;
}

/** Only the latest request owns a pending-input hint. */
export function isLatestHandover(notice: HandoverNotice, all: readonly HandoverNotice[]): boolean {
  return !all.some(other => other.specialistId === notice.specialistId &&
    (other.createdAt > notice.createdAt ||
      (other.createdAt === notice.createdAt && other.handoverId > notice.handoverId)));
}

export function statusLead(status: string): string {
  return STATUS_TEXT[status] ?? "转交状态待核实 ·";
}

export function useHandoverNotices(): HandoverNotice[] {
  // server snapshot 同样返回当前条目：模块态在服务端本来就是空，
  // 而单测的 SSR 渲染要先 ingest 再断言。
  return useSyncExternalStore(handoverStore.subscribe, handoverStore.snapshot, handoverStore.snapshot);
}
