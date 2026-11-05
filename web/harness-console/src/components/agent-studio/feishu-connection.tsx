"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { assistantRequest } from "../../lib/assistant-client";
import { ChannelCard } from "./channel-card";
import channels from "./remote-channels.module.css";
import { SecretInput } from "../secret-input";
import styles from "./feishu-connection.module.css";

type Status = { configured: boolean; enabled: boolean; receiverOnline: boolean; receiverState: string;
  lastEventAt: string | null; bound: boolean; notifications: boolean; boundAt: string | null;
  pendingDeliveries: number; failedDeliveries: number; pendingFiles?: number; failedFiles?: number; deliveredFiles?: number };
type Configuration = { appId: string; configured: boolean; enabled: boolean; revision: number;
  appliedRevision: number; receiverLabel: string; canManage: boolean; source: string };
type Diagnostics = { checkedAt: string; checks: { key: string; label: string;
  status: "pass" | "pending" | "fail"; detail: string }[] };
type Code = { code: string; expiresAt: string };
const date = (value: string) => new Date(value).toLocaleString("zh-CN");
const permissions = JSON.stringify({ scopes: { tenant: ["im:message.p2p_msg:readonly",
  "im:message:send_as_bot", "im:message:readonly", "im:resource:upload"], user: [] } }, null, 2);

export function FeishuConnection({ compact = true }: { compact?: boolean }) {
  const [managed, setManaged] = useState(false); const management = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (managed) management.current?.showModal(); else management.current?.close(); }, [managed]);
  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Configuration | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [code, setCode] = useState<Code | null>(null);
  const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false); const pending = useRef(false);
  const [open, setOpen] = useState(false); const dialog = useRef<HTMLDialogElement>(null);
  const [appId, setAppId] = useState(""); const [secret, setSecret] = useState("");
  const [confirm, setConfirm] = useState<"disconnect" | "unbind" | null>(null);
  const [now, setNow] = useState(Date.now());
  const refresh = useCallback(async () => {
    const [next, configuration] = await Promise.all([
      assistantRequest<Status>("feishu/status"), assistantRequest<Configuration>("feishu/connection"),
    ]);
    setStatus(next); setConfig(configuration); if (next.bound) setCode(null);
  }, []);
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const [next, configuration] = await Promise.all([
          assistantRequest<Status>("feishu/status"), assistantRequest<Configuration>("feishu/connection"),
        ]);
        if (alive) { setStatus(next); setConfig(configuration); if (next.bound) setCode(null); }
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : "无法读取连接状态。"); }
    };
    void poll(); const timer = setInterval(() => { setNow(Date.now()); if (!document.hidden) void poll(); }, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close(); }, [open]);
  const close = () => { setSecret(""); setOpen(false); };
  const perform = async (action: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作未完成，请重试。"); }
    finally { pending.current = false; setBusy(false); }
  };
  const diagnose = async () => setDiagnostics(await assistantRequest<Diagnostics>("feishu/connection/diagnose", "POST"));
  async function save(event: FormEvent) {
    event.preventDefault(); await perform(async () => {
      await assistantRequest("feishu/connection", "PUT", { expectedRevision: config?.revision ?? 0,
        appId: appId.trim(), ...(secret.trim() ? { appSecret: secret.trim() } : {}) });
      close(); setDiagnostics(null); setNotice("配置已保存，正在连接飞书。账号绑定和聊天记录已保留。");
    });
  }
  async function sendTest() {
    const { requestId } = await assistantRequest<{ requestId: string }>("feishu/connection/test", "POST");
    for (let attempt = 0; attempt < 5; attempt++) {
      const result = await assistantRequest<{ status: string }>(`feishu/connection/tests/${encodeURIComponent(requestId)}`);
      if (result.status === "sent") { setNotice("测试消息已送达你的飞书。"); await diagnose(); return; }
      if (["failed", "uncertain"].includes(result.status)) throw new Error("测试消息未确认送达，请运行连接诊断。");
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
    setNotice("测试消息正在发送，请稍后运行连接诊断确认结果。");
  }
  const online = status?.enabled && status.receiverOnline && config?.revision === config?.appliedRevision
    && ["awaiting_event", "receiving"].includes(status.receiverState);
  const label = !config ? "正在读取" : !config.configured ? "未连接" : !config.enabled ? "已断开"
    : online ? "已连接" : status?.receiverState === "reconnecting" ? "正在重连" : "连接中";
  const details = <>
    {!open && error && <p role="alert" className={styles.error}>{error}</p>}
    {notice && <p role="status" className={styles.notice}>{notice}</p>}
    <article className={compact ? channels.detailCard : styles.card}>
      {!compact && <><div className={styles.cardTop}><div className={styles.channel}>
        <svg className={styles.logo} viewBox="0 0 48 48" aria-hidden="true"><path fill="#00c9b8" d="M5 8c13 1 22 9 28 22L19 35C15 23 10 14 5 8Z" /><path fill="#3370ff" d="M4 26c10 9 22 7 39-9-2 16-13 27-25 24C10 39 6 34 4 26Z" /></svg>
        <h3>飞书集成</h3></div><span className={styles.state} data-online={Boolean(online)}><i />{label}</span></div></>}
      {!compact && <p>直接发送文字、图片、文件和语音，助理的答复同步回飞书。</p>}
      {config?.configured && <div className={styles.connectionLine}><span>{config.receiverLabel}</span><code>{config.appId}</code></div>}
      <div className={styles.actions}>
        {config?.canManage && <button className={styles.primary} disabled={busy} onClick={() => { setAppId(config.appId); setSecret(""); setError(""); setOpen(true); }}>{config.enabled ? "配置连接" : "连接飞书"}</button>}
        {config?.configured && <button className={styles.button} disabled={busy} onClick={() => void perform(diagnose)}>连接诊断</button>}
        {online && status?.bound && <button className={styles.button} disabled={busy} onClick={() => void perform(sendTest)}>发送测试消息</button>}
        {Boolean(status?.failedFiles) && <button className={styles.button} disabled={busy} onClick={() => void perform(async () => {
          const result = await assistantRequest<{ queued: number }>("feishu/files/retry", "POST");
          setNotice(`已重新排队 ${result.queued} 份未发送文件。回执不明确的文件不会自动重发。`);
        })}>重试未发送文件</button>}
        {config?.canManage && config.enabled && <button className={styles.textButton} disabled={busy} onClick={() => setConfirm("disconnect")}>断开连接</button>}
      </div>
      {config && !config.canManage && !config.enabled && <small>由工作区管理员连接飞书机器人，成员只需绑定本人账号。</small>}
      {confirm === "disconnect" && <div className={styles.confirm}><p>断开后暂停接收飞书消息，所有成员的账号绑定和聊天记录仍会保留。</p>
        <button className={styles.button} disabled={busy} onClick={() => void perform(async () => {
          await assistantRequest("feishu/connection/disconnect", "POST", { expectedRevision: config?.revision });
          setConfirm(null); setDiagnostics(null); setNotice("已断开飞书连接，绑定和聊天记录已保留。");
        })}>确认断开</button><button className={styles.textButton} onClick={() => setConfirm(null)}>保持连接</button></div>}
    </article>
    {diagnostics && <section className={styles.diagnostics} aria-label="连接诊断结果">
      <div className={styles.sectionTitle}><h3>连接诊断</h3><small>{date(diagnostics.checkedAt)}</small></div>
      {diagnostics.checks.map(check => <div className={styles.check} key={check.key} data-state={check.status}>
        <span aria-label={check.status === "pass" ? "通过" : check.status === "fail" ? "失败" : "待验证"}>{check.status === "pass" ? "✓" : check.status === "fail" ? "!" : "…"}</span>
        <div><strong>{check.label}</strong><p>{check.detail}</p></div></div>)}
    </section>}
    {config?.configured && <section className={styles.account} aria-label="本人飞书账号">
      <div className={styles.sectionTitle}><h3>本人账号</h3><span className={styles.state}>{status?.bound ? "已绑定" : "待绑定"}</span></div>
      {status?.bound ? <><p>已关联当前 KAI 账号。</p>
        <div className={styles.accountMeta}>通知：{status.notifications ? "已开启" : "已关闭"} · 待投递 {status.pendingDeliveries} 条{status.failedDeliveries > 0 && ` · 未确认或失败 ${status.failedDeliveries} 条`}</div>
        <div className={styles.accountMeta}>文件：已交付 {status.deliveredFiles ?? 0} 份 · 待发送 {status.pendingFiles ?? 0} 份{Boolean(status.failedFiles) && ` · 未确认或失败 ${status.failedFiles} 份，可在助理文件中下载`}</div>
        <div className={styles.actions}><button className={styles.button} disabled={busy} onClick={() => void perform(async () => {
          await assistantRequest("feishu/notifications", "PUT", { enabled: !status.notifications });
        })}>{status.notifications ? "关闭飞书通知" : "开启飞书通知"}</button><button className={styles.textButton} disabled={busy} onClick={() => setConfirm("unbind")}>解除绑定</button></div>
        {confirm === "unbind" && <div className={styles.confirm}><p>解除后，这个飞书账号将无法访问你的 KAI 助理；聊天记录保留。</p><button className={styles.button} disabled={busy} onClick={() => void perform(async () => {
          await assistantRequest("feishu/binding", "DELETE"); setCode(null); setConfirm(null); setNotice("已解除本人账号绑定。");
        })}>确认解除绑定</button><button className={styles.textButton} onClick={() => setConfirm(null)}>保留绑定</button></div>}
      </> : <><p>在飞书私聊发送绑定码，关联本人账号。</p>
        <button className={styles.button} disabled={busy || !config.enabled || Boolean(code && now < Date.parse(code.expiresAt) - 570000)} onClick={() => void perform(async () => { setCode(await assistantRequest<Code>("feishu/binding-code", "POST")); setNow(Date.now()); })}>{code ? "重新生成绑定码" : "生成绑定码"}</button>
        {code && Date.parse(code.expiresAt) > now && <div className={styles.bindingCode}><input aria-label="发送给 KAI 机器人的绑定消息" readOnly value={`绑定 ${code.code}`} onFocus={event => event.target.select()} /><button className={styles.button} onClick={() => void perform(async () => { await navigator.clipboard.writeText(`绑定 ${code.code}`); setNotice("绑定消息已复制。"); })}>复制绑定消息</button><small>有效至 {date(code.expiresAt)}，请只发送给专用 KAI 机器人。</small></div>}
        {code && Date.parse(code.expiresAt) <= now && <p>绑定码已过期，请重新生成。</p>}
      </>}
    </section>}
    <details className={styles.guide}><summary>首次配置指南</summary><ol>
      <li>在飞书开放平台创建企业自建应用，并启用机器人。</li><li>将 App ID 和 App Secret 填入“连接飞书”，让 KAI 建立长连接。</li>
      <li>在飞书后台导入消息权限，选择长连接，订阅 <code>im.message.receive_v1</code>；使用卡片操作时同时添加 <code>card.action.trigger</code>。</li>
      <li>设置应用可用范围并按企业要求发布，然后绑定本人账号、发送消息测试。</li></ol>
      <button className={styles.button} onClick={() => void perform(async () => { await navigator.clipboard.writeText(permissions); setNotice("消息权限清单已复制，可在飞书权限管理中批量导入。"); })}>复制最小消息权限清单</button><p>同一个应用应连接到当前使用的 KAI 环境。切换服务器时，请先停用旧环境的接收器。</p>
    </details>
  </>;
  return <section className={compact ? `${styles.panel} ${channels.channel}` : styles.panel} aria-label="飞书远程通道">
    {compact ? <><ChannelCard name="飞书集成" description="通过飞书与助理对话，结果同步回聊天。" logo={<svg className={styles.logo} viewBox="0 0 48 48" aria-hidden="true"><path fill="#00c9b8" d="M5 8c13 1 22 9 28 22L19 35C15 23 10 14 5 8Z" /><path fill="#3370ff" d="M4 26c10 9 22 7 39-9-2 16-13 27-25 24C10 39 6 34 4 26Z" /></svg>} state={online && !status?.bound ? "待绑定" : label} online={Boolean(online && status?.bound)} connected={Boolean(config?.enabled && status?.bound)} disabled={busy || !config}
      onClick={() => { if (config?.canManage && !config.enabled) { setAppId(config.appId); setSecret(""); setError(""); setOpen(true); } else setManaged(true); }} />
      {!managed && !open && error && <p role="alert" className={styles.error}>{error}</p>}
      <dialog ref={management} className={`${styles.panel} ${styles.dialog} ${channels.management}`} aria-labelledby="feishu-management-title" onCancel={() => setManaged(false)}>
        <div className={styles.dialogTitle}><h2 id="feishu-management-title">飞书集成</h2><button className={styles.close} aria-label="关闭飞书管理" onClick={() => setManaged(false)}>×</button></div>{details}
      </dialog></> : <>    <header className={styles.header}><div><h2>远程通道</h2><p>连接飞书，在聊天里使用你的 KAI 助理。与网页保持同一段对话。</p></div>
      <a href="https://open.feishu.cn/app" target="_blank" rel="noreferrer">飞书开放平台 ↗</a></header>
{details}</>}
    {config?.canManage && <dialog ref={dialog} className={styles.dialog} aria-labelledby="feishu-dialog-title" onCancel={close}>
      <form onSubmit={save}><div className={styles.dialogTitle}><h2 id="feishu-dialog-title">连接飞书</h2><button className={styles.close} type="button" aria-label="关闭连接配置" onClick={close}>×</button></div>
        <div className={styles.mode}><span>WebSocket 长连接</span><small>无需公网回调地址</small></div>
        <label>飞书 App ID<input autoFocus autoComplete="off" value={appId} onChange={e => setAppId(e.target.value)} placeholder="cli_…" required pattern="cli_[A-Za-z0-9]+" maxLength={128} /></label>
        <label>飞书 App Secret<SecretInput revealLabel="App Secret" autoComplete="off" value={secret} onChange={e => setSecret(e.target.value)} placeholder={config?.configured ? "已保存，留空保留现有 Secret" : "请输入"} required={!config?.configured || appId !== config.appId} maxLength={16384} /></label>
        <p className={styles.help}>仅工作区管理员配置。凭据加密保存在 KAI 服务端，成员无需填写。</p>
        {error && <p role="alert" className={styles.error}>{error}</p>}
        <div className={styles.dialogFooter}><button className={styles.button} type="button" disabled={busy} onClick={close}>取消</button><button className={styles.primary} disabled={busy}>{busy ? "正在验证…" : "连接"}</button></div>
      </form>
    </dialog>}
  </section>;
}
