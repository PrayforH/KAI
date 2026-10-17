import { expect,it } from "vitest";
import { collectConversationSecrets, redactConversationText, SECRET_MASK } from "../src/lib/conversation-secrets";
import { formatConversationAnswers, isSensitiveQuestion, parseConversationInput } from "../src/lib/conversation-input";
const password='Example-Pass-42!';
it('masks labelled secrets, credential URLs, and historical combined DB credentials',()=>{
 for(const text of [`密码\n${password}`,`password: ${password}`,JSON.stringify({password}),`postgresql://reader:${password}@db:5432/app`,`数据库用户名（只读账号）\nreader/${password}`,`选择方向\n172.20.1.2:5236 达梦数据库 FAO_RO/${password} 现在在业务库`]){
  const visible=redactConversationText(text);expect(visible).not.toContain(password);expect(visible).toContain(SECRET_MASK);
 }
 expect(redactConversationText('密码\n已跳过\n\n主机:端口\ndb:5432')).toContain('已跳过');
});
it('keeps ordinary paths, questions and prose readable',()=>{
 for(const text of ['数据库报表在 ontology/FAO_SENTIMENT 和 /tmp/report/result.md','是否配置密码？','请配置连接之后继续。','密码修改流程说明。']) expect(redactConversationText(text)).toBe(text);
});
it('masks known secret values in unlabelled model echoes without changing the authored input',()=>{
 const original=`账号\nreader/${password}\n\n密码\n已跳过`;
 const secrets=collectConversationSecrets([original]);expect(secrets).toContain(password);
 expect(redactConversationText(`已收到 ${password}，继续连接`,secrets)).toBe(`已收到 ${SECRET_MASK}，继续连接`);
 expect(original).toContain(password);
});
it('supports inferred and explicit password questions while retaining the exact runtime credential',()=>{
 for(const question of [{id:'pwd',label:'数据库密码',type:'text'}, {id:'credential',label:'连接信息',type:'password'}, {id:'credential',label:'连接信息',type:'text',sensitive:true}]){
  const input=parseConversationInput('```harness-input\n'+JSON.stringify({version:1,title:'补充连接',questions:[question]})+'\n```')!;
  expect(isSensitiveQuestion(input.questions[0])).toBe(true);
  const answer=formatConversationAnswers(input,{[question.id]:{selected:[],text:'  '+password+'  '}})!;
  expect(answer).toContain('  '+password+'  ');expect(redactConversationText(answer)).not.toContain(password);
 }
 expect(isSensitiveQuestion({id:'has_password',label:'是否设置了密码',type:'single',options:['是','否']})).toBe(false);
});
it('recognizes a bare password reply only when the previous form requested one',async()=>{
 const {conversationSecretInputs}=await import('../src/lib/conversation-secret-inputs');
 const form='```harness-input\n'+JSON.stringify({version:1,title:'补充连接',questions:[{id:'pwd',label:'密码',type:'text'}]})+'\n```';
 const messages=[{role:'assistant',content:[{type:'text',text:form}]},{role:'user',content:[{type:'text',text:password}]}];
 const secrets=collectConversationSecrets(conversationSecretInputs(messages));
 expect(redactConversationText(password,secrets)).toBe(SECRET_MASK);
 expect(collectConversationSecrets(conversationSecretInputs([{role:'user',content:[{type:'text',text:password}]}]))).toEqual([]);
});
it('keeps complex and multiline secret prompts redacted after round-trip formatting',()=>{
 for(const label of ['请填写数据库密码，用于本次连接','数据库密码\n请提供只读账号的连接密码']){
  const input={version:1 as const,title:'连接',questions:[{id:'pwd',label,type:'text' as const,options:[]}]};
  const answer=formatConversationAnswers(input,{pwd:{selected:[],text:password}})!;
  expect(answer).toContain(password);expect(redactConversationText(answer)).not.toContain(password);
 }
});
