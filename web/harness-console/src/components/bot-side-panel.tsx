"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { assistantRequest } from "../lib/assistant-client";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import type { ArtifactDetails } from "./artifact-list";
import { AssistantComputerLink } from "./assistant-computer-link";
import { AssistantResults } from "./assistant-results";
import { BotDuties } from "./bot-duties-dialog";
import { BotRailAvatar } from "./bot-rail";
import styles from "./bot-side-panel.module.css";

type VaultEntry = { itemId: string; title: string; grants: { specialistId: string; status: string }[] };

function BotLibrary({ botId, threadId }: { botId: string; threadId: string }) {
  const [files, setFiles] = useState<ArtifactDetails[] | null>(threadId ? null : []);
  const [vault, setVault] = useState<VaultEntry[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams({ thread_id: threadId, limit: "100" });
    if (threadId) void fetch(`/api/harness/artifacts?${query}`, { cache: "no-store", signal: controller.signal })
      .then(requireAuthenticatedResponse)
      .then(async response => {
        if (!response.ok) throw new Error();
        const items = await response.json() as ArtifactDetails[];
        if (!controller.signal.aborted) setFiles(items.filter(item => item.thread_id === threadId));
      })
      .catch(() => { if (!controller.signal.aborted) setError("文件暂时无法读取"); });
    void assistantRequest<VaultEntry[]>("vault").then(items => {
      if (!controller.signal.aborted) setVault(items.filter(item => item.grants?.some(
        grant => grant.specialistId === botId && grant.status === "ok",
      )));
    }).catch(() => { if (!controller.signal.aborted) setError("资料库暂时无法读取"); });
    return () => controller.abort();
  }, [botId, threadId]);
  return <>
    <section className={styles.section} aria-label="文件">
      <h3>文件</h3>
      {files === null && !error ? <p role="status">正在读取文件…</p> : files?.length === 0 ? <p>暂无文件</p> : null}
      {files && <AssistantResults artifacts={files} />}
    </section>
    <section className={styles.section} aria-label="已授权资料">
      <div className={styles.heading}><h3>已授权资料</h3><Link href="/settings#vault">管理</Link></div>
      {vault.length > 0 ? <ul className={styles.vault}>{vault.map(item => <li key={item.itemId}>{item.title}</li>)}</ul>
        : !error && <p>暂无已授权资料</p>}
    </section>
    {error && <p role="alert">{error}</p>}
  </>;
}

function BotComputerCard({ botId, ready, onOpen }: { botId: string; ready: boolean; onOpen: () => void }) {
  const [state, setState] = useState<string | null>(null);
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    const request = { cache: "no-store", signal: controller.signal } as const;
    async function load() {
      const response = await fetch("/api/studio/assistant/computer/targets", request);
      if (!response.ok) return;
      const selection = await response.json() as { active_target: string | null; targets: { target_id: string; label: string }[] };
      if (controller.signal.aborted) return;
      if (selection.active_target !== "cloud") {
        setState(selection.targets.find(target => target.target_id === selection.active_target)?.label ?? null);
        return;
      }
      const preview = await fetch(`/api/studio/assistant/computer/desktops/preview?bot_id=${encodeURIComponent(botId)}`, request);
      if (controller.signal.aborted) return;
      if (preview.status === 404) setState("休眠中");
      else if (preview.ok) {
        const { state: value } = await preview.json() as { state: string };
        if (!controller.signal.aborted) setState(
          ({ stopped: "休眠中", created: "休眠中", starting: "正在连接", idle: "待命", busy: "运行中", failed: "暂不可用" } as Record<string, string>)[value] ?? null,
        );
      }
    }
    void load().catch(() => undefined);
    return () => controller.abort();
  }, [botId, ready]);
  return <section className={styles.section} aria-label="电脑">
    <h3>电脑</h3>
    <AssistantComputerLink open={false} ready={ready} onToggle={onOpen} className={styles.computer}>
      <span>Bot 的电脑{state && <small>{state}</small>}</span>
      <span className={styles.screen} aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="3" y="4.5" width="18" height="13" rx="2" /><path d="M8 21h8m-4-3.5V21" />
        </svg>
      </span>
    </AssistantComputerLink>
  </section>;
}

export function BotSidePanel({ botId, name, tone, shape, threadId, ready, onClose, onDetails, onDuties, onComputer }: {
  botId: string; name: string; tone: string; shape?: string; threadId: string; ready: boolean;
  onClose: () => void; onDetails: () => void; onDuties: () => void; onComputer: () => void;
}) {
  const [tab, setTab] = useState<"activity" | "library">("activity");
  const id = useId();
  return <aside className={styles.panel} aria-label={`${name}的侧栏`}>
    <button type="button" className={styles.close} aria-label="关闭 Bot 侧栏" onClick={onClose}>×</button>
    <header className={styles.header}>
      <BotRailAvatar tone={tone} shape={shape} />
      <button type="button" className={styles.name} aria-label={`${name}详情`} onClick={onDetails}>{name}</button>
      <div className={styles.tabs} role="tablist" aria-label="Bot 侧栏">
        {(["activity", "library"] as const).map(value => <button type="button" key={value} role="tab"
          id={`${id}-${value}`} aria-selected={tab === value} aria-controls={`${id}-panel`}
          tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)}
          onKeyDown={event => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            const next = value === "activity" ? "library" : "activity";
            setTab(next);
            document.getElementById(`${id}-${next}`)?.focus();
          }}>{value === "activity" ? "动态" : "资料库"}</button>)}
      </div>
    </header>
    <div className={styles.content} role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${tab}`}>
      {tab === "activity" ? <>
        <BotDuties specialistId={botId} onManage={onDuties} />
        <BotComputerCard botId={botId} ready={ready} onOpen={onComputer} />
      </> : <BotLibrary botId={botId} threadId={threadId} />}
    </div>
  </aside>;
}
