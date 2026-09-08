"use client";

import { type FormEvent, useEffect, useMemo, useState } from "react";
import { writeTextToClipboard } from "../lib/clipboard";
import { SecretInput } from "./secret-input";
import styles from "./api-integration.module.css";

type Permission = "tasks:read" | "tasks:write" | "studio:read";

type ApiKey = {
  key_id: string;
  name: string;
  prefix: string;
  permissions: Permission[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

type CreatedApiKey = { api_key: ApiKey; secret: string };

const PERMISSIONS: ReadonlyArray<{
  value: Permission;
  label: string;
  description: string;
}> = [
  { value: "tasks:read", label: "读取任务与 Agent", description: "读取 Agent 目录、会话、运行状态和产物。" },
  { value: "tasks:write", label: "发起对话与任务", description: "创建会话、运行任务、上传输入并取消运行。" },
  { value: "studio:read", label: "读取 Studio", description: "读取工作区内可见的智能体配置与能力目录。" },
];

function errorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = payload.error;
    if (error && typeof error === "object" && "message" in error && typeof error.message === "string") return error.message;
  }
  return fallback;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  const payload = (await response.json().catch(() => null)) as T | null;
  if (!response.ok) throw new Error(errorMessage(payload, `请求失败（${response.status}）`));
  return payload as T;
}

function formatTime(value: string | null) {
  if (!value) return "尚未使用";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

export function ApiIntegration() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState("");
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [baseUrl, setBaseUrl] = useState("/v1");

  useEffect(() => {
    Promise.all([
      request<ApiKey[]>("/api/auth/api-keys"),
      request<{ baseUrl: string }>("/api/auth/api-config"),
    ])
      .then(([nextKeys, config]) => {
        setKeys(nextKeys);
        setBaseUrl(config.baseUrl);
      })
      .catch((error: unknown) => setMessage({ kind: "error", text: error instanceof Error ? error.message : "API 密钥暂时不可用。" }))
      .finally(() => setLoading(false));
  }, []);

  const activeCount = useMemo(() => keys.filter((key) => !key.revoked_at).length, [keys]);

  async function createKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreating(true);
    setMessage(null);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const permissions = PERMISSIONS.map((item) => item.value).filter((value) => form.getAll("permissions").includes(value));
    try {
      const result = await request<CreatedApiKey>("/api/auth/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: String(form.get("name") ?? ""), permissions }),
      });
      setCreated(result);
      setKeys((current) => [result.api_key, ...current]);
      formElement.reset();
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "API 密钥创建失败。" });
    } finally {
      setCreating(false);
    }
  }

  async function revoke(key: ApiKey) {
    if (!window.confirm(`吊销“${key.name}”？使用该密钥的集成会立即失效。`)) return;
    setRevoking(key.key_id);
    setMessage(null);
    try {
      const next = await request<ApiKey>(`/api/auth/api-keys/${encodeURIComponent(key.key_id)}`, { method: "DELETE" });
      setKeys((current) => current.map((item) => item.key_id === next.key_id ? next : item));
      setMessage({ kind: "success", text: `${key.name} 已吊销。` });
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "密钥未能吊销。" });
    } finally {
      setRevoking("");
    }
  }

  async function copy(value: string, label: string) {
    const copied = await writeTextToClipboard(value);
    setMessage(copied
      ? { kind: "success", text: `${label}已复制。` }
      : { kind: "error", text: `${label}复制失败，请选中文本手动复制。` });
  }

  const curlExample = `curl '${baseUrl}/agents' \\\n+  -H 'X-API-Key: $AXIS_API_KEY'`;

  const displayedCurlExample = curlExample.replace("\n+", "\n");
  const createSessionExample = [
    `curl -sS -X POST '${baseUrl}/sessions' \\`,
    "  -H 'X-API-Key: $AXIS_API_KEY' \\",
    "  -H 'Content-Type: application/json' \\",
    `  -d '{"agent_name":"lead-agent","agent_version":"1.0.0"}'`,
  ].join("\n");
  const askExample = [
    "SESSION_ID='填写创建会话返回的 session_id'",
    `curl -sS -X POST "${baseUrl}/sessions/\${SESSION_ID}/runs" \\`,
    "  -H 'X-API-Key: $AXIS_API_KEY' \\",
    "  -H 'Idempotency-Key: question-001' \\",
    "  -H 'Content-Type: application/json' \\",
    `  -d '{"prompt":"你好，请介绍一下你能做什么"}'`,
  ].join("\n");
  const answerExample = [
    "RUN_ID='填写提问返回的 run_id'",
    `curl -sS "${baseUrl}/runs/\${RUN_ID}" -H 'X-API-Key: $AXIS_API_KEY'`,
    `curl -sS "${baseUrl}/runs/\${RUN_ID}/events" -H 'X-API-Key: $AXIS_API_KEY'`,
  ].join("\n");

  return (
    <div className={styles.integration}>
      <section className={styles.endpoint}>
        <div><strong>API Base URL</strong><small>服务端 API 的统一入口，可用于脚本、工作流和第三方系统。</small></div>
        <div className={styles.copyField}><code>{baseUrl}</code><button type="button" onClick={() => void copy(baseUrl, "Base URL")}>复制</button></div>
      </section>

      {message && <p className={`${styles.message} ${styles[message.kind]}`} role="status">{message.text}</p>}

      <section className={styles.block}>
        <header><div><strong>API 密钥</strong><small>{activeCount} 个有效密钥 · 完整密钥只在创建后显示一次</small></div></header>
        <form className={styles.createForm} onSubmit={createKey}>
          <label>名称<input name="name" required maxLength={160} placeholder="例如：数据同步工作流" /></label>
          <fieldset>
            <legend>访问权限</legend>
            <div className={styles.permissionGrid}>
              {PERMISSIONS.map((permission) => (
                <label key={permission.value}>
                  <input type="checkbox" name="permissions" value={permission.value} defaultChecked={permission.value !== "studio:read"} />
                  <span><strong>{permission.label}</strong><small>{permission.description}</small></span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className={styles.createAction}><span>建议为不同系统分别创建密钥，按最小权限授权。</span><button type="submit" disabled={creating}>{creating ? "正在创建…" : "+ 创建密钥"}</button></div>
        </form>

        <div className={styles.keyList}>
          {loading && <p className={styles.empty}>正在读取 API 密钥…</p>}
          {!loading && keys.length === 0 && <p className={styles.empty}>尚未创建 API 密钥。</p>}
          {keys.map((key) => (
            <article key={key.key_id} data-revoked={Boolean(key.revoked_at)}>
              <div className={styles.keyIdentity}><span className={styles.keyMark}>K</span><span><strong>{key.name}</strong><code>{key.prefix}••••••••••••</code></span></div>
              <div className={styles.keyMeta}><span>{key.permissions.map((value) => PERMISSIONS.find((item) => item.value === value)?.label ?? value).join("、")}</span><small>{key.revoked_at ? `已于 ${formatTime(key.revoked_at)}吊销` : `最近使用：${formatTime(key.last_used_at)}`}</small></div>
              {!key.revoked_at && <button className={styles.revoke} type="button" disabled={Boolean(revoking)} onClick={() => void revoke(key)}>{revoking === key.key_id ? "吊销中…" : "吊销"}</button>}
            </article>
          ))}
        </div>
      </section>

      <section className={styles.block}>
        <header><div><strong>调用示例</strong><small>通过 X-API-Key 请求头调用；不要把密钥写进代码仓库或浏览器前端。</small></div></header>
        <div className={styles.codeBlock}><pre>{displayedCurlExample}</pre><button type="button" onClick={() => void copy(displayedCurlExample, "cURL 示例")}>复制</button></div>
        <div className={styles.guide}><strong>推荐接入步骤</strong><ol><li>为集成创建独立密钥并选择最小权限。</li><li>将密钥保存到服务端环境变量 <code>AXIS_API_KEY</code>。</li><li>先读取 <code>/v1/agents</code>，再创建会话并发起运行。</li><li>集成下线后立即吊销对应密钥。</li></ol></div>
      </section>

      <section className={styles.block}>
        <header><div><strong>问答请求预览</strong><small>Session 是一段连续对话；每次问题会在该 Session 下创建一个独立 Run。</small></div></header>
        <div className={styles.conversationModel}>
          <span><strong>Session</strong><small>绑定 Agent 与版本，并承载多轮上下文</small></span>
          <i aria-hidden="true">→</i>
          <span><strong>Run 1</strong><small>第一个问题</small></span>
          <i aria-hidden="true">→</i>
          <span><strong>Run 2</strong><small>复用 Session 继续追问</small></span>
        </div>
        <div className={styles.exampleList}>
          <article><header><span>1</span><div><strong>创建 Session</strong><small>一次创建，后续追问复用返回的 <code>session_id</code>。</small></div></header><div className={styles.codeBlock}><pre>{createSessionExample}</pre><button type="button" onClick={() => void copy(createSessionExample, "创建 Session 示例")}>复制</button></div></article>
          <article><header><span>2</span><div><strong>发送问题</strong><small>POST 返回 <code>run_id</code>；相同幂等键不会重复创建任务。</small></div></header><div className={styles.codeBlock}><pre>{askExample}</pre><button type="button" onClick={() => void copy(askExample, "提问示例")}>复制</button></div></article>
          <article><header><span>3</span><div><strong>读取回答</strong><small>先查询 Run，完成后读取事件；拼接 <code>message.delta</code> 的 <code>payload.text</code> 即为回答。</small></div></header><div className={styles.codeBlock}><pre>{answerExample}</pre><button type="button" onClick={() => void copy(answerExample, "读取回答示例")}>复制</button></div></article>
        </div>
        <div className={styles.sessionNote}><strong>如何追问？</strong><span>继续向同一个 <code>/sessions/&#123;session_id&#125;/runs</code> 发送新 prompt；若要开始完全独立的新对话，再创建一个新的 Session。</span></div>
      </section>

      {created && (
        <div className={styles.backdrop} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setCreated(null); }}>
          <div className={styles.secretDialog} role="dialog" aria-modal="true" aria-labelledby="created-key-title">
            <header><span><strong id="created-key-title">保存 API 密钥</strong><small>关闭后将无法再次查看完整内容。</small></span><button type="button" aria-label="关闭" onClick={() => setCreated(null)}>×</button></header>
            <div className={styles.secretNotice}>请立即复制到密码管理器或服务器环境变量。系统只保存密钥哈希，无法帮你恢复。</div>
            <label>API Key<SecretInput value={created.secret} readOnly revealLabel="新建 API Key" /></label>
            <footer><button type="button" onClick={() => void copy(created.secret, "API Key")}>复制密钥</button><button type="button" className={styles.done} onClick={() => setCreated(null)}>我已保存</button></footer>
          </div>
        </div>
      )}
    </div>
  );
}
