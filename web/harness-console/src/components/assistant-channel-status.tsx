"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ProductIcon } from "./product-icon";
import styles from "./assistant-channel-status.module.css";

type Channel = { name: string; state: "online" | "offline" | "unbound" | "unknown" | "disconnected" };
type Status = { enabled?: boolean; bound?: boolean; online?: boolean; receiverOnline?: boolean; receiverState?: string };
const channels = [{ key: "weixin", name: "微信" }, { key: "feishu", name: "飞书" }];
const labels = { online: "已连接", offline: "离线", unbound: "待绑定", unknown: "状态待确认", disconnected: "未连接" };

export function AssistantChannelStatus() {
  const [status, setStatus] = useState<Channel[] | null>(null);
  useEffect(() => {
    let alive = true; let pending = false; let controller: AbortController | null = null;
    async function refresh() {
      if (pending || document.hidden) return;
      pending = true; controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 10000);
      try {
        const results = await Promise.allSettled(channels.map(async channel => {
          const response = await fetch(`/api/studio/assistant/${channel.key}/status`, { cache: "no-store", signal: controller!.signal });
          if (!response.ok) throw new Error("Connection status unavailable");
          return await response.json() as Status;
        }));
        if (alive) setStatus(results.map((result, i) => {
          let state: Channel["state"] = "unknown";
          if (result.status === "fulfilled") {
            const value = result.value;
            state = !value.enabled ? "disconnected" : !value.bound ? "unbound"
              : (i === 0 ? value.online : value.receiverOnline && ["awaiting_event", "receiving"].includes(value.receiverState ?? "")) ? "online" : "offline";
          }
          return { name: channels[i].name, state };
        }));
      } finally { clearTimeout(timeout); pending = false; }
    }
    void refresh();
    const interval = setInterval(() => void refresh(), 15000);
    window.addEventListener("focus", refresh); document.addEventListener("visibilitychange", refresh);
    return () => { alive = false; clearInterval(interval); controller?.abort(); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, []);
  const connected = status?.filter(channel => channel.state === "online") ?? [];
  const attention = status?.filter(channel => !["online", "disconnected"].includes(channel.state)) ?? [];
  return <div className={styles.status} aria-label="助理通道连接状态" aria-live="polite">
    <span>{!status ? "正在读取连接状态" : connected.length ? "已连接：" : attention.length ? "连接状态：" : "未连接"}</span>
    {connected.map(channel => <span className={styles.connected} key={channel.name}><i aria-hidden="true" />{channel.name}</span>)}
    {attention.map(channel => <span className={styles.attention} key={channel.name}>{channel.name}{labels[channel.state]}</span>)}
    <Link className={styles.configure} href="/settings#assistant" aria-label="配置助理通道" title="助理连接设置">
      {connected.length ? <ProductIcon name="settings" /> : "配置"}
    </Link>
  </div>;
}
