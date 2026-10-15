import { describe, expect, it } from "vitest";
import { parseConversationInput, conversationInputDisplay, latestConversationInput, formatConversationAnswers } from "../src/lib/conversation-input";
const payload = {version: 1, title: "确认范围", questions: [{id: "scope", label: "选择内容", type: "multi", options: ["UI", "接口"]}, {id: "note", label: "补充要求", type: "text"}]};
const fence = (data: unknown) => "```harness-input\n" + JSON.stringify(data) + "\n```";
describe("model-driven input display protocol", () => {
  it("accepts bounded choices and free text, emits ordinary user answers", () => {
    const input = parseConversationInput(fence(payload))!;
    expect(input.questions).toHaveLength(2);
    expect(formatConversationAnswers(input, {scope: {selected: ["UI", "接口"], text: ""}})).toBeNull();
    expect(formatConversationAnswers(input, {scope: {selected: ["UI", "接口", "injected"], text: ""}, note: {selected: [], text: "保留布局"}})).toBe("确认范围\n\n选择内容\nUI；接口\n\n补充要求\n保留布局");
    expect(formatConversationAnswers(input, {scope: {selected: [], text: "只修改文案"}, note: {selected: [], text: "保留布局"}})).toContain("只修改文案");
  });
  it("rejects malformed, duplicate and oversized blocks", () => {
    for (const data of [null, {}, {...payload, version: 2}, {...payload, questions: Array(5).fill(payload.questions[0])}, {...payload, questions: [payload.questions[0], payload.questions[0]]}, {...payload, questions: [{...payload.questions[0], options: ["same", "same"]}]}, {...payload, title: "x".repeat(161)}, {...payload, questions: [{...payload.questions[0], type: "execute"}]}]) expect(parseConversationInput(fence(data))).toBeNull();
    expect(parseConversationInput(fence(payload) + "\n选好后我再继续。")?.title).toBe("确认范围");
    expect(parseConversationInput(fence(payload) + "\n" + fence(payload))).toBeNull();
  });
  it("does not re-offer older, answered or unfinished assistant requests", () => {
    const message = {id: "a", role: "assistant", status: {type: "complete"}, content: [{type: "text", text: fence(payload)}]};
    expect(latestConversationInput([message])?.messageId).toBe("a");
    expect(latestConversationInput([message, {id: "u", role: "user", content: [{type: "text", text: "answer"}]}])).toBeNull();
    expect(latestConversationInput([{...message, status: {type: "running"}}])).toBeNull();
    expect(latestConversationInput([{...message, status: {type: "incomplete"}}])).toBeNull();
    expect(latestConversationInput([])).toBeNull();
  });
  it("keeps readable history and falls back safely after interrupted streaming", () => {
    expect(conversationInputDisplay("请确认\n" + fence(payload))).toContain("选择内容（UI / 接口）");
    expect(conversationInputDisplay(fence(payload))).not.toContain("harness-input");
    const partial = '正在整理\n```harness-input\n{"version":';
    expect(conversationInputDisplay(partial, true)).toBe("正在整理");
    expect(conversationInputDisplay(partial)).toContain("请直接输入");
  });
});

it("recognizes the screenshot's unfenced form and generic JSON fences without exposing protocol text", () => {
  const data = {version:1,title:"需要你补充",questions:[{id:"claim",label:"要核实的具体主张/数据点（贴原句或描述即可）",type:"text"},{id:"material",label:"你能提供的材料来源",type:"single",options:["我直接粘贴原文", "我提供原文链接", "我把文件放进 inputs/ 目录", "请先列可能的来源"]}]};
  for (const text of [`知识库检索为空。请补充：\n${JSON.stringify(data)}`, `请补充\n\`\`\`json\n${JSON.stringify(data)}\n\`\`\``]) {
    expect(parseConversationInput(text)?.questions).toHaveLength(2);
    expect(conversationInputDisplay(text)).not.toContain('"version"');
    expect(latestConversationInput([{id:"screenshot",role:"assistant",status:{type:"complete"},content:[{type:"text",text}]}])?.input.title).toBe("需要你补充");
  }
});
it("handles quoted braces, refuses ambiguous or non-protocol JSON and suppresses only recognized partial forms", () => {
  const data={...payload,title:'解释 {x} 和 "引号"'};
  expect(parseConversationInput(JSON.stringify(data))?.title).toBe(data.title);
  expect(parseConversationInput(JSON.stringify(payload)+'\n'+JSON.stringify(payload))).toBeNull();
  expect(parseConversationInput('```python\n'+JSON.stringify(payload)+'\n```')).toBeNull();
  for(const data of [{version:1,title:"普通 JSON",items:[]},{...payload,execute:"command"},{...payload,version:2}]) {
    const text=JSON.stringify(data); expect(parseConversationInput(text)).toBeNull();expect(conversationInputDisplay(text)).toBe(text);
  }
  const partial='请补充\n{"version":1,"title":"需要你补充","questions":[{"id":"x"';
  expect(conversationInputDisplay(partial,true)).toBe('请补充');
  expect(parseConversationInput(partial)).toBeNull();
});

 it("renders the reported harness alias as a form and keeps unfinished protocol out of the stream", () => {
 const block = "```harness\n" + JSON.stringify(payload) + "\n```";
 expect(parseConversationInput(block)?.questions).toHaveLength(2);
 expect(latestConversationInput([{id:"alias",role:"assistant",status:{type:"complete"},content:[{type:"text",text:block}]}])?.messageId).toBe("alias");
 expect(conversationInputDisplay(block)).not.toContain('"questions"');
 expect(conversationInputDisplay('请确认\n```harness\n{"version":1',true)).toBe("请确认");
 expect(parseConversationInput('```harness\n{"version":1,"execute":"shell"}\n```')).toBeNull();
 });

it("keeps all over-limit choices readable without exposing protocol or accepting an invalid card", () => {
 const data={version:1,title:"确认查询范围",questions:[{id:"scope",label:"查询哪些维度",type:"multi",options:Array.from({length:9},(_,i)=>`维度 ${i+1}`)}]};
 for (const language of ["harness-input","harness","json"]) {
  const text=`请确认。\n\n\`\`\`${language}\n${JSON.stringify(data)}\n\`\`\`\n\n后续说明。`;
  expect(parseConversationInput(text)).toBeNull();
  const display=conversationInputDisplay(text);
  expect(display).toContain("维度 9");expect(display).toContain("后续说明。");
  expect(display).not.toMatch(/```|"questions"|"version"/);
 }
});

it("consumes the complete form envelope even when the closing fence is missing or indented", () => {
 for(const text of [fence(payload).slice(0,-4),fence(payload).replace(/```$/,"  ```"),fence(payload).replaceAll("```","~~~")]) {
  expect(parseConversationInput(text)?.title).toBe(payload.title);
  expect(conversationInputDisplay(text)).not.toMatch(/```|~~~|harness-input|"questions"/);
 }
});

it("degrades malformed internal forms without hiding ordinary code or JSON", () => {
 const malformed='说明。\n```harness-input\n{"version":1,"questions":';
 expect(conversationInputDisplay(malformed,true)).toBe("说明。");
 expect(conversationInputDisplay(malformed)).toContain("请直接输入");
 expect(conversationInputDisplay(malformed)).not.toContain('"questions"');
 const ordinary='```json\n{"version":1,"items":[1,2]}\n```';
 expect(conversationInputDisplay(ordinary)).toBe(ordinary);
 expect(conversationInputDisplay('```python\nprint("hello")\n```')).toContain('print("hello")');
});

it("preserves an internal protocol shown literally inside an outer code example", () => {
 const example="````markdown\n```harness-input\n{invalid}\n```\n````";
 expect(conversationInputDisplay(example)).toBe(example);
});
