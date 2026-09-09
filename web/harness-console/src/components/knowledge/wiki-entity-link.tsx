"use client";

import type { ComponentPropsWithoutRef } from "react";
import styles from "./wiki-entity-link.module.css";

/** Shared inline entity link for answers, Wiki pages and graph drawers. */
export function WikiEntityLink({ className, ...props }: ComponentPropsWithoutRef<"button">) {
  return <button {...props} type="button" className={[styles.link, className].filter(Boolean).join(" ")} />;
}
