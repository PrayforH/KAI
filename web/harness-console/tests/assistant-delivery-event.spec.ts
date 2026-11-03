// @vitest-environment jsdom
import {expect, it} from "vitest";
import {HarnessHttpAgent} from "../src/lib/harness-agent";
import {handoverStore, mentionsForThread} from "../src/lib/handover-store";

it("tracks a server-routed child from the actual SSE delivery event", async () => {
  const delivery = {handoverId:"auto-delivery-1",specialistId:"weather",specialistName:"天气提醒",
    sourceThreadId:"main-auto",targetThreadId:"original-weather",runId:"actual-child",
    createdAt:"2026-11-03T06:00:00Z"};
  const agent = new HarnessHttpAgent({url:"http://harness/v1/agui", fetch: async () => new Response([
    'data: {"type":"RUN_STARTED","threadId":"main-auto","runId":"parent-auto"}', "",
    'data: '+JSON.stringify({type:"CUSTOM",name:"ui.assistant_delivery",value:delivery}), "",
    'data: '+JSON.stringify({type:"CUSTOM",name:"ui.assistant_delivery",value:delivery}), "",
    'data: {"type":"RUN_FINISHED","threadId":"main-auto","runId":"parent-auto"}', "", "",
  ].join("\n"), {headers:{"Content-Type":"text/event-stream"}})});
  await agent.runAgent({runId:"parent-auto"});
  const owned = mentionsForThread(handoverStore.snapshot(), "main-auto");
  expect(owned).toHaveLength(1);
  expect(owned[0]).toMatchObject({runId:"actual-child", targetThreadId:"original-weather", mention:true});
  expect(mentionsForThread(handoverStore.snapshot(), "rest-bot")).toHaveLength(0);
  handoverStore.ingestDelivery({...delivery, handoverId:"invalid-delivery",runId:null});
  expect(mentionsForThread(handoverStore.snapshot(), "main-auto")).toHaveLength(1);
});
