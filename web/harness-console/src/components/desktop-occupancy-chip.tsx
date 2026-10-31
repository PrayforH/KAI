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

/**
 * 只读占用文案：有持有人 → 「占用：<职责标题>」（空标题退短 id）；
 * 有人排队时追加「· 排队 N」。空闲（无占用且无排队）→ null（不渲染 chip）。
 */
export function desktopOccupancyLabel(
  data: DesktopOccupancy | null,
): { state: "held" | "queued"; text: string; aria: string } | null {
  if (!data) return null;
  const waiting = Array.isArray(data.waiting) ? data.waiting.length : 0;
  const holder = data.holder;
  const title = holder?.objective_title?.trim() || (holder?.objective_id ? shortId(holder.objective_id) : "");
  if (holder && title) {
    const queue = waiting ? ` · 排队 ${waiting}` : "";
    return {
      state: "held",
      text: `占用：${title}${queue}`,
      aria: `共享桌面：${title} 正在使用${waiting ? `，${waiting} 项排队` : ""}`,
    };
  }
  if (waiting) {
    return {
      state: "queued",
      text: `桌面排队 ${waiting}`,
      aria: `共享桌面：${waiting} 项排队，暂无占用者`,
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
      role="status"
      aria-live="polite"
      aria-label={label.aria}
      title={label.aria}
    >
      {label.text}
    </span>
  );
}
