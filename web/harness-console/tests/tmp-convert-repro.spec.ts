import { readFileSync } from "node:fs";
import { fromAgUiMessages } from "@assistant-ui/react-ag-ui";
import { describe, expect, it } from "vitest";

describe("automation history conversion repro", () => {
  it("converts the production history payload", () => {
    const history = JSON.parse(readFileSync("/tmp/hist.json", "utf8"));
    const converted = fromAgUiMessages(history.messages, { showThinking: true });
    console.log("converted:", converted.length, converted.map(m => `${m.role}:${JSON.stringify(m.content).slice(0, 40)}`));
    expect(converted.length).toBeGreaterThan(0);
  });
});
