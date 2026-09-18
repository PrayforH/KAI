"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "./auth-provider";
import { ProductIcon } from "./product-icon";
import { useColorMode, type ColorMode } from "../lib/color-mode";

const ROLE_LABELS = {
  owner: "所有者",
  admin: "管理员",
  member: "成员",
  viewer: "只读",
} as const;

function PaletteIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 3.25a6.75 6.75 0 0 0 0 13.5c1 0 1.6-.6 1.6-1.35 0-.7-.45-1.05-.45-1.75 0-.8.65-1.4 1.55-1.4h1.2c1.6 0 2.85-1.25 2.85-2.85C16.75 5.75 13.9 3.25 10 3.25Z" />
      <path d="M6.9 9.4h.01M9.3 6.6h.01M12.4 6.9h.01M6.6 12.4h.01" />
    </svg>
  );
}

function ThemeQuickSwitch() {
  const { mode, setColorMode } = useColorMode();
  const options: ReadonlyArray<{ value: ColorMode; label: string }> = [
    { value: "dark", label: "深色主题" },
    { value: "light", label: "浅色主题" },
  ];
  return (
    <div className="account-theme" role="radiogroup" aria-label="主题外观">
      <span className="account-theme-label">
        <PaletteIcon />
        主题外观
      </span>
      <div className="account-theme-options">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={mode === option.value}
            className="account-theme-option"
            onClick={() => setColorMode(option.value)}
          >
            <span>{option.label}</span>
            {mode === option.value && (
              <svg className="account-theme-check" viewBox="0 0 16 16" aria-hidden="true">
                <path d="m3.5 8.5 3 3 6-7" />
              </svg>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

export function AccountMenu() {
  const { user, membership } = useAuth();
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const initial = (user.display_name || user.email).trim().slice(0, 1).toUpperCase();

  useEffect(() => {
    if (!open) return;
    function closeOnOutsideClick(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="account-menu" ref={menuRef}>
      <button
        className="account-trigger"
        type="button"
        aria-expanded={open}
        aria-label="账户菜单"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="account-trigger-avatar" aria-hidden="true">{initial}</span>
        <span className="account-trigger-copy">
          <strong>{user.display_name}</strong>
          <small>{ROLE_LABELS[membership.role]}</small>
        </span>
        <svg className="account-trigger-chevron" viewBox="0 0 16 16" aria-hidden="true">
          <path d={open ? "m4 10 4-4 4 4" : "m4 6 4 4 4-4"} />
        </svg>
      </button>
      {open && (
        <div className="account-popover" role="dialog" aria-label="当前账户">
          <div className="account-identity">
            <strong>{user.display_name}</strong>
            <span>{user.email}</span>
          </div>
          <ThemeQuickSwitch />
          <nav className="account-actions" aria-label="账户操作">
            <Link className="account-settings" href="/settings">
              <ProductIcon name="settings" />
              个人设置
            </Link>
            <a className="account-logout" href="/api/auth/logout">
              <ProductIcon name="logout" />
              退出登录
            </a>
          </nav>
        </div>
      )}
    </div>
  );
}
