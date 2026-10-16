/** Compiled-UI regression for Entropy discovery from a zero-record workspace.
 * Only a temporary copy of the supplied snapshot receives the setup-only
 * experiment. This test creates no runs, evaluations, observations or scores.
 */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),options={};
for(let i=2;i<process.argv.length;i+=2)options[process.argv[i].replace(/^--/,'')]=process.argv[i+1];
if(!options['data-dir'])throw Error('--data-dir must name the supplied saved workspace.');
const source=path.resolve(options['data-dir']),out=path.resolve(options.out||path.join(root,'docs/v097'));
await fs.mkdir(path.join(out,'screenshots'),{recursive:true});
const report={version:'0.9.7',passed:false,transport:'Compiled frontend with real LabServer HTTP through virtual-origin transport',checks:[],javascript_errors:[],http_requests:[],screenshots:[],limitations:[
 'Original snapshot files are only copied and hashed; the server opens a temporary copy.',
 'The new Entropy experiment is a setup-only QA fixture in that temporary copy.',
 'No model, logits, teacher, search service, optimizer or training is executed.',
]};
const check=(name,passed)=>{report.checks.push({name,passed:!!passed});console.log((passed?'PASS ':'FAIL ')+name);if(!passed)throw Error(name);};
async function hashInput(){
 const hashes={};
 async function walk(directory,prefix){let entries;try{entries=await fs.readdir(directory,{withFileTypes:true});}catch(error){if(error.code==='ENOENT')return;throw error;}
  for(const entry of entries.sort((a,b)=>a.name.localeCompare(b.name))){const absolute=path.join(directory,entry.name),key=path.posix.join(prefix,entry.name);if(entry.isDirectory())await walk(absolute,key);else if(entry.isFile())hashes[key]=crypto.createHash('sha256').update(await fs.readFile(absolute)).digest('hex');}}
 await walk(source,'data');await walk(path.join(path.dirname(source),'imports'),'imports');return hashes;
}
const originalHashes=await hashInput(),temp=await fs.mkdtemp(path.join(os.tmpdir(),'searchagent-entropy-discovery-v097-')),data=path.join(temp,'snapshot-copy');
await fs.cp(source,data,{recursive:true});
const serverCode=`import json,sys\nfrom pathlib import Path\nfrom searchagent_lab.store import Store\nfrom searchagent_lab.server import LabServer\nwith Store(Path(sys.argv[1])) as store,LabServer(('127.0.0.1',0),store) as server:\n print('QA_READY='+json.dumps({'url':'http://127.0.0.1:'+str(server.server_address[1])}),flush=True)\n server.serve_forever()\n`;
const child=spawn(process.env.SEARCHAGENT_PYTHON||process.env.CODEX_PRIMARY_RUNTIME_PYTHON||'python',['-u','-c',serverCode,data],{cwd:root,stdio:['ignore','pipe','pipe']});
let browser,page,stderr='';child.stderr.on('data',chunk=>stderr+=String(chunk));
try{
 const server=await new Promise((resolve,reject)=>{let buffer='';const timer=setTimeout(()=>reject(Error('Server startup timeout: '+stderr)),30000);child.stdout.on('data',chunk=>{buffer+=String(chunk);const line=buffer.split('\n').find(line=>line.startsWith('QA_READY='));if(line){clearTimeout(timer);resolve(JSON.parse(line.slice(9)));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Server exited '+code+': '+stderr));});});
 const library=process.env.SEARCHAGENT_PLAYWRIGHT_PATH||(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES?path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'playwright/index.mjs'):null);
 const {chromium}=await import(library?pathToFileURL(library).href:'playwright');
 browser=await chromium.launch({headless:true,executablePath:process.env.SEARCHAGENT_BROWSER_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1440,height:1060}});page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',error=>report.javascript_errors.push(String(error)));
 const origin='http://searchagent-entropy-discovery.test';
 await page.route(origin+'/**',async route=>{const request=route.request(),url=new URL(request.url()),headers={...request.headers()};delete headers.host;delete headers['accept-encoding'];headers.origin=server.url;
  try{const response=await fetch(server.url+url.pathname+url.search,{method:request.method(),headers,body:['GET','HEAD'].includes(request.method())?undefined:request.postDataBuffer()});report.http_requests.push({method:request.method(),path:url.pathname,query:url.search,status:response.status});const responseHeaders=Object.fromEntries(response.headers);delete responseHeaders['content-length'];delete responseHeaders['content-encoding'];await route.fulfill({status:response.status,headers:responseHeaders,body:Buffer.from(await response.arrayBuffer())});}
  catch(error){if(!page.isClosed()){try{await route.abort();}catch{}report.transport_errors??=[];report.transport_errors.push(String(error));}}
 });
 async function httpJson(endpoint){const response=await fetch(server.url+endpoint);if(!response.ok)throw Error('HTTP '+response.status+' '+endpoint);return response.json();}
 async function ready(){await page.waitForFunction(()=>document.querySelector('#s-app')?.getAttribute('aria-busy')==='false');}
 async function go(hash,selector){await page.goto(origin+'/'+hash,{waitUntil:'domcontentloaded'});await ready();if(selector)await page.locator(selector).first().waitFor();}
 async function shot(name,fixture=false){await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(out,'screenshots',name+'.png'),fullPage:true,animations:'disabled'});report.screenshots.push({file:'screenshots/'+name+'.png',temporary_setup_fixture:fixture});}
 const workspaceBefore=await httpJson('/api/studio/workspace'),methodsBefore=await httpJson('/api/methods/catalog?limit=1');report.original_counts=workspaceBefore.counts;
 check('input snapshot has no Entropy-classified experiments',workspaceBefore.families.find(f=>f.id==='entropy')?.count===0);
 await go('#/experiments','#s-nav');
 const entropyNav=page.locator('#s-nav a[href="#/experiments?family=entropy"]');
 check('default navigation visibly includes Entropy before its first experiment',await entropyNav.isVisible());
 check('Entropy navigation reports zero experiments honestly',(await entropyNav.locator('.w-nav-count').innerText()).trim()==='0');
 check('unrecorded unrelated directions remain hidden',await page.locator('#s-nav a[href="#/experiments?family=latent"]').count()===0);
 check('workspace still shows the supplied experiments only',(await httpJson('/api/studio/workspace')).experiments.length===workspaceBefore.experiments.length);
 await shot('01_entropy_entry_on_saved_workspace');
 await entropyNav.click();await ready();await page.locator('[data-entropy-discovery="direction"]').waitFor();
 const guide=page.locator('[data-entropy-discovery="direction"]');
 check('Entropy direction has an explicit pending state',(await guide.innerText()).includes('尚未接入实验'));
 check('direction explains separate Agent execution and RL training views',(await guide.innerText()).includes('Agent 执行评测')&&(await guide.innerText()).includes('RL 训练评测'));
 check('empty Entropy direction offers the existing real creation workflow',await guide.getByRole('button',{name:'＋ 新建 Entropy 实验',exact:true}).count()===1);
 check('empty direction does not invent measurements or duplicate unused filters',await page.locator('[data-entropy-record],.et-counts,.w-toolbar').count()===0);
 check('Entropy introduction appears before the common baseline',await page.evaluate(()=>Boolean(document.querySelector('.en-guide').compareDocumentPosition(document.querySelector('.w-baseline'))&Node.DOCUMENT_POSITION_FOLLOWING)));
 await shot('02_entropy_empty_direction');
 await page.setViewportSize({width:390,height:844});
 check('empty Entropy direction fits 390px viewport',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await shot('03_entropy_empty_mobile');
 await guide.getByRole('button',{name:'查看 Entropy 接入说明',exact:true}).click();await page.locator('#s-modal[open] .en-integration').waitFor();
 check('SDK and synthetic demo instructions start folded',await page.locator('.en-integration details[open]').count()===0);
 await page.locator('.en-integration summary').filter({hasText:'SDK 最小接入'}).click();
 check('SDK guide uses the actual recorder API',(await page.locator('.en-integration pre').first().innerText()).includes('MethodEvidenceRecorder')&&(await page.locator('.en-integration pre').first().innerText()).includes('log.entropy_observation'));
 check('expanded integration instructions fit 390px viewport',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.locator('.en-integration summary').filter({hasText:'查看独立合成示例'}).click();
 check('demo instructions require a separate new directory and identify synthetic results',(await page.locator('.en-integration').innerText()).includes('尚不存在的新目录')&&(await page.locator('.en-integration').innerText()).includes('不代表真实训练结果'));
 check('reading integration instructions does not create a run or evidence',JSON.stringify((await httpJson('/api/studio/workspace')).counts)===JSON.stringify(workspaceBefore.counts)&&(await httpJson('/api/methods/catalog?limit=1')).total===methodsBefore.total);
 await page.locator('#s-modal-close').click();await page.setViewportSize({width:1440,height:1060});
 await guide.getByRole('button',{name:'＋ 新建 Entropy 实验',exact:true}).click();await page.locator('#s-modal[open] form').waitFor();
 check('new experiment form preselects Entropy',await page.getByLabel('研究方向',{exact:true}).inputValue()==='entropy');
 const qaName='[SYNTHETIC QA] Entropy setup only';await page.getByLabel('实验名称',{exact:true}).fill(qaName);await page.getByLabel('研究问题',{exact:true}).fill('Temporary setup-only navigation check; no measured result.');
 await page.getByRole('button',{name:'创建实验',exact:true}).click();await page.getByRole('link',{name:qaName,exact:true}).waitFor();await ready();
 const workspaceAfter=await httpJson('/api/studio/workspace'),created=workspaceAfter.experiments.find(e=>e.experiment.name===qaName);report.temporary_experiment_id=created?.experiment.id;
 check('existing creation API persists the selected Entropy presentation',created?.family==='entropy'&&created?.presentation?.source==='explicit');
 check('new setup does not invent a run or measured aggregate',created?.runs.length===0&&created?.aggregate.kind==='missing'&&workspaceAfter.counts.runs===workspaceBefore.counts.runs&&workspaceAfter.counts.trajectories===workspaceBefore.counts.trajectories);
 const overview=await httpJson('/api/methods/experiment-overview?experiment_id='+encodeURIComponent(created.experiment.id));
 check('presentation does not manufacture a method profile or entropy evidence',overview.families.length===0&&overview.profiles.length===0&&Object.values(overview.counts).every(count=>count===0));
 await page.getByRole('link',{name:qaName,exact:true}).click();await ready();await page.locator('[data-entropy-discovery="execution"]').waitFor();
 check('empty Entropy experiment keeps only the two established primary tabs',(await page.locator('.ex-tab').allTextContents()).join('|')==='Agent 执行评测|RL 训练评测');
 check('Agent view explains where its execution records will appear',(await page.locator('[data-entropy-discovery="execution"]').innerText()).includes('执行中的熵记录'));
 await page.locator('.ex-tab[data-view="training"]').click();await page.locator('[data-entropy-discovery="training"]').waitFor();
 check('RL view has its own training-specific pending state',(await page.locator('[data-entropy-discovery="training"]').innerText()).includes('checkpoint 对照'));
 check('pending experiment does not show zero-valued measurement cards',await page.locator('.et-counts,[data-entropy-record]').count()===0);
 await shot('04_entropy_setup_training',true);
 const existing=workspaceBefore.experiments.find(e=>e.family==='skill'&&e.runs.length);
 if(!existing)throw Error('Expected a saved Skill experiment for unrelated-view regression.');
 await go('#/experiment?run='+encodeURIComponent(existing.runs[0].id)+'&view=results','.ex-tab');
 check('unrelated real experiment does not acquire an Entropy pending pane or tab',await page.locator('[data-entropy-discovery],.ex-tab[data-view="entropy"]').count()===0);
 check('Entropy stays discoverable from an unrelated experiment',await page.locator('#s-nav a[href="#/experiments?family=entropy"]').isVisible());
 check('discovery flow has no JavaScript errors',report.javascript_errors.length===0);
 report.tested_compiled_entries=[...new Set(report.http_requests.filter(request=>/^\/assets\/main-.*\.js$/.test(request.path)).map(request=>request.path))];
 check('regression used the compiled application',report.tested_compiled_entries.length===1);
 const manifest=await fs.readFile(path.join(root,'searchagent_lab/static/assets/build-manifest.json'));report.build_manifest_sha256=crypto.createHash('sha256').update(manifest).digest('hex');report.build=JSON.parse(manifest);
 report.passed=true;
}catch(error){report.failure={message:String(error),stack:error.stack};if(page&&!page.isClosed()){await page.screenshot({path:path.join(out,'screenshots','entropy-discovery-failure.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(out,'entropy-discovery-failure.txt'),String(error)+'\n'+await page.locator('body').innerText().catch(()=>''));}console.error(error.stack);}
finally{
 await browser?.close();if(child.exitCode==null){child.kill('SIGTERM');await new Promise(resolve=>{child.once('exit',resolve);setTimeout(resolve,3000).unref();});if(child.exitCode==null)child.kill('SIGKILL');}
 const after=await hashInput();report.source_data_unchanged=JSON.stringify(after)===JSON.stringify(originalHashes);report.source_hashes=originalHashes;if(!report.source_data_unchanged)report.passed=false;
 report.check_count=report.checks.length;await fs.writeFile(path.join(out,'entropy-discovery-acceptance.json'),JSON.stringify(report,null,2)+'\n');await fs.rm(temp,{recursive:true,force:true});
 console.log(JSON.stringify({passed:report.passed,checks:report.check_count,source_data_unchanged:report.source_data_unchanged,report:path.join(out,'entropy-discovery-acceptance.json')}));if(!report.passed)process.exitCode=1;
}
