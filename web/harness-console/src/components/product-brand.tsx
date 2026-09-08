export const PRODUCT_NAME = "KAI WORKBENCH";
export const PRODUCT_DESCRIPTOR = "AGENT OPERATIONS";
export const PRODUCT_DESCRIPTION = "面向组织的智能体任务、能力与运行治理平台";

export function ProductBrandMark({
  className = "",
}: {
  className?: string;
}) {
  return (
    <span
      className={`product-brand-mark ${className}`.trim()}
      aria-hidden="true"
    >
      {/* Native image keeps the same transparent mark in every brand surface. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/brand/kai-mark-v2.png" alt="" width="40" height="40" draggable={false} />
    </span>
  );
}

export function ProductBrandCopy({
  compact = false,
  className = "",
}: {
  compact?: boolean;
  className?: string;
}) {
  return (
    <span className={`product-brand-copy ${className}`.trim()}>
      <strong>{PRODUCT_NAME}</strong>
      {!compact && <small>{PRODUCT_DESCRIPTOR}</small>}
    </span>
  );
}

export function ProductLoading({ label = "正在加载…" }: { label?: string }) {
  return <div className="product-loading"><ProductBrandMark /><span>{label}</span></div>;
}
