/**
 * Copy text in both secure HTTPS contexts and private-network HTTP deployments.
 *
 * The modern Clipboard API is intentionally unavailable on most non-localhost
 * HTTP origins. AXIS is also deployed on private IPs during acceptance, so
 * keep the user gesture and fall back to a temporary selected textarea.
 */
export async function writeTextToClipboard(value: string): Promise<boolean> {
  if (!value) return false;

  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Restrictive browser policy or an HTTP origin: use the legacy selection
    // path below instead of surfacing an unhandled promise rejection.
  }

  if (typeof document === "undefined" || !document.body) return false;
  const activeElement = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.inset = "0 auto auto -9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, value.length);
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
    activeElement?.focus({ preventScroll: true });
  }
}
