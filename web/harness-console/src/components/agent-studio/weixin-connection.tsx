"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { assistantRequest } from "../../lib/assistant-client";
import styles from "./feishu-connection.module.css";
import wx from "./weixin-connection.module.css";

type Status = { bound: boolean; enabled: boolean; online: boolean; state: string;
  loginId?: string; loginStatus: string | null; expiresAt?: string; qrSvg?: string | null;
  lastEventAt?: string | null; lastReplyAt?: string | null; errorCode?: string | null;
  pendingMessages?: number; failedMessages?: number; receiverLabel?: string };
type Diagnostics = { checkedAt: string; checks: { key: string; label: string;
  status: "pass" | "pending" | "fail"; detail: string }[] };

export function WeixinConnection() {
  const [status, setStatus] = useState<Status | null>(null);
  const [open, setOpen] = useState(false); const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false); const pending = useRef(false);
  const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [code, setCode] = useState(""); const [confirm, setConfirm] = useState(false);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const refresh = useCallback(async () => {
    const next = await assistantRequest<Status>("weixin/status"); setStatus(next); return next;
  }, []);
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const next = await assistantRequest<Status>("weixin/status");
        if (alive) { setStatus(next); if (next.loginStatus === "confirmed") setCode(""); }
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : "无法读取微信状态。"); }
    };
    void poll(); const timer = setInterval(() => { if (!document.hidden) void poll(); }, 2500);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close(); }, [open]);
  const close = () => { setOpen(false); setCode(""); };
  const perform = async (action: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作未完成，请重试。"); }
    finally { pending.current = false; setBusy(false); }
  };
  const start = () => perform(async () => {
    setStatus(await assistantRequest<Status>("weixin/login", "POST")); setOpen(true);
  });
  const verify = (event: FormEvent) => { event.preventDefault(); void perform(async () => {
    setStatus(await assistantRequest<Status>("weixin/verify", "POST", { loginId: status?.loginId, code })); setCode("");
  }); };
  const expired = ["expired", "failed", "verify_code_blocked"].includes(status?.loginStatus ?? "");
  const paired = status?.enabled && status.loginStatus === "confirmed";
  const label = !status ? "正在读取" : status.online ? "已连接" : status.enabled ? "连接中"
    : status.state === "authorization_expired" ? "需重新扫码" : "未连接";
  return <section className={`${styles.panel} ${wx.panel}`} aria-label="微信远程通道">
    <article className={styles.card}>
      <div className={styles.cardTop}><div className={styles.channel}>
        <svg className={styles.logo} viewBox="0 0 48 48" aria-hidden="true"><path fill="#07c160" d="M4 20C4 11 13 5 23 5s19 6 19 15-9 15-19 15c-3 0-6-.5-8-1.5L7 38l2-8C6 27 4 24 4 20Z"/><circle cx="16" cy="18" r="2" fill="white"/><circle cx="30" cy="18" r="2" fill="white"/></svg>
        <h3>微信集成</h3></div><span className={styles.state} data-online={Boolean(status?.online)}><i />{label}</span></div>
      <p>用手机微信扫码连接，直接发文字。与飞书、网页共用你的助理会话。</p>
      {status?.bound && <div className={styles.connectionLine}><span>已关联本人 KAI 账号</span><span>{status.receiverLabel}</span></div>}
      <div className={styles.actions}>
        <button className={styles.primary} disabled={busy} onClick={() => void start()}>{busy && !open ? "正在连接…" : status?.enabled ? "重新扫码" : "连接微信"}</button>
        {status?.bound && <button className={styles.button} disabled={busy} onClick={() => void perform(async () => setDiagnostics(await assistantRequest<Diagnostics>("weixin/diagnose", "POST")))}>微信连接诊断</button>}
        {(status?.enabled || status?.qrSvg) && <button className={styles.textButton} disabled={busy} onClick={() => setConfirm(true)}>断开微信</button>}
      </div>
      {confirm && <div className={styles.confirm}><p>断开后暂停微信接收。飞书连接、网页会话和聊天记录继续保留。</p>
        <button className={styles.button} disabled={busy} onClick={() => void perform(async () => { await assistantRequest("weixin/disconnect", "POST"); setConfirm(false); setDiagnostics(null); close(); setNotice("微信已断开，聊天记录已保留。"); })}>确认断开微信</button>
        <button className={styles.textButton} onClick={() => setConfirm(false)}>保持微信连接</button></div>}
      {status?.enabled && <p className={wx.meta}>待处理 {status.pendingMessages ?? 0} 条{Boolean(status.failedMessages) && ` · 未确认或失败 ${status.failedMessages} 条`}</p>}
    </article>
    {!open && error && <p role="alert" className={styles.error}>{error}</p>}
    {notice && <p role="status" className={styles.notice}>{notice}</p>}
    {diagnostics && <section className={styles.diagnostics} aria-label="微信诊断结果">
      <div className={styles.sectionTitle}><h3>微信连接诊断</h3><small>{new Date(diagnostics.checkedAt).toLocaleString("zh-CN")}</small></div>
      {diagnostics.checks.map(check => <div className={styles.check} key={check.key} data-state={check.status}><span aria-label={check.status === "pass" ? "通过" : "待验证"}>{check.status === "pass" ? "✓" : "…"}</span><div><strong>{check.label}</strong><p>{check.detail}</p></div></div>)}
    </section>}
    <dialog ref={dialog} className={styles.dialog} aria-labelledby="weixin-dialog-title" onCancel={close}>
      <div className={styles.dialogTitle}><h2 id="weixin-dialog-title">连接微信</h2><button className={styles.close} aria-label="关闭微信连接" onClick={close}>×</button></div>
      {paired ? <div className={wx.success}><span aria-hidden="true">✓</span><h3>微信已绑定</h3><p>在微信助理里发一句话，即可在网页接着聊。</p><button className={styles.primary} onClick={close}>完成</button></div>
      : expired ? <div className={wx.success}><h3>二维码已失效</h3><p>重新生成后，用本人微信扫码。</p><button className={styles.primary} disabled={busy} onClick={() => void start()}>重新生成微信二维码</button></div>
      : <><div className={wx.qr}>{status?.qrSvg ? <img width="240" height="240" alt="微信助理绑定二维码" src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(status.qrSvg)}`} /> : <p>正在生成二维码…</p>}</div>
        <p className={wx.caption}>{status?.loginStatus === "scaned" ? "已扫码，请在手机微信确认连接。" : status?.loginStatus === "need_verifycode" ? "请输入手机微信显示的配对码。" : "用本人微信扫一扫，并在手机上确认。"}</p>
        <p className={styles.help}>扫码后，此微信将关联当前登录的 KAI 账号，可访问同一段助理对话。首版支持文字消息。</p>
        {status?.loginStatus === "need_verifycode" && <form onSubmit={verify}><label>微信配对码<input aria-label="微信配对码" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} required pattern="[0-9A-Za-z]{4,12}" maxLength={12} /></label><button className={styles.primary} disabled={busy}>确认配对码</button></form>}
        {status?.expiresAt && <p className={wx.meta}>二维码有效至 {new Date(status.expiresAt).toLocaleTimeString("zh-CN")}</p>}</>}
      {error && <p role="alert" className={styles.error}>{error}</p>}
    </dialog>
  </section>;
}
