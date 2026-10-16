/** Native browser acceptance against a scratch copy of saved data, with real HTTP.
 * Usage: node tests/browser_v091.mjs --data-dir /path/to/data --out /path/to/report
 * Install Playwright for development; optional QA_CHROMIUM_PATH selects a local browser.
 * This script never creates model results or modifies the supplied data directory.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
let playwright;
try{playwright=require('playwright');}catch{if(!process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES)throw Error('Install playwright as a development tool.');playwright=require(path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'playwright'));}
const options={};for(let i=2;i<process.argv.length;i+=2)options[process.argv[i].replace(/^--/,'')]=process.argv[i+1];
if(!options['data-dir'])throw Error('--data-dir must name existing saved results; no synthetic scores are generated.');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(options.out||path.join(root,'docs','v091','browser'));fs.mkdirSync(out,{recursive:true});
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'searchagent-browser-'));
const data=path.join(tmp,'data');fs.cpSync(path.resolve(options['data-dir']),data,{recursive:true});
const checks=[],pageErrors=[],consoleErrors=[],requests=[],failedRequests=[];let browser,server;
const report={transport:'Native Chromium with real HTTP LabServer; no route mocks or fetch bridge',data:'Copy of supplied saved workspace; original data preserved',checks,page_errors:pageErrors,console_errors:consoleErrors,requests,request_failures:failedRequests,views:[]};
function check(name,condition,detail=''){checks.push({name,passed:!!condition,detail});console.log((condition?'PASS ':'FAIL ')+name+(detail?' — '+detail:''));}
const childCode=`import sys\nfrom searchagent_lab.store import Store\nfrom searchagent_lab.server import LabServer\nwith Store(sys.argv[1]) as store,LabServer(('127.0.0.1',0),store) as server:\n print('QA_SERVER_URL=http://127.0.0.1:'+str(server.server_address[1]),flush=True)\n server.serve_forever()\n`;
try{
 server=spawn(process.env.QA_PYTHON||'python',['-u','-c',childCode,data],{cwd:root,stdio:['ignore','pipe','pipe']});let serverLog='';server.stderr.on('data',b=>{serverLog+=String(b);});
 const base=await new Promise((resolve,reject)=>{let text='';server.stdout.on('data',b=>{text+=String(b);const m=text.match(/QA_SERVER_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(m)resolve(m[1]);});server.once('exit',c=>reject(Error('LabServer exited '+c+' '+serverLog)));setTimeout(()=>reject(Error('LabServer startup timeout '+serverLog)),10000).unref();});
 browser=await playwright.chromium.launch({headless:true,...(process.env.QA_CHROMIUM_PATH?{executablePath:process.env.QA_CHROMIUM_PATH}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--no-zygote','--disable-gpu','--font-render-hinting=none']});report.browser=browser.version();
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.setDefaultTimeout(10000);
 page.on('pageerror',e=>pageErrors.push(e.message));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
 page.on('requestfailed',r=>failedRequests.push({url:r.url().replace(base,''),error:r.failure()?.errorText}));
 page.on('requestfinished',async r=>{const res=await r.response();requests.push({url:r.url().replace(base,''),type:r.resourceType(),status:res.status(),body_bytes:Number(res.headers()['content-length']||0),content_encoding:res.headers()['content-encoding']||'',timing:r.timing()});});
 await page.addInitScript(()=>{window.qaRouteReady=[];const observer=new MutationObserver(records=>{if(records.some(x=>x.type==='attributes'&&x.attributeName==='aria-busy'&&x.target.id==='s-app'&&x.target.getAttribute('aria-busy')==='false'))window.qaRouteReady.push({hash:location.hash,ready_ms:performance.now()});});observer.observe(document,{subtree:true,attributes:true,attributeFilter:['aria-busy']});});
 async function ready(){await page.waitForFunction(()=>document.querySelector('#s-app')?.getAttribute('aria-busy')==='false');}
 async function go(hash,selector='h1'){const before=await page.evaluate(()=>({hash:location.hash,marks:window.qaRouteReady.length}));if(before.hash!==hash){await page.evaluate(h=>{location.hash=h;},hash);await page.waitForFunction(n=>window.qaRouteReady.length>n,before.marks);}await ready();await page.locator(selector).first().waitFor();}
 async function clickRoute(locator){const before=await page.evaluate(()=>window.qaRouteReady.length);await locator.click();await page.waitForFunction(n=>window.qaRouteReady.length>n,before);await ready();}
 async function shot(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});}
 async function collect(name){const text=await page.locator('#s-app').innerText();report.views.push({name,hash:await page.evaluate(()=>location.hash),text,dom_nodes:await page.locator('*').count(),overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)});check(name+' has no visible runtime exception',!/(Cannot read|is not a function|ReferenceError|TypeError|读取失败：|Internal error)/i.test(text));}
 await page.goto(base+'/#/experiments',{waitUntil:'domcontentloaded'});await ready();await page.waitForLoadState('networkidle');
 const firstRequests=requests.slice();report.first_screen={ready_marks:await page.evaluate(()=>window.qaRouteReady),http_requests:firstRequests.length,api_requests:firstRequests.filter(x=>x.url.startsWith('/api/')).length,body_bytes:firstRequests.reduce((s,x)=>s+x.body_bytes,0),urls:firstRequests.map(x=>x.url)};
 check('homepage shows shared native Qwen baseline',await page.locator('.w-baseline').count()===1&&await page.locator('.w-baseline h2').innerText()==='原生 Qwen · 七集问答基线');
 check('baseline precedes research groups',await page.evaluate(()=>{const a=document.querySelector('.w-baseline'),b=document.querySelector('.w-groups');return !!a&&!!b&&!!(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING);}));
 check('homepage keeps source groups visible',await page.locator('[data-experiment]').count()>=3);await collect('homepage');await shot('01_home');
 const baselineLink=await page.locator('.w-baseline .w-exp-name').first().getAttribute('href');const baselineRun=new URLSearchParams(baselineLink.split('?')[1]).get('run');
 check('global navigation avoids an empty unscoped comparison entry',await page.locator('#s-nav a[href^="#/reports"]').count()===0);
 await page.getByRole('button',{name:'＋ 新建实验',exact:true}).click();check('new experiment form opens with research direction and origin',await page.locator('#s-modal').isVisible()&&await page.locator('#s-modal').getByLabel('研究方向',{exact:true}).count()===1&&await page.locator('#s-modal').getByLabel('实验来源',{exact:true}).count()===1);await page.locator('#s-modal').getByLabel('实验来源',{exact:true}).selectOption('reproduction');check('reproduction selection exposes method source fields',await page.locator('#s-modal').getByLabel('复现方法',{exact:true}).isVisible());await page.keyboard.press('Escape');check('new experiment form can be cancelled',!await page.locator('#s-modal').isVisible());
 await clickRoute(page.locator('#s-nav a').filter({hasText:'Self-distillation'}));await page.locator('[data-family="self_distillation"]').first().waitFor();
 const distillNames=await page.locator('[data-family="self_distillation"] .w-exp-name').allTextContents();check('SDAR and UniOPSD are grouped under self-distillation',distillNames.some(n=>/SDAR/i.test(n))&&distillNames.some(n=>/UniOPSD/i.test(n)),distillNames.join(' / '));
 check('SDAR and UniOPSD are marked as reproductions',await page.locator('[data-family="self_distillation"][data-origin="reproduction"]').count()>=2);
 await page.locator('.w-groups .w-organize').first().click();check('classification form shows the existing research direction',await page.locator('#s-modal').isVisible()&&await page.locator('#s-modal').getByLabel('研究方向',{exact:true}).inputValue()==='self_distillation');check('classification form includes explicit shared-baseline setting',await page.locator('#s-modal').getByLabel('设为共同原生基线',{exact:true}).count()===1);await page.keyboard.press('Escape');
 await page.locator('[data-origin="main"]').first().click();check('my-experiments filter excludes literature records',await page.locator('.w-groups [data-origin="reproduction"]').count()===0);
 await page.locator('[data-origin="reproduction"]').first().click();check('reproduction filter restores matching experiments',await page.locator('.w-groups [data-origin="reproduction"]').count()>=2);await collect('self_distillation');await shot('02_self_distillation');
 await page.getByLabel('搜索实验',{exact:true}).fill('UniOPSD');await page.waitForTimeout(160);check('search narrows experiments',await page.locator('.w-groups [data-experiment]').count()===1);
 const uniLink=page.locator('.w-groups .w-exp-name').first();const uniHref=await uniLink.getAttribute('href');const startDetail=requests.length;await clickRoute(uniLink);await page.locator('.w-reference-card[aria-busy="false"]').waitFor();
 check('experiment opens on result and baseline tab',await page.locator('[data-view="results"]').getAttribute('aria-selected')==='true');
 check('result entry does not fetch training dynamics',!requests.slice(startDetail).some(r=>r.url.startsWith('/api/studio/dynamics')));
 await collect('uniopsd_results');await shot('03_uniopsd_results');
 if(baselineRun){const hash=await page.evaluate(()=>location.hash);const query=new URLSearchParams(hash.split('?')[1]);query.set('baseline_run',baselineRun);await go('#/experiment?'+query,'.w-reference-card[aria-busy="false"]');}
 if(await page.locator('[data-result-sample]').count()){
  check('saved fixed-panel trajectories are visible',await page.locator('[data-result-sample]').count()>0);
  const filter=page.locator('[data-result-filter="incorrect"]');await filter.click();const count=await page.locator('[data-result-sample]').count();
  if(count){const beforeHash=await page.evaluate(()=>location.hash);const sample=await page.locator('[data-result-sample]').first().getAttribute('data-result-sample');await clickRoute(page.getByRole('link',{name:'查看轨迹 →',exact:true}).first());await page.locator('.s-step-item').first().waitFor();check('selected case opens its saved trace',await page.locator('.s-replay-meta').innerText().then(t=>t.includes(sample)));check('trace does not show a permanent manual-review form',await page.locator('textarea:visible').count()===0);await collect('saved_trace');await shot('04_trace');
   await clickRoute(page.getByRole('link',{name:'返回实验',exact:true}));await page.locator('[data-result-sample]').first().waitFor();check('return from trace preserves result filter',await page.locator('[data-result-filter="incorrect"]').getAttribute('class').then(c=>c.includes('active'))&&await page.locator('[data-result-sample]').count()===count);report.trace_return={before:beforeHash,after:await page.evaluate(()=>location.hash)};if(baselineRun)check('return from trace preserves selected native baseline',new URLSearchParams(report.trace_return.after.split('?')[1]).get('baseline_run')===baselineRun);
  }
 }
 await page.locator('[data-view="training"]').click();await page.locator('#ex-pane').waitFor();await page.waitForTimeout(100);await page.waitForLoadState('networkidle');await collect('uniopsd_training');check('training dynamics are requested only when selected',requests.some(r=>r.url.startsWith('/api/studio/dynamics')));check('saved training curves render',await page.locator('.s-chart').count()>0);await shot('05_training');
 await go('#/experiments?family=self_distillation','.w-groups');await clickRoute(page.locator('.w-groups .w-exp-name').filter({hasText:/SDAR/}).first());await page.locator('.w-reference-card[aria-busy="false"]').waitFor();await collect('sdar_results');await shot('06_sdar_results');
 await go('#/experiments?family=skill','.w-groups');await clickRoute(page.locator('.w-groups .w-exp-name').first());await page.locator('.w-reference-card[aria-busy="false"]').waitFor();await collect('skill_results');check('skill experiment does not invent unrelated method tabs',await page.locator('.ex-tabs [data-view="self_play"],.ex-tabs [data-view="entropy"],.ex-tabs [data-view="latent"]').count()===0);await shot('07_skill_results');
 await go('#/experiments?family=baseline','.w-baseline');await clickRoute(page.locator('.w-baseline .w-exp-name').first());await page.locator('.w-reference-card[aria-busy="false"]').waitFor();await collect('native_baseline');check('native baseline contains seven dataset rows',await page.locator('[data-reference-dataset]').count()===7);await shot('08_native_baseline');
 await page.setViewportSize({width:390,height:844});await go('#/experiments','.w-groups');await collect('narrow_home');check('narrow homepage has no document overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('#s-menu').click();check('narrow navigation opens',await page.locator('body.s-nav-open').count()===1);await page.locator('#s-closemenu').click();check('narrow navigation closes',await page.locator('body.s-nav-open').count()===0);await shot('09_narrow_home');
 await go(uniHref,'.w-reference-card');await collect('narrow_detail');check('narrow experiment has no document overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await shot('10_narrow_detail');
 check('no uncaught JavaScript errors',pageErrors.length===0,pageErrors.join('; '));check('no failed HTTP responses',requests.every(r=>r.status<400),requests.filter(r=>r.status>=400).map(r=>r.status+' '+r.url).join('; '));check('no browser request failures',failedRequests.length===0);
 await context.close();
}catch(e){report.fatal={message:e.message,stack:e.stack};console.error(e.stack);process.exitCode=1;}
finally{await browser?.close();server?.kill('SIGTERM');report.passed=checks.length>0&&checks.every(c=>c.passed)&&!report.fatal;fs.writeFileSync(path.join(out,'browser-acceptance.json'),JSON.stringify(report,null,2));fs.rmSync(tmp,{recursive:true,force:true});if(!report.passed)process.exitCode=1;console.log(JSON.stringify({passed:report.passed,checks:checks.length,report:path.join(out,'browser-acceptance.json')}));}
