"use client";

import { useSyncExternalStore } from "react";

export type HandoverNotice = {
  specialistId: string;
  specialistName: string;
  objectiveId?: string | null;
  status: string;
  at: number;
};

const STATUS_TEXT: Record<string, string> = {
  sent: "已发送给",
  received: "已转交给",
  applied: "已安排",
  executing: "执行中 ·",
  pending_input: "待补充 ·",
  failed: "转交失败 ·",
};

const LIMIT = 3;
const listeners = new Set<() => void>();
let entries: HandoverNotice[] = [];
let seeded = false;

function emitChange() {
  for (const listener of listeners) listener();
}

/** 一次快照去重：同一 bot 只保留最新一条。 */
function upsert(notice: HandoverNotice) {
  const current = entries.find((item) => item.specialistId === notice.specialistId);
  // A slow history response must not replace a live receipt or a newer config result.
  if (current && current.at > notice.at) return;
  entries = [
    notice,
    ...entries.filter((item) => item.specialistId !== notice.specialistId),
  ].sort((a, b) => b.at - a.at).slice(0, LIMIT);
  emitChange();
}

export const handoverStore = {
  /** 派活工具的 ui.handover 事件（即时，免轮询）。 */
  ingest(payload: unknown) {
    if (typeof payload !== "object" || payload === null) return;
    const raw = payload as Record<string, unknown>;
    const specialistId = typeof raw.specialist_id === "string" ? raw.specialist_id : "";
    const specialistName = typeof raw.specialist_name === "string" ? raw.specialist_name : "";
    if (!specialistId || !specialistName) return;
    upsert({
      specialistId,
      specialistName,
      objectiveId: typeof raw.objective_id === "string" ? raw.objective_id : null,
      status: typeof raw.status === "string" ? raw.status : "sent",
      at: Date.now(),
    });
  },
  /** 页面加载的一次性投影：把持久 handovers 落成状态行（无轮询）。 */
  async seed() {
    if (seeded) return;
    seeded = true;
    try {
      const { assistantRequest } = await import("./assistant-client");
      const handovers = await assistantRequest<
        Array<{ specialistId: string; objectiveId?: string | null; status: string; createdAt: string; updatedAt?: string }>
      >("handovers");
      const specialists = await assistantRequest<
        Array<{ specialistId: string; name: string }>
      >("specialists");
      const names = new Map(specialists.map((item) => [item.specialistId, item.name]));
      for (const handover of handovers) {
        upsert({
          specialistId: handover.specialistId,
          objectiveId: handover.objectiveId,
          specialistName: names.get(handover.specialistId) ?? "专职子助理",
          status: handover.status,
          at: Date.parse(handover.updatedAt ?? handover.createdAt) || 0,
        });
      }
    } catch {
      seeded = false;
    }
  },
  /** 主会话内联补齐配置成功后：本地把该行从「待补充」改为「已安排」。 */
  markConfigured(specialistId: string) {
    const current = entries.find((item) => item.specialistId === specialistId);
    if (!current) return;
    upsert({ ...current, status: "applied", at: Date.now() });
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  snapshot() {
    return entries;
  },
};

export function statusLead(status: string): string {
  return STATUS_TEXT[status] ?? "已发送给";
}

export function useHandoverNotices(): HandoverNotice[] {
  // server snapshot 同样返回当前条目：模块态在服务端本来就是空，
  // 而单测的 SSR 渲染要先 ingest 再断言。
  return useSyncExternalStore(handoverStore.subscribe, handoverStore.snapshot, handoverStore.snapshot);
}
