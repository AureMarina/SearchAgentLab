/** Compiled application acceptance against real LabServer HTTP responses.
 * Usage: node tests/browser_integration_v093.mjs --data-dir ../data --out docs/v093
 * The supplied data is copied to a temporary directory. Synthetic examples are
 * generated into a different fresh temporary directory. Neither writes input data.
 * A virtual browser origin bridges requests to actual local HTTP with Node fetch;
 * API responses and compiled assets are not mocked or rewritten.
 */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const options={};for(let i=2;i<process.argv.length;i+=2)options[process.argv[i].replace(/^--/,'')]=process.argv[i+1];
if(!options['data-dir'])throw Error('--data-dir must name the delivered saved workspace.');
const source=path.resolve(options['data-dir']),out=path.resolve(options.out||path.join(root,'docs/v093'));
const wanted=options.mode?[options.mode]:['snapshot','synthetic'];
if(wanted.some(mode=>!['snapshot','synthetic'].includes(mode)))throw Error('--mode is snapshot or synthetic');
await fs.mkdir(path.join(out,'screenshots'),{recursive:true});
const library=process.env.SEARCHAGENT_PLAYWRIGHT_PATH||(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES?path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'playwright/index.mjs'):null);
const {chromium}=await import(library?pathToFileURL(library).href:'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.SEARCHAGENT_BROWSER_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
const modes=[],temp=await fs.mkdtemp(path.join(os.tmpdir(),'searchagent-integration-v093-'));
let activeServer=null,activePage=null,failed=false;
async function hashInput(){const files=(await fs.readdir(source)).filter(name=>name.endsWith('.sqlite3')).sort(),hashes={};for(const name of files)hashes[name]=crypto.createHash('sha256').update(await fs.readFile(path.join(source,name))).digest('hex');return hashes;}
const before=await hashInput();
const serverCode=`import json,sys\nfrom pathlib import Path\nfrom searchagent_lab.store import Store\nfrom searchagent_lab.server import LabServer\ndata=Path(sys.argv[1]);mode=sys.argv[2]\nif mode=='synthetic':\n sys.path.insert(0,str(Path.cwd()/'examples'))\n from method_evidence_example import build_example\n build_example(data)\nwith Store(data) as store,LabServer(('127.0.0.1',0),store) as server:\n ids=[r[0] for r in store.conn.execute('SELECT id FROM evaluations ORDER BY id').fetchall()]\n meta={'url':'http://127.0.0.1:'+str(server.server_address[1]),'evaluations':{eid:store.evaluation(eid)['stats'] for eid in ids}}\n print('QA_READY='+json.dumps(meta),flush=True)\n server.serve_forever()\n`;
async function start(mode){
 const data=path.join(temp,mode);if(mode==='snapshot')await fs.cp(source,data,{recursive:true});
 const child=spawn(process.env.SEARCHAGENT_PYTHON||process.env.CODEX_PRIMARY_RUNTIME_PYTHON||'python',['-u','-c',serverCode,data,mode],{cwd:root,stdio:['ignore','pipe','pipe']});activeServer=child;let stderr='';child.stderr.on('data',chunk=>stderr+=String(chunk));
 const metadata=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Local HTTP startup timeout: '+stderr)),30000);child.stdout.on('data',chunk=>{text+=String(chunk);const line=text.split('\n').find(value=>value.startsWith('QA_READY='));if(line){clearTimeout(timer);resolve(JSON.parse(line.slice(9)));}});child.once('exit',code=>{clearTimeout(timer);reject(Error('Local HTTP server exited '+code+': '+stderr));});});
 return {child,data,...metadata};
}
async function stop(child){if(!child||child.exitCode!=null)return;child.kill('SIGTERM');await new Promise(resolve=>{child.once('exit',resolve);setTimeout(resolve,3000).unref();});if(child.exitCode==null)child.kill('SIGKILL');if(activeServer===child)activeServer=null;}
try{
 for(const mode of wanted){
  const server=await start(mode),report={mode,synthetic:mode==='synthetic',transport:'Compiled assets + real LabServer HTTP through virtual-origin Node fetch bridge',checks:[],javascript_errors:[],http_requests:[],screenshots:[],source_evaluations:server.evaluations};modes.push(report);
  const context=await browser.newContext({viewport:{width:1440,height:1060}}),page=await context.newPage();activePage=page;page.setDefaultTimeout(14000);
  page.on('pageerror',error=>report.javascript_errors.push(String(error)));
  const origin='http://searchagent-'+mode+'.test';
  await page.route(origin+'/**',async route=>{
   const request=route.request(),url=new URL(request.url()),headers={...request.headers()};delete headers.host;delete headers['accept-encoding'];headers.origin=server.url;
   try{const response=await fetch(server.url+url.pathname+url.search,{method:request.method(),headers,body:['GET','HEAD'].includes(request.method())?undefined:request.postDataBuffer()});report.http_requests.push({path:url.pathname,query:url.search,status:response.status});const responseHeaders=Object.fromEntries(response.headers);delete responseHeaders['content-length'];delete responseHeaders['content-encoding'];await route.fulfill({status:response.status,headers:responseHeaders,body:Buffer.from(await response.arrayBuffer())});}
   catch(error){if(!page.isClosed()){try{await route.abort();}catch{}report.transport_errors??=[];report.transport_errors.push(String(error));}}
  });
  const check=(name,passed,detail)=>{report.checks.push({name,passed:!!passed,...(detail?{detail}:{})});console.log((passed?'PASS ':'FAIL ')+mode+' · '+name);if(!passed)throw Error(mode+': '+name);};
  const text=()=>page.locator('#s-app').innerText();
  async function ready(){await page.waitForFunction(()=>document.querySelector('#s-app')?.getAttribute('aria-busy')==='false');}
  async function go(hash,selector){await page.goto(origin+'/'+hash,{waitUntil:'domcontentloaded'});await ready();if(selector)await page.locator(selector).first().waitFor();}
  async function tab(view){await page.locator('.ex-tab[data-view="'+view+'"]').click();await page.waitForSelector('.ex-tab[data-view="'+view+'"][aria-selected="true"]');}
  async function shot(name,synthetic=mode==='synthetic'){const file=name+'.png';await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(out,'screenshots',file),fullPage:true,animations:'disabled'});report.screenshots.push({file:'screenshots/'+file,synthetic});}
  async function openEvidence(id){const outer=page.locator('.ma-evidence>.ma-fold');if(await outer.count()&&!await outer.evaluate(element=>element.open))await outer.locator(':scope>summary').click();const row=page.locator('.ma-evidence-row[data-object="'+id+'"]');if(!await row.evaluate(element=>element.open))await row.locator(':scope>summary').click();await row.locator('.ma-detail>.ma-facts,.ma-detail>.ma-content').first().waitFor();return row;}
  async function metricsAligned(){return page.locator('.ma-metric').evaluateAll(elements=>{const boxes=elements.map(element=>element.getBoundingClientRect());return boxes.length===3&&boxes.every(box=>Math.abs(box.top-boxes[0].top)<1&&Math.abs(box.bottom-boxes[0].bottom)<1);});}
  async function httpJson(endpoint){const response=await fetch(server.url+endpoint);if(!response.ok)throw Error('Real HTTP '+response.status+' '+endpoint);return response.json();}
  await go('#/experiments','#s-app h1');
  if(mode==='snapshot'){
   const workspace=await httpJson('/api/studio/workspace');
   check('four original experiments retained',workspace.experiments.length===4&&await page.locator('.w-groups [data-experiment]').count()===3&&await page.locator('[data-baseline]').count()===1);
   check('original seven-dataset native baseline score retained',(await text()).includes('36.80%')&&await page.locator('.w-baseline').count()===1);
   check('no global method metadata navigation',!(await page.locator('#s-nav').innerText()).includes('方法与元数据'));
   await shot('01_saved_workspace');
   const skillRun='skillrl-qwen3-4b-150steps-20261006',skillExperiment='skillrl-qwen3-4b-reproduction';
   await go('#/experiment?run='+skillRun+'&view=results','[data-result-sample]');
   check('Agent and RL evaluation lenses remain first two tabs',(await page.locator('.ex-tab').allTextContents()).slice(0,2).join('|')==='Agent 执行评测|RL 训练评测');
   const saved=server.evaluations['skillrl-qwen3-4b-step150-fixed11-eval-20261009'];
   check('SkillRL original fixed 11 questions and score retained',await page.locator('[data-result-sample]').count()===saved.expected&&(await page.locator('.f-kpis').innerText()).includes(saved.answer_correct+'/'+saved.expected));
   check('Agent view does not prefetch training dynamics',!report.http_requests.some(request=>request.path==='/api/studio/dynamics'));
   await tab('training');await page.locator('.s-chart').first().waitFor();
   check('RL view shows saved training curves',await page.locator('.s-chart').count()>=3);
   check('train-derived diagnostic panel is not promoted to independent test',(await text()).includes('未核对训练隔离'));
   await tab('skill');await page.locator('.ma-metric').first().waitFor();
   check('declared Skill tab has three honest missing metrics',await page.locator('.ma-metric').count()===3&&await page.locator('.ma-metric-value').allTextContents().then(values=>values.every(value=>['未记录','未测'].includes(value))));
   const parentLink=page.getByRole('link',{name:'查看实验级技能归档 →',exact:true});await parentLink.waitFor();
   check('unbound parent archive is a separate explicit link',(await parentLink.getAttribute('href')).includes('scope=experiment')&&await page.locator('.md-version').count()===0);
   await parentLink.click();await page.locator('#md-detail h2').first().waitFor();await ready();
   check('parent archive opens with explicit unbound experiment scope',await page.getByLabel('当前运行',{exact:true}).inputValue()===''&&(await page.evaluate(()=>location.hash)).includes('scope=experiment'));
   check('all 23 original skill snapshots are available',await page.locator('.md-version').count()===23);
   check('frozen step100 and separate skill/lesson counts preserved',(await page.locator('.md-selected-bar').innerText()).includes('冻结评测 · step 100')&&(await page.locator('.md-selected-bar').innerText()).includes('89 条技能')&&(await page.locator('.md-selected-bar').innerText()).includes('11 条经验教训'));
   check('snapshot statistics are compact below three effect metrics',await page.locator('.ma-metric').count()===3&&await page.locator('.md-snapshot-strip').count()===1&&await page.locator('.ex-pane .md-stat-grid').count()===0);
   check('saved Skill metric cards share aligned top and bottom edges',await metricsAligned());
   await shot('02_saved_skill_archive');
   await page.locator('.md-version').filter({hasText:'训练 · step 105'}).click();await page.waitForFunction(()=>document.querySelector('.md-selected-bar')?.textContent.includes('92 条技能'));
   await page.getByLabel('搜索技能全文').fill('dyn_060');await page.getByLabel('搜索技能全文').press('Enter');await page.waitForFunction(()=>document.querySelectorAll('.md-member').length===1);await page.locator('.md-member').click();
   await page.waitForFunction(()=>document.querySelector('#md-detail')?.textContent.includes('首次观测：step 105'));
   check('real skill content and saved lifecycle remain readable',(await page.locator('#md-detail').innerText()).includes('High-Certainty Memory First')&&(await page.locator('#md-detail').innerText()).includes('冻结评测库不含此技能'));
   await page.getByRole('button',{name:'版本内容差异',exact:true}).click();await page.locator('#s-modal[open] .md-code').waitFor();
   check('single-skill version diff is readable',(await page.locator('#s-modal .md-code').innerText()).includes('High-Certainty Memory First'));await page.locator('#s-modal-close').click();
   await page.getByRole('button',{name:'查询已登记使用记录',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#s-modal')?.textContent.includes('不是“使用次数为 0”'));
   check('missing historical usage is not rendered as zero use',(await page.locator('#s-modal').innerText()).includes('不能计算技能成功率'));await page.locator('#s-modal-close').click();
   await page.getByLabel('当前运行',{exact:true}).selectOption(skillRun);await page.locator('.ma-metric').first().waitFor();
   check('returning to run removes unbound skill content',await page.locator('.md-version').count()===0&&await page.getByRole('link',{name:'查看实验级技能归档 →',exact:true}).count()===1);
   for(const [run,eid] of [['remote-run-4dd0083cbd52c839ac4262c3c9b64979','remote-evaluation-671beb15243b682783668cd81be09d2b'],['uniopsd-qwen3-4b-step150-20261008','uniopsd-qwen3-4b-step150-fixed11-eval-20261009']]){
    await go('#/experiment?run='+run+'&view=results','[data-result-sample]');const expected=server.evaluations[eid];
    check(run+' keeps original per-question result',await page.locator('[data-result-sample]').count()===expected.expected&&(await page.locator('.f-kpis').innerText()).includes(expected.answer_correct+'/'+expected.expected));
    await tab('opd');await page.locator('.ma-metric').first().waitFor();
    check(run+' OPD is declared pending without invented branches',await page.locator('.ma-metric').count()===3&&await page.locator('.ma-evidence-row').count()===0&&(await text()).includes('尚未接入失败状态'));
    check(run+' exposes only applicable method tab',await page.locator('.ex-tabs [data-view="skill"],.ex-tabs [data-view="self_play"],.ex-tabs [data-view="entropy"],.ex-tabs [data-view="latent"]').count()===0);
   }
   await shot('03_saved_opd_pending');
   await page.setViewportSize({width:390,height:844});
   check('saved OPD page fits 390px width',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await go('#/experiment?experiment='+skillExperiment+'&scope=experiment&view=skill','#md-detail h2');
   check('real expanded Skill archive fits 390px width',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await shot('04_saved_skill_mobile');
  }else{
   const workspace=await httpJson('/api/studio/workspace');check('separate fresh synthetic workspace has three experiments',workspace.experiments.length===3&&workspace.experiments.every(experiment=>experiment.runs.every(run=>run.demo)));
   await go('#/experiment?run=sk-run&view=skill','.ma-metric');
   check('synthetic Skill has exactly three method metrics',await page.locator('.ma-metric').count()===3);
   check('multiple saved independent evaluations are not silently merged',await page.getByLabel('方法对应的固定评测记录').inputValue()===''&&await page.locator('[data-metric="external_em"] .ma-metric-value').innerText()==='待选择评测');
   await page.getByLabel('方法对应的固定评测记录').selectOption('sk-eval-new-bank');await page.waitForFunction(()=>document.querySelector('[data-metric="external_em"] .ma-metric-value')?.textContent==='100%');
   check('explicit assessment selection reaches real HTTP and URL',report.http_requests.some(request=>request.path==='/api/methods/assessment'&&request.query.includes('evaluation=sk-eval-new-bank'))&&(await page.evaluate(()=>location.hash)).includes('skill_evaluation=sk-eval-new-bank'));
   check('real token cost is displayed from selected evaluation',await page.locator('[data-metric="tokens_per_task"] .ma-metric-value').innerText()==='24');
   check('incomplete input remains outside injection denominator',(await page.locator('[data-metric="injection_coverage"]').innerText()).includes('1 / 2 · 1 条未知')&&await page.locator('[data-metric="injection_coverage"] .ma-metric-value').innerText()==='50%');
   const used=await openEvidence('sk-retrieval-used');
   check('skill rendered text is visible with final-input evidence',(await used.innerText()).includes('核对人物与问题指代是否一致，再查原始资料中的出生地。'));
   const finalInput=used.locator('details').filter({has:page.locator('summary').filter({hasText:'核对最终模型输入'})});await finalInput.locator(':scope>summary').click();
   check('final model input includes the actual rendered skill',(await finalInput.innerText()).includes('system')&&(await finalInput.innerText()).includes('再查原始资料'));
   const trimmed=await openEvidence('sk-retrieval-trimmed');check('selected but trimmed skill is shown as not injected',(await trimmed.locator(':scope>summary').innerText()).includes('记录为未注入'));
   const unknown=await openEvidence('sk-retrieval-unknown');check('unlogged skill input stays unknown',(await unknown.locator(':scope>summary').innerText()).includes('未核验'));
   await shot('05_synthetic_skill_evidence');
   await go('#/experiment?run=sk-run&view=results','[data-result-sample]');
   await page.getByLabel('实验当前评测 B').selectOption('sk-eval-new-bank');await page.waitForFunction(()=>document.querySelector('[aria-label="实验当前评测 B"]')?.value==='sk-eval-new-bank');
   await page.getByLabel('实验对照评测 A').selectOption('sk-eval-old-bank');await page.getByLabel('实验对比目标').waitFor();
   check('method-state comparison axis is available',await page.getByLabel('实验对比目标').locator('option[value="method_state"]').count()===1);
   await page.getByLabel('实验对比目标').selectOption('method_state');await page.waitForFunction(()=>document.querySelector('.ev-comparison>summary')?.textContent.includes('方法状态 / SkillBank')&&document.querySelector('.ev-comparison>summary')?.textContent.includes('已登记条件可比'));
   check('same-model old/new SkillBank comparison is compatible',(await page.locator('.f-kpis').innerText()).includes('↑ 1'));
   await page.locator('.ev-comparison>summary').click();check('paired token costs and method-state differences are inspectable',(await page.locator('.ev-comparison').innerText()).includes('配对消耗')&&await page.getByRole('button',{name:'SkillBank 与检索配置差异',exact:true}).count()===1);
   await shot('06_synthetic_method_state_comparison');
   await page.getByLabel('实验对照评测 A').selectOption('sk-eval-bank-off');await page.waitForFunction(()=>document.querySelector('.ev-comparison>summary')?.textContent.includes('已登记条件可比'));
   check('explicit Bank Off/On is also compatible under method-state axis',await page.getByLabel('实验对照评测 A').inputValue()==='sk-eval-bank-off'&&(await page.locator('.f-kpis').innerText()).includes('↑ 1'));
   await page.getByLabel('实验对比目标').selectOption('checkpoint');await page.waitForFunction(()=>document.querySelector('.ev-comparison>summary')?.textContent.includes('Checkpoint')&&document.querySelector('.ev-comparison>summary')?.textContent.includes('待核对条件'));
   check('checkpoint comparison still rejects a simultaneous Bank change',!(await page.locator('.f-kpis').innerText()).includes('↑ 1'));
   await go('#/reports?b=sk-eval-new-bank&a=sk-eval-old-bank&axis=method_state','.ev-comparison');
   check('full comparison report supports method-state controls',await page.getByLabel('报告对比目标').inputValue()==='method_state'&&(await page.locator('.ev-comparison>summary').innerText()).includes('已登记条件可比'));
   await go('#/experiment?run=sp-run&view=self_play','.ma-metric');
   check('Self-play keeps three headline metrics with full cycle history collapsed',await page.locator('.ma-metric').count()===3&&!await page.locator('.ma-legacy').evaluate(element=>element.open)&&await page.locator('.mx-question-card').count()===0);
   check('collapsed cycle history does not prefetch the full cycle',!report.http_requests.some(request=>request.path==='/api/methods/cycle'));
   check('new supply counts stable identity once',await page.locator('[data-metric="new_verified_consumed"] .ma-metric-value').innerText()==='1');
   check('Self-play supply shows sampled and completed-update stages separately',(await page.locator('.ma-supply-strip').innerText()).includes('已采样题')&&(await page.locator('.ma-supply-strip').innerText()).includes('已关联完成更新'));
   check('reuse and verifier error stay visible',(await page.locator('.ma-supply-note').innerText()).includes('复用题 1')&&(await page.locator('.ma-supply-note').innerText()).includes('验证异常 1'));
   await openEvidence('sp-q-valid');await shot('07_synthetic_selfplay_supply');
   await page.locator('.ma-legacy>summary').click();await page.locator('.mx-question-card').first().waitFor();
   check('expanding full Self-play history retains all four questions',await page.locator('.mx-question-card').count()===4);
   await go('#/experiment?run=sp-run&view=self_play&selfplay_object=sp-cycle-1','.mx-question-card');
   check('specific Self-play cycle deep link opens its folded history',await page.locator('.ma-legacy').evaluate(element=>element.open)&&await page.locator('.mx-question-card').count()===4&&await page.locator('.mx-object-row.selected[data-object="sp-cycle-1"]').count()===1);
   check('Self-play does not expose unrelated Skill or OPD tabs',await page.locator('.ex-tabs [data-view="skill"],.ex-tabs [data-view="opd"]').count()===0);
   await go('#/experiment?run=opd-run&view=opd','.ma-metric');
   check('OPD has three distinct method metrics',await page.locator('.ma-metric').count()===3);
   check('OPD metric cards share aligned top and bottom edges',await metricsAligned());
   check('OPD teacher metric preserves K and unmeasured states',(await page.locator('[data-metric="teacher_repair"]').innerText()).includes('K = 2')&&(await page.locator('[data-metric="teacher_repair"]').innerText()).includes('未知'));
   const rescore=await openEvidence('opd-failure-rescore');check('rescore-only failure has no executed continuation',(await rescore.locator(':scope>summary').innerText()).includes('0 次接续 · 1 次重打分')&&(await rescore.locator(':scope>summary').innerText()).includes('未测 / 未确认'));
   const rt=rescore.locator('.ma-related>.ma-fold').filter({has:page.locator('summary').filter({hasText:/Teacher 记录.*opd-teacher-rescore/})});await rt.locator(':scope>summary').click();await rt.getByText('只做重打分',{exact:true}).waitFor();
   check('rescore teacher exposes extra information and its limited interpretation',(await rt.innerText()).includes('hindsight_hint')&&(await rt.innerText()).includes('不计为已经运行的接续或任务修复'));
   const zero=rescore.locator('.ma-related>.ma-fold').filter({has:page.locator('summary').filter({hasText:/训练监督.*opd-supervision-zero/})});await zero.locator(':scope>summary').click();await zero.getByText('记录为无有效项',{exact:true}).waitFor();
   check('zero-advantage reweight remains distinct from successful repair',(await zero.innerText()).includes('原始与调整后 advantage 都为 0'));
   const repaired=await openEvidence('opd-failure-repair');check('verified repaired state is distinct from rescore',(await repaired.locator(':scope>summary').innerText()).includes('经验证成功'));
   const ct=repaired.locator('.ma-related>.ma-fold').filter({has:page.locator('summary').filter({hasText:/Teacher 记录.*opd-teacher-2/})});await ct.locator(':scope>summary').click();await ct.getByText('实际接续',{exact:true}).waitFor();
   check('executed teacher branch exposes output, context and verifier',(await ct.innerText()).includes('Teacher 输出 / 接续')&&(await ct.innerText()).includes('synthetic-environment-verifier')&&(await ct.innerText()).includes('hindsight_hint'));
   const sup=repaired.locator('.ma-related>.ma-fold').filter({has:page.locator('summary').filter({hasText:/训练监督.*opd-supervision-active/})});await sup.locator(':scope>summary').click();await sup.getByText('已记录有效项',{exact:true}).waitFor();
   await sup.getByText('实际训练消费记录',{exact:true}).click();await sup.getByText('opd-use-active',{exact:true}).first().waitFor();await sup.getByText('关联更新记录',{exact:true}).click();await sup.getByText('opd-update-1',{exact:true}).first().waitFor();
   check('supervision can be followed to actual consumption and completed update',(await sup.innerText()).includes('opd-batch-1')&&(await sup.innerText()).includes('opd-update-1')&&(await sup.innerText()).includes('已完成'));
   await shot('08_synthetic_opd_branches');
   await ct.getByRole('link',{name:'查看 Teacher 完整记录 ↗',exact:true}).click();await page.locator('.s-step-item').first().waitFor();await page.locator('.s-step-item').last().click();await page.waitForFunction(()=>document.querySelector('.s-replay-body')?.textContent.includes('银湾'));
   check('teacher branch opens saved shared trace viewer',await page.locator('.s-step-item').count()===3&&(await page.locator('.s-replay-body').innerText()).includes('银湾'));
   await page.getByRole('link',{name:'返回实验',exact:true}).click();await page.locator('.ma-assessment[data-family="opd"]').waitFor();await ready();
   check('trace return restores OPD and original failure selection',await page.locator('.ex-tab[data-view="opd"]').getAttribute('aria-selected')==='true'&&await page.locator('.ma-evidence-row[data-object="opd-failure-repair"]').evaluate(element=>element.open));
   check('method-specific pages have no permanent review form',await page.locator('textarea:visible').count()===0);
   await go('#/experiment?run=opd-run&view=opd&opd_evidence=sk-retrieval-used','.ma-metric');
   check('foreign method object is not opened inside current run',await page.locator('.ma-evidence-row[data-object="sk-retrieval-used"]').count()===0&&(await text()).includes('所选记录不在当前页中'));
   const invalid=await fetch(server.url+'/api/methods/assessment?experiment_id=sk-experiment&run_id=opd-run&family=opd');check('real API rejects mismatched experiment/run scope',invalid.status===400);
   await page.setViewportSize({width:390,height:844});await go('#/experiment?run=opd-run&view=opd','.ma-metric');await openEvidence('opd-failure-repair');
   check('OPD evidence fits 390px width',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await shot('09_synthetic_opd_mobile');
  }
  check('zero JavaScript runtime errors',report.javascript_errors.length===0,report.javascript_errors.join('\n'));
  check('all application HTTP reads succeeded',report.http_requests.every(request=>request.status>=200&&request.status<400));
  check('browser used compiled production entry',report.http_requests.some(request=>/^\/assets\/main-.*\.js$/.test(request.path)));
  report.passed=true;await fs.writeFile(path.join(out,'browser-'+mode+'.json'),JSON.stringify(report,null,2)+'\n');await context.close();activePage=null;await stop(server.child);
 }
}catch(error){failed=true;const current=modes.at(-1);if(current){current.passed=false;current.failure={message:String(error),stack:error.stack};}if(activePage&&!activePage.isClosed()){await activePage.screenshot({path:path.join(out,'screenshots','browser-failure.png'),fullPage:true});await fs.writeFile(path.join(out,'browser-failure.txt'),String(error)+'\n'+await activePage.locator('body').innerText());}console.error(error.stack);}
finally{
 await browser.close();await stop(activeServer);const after=await hashInput();const unchanged=JSON.stringify(before)===JSON.stringify(after);if(!unchanged)failed=true;
 const existing=[];for(const mode of ['snapshot','synthetic']){const current=modes.find(report=>report.mode===mode);if(current)existing.push(current);else{try{existing.push(JSON.parse(await fs.readFile(path.join(out,'browser-'+mode+'.json'),'utf8')));}catch{}}}
 const report={passed:!failed&&existing.length===2&&existing.every(item=>item.passed),version:'0.9.3',mode:'Compiled production frontend; real LabServer HTTP; virtual-origin transport bridge',checks:existing.reduce((sum,item)=>sum+item.checks.length,0),source_data_unchanged:unchanged,source_data_hashes:before,runs:existing,limitations:['No model, teacher, search service, optimizer or training was executed.','Snapshot screenshots show real supplied saved evidence. Screenshots prefixed synthetic display example fixtures only.','Browser traffic uses a virtual origin bridged to real local HTTP; network timing is not a performance benchmark.']};
 await fs.writeFile(path.join(out,'browser-acceptance.json'),JSON.stringify(report,null,2)+'\n');await fs.rm(temp,{recursive:true,force:true});console.log(JSON.stringify({passed:report.passed,checks:report.checks,source_data_unchanged:unchanged,report:path.join(out,'browser-acceptance.json')}));if(failed)process.exitCode=1;
}
