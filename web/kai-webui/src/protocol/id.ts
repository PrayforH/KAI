export function randomId() {
  if (typeof window.crypto?.randomUUID === "function") return window.crypto.randomUUID();
  const parts = new Uint32Array(4);
  try {
    window.crypto?.getRandomValues(parts);
  } catch {
    for (let index = 0; index < parts.length; index += 1) parts[index] = Math.floor(Math.random() * 0xffffffff);
  }
  return `${Date.now().toString(36)}-${Array.from(parts, (part) => part.toString(36)).join("-")}`;
}
