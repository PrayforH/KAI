"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { PcmRecorder } from "../lib/dictation-audio";
import { dictationRequest, DictationStream } from "../lib/dictation-client";
import styles from "./voice-input.module.css";

type Phase = "idle" | "starting" | "recording" | "finishing";

export function VoiceInput({ disabled, modelRoute, onInsert, onActive, onDraft }: {
  disabled: boolean; modelRoute: string; onInsert: (text: string) => void; onActive: (active: boolean) => void;
  onDraft?: (text: string) => void;
}) {
  const [enabled, setEnabled] = useState(false);
  const mic = useRef<HTMLButtonElement | null>(null);
  const [controlsHost, setControlsHost] = useState<Element | null>(null);
  const [controlsHeight, setControlsHeight] = useState(32);
  const [levels, setLevels] = useState<number[]>(Array(120).fill(0.08));
  const [phase, setPhase] = useState<Phase>("idle");
  const generation = useRef(0); const draftRef = useRef("");
  const finishing = useRef(false);
  const recorder = useRef<PcmRecorder | undefined>(undefined);
  const stream = useRef<DictationStream | undefined>(undefined);
  const abort = useRef<AbortController | undefined>(undefined);
  const frames = useRef<Float32Array[]>([]); const count = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const active = useRef(onActive); active.current = onActive;
  const insert = useRef(onInsert); insert.current = onInsert;
  const draft = useRef(onDraft); draft.current = onDraft;
  const finishCallback = useRef<() => Promise<void>>(async () => undefined);
  const sessionLimit = useRef(120);
  const deviceEnded = useRef(false);

  function release() {
    clearTimeout(timer.current); recorder.current?.release(); recorder.current = undefined;
    stream.current?.cancel(); stream.current = undefined;
    abort.current?.abort(); abort.current = undefined;
    frames.current = []; count.current = 0;
  }

  useEffect(() => {
    let live = true;
    void dictationRequest<{ enabled: boolean; mode: string; maxSessionSeconds: number }>("/capabilities")
      .then((caps) => { if (live) { setEnabled(caps.enabled && caps.mode === "realtime"); sessionLimit.current = caps.maxSessionSeconds; } })
      .catch(() => undefined);
    return () => { live = false; generation.current++; release(); active.current(false); };
  }, []);

  useEffect(() => {
    if (enabled) {
      const composer = mic.current?.closest(".aui-composer-root");
      setControlsHost(composer?.querySelector(".composer-footer") ?? null);
    }
  }, [enabled]);

  function flush() {
    if (!count.current) return;
    const samples = new Float32Array(count.current); let offset = 0;
    for (const frame of frames.current) { samples.set(frame, offset); offset += frame.length; }
    frames.current = []; count.current = 0;
    stream.current?.send(samples);
  }

  function cancel() {
    generation.current++; release(); setPhase("idle"); active.current(false);
  }

  function fail() {
    if (draftRef.current.trim()) insert.current(draftRef.current);
    cancel();
  }

  async function finish() {
    if (phase !== "recording" || finishing.current) return;
    finishing.current = true;
    const current = generation.current;
    setPhase("finishing"); clearTimeout(timer.current);
    try {
      await recorder.current?.stop(); flush();
      const result = await stream.current!.finish();
      if (current !== generation.current) return;
      draftRef.current = result.text;
      if (!result.text.trim()) { cancel(); return; }
      draft.current?.(result.text);
      const refined = await dictationRequest<{ text: string; status: string }>("/refine", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft: result.text, model_route: modelRoute }),
        signal: AbortSignal.any([abort.current!.signal, AbortSignal.timeout(20000)]),
      }).catch(() => ({ text: result.text, status: "fallback" }));
      if (current !== generation.current) return;
      insert.current(refined.text); cancel();
    } catch {
      if (current !== generation.current) return;
      fail();
    }
  }
  finishCallback.current = finish;

  useEffect(() => {
    if (phase === "recording" && deviceEnded.current) void finishCallback.current();
  }, [phase]);

  async function start() {
    release(); const current = ++generation.current;
    const toolbar = controlsHost?.querySelector(".composer-toolbar");
    setControlsHeight(toolbar?.getBoundingClientRect().height || 32);
    finishing.current = false; deviceEnded.current = false; setLevels(Array(120).fill(0.08));
    draftRef.current = "";
    setPhase("starting"); active.current(true); abort.current = new AbortController();
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error("麦克风需要 HTTPS 或 localhost 入口。请通过安全入口打开 KAI。");
      }
      stream.current = new DictationStream((value) => {
        if (current !== generation.current) return;
        draftRef.current = value;
        draft.current?.(value);
      }, () => {
        if (current !== generation.current) return;
        fail();
      });
      await stream.current.start();
      if (current !== generation.current) return;
      recorder.current = new PcmRecorder();
      await recorder.current.start((samples) => {
        if (current !== generation.current) return;
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        setLevels((values) => [...values.slice(1), Math.max(0.08, Math.min(1, rms * 8))]);
        frames.current.push(samples); count.current += samples.length;
        if (count.current >= 4800) flush();
      }, () => {
        if (current !== generation.current) return;
        deviceEnded.current = true;
        void finishCallback.current();
      });
      if (current !== generation.current) return;
      setPhase("recording"); timer.current = setTimeout(() => { void finishCallback.current(); }, sessionLimit.current * 1000);
    } catch {
      if (current !== generation.current) return;
      fail();
    }
  }

  if (!enabled) return null;
  const working = ["starting", "recording", "finishing"].includes(phase);
  const closeIcon = <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 6 8 8M14 6l-8 8"/></svg>;
  const controls = <div className={styles.recordingBar} data-dictation-controls="true" role="group"
    style={{ height: controlsHeight }}
    aria-label="语音输入控制" aria-busy={phase !== "recording"}
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); cancel(); } }}>
    <button type="button" className={styles.circle} aria-label="取消语音输入" title="取消" onClick={cancel}>{closeIcon}</button>
    <div className={`${styles.signal} ${phase !== "recording" ? styles.waiting : ""}`} aria-hidden="true">
      <svg viewBox="0 0 600 32" preserveAspectRatio="none">
        {levels.map((level, index) => <rect key={index} x={index * 5 + 1} y={16 - level * 14} width="2.5" height={level * 28} rx="1.25" opacity={0.25 + level * 0.65}/>)}</svg>
    </div>
    <button type="button" className={`${styles.circle} ${styles.stop}`} disabled={phase !== "recording"}
      aria-label="停止录音并整理文字" title="停止录音并整理文字" onClick={() => void finish()}>
      <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="6" y="6" width="8" height="8" rx="1.2"/></svg>
    </button>
  </div>;
  return <div className={styles.root}>
    <button ref={mic} type="button" className={styles.mic} disabled={disabled || phase !== "idle"}
      title="语音输入" aria-label="语音输入" aria-expanded={phase !== "idle"} onClick={() => void start()}>
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><rect x="7" y="2" width="6" height="10" rx="3"/><path d="M4 9v1a6 6 0 0 0 12 0V9M10 16v3M7 19h6"/></svg>
    </button>
    {working && (controlsHost ? createPortal(controls, controlsHost) : controls)}
  </div>;
}
