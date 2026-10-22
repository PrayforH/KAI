"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { assistantRequest } from "../../lib/assistant-client";
import styles from "./assistant-manager.module.css";

type Connection = {
  configured: boolean; enabled: boolean; receiverOnline: boolean; receiverState: string;
  lastEventAt: string | null; bound: boolean; notifications: boolean; boundAt: string | null;
  pendingDeliveries: number; failedDeliveries: number;
};
type Code = { code: string; expiresAt: string };
const date = (value: string) => new Date(value).toLocaleString("zh-CN");

export function FeishuConnection() {
  const [status, setStatus] = useState<Connection | null>(null);
  const [code, setCode] = useState<Code | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [confirmUnbind, setConfirmUnbind] = useState(false);
  const [now, setNow] = useState(Date.now());
  const refresh = useCallback(async () => {
    const next = await assistantRequest<Connection>("feishu/status");
    setStatus(next);
    if (next.bound) setCode(null);
  }, []);
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const next = await assistantRequest<Connection>("feishu/status");
        if (alive) { setStatus(next); if (next.bound) setCode(null); setError(""); }
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : "无法读取连接状态。"); }
    };
    void poll();
    const timer = setInterval(() => { setNow(Date.now()); if (!document.hidden) void poll(); }, 10000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  const perform = async (action: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作未完成，请稍后重试。"); }
    finally { pending.current = false; setBusy(false); }
  };
  const validCode = code && Date.parse(code.expiresAt) > now;
  const connectionLabel = !status ? "读取连接状态" : !status.enabled ? "待管理员配置"
    : !status.receiverOnline ? "接收进程离线" : status.receiverState === "receiving" ? "已收到飞书事件"
    : status.receiverState === "reconnecting" ? "正在重连" : "进程在线，等待消息验证";
  return <section className={styles.setup} aria-label="飞书连接">
    <span className={styles.badge}>{connectionLabel}</span>
    <h2>连接 KAI 专用飞书机器人</h2>
    <p>在飞书私聊创建和跟进事项，接收自己的结果通知。网页和飞书共用同一份事项记录。</p>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {notice && <p role="status" className={styles.message}>{notice}</p>}
    {status?.enabled && <>
      {status.lastEventAt && <p className={styles.muted}>最近收到事件：{date(status.lastEventAt)}</p>}
      {!status.receiverOnline && <p>接收进程当前离线，飞书消息和通知暂时无法处理。请联系管理员检查连接。</p>}
      {status.bound ? <>
        <h3>你的账号已绑定{status.boundAt ? ` · ${date(status.boundAt)}` : ""}</h3>
        <p>通知：{status.notifications ? "已开启" : "已关闭"} · 待投递 {status.pendingDeliveries} 条
          {status.failedDeliveries > 0 && ` · 未确认或失败 ${status.failedDeliveries} 条`}</p>
        {status.failedDeliveries > 0 && <p>请在“待看结果”查看完整内容，管理员可检查投递记录。</p>}
        <div className={styles.actions}>
          <button disabled={busy} onClick={() => void perform(async () => {
            await assistantRequest("feishu/notifications", "PUT", { enabled: !status.notifications });
          })}>{status.notifications ? "关闭飞书通知" : "开启飞书通知"}</button>
          {!confirmUnbind ? <button disabled={busy} onClick={() => setConfirmUnbind(true)}>解除绑定</button>
            : <><button disabled={busy} onClick={() => void perform(async () => {
              await assistantRequest("feishu/binding", "DELETE"); setCode(null); setConfirmUnbind(false); setNotice("已解除绑定，后续通知不会再发往原账号。");
            })}>确认解除绑定</button><button disabled={busy} onClick={() => setConfirmUnbind(false)}>保留绑定</button></>}
        </div>
        <p className={styles.muted}>私聊发送“事项”查看列表，或发送“新建 标题”并换行填写要求。需要暂停、执行或补充时，发送对应命令和事项标题。</p>
      </> : <>
        <h3>绑定你的 KAI 账号</h3>
        <p>生成一次性绑定码，将下方内容私聊发送给 KAI 机器人。绑定码 10 分钟内有效，请只发送给专用机器人。</p>
        <button className={styles.primary} disabled={busy || (code !== null && now < Date.parse(code.expiresAt) - 570000)} onClick={() => void perform(async () => {
          setCode(await assistantRequest<Code>("feishu/binding-code", "POST")); setNow(Date.now());
        })}>{code ? "重新生成绑定码" : "生成绑定码"}</button>
        {validCode && <>
          <label>发送给 KAI 机器人<input readOnly value={`绑定 ${code.code}`} onFocus={event => event.target.select()} /></label>
          <button disabled={busy} onClick={() => void perform(async () => { await navigator.clipboard.writeText(`绑定 ${code.code}`); setNotice("已复制，可粘贴到 KAI 机器人私聊。"); })}>复制绑定消息</button>
          <p className={styles.muted}>有效至 {date(code.expiresAt)}。重新生成后，旧绑定码立即失效。</p>
        </>}
        {code && !validCode && <p>绑定码已过期，请重新生成。</p>}
      </>}
    </>}
    {status && !status.enabled && <>
      <ol><li>在飞书开放平台创建企业自建应用，命名为 KAI，并启用机器人。</li>
        <li>开通单聊消息读取和机器人发信权限，订阅消息接收事件与卡片回调。</li>
        <li>管理员在服务器私有配置中注入 App ID、App Secret 和 KAI 访问地址，启动长连接接收进程。</li>
        <li>在飞书后台选择长连接并发布应用，然后在本页生成绑定码。</li></ol>
      <p>App Secret 通过服务器私有配置注入，无需填写在聊天或普通表单中。</p>
      <a href="https://open.feishu.cn/app" target="_blank" rel="noreferrer">打开飞书开放平台 ↗</a>
    </>}
  </section>;
}
