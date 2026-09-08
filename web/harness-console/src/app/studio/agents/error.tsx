"use client";

import Link from "next/link";
import { useEffect } from "react";
import styles from "../../../components/agent-studio/agent-studio.module.css";
import { PRODUCT_NAME } from "../../../components/product-brand";

export default function AgentStudioError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Agent Studio route failed", error);
  }, [error]);

  return (
    <main className={styles.studioStateShell}>
      <section className={styles.studioStateCard} role="alert">
        <span className={styles.studioStateMark} aria-hidden="true">!</span>
        <h1>{PRODUCT_NAME}没有正常加载</h1>
        <p>
          当前配置不会因此发布或覆盖。可以重试加载；如果仍失败，先返回任务页，草稿仍保留在浏览器或控制面中。
        </p>
        <code>{error.digest ? `error digest: ${error.digest}` : "route render failed"}</code>
        <div className={styles.studioStateActions}>
          <button type="button" onClick={reset}>重新加载</button>
          <Link href="/">返回任务页</Link>
        </div>
      </section>
    </main>
  );
}
