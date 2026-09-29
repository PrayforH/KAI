import { expect, it } from "vitest";
import { normalizeTemperatureRanges } from "../src/lib/message-text";

it("keeps temperature ranges from opening a cross-sentence deletion", () => {
  expect(normalizeTemperatureRanges("18~~24℃，我采用覆盖全周的官方通报口径 17~~26℃"))
    .toBe("18～24℃，我采用覆盖全周的官方通报口径 17～26℃");
  expect(normalizeTemperatureRanges("-5.5~-1℃ / 18℃ ~~ 24℃ / 17~26°C"))
    .toBe("-5.5～-1℃ / 18℃～24℃ / 17～26°C");
});

it("preserves deliberate deletion and literal code", () => {
  const code = "~~旧数据~~ ~~18℃~~ `18~~24℃`\n```text\n18~~24℃\n```\n~~~\n17~~26℃\n~~~\n    18~~24℃";
  expect(normalizeTemperatureRanges(code)).toBe(code);
  expect(normalizeTemperatureRanges("`18~~24℃")).toBe("`18~~24℃");
  expect(normalizeTemperatureRanges("``a ` 18~~24℃`` 后面 17~~26℃"))
    .toBe("``a ` 18~~24℃`` 后面 17～26℃");
});
