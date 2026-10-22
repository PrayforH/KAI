const {chromium}=require('/Users/xiaokai/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),assert=require('node:assert/strict');
const [base,stage,authPath]=process.argv.slice(2);
(async()=>{
 const auth=JSON.parse(fs.readFileSync(authPath,'utf8'));
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--no-proxy-server']});
 const ctx=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:1080}});
 await ctx.addCookies(['access','refresh'].map(kind=>({name:`harness_${kind}_token`,value:auth[`${kind}_token`],domain:new URL(base).hostname,path:'/',httpOnly:true,sameSite:'Lax',secure:base.startsWith('https')})));
 const page=await ctx.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));let draft,phase='create';
 const receipt={base,stage,realPublish:true};
 try {
  const res=await ctx.request.post(`${base}/api/studio/drafts`,{data:{name:`publish-state-check-${Date.now().toString(36)}`,domain:'general',displayName:'发布状态回归验证',description:'临时验证发布与草稿状态，完成后删除。',template:'analyst'}});
  assert.equal(res.status(),201);draft=await res.json();
  const path=`${base}/api/studio/drafts/${draft.draftId}`;
  const catalog=await (await ctx.request.get(`${base}/api/studio/capabilities`)).json();
  if(!catalog.modelRoutes.some(r=>r.enabled&&r.routeId===draft.spec.model.routeId)){
   const route=catalog.modelRoutes.find(r=>r.enabled&&(!r.modelType||r.modelType==='chat')&&r.apiFormat!=='openai_images'&&r.models.length&&draft.spec.model.requiredCapabilities.every(c=>r.capabilities.includes(c)));
   assert(route,'no eligible enabled QA model route');
   const changed=await ctx.request.put(path,{data:{expectedRevision:draft.revision,spec:{...draft.spec,model:{...draft.spec.model,routeId:route.routeId,model:route.models[0]}}}});assert.equal(changed.status(),200);draft=await changed.json();
  }
  const header=page.locator('header').filter({has:page.locator('button[aria-label^="版本历史"]')});
  async function loaded(){await header.getByRole('status').filter({hasText:/已保存 r\d+/}).waitFor({timeout:30000});}
  async function state(published){await page.waitForFunction(({published})=>{const h=document.querySelector('button[aria-label^="版本历史"]')?.closest('header');return h&&(published?/已发布/.test(h.innerText):/草稿/.test(h.innerText));},{published},{timeout:10000});}
  async function reload(){await page.reload({waitUntil:'domcontentloaded'});await loaded();}
  async function publish(){const response=page.waitForResponse(r=>r.url().endsWith(`/drafts/${draft.draftId}/publish`)&&r.request().method()==='POST',{timeout:30000}).catch(()=>null);await header.getByRole('button',{name:'发布',exact:true}).click();const r=await response;assert(r,`publish missing: ${(await page.locator('body').innerText()).slice(-1200)}`);assert.equal(r.status(),200,`publish status ${r.status()}`);await state(true);await loaded();}
  await page.goto(`${base}/studio/agents/${draft.spec.name}`,{waitUntil:'domcontentloaded'});await loaded();await state(false);
  phase='first publish';await publish();receipt.publishedImmediately=true;
  phase='reload published';await reload();await state(true);receipt.publishedAfterReload=true;
  const first=await (await ctx.request.get(path)).json();
  phase='repeat publish';await publish();const repeated=await (await ctx.request.get(path)).json();assert.equal(repeated.revision,first.revision);assert.equal(repeated.spec.version,first.spec.version);receipt.repeatPublishIdempotent=true;
  phase='no-op model selection';await page.getByRole('button',{name:'模型与基本信息',exact:false}).click();
  const dialog=page.getByRole('dialog').filter({has:page.getByRole('button',{name:'保存配置',exact:true})});
  const model=dialog.locator('select').first();await model.selectOption(await model.inputValue());
  await state(true);assert(await dialog.getByRole('button',{name:'保存配置',exact:true}).isDisabled());receipt.sameModelKeepsPublished=true;
  phase='unsaved edit';const name=dialog.locator('input').first();await name.fill('发布状态回归验证 · 修订');await state(false);assert((await header.innerText()).includes('未保存'));receipt.unsavedEditIsDraft=true;
  phase='save edit';await dialog.getByRole('button',{name:'保存配置',exact:true}).click();await loaded();await state(false);await page.getByRole('button',{name:'收起完整配置',exact:true}).click();
  const edited=await (await ctx.request.get(path)).json();assert.notEqual(edited.spec.version,edited.publishedVersion);assert.equal(edited.publishedVersion,first.spec.version);receipt.savedEditIsNewDraft=true;
  phase='reload edited';await reload();await state(false);receipt.savedDraftAfterReload=true;
  phase='republish';await publish();await reload();await state(true);receipt.republishedAfterReload=true;receipt.pageErrors=errors;assert.deepEqual(errors,[]);
  await header.screenshot({path:`/tmp/publish-state-${stage}.png`});console.log(JSON.stringify(receipt));
 }catch(e){console.error(JSON.stringify({stage,phase,error:String(e).split('Call log:')[0].trim(),url:page.url(),pageErrors:errors}));await page.screenshot({path:`/tmp/publish-state-${stage}-error.png`}).catch(()=>{});process.exitCode=1;}
 finally{if(draft){const path=`${base}/api/studio/drafts/${draft.draftId}`;const latest=await (await ctx.request.get(path)).json();const deleted=await ctx.request.delete(`${path}?expectedRevision=${latest.revision}`);console.log(JSON.stringify({stage,qaDraftDeleted:deleted.status()===204}));}await browser.close();}
})().catch(e=>{console.error(String(e).split('Call log:')[0].trim());process.exitCode=1;});
