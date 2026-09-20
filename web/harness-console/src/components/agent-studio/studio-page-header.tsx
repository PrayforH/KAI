"use client";

import { type ReactNode } from "react";
import styles from "./studio-page-header.module.css";

export interface StudioPageTab<T extends string> {
  id: T;
  label: string;
  icon?: ReactNode;
}

/**
 * One header contract for every Studio page: section tabs on the left, the
 * page's own actions on the right. The tabs stay put while the body scrolls,
 * which is the layout the automation page established.
 */
export function StudioPageHeader<T extends string>({
  tabs,
  active,
  onSelect,
  ariaLabel,
  children,
}: {
  tabs: ReadonlyArray<StudioPageTab<T>>;
  active: T;
  onSelect: (id: T) => void;
  ariaLabel: string;
  /** Page-level actions, right aligned. */
  children?: ReactNode;
}) {
  return (
    <header className={styles.header}>
      <div className={styles.tabs} role="tablist" aria-label={ariaLabel}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={tab.id === active}
            className={tab.id === active ? styles.tabActive : styles.tab}
            onClick={() => onSelect(tab.id)}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>
      {children ? <div className={styles.actions}>{children}</div> : null}
    </header>
  );
}

/** Link-based variant for tabs that map onto routes instead of local state. */
export function StudioPageHeaderLinks({
  links,
  ariaLabel,
  onNavigate,
  children,
}: {
  links: ReadonlyArray<{ id: string; label: string; href: string; active: boolean; icon?: ReactNode }>;
  ariaLabel: string;
  /** Client-side navigation; the href stays for prefetch and middle-click. */
  onNavigate?: (href: string) => void;
  children?: ReactNode;
}) {
  return (
    <header className={styles.header}>
      <nav className={styles.tabs} aria-label={ariaLabel}>
        {links.map((link) => (
          <a
            key={link.id}
            href={link.href}
            aria-current={link.active ? "page" : undefined}
            className={link.active ? styles.tabActive : styles.tab}
            onClick={(event) => {
              if (!onNavigate) return;
              if (event.metaKey || event.ctrlKey || event.shiftKey) return;
              event.preventDefault();
              onNavigate(link.href);
            }}
          >
            {link.icon}
            {link.label}
          </a>
        ))}
      </nav>
      {children ? <div className={styles.actions}>{children}</div> : null}
    </header>
  );
}
