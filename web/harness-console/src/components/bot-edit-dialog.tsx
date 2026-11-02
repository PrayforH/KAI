"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { BOT_COLORS, BOT_SHAPES } from "./create-bot-dialog";
import { DutyScheduleControl, type BotDuty } from "./duty-schedule-control";
import { LocalToolPermissions } from "./local-tool-permissions";
import { SpecialistCredentials } from "./specialist-credentials";
import styles from "./bot-edit-dialog.module.css";

type Detail = {
  specialist: Specialist;
  duties: BotDuty[];
};

const STATUS_TEXT: Record<string, string> = {
  active: "已激活",
  paused: "已暂停",
  needs_input: "待补充",
  blocked: "受阻",
  ready: "待启用",
};

function scheduleCopy(duty: BotDuty): string {
  const minutes = duty.schedule?.intervalMinutes;
  return minutes ? `每 ${minutes} 分钟` : "按需触发";
}

/**
 * 点开 Bot 的「…」：姓名/标题/描述 + 例行程序，对齐客户端的编辑弹窗。
 * 只改呈现信息，不碰配置状态机。
 */
export function BotEditDialog({
  specialistId,
  onClose,
  onSaved,
}: {
  specialistId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  // 后端的 routinePausedReason 在 Specialist 顶层（与 lastViewedAt 一起），不在 config 里。
  const paused = detail?.specialist.routinePausedReason?.reason === "unviewed";
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState<string>(BOT_COLORS[7]);
  const [avatar, setAvatar] = useState<string>("blob");
  const [busy, setBusy] = useState(false);
  const [trialBusy, setTrialBusy] = useState(false);
  const [trialMessage, setTrialMessage] = useState("");
  const [trialFailed, setTrialFailed] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [statusFailed, setStatusFailed] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void assistantRequest<Detail>(`specialists/${encodeURIComponent(specialistId)}`)
      .then((data) => {
        if (!alive) return;
        setDetail(data);
        setName(data.specialist.name);
        setTitle(String((data.specialist.config as { title?: string })?.title ?? ""));
        setDescription(data.specialist.description ?? "");
        const appearance = (data.specialist.config as { appearance?: { color?: string; avatar?: string } })
          ?.appearance;
        if (appearance?.color) setColor(appearance.color);
        if (appearance?.avatar) setAvatar(appearance.avatar);
      })
      .catch((cause) => {
        if (alive) setError(cause instanceof Error ? cause.message : "读取失败");
      });
    return () => {
      alive = false;
    };
  }, [specialistId]);

  async function save() {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      await assistantRequest(`specialists/${encodeURIComponent(specialistId)}/profile`, "POST", {
        name: name.trim() || detail.specialist.name,
        title,
        description,
        appearance: { color, avatar },
        expectedRevision: detail.specialist.revision,
      });
      onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
      setBusy(false);
    }
  }

  async function changeStatus(next: "active" | "paused") {
    if (!detail || statusBusy || trialBusy || busy) return;
    setStatusBusy(true);
    setStatusMessage("");
    setStatusFailed(false);
    try {
      const updated = await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/status`,
        "POST",
        { status: next, expectedRevision: detail.specialist.revision },
      );
      // 用服务端返回的 revision 继续后续操作，避免 CAS 冲突。
      setDetail({ ...detail, specialist: updated });
      setStatusMessage(
        next === "active"
          ? detail.specialist.status === "paused"
            ? "已恢复接单"
            : "已启用助理"
          : "已暂停",
      );
      window.dispatchEvent(
        new CustomEvent("kai:specialists-changed", {
          detail: { specialistId, reason: "status" },
        }),
      );
    } catch (cause) {
      setStatusFailed(true);
      setStatusMessage(cause instanceof Error ? cause.message : "状态更新失败");
    } finally {
      setStatusBusy(false);
    }
  }

  async function runTrial() {
    if (!detail || trialBusy) return;
    setTrialBusy(true);
    setTrialMessage("");
    setTrialFailed(false);
    try {
      const trial = await assistantRequest<{ executionId: string; scheduled: string }>(
        `specialists/${encodeURIComponent(specialistId)}/trial`,
        "POST",
        { expectedRevision: detail.specialist.revision },
      );
      setTrialMessage(`${trial.scheduled}（执行 ${trial.executionId}）`);
      window.dispatchEvent(
        new CustomEvent("kai:specialists-changed", {
          detail: { specialistId, reason: "trial" },
        }),
      );
    } catch (cause) {
      setTrialFailed(true);
      setTrialMessage(cause instanceof Error ? cause.message : "试运行失败");
    } finally {
      setTrialBusy(false);
    }
  }

  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-label="编辑 Bot">
      <div className={styles.panel}>
        <button type="button" className={styles.close} aria-label="关闭" onClick={onClose}>
          ×
        </button>
        <div className={styles.appearance}>
          <div className={styles.avatarPreview} style={{ background: `${color}22` }}>
            <svg viewBox="0 0 64 64" aria-hidden="true">
              <path
                d={(BOT_SHAPES.find((item) => item.id === avatar) ?? BOT_SHAPES[0]).path}
                fill={color}
              />
            </svg>
          </div>
          <div className={styles.colors}>
            {BOT_COLORS.map((item) => (
              <button
                key={item}
                type="button"
                aria-label={`颜色 ${item}`}
                data-selected={item === color ? "true" : undefined}
                className={styles.swatch}
                style={{ background: item }}
                onClick={() => setColor(item)}
              />
            ))}
          </div>
          <div className={styles.shapes}>
            {BOT_SHAPES.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-label={`形状 ${item.id}`}
                data-selected={item.id === avatar ? "true" : undefined}
                className={styles.shapeButton}
                onClick={() => setAvatar(item.id)}
              >
                <svg viewBox="0 0 64 64" aria-hidden="true">
                  <path d={item.path} fill={item.id === avatar ? color : "#5b5b5b"} />
                </svg>
              </button>
            ))}
          </div>
        </div>
        <label className={styles.field}>
          <span>姓名</span>
          <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className={styles.field}>
          <span>标题（可选）</span>
          <input
            value={title}
            maxLength={80}
            placeholder="研究、营销、管理"
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span>描述</span>
          <textarea
            value={description}
            rows={7}
            maxLength={2000}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <section className={styles.routines}>
          <p className={styles.routinesTitle}>交办与执行方式</p>
          {detail?.duties.length ? (
            detail.duties.map((duty) => (
              <div key={duty.objectiveId}>
              <div className={styles.routine}>
                <span className={styles.routineIcon} aria-hidden="true">◷</span>
                <span className={styles.routineCopy}>
                  <strong>{duty.title}</strong>
                  <small>
                    {scheduleCopy(duty)}
                    {paused ? " · 因无人查看已暂停，打开本会话后恢复" : ""}
                  </small>
                </span>
                <span
                  className={styles.routineState}
                  data-active={!paused && duty.status === "active" ? "true" : undefined}
                  data-paused={paused ? "true" : undefined}
                >
                  {paused ? "已暂停" : STATUS_TEXT[duty.status] ?? duty.status}
                </span>
              </div>
              {!["completed", "cancelled"].includes(duty.status) && <DutyScheduleControl
                duty={duty}
                onSaved={updated => setDetail(current => current ? {
                  ...current, duties: current.duties.map(item => item.objectiveId === updated.objectiveId ? updated : item),
                } : current)}
              />}
              </div>
            ))
          ) : (
            <p className={styles.routineEmpty}>可以直接对话；有交办后，在这里为具体任务设置执行方式。</p>
          )}
        </section>
        {detail && ["ready", "active", "paused"].includes(detail.specialist.status) && (
          <section className={styles.statusActions}>
            {detail.specialist.status === "ready" && (
              <button
                type="button"
                className={styles.statusPrimary}
                data-action="enable"
                disabled={busy || trialBusy || statusBusy}
                onClick={() => void changeStatus("active")}
              >
                {statusBusy ? "正在启用…" : "启用助理"}
              </button>
            )}
            {detail.specialist.status === "active" && (
              <button
                type="button"
                className={styles.statusSecondary}
                data-action="pause"
                disabled={busy || trialBusy || statusBusy}
                onClick={() => void changeStatus("paused")}
              >
                {statusBusy ? "正在暂停…" : "暂停"}
              </button>
            )}
            {detail.specialist.status === "paused" && (
              <button
                type="button"
                className={styles.statusPrimary}
                data-action="resume"
                disabled={busy || trialBusy || statusBusy}
                onClick={() => void changeStatus("active")}
              >
                {statusBusy ? "正在恢复…" : "恢复接单"}
              </button>
            )}
            <p className={styles.statusHint}>
              {detail.specialist.status === "ready"
                ? "启用后可接收交办；是否定时由每项任务的执行方式决定。"
                : detail.specialist.status === "paused"
                  ? "已暂停接单；恢复不会重跑以前的交办。"
                  : "暂停后不再派发新执行；已有运行按原任务状态收尾。"}
            </p>
          </section>
        )}
        {statusMessage && (
          <p
            className={statusFailed ? styles.trialError : styles.trialResult}
            role={statusFailed ? "alert" : "status"}
          >
            {statusMessage}
          </p>
        )}
        {detail && detail.duties.length > 0 && ["ready", "active"].includes(detail.specialist.status) && (
          <section className={styles.trial}>
            <button
              type="button"
              className={styles.trialButton}
              disabled={busy || trialBusy}
              onClick={() => void runTrial()}
            >
              {trialBusy ? "正在试运行…" : "首次试运行"}
            </button>
            <p className={styles.trialHint}>
              只安排一次执行；不会创建或修改周期规则。
            </p>
            {trialMessage && (
              <p
                className={trialFailed ? styles.trialError : styles.trialResult}
                role={trialFailed ? "alert" : "status"}
              >
                {trialMessage}
              </p>
            )}
          </section>
        )}
        {detail && (
          <LocalToolPermissions
            specialistId={specialistId}
            specialist={detail.specialist}
            onUpdated={(updated) => setDetail({ ...detail, specialist: updated })}
          />
        )}
        {detail && (
          <SpecialistCredentials
            specialistId={specialistId}
            specialist={detail.specialist}
            onUpdated={(updated) => setDetail({ ...detail, specialist: updated })}
          />
        )}
        {error && <p className={styles.error} role="alert">{error}</p>}
        <button type="button" className={styles.primary} disabled={busy || !detail} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}
