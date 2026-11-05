"use client";

import Link from "next/link";
import { BotGlyph } from "./bot-glyph";
import { useEffect, useRef, useState } from "react";
import type RFB from "@novnc/novnc";
import styles from "./computer-desktop.module.css";

type TargetSelection = { active_target: string | null; targets: { target_id: string; label: string; binding_key: string }[] };
type ConnectionState = "connecting" | "connected" | "disconnected" | "unauthorized";
type ControlState = "observe" | "agent" | "human" | "takeover_pending";
type DesktopControl = { state: ControlState; binding_key: string; epoch?: number; revision?: number };
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
  const [authority, setAuthority] = useState<DesktopControl | null>(null);
  const [controlError, setControlError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const operationVersion = useRef(0);
  const actionInFlight = useRef(false);
  const refreshControl = useRef<() => Promise<void>>(async () => {});
  const viewingExecution = Boolean(selection && viewTarget === selection.active_target);
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
    let polling = false;
    setState("connecting");
    // Full navigations can enter bfcache without React unmounting this tree.
    const leavePage = () => { disposed = true; controller.abort(); rfb?.disconnect(); };
    const restorePage = (event: PageTransitionEvent) => { if (event.persisted) setRevision(value => value + 1); };
    window.addEventListener("pagehide", leavePage);
    window.addEventListener("pageshow", restorePage);
    async function pollControl() {
      // The lease is the authority; the viewer only mirrors it for the UI and
      // stops asking noVNC to send input when the user does not own it.
      if (disposed || polling || actionInFlight.current) return;
      polling = true;
      const version = operationVersion.current;
      try {
        const [response, targets] = await Promise.all([
          fetch("/api/studio/assistant/computer/desktop", { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]) }),
          fetch("/api/studio/assistant/computer/targets", { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]) }),
        ]);
        if (!response.ok || !targets.ok) throw new Error("控制状态暂时无法确认，请稍后重试；画面已暂停输入");
        const selected = await targets.json() as TargetSelection;
        const value = await response.json() as DesktopControl;
        if (disposed || version !== operationVersion.current) return;
        const activeBinding = selected.targets.find(target => target.target_id === selected.active_target)?.binding_key;
        if (!activeBinding || value.binding_key !== activeBinding) throw new Error("电脑已切换，正在重新确认控制状态");
        setSelection(selected); setAuthority(value); setControlError("");
        const next = selected.active_target === targetRef.current ? value.state : "observe";
        if (next !== controlRef.current) applyControl(next);
      } catch (error) {
        if (!disposed && version === operationVersion.current) {
          applyControl("observe");
          setControlError(error instanceof Error && error.name === "Error" ? error.message : "控制状态暂时无法确认，请重试；画面已暂停输入");
        }
      } finally { polling = false; }
    }
    refreshControl.current = pollControl;
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
      refreshControl.current = async () => {};
      window.removeEventListener("pagehide", leavePage);
      window.removeEventListener("pageshow", restorePage);
    };
  }, [revision, embedded]);

  function viewComputer(targetId: string) {
    if (targetId === targetRef.current) return;
    operationVersion.current += 1;
    targetRef.current = targetId;
    setViewTarget(targetId); setSelectionError("");
    applyControl("observe"); setRevision(value => value + 1);
  }

  async function selectComputer(targetId: string) {
    if (actionInFlight.current) return;
    const version = ++operationVersion.current;
    actionInFlight.current = true;
    setSwitching(true); setSelectionError("");
    applyControl("observe");
    try {
      const response = await fetch("/api/studio/assistant/computer/targets/select", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_id: targetId }),
        signal: AbortSignal.timeout(8000),
      });
      if (version !== operationVersion.current) return;
      if (!response.ok) {
        setSelectionError(response.status === 409 ? "当前工作仍在原电脑执行，结束并交还控制后可更换助理使用的电脑；现在可以自由切换查看" : "暂时无法更换助理使用的电脑，请重试");
        return;
      }
      const value = await response.json() as TargetSelection;
      if (version !== operationVersion.current) return;
      setSelection(value); setAuthority(null); setActionError("");
      applyControl("observe"); setRevision(value => value + 1);
    } catch { if (version === operationVersion.current) setSelectionError("暂时无法切换电脑，请重试"); }
    finally { actionInFlight.current = false; setSwitching(false); void refreshControl.current(); }
  }

  async function changeControl(action: "takeover" | "return") {
    if (actionInFlight.current || action === "takeover" && !viewingExecution) return;
    const binding = selection?.targets.find(target => target.target_id === selection.active_target)?.binding_key;
    if (!binding) { setActionError("尚未确认助理使用的电脑，请重试控制状态"); return; }
    const version = ++operationVersion.current;
    actionInFlight.current = true; setActionBusy(true); setActionError("");
    // A failed or superseded request must not leave the canvas accepting input.
    applyControl("observe");
    try {
      const response = await fetch(`/api/studio/assistant/computer/desktop/${action}?${new URLSearchParams({ binding_key: binding })}`, {
        method: "POST", signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) throw new Error(response.status === 409
        ? "控制权或电脑已变化，请重试控制状态后再操作"
        : response.status === 401 || response.status === 403 ? "暂时无法操作控制权，请确认登录后重试"
        : "控制权操作未成功，请重试；当前画面仅观看");
      const value = await response.json() as DesktopControl;
      if (version !== operationVersion.current) return;
      setAuthority({ ...value, binding_key: binding });
      setControlError("");
      applyControl(viewingExecution ? value.state : "observe");
    } catch (error) {
      if (version === operationVersion.current) setActionError(error instanceof Error && error.name === "Error"
        ? error.message : "控制权请求未完成，请重试控制状态后再操作");
    } finally {
      actionInFlight.current = false; setActionBusy(false);
      void refreshControl.current();
    }
  }

  const viewedLabel = selection?.targets.find(target => target.target_id === viewTarget)?.label ?? "我的电脑";
  const executionLabel = selection?.targets.find(target => target.target_id === selection.active_target)?.label;
  const activeBinding = selection?.targets.find(target => target.target_id === selection.active_target)?.binding_key;
  const returnable = authority?.binding_key === activeBinding && (authority?.state === "human" || authority?.state === "takeover_pending");
  const explanation = controlError ? controlError : !selection ? "正在确认电脑和控制权"
    : !viewingExecution ? `当前仅观看${viewedLabel}；助理使用的是${executionLabel ?? "另一台电脑"}。要让助理操作这里，请点击“供助理使用”`
    : control === "human" ? "由你控制：点击画面后可使用鼠标和键盘；完成后点击“交还助理”"
    : control === "takeover_pending" ? "正在等待助理结束当前动作，交接完成后才能操作"
    : control === "agent" ? "助理正在操作；需要自行操作时，点击“取得控制权”"
    : "当前仅观看，点击“取得控制权”后才能操作鼠标和键盘";
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
      {selection && selection.targets.length > 1 ? <select className={styles.targetSelect} aria-label="查看电脑" disabled={actionBusy || switching}
        value={viewTarget ?? ""} onChange={event => viewComputer(event.target.value)}>
        {selection.targets.map(target => <option key={target.target_id} value={target.target_id}>{target.label}</option>)}
      </select> : <span className={styles.caption}>{viewedLabel}的屏幕</span>}
      <div className={styles.controlPill} data-pending={control === "takeover_pending"}>
        <span className={styles.viewState} role="status">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Z"/><circle cx="10" cy="10" r="2"/></svg>
          {controlError ? "控制状态待确认" : control === "human" ? "由你控制" : control === "takeover_pending" ? "正在交接" : control === "agent" ? "助理操作中" : "仅观看"}
        </span>
        {!selection ? <button className={styles.primaryAction} disabled>正在确认控制权</button> : viewingExecution ? (returnable
          ? <button className={styles.primaryAction} disabled={actionBusy} onClick={() => void changeControl("return")}>{authority?.state === "takeover_pending" ? "取消接管" : "交还助理"}</button>
          : <button className={styles.primaryAction} disabled={state !== "connected" || actionBusy || !authority || Boolean(controlError)} onClick={() => void changeControl("takeover")}>{actionBusy ? "正在请求" : "取得控制权"}</button>)
          : <button className={styles.primaryAction} disabled={switching || actionBusy || !selection} onClick={() => viewTarget && void selectComputer(viewTarget)}>{switching ? "正在切换" : "供助理使用"}</button>}
      </div>
      <details className={styles.more}>
        <summary aria-label="更多电脑操作">···</summary>
        <div className={styles.menu}>
          <button onClick={() => setRevision(value => value + 1)}>重新连接</button>
          <button onClick={() => void refreshControl.current()}>重试控制状态</button>
        </div>
      </details>
      <span className={styles.connection} role="status">{state === "connected" && control === "agent" ? "助理操作中" : labels[state]}</span>
    </footer>
    <p className={styles.notice} role="status">{explanation}</p>
    {selection && selection.targets.length > 1 && <p className={styles.notice}>默认使用云电脑；仅在明确需要时，将本地电脑设为“供助理使用”</p>}
    {!viewingExecution && returnable && <div className={styles.ownershipNotice}>
      <span>{executionLabel ?? "助理使用的电脑"}{authority?.state === "human" ? "由你控制" : "正在交接控制权"}</span>
      <button disabled={actionBusy} onClick={() => void changeControl("return")}>{authority?.state === "takeover_pending" ? "取消接管" : `交还${executionLabel ?? "控制权"}`}</button>
    </div>}
    {actionError && <p className={styles.selectionError} role="alert">{actionError}</p>}
    {selectionError && <p className={styles.selectionError} role="alert">{selectionError}</p>}
  </section>;
}
