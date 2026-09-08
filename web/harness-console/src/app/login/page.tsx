"use client";

import { type FormEvent, useEffect, useState } from "react";
import { publishAuthEvent } from "../../lib/auth-coordination";
import { PRODUCT_NAME } from "../../components/product-brand";
import { SecretInput } from "../../components/secret-input";
import alpinePeak from "./assets/alpine-peak.jpg";

type AuthConfig = {
  registration_enabled: boolean;
  providers: { google: boolean; github: boolean };
};

const ERROR_MESSAGES: Record<string, string> = {
  session_expired: "登录状态已失效，请重新登录。",
  session_replaced: "该账号已在其他设备登录，本设备已安全退出。",
  account_changed: "当前浏览器已登录其他账号，请确认后重新登录。",
  sso_unavailable: "该登录方式尚未配置。",
  sso_state_invalid: "登录请求已经过期，请重新尝试。",
  sso_exchange_failed: "第三方登录没有完成，请重试。",
};

export default function LoginPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [configUnavailable, setConfigUnavailable] = useState(false);

  useEffect(() => {
    fetch("/api/auth/config", { cache: "no-store" })
      .then((response) => response.json())
      .then((value: AuthConfig) => setConfig(value))
      .catch(() => {
        setConfigUnavailable(true);
        setError("认证服务暂时不可用。");
      });
    const code = new URLSearchParams(window.location.search).get("error");
    if (code) setError(ERROR_MESSAGES[code] ?? "登录没有完成，请重新尝试。");
    if (new URLSearchParams(window.location.search).get("password") === "changed") {
      setNotice("密码已更新，请使用新密码重新登录。");
    }
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const payload = {
      email: String(form.get("email") ?? ""),
      password: String(form.get("password") ?? ""),
      ...(mode === "register"
        ? { display_name: String(form.get("display_name") ?? "") }
        : {}),
    };
    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = (await response.json()) as {
        error?: { message?: string };
        user?: { user_id: string };
      };
      if (!response.ok) {
        setError(result.error?.message ?? "登录信息无法验证。");
        return;
      }
      if (result.user?.user_id) {
        publishAuthEvent({ type: "signed_in", userId: result.user.user_id });
      }
      window.location.replace("/");
    } catch {
      setError("认证服务暂时不可用。");
    } finally {
      setPending(false);
    }
  }

  const hasSso = Boolean(config?.providers.google || config?.providers.github);
  const systemStatus = configUnavailable
    ? "认证服务待恢复"
    : config
      ? "系统就绪"
      : "正在连接";
  return (
    <main className="login-shell" id="main-content">
      <div className="login-workspace-preview" aria-hidden="true">
        <aside>
          <div className="login-preview-brand">
            <span>K</span>
            <strong>{PRODUCT_NAME}</strong>
          </div>
          <div className="login-preview-action">＋&nbsp;&nbsp;新建任务</div>
          <nav>
            <span>智能体</span>
            <span>技能 / MCP</span>
            <span>项目</span>
          </nav>
          <div className="login-preview-lines"><i /><i /><i /></div>
        </aside>
        <section>
          <div className="login-preview-thread"><i /><i /><i /></div>
          <div className="login-preview-composer">描述你要完成的任务… <span>↑</span></div>
        </section>
      </div>

      <section className="login-panel" aria-label={mode === "login" ? "登录" : "创建账户"}>
        <article className="login-card">
          <header className="login-card-banner">
            <div className="login-card-brand">
              <span className="login-brand-mark" aria-hidden="true">K</span>
              <strong>{PRODUCT_NAME}</strong>
            </div>
            <p
              className="login-system-status"
              data-state={configUnavailable ? "unavailable" : config ? "ready" : "loading"}
              role="status"
            >
              <span aria-hidden="true" />
              {systemStatus}
            </p>
          </header>

          <div className="login-card-body">
            <section className="login-access-context" aria-label="阿尔卑斯雪峰">
              <img src={alpinePeak.src} alt="晨光中的阿尔卑斯雪峰" />
              <div className="login-image-caption">
                <span>Agent workbench</span>
                <strong>让复杂任务保持清晰。</strong>
              </div>
            </section>

            <section className="login-auth-panel">
              <header>
                <h2>{mode === "login" ? "邮箱登录" : "创建工作区账户"}</h2>
                <span>{mode === "login" ? "使用你的工作区账户继续。" : "首位注册用户将成为工作区所有者。"}</span>
              </header>

              {hasSso && (
                <div className="sso-actions">
                  {config?.providers.google && <a href="/api/auth/oauth/google/start"><GoogleIcon />使用 Google 登录</a>}
                  {config?.providers.github && <a href="/api/auth/oauth/github/start"><GithubIcon />使用 GitHub 登录</a>}
                </div>
              )}
              {hasSso && <div className="login-divider"><span>或使用邮箱</span></div>}

              <form onSubmit={submit}>
                {mode === "register" && (
                  <label>姓名<input name="display_name" autoComplete="name" required placeholder="你希望显示的名称" /></label>
                )}
                <label>企业邮箱地址<input name="email" type="email" autoComplete="email" required placeholder="name@company.com" /></label>
                <label>安全通行密码<SecretInput name="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required minLength={10} placeholder="至少 10 位，含大小写与数字" revealLabel="密码" /></label>
                {error && <p className="login-error" role="alert">{error}</p>}
                {notice && <p className="login-notice" role="status">{notice}</p>}
                <button className="login-submit" type="submit" disabled={pending}>
                  <span>{pending ? "正在验证…" : mode === "login" ? "登录工作台" : "创建并登录"}</span>
                  {!pending && <span aria-hidden="true">→</span>}
                </button>
              </form>

              {config?.registration_enabled && (
                <button className="auth-mode-switch" type="button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
                  {mode === "login" ? "没有账户？创建一个" : "已有账户？返回登录"}
                </button>
              )}
            </section>
          </div>

          <footer className="login-card-footer">
            <span>继续即表示你已了解工作区的数据与安全策略</span>
            <nav aria-label="登录页说明">
              <span>服务条款</span>
              <span>隐私保护</span>
              <span>会话由 API 安全验签</span>
            </nav>
          </footer>
        </article>
      </section>
    </main>
  );
}

function GoogleIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21.4 12.2c0-.7-.1-1.4-.2-2H12v3.8h5.3a4.5 4.5 0 0 1-2 3v2.5h3.2c1.9-1.8 2.9-4.3 2.9-7.3Z" fill="#4285F4"/><path d="M12 21.8c2.7 0 5-.9 6.6-2.4l-3.2-2.5c-.9.6-2 1-3.4 1a5.8 5.8 0 0 1-5.4-4H3.3v2.6a10 10 0 0 0 8.7 5.3Z" fill="#34A853"/><path d="M6.6 13.9a6 6 0 0 1 0-3.8V7.5H3.3a10 10 0 0 0 0 9l3.3-2.6Z" fill="#FBBC05"/><path d="M12 6.1c1.6 0 3 .5 4.1 1.6l3-3A10 10 0 0 0 3.3 7.5l3.3 2.6a5.8 5.8 0 0 1 5.4-4Z" fill="#EA4335"/></svg>;
}

function GithubIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 .8a11.4 11.4 0 0 0-3.6 22.2c.6.1.8-.2.8-.5v-2c-3.3.7-4-1.4-4-1.4-.6-1.4-1.4-1.8-1.4-1.8-1.1-.8.1-.8.1-.8 1.2.1 1.9 1.3 1.9 1.3 1.1 1.9 2.9 1.3 3.6 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11 11 0 0 1 6 0C14.3 3.8 15.3 4 15.3 4c.6 1.7.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v4c0 .3.2.6.8.5A11.4 11.4 0 0 0 12 .8Z"/></svg>;
}
