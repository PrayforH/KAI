"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { BOT_COLORS, BOT_SHAPES } from "./create-bot-dialog";
import styles from "./bot-edit-dialog.module.css";

type Duty = {
  objectiveId: string;
  title: string;
  status: string;
  schedule: { everyMinutes?: number; scheduleMinutes?: number } | null;
};

type Detail = {
  specialist: Specialist;
  duties: Duty[];
};

const STATUS_TEXT: Record<string, string> = {
  active: "已激活",
  paused: "已暂停",
  needs_input: "待补充",
  blocked: "受阻",
  ready: "待启用",
};

function scheduleCopy(duty: Duty): string {
  const minutes = duty.schedule?.everyMinutes ?? duty.schedule?.scheduleMinutes;
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
  const paused =
    (detail?.specialist.config as { routinePausedReason?: { reason?: string } } | undefined)
      ?.routinePausedReason?.reason === "unviewed";
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState<string>(BOT_COLORS[7]);
  const [avatar, setAvatar] = useState<string>("blob");
  const [busy, setBusy] = useState(false);
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
          <p className={styles.routinesTitle}>例行程序</p>
          {detail?.duties.length ? (
            detail.duties.map((duty) => (
              <div className={styles.routine} key={duty.objectiveId}>
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
            ))
          ) : (
            <p className={styles.routineEmpty}>还没有例行程序：去主助理那儿给它派一件事。</p>
          )}
        </section>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <button type="button" className={styles.primary} disabled={busy || !detail} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}
