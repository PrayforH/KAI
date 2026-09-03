import Link from "next/link";
import { PRODUCT_NAME } from "../components/product-brand";

function WayfindingMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path d="M8 15.5 24 7l16 8.5v17L24 41 8 32.5Z" />
      <path d="m17 27 5-7 5 4 5-7" />
      <circle cx="17" cy="27" r="1.5" />
      <circle cx="32" cy="17" r="1.5" />
    </svg>
  );
}

export default function NotFound() {
  return (
    <main className="recovery-page" id="main-content">
      <section className="recovery-card" aria-labelledby="not-found-title">
        <div className="recovery-mark"><WayfindingMark /></div>
        <p className="recovery-code">404 · PAGE NOT FOUND</p>
        <h1 id="not-found-title">这个入口不存在</h1>
        <p className="recovery-copy">
          地址可能已经改变。返回任务继续工作，或去智能体中心查看可用智能体。
        </p>
        <div className="recovery-actions">
          <Link className="recovery-primary" href="/">返回任务</Link>
          <Link className="recovery-secondary" href="/studio/agents">进入智能体构建</Link>
        </div>
      </section>
      <p className="recovery-footnote">{PRODUCT_NAME} · 工作不会因为走错入口而丢失</p>
    </main>
  );
}
