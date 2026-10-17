import { expect, it } from "vitest";
import { responseTextForTurn } from "../src/components/agent-thread";

it("never borrows the previous final answer while the next optimistic message waits for ownership", () => {
  const captured = { current: undefined as {messageId:string;text:string}|undefined };
  const old = {text:"上一轮最终回答",visible:true,status:"complete" as const};
  expect(responseTextForTurn("assistant-next", "上一轮原生缓存", true, old, captured, true)).toBe("");
  const waiting = {text:"",visible:false,status:"streaming" as const};
  expect(responseTextForTurn("assistant-next", "上一轮原生缓存", true, waiting, captured, true)).toBe("");
  expect(responseTextForTurn("assistant-next", "上一轮原生缓存", true, {...waiting,text:"本轮新回答",visible:true}, captured)).toBe("本轮新回答");
});
it("keeps the previous turn's cleaned response when global live state is reset", () => {
  const captured = {current:undefined as {messageId:string;text:string}|undefined};
  responseTextForTurn("assistant-old", "过程说明最终回答", true, {text:"最终回答",visible:true,status:"complete"}, captured);
  expect(responseTextForTurn("assistant-old", "过程说明最终回答", false, {text:"",visible:false,status:"idle"}, captured)).toBe("最终回答");
  expect(responseTextForTurn("assistant-old", "最终回答", false, {text:"",visible:false,status:"streaming"}, captured)).toBe("最终回答");
  expect(captured.current).toBeUndefined();
});
