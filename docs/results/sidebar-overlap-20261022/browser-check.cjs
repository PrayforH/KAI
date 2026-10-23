const { chromium } = require('/Users/xiaokai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const [base, stage] = process.argv.slice(2);
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args:['--no-proxy-server']});
 let phase = 'setup'; let pinCalls = 0; const errors = [], states = []; const now = new Date().toISOString();
 const tasks = [
 ['规划', '行政战略规划政策协同实施路径以及落实方案', 'succeeded'],
 ['复验', '文字回复验收与业务回归测试报告 Codex Layout Review', 'succeeded'],
 ['输入', '请补充企业业务材料以继续执行当前任务', 'waiting_input'],
 ['错误', '研究报告文件解析失败后的恢复与重试任务', 'failed'],
 ['项目', '项目中的超长标题与置顶按钮布局检查 Project Analysis', 'succeeded'],
 ].map(([id,title,status], i) => ({thread_id:`qa-${id}`,session_id:`qa-${id}`,title,status,agent_name:'lead',agent_version:'1',agent_owner_user_id:'qa',run_id:`run-${i}`,created_at:now,updated_at:new Date(Date.now()-3600000*(i+1)).toISOString(),...(i===1?{pinned_at:now}:{}),...(i===4?{project_id:'qa-project'}:{})}));
 const project={projectId:'qa-project',name:'金融办',tenantId:'local',userId:'qa',createdAt:now,updatedAt:now};
 async function setup(page) {
  page.on('pageerror',e => errors.push(e.message));
  await page.route('**/api/**', async route => {
   const req=route.request(), u=new URL(req.url()), p=u.pathname; let data=[];
   if(p==='/api/auth/session')data={user:{user_id:'qa',display_name:'侧栏布局验收'},membership:{role:'admin',tenant_id:'local'},password_enabled:false};
   else if(p==='/api/studio/projects')data=[project];
   else if(p==='/api/agui/threads')data=u.searchParams.get('archived')==='true'?[]:tasks;
   else if(p.startsWith('/api/agui/threads/')&&req.method()==='PATCH'){const body=req.postDataJSON();assert('pinned' in body);data=tasks.find(t=>p.endsWith(encodeURIComponent(t.thread_id)));assert(data);data.pinned_at=body.pinned?now:null;pinCalls++;}
   else if(p.endsWith('/history'))data={thread_id:decodeURIComponent(p.split('/').at(-2)),status:'succeeded',has_more:false,total:0,messages:[]};
   else if(p==='/api/harness/runtime-config')data={name:'lead',version:'1'};
   else if(p==='/api/harness/agents')data=[{name:'lead',version:'1',display_name:'通用助手',owner_user_id:'qa',scope:'personal',can_chat:true,platform_default:true,model_route:'chat'}];
   else if(p==='/api/studio/capabilities')data={mcpServers:[],modelRoutes:[{routeId:'chat',label:'模型',provider:'anthropic',models:['model'],enabled:true,capabilities:['chat']}],skills:[],tools:[]};
   else if(p.includes('/skills'))data={packages:[]};
   else if(p.includes('dictation')&&p.endsWith('capabilities'))data={enabled:true,maxSessionSeconds:120};
   await route.fulfill({json:data});
  });
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.locator('.task-list-row').first().waitFor({timeout:30000});
  const projectToggle=page.getByRole('button',{name:'展开项目 金融办',exact:true});
  if(await projectToggle.count()) await projectToggle.click();
  await page.waitForFunction(()=>document.querySelectorAll('.task-list-row').length===5);
 }
 const ctx=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000}});const page=await ctx.newPage();
 try {
  await setup(page);
  async function metrics(row){return row.evaluate(e=>{const box=s=>{const r=(e.querySelector(s)||(s==='.task-list-content'?e.querySelector('.task-title-viewport'):null)).getBoundingClientRect();return{left:r.left,right:r.right,width:r.width,height:r.height}};return{title:box('.task-title-viewport'),content:box('.task-list-content'),actions:box('.task-row-actions'),time:box('.task-list-meta'),pin:box('.task-list-pin'),more:box('.task-header-more'),clip:getComputedStyle(e.querySelector('.task-title-viewport')).overflow,visible:getComputedStyle(e.querySelector('.task-row-actions')).opacity,timeOpacity:getComputedStyle(e.querySelector('.task-list-meta')).opacity};});}
  for(const mode of ['light','dark'])for(const width of [220,264,380]) {
   await page.evaluate(({mode,width})=>{document.documentElement.dataset.colorMode=mode;document.documentElement.style.colorScheme=mode;document.body.style.setProperty('--preferred-sidebar-width',`${width}px`);}, {mode,width});
   for(const task of tasks){
    const row=page.locator('.task-list-row').filter({has:page.locator(`.task-list-item[aria-label^="${task.title}"]`)});
    await page.mouse.move(900,5);await page.evaluate(()=>document.activeElement?.blur());await page.waitForTimeout(130);
    const before=await metrics(row);assert.equal(before.visible,'0');
    await row.locator('.task-list-item').hover();await page.waitForTimeout(400);const hover=await metrics(row);
    assert.equal(hover.visible,'1');assert.equal(hover.timeOpacity,'0');assert.equal(hover.clip,'hidden');
    assert(hover.content.right<=hover.actions.left-4,JSON.stringify({mode,width,task:task.title,hover}));
    assert(Math.abs(hover.title.width-before.title.width)<.1,'Title width changed on hover');
    assert.equal(hover.more.width,28);assert.equal(hover.more.height,28);
    await row.locator('.task-list-item').focus();await page.mouse.move(900,5);await page.waitForTimeout(150);const focus=await metrics(row);assert.equal(focus.visible,'1');assert(focus.content.right<=focus.actions.left-4);
    states.push({mode,width,status:task.status,gap:hover.actions.left-hover.content.right,titleWidth:hover.title.width});
   }
   if(width===264){await page.screenshot({path:`/tmp/sidebar-overlap-${stage}-${mode}.png`});}
  }
  const first=page.locator('.task-list-row').filter({has:page.locator(`.task-list-item[aria-label^="${tasks[0].title}"]`)});
  phase='pin';await first.hover();await first.locator('.task-list-pin').click();assert.equal(pinCalls,1);
  phase='menu';await first.hover();await first.locator('.task-header-more').click();await page.getByRole('menuitem',{name:'重命名',exact:true}).waitFor();await page.mouse.move(900,5);await page.waitForTimeout(150);assert.equal(await first.locator('.task-header-more').getAttribute('aria-expanded'),'true');assert.equal((await metrics(first)).visible,'1');await page.keyboard.press('Escape');
  phase='select';await first.locator('.task-list-item').click();await page.waitForURL(u=>u.searchParams.get('thread')===tasks[0].thread_id);
  phase='touch';const touch=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1000},hasTouch:true});const tp=await touch.newPage();await setup(tp);
  assert(await tp.evaluate(()=>matchMedia('(hover:none)').matches));for(const row of await tp.locator('.task-list-row').all()){const m=await metrics(row);assert.equal(m.visible,'1');assert.equal(m.timeOpacity,'0');assert(m.content.right<=m.actions.left-4);}
  await touch.close();assert.deepEqual(errors,[]);
  console.log(JSON.stringify({base,stage,states,pinCalls,menuOpenAndKeyboard:true,titleClick:true,touchNoOverlap:true,pageErrors:errors,fixture:'All API responses mocked in browser; no server records changed'}));
 } catch(e) {await page.screenshot({path:`/tmp/sidebar-overlap-${stage}-error.png`}).catch(()=>{});console.error(phase, String(e).split('Call log:')[0]);process.exitCode=1;}finally{await browser.close();}
})().catch(e=>{console.error(String(e).split('Call log:')[0]);process.exitCode=1});
