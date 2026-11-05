"use client";

import { useState } from "react";
import { assistantRequest } from "../lib/assistant-client";
import { useAppearancePalette } from "../lib/bot-appearance-palette";
import { BOT_SHAPES, botAppearanceTokens, botAppearanceVars } from "../lib/bot-appearance";
import styles from "./create-bot-dialog.module.css";

import { BotGlyph } from "./bot-glyph";
export { BOT_SHAPES } from "../lib/bot-appearance";

/**
 * 新建 Bot：只填名字。外观默认由服务端随机分配——
 * 从本人还没用过的颜色与图标里挑一套，建完就固化，不再重摇。
 * 想自己指定时再点色板/图标。
 */
export function CreateBotDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (specialistId: string) => void;
}) {
  const [name, setName] = useState("新 Bot");
  const palette = useAppearancePalette();
  const [manual, setManual] = useState<{ color: string; avatar: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const preview = manual ?? null;
  const tokens = botAppearanceTokens(preview?.color);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const created = await assistantRequest<{ specialistId: string }>("specialists", "POST", {
        name: name.trim() || "新 Bot",
        ...(manual ? { appearance: manual } : {}),
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
        <div
          className={styles.avatar}
          style={{ background: preview ? `${preview.color}22` : "transparent", ...(preview ? botAppearanceVars(preview.color) : {}) } as React.CSSProperties}
        >
          {preview ? (
            <BotGlyph shape={preview.avatar} color={tokens.accent} />
          ) : (
            <span className={styles.autoPreview} aria-hidden="true">?</span>
          )}
        </div>
        <input
          className={styles.name}
          value={name}
          maxLength={80}
          aria-label="Bot 名称"
          onChange={event => setName(event.target.value)}
        />
        <p className={styles.hint}>
          {manual
            ? "已指定外观"
            : "外观由服务端随机分配：挑一套你还没用过的颜色与图标，建完固定不变。"}
        </p>
        {manual && (
          <button type="button" className={styles.autoButton} onClick={() => setManual(null)}>
            改回随机
          </button>
        )}
        <div className={styles.colors}>
          {palette.colors.map(item => (
            <button
              key={item}
              type="button"
              aria-label={`颜色 ${item}`}
              data-selected={item === preview?.color ? "true" : undefined}
              className={styles.swatch}
              style={{ background: item }}
              onClick={() => setManual(current => ({
                color: item,
                avatar: current?.avatar ?? palette.avatars[0] ?? "blob",
              }))}
            />
          ))}
        </div>
        <div className={styles.shapes}>
          {BOT_SHAPES.map(item => (
            <button
              key={item.id}
              type="button"
              aria-label={`形状 ${item.id}`}
              data-selected={item.id === preview?.avatar ? "true" : undefined}
              className={styles.shapeButton}
              onClick={() => setManual(current => ({
                color: current?.color ?? palette.colors[0] ?? "#126fd4",
                avatar: item.id,
              }))}
            >
              <BotGlyph
                shape={item.id}
                color={item.id === preview?.avatar ? tokens.accent : "#5b5b5b"}
              />
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
