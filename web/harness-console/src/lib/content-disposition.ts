export function inlineContentDisposition(value: string | null): string {
  if (!value) return "inline";
  return value.replace(/^\s*attachment(?=\s*(?:;|$))/i, "inline");
}
