/**
 * A knowledge base identifier travels through URLs, the Studio API and the
 * WeKnora engine, so it stays lowercase latin: `^[a-z][a-z0-9-]*$`.
 */
const REFERENCE_PATTERN = /^[a-z][a-z0-9-]*$/;
const MAX_REFERENCE_LENGTH = 128;

export function isValidKnowledgeReference(value: string): boolean {
  const candidate = value.trim();
  return (
    candidate.length > 0 &&
    candidate.length <= MAX_REFERENCE_LENGTH &&
    REFERENCE_PATTERN.test(candidate)
  );
}

/**
 * Suggest an identifier from a display name while the operator has not typed
 * one. Names without latin characters (for example 中文) yield no suggestion,
 * so the field stays empty and the hint explains what is allowed instead of
 * submitting a value the API will reject.
 */
export function slugifyKnowledgeReference(name: string): string {
  const slug = name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!slug) return "";
  const prefixed = /^[a-z]/.test(slug) ? slug : `kb-${slug}`;
  return prefixed.slice(0, MAX_REFERENCE_LENGTH).replace(/-+$/, "");
}
