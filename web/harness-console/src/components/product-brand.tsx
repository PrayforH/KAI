export const PRODUCT_NAME = "sapling preview";
export const PRODUCT_DESCRIPTOR = "智能体预研平台";
export const PRODUCT_DESCRIPTION = "探索智能体能力，从想法、构建到效果验证。";

export function ProductBrandMark({ className = "" }: { className?: string }) {
  return <span className={`product-brand-mark ${className}`.trim()} aria-hidden="true">
    <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M16 27V16M16 22c0-5 3-9 9-10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M16 18C7 18 5 12 5 6c7 0 12 4 11 12Z" fill="currentColor" />
      <path d="M17 20c-1-8 3-13 10-14 1 7-2 12-10 14Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  </span>;
}

export function ProductWordmark() {
  return <strong className="product-wordmark"><span>sapling</span>{" "}<span className="product-preview-badge">preview</span></strong>;
}

export function ProductBrandCopy({ compact = false, className = "" }: { compact?: boolean; className?: string }) {
  return <span className={`product-brand-copy ${className}`.trim()}>
    <ProductWordmark />
    {!compact && <small>{PRODUCT_DESCRIPTOR}</small>}
  </span>;
}

export function ProductLoading({ label = "正在加载…" }: { label?: string }) {
  return <div className="product-loading"><ProductBrandMark /><span>{label}</span></div>;
}
