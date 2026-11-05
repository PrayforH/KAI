// Real browser and real model: no route mocking or canned agent responses.
const {chromium}=require('/Users/xiaokai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {execFileSync}=require('node:child_process');
const {randomBytes}=require('node:crypto');
const fs=require('node:fs');
const assert=require('node:assert/strict');
(async()=>{
 const base='http://172.20.10.7:3501';
 const login=JSON.parse(execFileSync('ssh',['-S','/tmp/kai-assistant-desktop-retry.sock','xiaokai@172.20.10.7','cat /home/xiaokai/deployments/kai-assistant-20261023/private/login.json'],{encoding:'utf8'}));
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--no-proxy-server']});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 let page; let passed=false;
 try {
  const authenticated=await context.request.post(base+'/api/auth/login',{data:{email:login.email,password:login.password}});
  assert(authenticated.ok(),'Browser login failed');
  console.log("Browser login verified");
  page=await context.newPage();
  await page.goto(base+'/assistant');
  await page.getByLabel('我的助理对话').waitFor({timeout:60000});
  const input=page.getByRole('textbox',{name:'消息输入',exact:true});
  await input.waitFor({timeout:60000});
  const code='KAI_'+randomBytes(6).toString('hex');
  await input.fill(`这是一条部署验收。请记住本次代号 ${code}，只回复“已记录”，无需调用工具。`);
  await page.getByRole('button',{name:'发送消息',exact:true}).click();
  await page.locator('.harness-assistant-message[data-turn-answer*="已记录"]').last().waitFor({timeout:180000});
  await page.getByRole('button',{name:'发送消息',exact:true}).waitFor({timeout:60000});
  console.log("Reloading assistant conversation");
  await page.reload();
  await page.locator('.harness-assistant-message[data-turn-answer*="已记录"]').last().waitFor({timeout:60000});
  await input.fill('刚才的验收代号是什么？只回复代号，无需调用工具。');
  await page.getByRole('button',{name:'发送消息',exact:true}).click();
  await page.locator(`.harness-assistant-message[data-turn-answer*="${code}"]`).last().waitFor({timeout:180000});
  await page.getByRole('button',{name:'发送消息',exact:true}).waitFor({timeout:60000});
  console.log("Reloading assistant conversation");
  await page.reload();
  await page.locator(`.harness-assistant-message[data-turn-answer*="${code}"]`).last().waitFor({timeout:60000});
  fs.mkdirSync('docs/results/assistant-desktop-20261023',{recursive:true});
  await page.screenshot({path:'docs/results/assistant-desktop-20261023/chat-refresh.png',fullPage:true});
  const report={base,assistant:'/assistant',login:true,realModelReply:true,reloadHistory:true,followupContext:true,secondReload:true,verificationCode:code,timestamp:new Date().toISOString()};
  fs.writeFileSync('docs/results/assistant-desktop-20261023/browser.json',JSON.stringify(report,null,2));
  passed=true; console.log(JSON.stringify(report));
 } finally {
  if (!passed && page) {
   fs.mkdirSync('docs/results/assistant-desktop-20261023',{recursive:true});
   await page.screenshot({path:'docs/results/assistant-desktop-20261023/chat-failure.png',fullPage:true}).catch(()=>{});
  }
  await context.close();await browser.close();
 }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
