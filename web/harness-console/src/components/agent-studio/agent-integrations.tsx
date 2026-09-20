"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { studioClient, type ApiAgentDraft, type StudioCapabilities } from "../../lib/studio-client";
import styles from "./agent-workspace.module.css";

export function AgentIntegrations({ draftId }: { draftId: string }) {
  const [draft, setDraft] = useState<ApiAgentDraft>();
  const [catalog, setCatalog] = useState<StudioCapabilities>();
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("attached");
  const [revision, refresh] = useState(0);
  useEffect(() => {
    let active = true; setDraft(undefined); setCatalog(undefined); setError("");
    void Promise.all([studioClient.getDraft(draftId), studioClient.capabilities()]).then(([d,c]) => { if (active) { setDraft(d); setCatalog(c); } }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [draftId, revision]);
  if (error) return <div role="alert"><p>集成目录读取失败：{error}</p><button onClick={() => refresh(v => v + 1)}>重新加载</button></div>;
  if (!draft || !catalog) return <p role="status">正在读取智能体配置和可用能力…</p>;
  const items = [
    ...catalog.mcpServers.map(item => ({ id: item.reference, label: item.label, description: item.description, type: "MCP", attached: draft.spec.mcpServers.includes(item.reference), enabled: item.enabled, tools: item.tools, risk: item.risk, permission: item.readOnly ? "只读能力" : "包含写操作", credential: item.authMode === "none" ? "无需认证" : "需在连接管理中检查授权" })),
    ...catalog.builtinTools.map(item => ({ id: item.name, label: item.label, description: item.description, type: "内置工具", attached: draft.spec.builtinTools.includes(item.name), enabled: true, tools: [item.name], risk: item.risk, permission: item.approvalBehavior, credential: "沿用运行环境" })),
  ];
  const missing = draft.spec.mcpServers.filter(id => !catalog.mcpServers.some(item => item.reference === id));
  const filtered = items.filter(item => (scope !== "attached" || item.attached) && `${item.label} ${item.description} ${item.tools.join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <section>
    <div className={styles.toolbar}><label>搜索集成<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="名称、用途或工具" /></label><label>范围<select value={scope} onChange={e => setScope(e.target.value)}><option value="attached">本智能体已声明</option><option value="all">全部可见能力</option></select></label><Link href={`/studio/agents?draft=${encodeURIComponent(draftId)}`}>配置能力 →</Link></div>
    <p>配置修订 r{draft.revision} · 已声明不代表授权或预检已通过。修改后在 Playground 验证，并发布需要使用的新版本。</p>
    {!!missing.length && <p role="alert">以下声明当前不可见或已停用：{missing.join("、")}。请检查能力配置。</p>}
    <div className={styles.integrationList}>{filtered.map(item => <article key={`${item.type}:${item.id}`}><div><span className={styles.scope}>{item.type}</span><h3>{item.label}</h3><p>{item.description}</p><small>{item.attached ? "已声明" : "可添加"} · {item.enabled ? "目录已启用" : "目录已停用"} · 风险 {item.risk}</small></div><div><p>{item.permission} · {item.credential}</p><details><summary>{item.tools.length} 项能力</summary><ul>{item.tools.map(tool => <li key={tool}><code>{tool}</code></li>)}</ul></details></div></article>)}</div>
    {!filtered.length && <div className={styles.empty}><h3>没有匹配的集成</h3><p>可切换到全部可见能力，或在构建中为智能体配置工具。</p></div>}
  </section>;
}
