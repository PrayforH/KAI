"use client";

import Link from "next/link";
import { BotGlyph } from "./bot-glyph";
import { useEffect, useRef, useState } from "react";
import type RFB from "@novnc/novnc";
import styles from "./computer-desktop.module.css";

type TargetSelection = { active_target: string | null; targets: { target_id: string; label: string; binding_key: string }[] };
type ConnectionState = "connecting" | "connected" | "disconnected" | "unauthorized";
type ControlState = "observe" | "agent" | "human" | "takeover_pending";
const labels = { connecting: "正在连接", connected: "已连接", disconnected: "连接已断开", unauthorized: "请重新登录" };


export function ComputerDesktop({ embedded = false, onClose }: { embedded?: boolean; onClose?: () => void }) {
  const screen = useRef<HTMLDivElement>(null);
  const shell = useRef<HTMLElement>(null);
  const client = useRef<RFB | null>(null);
  const controlRef = useRef<ControlState>("observe");
  const [state, setState] = useState<ConnectionState>("connecting");
  const [control, setControl] = useState<ControlState>("observe");
  const [revision, setRevision] = useState(0);
  const targetRef = useRef<string | null>(null);
  const [viewTarget, setViewTarget] = useState<string | null>(null);
  const [selection, setSelection] = useState<TargetSelection | null>(null);
  const [switching, setSwitching] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const viewingExecution = !selection || viewTarget === selection.active_target;
  const applyControl = (value: ControlState) => {
    controlRef.current = value;
    setControl(value);
    if (!client.current) return;
    client.current.viewOnly = value !== "human";
    // noVNC sends keys only while its canvas holds focus; mouse needs no focus,
    // which is why a takeover used to leave the keyboard dead.
    if (value === "human") client.current.focus();
  };
  useEffect(() => {
    let disposed = false;
    let rfb: RFB | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let initialRetries = 0;
    let connected = false;
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
        const [response, targets] = await Promise.all([
          fetch("/api/studio/assistant/computer/desktop", { cache: "no-store", signal: controller.signal }),
          fetch("/api/studio/assistant/computer/targets", { cache: "no-store", signal: controller.signal }),
        ]);
        const selected = targets.ok ? await targets.json() as TargetSelection : null;
        if (disposed) return;
        if (selected) setSelection(selected);
        if (response.ok) {
          const value = (await response.json()) as { state?: ControlState; binding_key?: string };
          const binding = selected?.targets.find(target => target.target_id === targetRef.current)?.binding_key;
          const next = (!selected || binding && value.binding_key === binding) ? value.state ?? "observe" : "observe";
          if (!disposed && next !== controlRef.current) applyControl(next);
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
        const targets = await fetch("/api/studio/assistant/computer/targets", { cache: "no-store", signal: controller.signal });
        if (targets.ok && !disposed) {
          const value = await targets.json() as TargetSelection;
          targetRef.current ??= value.active_target;
          setViewTarget(targetRef.current);
          setSelection(value);
        }
        await pollControl();
        const { default: RemoteFramebuffer } = await import("@novnc/novnc");
        if (disposed || !screen.current) return;
        const url = new URL("/computer/desktop", window.location.href);
        if (targetRef.current) url.searchParams.set("target", targetRef.current);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        rfb = new RemoteFramebuffer(screen.current, url.toString());
        client.current = rfb;
        rfb.viewOnly = controlRef.current !== "human";
        rfb.scaleViewport = true;
        rfb.resizeSession = false;
        rfb.qualityLevel = 6;
        rfb.compressionLevel = 2;
        rfb.addEventListener("connect", () => { if (!disposed) { connected = true; setState("connected"); if (!embedded && controlRef.current === "human") rfb?.focus(); } });
        rfb.addEventListener("disconnect", () => {
          if (disposed) return;
          // A recently closed preview may still be draining its SSH pipe.
          // Bound retries to setup only; never loop after a live session ends.
          if (!connected && initialRetries++ < 5) retryTimer = setTimeout(() => void connect(), 1500);
          else setState("disconnected");
        });
        rfb.addEventListener("securityfailure", () => { if (!disposed) setState("disconnected"); });
      } catch (error) {
        if (!disposed) { console.error("Desktop initialization failed", error); setState("disconnected"); }
      }
    }
    void connect();
    const poll = setInterval(() => { void pollControl(); }, 2000);
    return () => {
      leavePage(); client.current = null; clearInterval(poll); clearTimeout(retryTimer);
      window.removeEventListener("pagehide", leavePage);
      window.removeEventListener("pageshow", restorePage);
    };
  }, [revision, embedded]);

  function viewComputer(targetId: string) {
    if (targetId === targetRef.current) return;
    targetRef.current = targetId;
    setViewTarget(targetId); setSelectionError("");
    applyControl("observe"); setRevision(value => value + 1);
  }

  async function selectComputer(targetId: string) {
    setSwitching(true); setSelectionError("");
    try {
      const response = await fetch("/api/studio/assistant/computer/targets/select", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_id: targetId }),
      });
      if (!response.ok) {
        setSelectionError(response.status === 409 ? "当前工作仍在原电脑执行，结束并交还控制后可更换助理使用的电脑；现在可以自由切换查看" : "暂时无法更换助理使用的电脑，请重试");
        return;
      }
      const value = await response.json() as TargetSelection;
      setSelection(value);
      applyControl("observe"); setRevision(value => value + 1);
    } catch { setSelectionError("暂时无法切换电脑，请重试"); }
    finally { setSwitching(false); }
  }

  async function changeControl(action: "takeover" | "return") {
    if (!viewingExecution) return;
    const binding = selection?.targets.find(target => target.target_id === viewTarget)?.binding_key;
    if (selection && !binding) return;
    const query = binding ? `?${new URLSearchParams({ binding_key: binding })}` : "";
    const response = await fetch(`/api/studio/assistant/computer/desktop/${action}${query}`, { method: "POST" });
    if (!response.ok) return;
    const value = (await response.json()) as { state?: ControlState };
    applyControl(value.state ?? "takeover_pending");
    // The lease settles takeover_pending after its grace; the poll confirms.
  }

  const viewedLabel = selection?.targets.find(target => target.target_id === viewTarget)?.label ?? "我的电脑";
  const executionLabel = selection?.targets.find(target => target.target_id === selection.active_target)?.label;
  return <section aria-label="我的电脑" className={`${styles.shell} ${embedded ? styles.embedded : ""}`} ref={shell}>
    <header className={styles.windowHeader}>
      {!embedded && <Link href="/assistant" className={styles.back} aria-label="返回我的助理">←</Link>}
      <span className={styles.windowTitle}>{viewedLabel}</span>
      <div className={styles.windowActions}>
        <button aria-label="全屏" title="全屏" onClick={() => {
          if (document.fullscreenElement === shell.current) void document.exitFullscreen().catch(() => undefined);
          else void shell.current?.requestFullscreen().catch(() => undefined);
        }}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12 3h5v5M17 3l-6 6M8 17H3v-5M3 17l6-6" /></svg></button>
        {onClose && <button onClick={onClose} aria-label="收起电脑">×</button>}
      </div>
    </header>
    <div className={styles.display}>
      <div className={styles.screen} data-ready={state === "connected"} ref={screen} tabIndex={0}
        onPointerDown={() => client.current?.focus()} onFocus={() => client.current?.focus()} aria-label="远程电脑桌面" onPaste={event => {
          if (controlRef.current !== "human") { event.preventDefault(); return; }
          const text = event.clipboardData.getData("text/plain");
          if (text && client.current) { client.current.clipboardPasteFrom(text); event.preventDefault(); }
        }} />
      {state !== "connected" && <div className={styles.overlay} data-state={state} role="status">
        <div className={styles.stateGlyph}><BotGlyph shape="block" color="#858585" /></div>
        <strong>{state === "connecting" ? "正在连接电脑" : state === "disconnected" ? "电脑暂未连接" : "请重新登录"}</strong>
        {state === "unauthorized" ? <Link href="/login">登录 KAI</Link>
          : state === "disconnected" ? <button onClick={() => setRevision(value => value + 1)}>重新连接</button> : null}
      </div>}
    </div>
    <footer className={styles.toolbar}>
      {selection && selection.targets.length > 1 ? <select className={styles.targetSelect} aria-label="查看电脑"
        value={viewTarget ?? ""} onChange={event => viewComputer(event.target.value)}>
        {selection.targets.map(target => <option key={target.target_id} value={target.target_id}>{target.label}</option>)}
      </select> : <span className={styles.caption}>{viewedLabel}的屏幕</span>}
      <div className={styles.controlPill} data-pending={control === "takeover_pending"}>
        <span className={styles.viewState} role="status">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Z"/><circle cx="10" cy="10" r="2"/></svg>
          {control === "human" ? "由你控制" : control === "takeover_pending" ? "正在交接" : "仅观看"}
        </span>
        {viewingExecution && (control === "human"
          ? <button className={styles.primaryAction} onClick={() => void changeControl("return")}>交还助理</button>
          : <button className={styles.primaryAction} disabled={state !== "connected" || control === "takeover_pending"} onClick={() => void changeControl("takeover")}>取得控制权</button>)}
      </div>
      <details className={styles.more}>
        <summary aria-label="更多电脑操作">···</summary>
        <div className={styles.menu}>
          {!viewingExecution && viewTarget && <button disabled={switching} onClick={() => void selectComputer(viewTarget)}>供助理使用</button>}
          <button onClick={() => setRevision(value => value + 1)}>重新连接</button>
        </div>
      </details>
      <span className={styles.connection} role="status">{state === "connected" && control === "agent" ? "助理操作中" : labels[state]}</span>
    </footer>
    {!viewingExecution && <p className={styles.notice}>助理仍在{executionLabel ?? "原电脑"}工作，切换查看不影响执行</p>}
    {selectionError && <p className={styles.selectionError} role="alert">{selectionError}</p>}
  </section>;
}
