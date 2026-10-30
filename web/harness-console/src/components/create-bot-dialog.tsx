"use client";

import { useState } from "react";
import { assistantRequest } from "../lib/assistant-client";
import styles from "./create-bot-dialog.module.css";

// 取自客户端「新 Bot」弹窗的配色与头像形状：颜色与形状都只影响呈现。
export const BOT_COLORS = [
  "#ffffff",
  "#a9734f",
  "#ff3b30",
  "#ff6a00",
  "#ff9f0a",
  "#25c06a",
  "#0d9381",
  "#126fd4",
  "#7442d1",
  "#ff2d95",
  "#8e8e93",
  "#c82779",
] as const;

export const BOT_SHAPES = [
  { id: "blob", path: "M32 6c10 0 18 6 18 16 0 8-6 12-6 20 0 6-5 10-12 10-8 0-12-4-12-11 0-8-6-11-6-19C14 12 22 6 32 6Z" },
  { id: "drop", path: "M32 5c9 10 16 18 16 27a16 16 0 0 1-32 0c0-9 7-17 16-27Z" },
  { id: "block", path: "M18 10h28a8 8 0 0 1 8 8v28a8 8 0 0 1-8 8H18a8 8 0 0 1-8-8V18a8 8 0 0 1 8-8Z" },
  { id: "star", path: "M32 4c4 10 6 12 16 16-10 4-12 6-16 16-4-10-6-12-16-16 10-4 12-6 16-16Z" },
  { id: "cloud", path: "M18 44c-7 0-12-5-12-11 0-5 4-9 9-10 1-7 7-12 15-12s14 5 15 12c5 1 9 5 9 10 0 6-5 11-12 11Z" },
  { id: "pebble", path: "M34 6c11 2 18 11 16 22s-9 20-20 20S8 40 8 28 21 4 34 6Z" },
] as const;

/**
 * 新建 Bot：名称 + 颜色 + 形状，对齐客户端的弹窗。
 * 创建后直接进入这个 bot 的会话，而不是先掉进一张配置表单。
 */
export function CreateBotDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (specialistId: string) => void;
}) {
  const [name, setName] = useState("新 Bot");
  const [color, setColor] = useState<string>(BOT_COLORS[7]);
  const [shape, setShape] = useState<string>(BOT_SHAPES[0].id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function create() {
    setBusy(true);
    setError("");
    try {
      const created = await assistantRequest<{ specialistId: string }>("specialists", "POST", {
        name: name.trim() || "新 Bot",
        appearance: { color, avatar: shape },
      });
      onCreated(created.specialistId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建失败");
      setBusy(false);
    }
  }

  const glyph = BOT_SHAPES.find((item) => item.id === shape) ?? BOT_SHAPES[0];
  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-label="新建 Bot">
      <div className={styles.panel}>
        <button type="button" className={styles.close} aria-label="关闭" onClick={onClose}>
          ×
        </button>
        <div className={styles.avatar} style={{ background: `${color}22` }}>
          <svg viewBox="0 0 64 64" aria-hidden="true">
            <path d={glyph.path} fill={color} />
          </svg>
        </div>
        <input
          className={styles.name}
          value={name}
          maxLength={80}
          aria-label="Bot 名称"
          onChange={(event) => setName(event.target.value)}
        />
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
              data-selected={item.id === shape ? "true" : undefined}
              className={styles.shapeButton}
              onClick={() => setShape(item.id)}
            >
              <svg viewBox="0 0 64 64" aria-hidden="true">
                <path d={item.path} fill={item.id === shape ? color : "#5b5b5b"} />
              </svg>
            </button>
          ))}
        </div>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <button type="button" className={styles.primary} disabled={busy} onClick={() => void create()}>
          {busy ? "正在创建…" : "创建 Bot"}
        </button>
      </div>
    </div>
  );
}
