"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  type AuthInvalidationReason,
  subscribeAuthEvents,
} from "../lib/auth-coordination";
import { ProductBrandMark } from "./product-brand";
import type { AuthUser, Membership } from "../lib/auth-session";

type AuthContextValue = {
  user: AuthUser;
  membership: Membership;
  passwordEnabled: boolean;
};

type AuthProfile = {
  user: AuthUser;
  membership: Membership;
  password_enabled: boolean;
};

// Route transitions remount the page-level provider. Keep the last verified
// profile in the client module so moving between Tasks and Studio does not
// flash a blocking auth screen while the same session is revalidated.
let cachedAuthProfile: AuthProfile | null = null;

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<AuthProfile | null>(cachedAuthProfile);
  const profileRef = useRef<AuthProfile | null>(cachedAuthProfile);
  const [invalidation, setInvalidation] = useState<AuthInvalidationReason | null>(null);

  useEffect(() => {
    let active = true;
    let checking = false;
    const invalidate = (reason: AuthInvalidationReason) => {
      if (!active) return;
      cachedAuthProfile = null;
      setInvalidation((current) => current ?? reason);
    };
    const verify = async () => {
      if (checking || !active) return;
      checking = true;
      try {
        const response = await fetch("/api/auth/session", { cache: "no-store" });
        if (!response.ok) {
          const reason = response.headers.get("x-harness-auth-error") === "session_replaced"
            ? "session_replaced"
            : "session_expired";
          if (profileRef.current) invalidate(reason);
          else window.location.replace(`/login?error=${reason}`);
          return;
        }
        const nextProfile = (await response.json()) as AuthProfile;
        const current = profileRef.current;
        if (current && current.user.user_id !== nextProfile.user.user_id) {
          invalidate("account_changed");
          return;
        }
        cachedAuthProfile = nextProfile;
        profileRef.current = nextProfile;
        if (active) setProfile(nextProfile);
      } catch {
        // A transient network failure must not be presented as an account takeover.
      } finally {
        checking = false;
      }
    };
    const unsubscribe = subscribeAuthEvents((event) => {
      if (event.type === "invalidated") {
        invalidate(event.reason);
        return;
      }
      const currentUserId = profileRef.current?.user.user_id;
      if (currentUserId) {
        invalidate(event.userId === currentUserId ? "session_replaced" : "account_changed");
      }
    });
    const onFocus = () => { void verify(); };
    const onVisibility = () => {
      if (document.visibilityState === "visible") void verify();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    const interval = window.setInterval(() => { void verify(); }, 15_000);
    void verify();
    return () => {
      active = false;
      unsubscribe();
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
      window.clearInterval(interval);
    };
  }, []);

  const value = useMemo<AuthContextValue | null>(() => {
    if (!profile) return null;
    return {
      user: profile.user,
      membership: profile.membership,
      passwordEnabled: profile.password_enabled,
    };
  }, [profile]);

  if (!value) {
    return (
      <main className="auth-loading" aria-busy="true" aria-label="正在验证登录状态">
        <ProductBrandMark className="auth-loading-mark" />
        <span>正在进入工作台…</span>
      </main>
    );
  }
  const invalidationCopy = invalidation === "account_changed"
    ? {
        title: "当前浏览器已切换到其他账号",
        detail: "浏览器窗口共享登录 Cookie。为避免把当前输入提交到另一个账号，本窗口已停止所有操作。",
      }
    : invalidation === "session_replaced"
      ? {
          title: "账号已在其他窗口或设备登录",
          detail: "按照单设备登录规则，当前会话已失效。尚未提交的文字不会发送。",
        }
      : {
          title: "登录状态已失效",
          detail: "当前页面已停止操作，请重新登录后继续。",
        };
  return (
    <AuthContext.Provider value={value}>
      <div className="auth-session-content" inert={Boolean(invalidation)}>{children}</div>
      {invalidation && (
        <div className="auth-session-blocker" role="presentation">
          <section role="alertdialog" aria-modal="true" aria-labelledby="auth-session-title" aria-describedby="auth-session-detail">
            <span className="auth-session-icon" aria-hidden="true">!</span>
            <p>登录安全保护</p>
            <h2 id="auth-session-title">{invalidationCopy.title}</h2>
            <span id="auth-session-detail">{invalidationCopy.detail}</span>
            <button autoFocus type="button" onClick={() => window.location.replace(`/login?error=${invalidation}`)}>
              重新登录
            </button>
          </section>
        </div>
      )}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
