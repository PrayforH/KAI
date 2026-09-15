export type AguiEvent = { type: string; [key: string]: unknown };

export function parseSseBlock(block: string): { id?: string; event?: AguiEvent } {
  let id: string | undefined;
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return { id };
  return { id, event: JSON.parse(data.join("\n")) as AguiEvent };
}

export async function streamAgui(
  response: Response,
  onEvent: (id: string | undefined, event: AguiEvent) => void,
) {
  if (!response.ok || !response.body) throw new Error(`AG-UI stream failed: ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const parsed = parseSseBlock(block);
        if (parsed.event) onEvent(parsed.id, parsed.event);
      }
      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
