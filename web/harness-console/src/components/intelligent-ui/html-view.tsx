"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { uiPath } from "./client";
import type { Presentation } from "./schema";

/** State is data, never an action request. Each mounted frame has its own token. */
export function HtmlView({ presentation, height, initialState, ready, onState }: {
  presentation: Presentation;
  height: number;
  initialState?: Record<string, unknown>;
  ready: boolean;
  onState: (state: Record<string, unknown>) => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [token, setToken] = useState("");
  const [initialized, setInitialized] = useState(false);
  const [showStateNotice, setShowStateNotice] = useState(false);
  const initializedRef = useRef(false);
  const [error, setError] = useState("");
  const restored = useRef(initialState);
  const change = useRef(onState);
  restored.current = initialState;
  change.current = onState;
  const url = `${uiPath(presentation.presentation_id)}/html?revision=${presentation.revision}`;

  useEffect(() => {
    // Generate after hydration so the server and client's initial markup agree.
    setToken(Array.from(crypto.getRandomValues(new Uint8Array(16)),
      b => b.toString(16).padStart(2, "0")).join(""));
  }, []);

  useEffect(() => {
    if (!ready || !token || initialized) return;
    // Avoid flashing a stateless notice while the normal ready handshake runs.
    const timer = window.setTimeout(() => setShowStateNotice(true), 3_000);
    return () => window.clearTimeout(timer);
  }, [ready, token, initialized]);

  useLayoutEffect(() => {
    if (!ready || !token) return;
    const receive = (event: MessageEvent) => {
      if (!frame.current || event.source !== frame.current.contentWindow || event.origin !== "null") return;
      const message = event.data;
      if (!message || typeof message !== "object" || Array.isArray(message) || message.token !== token) return;
      if (message.type === "harness.ui.ready" && !initializedRef.current) {
        if (Object.keys(message).some(key => !["type", "token"].includes(key))) return;
        initializedRef.current = true;
        frame.current.contentWindow?.postMessage({
          type: "harness.ui.init", token, state: restored.current ?? {},
        }, "*");
        setInitialized(true);
      } else if (message.type === "harness.ui.state" && initializedRef.current) {
        if (Object.keys(message).some(key => !["type", "token", "state"].includes(key))) return;
        if (!message.state || typeof message.state !== "object" || Array.isArray(message.state)) return;
        try {
          const encoded = JSON.stringify(message.state, (_key, value) => {
            if (typeof value === "number" && !Number.isFinite(value)) throw new Error("invalid number");
            return value;
          });
          // Leave space for the view envelope within the server's 16 KB bound.
          if (new TextEncoder().encode(encoded).byteLength > 12_000) throw new Error("state too large");
          const state = JSON.parse(encoded);
          if (!state || typeof state !== "object" || Array.isArray(state)) throw new Error("invalid state");
          change.current(state);
          setError("");
        } catch {
          setError("操作状态过大或无效，当前输入仍保留在此页。");
        }
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [ready, token]);

  if (!ready || !token) return <p className="intelligent-ui-note">正在恢复工具…</p>;
  return <div className="intelligent-ui-html">
    <iframe ref={frame} src={`${url}#harness_ui_token=${token}`}
      title={presentation.title || "交互工具"} height={height}
      sandbox="allow-scripts" referrerPolicy="no-referrer" />
    {showStateNotice && !initialized ? <p className="intelligent-ui-note">此工具未启用状态恢复，重新打开时会从初始设置开始。</p> : null}
    {error ? <p role="alert" className="intelligent-ui-form-error">{error}</p> : null}
  </div>;
}
