"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { AuthProvider, useAuth } from "../../components/auth-provider";
import { WebConfiguration } from "../../components/web-configuration";
import { MemoryBank } from "../../components/memory-bank/memory-bank";
import { DataLifecycleControlPlane } from "../../components/agent-studio/data-lifecycle-control-plane";
import { ApiIntegration } from "../../components/api-integration";
import { ModelManagement } from "../../components/model-management";
import {
  PRODUCT_NAME,
} from "../../components/product-brand";
import {
  ProductIcon,
  type ProductIconName,
} from "../../components/product-icon";
import { SecretInput } from "../../components/secret-input";
import { ThemeSelector } from "../../components/theme-toggle";
import { useFollowUpPreference, useInternalAgentsPreference } from "../../lib/interface-preferences";
import {
  loadTasks,
  setTaskArchived,
  type TaskSummary,
} from "../../lib/task-history";

const ROLE_LABELS = {
  owner: "所有者",
  admin: "管理员",
  member: "成员",
  viewer: "只读成员",
} as const;

type Message = { kind: "success" | "error"; text: string } | null;

type SettingsSectionId =
  | "profile"
  | "members"
  | "models"
  | "api"
  | "configuration"
  | "appearance"
  | "security"
  | "data"
  | "memory"
  | "session"
  | "archived";

const SETTINGS_NAV: ReadonlyArray<{
  id: SettingsSectionId;
  href: string;
  label: string;
  description: string;
  icon: ProductIconName;
}> = [
  {
    id: "profile",
    href: "#profile",
    label: "个人资料",
    description: "管理显示名称、登录邮箱和当前工作区身份。",
    icon: "profile",
  },
  {
    id: "models",
    href: "#models",
    label: "模型管理",
    description: "配置对话、视觉、图像和视频生成模型，并指定内置 Agent 的默认模型。",
    icon: "agent",
  },
  {
    id: "api",
    href: "#api",
    label: "API 集成",
    description: "创建按能力授权的 API 密钥，将对话、任务和 Agent 安全接入外部系统。",
    icon: "api",
  },
  {
    id: "configuration",
    href: "#configuration",
    label: "配置",
    description: "管理个人对话与智能体显示偏好。",
    icon: "settings",
  },
  {
    id: "appearance",
    href: "#appearance",
    label: "外观",
    description: `选择整个${PRODUCT_NAME}使用的界面主题。`,
    icon: "appearance",
  },
  {
    id: "security",
    href: "#security",
    label: "账户安全",
    description: "修改密码后会撤销所有刷新会话。",
    icon: "security",
  },
  {
    id: "data",
    href: "#data",
    label: "我的数据",
    description: "导出或删除当前用户产生的会话、文件、记忆与观测数据。",
    icon: "data",
  },
  {
    id: "memory",
    href: "#memory",
    label: "长期记忆",
    description: "查看智能体建议保存的内容，并逐条确认、纠正或删除。",
    icon: "memory",
  },
  {
    id: "session",
    href: "#session",
    label: "登录会话",
    description: "查看当前浏览器会话或安全退出登录。",
    icon: "session",
  },
  {
    id: "archived",
    href: "#archived",
    label: "已归档",
    description: "查看并恢复从最近任务中移出的记录。",
    icon: "archive",
  },
];

function errorMessage(payload: unknown, fallback: string): string {
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    payload.error &&
    typeof payload.error === "object" &&
    "message" in payload.error &&
    typeof payload.error.message === "string"
  ) {
    return payload.error.message;
  }
  return fallback;
}

function ArchivedTasksSettings() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [restoring, setRestoring] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    loadTasks(true)
      .then((items) => {
        if (active) setTasks(items);
      })
      .catch(() => {
        if (active) setError("暂时无法读取已归档任务。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function restore(task: TaskSummary) {
    setRestoring(task.thread_id);
    setError("");
    try {
      await setTaskArchived(task.thread_id, false);
      setTasks((current) =>
        current.filter((item) => item.thread_id !== task.thread_id),
      );
    } catch {
      setError("恢复失败，请稍后重试。");
    } finally {
      setRestoring("");
    }
  }

  return (
    <div className="settings-form archived-task-settings">
      {loading && <p className="archived-task-empty">正在读取…</p>}
      {!loading && tasks.length === 0 && !error && (
        <p className="archived-task-empty">暂无已归档任务。</p>
      )}
      {tasks.map((task) => (
        <div className="archived-task-row" key={task.thread_id}>
          <span>
            <strong>{task.title}</strong>
            <small>
              {new Intl.DateTimeFormat("zh-CN", {
                year: "numeric",
                month: "numeric",
                day: "numeric",
              }).format(new Date(task.updated_at))}
            </small>
          </span>
          <button
            type="button"
            disabled={restoring === task.thread_id}
            onClick={() => void restore(task)}
          >
            {restoring === task.thread_id ? "正在恢复…" : "恢复"}
          </button>
        </div>
      ))}
      {error && <p className="settings-message error">{error}</p>}
    </div>
  );
}

function SettingsContent() {
  const { user, membership, passwordEnabled } = useAuth();
  const [followUpBehavior, setFollowUpBehavior] = useFollowUpPreference();
  const [showInternalAgents, setShowInternalAgents] = useInternalAgentsPreference();
  const canManageModels =
    membership.role === "owner" || membership.role === "admin";
  const visibleNavigation = SETTINGS_NAV.filter(
    (item) => !(["models", "api"] as SettingsSectionId[]).includes(item.id) || canManageModels,
  );
  const [activeSection, setActiveSection] =
    useState<SettingsSectionId>("profile");
  const contentRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (contentRef.current) contentRef.current.scrollTop = 0; }, [activeSection]);
  const [profileMessage, setProfileMessage] = useState<Message>(null);
  const [passwordMessage, setPasswordMessage] = useState<Message>(null);
  const [profilePending, setProfilePending] = useState(false);
  const [passwordPending, setPasswordPending] = useState(false);

  useEffect(() => {
    function syncSectionFromHash() {
      const requested = window.location.hash.slice(1) as SettingsSectionId;
      const canOpenRequested = SETTINGS_NAV.some(
        (item) =>
          item.id === requested && (!["models", "api"].includes(item.id) || canManageModels),
      );
      const next = canOpenRequested ? requested : "profile";
      setActiveSection(next);
    }

    syncSectionFromHash();
    window.addEventListener("hashchange", syncSectionFromHash);
    return () => window.removeEventListener("hashchange", syncSectionFromHash);
  }, [canManageModels]);

  const activeNavigation =
    visibleNavigation.find((item) => item.id === activeSection) ??
    visibleNavigation[0];

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProfilePending(true);
    setProfileMessage(null);
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/auth/profile", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: String(form.get("display_name") ?? ""),
        }),
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok) {
        setProfileMessage({
          kind: "error",
          text: errorMessage(payload, "个人资料未能保存。"),
        });
        return;
      }
      setProfileMessage({ kind: "success", text: "个人资料已保存。" });
    } catch {
      setProfileMessage({ kind: "error", text: "认证服务暂时不可用。" });
    } finally {
      setProfilePending(false);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPasswordPending(true);
    setPasswordMessage(null);
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("current_password") ?? "");
    const newPassword = String(form.get("new_password") ?? "");
    const confirmPassword = String(form.get("confirm_password") ?? "");
    if (newPassword !== confirmPassword) {
      setPasswordMessage({ kind: "error", text: "两次输入的新密码不一致。" });
      setPasswordPending(false);
      return;
    }
    try {
      const response = await fetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      if (!response.ok) {
        const payload = (await response.json()) as unknown;
        setPasswordMessage({
          kind: "error",
          text: errorMessage(payload, "密码未能更新。"),
        });
        return;
      }
      window.location.replace("/login?password=changed");
    } catch {
      setPasswordMessage({ kind: "error", text: "认证服务暂时不可用。" });
    } finally {
      setPasswordPending(false);
    }
  }

  return (
    <main className="settings-shell" id="main-content">
      <div
        className="settings-dialog"
        aria-labelledby="settings-page-title"
      >
        <div className="settings-layout">
          <aside className="settings-index" aria-label="设置目录">
            <a className="settings-return-app" href="/" aria-label="返回应用"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16 10H4m5-5-5 5 5 5" /></svg><span>返回应用</span></a>
            <p>设置</p>
            {visibleNavigation.map((item) => (
              <a
                aria-current={activeSection === item.id ? "page" : undefined}
                href={item.href}
                key={item.href}
                onClick={(event) => {
                  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  event.preventDefault();
                  window.history.pushState(null, "", item.href);
                  setActiveSection(item.id);
                  if (contentRef.current) contentRef.current.scrollTop = 0;
                }}
              >
                <ProductIcon name={item.icon} />
                <span>{item.label}</span>
              </a>
            ))}
          </aside>

          <div className="settings-content" ref={contentRef}>
            <header className="settings-title">
              <p>设置中心</p>
              <h1 id="settings-page-title">{activeNavigation.label}</h1>
              <span>{activeNavigation.description}</span>
            </header>

            <section
              className="settings-section"
              id="profile"
              hidden={activeSection !== "profile"}
            >
              <form className="settings-form" onSubmit={saveProfile}>
                <label>
                  显示名称
                  <input
                    name="display_name"
                    defaultValue={user.display_name}
                    required
                    maxLength={160}
                    autoComplete="name"
                  />
                </label>
                <label>
                  登录邮箱
                  <input value={user.email} readOnly aria-readonly="true" />
                  <small>邮箱暂不支持自行修改。</small>
                </label>
                <div className="settings-facts">
                  <span>
                    <small>工作区角色</small>
                    <strong>{ROLE_LABELS[membership.role]}</strong>
                  </span>
                  <span>
                    <small>工作区</small>
                    <code>{membership.tenant_id}</code>
                  </span>
                </div>
                {profileMessage && (
                  <p
                    className={`settings-message ${profileMessage.kind}`}
                    role="status"
                  >
                    {profileMessage.text}
                  </p>
                )}
                <div className="settings-form-action">
                  <button type="submit" disabled={profilePending}>
                    {profilePending ? "正在保存…" : "保存资料"}
                  </button>
                </div>
              </form>
            </section>



            {canManageModels && (
              <section
                className="settings-section settings-section-models"
                id="models"
                hidden={activeSection !== "models"}
              >
                <ModelManagement />
              </section>
            )}

            {canManageModels && (
              <section
                className="settings-section"
                id="api"
                hidden={activeSection !== "api"}
              >
                <ApiIntegration />
              </section>
            )}

            <section className="settings-section" id="configuration" hidden={activeSection !== "configuration"}>
              <div className="settings-form settings-configuration">
                <label className="settings-preference-row">
                  <span><strong>运行中的补充消息</strong><small>Enter 使用所选方式，Alt Enter 临时切换。带附件的消息会加入队列。</small></span>
                  <select aria-label="补充消息默认行为" value={followUpBehavior} onChange={(event) => setFollowUpBehavior(event.target.value === "steer" ? "steer" : "queue")}>
                    <option value="queue">加入队列</option><option value="steer">调整方向</option>
                  </select>
                </label>
                <label className="settings-preference-toggle">
                  <input type="checkbox" checked={showInternalAgents} onChange={(event) => setShowInternalAgents(event.target.checked)} />
                  <span><strong>显示内部子智能体</strong><small>在智能体列表和各处选择框中显示内部子智能体，默认关闭。</small></span>
                </label>
                {activeSection === "configuration" && <WebConfiguration />}
              </div>
            </section>

            <section
              className="settings-section"
              id="appearance"
              hidden={activeSection !== "appearance"}
            >
              <div className="settings-form settings-appearance">
                <div>
                  <strong>界面主题</strong>
                  <small>
                    选择后立即应用，并在刷新和跨页面导航后保持一致。
                  </small>
                </div>
                <ThemeSelector />

              </div>
            </section>

            <section
              className="settings-section"
              id="security"
              hidden={activeSection !== "security"}
            >
              {passwordEnabled ? (
                <form className="settings-form" onSubmit={changePassword}>
                  <label>
                    当前密码
                    <SecretInput
                      name="current_password"
                      required
                      autoComplete="current-password"
                      revealLabel="当前密码"
                    />
                  </label>
                  <label>
                    新密码
                    <SecretInput
                      name="new_password"
                      required
                      minLength={10}
                      autoComplete="new-password"
                      placeholder="至少 10 位，含大小写字母和数字"
                      revealLabel="新密码"
                    />
                  </label>
                  <label>
                    确认新密码
                    <SecretInput
                      name="confirm_password"
                      required
                      minLength={10}
                      autoComplete="new-password"
                      revealLabel="确认密码"
                    />
                  </label>
                  {passwordMessage && (
                    <p
                      className={`settings-message ${passwordMessage.kind}`}
                      role="alert"
                    >
                      {passwordMessage.text}
                    </p>
                  )}
                  <div className="settings-form-action">
                    <button type="submit" disabled={passwordPending}>
                      {passwordPending ? "正在更新…" : "修改密码"}
                    </button>
                  </div>
                </form>
              ) : (
                <div className="settings-form settings-empty">
                  <strong>当前账户通过单点登录验证</strong>
                  <p>
                    请在 Google 或 GitHub
                    中管理密码。本系统不会直接保存第三方账户密码。
                  </p>
                </div>
              )}
            </section>

            <section
              className="settings-section"
              id="data"
              hidden={activeSection !== "data"}
            >
              {activeSection === "data" && <DataLifecycleControlPlane embedded />}
            </section>

            <section
              className="settings-section"
              id="memory"
              hidden={activeSection !== "memory"}
            >
              {activeSection === "memory" && <MemoryBank embedded />}
            </section>

            <section
              className="settings-section"
              id="session"
              hidden={activeSection !== "session"}
            >
              <div className="settings-form settings-session">
                <div>
                  <span className="settings-session-dot" aria-hidden="true" />
                  <span>
                    <strong>当前浏览器</strong>
                    <small>会话有效</small>
                  </span>
                </div>
                <a href="/api/auth/logout">退出登录</a>
              </div>
            </section>

            <section
              className="settings-section"
              id="archived"
              hidden={activeSection !== "archived"}
            >
              <ArchivedTasksSettings />
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}

export default function SettingsPage() {
  return (
    <AuthProvider>
      <SettingsContent />
    </AuthProvider>
  );
}
