"use client";

import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useAuth } from "./auth-provider";
import { useInternalAgentsPreference } from "../lib/interface-preferences";
import { loadTaskAgentCatalog } from "../lib/task-agent-catalog";
import { SecretInput } from "./secret-input";
import styles from "./model-management.module.css";

type ModelType = "chat" | "vision" | "image_generation" | "video_generation";
type ApiFormat = "anthropic_compatible" | "openai_compatible" | "openai_images" | "openai_videos";

type ManagedModel = {
  routeId: string;
  label: string;
  modelType: ModelType;
  provider: string;
  model: string;
  baseUrl: string | null;
  apiFormat: ApiFormat;
  authScheme: "bearer" | "x-api-key" | "none";
  capabilities: string[];
  enabled: boolean;
  credentialConfigured: boolean;
  deletable: boolean;
  version: number;
};

type ModelState = {
  revision: number;
  models: ManagedModel[];
  agentModelBindings: Record<string, string>;
};

type AgentItem = { name: string; display_name: string };
type ProviderPreset = "minimax" | "minimax_h3" | "openai" | "anthropic" | "custom";

const TYPE_COPY: Record<ModelType, { label: string; mark: string; description: string }> = {
  chat: { label: "对话", mark: "C", description: "文本理解、工具调用与流式回答" },
  vision: { label: "视觉", mark: "V", description: "同时理解文本、图片与文档截图" },
  image_generation: { label: "图像生成", mark: "I", description: "通过独立接口生成图片，不参与对话路由" },
  video_generation: { label: "视频生成", mark: "M", description: "通过独立接口生成 MP4，不参与对话路由" },
};

const EMPTY_STATE: ModelState = { revision: 1, models: [], agentModelBindings: {} };

const PROVIDER_PRESETS: Record<Exclude<ProviderPreset, "custom">, {
  label: string;
  provider: string;
  baseUrl: string;
  apiFormat: ApiFormat;
  authScheme: ManagedModel["authScheme"];
}> = {
  minimax: {
    label: "MiniMax",
    provider: "MiniMax",
    baseUrl: "https://api.minimaxi.com/anthropic/v1",
    apiFormat: "anthropic_compatible",
    authScheme: "x-api-key",
  },
  minimax_h3: {
    label: "MiniMax H3（229 内网）",
    provider: "MiniMax",
    baseUrl: "http://172.20.109.229:18000/v1",
    apiFormat: "openai_videos",
    authScheme: "none",
  },
  openai: {
    label: "OpenAI",
    provider: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    apiFormat: "openai_compatible",
    authScheme: "bearer",
  },
  anthropic: {
    label: "Anthropic",
    provider: "Anthropic",
    baseUrl: "https://api.anthropic.com/v1",
    apiFormat: "anthropic_compatible",
    authScheme: "x-api-key",
  },
};

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  const payload = (await response.json().catch(() => null)) as
    | T
    | { error?: { message?: string } }
    | null;
  if (!response.ok) {
    const message = payload && typeof payload === "object" && "error" in payload
      ? payload.error?.message
      : null;
    throw new Error(message || `请求失败（${response.status}）`);
  }
  return payload as T;
}

function routeIdFromLabel(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/^[^a-z]+/, "");
}

function providerPresetFor(model: ManagedModel | null): ProviderPreset {
  if (!model) return "minimax";
  const signature = `${model.provider} ${model.baseUrl ?? ""}`.toLowerCase();
  if (model.apiFormat === "openai_videos" || signature.includes("172.20.109.229:18000")) return "minimax_h3";
  if (signature.includes("minimax")) return "minimax";
  if (signature.includes("anthropic")) return "anthropic";
  if (signature.includes("openai")) return "openai";
  return "custom";
}

export function ModelManagement() {
  const { user } = useAuth();
  const [showInternalAgents] = useInternalAgentsPreference();
  const [state, setState] = useState<ModelState>(EMPTY_STATE);
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [filter, setFilter] = useState<"all" | ModelType>("all");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ManagedModel | null | undefined>(undefined);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  async function refresh() {
    const [models, catalog] = await Promise.all([
      api<ModelState>("/api/studio/models"),
      loadTaskAgentCatalog(user.user_id, showInternalAgents).then((catalog) => catalog.agents.map((agent) => ({name: agent.name, display_name: agent.displayName}))).catch(() => []),
    ]);
    setState(models);
    setAgents(
      catalog.filter(
        (item, index, all) => all.findIndex((candidate) => candidate.name === item.name) === index,
      ),
    );
  }

  useEffect(() => {
    let active = true;
    refresh()
      .catch((error: unknown) => {
        if (active) setMessage({ kind: "error", text: error instanceof Error ? error.message : "模型配置暂时不可用。" });
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [user.user_id, showInternalAgents]);

  const visible = useMemo(
    () => state.models.filter((model) => filter === "all" || model.modelType === filter),
    [filter, state.models],
  );
  const conversational = state.models.filter(
    (model) => model.enabled && ["chat", "vision"].includes(model.modelType) && model.baseUrl && model.credentialConfigured,
  );

  async function testConnection(model: ManagedModel) {
    setBusy(`test:${model.routeId}`);
    setMessage(null);
    try {
      const result = await api<{ message: string; latencyMs: number }>(
        `/api/studio/models/${encodeURIComponent(model.routeId)}/test`,
        { method: "POST" },
      );
      setMessage({ kind: "success", text: `${model.label}：${result.message}（${result.latencyMs} ms）` });
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "模型连接失败。" });
    } finally {
      setBusy("");
    }
  }

  async function disable(model: ManagedModel) {
    if (!window.confirm(`停用“${model.label}”？已绑定的 Agent 将无法使用该路由。`)) return;
    setBusy(`disable:${model.routeId}`);
    try {
      const next = await api<ModelState>(
        `/api/studio/models/${encodeURIComponent(model.routeId)}?expectedRevision=${state.revision}`,
        { method: "DELETE" },
      );
      setState(next);
      setMessage({ kind: "success", text: `${model.label} 已停用。` });
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "模型未能停用。" });
    } finally {
      setBusy("");
    }
  }

  async function deleteModel(model: ManagedModel) {
    if (!window.confirm(`永久删除“${model.label}”？模型配置和已保存的密钥都会被移除，此操作无法撤销。`)) return;
    setBusy(`delete:${model.routeId}`);
    setMessage(null);
    try {
      const next = await api<ModelState>(
        `/api/studio/models/${encodeURIComponent(model.routeId)}/permanent?expectedRevision=${state.revision}`,
        { method: "DELETE" },
      );
      setState(next);
      setMessage({ kind: "success", text: `${model.label} 已删除。` });
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "模型未能删除。" });
    } finally {
      setBusy("");
    }
  }

  async function bindAgent(agentName: string, routeId: string) {
    setBusy(`bind:${agentName}`);
    try {
      const next = await api<ModelState>(
        `/api/studio/models/agent-bindings/${encodeURIComponent(agentName)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expectedRevision: state.revision, routeId }),
        },
      );
      setState(next);
      setMessage({ kind: "success", text: "Agent 默认模型已更新，新任务立即生效。" });
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof Error ? error.message : "默认模型未能更新。" });
    } finally {
      setBusy("");
    }
  }

  return (
    <div className={styles.controlPlane}>
      <div className={styles.guardrail}>
        <span className={styles.lock} aria-hidden="true">⌁</span>
        <div><strong>模型由控制面统一管理</strong><small>API Key 加密保存且不回传浏览器；只有 Owner / Admin 可以查看连接信息或修改配置。</small></div>
      </div>

      <div className={styles.toolbar}>
        <div className={styles.filters} aria-label="模型类型">
          {(["all", "chat", "vision", "image_generation", "video_generation"] as const).map((type) => (
            <button key={type} type="button" className={filter === type ? styles.filterActive : ""} onClick={() => setFilter(type)}>
              {type === "all" ? `全部 ${state.models.length}` : `${TYPE_COPY[type].label} ${state.models.filter((item) => item.modelType === type).length}`}
            </button>
          ))}
        </div>
        <button type="button" className={styles.addButton} onClick={() => setEditing(null)}>+ 添加模型</button>
      </div>

      {message && <p className={`${styles.message} ${styles[message.kind]}`} role="status">{message.text}</p>}
      {loading ? <p className={styles.empty}>正在读取模型配置…</p> : (
        <div className={styles.grid}>
          {visible.map((model) => (
            <article className={styles.modelCard} key={model.routeId}>
              <span className={`${styles.typeMark} ${styles[model.modelType]}`} aria-hidden="true">{TYPE_COPY[model.modelType].mark}</span>
              <div className={styles.modelCopy}>
                <div className={styles.cardTitle}>
                  <strong>{model.label}</strong>
                  <span className={model.enabled ? styles.ready : styles.disabled}>{model.enabled ? "已启用" : "已停用"}</span>
                </div>
                <p>{model.model}</p>
                <small>{model.provider} · {TYPE_COPY[model.modelType].description}</small>
                <div className={styles.statusRow}>
                  <span data-ok>控制面配置</span>
                  <span data-ok={Boolean(model.baseUrl)}>端点{model.baseUrl ? "已配置" : "待配置"}</span>
                  <span data-ok={model.credentialConfigured}>{model.authScheme === "none" ? "内网免鉴权" : `密钥${model.credentialConfigured ? "已保存" : "待配置"}`}</span>
                </div>
              </div>
              <div className={styles.cardActions}>
                <button type="button" onClick={() => setEditing(model)}>编辑</button>
                <button type="button" disabled={!model.baseUrl || !model.credentialConfigured || busy !== ""} onClick={() => void testConnection(model)}>
                  {busy === `test:${model.routeId}` ? "测试中" : "测试"}
                </button>
                {model.enabled && <button type="button" className={styles.danger} disabled={busy !== ""} onClick={() => void disable(model)}>停用</button>}
                {model.deletable && <button type="button" className={styles.deleteButton} disabled={busy !== ""} onClick={() => void deleteModel(model)} aria-label={`删除 ${model.label}`} title="永久删除模型">删除</button>}
              </div>
            </article>
          ))}
          <button type="button" className={styles.emptyCard} onClick={() => setEditing(null)}>
            <span>+</span><strong>添加模型</strong><small>对话、视觉、图像或视频生成</small>
          </button>
        </div>
      )}

      <div className={styles.bindings}>
        <div><strong>Agent 默认模型</strong><small>替代内置 Agent 包中的后台固定值；只影响新任务，任务中仍可临时切换。</small></div>
        {agents.length === 0 ? <p className={styles.empty}>暂无可配置的 Agent。</p> : agents.map((agent) => (
          <label key={agent.name}>
            <span><strong>{agent.display_name}</strong><small>{agent.name}</small></span>
            <select
              value={state.agentModelBindings[agent.name] ?? ""}
              disabled={busy !== "" || conversational.length === 0}
              onChange={(event) => void bindAgent(agent.name, event.target.value)}
            >
              <option value="" disabled>选择默认模型</option>
              {conversational.map((model) => <option key={model.routeId} value={model.routeId}>{model.label} · {TYPE_COPY[model.modelType].label}</option>)}
            </select>
          </label>
        ))}
      </div>

      {editing !== undefined && (
        <ModelDialog
          model={editing}
          revision={state.revision}
          onClose={() => setEditing(undefined)}
          onSaved={(next, label) => {
            setState(next);
            setEditing(undefined);
            setMessage({ kind: "success", text: `${label} 已保存。` });
          }}
        />
      )}
    </div>
  );
}

function ModelDialog({
  model,
  revision,
  onClose,
  onSaved,
}: {
  model: ManagedModel | null;
  revision: number;
  onClose: () => void;
  onSaved: (next: ModelState, label: string) => void;
}) {
  const [type, setType] = useState<ModelType>(model?.modelType ?? "chat");
  const initialPreset = providerPresetFor(model);
  const initialConnection = initialPreset === "custom" ? null : PROVIDER_PRESETS[initialPreset];
  const [providerPreset, setProviderPreset] = useState<ProviderPreset>(initialPreset);
  const [provider, setProvider] = useState(model?.provider ?? initialConnection?.provider ?? "MiniMax");
  const [baseUrl, setBaseUrl] = useState(model?.baseUrl ?? initialConnection?.baseUrl ?? "");
  const [modelName, setModelName] = useState(model?.model ?? (initialPreset === "minimax_h3" ? "/model" : ""));
  const [apiFormat, setApiFormat] = useState<ApiFormat>(
    model?.apiFormat ?? initialConnection?.apiFormat ?? "openai_compatible",
  );
  const [authScheme, setAuthScheme] = useState<ManagedModel["authScheme"]>(model?.authScheme ?? initialConnection?.authScheme ?? "bearer");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  function chooseProvider(next: ProviderPreset) {
    setProviderPreset(next);
    if (next === "custom") return;
    const preset = PROVIDER_PRESETS[next];
    setProvider(preset.provider);
    setBaseUrl(preset.baseUrl);
    setApiFormat(preset.apiFormat);
    setAuthScheme(preset.authScheme);
    if (next === "minimax_h3") setModelName("/model");
  }

  function chooseType(next: ModelType) {
    setType(next);
    if (next === "video_generation") chooseProvider("minimax_h3");
    else if (providerPreset === "minimax_h3") chooseProvider("minimax");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const normalizedModelName = modelName.trim();
    const label = String(form.get("label") ?? "").trim() || (providerPreset === "minimax_h3" ? "MiniMax H3 视频" : normalizedModelName);
    const routeId = model?.routeId ?? routeIdFromLabel(String(form.get("routeId") ?? "").trim() || (providerPreset === "minimax_h3" ? "minimax-h3-video" : normalizedModelName) || label);
    if (!routeId) {
      setError("路由 ID 需以英文字母开头，只能包含小写字母、数字和连字符。");
      setPending(false);
      return;
    }
    const apiKey = String(form.get("apiKey") ?? "").trim();
    try {
      const next = await api<ModelState>(`/api/studio/models/${encodeURIComponent(routeId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expectedRevision: revision,
          label,
          modelType: type,
          provider,
          model: normalizedModelName,
          baseUrl,
          apiFormat: type === "image_generation" ? "openai_images" : type === "video_generation" ? "openai_videos" : apiFormat,
          authScheme,
          apiKey: apiKey || null,
          enabled: true,
        }),
      });
      onSaved(next, label);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "模型配置未能保存。请检查字段后重试。");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className={styles.backdrop} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="model-dialog-title">
        <header>
          <div><span className={`${styles.typeMark} ${styles[type]}`}>{TYPE_COPY[type].mark}</span><span><strong id="model-dialog-title">{model ? "编辑模型" : "添加模型"}</strong><small>{TYPE_COPY[type].description}</small></span></div>
          <button type="button" aria-label="关闭" onClick={onClose}>×</button>
        </header>
        <form onSubmit={save}>
          <fieldset className={styles.typePicker}>
            <legend>模型类型</legend>
            {(["chat", "vision", "image_generation", "video_generation"] as const).map((item) => (
              <label key={item} className={type === item ? styles.typeActive : ""}>
                <input type="radio" name="modelType" value={item} checked={type === item} onChange={() => chooseType(item)} />
                <span>{TYPE_COPY[item].mark}</span><strong>{TYPE_COPY[item].label}</strong>
              </label>
            ))}
          </fieldset>
          <div className={styles.quickStart}>
            <strong>常用配置</strong>
            <small>选择服务商后会自动填写接口地址和鉴权方式，通常只需确认模型名称并粘贴 API Key。</small>
          </div>
          <div className={styles.formGrid}>
            <label>服务商<select value={providerPreset} onChange={(event) => chooseProvider(event.target.value as ProviderPreset)}><option value="minimax">MiniMax</option><option value="minimax_h3">MiniMax H3（229 内网）</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="custom">自定义兼容接口</option></select></label>
            <label>模型名称<input name="model" value={modelName} onChange={(event) => setModelName(event.target.value)} required placeholder="服务商使用的实际模型名" /></label>
            <label className={styles.full}>API Key<SecretInput name="apiKey" autoComplete="new-password" disabled={authScheme === "none"} required={authScheme !== "none" && !model?.credentialConfigured} placeholder={authScheme === "none" ? "该内网服务无需鉴权" : model?.credentialConfigured ? "已安全保存；留空则保持不变" : "输入 API Key"} revealLabel="API Key" /><small>{authScheme === "none" ? "不会保存或发送密钥。" : "保存后不会再次显示明文。"}</small></label>
          </div>
          <details className={styles.advanced} open={providerPreset === "custom" ? true : undefined}>
            <summary><span><strong>高级配置</strong><small>显示名称、路由 ID、接口地址与协议</small></span><span aria-hidden="true">⌄</span></summary>
            <div className={styles.formGrid}>
              <label>显示名称（可选）<input name="label" defaultValue={model?.label ?? ""} placeholder="默认使用模型名称" /></label>
              <label>路由 ID（可选）<input name="routeId" defaultValue={model?.routeId ?? ""} disabled={Boolean(model)} placeholder="默认按模型名称生成" pattern="[a-z][a-z0-9-]*" /></label>
              <label>服务商标识<input value={provider} onChange={(event) => setProvider(event.target.value)} required placeholder="例如：MiniMax" /></label>
              <label>Base URL<input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} required placeholder="https://api.example.com/v1" /></label>
              {type === "chat" || type === "vision" ? (
                <label>接口格式<select value={apiFormat} onChange={(event) => setApiFormat(event.target.value as ApiFormat)}><option value="openai_compatible">OpenAI 兼容</option><option value="anthropic_compatible">Anthropic 兼容</option></select></label>
              ) : null}
              <label>鉴权方式<select value={authScheme} onChange={(event) => setAuthScheme(event.target.value as ManagedModel["authScheme"])}><option value="bearer">Bearer Token</option><option value="x-api-key">x-api-key</option>{type === "video_generation" && <option value="none">无需鉴权（仅限受控内网）</option>}</select></label>
            </div>
          </details>
          {error && <p className={`${styles.message} ${styles.error}`} role="alert">{error}</p>}
          <footer><button type="button" onClick={onClose}>取消</button><button type="submit" className={styles.save} disabled={pending}>{pending ? "正在保存…" : "保存模型"}</button></footer>
        </form>
      </div>
    </div>
  );
}
