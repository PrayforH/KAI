import { expect, it } from "vitest";
import { credentialRequest } from "../src/lib/credential-request";
import { isSensitiveQuestion, parseConversationInput } from "../src/lib/conversation-input";

it("uses explicit credential type while recovering older login requests without business names", () => {
  const input = parseConversationInput('```harness-input\n'+JSON.stringify({version:1,title:"登录",questions:[{id:"opaque",label:"网站访问",type:"text",credentialType:"login"}]})+'\n```')!;
  expect(isSensitiveQuestion(input.questions[0])).toBe(true);
  expect(credentialRequest("opaque",input.questions[0])).toEqual({name:"opaque",label:"登录信息",category:"login"});
  expect(credentialRequest("login_detail").category).toBe("login");
  expect(credentialRequest("api_key").category).toBe("secret");
  expect(credentialRequest("site_password",{id:"site_password",label:"网站密码",type:"password",options:[]})).toMatchObject({category:"secret",label:"网站密码"});
});
