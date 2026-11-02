"use client";

import { useState } from "react";
import { assistantRequest } from "../lib/assistant-client";
import styles from "./create-bot-dialog.module.css";

import { BOT_COLORS, BOT_SHAPES } from "../lib/bot-appearance";
import { BotGlyph } from "./bot-glyph";
export { BOT_COLORS, BOT_SHAPES } from "../lib/bot-appearance";

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

  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-label="新建 Bot">
      <div className={styles.panel}>
        <button type="button" className={styles.close} aria-label="关闭" onClick={onClose}>
          ×
        </button>
        <div className={styles.avatar} style={{ background: `${color}22` }}>
          <BotGlyph shape={shape} color={color} />
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
              <BotGlyph shape={item.id} color={item.id === shape ? color : "#5b5b5b"} />
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
