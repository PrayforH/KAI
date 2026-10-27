"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type RFB from "@novnc/novnc";
import styles from "./computer-desktop.module.css";

type ConnectionState = "connecting" | "connected" | "disconnected" | "unauthorized";
const labels = { connecting: "正在连接", connected: "已连接", disconnected: "连接已断开", unauthorized: "请重新登录" };

export function ComputerDesktop() {
  const screen = useRef<HTMLDivElement>(null);
  const shell = useRef<HTMLElement>(null);
  const client = useRef<RFB | null>(null);
  const [state, setState] = useState<ConnectionState>("connecting");
  const [revision, setRevision] = useState(0);
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
    async function connect() {
      try {
        // Refresh through the existing BFF; the socket uses HttpOnly cookies.
        const session = await fetch("/api/auth/session", { cache: "no-store", signal: controller.signal });
        if (!session.ok) { if (!disposed) setState("unauthorized"); return; }
        const status = await fetch("/api/computer/status", { cache: "no-store", signal: controller.signal });
        if (!status.ok) { if (!disposed) setState(status.status === 403 ? "unauthorized" : "disconnected"); return; }
        const { default: RemoteFramebuffer } = await import("@novnc/novnc");
        if (disposed || !screen.current) return;
        const url = new URL("/computer/desktop", window.location.href);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        rfb = new RemoteFramebuffer(screen.current, url.toString());
        client.current = rfb;
        rfb.viewOnly = false;
        rfb.scaleViewport = true;
        rfb.resizeSession = false;
        rfb.qualityLevel = 6;
        rfb.compressionLevel = 2;
        rfb.addEventListener("connect", () => { if (!disposed) { setState("connected"); rfb?.focus(); } });
        rfb.addEventListener("disconnect", () => { if (!disposed) setState("disconnected"); });
        rfb.addEventListener("securityfailure", () => { if (!disposed) setState("disconnected"); });
      } catch (error) {
        if (!disposed) { console.error("Desktop initialization failed", error); setState("disconnected"); }
      }
    }
    void connect();
    return () => {
      leavePage(); client.current = null;
      window.removeEventListener("pagehide", leavePage);
      window.removeEventListener("pageshow", restorePage);
    };
  }, [revision]);

  return <main id="main-content" className={styles.shell} ref={shell}>
    <header className={styles.toolbar}>
      <Link href="/assistant" className={styles.back} aria-label="返回我的助理">←</Link>
      <strong>我的电脑</strong><span className={styles.preview}>预览</span>
      <span className={styles.connection} data-connected={state === "connected"} role="status"><i />{labels[state]}</span>
      <div className={styles.actions}>
        <button onClick={() => setRevision(value => value + 1)}>重连</button>
        <button onClick={() => { void shell.current?.requestFullscreen().catch(() => undefined); }}>全屏</button>
      </div>
    </header>
    <div className={styles.screen} ref={screen} tabIndex={0} onFocus={() => client.current?.focus()} aria-label="远程电脑桌面" onPaste={event => {
      const text = event.clipboardData.getData("text/plain");
      if (text && client.current) { client.current.clipboardPasteFrom(text); event.preventDefault(); }
    }} />
    {state !== "connected" && <div className={styles.overlay}>
      <p>{labels[state]}</p>
      {state === "unauthorized" ? <Link href="/login">登录 KAI</Link>
        : state === "disconnected" ? <button onClick={() => setRevision(value => value + 1)}>重新连接</button> : null}
    </div>}
  </main>;
}
