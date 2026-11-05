"use client";

import { useState } from "react";
import { assistantRequest } from "../lib/assistant-client";
import styles from "./bot-edit-dialog.module.css";

export type BotDuty = {
  objectiveId: string;
  title: string;
  status: string;
  revision: number;
  schedule: { intervalMinutes: number; timezone: string } | null;
};

/** Trigger edits update one owned Objective with CAS; they never start a Run. */
export function DutyScheduleControl({ duty, onSaved }: {
  duty: BotDuty;
  onSaved: (duty: BotDuty) => void;
}) {
  const stored = duty.schedule ? String(duty.schedule.intervalMinutes) : "";
  const [minutes, setMinutes] = useState(stored);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const choices = [...new Set([60, 360, 1440, ...(duty.schedule ? [duty.schedule.intervalMinutes] : [])])];
  async function save() {
    if (busy || !duty.revision) return;
    setBusy(true); setError(""); setSaved(false);
    try {
      const updated = await assistantRequest<BotDuty>(
        `objectives/${encodeURIComponent(duty.objectiveId)}/schedule`, "PATCH", {
          expectedRevision: duty.revision,
          schedule: minutes ? {
            intervalMinutes: Number(minutes), timezone: duty.schedule?.timezone || "Asia/Shanghai",
          } : null,
        },
      );
      onSaved(updated); setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "执行规则未保存");
    } finally { setBusy(false); }
  }
  return <div className={styles.dutyTrigger}>
    <label>
      <span>执行方式</span>
      <select aria-label={`${duty.title}的执行方式`} value={minutes} disabled={busy}
        onChange={event => { setMinutes(event.target.value); setSaved(false); }}>
        <option value="">按需交办</option>
        {choices.map(value => <option key={value} value={value}>每 {value} 分钟</option>)}
      </select>
    </label>
    <small>只影响这项任务；保存规则不会立即重跑。</small>
    <button type="button" disabled={busy || minutes === stored || !duty.revision}
      onClick={() => void save()}>{busy ? "正在保存…" : "保存执行方式"}</button>
    {error && <p role="alert">{error}</p>}
    {saved && <p role="status">执行方式已保存</p>}
  </div>;
}
