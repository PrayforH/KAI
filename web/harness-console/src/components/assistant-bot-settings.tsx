"use client";

import { useCallback, useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { botSidebar } from "../lib/bot-sidebar";
import { forgetClientReadPrefix } from "../lib/client-read-cache";
import { BotRailAvatar, specialistAppearance } from "./bot-rail";
import { useConfirmationDialog } from "./confirmation-dialog";
import styles from "./assistant-bot-settings.module.css";

/** Organization lives here; the chat rail only contains available contacts. */
export function AssistantBotSettings() {
  const [bots, setBots] = useState<Specialist[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();
  const load = useCallback(async () => {
    try { setBots(await assistantRequest<Specialist[]>("specialists")); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "暂时无法读取机器人"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function update(bot: Specialist, remove = false) {
    if (busy) return;
    if (remove && !await requestConfirmation({
      title: `删除“${bot.name}”？`, confirmLabel: "删除机器人", tone: "danger",
      description: "从机器人列表中移除，并停止它的后续职责。已有聊天、任务记录和你的资料库保留。",
    })) return;
    setBusy(bot.specialistId); setError("");
    try {
      const path = `specialists/${encodeURIComponent(bot.specialistId)}`;
      let current = (await assistantRequest<{ specialist: Specialist }>(path)).specialist;
      if (remove) {
        if (current.name !== bot.name) throw new Error("机器人资料已更改，请重新确认删除");
        if (current.status !== "archived") current = await assistantRequest<Specialist>(`${path}/status`, "POST", {
          status: "archived", expectedRevision: current.revision,
        });
        await assistantRequest(path, "DELETE", { expectedRevision: current.revision, confirmName: current.name });
      } else {
        if (current.status === "archived") current = await assistantRequest<Specialist>(`${path}/status`, "POST", {
          status: "paused", expectedRevision: current.revision,
        });
        await assistantRequest(`${path}/profile`, "POST", {
          sidebar: { ...botSidebar(current), hidden: false }, expectedRevision: current.revision,
        });
      }
      forgetClientReadPrefix("assistant-detail:");
      window.dispatchEvent(new CustomEvent("kai:specialists-changed"));
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "操作未完成，请重试"); }
    finally { setBusy(null); }
  }

  const groups = [
    { title: "已归档", items: bots.filter(bot => bot.status === "archived") },
    { title: "已隐藏", items: bots.filter(bot => bot.status !== "archived" && botSidebar(bot).hidden) },
  ];
  return <div className={styles.settings}>
    <p className={styles.hint}>归档和隐藏的机器人统一在这里管理。恢复归档机器人后保持暂停，由你决定何时启用。</p>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {groups.map(group => <section key={group.title} aria-label={`${group.title}的机器人`}>
      <h3>{group.title}<span>{group.items.length}</span></h3>
      {!loading && !group.items.length && <p className={styles.empty}>暂无{group.title}的机器人</p>}
      <ul className={styles.list}>{group.items.map(bot => <li key={bot.specialistId}>
        <BotRailAvatar tone={specialistAppearance(bot).color} shape={specialistAppearance(bot).avatar} />
        <strong>{bot.name}</strong>
        <button type="button" disabled={Boolean(busy)} onClick={() => void update(bot)} aria-label={`恢复 ${bot.name}`}>恢复</button>
        <button type="button" disabled={Boolean(busy)} className={styles.danger} onClick={() => void update(bot, true)} aria-label={`删除 ${bot.name}`}>删除</button>
      </li>)}</ul>
    </section>)}
    {confirmationDialog}
  </div>;
}
