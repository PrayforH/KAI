"use client";

import type { ComponentPropsWithoutRef, ReactNode } from "react";
import styles from "./citation-link.module.css";

/**
 * Inline document chip for a RAG citation. A cited excerpt is a document slice,
 * so it carries the document glyph instead of the underlined Wiki entity style;
 * both open their own drawer behind the click.
 */
export function CitationLink({
  className,
  children,
  ...props
}: ComponentPropsWithoutRef<"button"> & { children?: ReactNode }) {
  return (
    <button {...props} type="button" className={[styles.chip, className].filter(Boolean).join(" ")}>
      <svg className={styles.glyph} viewBox="0 0 20 20" aria-hidden="true">
        <path d="M4.5 3.5h7l4 4v9h-11z" />
        <path d="M11.5 3.5v4h4M7.5 11h5m-5 2.8h5" />
      </svg>
      <span className={styles.title}>{children}</span>
    </button>
  );
}
