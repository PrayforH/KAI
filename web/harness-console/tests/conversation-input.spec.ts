import { describe, expect, it } from "vitest";
import { parseConversationInput, conversationInputDisplay, conversationInputForDisplay, latestConversationInput, formatConversationAnswers, isSensitiveQuestion } from "../src/lib/conversation-input";
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

it("accepts a paginated choice list without exposing protocol", () => {
 const data={version:1,title:"确认查询范围",questions:[{id:"scope",label:"查询哪些维度",type:"multi",options:Array.from({length:9},(_,i)=>`维度 ${i+1}`)}]};
 for (const language of ["harness-input","harness","json"]) {
  const text=`请确认。\n\n\`\`\`${language}\n${JSON.stringify(data)}\n\`\`\`\n\n后续说明。`;
  expect(parseConversationInput(text)?.questions[0].options).toHaveLength(9);
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

it("accepts longer generic forms within transport bounds and never truncates multi-selection", () => {
 const options=Array.from({length:128},(_,i)=>`选项${i+1}`);
 const data={version:1 as const,title:"查询范围",questions:[{id:"scope",label:"选择范围",type:"multi" as const,options}]};
 const input=parseConversationInput(fence(data))!;
 expect(input.questions[0].options).toHaveLength(128);
 expect(formatConversationAnswers(input,{scope:{selected:options.slice(0,20),text:""}})).toContain("选项20");
 expect(parseConversationInput(fence({...data,questions:[{...data.questions[0],options:[...options,"额外选项"]}]}))).toBeNull();
 const questions=Array.from({length:5},(_,i)=>({id:`q${i}`,label:`问题${i}`,type:"text"}));
 expect(parseConversationInput(fence({version:1,title:"补充信息",questions}))?.questions).toHaveLength(5);
});
it("keeps the actual unanswered question when background results arrive", () => {
  const question = { id: "assistant-question", role: "assistant", status: {type: "complete"}, content: [{type: "text", text: fence(payload)}] };
  const result = {...question, id: "notice-background", content: [{type: "text", text: fence({...payload, title: "Quoted configuration"})}]};
  expect(latestConversationInput([question, result])?.messageId).toBe("assistant-question");
  expect(latestConversationInput([result])).toBeNull();
  expect(latestConversationInput([question, {id: "user-answer", role: "user", content: [{type: "text", text: "网页"}]}, result])).toBeNull();
});


it("keeps a main Bot question active when a child result arrives", async () => {
  const { latestConversationInput } = await import("../src/lib/conversation-input");
  const question = {id:"main-question",role:"assistant",status:{type:"complete"},content:[
    {type:"text",text:fence({version:1,title:"选择",questions:[{id:"color",label:"颜色",type:"single",options:["蓝色","绿色"]}]})}]};
  const notice = {id:"mention-result-child",role:"assistant",content:[
    {type:"tool-call",toolName:"harness_notice_source"},{type:"text",text:"我在等待颜色"}]};
  expect(latestConversationInput([question,notice])?.messageId).toBe("main-question");
  expect(latestConversationInput([question,notice,{id:"answer",role:"user",content:[{type:"text",text:"绿色"}]}])).toBeNull();
});
});

describe("near-miss forms still render as a question instead of raw JSON", () => {
  const nearMiss = {
    version: 1, title: "确认下一步", extra: "ignored",
    questions: [
      { id: "scope", label: "方案文档范围", type: "single", options: [{ label: "路线 A" }, { value: "路线 B" }] },
      { key: "notes", question: "补充说明", type: "textarea" },
      { id: "scope", label: "重复 id", type: "single", options: ["保留", "丢弃"] },
    ],
  };
  const block = "```json\n" + JSON.stringify(nearMiss) + "\n```";
  const text = "四、下一步等你定。\n\n" + block;

  it("recovers the questions, ids and options a model got slightly wrong", () => {
    expect(parseConversationInput(text)).toBeNull();
    const input = conversationInputForDisplay(text)!;
    expect(input.title).toBe("确认下一步");
    expect(input.questions.map(q => [q.id, q.type])).toEqual([["scope", "single"], ["notes", "text"], ["scope-2", "single"]]);
    expect(input.questions[0].options).toEqual(["路线 A", "路线 B"]);
  });

  it("hides the block from the answer, with or without the card", () => {
    const shown = conversationInputDisplay(text);
    expect(shown).not.toMatch(/"questions"|"version"|```/);
    expect(shown).toContain("方案文档范围（路线 A / 路线 B）");
    expect(conversationInputDisplay(text, false, true)).toBe("四、下一步等你定。");
  });

  it("offers the recovered form as the pending question", () => {
    const message = { id: "near", role: "assistant", status: { type: "complete" }, content: [{ type: "text", text }] };
    expect(latestConversationInput([message])?.messageId).toBe("near");
  });

  it("never prints an unrecoverable protocol-shaped fence as code", () => {
    const broken = '```json\n{"version":1,"questions":[{"options":[]}]}\n```';
    expect(conversationInputDisplay("请确认。\n" + broken)).not.toContain('"questions"');
    expect(conversationInputDisplay(broken)).toContain("请直接输入");
  });
});

describe("a second form block never falls through as raw JSON", () => {
  const strict = fence(payload);
  // Protocol-shaped but no label the card can use, so recovery gives up on it.
  const unrecoverable = '```json\n{"version":1,"title":"坏表单","questions":[{"id":"x","options":["甲","乙"]}]}\n```';
  const brokenAlias = '```harness-input\n{"version":1,"questions":\n```';
  const nearMiss = '```json\n' + JSON.stringify({version:1,title:"下一步",extra:"x",questions:[{key:"route",label:"选路线",type:"single",options:[{label:"路线 A"},{value:"路线 B"}]}]}) + '\n```';

  it("cleans the rest of the message after a strict form is rendered", () => {
    const text = "请确认。\n" + strict + "\n另外：\n" + unrecoverable;
    expect(parseConversationInput(text)?.title).toBe("确认范围");
    const shown = conversationInputDisplay(text);
    expect(shown).toContain("选择内容（UI / 接口）");
    expect(shown).toContain("请直接输入");
    expect(shown).not.toMatch(/"questions"|"version"|```/);
    const hidden = conversationInputDisplay(text, false, true);
    expect(hidden).toBe("请确认。\n另外：\n表单信息未能识别，请直接输入你的选择或补充信息。");
    expect(hidden).not.toContain('"questions"');
  });

  it("cleans the rest after a recovered form is rendered", () => {
    const text = "请确认。\n" + nearMiss + "\n另外：\n" + brokenAlias;
    expect(parseConversationInput(text)).toBeNull();
    expect(conversationInputForDisplay(text)?.title).toBe("下一步");
    const shown = conversationInputDisplay(text);
    expect(shown).toContain("选路线（路线 A / 路线 B）");
    expect(shown).toContain("请直接输入");
    expect(shown).not.toMatch(/"questions"|"version"|```/);
    const hidden = conversationInputDisplay(text, false, true);
    expect(hidden).toContain("请直接输入");
    expect(hidden).not.toMatch(/"questions"|"version"|```/);
    expect(conversationInputDisplay(text, true, true)).toBe("请确认。\n另外：");
  });

  it("drops a trailing broken block while streaming and keeps ordinary code literal", () => {
    const ordinary = '```python\nprint("questions")\n```';
    expect(conversationInputDisplay("说明。\n" + strict + "\n" + ordinary, true, true)).toBe("说明。\n" + ordinary);
    expect(conversationInputDisplay(ordinary, false, true)).toBe(ordinary);
    expect(conversationInputDisplay('```json\n{"version":1,"items":[1]}\n```', false, true)).toBe('```json\n{"version":1,"items":[1]}\n```');
  });
});

describe("a recovered form keeps sensitive fields protected", () => {
  it("preserves the sensitive flag and the password type through recovery", () => {
    const text = '```json\n' + JSON.stringify({version:1,title:"配置",questions:[
      {key:"tok",label:"API Token",type:"text",sensitive:true},
      {id:"pwd",label:"访问密码",type:"secret"},
      {id:"hint",label:"备注",type:"textarea"},
    ]}) + '\n```';
    expect(parseConversationInput(text)).toBeNull();
    const input = conversationInputForDisplay(text)!;
    expect(input.questions.map(q => [q.id, q.type, q.sensitive])).toEqual([["tok","text",true],["pwd","password",undefined],["hint","text",undefined]]);
    expect(isSensitiveQuestion(input.questions[0])).toBe(true);
    expect(isSensitiveQuestion(input.questions[1])).toBe(true);
    expect(isSensitiveQuestion(input.questions[2])).toBe(false);
    expect(formatConversationAnswers(input, {tok:{selected:[],text:"s3cret"},pwd:{selected:[],text:"hunter2"},hint:{selected:[],text:"普通"}}))
      .toContain("API Token（敏感信息）");
  });

  it("treats a text question whose label names a secret as sensitive after recovery", () => {
    const text = '```json\n' + JSON.stringify({version:1,title:"凭据",questions:[{key:"db",label:"数据库密码",type:"input"}]}) + '\n```';
    const question = conversationInputForDisplay(text)!.questions[0];
    expect(question.sensitive).toBeUndefined();
    expect(isSensitiveQuestion(question)).toBe(true);
  });
});
