"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { PcmRecorder, pcmWav } from "../lib/dictation-audio";
import { dictationRequest } from "../lib/dictation-client";
import styles from "./voice-input.module.css";

type Phase = "idle" | "starting" | "recording" | "stopping" | "transcribing" | "error";

export function VoiceInput({ disabled, onInsert, onActive, placement = "composer" }: {
  disabled: boolean; modelRoute: string; onInsert: (text: string) => void; onActive: (active: boolean) => void;
  onDraft?: (text: string) => void;
  placement?: "composer" | "inline";
}) {
  const [enabled, setEnabled] = useState(false);
  const mic = useRef<HTMLButtonElement | null>(null);
  const [controlsHost, setControlsHost] = useState<Element | null>(null);
  const [controlsHeight, setControlsHeight] = useState(32);
  const [levels, setLevels] = useState<number[]>(Array(120).fill(0.08));
  const [phase, setPhase] = useState<Phase>("idle");
  const phaseRef = useRef<Phase>("idle");
  const [error, setError] = useState("");
  const generation = useRef(0);
  const recorder = useRef<PcmRecorder | undefined>(undefined);
  const abort = useRef<AbortController | undefined>(undefined);
  const frames = useRef<Float32Array[]>([]); const count = useRef(0);
  // Retain only in component memory for an explicit retry; never persist audio.
  const recording = useRef<Blob | undefined>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const active = useRef(onActive); active.current = onActive;
  const insert = useRef(onInsert); insert.current = onInsert;
  const finishCallback = useRef<() => Promise<void>>(async () => undefined);
  const sessionLimit = useRef(120);
  const deviceEnded = useRef(false);

  function transition(next: Phase) { phaseRef.current = next; setPhase(next); }
  function release() {
    clearTimeout(timer.current); recorder.current?.release(); recorder.current = undefined;
    abort.current?.abort(); abort.current = undefined;
    frames.current = []; count.current = 0;
  }
  function cancel() {
    generation.current++; release(); recording.current = undefined;
    setError(""); transition("idle"); active.current(false);
  }

  useEffect(() => {
    let live = true;
    void dictationRequest<{ enabled: boolean; maxSessionSeconds: number }>("/capabilities")
      .then(caps => {
        if (!live) return;
        setEnabled(caps.enabled);
        sessionLimit.current = Number.isFinite(caps.maxSessionSeconds)
          ? Math.max(10, Math.min(300, caps.maxSessionSeconds)) : 120;
      }).catch(() => undefined);
    return () => { live = false; generation.current++; release(); recording.current = undefined; active.current(false); };
  }, []);

  useEffect(() => {
    if (enabled && placement === "composer") {
      const composer = mic.current?.closest(".aui-composer-root");
      setControlsHost(composer?.querySelector(".composer-footer") ?? null);
    }
  }, [enabled, placement]);

  function fail(cause: unknown) {
    release();
    const message = cause instanceof DOMException && cause.name === "NotAllowedError"
      ? "请允许使用麦克风后重新录音。"
      : cause instanceof Error ? cause.message : "转写失败，请重试。";
    setError(message); transition("error");
  }

  async function transcribe() {
    if (!recording.current || phaseRef.current === "transcribing") return;
    const current = generation.current;
    transition("transcribing"); setError(""); abort.current = new AbortController();
    const body = new FormData(); body.append("file", recording.current, "dictation.wav");
    try {
      const result = await dictationRequest<{ text: string }>("/transcribe", {
        method: "POST", body,
        signal: AbortSignal.any([abort.current.signal, AbortSignal.timeout(90000)]),
      });
      if (current !== generation.current) return;
      if (typeof result.text !== "string") throw new Error("语音服务返回了无效文字，请重试。");
      if (!result.text.trim()) {
        recording.current = undefined;
        throw new Error("没有识别到语音，请重新录音。");
      }
      // One atomic insertion. Dictation does not submit a task or rewrite it
      // through the selected chat model after speech recognition.
      insert.current(result.text.trim()); cancel();
    } catch (cause) {
      if (current === generation.current) fail(cause);
    }
  }

  async function finish() {
    if (phaseRef.current !== "recording") return;
    const current = generation.current;
    // Lock synchronously against double-clicks and the duration/device timers.
    transition("stopping"); clearTimeout(timer.current);
    try {
      await recorder.current?.stop(); recorder.current = undefined;
      if (current !== generation.current) return;
      if (count.current < 16000) throw new Error("录音太短，请至少录制一秒后重试。");
      const samples = new Float32Array(count.current); let offset = 0;
      for (const frame of frames.current) { samples.set(frame, offset); offset += frame.length; }
      recording.current = pcmWav(samples); frames.current = []; count.current = 0;
      await transcribe();
    } catch (cause) {
      if (current === generation.current) fail(cause);
    }
  }
  finishCallback.current = finish;

  async function start() {
    if (disabled || !["idle", "error"].includes(phaseRef.current)) return;
    release(); recording.current = undefined; const current = ++generation.current;
    const toolbar = controlsHost?.querySelector(".composer-toolbar");
    setControlsHeight(toolbar?.getBoundingClientRect().height || 32);
    deviceEnded.current = false; setLevels(Array(120).fill(0.08)); setError("");
    transition("starting"); active.current(true);
    const capture = new PcmRecorder(); recorder.current = capture;
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error("麦克风需要 HTTPS 或 localhost，请从 HTTPS 入口打开。");
      }
      await capture.start(samples => {
        if (current !== generation.current) return;
        const remaining = sessionLimit.current * 16000 - count.current;
        const frame = samples.slice(0, Math.max(0, remaining));
        if (frame.length) {
          const rms = Math.sqrt(frame.reduce((sum, value) => sum + value * value, 0) / frame.length);
          setLevels(values => [...values.slice(1), Math.max(0.08, Math.min(1, rms * 8))]);
          frames.current.push(frame); count.current += frame.length;
        }
        if (count.current >= sessionLimit.current * 16000) void finishCallback.current();
      }, () => {
        if (current !== generation.current) return;
        deviceEnded.current = true; void finishCallback.current();
      });
      if (current !== generation.current) { capture.release(); return; }
      transition("recording");
      timer.current = setTimeout(() => { void finishCallback.current(); }, sessionLimit.current * 1000);
      if (deviceEnded.current || count.current >= sessionLimit.current * 16000) void finishCallback.current();
    } catch (cause) {
      if (current === generation.current) fail(cause);
    }
  }

  if (!enabled) return null;
  const working = phase !== "idle";
  const controls = <div className={styles.recordingBar} data-dictation-controls="true" data-phase={phase} role="group"
    style={{ minHeight: controlsHeight }} aria-label="语音输入控制" aria-busy={["starting", "stopping", "transcribing"].includes(phase)}
    onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); cancel(); } }}>
    <button type="button" className={styles.circle} aria-label="取消语音输入" title="取消" onClick={cancel}>
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 6 8 8M14 6l-8 8"/></svg>
    </button>
    {phase === "recording" ? <div className={styles.signal} aria-label="正在录音" role="status">
      <svg viewBox="0 0 600 32" preserveAspectRatio="none" aria-hidden="true">
        {levels.map((level, index) => <rect key={index} x={index * 5 + 1} y={16 - level * 14} width="2.5" height={level * 28} rx="1.25" opacity={0.25 + level * 0.65}/>)}</svg>
    </div> : <div className={styles.status} role={phase === "error" ? "alert" : "status"}>
      {phase === "error" ? error : phase === "transcribing" ? "正在转写…" : phase === "stopping" ? "正在结束录音…" : "正在准备录音…"}
    </div>}
    {phase === "error" ? <button type="button" className={styles.retry} onClick={() => void (recording.current ? transcribe() : start())}>
      {recording.current ? "重试转写" : "重新录音"}
    </button> : <button type="button" className={styles.circle} disabled={phase !== "recording"}
      aria-label="完成录音并转写" title="完成录音并转写" onClick={() => void finish()}>
      {phase === "transcribing" ? <span className={styles.spinner} aria-hidden="true" />
        : <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 10 3 3 7-7"/></svg>}
    </button>}
  </div>;
  return <div className={styles.root} data-placement={placement} data-active={working}>
    <button ref={mic} type="button" className={styles.mic} disabled={disabled || working}
      title="语音输入" aria-label="语音输入" aria-expanded={working} onClick={() => void start()}>
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><rect x="7" y="2" width="6" height="10" rx="3"/><path d="M4 9v1a6 6 0 0 0 12 0V9M10 16v3M7 19h6"/></svg>
    </button>
    {working && (controlsHost ? createPortal(controls, controlsHost) : controls)}
  </div>;
}
