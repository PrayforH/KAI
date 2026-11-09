"use client";

import { useEffect, useId, useState } from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import type { ArtifactDetails } from "./artifact-list";
import { AssistantComputerLink } from "./assistant-computer-link";
import { AssistantResults } from "./assistant-results";
import { BotDuties } from "./bot-duties-dialog";
import { BotRailAvatar } from "./bot-rail";
import styles from "./bot-side-panel.module.css";

function BotLibrary({ threadId }: { threadId: string }) {
  const [files, setFiles] = useState<ArtifactDetails[] | null>(threadId ? null : []);
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
    return () => controller.abort();
  }, [threadId]);
  return <section className={styles.section} aria-label="文件">
    <h3>文件</h3>
    {files === null && !error ? <p role="status">正在读取文件…</p> : files?.length === 0 ? <p>暂无文件</p> : null}
    {files && <AssistantResults artifacts={files} />}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function BotComputerCard({ botId, ready, onOpen }: { botId?: string; ready: boolean; onOpen: () => void }) {
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
      const query = botId ? `?${new URLSearchParams({ bot_id: botId })}` : "";
      const preview = await fetch(`/api/studio/assistant/computer/desktops/preview${query}`, request);
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
      <span>{botId ? "Bot 的电脑" : "主助理的电脑"}{state && <small>{state}</small>}</span>
      <span className={styles.screen} aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="3" y="4.5" width="18" height="13" rx="2" /><path d="M8 21h8m-4-3.5V21" />
        </svg>
      </span>
    </AssistantComputerLink>
  </section>;
}

export function BotSidePanel({ botId, primary = false, name, tone, shape, threadId, ready, onClose, onDetails, onComputer }: {
  botId?: string; primary?: boolean; name: string; tone: string; shape?: string; threadId: string; ready: boolean;
  onClose: () => void; onDetails?: () => void; onComputer: () => void;
}) {
  const [tab, setTab] = useState<"activity" | "library">("activity");
  const id = useId();
  const label = primary ? "助理侧栏" : "Bot 侧栏";
  return <aside className={styles.panel} aria-label={`${name}的侧栏`}>
    <button type="button" className={styles.close} aria-label={primary ? "关闭助理侧栏" : "关闭 Bot 侧栏"} onClick={onClose}>×</button>
    <header className={styles.header}>
      <BotRailAvatar tone={tone} shape={shape} />
      {onDetails ? <button type="button" className={styles.name} aria-label={`${name}详情`} onClick={onDetails}>{name}</button>
        : <h2 className={styles.name}>{name}</h2>}
      <div className={styles.tabs} role="tablist" aria-label={label}>
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
        {botId && <BotDuties specialistId={botId} compact />}
        <BotComputerCard botId={primary ? undefined : botId} ready={ready} onOpen={onComputer} />
      </> : <BotLibrary threadId={threadId} />}
    </div>
  </aside>;
}
