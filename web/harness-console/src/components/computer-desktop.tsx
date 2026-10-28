"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type RFB from "@novnc/novnc";
import styles from "./computer-desktop.module.css";

type ConnectionState = "connecting" | "connected" | "disconnected" | "unauthorized";
type ControlState = "observe" | "agent" | "human" | "takeover_pending";
const labels = { connecting: "正在连接", connected: "已连接", disconnected: "连接已断开", unauthorized: "请重新登录" };
const controlLabels: Record<ControlState, string> = {
  observe: "观察中",
  agent: "助理操作中",
  human: "由你控制",
  takeover_pending: "切换中…",
};

export function ComputerDesktop({ embedded = false, onClose }: { embedded?: boolean; onClose?: () => void }) {
  const screen = useRef<HTMLDivElement>(null);
  const shell = useRef<HTMLElement>(null);
  const client = useRef<RFB | null>(null);
  const controlRef = useRef<ControlState>("observe");
  const [state, setState] = useState<ConnectionState>("connecting");
  const [control, setControl] = useState<ControlState>("observe");
  const [revision, setRevision] = useState(0);
  const applyControl = (value: ControlState) => {
    controlRef.current = value;
    setControl(value);
    if (client.current) client.current.viewOnly = value !== "human";
  };
  useEffect(() => {
    let disposed = false;
    let rfb: RFB | null = null;
    const controller = new AbortController();
    setState("connecting");
    // Full navigations can enter bfcache without React unmounting this tree.
    const leavePage = () => { disposed = true; controller.abort(); rfb?.disconnect(); };
    const restorePage = (event: PageTransitionEvent) => { if (event.persisted) setRevision(value => value + 1); };
    window.addEventListener("pagehide", leavePage);
    window.addEventListener("pageshow", restorePage);
    async function pollControl() {
      // The lease is the authority; the viewer only mirrors it for the UI and
      // stops asking noVNC to send input when the user does not own it.
      try {
        const response = await fetch("/api/studio/assistant/computer/desktop", { cache: "no-store", signal: controller.signal });
        if (response.ok) {
          const value = (await response.json()) as { state?: ControlState };
          if (value.state && value.state !== controlRef.current) applyControl(value.state);
        }
      } catch { /* transient; the next tick retries */ }
    }
    async function connect() {
      try {
        // Refresh through the existing BFF; the socket uses HttpOnly cookies.
        const session = await fetch("/api/auth/session", { cache: "no-store", signal: controller.signal });
        if (!session.ok) { if (!disposed) setState("unauthorized"); return; }
        const status = await fetch("/api/computer/status", { cache: "no-store", signal: controller.signal });
        if (!status.ok) { if (!disposed) setState(status.status === 403 ? "unauthorized" : "disconnected"); return; }
        await pollControl();
        const { default: RemoteFramebuffer } = await import("@novnc/novnc");
        if (disposed || !screen.current) return;
        const url = new URL("/computer/desktop", window.location.href);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        rfb = new RemoteFramebuffer(screen.current, url.toString());
        client.current = rfb;
        rfb.viewOnly = controlRef.current !== "human";
        rfb.scaleViewport = true;
        rfb.resizeSession = false;
        rfb.qualityLevel = 6;
        rfb.compressionLevel = 2;
        rfb.addEventListener("connect", () => { if (!disposed) { setState("connected"); if (!embedded && controlRef.current === "human") rfb?.focus(); } });
        rfb.addEventListener("disconnect", () => { if (!disposed) setState("disconnected"); });
        rfb.addEventListener("securityfailure", () => { if (!disposed) setState("disconnected"); });
      } catch (error) {
        if (!disposed) { console.error("Desktop initialization failed", error); setState("disconnected"); }
      }
    }
    void connect();
    const poll = setInterval(() => { void pollControl(); }, 2000);
    return () => {
      leavePage(); client.current = null; clearInterval(poll);
      window.removeEventListener("pagehide", leavePage);
      window.removeEventListener("pageshow", restorePage);
    };
  }, [revision, embedded]);

  async function changeControl(action: "takeover" | "return") {
    const response = await fetch(`/api/studio/assistant/computer/desktop/${action}`, { method: "POST" });
    if (!response.ok) return;
    const value = (await response.json()) as { state?: ControlState };
    applyControl(value.state ?? "takeover_pending");
    // The lease settles takeover_pending after its grace; the poll confirms.
  }

  return <section aria-label="我的电脑" className={`${styles.shell} ${embedded ? styles.embedded : ""}`} ref={shell}>
    <header className={styles.toolbar}>
      {!embedded && <Link href="/assistant" className={styles.back} aria-label="返回我的助理">←</Link>}
      <strong>我的电脑</strong>
      <span className={styles.connection} data-connected={state === "connected"} role="status"><i />{labels[state]}</span>
      <span className={styles.connection} data-connected={control === "human"} role="status"><i />{controlLabels[control]}</span>
      <div className={styles.actions}>
        {control === "human"
          ? <button onClick={() => void changeControl("return")}>交还助理</button>
          : <button disabled={control === "takeover_pending"} onClick={() => void changeControl("takeover")}>接管</button>}
        <button onClick={() => setRevision(value => value + 1)}>重连</button>
        <button onClick={() => { void shell.current?.requestFullscreen().catch(() => undefined); }}>全屏</button>
        {onClose && <button onClick={onClose} aria-label="收起电脑">×</button>}
      </div>
    </header>
    <div className={styles.screen} ref={screen} tabIndex={0} onFocus={() => { if (controlRef.current === "human") client.current?.focus(); }} aria-label="远程电脑桌面" onPaste={event => {
      if (controlRef.current !== "human") { event.preventDefault(); return; }
      const text = event.clipboardData.getData("text/plain");
      if (text && client.current) { client.current.clipboardPasteFrom(text); event.preventDefault(); }
    }} />
    {state !== "connected" && <div className={styles.overlay}>
      <p>{labels[state]}</p>
      {state === "unauthorized" ? <Link href="/login">登录 KAI</Link>
        : state === "disconnected" ? <button onClick={() => setRevision(value => value + 1)}>重新连接</button> : null}
    </div>}
    {state === "connected" && control !== "human" && <div className={styles.notice} role="status">
      {control === "agent" ? "助理正在操作这台电脑 · 点「接管」可随时接手"
        : control === "takeover_pending" ? "正在切换控制权…"
        : "观察中 · 画面实时更新，点「接管」获得键盘和鼠标"}
    </div>}
  </section>;
}
