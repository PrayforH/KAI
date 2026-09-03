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
      <svg viewBox="0 0 32 32" focusable="false">
        <path
          className="product-brand-track"
          d="M5.75 24 12 8l6.25 16M8.6 17h6.8"
        />
        <path className="product-brand-core" d="m18.5 8 7.5 16M26 8l-7.5 16" />
      </svg>
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
