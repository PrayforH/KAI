"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { VideoAspectRatio, VideoGenerationSettings } from "./video-generation";
import styles from "./video-generation-controls.module.css";

const RATIOS: VideoAspectRatio[] = ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"];
type Panel = "format" | "duration";

function Icon({ kind }: { kind: "clock" | "frame" | "chevron" }) {
  const paths = {
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    frame: <rect x="3" y="6" width="18" height="12" rx="3" />,
    chevron: <path d="m8 10 4 4 4-4" />,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{paths[kind]}</svg>;
}

export function VideoSettingsToolbar({ routeId, official, referenceCount, disabled, settings, onChange, renderControls }: {
  routeId?: string; official: boolean; referenceCount: number; disabled: boolean;
  settings: VideoGenerationSettings; onChange: (settings: VideoGenerationSettings) => void;
  renderControls: (controls: { format: ReactNode; duration: ReactNode }) => ReactNode;
}) {
  const [panel, setPanel] = useState<Panel | null>(null);
  const [position, setPosition] = useState({ left: 12, top: 12 });
  const toolbar = useRef<HTMLDivElement>(null);
  const popover = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const id = useId();
  const resolution = settings.resolution ?? "768P";
  const seconds = official ? (resolution === "1080P" ? 6 : settings.seconds === 10 ? 10 : 6) : settings.seconds;
  const followsReference = referenceCount > 0;
  const ratio = followsReference ? "跟随首图" : official ? "16:9" : settings.aspectRatio;
  const maxSeconds = official ? (resolution === "1080P" ? 6 : 10) : 15;
  const minSeconds = official ? 6 : 4;
  const close = (restoreFocus = false) => { setPanel(null); if (restoreFocus) trigger.current?.focus(); };

  useEffect(() => { setPanel(null); }, [routeId, official, disabled]);
  useLayoutEffect(() => {
    if (!panel || !trigger.current || !popover.current) return;
    const anchor = trigger.current.getBoundingClientRect();
    const bounds = popover.current.getBoundingClientRect();
    const left = Math.max(12, Math.min(anchor.left, window.innerWidth - bounds.width - 12));
    const above = anchor.top - bounds.height - 10;
    setPosition({ left, top: above >= 12 ? above : Math.max(12, Math.min(anchor.bottom + 10, window.innerHeight - bounds.height - 12)) });
    popover.current.focus();
  }, [panel]);
  useEffect(() => {
    if (!panel) return;
    const outside = (event: PointerEvent) => {
      if (!popover.current?.contains(event.target as Node) && !toolbar.current?.contains(event.target as Node)) setPanel(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setPanel(null); trigger.current?.focus(); }
    };
    const resize = () => setPanel(null);
    const scroll = (event: Event) => { if (!popover.current?.contains(event.target as Node)) setPanel(null); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("resize", resize);
    document.addEventListener("scroll", scroll, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); window.removeEventListener("resize", resize); document.removeEventListener("scroll", scroll, true); };
  }, [panel]);

  function button(name: Panel, title: string, icon: "clock" | "frame", content: ReactNode) {
    return <button type="button" className={styles.trigger} aria-label={title} aria-expanded={panel === name}
      aria-controls={panel === name ? id : undefined} aria-haspopup="dialog" disabled={disabled}
      onClick={event => { trigger.current = event.currentTarget; setPanel(panel === name ? null : name); }}>
      <Icon kind={icon} />{content}<Icon kind="chevron" />
    </button>;
  }

  return <div className={styles.anchor} ref={toolbar}>
    {renderControls({
      format: button("format", "画幅与分辨率", "frame", <><span>{ratio}</span>{official && <><span className={styles.dot}>·</span><span>{resolution}</span></>}</>),
      duration: button("duration", "视频时长", "clock", `${seconds} 秒`),
    })}
    {panel && !disabled && createPortal(<div ref={popover} id={id} role="dialog" tabIndex={-1}
      aria-label={{ format: "画幅与分辨率", duration: "视频时长" }[panel]}
      className={styles.popover} style={{ left: position.left, top: position.top }}
      onBlur={event => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node) && !toolbar.current?.contains(event.relatedTarget as Node)) close(); }}>
      <div className={styles.heading}><span>{{ format: "画幅与清晰度", duration: "视频时长" }[panel]}</span>
        <button type="button" className={styles.close} aria-label="关闭视频设置" onClick={() => close(true)}>×</button></div>
      {panel === "format" && <>
        <fieldset className={styles.fieldset}><legend>画面比例</legend>
          {followsReference || official ? <div className={styles.reference}><Icon kind="frame" /><span>{followsReference ? "跟随首张参考图" : "16:9 横屏"}<small>{followsReference ? "输出画幅由图片比例决定" : "添加首帧图片可改变画幅"}</small></span></div> : <div className={styles.ratios}>
            {RATIOS.map(value => <button key={value} type="button" aria-label={`画幅 ${value}`} aria-pressed={settings.aspectRatio === value}
              onClick={() => onChange({ ...settings, aspectRatio: value })}>
              <span className={styles.ratioIcon} data-ratio={value} /><span>{value}</span>
            </button>)}
          </div>}
        </fieldset>
        {official && <fieldset className={styles.fieldset}><legend>分辨率</legend><div className={styles.segments}>
          {(["768P", "1080P"] as const).map(value => <button key={value} type="button" aria-pressed={resolution === value}
            onClick={() => onChange({ ...settings, resolution: value, seconds: value === "1080P" ? 6 : seconds })}>{value}<small>{value === "768P" ? "标准" : "高清"}</small></button>)}
        </div><p className={styles.note}>1080P 支持 6 秒视频。</p></fieldset>}
      </>}
      {panel === "duration" && <>
        {official ? <div className={styles.segments}>{[6, 10].map(value => <button type="button" key={value} disabled={value > maxSeconds}
          aria-pressed={seconds === value} onClick={() => onChange({ ...settings, seconds: value })}>{value}<small>秒</small></button>)}</div> :
          <div className={styles.duration}><div className={styles.slider}><input aria-label="视频时长滑块" type="range" min={minSeconds} max={maxSeconds} step={1}
            value={seconds} style={{ "--range-fill": `${((seconds - minSeconds) / (maxSeconds - minSeconds)) * 100}%` } as CSSProperties} onChange={event => onChange({ ...settings, seconds: Number(event.target.value) })} />
            <div className={styles.ticks}><span>4s</span><span>10s</span><span>15s</span></div></div>
            <label className={styles.number}><input aria-label="视频时长秒数" type="number" min={minSeconds} max={maxSeconds} step={1} value={seconds}
              onChange={event => { if (event.target.value) onChange({ ...settings, seconds: Math.max(minSeconds, Math.min(maxSeconds, Math.round(Number(event.target.value)))) }); }} /><span>秒</span></label>
          </div>}
        <p className={styles.note}>{official ? resolution === "1080P" ? "当前为高清画质，切换到 768P 可选择 10 秒。" : "时长越长，生成所需时间和消耗通常越多。" : "支持 4–15 秒，生成后可随时查看进度。"}</p>
      </>}
    </div>, document.body)}
  </div>;
}
