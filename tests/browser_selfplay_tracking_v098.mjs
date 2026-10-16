/** Compiled UI + real LabServer HTTP over a COPY of selfplay_tracking_example.
 * Every question, score, mask, budget and parameter update is synthetic.
 * This test never executes a solver, proposer, search service or optimizer.
 */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),options={};
for(let i=2;i<process.argv.length;i+=2)options[process.argv[i].replace(/^--/,'')]=process.argv[i+1];
if(!options['data-dir']||!options.out)throw Error('--data-dir must name an existing synthetic Self-play fixture and --out a temporary QA report directory.');
const source=path.resolve(options['data-dir']),out=path.resolve(options.out);await fs.mkdir(path.join(out,'screenshots'),{recursive:true});
const report={version:'0.9.8',passed:false,synthetic:true,transport:'Compiled frontend with real LabServer HTTP through virtual-origin transport',checks:[],javascript_errors:[],http_requests:[],screenshots:[],limitations:['Source fixture is copied and hashed; only the copy is opened by LabServer.','All records are explicitly synthetic; no model, logits, search, teacher or optimizer is executed.','Screenshots are internal QA artifacts.']};
const check=(name,passed)=>{report.checks.push({name,passed:!!passed});console.log((passed?'PASS ':'FAIL ')+name);if(!passed)throw Error(name);};
async function hashInput(){const hashes={};async function walk(dir,prefix=''){for(const item of(await fs.readdir(dir,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){const abs=path.join(dir,item.name),key=path.posix.join(prefix,item.name);if(item.isDirectory())await walk(abs,key);else if(item.isFile())hashes[key]=crypto.createHash('sha256').update(await fs.readFile(abs)).digest('hex');}}await walk(source);return hashes;}
const originalHashes=await hashInput(),temp=await fs.mkdtemp(path.join(os.tmpdir(),'searchagent-selfplay-tracking-v098-')),data=path.join(temp,'fixture-copy');await fs.cp(source,data,{recursive:true});
const serverCode=`import json,sys\nfrom pathlib import Path\nfrom searchagent_lab.store import Store\nfrom searchagent_lab.server import LabServer\nwith Store(Path(sys.argv[1])) as store,LabServer(('127.0.0.1',0),store) as server:\n print('QA_READY='+json.dumps({'url':'http://127.0.0.1:'+str(server.server_address[1])}),flush=True)\n server.serve_forever()\n`;
const child=spawn(process.env.SEARCHAGENT_PYTHON||process.env.CODEX_PRIMARY_RUNTIME_PYTHON||'python',['-u','-c',serverCode,data],{cwd:root,stdio:['ignore','pipe','pipe']});let browser,page,stderr='';child.stderr.on('data',chunk=>stderr+=String(chunk));
try{
 const server=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Server startup timeout: '+stderr)),30000);child.stdout.on('data',chunk=>{text+=String(chunk);const line=text.split('\n').find(line=>line.startsWith('QA_READY='));if(line){clearTimeout(timer);resolve(JSON.parse(line.slice(9)));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Server exited '+code+': '+stderr));});});
 const library=process.env.SEARCHAGENT_PLAYWRIGHT_PATH||(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES?path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'playwright/index.mjs'):null),{chromium}=await import(library?pathToFileURL(library).href:'playwright');
 browser=await chromium.launch({headless:true,executablePath:process.env.SEARCHAGENT_BROWSER_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});const context=await browser.newContext({viewport:{width:1440,height:1060}});page=await context.newPage();page.setDefaultTimeout(14000);page.on('pageerror',error=>report.javascript_errors.push(String(error)));const origin='http://searchagent-selfplay-tracking.test';
 await page.route(origin+'/**',async route=>{const request=route.request(),url=new URL(request.url()),headers={...request.headers()};delete headers.host;delete headers['accept-encoding'];headers.origin=server.url;try{const response=await fetch(server.url+url.pathname+url.search,{method:request.method(),headers,body:['GET','HEAD'].includes(request.method())?undefined:request.postDataBuffer()});report.http_requests.push({method:request.method(),path:url.pathname,query:url.search,status:response.status});const responseHeaders=Object.fromEntries(response.headers);delete responseHeaders['content-length'];delete responseHeaders['content-encoding'];await route.fulfill({status:response.status,headers:responseHeaders,body:Buffer.from(await response.arrayBuffer())});}catch(error){if(!page.isClosed()){try{await route.abort();}catch{}report.transport_errors??=[];report.transport_errors.push(String(error));}}});
 async function httpJson(endpoint){const response=await fetch(server.url+endpoint);if(!response.ok)throw Error('HTTP '+response.status+' '+endpoint);return response.json();}
 async function ready(){await page.waitForFunction(()=>document.querySelector('#s-app')?.getAttribute('aria-busy')==='false');}
 async function go(hash,selector){await page.goto(origin+'/'+hash,{waitUntil:'domcontentloaded'});await ready();if(selector)await page.locator(selector).first().waitFor();}
 async function shot(name,selector='.sp-training'){await page.locator(selector).screenshot({path:path.join(out,'screenshots',name+'.png'),animations:'disabled',style:'.s-top{visibility:hidden!important}'});report.screenshots.push({file:'screenshots/'+name+'.png',synthetic:true});}
 async function openFold(host,title){const fold=host.locator(':scope>.sp-fold').filter({has:page.locator(':scope>summary').filter({hasText:title})});if(!await fold.evaluate(el=>el.open))await fold.locator(':scope>summary').click();return fold;}
 async function openQuestion(id){const outer=page.locator('[data-selfplay-questions]');if(!await outer.evaluate(el=>el.open))await outer.locator(':scope>summary').click();const row=page.locator('[data-selfplay-question="'+id+'"]');if(!await row.evaluate(el=>el.open))await row.locator(':scope>summary').click();await row.locator('.sp-question-body>.sp-content').first().waitFor();return row;}
 const prefix='selfplay-v098',run=prefix+'-fresh-run',round1=prefix+'-fresh-round-1',round2=prefix+'-fresh-round-2',qid=suffix=>prefix+'-fresh-q-'+suffix,base='#/experiment?run='+run+'&view=training';
 const workspace=await httpJson('/api/studio/workspace');check('fixture remains one experiment with two explicitly synthetic runs',workspace.experiments.length===1&&workspace.experiments[0].runs.length===2&&workspace.experiments[0].runs.every(run=>run.demo===true));
 await go(base+'&selfplay_cycle='+round1,'.sp-counts');
 check('Self-play is embedded in exactly the two primary views',(await page.locator('.ex-tab').allTextContents()).join('|')==='Agent 执行评测|RL 训练评测');
 check('round selector explicitly opens round one',await page.getByLabel('Self-play 当前轮次',{exact:true}).inputValue()===round1);
 check('default Self-play has three compact summaries',await page.locator('.sp-counts>div').count()===3);
 check('independent newly generated consumed questions are counted once',await page.locator('[data-selfplay-metric="new_verified_consumed"]>strong').innerText()==='2');
 check('task coverage uses actual active rollout denominator',(await page.locator('[data-selfplay-metric="signal_coverage"]').innerText()).includes('50.0%')&&(await page.locator('[data-selfplay-metric="signal_coverage"]').innerText()).includes('2 / 4'));
 check('two external evaluations require an explicit selection',await page.locator('[data-selfplay-metric="external_em"]').getAttribute('data-status')==='selection_required'&&await page.getByLabel('Self-play 固定外部评测',{exact:true}).inputValue()==='');
 check('question details and comparisons are fetched lazily',!report.http_requests.some(r=>['/api/methods/selfplay-question','/api/methods/selfplay-comparison'].includes(r.path)));
 check('generation through completed updates remain six separate stages',await page.locator('.sp-supply>.sp-pair').count()===6&&(await page.locator('.sp-supply').innerText()).includes('实际采样')&&(await page.locator('.sp-supply').innerText()).includes('完成更新'));
 check('deduplication count states its all-candidate denominator',(await page.locator('.sp-supply').innerText()).includes('去重独立')&&(await page.locator('.sp-supply-note').innerText()).includes('全部生成候选')&&(await page.locator('.sp-supply-note').innerText()).includes('验证未通过与异常'));
 check('rejected validator error and unknown each remain visible',(await page.locator('.sp-supply-extra').innerText()).includes('验证未通过 1')&&(await page.locator('.sp-supply-extra').innerText()).includes('验证异常 1')&&(await page.locator('.sp-supply-extra').innerText()).includes('验证未知 1'));
 check('frozen solver selection is independent from current training solver',await page.getByLabel('Self-play 固定参考 Solver',{exact:true}).inputValue()===prefix+'-fresh-reference');
 await shot('01_selfplay_round_overview');
 await openFold(page.locator('.sp-training'),'固定参考 Solver：各轮题目难度');
 const difficulty=page.locator('[data-selfplay-difficulty-round="'+round1+'"]');
 check('one frozen solver shows all failed partly solved and all solved groups',(await difficulty.innerText()).includes('1\t1\t1\t4\t7'));
 check('difficulty table compares different rounds under one reference',await page.locator('[data-selfplay-difficulty-round]').count()===3);
 check('difficulty does not prescribe a universal fifty percent target',(await page.locator('.sp-training').innerText()).includes('不预设 50% 成功率为最优'));
 const good=await openQuestion(qid('good'));
 check('all generated questions including rejected duplicate and pending remain in list',await page.locator('[data-selfplay-question]').count()===7&&await page.locator('[data-selfplay-question="'+qid('duplicate')+'"]').count()===1&&await page.locator('[data-selfplay-question="'+qid('rejected')+'"]').count()===1);
 check('question detail preserves stable identity and first generation round',(await good.innerText()).includes('fixture-question-good')&&(await good.innerText()).includes(round1));
 check('supporting source evidence is reachable',await good.getByRole('link',{name:'查看已保存支持材料 →',exact:true}).count()>0);
 const origins=await openFold(good.locator('.sp-question-body'),'轮次、角色与出题条件');
 const proposal=origins.locator('[data-selfplay-related="'+qid('good')+'-proposal"]');await proposal.locator(':scope>summary').click();await proposal.getByText('Proposer 实际输入',{exact:true}).waitFor();
 check('actual proposer input and output are retained separately',(await proposal.innerText()).includes('Proposer 实际输入')&&(await proposal.innerText()).includes('Proposer 实际输出')&&(await proposal.innerText()).includes('doc-1'));
 const pool=await openFold(good.locator('.sp-question-body'),'去重、入池与复用');const admission=pool.locator('[data-selfplay-related="'+qid('good')+'-admitted"]');await admission.locator(':scope>summary').click();await admission.getByText('2026-01-01T00:01:00Z',{exact:true}).waitFor();
 check('question pool admission retains its timestamp',(await admission.innerText()).includes('2026-01-01T00:01:00Z'));
 const attempts=await openFold(good.locator('.sp-question-body'),'多次解答与训练信号');
 check('reference and training attempts remain individually visible',await attempts.locator('.sp-attempt-table tbody tr').count()===6&&(await attempts.innerText()).includes('固定参考')&&(await attempts.innerText()).includes('训练'));
 check('actual masks are read from linked training consumption',(await attempts.innerText()).includes('mask [1,1,0]')&&(await attempts.innerText()).includes('advantage -1'));
 const trainRow=attempts.locator('.sp-attempt-table tbody tr').filter({hasText:'advantage -1'});await trainRow.getByRole('button',{name:'展开',exact:true}).click();
 const attemptDetail=attempts.locator('[data-selfplay-attempt="'+prefix+'-fresh-r1-sample-0-attempt"]');await attemptDetail.getByRole('link',{name:'查看关联轨迹 →',exact:true}).first().click();await page.locator('.s-step-item').first().waitFor();
 check('a recorded solver attempt opens the existing full replay',await page.locator('.s-step-item').count()===3);
 await page.getByRole('link',{name:'返回实验',exact:true}).click();await ready();await page.locator('[data-selfplay-question="'+qid('good')+'"] .sp-question-body>.sp-content').first().waitFor();
 check('replay return preserves selected round and selected question',await page.getByLabel('Self-play 当前轮次',{exact:true}).inputValue()===round1&&await page.locator('[data-selfplay-question="'+qid('good')+'"]').evaluate(el=>el.open));
 const error=await openQuestion(qid('error'));const reviewFold=await openFold(error.locator('.sp-question-body'),'验证、拒绝与异常');await reviewFold.locator('[data-selfplay-related="'+qid('error')+'-review"]>:first-child').click();await reviewFold.getByText('合成验证器超时',{exact:true}).waitFor();
 check('validator failure details remain an error with saved reason',(await reviewFold.innerText()).includes('验证异常')&&(await reviewFold.innerText()).includes('合成验证器超时'));
 await page.getByLabel('Self-play 当前轮次',{exact:true}).selectOption(round2);await page.waitForFunction(()=>document.querySelector('[data-selfplay-metric="signal_coverage"]')?.dataset.status==='partial');
 const partial=await page.locator('[data-selfplay-metric="signal_coverage"]').innerText();check('missing actual mask keeps coverage unknown rather than a measured subset rate',partial.includes('部分记录')&&partial.includes('1 项未知')&&!partial.includes('25.0%')&&!partial.includes('50.0%'));
 check('old first consumption and reuse are separated from one new question',(await page.locator('.sp-supply-extra').innerText()).includes('旧题首次消费 1')&&(await page.locator('.sp-supply-extra').innerText()).includes('旧题复用 1')&&await page.locator('[data-selfplay-metric="new_verified_consumed"]>strong').innerText()==='1');
 const signal=await openFold(page.locator('.sp-training'),'实际 advantage、mask 与训练信号');
 check('RLVR and OPD coverage remain separate rows',await signal.locator('tbody tr').count()===2&&(await signal.innerText()).includes('任务 / RLVR')&&(await signal.innerText()).includes('OPD 蒸馏'));
 check('unknown rollout stays inside the four-rollout denominator',(await signal.locator('tbody tr').first().innerText()).includes('1\t2\t1\t4'));
 const incomplete=await openQuestion(qid('round2'));const incomAttempts=await openFold(incomplete.locator('.sp-question-body'),'多次解答与训练信号');
 check('masked nonzero component remains visible with its zero support',(await incomAttempts.innerText()).includes('advantage [0,0,2] / mask [1,1,0]'));
 await openFold(page.locator('.sp-training'),'固定参考 Solver：各轮题目难度');check('an unfinished K=3 probe is not called a complete partial-success task',(await page.locator('[data-selfplay-difficulty-round="'+round2+'"]').innerText()).includes('0\t0\t0\t1\t1'));
 await page.getByLabel('Self-play 固定外部评测',{exact:true}).selectOption(prefix+'-fresh-external-2');await page.waitForFunction(()=>document.querySelector('[data-selfplay-metric="external_em"]')?.dataset.status==='measured');
 check('independent fixed evaluation provides solver EM',await page.locator('[data-selfplay-metric="external_em"]>strong').innerText()==='100.0%');
 await page.getByLabel('Self-play 固定外部评测',{exact:true}).selectOption(prefix+'-fresh-external-0');await page.waitForFunction(()=>document.querySelector('[data-selfplay-metric="external_em"]>strong')?.textContent==='50.0%');
 check('changing external solver evaluation does not change selected round',await page.getByLabel('Self-play 当前轮次',{exact:true}).inputValue()===round2&&await page.locator('[data-selfplay-metric="new_verified_consumed"]>strong').innerText()==='1');
 await page.locator('.sp-comparison>summary').click();await page.getByLabel('Self-play 同预算供给对照',{exact:true}).waitFor();check('same-budget comparison requires its own explicit protocol selection',await page.getByLabel('Self-play 同预算供给对照',{exact:true}).inputValue()===''&&!report.http_requests.some(r=>r.path==='/api/methods/selfplay-comparison'));
 await page.getByLabel('Self-play 同预算供给对照',{exact:true}).selectOption(prefix+'-compare-supply');await page.locator('[data-selfplay-qualified="true"]').waitFor();
 check('fixed pool and continual supply retain separate run identities',await page.locator('[data-selfplay-arm]').count()===2&&(await page.locator('[data-selfplay-arm] a').allTextContents()).length===2);
 check('matched complete accounting shows the qualified external effect',(await page.locator('[data-selfplay-effect]').innerText()).includes('+50 pp'));
 await shot('02_selfplay_budget_comparison');
 await page.getByLabel('Self-play 同预算供给对照',{exact:true}).selectOption(prefix+'-compare-budget-missing');await page.locator('[data-selfplay-qualified="false"]').waitFor();
 check('missing generation cost suppresses controlled gain',(await page.locator('[data-selfplay-effect]').innerText()).includes('暂不显示受控收益')&&!(await page.locator('[data-selfplay-effect]').innerText()).includes('+50'));
 await page.setViewportSize({width:390,height:844});check('expanded Self-play controls and tables fit 390 pixels',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await shot('03_selfplay_mobile');await page.setViewportSize({width:1440,height:1060});
 await go('#/experiment?run='+run+'&view=results&result_b='+prefix+'-fresh-external-0','.sp-runtime');
 check('Agent view follows the selected standard fixed evaluation',(await page.locator('.sp-runtime').innerText()).includes(prefix+'-fresh-external-0')&&(await page.locator('.sp-runtime').innerText()).includes('50.0%'));
 check('Agent view does not display training supply summaries',await page.locator('.sp-counts,.sp-supply,.sp-question').count()===0);
 await page.getByLabel('实验当前评测 B',{exact:true}).selectOption(prefix+'-fresh-external-2');await page.waitForFunction(()=>document.querySelector('.sp-runtime')?.textContent.includes('100.0%'));
 check('standard result selection updates method evaluation scope',(await page.locator('.sp-runtime').innerText()).includes(prefix+'-fresh-external-2')&&!(await page.locator('.sp-runtime').innerText()).includes(prefix+'-fresh-external-0'));
 await shot('04_selfplay_agent','.sp-runtime');
 await go(base+'&selfplay_cycle='+round1+'&selfplay_question='+prefix+'-fixed-q-old','.sp-counts');
 check('another run question is not silently opened',await page.locator('[data-selfplay-question="'+prefix+'-fixed-q-old"]').count()===0&&(await page.locator('.sp-training').innerText()).includes('所选题目不在当前轮次 / 当前页'));
 check('no permanent manual annotation form is introduced',await page.locator('textarea:visible').count()===0);
 check('zero JavaScript errors',report.javascript_errors.length===0);
 check('all application HTTP reads succeeded',report.http_requests.every(r=>r.status>=200&&r.status<400));
 report.tested_compiled_entries=[...new Set(report.http_requests.filter(r=>/^\/assets\/main-.*\.js$/.test(r.path)).map(r=>r.path))];check('acceptance used one compiled application build',report.tested_compiled_entries.length===1);
 const manifest=await fs.readFile(path.join(root,'searchagent_lab/static/assets/build-manifest.json'));report.build_manifest_sha256=crypto.createHash('sha256').update(manifest).digest('hex');report.build=JSON.parse(manifest);report.passed=true;await fs.rm(path.join(out,'screenshots','selfplay-tracking-failure.png'),{force:true});await fs.rm(path.join(out,'selfplay-tracking-failure.txt'),{force:true});
}catch(error){report.failure={message:String(error),stack:error.stack};if(page&&!page.isClosed()){await page.screenshot({path:path.join(out,'screenshots','selfplay-tracking-failure.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(out,'selfplay-tracking-failure.txt'),String(error)+'\n'+await page.locator('body').innerText().catch(()=>''));}console.error(error.stack);}
finally{await browser?.close();if(child.exitCode==null){child.kill('SIGTERM');await new Promise(resolve=>{child.once('exit',resolve);setTimeout(resolve,3000).unref();});if(child.exitCode==null)child.kill('SIGKILL');}const after=await hashInput();report.source_data_unchanged=JSON.stringify(after)===JSON.stringify(originalHashes);report.source_hashes=originalHashes;if(!report.source_data_unchanged)report.passed=false;report.check_count=report.checks.length;await fs.writeFile(path.join(out,'selfplay-tracking-acceptance.json'),JSON.stringify(report,null,2)+'\n');await fs.rm(temp,{recursive:true,force:true});console.log(JSON.stringify({passed:report.passed,checks:report.check_count,source_data_unchanged:report.source_data_unchanged,report:path.join(out,'selfplay-tracking-acceptance.json')}));if(!report.passed)process.exitCode=1;}
