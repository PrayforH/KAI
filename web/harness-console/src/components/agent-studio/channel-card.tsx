"use client";

import type { ReactNode } from "react";
import styles from "./remote-channels.module.css";

export function ChannelCard({ name, description, logo, state, online, connected, disabled, onClick }:
  { name: string; description: string; logo: ReactNode; state: string; online: boolean;
    connected: boolean; disabled: boolean; onClick: () => void }) {
  return <article className={styles.card}>
    <div className={styles.cardTop}><div className={styles.identity}>{logo}<div>
      <h3>{name}</h3><span className={styles.state} data-online={online}>{state}</span>
    </div></div><button className={styles.action} disabled={disabled} aria-label={`${connected ? "管理" : "连接"}${name}`} onClick={onClick}>{connected ? "管理" : "连接"}</button></div>
    <p>{description}</p>
  </article>;
}
