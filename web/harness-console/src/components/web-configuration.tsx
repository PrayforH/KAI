"use client";
import { useEffect, useState } from "react";
import { SecretInput } from "./secret-input";

type Config = {enabled:boolean;effectiveEnabled:boolean;platformEnabled:boolean;provider:"platform"|"minimax"|"tavily";platformProvider:string;credentialConfigured:boolean;personalKeyConfigured:boolean};
export function WebConfiguration() {
  const [config,setConfig]=useState<Config|null>(null);
  const [key,setKey]=useState("");
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  useEffect(()=>{let active=true;fetch('/api/studio/web-configuration',{cache:'no-store'}).then(async r=>{if(!r.ok)throw new Error('联网配置暂时不可用');return r.json() as Promise<Config>}).then(c=>{if(active)setConfig(c)}).catch(e=>{if(active)setMessage(e.message)});return()=>{active=false}},[]);
  async function save(next:Config, clearKey=false, keyToSave="") {
    setBusy(true);setMessage("");
    try {
      const r=await fetch('/api/studio/web-configuration',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:next.enabled,provider:next.provider,...(!clearKey&&keyToSave.trim()?{apiKey:keyToSave.trim()}:{}),clearKey})});
      if(!r.ok)throw new Error('联网配置未能保存，请稍后重试');
      setConfig(await r.json());setKey("");setMessage('联网配置已保存');window.dispatchEvent(new Event('harness-web-configuration'));
    } catch(e){setMessage(e instanceof Error?e.message:'保存失败')}finally{setBusy(false)}
  }
  return <section className="settings-web-configuration" aria-label="联网配置"><h2>联网配置</h2><p>控制本账号的 WebSearch / WebFetch。已有 MCP 连接仍单独管理。</p>
    {config&&<>
      <label className="settings-preference-toggle"><input type="checkbox" checked={config.enabled} disabled={busy||!config.platformEnabled} onChange={e=>void save({...config,enabled:e.target.checked})}/><span><strong>允许公开联网</strong><small>关闭后停止这两项工具的后续调用，不改动智能体已发布版本。</small></span></label>
      {!config.platformEnabled&&<p>当前环境已停用内置联网。</p>}
      <label className="settings-preference-row"><span><strong>搜索服务</strong><small>网页读取不需要搜索服务密钥。</small></span><select aria-label="联网搜索服务" disabled={busy} value={config.provider} onChange={e=>{setKey("");void save({...config,provider:e.target.value as Config['provider']})}}><option value="platform">平台默认（{config.platformProvider}）</option><option value="minimax">MiniMax</option><option value="tavily">Tavily</option></select></label>
      <label className="settings-web-key"><span>搜索 API Key</span><SecretInput aria-label="搜索 API Key" revealLabel="搜索 API Key" value={key} autoComplete="new-password" onChange={e=>setKey(e.target.value)} placeholder={config.personalKeyConfigured?'已保存个人密钥，输入可替换':config.credentialConfigured?'正在使用平台密钥，可填写个人密钥':'填写该搜索服务的 API Key'}/><small>密钥加密保存，仅供当前账号使用；平台密钥不在页面回显。</small></label>
      <div className="settings-web-actions"><button type="button" disabled={busy||!key.trim()} onClick={()=>void save(config,false,key)}>保存密钥</button>{config.personalKeyConfigured&&<button type="button" disabled={busy} onClick={()=>{setKey("");void save(config,true)}}>移除个人密钥</button>}</div>
      <p>实际可用能力 = 个人联网开关开启 + 智能体已勾选对应工具。开启开关不会自动为智能体添加工具；工具配置仍需保存并发布。</p>
    </>}
    {!config&&!message&&<p>正在读取联网配置…</p>}{message&&<p role="status">{message}</p>}
  </section>
}
