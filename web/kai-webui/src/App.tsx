import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive, ArrowDownToLine, ArrowUp, Bot, Check, ChevronDown, CircleAlert, CircleHelp,
  File, Folder, History, LoaderCircle, LogOut, Menu, MessageSquareText, Moon, MoreHorizontal,
  PanelLeftClose, PanelLeftOpen, Paperclip, RotateCcw, Search, Sparkles, Square,
  Sun, Trash2, X,
} from "lucide-react";
import { applyActivityPatch } from "./protocol/agui-adapter";
import { authStore, kaiClient } from "./protocol/client";
import { randomId } from "./protocol/id";
import type { Agent, Approval, Artifact, ContextOverview, InputArtifact, KaiEvent, Message, RunHandle, Thread, ToolCall, User } from "./protocol/types";

type Theme = "light" | "dark";

function initials(name: string) { return name.trim().slice(0, 1).toUpperCase() || "K"; }
function relativeTime(value: string) {
  const delta = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(delta) || delta < 60_000) return "刚刚";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`;
  return `${Math.floor(delta / 86_400_000)} 天前`;
}
function fileSize(value?: number) { if (!value) return ""; return value < 1024 * 1024 ? `${Math.ceil(value / 1024)} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`; }
function terminal(status: string) { return ["succeeded", "failed", "cancelled", "timed_out", "rejected", "idle"].includes(status); }

function Brand({ compact = false }: { compact?: boolean }) {
  return <div className="brand" aria-label="KAI"><span className="brand-mark">K</span>{!compact && <span className="brand-name">KAI</span>}</div>;
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { onLogin(await kaiClient.login(email, password)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "登录失败"); }
    finally { setBusy(false); }
  }
  return <main className="login-page"><section className="login-card">
    <Brand /><div className="login-heading"><h1>欢迎回来</h1><p>登录后继续你的 Agent 工作。</p></div>
    <form onSubmit={submit}>
      <label><span>邮箱</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@company.com" required autoFocus /></label>
      <label><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="输入密码" required /></label>
      {error && <div className="login-error" role="alert">{error}</div>}
      <button className="login-submit" disabled={busy}>{busy && <LoaderCircle className="spin" size={17} />}登录</button>
    </form><p className="login-footnote">KAI · Harness connected</p>
  </section></main>;
}

function Sidebar({ collapsed, threads, agents, selectedId, user, loading, error, archived, onOpenSearch, onToggle, onNew, onSelect, onArchiveView, onRetry, onLogout }: {
  collapsed: boolean; threads: Thread[]; agents: Agent[]; selectedId: string | null; user: User; loading: boolean; error: string; archived: boolean;
  onOpenSearch: () => void; onToggle: () => void; onNew: () => void; onSelect: (thread: Thread) => void; onArchiveView: () => void; onRetry: () => void; onLogout: () => void;
}) {
  const projects = useMemo(() => {
    const values = new Map<string, { id: string; name: string; threads: Thread[] }>();
    if (!archived) {
      for (const item of agents) {
        const id = item.spaceId || `agent:${item.name}`;
        values.set(id, { id, name: item.spaceName || item.displayName, threads: [] });
      }
    }
    for (const thread of threads) {
      const bound = agents.find((item) => item.name === thread.agentName && item.version === thread.agentVersion && (item.spaceId || "") === (thread.spaceId || ""));
      const id = thread.spaceId || `agent:${thread.agentName}`;
      const project = values.get(id) || { id, name: bound?.spaceName || bound?.displayName || thread.agentName, threads: [] };
      project.threads.push(thread);
      values.set(id, project);
    }
    return [...values.values()];
  }, [agents, archived, threads]);

  return <aside className={`sidebar ${collapsed ? "sidebar-collapsed" : ""}`}>
    <div className="sidebar-brand-row"><button className="brand-button" onClick={onNew}><Brand compact={collapsed} /></button><button className="icon-button sidebar-toggle" onClick={onToggle} aria-label={collapsed ? "展开侧栏" : "收起侧栏"}>{collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={17} />}</button></div>
    <nav className="sidebar-primary" aria-label="主要操作">
      <button onClick={onNew} title="新建任务"><MessageSquareText size={17} />{!collapsed && <span>新建任务</span>}</button>
      <button onClick={onOpenSearch} title="搜索"><Search size={17} />{!collapsed && <span>搜索</span>}</button>
    </nav>
    <div className="workspace-region">
      {!collapsed ? <>
        <div className="section-title"><span>{archived ? "已归档项目" : "项目"}</span></div>
        <div className="project-list">
          {loading && [0, 1, 2].map((item) => <div className="session-skeleton" key={item} />)}
          {!loading && error && <div className="sidebar-state"><CircleAlert size={17} /><span>{error}</span><button onClick={onRetry}>重试</button></div>}
          {!loading && !error && !projects.length && <div className="sidebar-state"><History size={17} /><span>{archived ? "还没有归档任务" : "还没有项目"}</span></div>}
          {!loading && !error && projects.map((project) => <section className="project-block" key={project.id}>
            <div className="project-heading"><Folder size={15} /><strong>{project.name}</strong></div>
            {project.threads.length ? <div className="session-list">{project.threads.map((thread) => <button key={thread.id} className={`session-row ${selectedId === thread.id ? "active" : ""}`} onClick={() => onSelect(thread)}>
              <span className="session-copy"><span className="session-title">{thread.title || "新任务"}</span><span className="session-meta"><span>{relativeTime(thread.updatedAt)}</span></span></span>
              {!terminal(thread.status) ? <span className="running-dot" /> : thread.approval ? <CircleAlert className="approval-dot" size={14} /> : <MoreHorizontal className="session-more" size={15} />}
            </button>)}</div> : <div className="project-empty">暂无任务</div>}
          </section>)}
        </div>
      </> : <><button className="rail-button" title="项目"><Folder size={18} /></button><button className="rail-button" title="归档" onClick={onArchiveView}><Archive size={18} /></button></>}
    </div>
    <div className="sidebar-footer">
      <button className={`footer-row ${archived ? "active" : ""}`} onClick={onArchiveView} title="已归档"><Archive size={18} />{!collapsed && <span>{archived ? "返回项目" : "已归档"}</span>}</button>
      <div className="account-row"><span className="avatar">{initials(user.name)}</span>{!collapsed && <><span className="account-copy"><strong>{user.name}</strong><small>{user.email}</small></span><button onClick={onLogout} className="account-logout" title="退出登录"><LogOut size={16} /></button></>}</div>
    </div>
  </aside>;
}

function SearchPalette({ open, query, loading, agents, threads, onQuery, onClose, onThread, onProject }: {
  open: boolean; query: string; loading: boolean; agents: Agent[]; threads: Thread[]; onQuery: (value: string) => void; onClose: () => void; onThread: (thread: Thread) => void; onProject: (agent: Agent) => void;
}) {
  const keyword = query.trim().toLowerCase();
  const projects = [...new Map(agents.map((agent) => [agent.spaceId || `agent:${agent.name}`, agent])).values()]
    .filter((agent) => !keyword || `${agent.spaceName || agent.displayName} ${agent.name}`.toLowerCase().includes(keyword));
  const tasks = threads.filter((thread) => !keyword || `${thread.title} ${thread.agentName}`.toLowerCase().includes(keyword)).slice(0, 12);
  if (!open) return null;
  return <div className="search-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="search-palette" role="dialog" aria-modal="true" aria-label="搜索项目和任务">
      <label className="search-palette-input"><Search size={17} /><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="搜索项目或任务" autoFocus /><button onClick={onClose} aria-label="关闭"><X size={15} /></button></label>
      <div className="search-results">
        {loading && <div className="search-loading"><LoaderCircle className="spin" size={15} />正在载入归档任务</div>}
        {projects.length > 0 && <section><h3>项目</h3>{projects.map((agent) => <button key={agent.spaceId || agent.name} onClick={() => onProject(agent)}><span className="search-result-icon"><Folder size={15} /></span><span><strong>{agent.spaceName || agent.displayName}</strong><small>{agent.scope === "team" ? "团队项目" : agent.model || agent.modelRoute || "个人项目"}</small></span></button>)}</section>}
        {tasks.length > 0 && <section><h3>任务</h3>{tasks.map((thread) => <button key={thread.id} onClick={() => onThread(thread)}><span className="search-result-icon"><MessageSquareText size={15} /></span><span><strong>{thread.title || "新任务"}</strong><small>{thread.agentName} · {thread.archived ? "已归档" : relativeTime(thread.updatedAt)}</small></span></button>)}</section>}
        {!loading && !projects.length && !tasks.length && <div className="search-empty"><Search size={18} /><span>没有匹配的项目或任务</span></div>}
      </div>
    </section>
  </div>;
}

function AgentPicker({ agents, value, disabled, onChange }: { agents: Agent[]; value?: Agent; disabled?: boolean; onChange: (agent: Agent) => void }) {
  return <label className="select-control"><Bot size={14} /><select value={value ? `${value.name}@${value.version}:${value.spaceId || ""}` : ""} disabled={disabled} onChange={(event) => { const next = agents.find((agent) => `${agent.name}@${agent.version}:${agent.spaceId || ""}` === event.target.value); if (next) onChange(next); }}>
    {!agents.length && <option value="">无可用 Agent</option>}
    {agents.map((agent) => <option key={`${agent.name}@${agent.version}:${agent.spaceId || ""}`} value={`${agent.name}@${agent.version}:${agent.spaceId || ""}`}>{agent.displayName} · {agent.model || agent.modelRoute || agent.version}</option>)}
  </select><ChevronDown size={12} /></label>;
}

function Composer({ value, busy, uploading, files, agents, agent, disabled, onAgent, onChange, onSubmit, onCancel, onUpload, onRemove }: {
  value: string; busy: boolean; uploading: boolean; files: InputArtifact[]; agents: Agent[]; agent?: Agent; disabled?: boolean;
  onAgent: (agent: Agent) => void; onChange: (value: string) => void; onSubmit: (currentValue: string) => void; onCancel: () => void; onUpload: (files: FileList) => void; onRemove: (id: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  function submitCurrent() { onSubmit(textarea.current?.value ?? value); }
  function keyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submitCurrent(); } }
  return <form className="composer-wrap" onSubmit={(event) => { event.preventDefault(); submitCurrent(); }}>
    {files.length > 0 && <div className="attachment-strip">{files.map((file) => <span className="attachment-chip" key={file.id}><File size={13} /><span>{file.name}</span><small>{fileSize(file.sizeBytes)}</small><button type="button" onClick={() => onRemove(file.id)} aria-label={`移除 ${file.name}`}><X size={12} /></button></span>)}</div>}
    <div className="composer-card"><textarea ref={textarea} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={keyDown} placeholder={disabled ? "Agent 正在加载，你仍可先输入任务……" : "告诉 KAI 你想完成什么……"} rows={2} disabled={busy} />
      <div className="composer-controls"><div className="composer-left"><input ref={input} className="visually-hidden" type="file" multiple onChange={(event) => { if (event.target.files) onUpload(event.target.files); event.target.value = ""; }} /><button type="button" className="attach-button" onClick={() => input.current?.click()} disabled={busy || uploading} title="添加附件">{uploading ? <LoaderCircle className="spin" size={15} /> : <Paperclip size={15} />}</button><span className="mode-label"><Sparkles size={13} />深度任务</span></div>
        <div className="composer-right"><AgentPicker agents={agents} value={agent} disabled={busy} onChange={onAgent} />{busy ? <button type="button" className="stop-button" onClick={onCancel} title="停止"><Square size={13} fill="currentColor" /></button> : <button type="button" className="send-button" onClick={(event) => { event.preventDefault(); submitCurrent(); }} title="发送"><ArrowUp size={17} strokeWidth={2.4} /></button>}</div></div>
    </div><p className="composer-note">运行由 Harness 管理；工具调用和重要产物会在会话中显示。</p>
  </form>;
}

function ToolList({ tools }: { tools: ToolCall[] }) {
  const visible = tools.filter((tool) => !tool.name.startsWith("harness_"));
  if (!visible.length) return null;
  return <details className="tool-list"><summary><span className={visible.some((tool) => tool.status === "running") ? "pulse-dot" : "done-dot"} />{visible.some((tool) => tool.status === "running") ? "正在使用工具" : `已完成 ${visible.length} 个工具调用`}<ChevronDown size={13} /></summary>
    <div>{visible.map((tool) => <div className="tool-row" key={tool.id}><span>{tool.name}</span><small>{tool.status === "running" ? "运行中" : tool.status === "error" ? "失败" : "完成"}</small>{tool.arguments && <code>{tool.arguments}</code>}</div>)}</div>
  </details>;
}

function ActivityView({ message }: { message: Message }) {
  const activity = message.activity;
  if (!activity?.items.length) return null;
  const recent = activity.items.slice(-4);
  return <details className="activity-view" open={activity.status === "running"}><summary><span className={activity.status === "running" ? "pulse-dot" : "done-dot"} />{activity.status === "running" ? recent.at(-1)?.title || "正在执行" : `完成 ${activity.items.length} 个步骤`}<ChevronDown size={13} /></summary>
    <ol>{recent.map((item) => <li key={item.id}><span>{item.title}</span>{item.summary && <small>{item.summary}</small>}</li>)}</ol>
  </details>;
}

function Artifacts({ items, onDownload }: { items: Artifact[]; onDownload: (item: Artifact) => void }) {
  if (!items.length) return null;
  return <div className="artifact-grid">{items.map((item) => <button className="artifact-card" key={item.id} onClick={() => onDownload(item)}><span className="artifact-icon"><File size={17} /></span><span><strong>{item.name}</strong><small>{item.mediaType} {fileSize(item.sizeBytes)}</small></span><ArrowDownToLine size={15} /></button>)}</div>;
}

function ApprovalCard({ approval, busy, onDecision }: { approval: Approval; busy: boolean; onDecision: (decision: "approved" | "rejected") => void }) {
  return <section className="approval-card"><div className="approval-heading"><span><CircleAlert size={16} /></span><div><strong>{approval.toolName ? `${approval.toolName} 请求确认` : "需要你的确认"}</strong><p>{approval.reason}</p></div>{approval.risk && <em>{approval.risk}</em>}</div>
    {approval.arguments && Object.keys(approval.arguments).length > 0 && <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>}
    <div className="approval-actions"><button onClick={() => onDecision("rejected")} disabled={busy}>拒绝</button><button className="approve" onClick={() => onDecision("approved")} disabled={busy}>{busy && <LoaderCircle className="spin" size={14} />}允许并继续</button></div>
  </section>;
}

function MessageView({ messages, approval, approvalBusy, onDecision, onDownload }: { messages: Message[]; approval?: Approval; approvalBusy: boolean; onDecision: (decision: "approved" | "rejected") => void; onDownload: (item: Artifact) => void }) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, approval]);
  return <div className="conversation-scroll"><div className="conversation-list">
    {messages.map((message) => <article key={message.id} className={`message ${message.role}`}>
      {message.role === "assistant" && <div className="assistant-mark"><Sparkles size={15} /></div>}
      <div className="message-body"><div className="message-role">{message.role === "user" ? "你" : "KAI"}</div><ActivityView message={message} /><ToolList tools={message.toolCalls || []} />
        {message.content ? <div className="message-content">{message.content}</div> : message.role === "assistant" && <div className="thinking"><span /><span /><span /></div>}
        <Artifacts items={message.artifacts || []} onDownload={onDownload} />
      </div>
    </article>)}
    {approval && <ApprovalCard approval={approval} busy={approvalBusy} onDecision={onDecision} />}<div ref={bottom} />
  </div></div>;
}

function ContextChip({ context, onRebase }: { context?: ContextOverview; onRebase: () => void }) {
  if (!context || context.percentage == null) return null;
  const warn = (context.percentage || 0) >= 70;
  return <button className={`context-chip ${warn ? "warn" : ""}`} onClick={context.rebaseSupported ? onRebase : undefined} title={`${context.totalTokens?.toLocaleString() || 0} / ${context.maxTokens?.toLocaleString() || 0} tokens`}><span className="context-ring" style={{ "--context": `${Math.min(context.percentage, 100) * 3.6}deg` } as React.CSSProperties} />{context.percentage.toFixed(0)}%{context.rebaseSupported && <RotateCcw size={12} />}</button>;
}

export function App() {
  const [user, setUser] = useState<User | null>(() => authStore.user());
  const [authChecking, setAuthChecking] = useState(Boolean(authStore.token()));
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem("kai_theme") as Theme) || "light");
  const [collapsed, setCollapsed] = useState(() => window.innerWidth <= 760);
  const [threads, setThreads] = useState<Thread[]>([]); const [threadsLoading, setThreadsLoading] = useState(false); const [threadsError, setThreadsError] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]); const [agent, setAgent] = useState<Agent>();
  const [selected, setSelected] = useState<Thread | null>(null); const [messages, setMessages] = useState<Message[]>([]); const [context, setContext] = useState<ContextOverview>();
  const [draft, setDraft] = useState(""); const [busy, setBusy] = useState(false); const [transport, setTransport] = useState<"connected" | "recovering">("connected");
  const [notice, setNotice] = useState(""); const [files, setFiles] = useState<InputArtifact[]>([]); const [uploading, setUploading] = useState(false);
  const [approval, setApproval] = useState<Approval>(); const [approvalBusy, setApprovalBusy] = useState(false); const [archived, setArchived] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false); const [searchQuery, setSearchQuery] = useState(""); const [searchThreads, setSearchThreads] = useState<Thread[]>([]); const [searchLoading, setSearchLoading] = useState(false);
  const handleRef = useRef<RunHandle>(); const controllerRef = useRef<AbortController>();

  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("kai_theme", theme); }, [theme]);
  useEffect(() => {
    const unauthorized = () => { setUser(null); setAuthChecking(false); };
    window.addEventListener("kai:unauthorized", unauthorized);
    if (authStore.token()) kaiClient.me().then((profile) => setUser(profile)).catch(() => undefined).finally(() => setAuthChecking(false));
    return () => window.removeEventListener("kai:unauthorized", unauthorized);
  }, []);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); void openSearch(); }
      if (event.key === "Escape") setSearchOpen(false);
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);

  async function loadThreads(nextArchived = archived) {
    setThreadsLoading(true); setThreadsError("");
    try { setThreads(await kaiClient.threads(nextArchived)); }
    catch (reason) { setThreads([]); setThreadsError(reason instanceof Error ? reason.message : "会话加载失败"); }
    finally { setThreadsLoading(false); }
  }

  useEffect(() => {
    if (authChecking || !user || !authStore.token()) return;
    void loadThreads(archived);
  }, [authChecking, user, archived]);

  useEffect(() => {
    if (authChecking || !user || !authStore.token()) return;
    kaiClient.agents().then((rows) => {
      const effective = rows.map((item) => ({ ...item, modelRoute: "deepseek-v4-flash", model: "deepseek-v4-flash" }));
      setAgents(effective);
      setAgent((current) => effective.find((item) => item.name === current?.name && item.version === current?.version && item.spaceId === current?.spaceId) || effective[0]);
      void kaiClient.modelRoutes().then((routes) => {
        const flash = routes.find((route) => route.id === "deepseek-v4-flash" && route.enabled);
        if (!flash) setNotice("Flash 模型暂不可用，请联系管理员检查模型路由");
      }).catch(() => undefined);
    }).catch((reason) => { if (authStore.token()) setNotice(reason instanceof Error ? reason.message : "Agent 列表加载失败"); });
  }, [authChecking, user]);

  const title = selected?.title || "新会话";

  async function openSearch() {
    setSearchOpen(true); setSearchLoading(true);
    try {
      const [active, archivedItems] = await Promise.all([kaiClient.threads(false), kaiClient.threads(true)]);
      setSearchThreads([...active, ...archivedItems]);
    } catch (reason) { if (authStore.token()) setNotice(reason instanceof Error ? reason.message : "搜索数据加载失败"); }
    finally { setSearchLoading(false); }
  }

  async function selectThread(thread: Thread) {
    if (busy) return;
    setSelected(thread); setNotice(""); setMessages([]); setApproval(thread.approval); setContext(undefined);
    const bound = agents.find((item) => item.name === thread.agentName && item.version === thread.agentVersion && (item.spaceId || "") === (thread.spaceId || "")); if (bound) setAgent(bound);
    setBusy(true);
    try {
      const [history, overview] = await Promise.all([kaiClient.history(thread.id), kaiClient.context(thread.id).catch(() => undefined)]);
      setMessages(history.messages); setContext(overview); setApproval(thread.approval);
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : "会话加载失败"); }
    finally { setBusy(false); }
  }

  function newSession() { if (busy) return; setSearchOpen(false); setSelected(null); setMessages([]); setContext(undefined); setApproval(undefined); setFiles([]); setDraft(""); setNotice(""); setArchived(false); }

  function applyEvent(event: KaiEvent, pendingId: string) {
    if (event.kind === "run.error") { setNotice(event.message); return; }
    if (event.kind === "run.finished") return;
    setMessages((current) => {
      const latestAssistant = () => [...current].reverse().find((item) => item.role === "assistant");
      if (event.kind === "message.started") {
        if (current.some((item) => item.id === event.messageId)) return current;
        return current.map((item) => item.id === pendingId ? { ...item, id: event.messageId } : item);
      }
      if (event.kind === "message.delta") {
        const target = current.some((item) => item.id === event.messageId) ? event.messageId : latestAssistant()?.id;
        return current.map((item) => item.id === target ? { ...item, content: item.content + event.delta } : item);
      }
      if (event.kind === "tool.started") {
        const target = latestAssistant()?.id; if (!target) return current;
        return current.map((item) => item.id === target ? { ...item, toolCalls: [...(item.toolCalls || []), { id: event.toolCallId, name: event.name, arguments: "", status: "running" }] } : item);
      }
      if (event.kind === "tool.arguments" || event.kind === "tool.result" || event.kind === "tool.finished") {
        return current.map((item) => {
          const call = item.toolCalls?.find((tool) => tool.id === event.toolCallId); if (!call) return item;
          const toolCalls = item.toolCalls!.map((tool) => tool.id !== event.toolCallId ? tool : event.kind === "tool.arguments" ? { ...tool, arguments: tool.arguments + event.delta } : event.kind === "tool.result" ? { ...tool, result: event.content, status: "complete" as const } : { ...tool, status: "complete" as const });
          const finished = toolCalls.find((tool) => tool.id === event.toolCallId);
          const artifacts = [...(item.artifacts || [])];
          if (event.kind === "tool.finished" && finished?.name === "harness_present_artifact") { const parsed = parseJson(finished.arguments); if (parsed.artifact_id) artifacts.push({ id: String(parsed.artifact_id), runId: String(parsed.run_id), name: String(parsed.name), mediaType: String(parsed.media_type), sizeBytes: typeof parsed.size_bytes === "number" ? parsed.size_bytes : undefined }); }
          if (event.kind === "tool.finished" && finished?.name === "harness_request_approval") { const parsed = parseJson(finished.arguments); if (parsed.approval_id) queueMicrotask(() => setApproval({ id: String(parsed.approval_id), runId: String(parsed.run_id), toolCallId: String(parsed.tool_call_id), status: "pending", reason: String(parsed.reason || "需要确认后继续"), toolName: typeof parsed.tool_name === "string" ? parsed.tool_name : undefined, arguments: record(parsed.argument_summary), risk: typeof parsed.risk === "string" ? parsed.risk : undefined })); }
          return { ...item, toolCalls, artifacts };
        });
      }
      if (event.kind === "activity.snapshot") { const target = latestAssistant()?.id; return current.map((item) => item.id === target ? { ...item, activity: event.activity } : item); }
      if (event.kind === "activity.delta") { const target = latestAssistant()?.id; return current.map((item) => item.id === target ? { ...item, activity: applyActivityPatch(item.activity, event.patch) } : item); }
      return current;
    });
  }

  async function send(currentValue = draft) {
    const typedPrompt = currentValue.trim();
    if (busy) return;
    if (!typedPrompt && !files.length) { setNotice("请先输入任务或添加附件"); return; }
    if (uploading) { setNotice("附件仍在上传，请稍候再发送"); return; }
    const activeAgent = agent ?? agents[0];
    if (!activeAgent) { setNotice("当前账户没有可运行的 Agent"); return; }
    const prompt = typedPrompt || `请分析并处理所附文件：${files.map((file) => file.name).join("、")}`;
    const history = messages; const threadId = selected?.id || randomId(); const pendingId = `assistant-${randomId()}`;
    const userMessage: Message = { id: `user-${randomId()}`, role: "user", content: prompt };
    setMessages([...history, userMessage, { id: pendingId, role: "assistant", content: "", toolCalls: [], artifacts: [] }]); setDraft(""); setBusy(true); setNotice(""); setApproval(undefined);
    const controller = new AbortController(); controllerRef.current = controller;
    if (!selected) { const created: Thread = { id: threadId, sessionId: "", title: prompt.slice(0, 36), agentName: activeAgent.name, agentVersion: activeAgent.version, ownerUserId: activeAgent.ownerUserId, spaceId: activeAgent.spaceId, status: "running", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() }; setSelected(created); setThreads((current) => [created, ...current]); }
    try {
      await kaiClient.streamRun(activeAgent, threadId, history, prompt, files, { onHandle: (handle) => { handleRef.current = handle; }, onTransportState: setTransport, onEvent: (event) => applyEvent(event, pendingId) }, controller.signal);
      setFiles([]); await loadThreads(false); const overview = await kaiClient.context(threadId).catch(() => undefined); setContext(overview);
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setNotice(reason instanceof Error ? reason.message : "运行连接中断");
    } finally { setBusy(false); handleRef.current = undefined; controllerRef.current = undefined; setTransport("connected"); }
  }

  async function cancel() { const handle = handleRef.current; if (!handle) return; try { await kaiClient.cancel(handle); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "停止失败"); } finally { controllerRef.current?.abort(new DOMException("已停止", "AbortError")); setBusy(false); } }
  async function upload(selectedFiles: FileList) { setUploading(true); setNotice(""); try { const uploaded = await Promise.all(Array.from(selectedFiles).map((file) => kaiClient.upload(file))); setFiles((current) => [...current, ...uploaded]); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "附件上传失败"); } finally { setUploading(false); } }
  async function decide(decision: "approved" | "rejected") { if (!approval) return; setApprovalBusy(true); try { await kaiClient.approve(approval.id, decision); setApproval(undefined); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "审批提交失败"); } finally { setApprovalBusy(false); } }
  async function archiveCurrent() { if (!selected) return; try { await kaiClient.archive(selected.id, !archived); setSelected(null); setMessages([]); setContext(undefined); await loadThreads(archived); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "归档失败"); } }
  async function rebase() { if (!selected || !context?.rebaseSupported) return; try { await kaiClient.rebaseContext(selected.id); setContext(await kaiClient.context(selected.id)); setNotice("上下文已压缩并建立恢复点"); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "上下文整理失败"); } }

  if (authChecking) return <main className="boot-page"><Brand /><span className="boot-line" /></main>;
  if (!user || !authStore.token()) return <Login onLogin={setUser} />;

  return <div className="app-shell">
    <a className="skip-link" href="#conversation">跳到会话</a>
    <Sidebar collapsed={collapsed} threads={threads} agents={agents} selectedId={selected?.id || null} user={user} loading={threadsLoading} error={threadsError} archived={archived} onOpenSearch={() => void openSearch()} onToggle={() => setCollapsed((value) => !value)} onNew={newSession} onSelect={selectThread} onArchiveView={() => { setArchived((value) => !value); setSelected(null); setMessages([]); }} onRetry={() => void loadThreads()} onLogout={() => void kaiClient.logout().finally(() => setUser(null))} />
    <SearchPalette open={searchOpen} query={searchQuery} loading={searchLoading} agents={agents} threads={searchThreads} onQuery={setSearchQuery} onClose={() => { setSearchOpen(false); setSearchQuery(""); }} onThread={(thread) => { setSearchOpen(false); setSearchQuery(""); setArchived(Boolean(thread.archived)); void selectThread(thread); }} onProject={(nextAgent) => { setAgent(nextAgent); newSession(); }} />
    <main className="main-column" id="conversation"><header className="topbar"><div className="mobile-brand"><button className="icon-button" onClick={() => setCollapsed(false)}><Menu size={18} /></button><Brand /></div>
      <div className="thread-heading"><h1>{title}</h1>{selected && <span className={`run-state ${busy ? "running" : ""}`}>{busy ? transport === "recovering" ? "正在恢复连接" : "运行中" : <><Check size={12} /> 已同步</>}</span>}</div>
      <div className="topbar-actions">{selected && <button className="icon-button" onClick={archiveCurrent} title={archived ? "恢复会话" : "归档会话"}>{archived ? <RotateCcw size={16} /> : <Trash2 size={16} />}</button>}<ContextChip context={context} onRebase={rebase} /><button className="icon-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} title="切换主题">{theme === "light" ? <Moon size={17} /> : <Sun size={17} />}</button><button className="icon-button" title="KAI Web Session Protocol v1"><CircleHelp size={17} /></button></div>
    </header>
    <section className={`content ${messages.length ? "has-messages" : "empty"}`}>
      {messages.length ? <MessageView messages={messages} approval={approval} approvalBusy={approvalBusy} onDecision={decide} onDownload={(item) => void kaiClient.downloadArtifact(item)} /> : <div className="hero"><div className="hero-kicker">KAI / HARNESS</div><div className="hero-title"><span className="hero-mark"><Sparkles size={19} /></span><h2>把目标交给 Agent</h2></div><p>{agent ? `${agent.displayName} 已连接到 ${agent.model || agent.modelRoute || "默认模型"}` : agents.length ? "选择一个 Agent 开始" : "当前账户没有可运行的 Agent"}</p></div>}
      {notice && <div className="notice" role="status"><span>{notice}</span><button onClick={() => setNotice("")}><X size={13} /></button></div>}
      <Composer value={draft} busy={busy} uploading={uploading} files={files} agents={agents} agent={agent} disabled={!agent} onAgent={setAgent} onChange={setDraft} onSubmit={send} onCancel={cancel} onUpload={upload} onRemove={(id) => setFiles((current) => current.filter((file) => file.id !== id))} />
    </section></main>
  </div>;
}

function parseJson(value: string): Record<string, unknown> { try { return JSON.parse(value) as Record<string, unknown>; } catch { return {}; } }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" ? value as Record<string, unknown> : {}; }
