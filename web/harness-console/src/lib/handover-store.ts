"use client";

import { useSyncExternalStore } from "react";

export type HandoverNotice = {
  handoverId: string;
  sourceRunId: string | null;
  specialistId: string;
  specialistName: string;
  objectiveId?: string | null;
  status: string;
  at: number;
  createdAt: number;
};

const STATUS_TEXT: Record<string, string> = {
  sent: "已发送给",
  received: "已转交给",
  applied: "已安排",
  executing: "执行中 ·",
  pending_input: "待补充 ·",
  failed: "转交失败 ·",
};

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
      objectiveId: typeof raw.objective_id === "string" ? raw.objective_id : null,
      status: typeof raw.status === "string" ? raw.status : "sent",
      at: receiptTime(raw.updated_at, createdAt),
      createdAt,
    });
  },
  /** 页面加载的一次性投影：把持久 handovers 落成状态行（无轮询）。 */
  async seed() {
    if (seeded) return;
    seeded = true;
    try {
      const { assistantRequest } = await import("./assistant-client");
      const handovers = await assistantRequest<
        Array<{ handoverId: string; messageId: string; specialistId: string; objectiveId?: string | null; status: string; createdAt: string; updatedAt?: string }>
      >("handovers");
      const specialists = await assistantRequest<
        Array<{ specialistId: string; name: string }>
      >("specialists");
      const names = new Map(specialists.map((item) => [item.specialistId, item.name]));
      for (const handover of handovers) {
        upsert({
          handoverId: handover.handoverId,
          sourceRunId: sourceRunId(handover.messageId),
          specialistId: handover.specialistId,
          objectiveId: handover.objectiveId,
          specialistName: names.get(handover.specialistId) ?? "专职子助理",
          status: handover.status,
          at: receiptTime(handover.updatedAt, receiptTime(handover.createdAt, 0)),
          createdAt: receiptTime(handover.createdAt, 0),
        });
      }
    } catch {
      seeded = false;
    }
  },
  /** 主会话内联补齐配置成功后：本地把该行从「待补充」改为「已安排」。 */
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

function receiptTime(value: unknown, fallback: number): number {
  const time = typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? time : fallback;
}

/** Current requests own actionable prompts; historical receipts stay lightweight. */
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
