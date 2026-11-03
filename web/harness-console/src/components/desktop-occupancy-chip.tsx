"use client";

import { useEffect, useState } from "react";

export type DesktopOccupancyHolder = {
  objective_id: string;
  execution_id: string;
  objective_title?: string | null;
  acquired_at?: string | null;
  expires_at?: string | null;
};

export type DesktopOccupancyWaiting = {
  objective_id: string;
  execution_id: string;
  objective_title?: string | null;
  position?: number;
  wait_reason?: string | null;
  /** 桌面排队优先级（af0b4edf 投影透出；旧载荷缺省视为 0，不展示）。 */
  desktop_priority?: number;
  desktopPriority?: number;
};

export type DesktopOccupancy = {
  holder: DesktopOccupancyHolder | null;
  waiting: DesktopOccupancyWaiting[];
};

const POLL_MS = 15_000;
const FETCH_TIMEOUT_MS = 8_000;

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 12)}…` : id;
}

/** 逐字段防御读取：缺字段/非数字（含布尔）一律 null，调用方按"无优先级"处理。 */
function finiteNumberField(item: unknown, key: string): number | null {
  if (typeof item !== "object" || item === null) return null;
  const raw = (item as Record<string, unknown>)[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

/** 队首桌面优先级：snake/camel 都认；缺字段安全回退 null（等价 0，不展示）。 */
function headDesktopPriority(first: unknown): number | null {
  return finiteNumberField(first, "desktop_priority") ?? finiteNumberField(first, "desktopPriority");
}

export type DesktopOccupancyView = {
  state: "held" | "queued";
  text: string;
  aria: string;
  /** 排队首位次（旧载荷没有 position 时为 null，文案退回计数）。 */
  position: number | null;
  /** 排队原因（中文，来自 P1-4 投影）；空则不加后缀。 */
  waitReason: string | null;
  /** 队首桌面优先级（仅 >0 时透出；旧载荷/缺字段/0 均为 null，不刷屏）。 */
  priority: number | null;
};

/**
 * 只读占用文案：有持有人 → 「占用：<职责标题>」（空标题退短 id）；
 * 有人排队时展示首位次与等待原因（「排队中 #1 · 共 2 · <原因>」），
 * 旧载荷没有 position 时退回「排队 N」。空闲（无占用且无排队）→ null。
 */
export function desktopOccupancyLabel(data: DesktopOccupancy | null): DesktopOccupancyView | null {
  if (!data) return null;
  const waitingList = Array.isArray(data.waiting) ? data.waiting : [];
  const waiting = waitingList.length;
  const holder = data.holder;
  const title = holder?.objective_title?.trim() || (holder?.objective_id ? shortId(holder.objective_id) : "");
  const first = waitingList[0];
  const position =
    typeof first?.position === "number" && Number.isFinite(first.position)
      ? first.position
      : null;
  const waitReason = first?.wait_reason?.trim() || null;
  const reasonSuffix = waiting && waitReason ? ` · ${waitReason}` : "";
  // 优先级只在 >0 时轻量点名（af0b4edf 只改排序不改文案口径）；0/缺字段零噪音。
  const priority = waiting ? headDesktopPriority(first) : null;
  const priorityShown = priority != null && priority > 0 ? priority : null;
  const prioritySuffix = priorityShown != null ? ` · 优 ${priorityShown}` : "";
  const ariaPriority = priorityShown != null ? `，优先级 ${priorityShown}` : "";

  if (holder && title) {
    const queue = !waiting
      ? ""
      : position != null
        ? ` · 排队中 #${position}${waiting > 1 ? ` · 共 ${waiting}` : ""}`
        : ` · 排队 ${waiting}`;
    const ariaQueue = !waiting
      ? ""
      : position != null
        ? `，排队中第 ${position} 位${waiting > 1 ? `，共 ${waiting} 项` : ""}`
        : `，${waiting} 项排队`;
    const ariaReason = waitReason ? `；等待原因：${waitReason}` : "";
    return {
      state: "held",
      text: `占用：${title}${queue}${prioritySuffix}${reasonSuffix}`,
      aria: `共享桌面：${title} 正在使用${ariaQueue}${ariaPriority}${ariaReason}`,
      position,
      waitReason: waiting ? waitReason : null,
      priority: priorityShown,
    };
  }
  if (waiting) {
    const queue =
      position != null
        ? `排队中 #${position}${waiting > 1 ? ` · 共 ${waiting}` : ""}`
        : `桌面排队 ${waiting}`;
    const ariaQueue =
      position != null
        ? `你在排队中第 ${position} 位${waiting > 1 ? `，共 ${waiting} 项` : ""}`
        : `${waiting} 项排队`;
    const ariaReason = waitReason ? `；等待原因：${waitReason}` : "";
    return {
      state: "queued",
      text: `${queue}${prioritySuffix}${reasonSuffix}`,
      aria: `共享桌面：暂无人占用，${ariaQueue}${ariaPriority}${ariaReason}`,
      position,
      waitReason,
      priority: priorityShown,
    };
  }
  return null;
}

/**
 * 顶栏电脑入口旁的只读占用 chip（接 P1-4 的 desktop/occupancy 投影）。
 * 轻轮询：页签隐藏时暂停，focus/visibilitychange 立即刷新，失败静默保留上次值。
 */
export function DesktopOccupancyChip() {
  const [data, setData] = useState<DesktopOccupancy | null>(null);

  useEffect(() => {
    let alive = true;
    let pending = false;
    let controller: AbortController | null = null;

    async function refresh() {
      if (pending || document.hidden) return;
      pending = true;
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), FETCH_TIMEOUT_MS);
      try {
        const response = await fetch("/api/studio/assistant/desktop/occupancy", {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const next = (await response.json()) as Partial<DesktopOccupancy>;
        if (!alive) return;
        setData({
          holder: next.holder ?? null,
          waiting: Array.isArray(next.waiting) ? next.waiting : [],
        });
      } catch {
        // 顶栏增强：读不到保留上一次成功值，不打断导航。
      } finally {
        clearTimeout(timeout);
        pending = false;
      }
    }

    void refresh();
    const interval = setInterval(() => void refresh(), POLL_MS);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      alive = false;
      clearInterval(interval);
      controller?.abort();
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  const label = desktopOccupancyLabel(data);
  if (!label) return null;
  return (
    <span
      className="desktop-occupancy-chip"
      data-occupancy={label.state}
      data-queue-position={label.position ?? undefined}
      data-wait-reason={label.waitReason ?? undefined}
      data-desktop-priority={label.priority ?? undefined}
      role="status"
      aria-live="polite"
      aria-label={label.aria}
      title={label.aria}
    >
      {label.text}
    </span>
  );
}
