"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { BOT_SHAPES, botAppearanceTokens, botAppearanceVars } from "../lib/bot-appearance";
import { useAppearancePalette } from "../lib/bot-appearance-palette";
import { BotGlyph } from "./bot-glyph";
import styles from "./bot-edit-dialog.module.css";

/**
 * 助理资料：外观（色 + 形）、姓名、标题（可选）、描述。
 *
 * 页头点名称与侧栏「···」里的「编辑资料」打开的是同一个弹窗，字段完全一致。
 * 执行方式（交办、试运行）与权限（本地能力、凭据）不在资料表单里：
 * 它们分别有主会话内联卡与资料库的入口，不混进来。
 */
export function BotProfileDialog({
  specialistId,
  onClose,
  onSaved,
}: {
  specialistId: string;
  onClose: () => void;
  onSaved: (updated: Specialist) => void;
}) {
  const [detail, setDetail] = useState<Specialist | null>(null);
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState<string>("");
  const [avatar, setAvatar] = useState<string>("blob");
  const palette = useAppearancePalette();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void assistantRequest<{ specialist: Specialist }>(
      `specialists/${encodeURIComponent(specialistId)}`,
    )
      .then(data => {
        if (!alive) return;
        setDetail(data.specialist);
        setName(data.specialist.name);
        setTitle(String((data.specialist.config as { title?: string })?.title ?? ""));
        setDescription(data.specialist.description ?? "");
        const appearance = (data.specialist.config as {
          appearance?: { color?: string; avatar?: string };
        })?.appearance;
        // 老 Bot 可能没存过外观：先给默认值，保存时由服务端继续接管。
        setColor(appearance?.color ?? "#126fd4");
        if (appearance?.avatar) setAvatar(appearance.avatar);
      })
      .catch(cause => {
        if (alive) setError(cause instanceof Error ? cause.message : "读取失败");
      });
    return () => {
      alive = false;
    };
  }, [specialistId]);

  async function save() {
    if (!detail || busy) return;
    setBusy(true);
    setError("");
    try {
      const updated = await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/profile`,
        "POST",
        {
          name: name.trim() || detail.name,
          title: title.trim(),
          description,
          appearance: { color, avatar },
          expectedRevision: detail.revision,
        },
      );
      onSaved(updated);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
      setBusy(false);
    }
  }

  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-label="编辑助理资料">
      <div className={styles.panel}>
        <button type="button" className={styles.close} aria-label="关闭" onClick={onClose}>
          ×
        </button>
        <div className={styles.appearance}>
          <div
            className={styles.avatarPreview}
            style={{ background: `${color}22`, ...botAppearanceVars(color) } as React.CSSProperties}
          >
            <svg viewBox="0 0 64 64" aria-hidden="true">
              <path
                d={(BOT_SHAPES.find(item => item.id === avatar) ?? BOT_SHAPES[0]).path}
                fill={botAppearanceTokens(color).accent}
              />
            </svg>
          </div>
          <div className={styles.colors}>
            {(color && !palette.colors.includes(color) ? [color, ...palette.colors] : palette.colors).map(item => (
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
            {BOT_SHAPES.map(item => (
              <button
                key={item.id}
                type="button"
                aria-label={`形状 ${item.id}`}
                data-selected={item.id === avatar ? "true" : undefined}
                className={styles.shapeButton}
                onClick={() => setAvatar(item.id)}
              >
                <BotGlyph
                  shape={item.id}
                  color={item.id === avatar ? botAppearanceTokens(color).accent : "#5b5b5b"}
                />
              </button>
            ))}
          </div>
        </div>
        <label className={styles.field}>
          <span>姓名</span>
          <input value={name} maxLength={80} aria-label="姓名" onChange={event => setName(event.target.value)} />
        </label>
        <label className={styles.field}>
          <span>标题（可选）</span>
          <input
            value={title}
            maxLength={80}
            placeholder="研究、营销、管理"
            aria-label="标题"
            onChange={event => setTitle(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span>描述</span>
          <textarea
            value={description}
            rows={6}
            maxLength={2000}
            aria-label="描述"
            onChange={event => setDescription(event.target.value)}
          />
        </label>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <button type="button" className={styles.primary} disabled={busy || !detail} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}
