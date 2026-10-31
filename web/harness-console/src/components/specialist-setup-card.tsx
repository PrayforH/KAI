"use client";

import { useCallback, useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";

const FREQUENCIES = [
  { minutes: 60, label: "每小时" },
  { minutes: 360, label: "每 6 小时" },
  { minutes: 1440, label: "每天" },
] as const;

type Detail = {
  specialist: Specialist;
  threadId: string | null;
  primaryObjectiveId: string | null;
};

const SETUP_STATUSES = new Set(["draft", "needs_input", "validating", "ready", "blocked"]);

/**
 * 转交后缺配置的补齐卡（/assistant 子助理会话内）：
 * 检查频率 + 通知渠道 + 「保存并启用」→ 配置落库、状态置为 active、
 * 首个执行入队；成功后卡片变成「已启用，首次执行排队中」并刷新 bot 状态。
 */
export function SpecialistSetupCard({
  specialistId,
  onReady,
}: {
  specialistId: string;
  onReady?: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [frequency, setFrequency] = useState<number>(1440);
  const [busy, setBusy] = useState(false);
  // 只在本次点击后显示「已启用，首次执行排队中」；刷新后 active 的 bot 直接不渲染，
  // 避免把一次性的成功回执变成常驻横幅（那时首跑可能早跑完了）。
  const [justEnabled, setJustEnabled] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await assistantRequest<Detail>(
        `specialists/${encodeURIComponent(specialistId)}`,
      );
      setDetail(data);
      const stored = (data.specialist.config as { schedule_minutes?: number })
        .schedule_minutes;
      if (typeof stored === "number") setFrequency(stored);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取子助理配置失败");
    }
  }, [specialistId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveAndEnable() {
    if (!detail || busy) return;
    setBusy(true);
    setError("");
    try {
      // 1) 落配置：schedule_minutes + notify_channel（缺一不可）。
      const configured = await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/config`,
        "POST",
        {
          config: {
            ...(detail.specialist.config as Record<string, unknown>),
            schedule_minutes: frequency,
            notify_channel: "inbox",
          },
          expectedRevision: detail.specialist.revision,
        },
      );
      // 2) 启用：服务端会为首个还没有执行的职责补一次入队。
      await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/status`,
        "POST",
        { status: "active", expectedRevision: configured.revision },
      );
      setJustEnabled(true);
      await load();
      onReady?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  if (!detail) {
    return error ? (
      <div className="specialist-setup-card" role="alert">
        <p className="specialist-setup-error">{error}</p>
      </div>
    ) : null;
  }

  if (justEnabled) {
    return (
      <div className="specialist-setup-card" data-state="enabled">
        <p className="specialist-setup-done">已启用，首次执行排队中</p>
        <p className="specialist-setup-hint">
          结果会出现在这条会话里；也可以在「…」里随时改频率或暂停。
        </p>
      </div>
    );
  }

  const status = detail.specialist.status;
  if (!SETUP_STATUSES.has(status)) return null;
  if (!detail.primaryObjectiveId) {
    return (
      <div className="specialist-setup-card" data-state="no-duty">
        <p className="specialist-setup-title">这个 Bot 还没有职责</p>
        <p className="specialist-setup-hint">
          先让主助理给它派一件事，再回来设置频率并启用。
        </p>
      </div>
    );
  }

  return (
    <div className="specialist-setup-card" role="group" aria-label="补齐子助理配置">
      <p className="specialist-setup-title">还差两步就能开始跑</p>
      <p className="specialist-setup-hint">
        交办已收下，但缺少检查频率和通知渠道；补上并启用后，第一次执行会立刻入队。
      </p>
      <div className="specialist-setup-field">
        <span>检查频率</span>
        <div className="specialist-setup-options" role="radiogroup" aria-label="检查频率">
          {FREQUENCIES.map((option) => (
            <button
              key={option.minutes}
              type="button"
              role="radio"
              aria-checked={frequency === option.minutes}
              data-selected={frequency === option.minutes ? "true" : undefined}
              onClick={() => setFrequency(option.minutes)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <div className="specialist-setup-field">
        <span>通知渠道</span>
        <div className="specialist-setup-options">
          <button type="button" aria-checked="true" role="radio" data-selected="true" disabled>
            站内消息
          </button>
        </div>
      </div>
      {error && <p className="specialist-setup-error" role="alert">{error}</p>}
      <button
        type="button"
        className="specialist-setup-submit"
        disabled={busy}
        onClick={() => void saveAndEnable()}
      >
        {busy ? "正在保存…" : "保存并启用"}
      </button>
    </div>
  );
}
