"use client";

import Link from "next/link";
import { useEffect } from "react";
import { PRODUCT_NAME } from "../components/product-brand";

function RecoveryMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path d="M24 7a17 17 0 1 0 15.4 9.8" />
      <path d="M40 8v10H30" />
      <path d="M24 16v9" />
      <circle cx="24" cy="32" r="1.5" />
    </svg>
  );
}

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="recovery-page" id="main-content">
      <section className="recovery-card" aria-labelledby="error-title">
        <div className="recovery-mark is-error"><RecoveryMark /></div>
        <p className="recovery-code">WORKSPACE INTERRUPTED</p>
        <h1 id="error-title">页面暂时没有完成加载</h1>
        <p className="recovery-copy">
          已保存的任务和工作区记录不会受影响。先重试当前页面；如果问题仍在，可以返回任务继续工作。
        </p>
        <div className="recovery-actions">
          <button className="recovery-primary" type="button" onClick={reset}>重新加载此页面</button>
          <Link className="recovery-secondary" href="/">返回任务</Link>
        </div>
        {error.digest ? <small className="recovery-reference">参考编号 {error.digest}</small> : null}
      </section>
      <p className="recovery-footnote">{PRODUCT_NAME} · 已有内容仍保存在原位置</p>
    </main>
  );
}
