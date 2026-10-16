/** Isolated UI regression cases, using clearly synthetic fixtures (no benchmark data). */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const output=path.resolve(process.argv[2]||'/tmp/searchagent-method-ui-v093');
const library=process.env.SEARCHAGENT_PLAYWRIGHT_PATH;
const {chromium}=await import(library?pathToFileURL(library).href:'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.SEARCHAGENT_BROWSER_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
const page=await browser.newPage({viewport:{width:1180,height:900}}),checks=[],errors=[];
page.on('pageerror',error=>errors.push(String(error)));page.setDefaultTimeout(8000);
const scope={project_id:'synthetic-project',experiment_id:'synthetic-experiment',run_id:'synthetic-run'};
const failure={id:'synthetic-failure',kind:'opd_failure',scope,demo:true,title:'Synthetic failure state',data:{sample_id:'synthetic-question',student_checkpoint_id:'synthetic-cp',state_hash:'synthetic-state',remaining_budget:{search:2},student_input:'SYNTHETIC STUDENT INPUT'},content_hash:'a'.repeat(64)};
const teacher={id:'synthetic-rescore',kind:'opd_teacher',scope,demo:true,title:'Synthetic rescore',data:{failure_id:failure.id,mode:'rescore',context:{hint:'SYNTHETIC EXTRA INFORMATION'},teacher_input:'SYNTHETIC TEACHER INPUT',teacher_output:'<img src=x onerror="window.fixtureUnsafe=true">',verification:{status:'not_run'}},content_hash:'b'.repeat(64)};
const supervision={id:'synthetic-supervision',kind:'opd_supervision',scope,demo:true,title:'Synthetic inactive supervision',data:{failure_id:failure.id,objective:'advantage_reweight',loss_mask_tokens:0,loss_weight:1,loss_active:false,original_advantage:0,effective_advantage:0},content_hash:'c'.repeat(64)};
const fixture={scope,family:'opd',status:'partial',metrics:[
 {id:'repair',label:'Teacher 接续成功率',value:.5,unit:'ratio',status:'partial',numerator:1,denominator:2,unknown:1},
 {id:'supervision',label:'有效监督覆盖率',value:0,unit:'ratio',status:'measured',numerator:0,denominator:2,unknown:0},
 {id:'independent_em',label:'Student 独立 EM',value:null,unit:'ratio',status:'not_recorded',missing_reason:'SYNTHETIC no independent evaluation'},
],counts:{failures:3},evidence:{items:[{...failure,assessment:{eligible:true,attempt_budget:2,continuation_attempts:0,rescore_count:1,repair_success:null,supervision_count:1,active_supervision_count:0,completed_update:null,verification_passes:0,verification_failures:0,verification_errors:0}}],total:1,offset:0,limit:25,has_more:false},protocol_notes:['Synthetic fixture: no training or task execution occurred.']};
await page.route('http://method-ui.test/**',async route=>{
 const url=new URL(route.request().url());
 if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/studio/shell.css"></head><body><main id="host" style="margin:20px;min-width:0"></main><dialog id="s-modal"><h2 id="s-modal-title"></h2><div id="s-modal-body"></div></dialog></body></html>'});
 const absolute=path.resolve(root,'searchagent_lab/static','.'+url.pathname);
 if(!absolute.startsWith(path.join(root,'searchagent_lab/static')+path.sep))return route.fulfill({status:400,body:'Invalid test path'});
 try{return route.fulfill({status:200,contentType:absolute.endsWith('.js')?'text/javascript':absolute.endsWith('.css')?'text/css':'application/octet-stream',body:await fs.readFile(absolute)});}catch{return route.fulfill({status:404,body:'Missing test asset'});}
});
function check(name,condition){if(!condition)throw Error('FAIL '+name);checks.push(name);console.log('PASS '+name);}
try{
 await page.goto('http://method-ui.test/');
 await page.evaluate(async ({fixture,scope,failure,teacher,supervision})=>{
  window.component=await import('/studio/method-assessment.js');window.fixture={fixture,scope,failure,teacher,supervision};window.calls=[];window.states=[];window.fixtureUnsafe=false;
  window.ctx={q:new URLSearchParams(),alive:()=>true,setViewState:patch=>window.states.push(patch),api:async (name,params)=>{
   window.calls.push({name,params});
   if(name==='methods/assessment')return fixture;
   if(name==='methods/object')return {object:failure,related:[teacher,supervision,{...teacher,id:'cross-scope-teacher',scope:{...scope,run_id:'OTHER'},data:{teacher_output:'MUST NOT DISPLAY'}}],lineage:{nodes:[]}};
   throw Error('Unexpected test API '+name);
  }};
  window.cleanup=await window.component.mountMethodAssessment(document.querySelector('#host'),window.ctx,'opd',scope);
 },{fixture,scope,failure,teacher,supervision});
 check('three method headline metrics',await page.locator('.ma-metric').count()===3);
 check('fraction ratio is presented as percent',await page.locator('[data-metric="repair"] .ma-metric-value').innerText()==='50%');
 check('recorded zero remains zero',await page.locator('[data-metric="supervision"] .ma-metric-value').innerText()==='0%');
 check('missing external result remains unknown',await page.locator('[data-metric="independent_em"] .ma-metric-value').innerText()==='未记录');
 check('unknown denominator coverage visible',(await page.locator('[data-metric="repair"]').innerText()).includes('1 / 2 · 1 条未知'));
 check('full objects remain lazy before expanding',await page.evaluate(()=>window.calls.filter(x=>x.name==='methods/object').length)===0);
 await page.locator('.ma-evidence-row>summary').click();await page.getByText('SYNTHETIC STUDENT INPUT',{exact:true}).waitFor();
 check('opening a case records selection inside experiment',await page.evaluate(()=>window.states.some(x=>x.opd_evidence==='synthetic-failure')));
 check('unrelated run evidence is excluded',!(await page.locator('#host').innerText()).includes('MUST NOT DISPLAY')&&await page.locator('.ma-related>.ma-fold').count()===2);
 const teacherFold=page.locator('.ma-related>.ma-fold').filter({has:page.locator('summary').filter({hasText:'Synthetic rescore'})});
 await teacherFold.locator('summary').first().click();await teacherFold.getByText('只做重打分',{exact:true}).waitFor();
 check('rescore is distinct from an executed continuation',(await teacherFold.innerText()).includes('只做重打分')&&(await teacherFold.innerText()).includes('不计为已经运行的接续或任务修复'));
 check('teacher extra input and actual input are readable',(await teacherFold.innerText()).includes('SYNTHETIC EXTRA INFORMATION')&&(await teacherFold.innerText()).includes('SYNTHETIC TEACHER INPUT'));
 check('logged markup is inert text',await teacherFold.locator('.ma-text').allTextContents().then(texts=>texts.some(text=>text.includes('<img src=x')))&&!await page.evaluate(()=>window.fixtureUnsafe)&&await teacherFold.locator('img').count()===0);
 const supervisionFold=page.locator('.ma-related>.ma-fold').filter({has:page.locator('summary').filter({hasText:'Synthetic inactive supervision'})});
 await supervisionFold.locator('summary').first().click();await supervisionFold.getByText('记录为无有效项',{exact:true}).waitFor();
 check('inactive supervision does not claim an update',(await supervisionFold.innerText()).includes('记录为无有效项')&&!(await supervisionFold.innerText()).includes('已完成'));
 check('no permanent manual review form',await page.locator('textarea,input').count()===0);
 await page.setViewportSize({width:390,height:844});
 check('expanded evidence fits narrow viewport',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await fs.mkdir(output,{recursive:true});await page.screenshot({path:path.join(output,'synthetic-opd-component.png'),fullPage:true});
 await page.evaluate(async()=>{
  window.cleanup();const host=document.querySelector('#host');host.replaceChildren();
  window.cleanup=await window.component.mountMethodAssessment(host,{...window.ctx,api:async()=>({...window.fixture.fixture,scope:{...window.fixture.scope,run_id:'OTHER'}})},'opd',window.fixture.scope);
 });
 check('mismatched run summary is rejected',(await page.locator('#host').innerText()).includes('实验 / 运行归属不一致')&&await page.locator('.ma-metric').count()===0);
 await page.evaluate(async()=>{
  window.cleanup();const host=document.querySelector('#host');host.replaceChildren();
  window.cleanup=await window.component.mountMethodAssessment(host,{...window.ctx,api:async(name,params)=>{
   window.calls.push({name,params});
   return {...window.fixture.fixture,evaluation_options:[{evaluation_id:'synthetic-eval-a',label:'Synthetic checkpoint A'},{evaluation_id:'synthetic-eval-b',label:'Synthetic checkpoint B'}],evaluation:{evaluation_id:params.evaluation||null},metrics:[...window.fixture.fixture.metrics.slice(0,2),{id:'external_em',label:'独立评测 EM',value:params.evaluation==='synthetic-eval-a'?.25:null,unit:'ratio',denominator:params.evaluation?4:null,status:params.evaluation?'measured':'selection_required'}],evidence:{items:[],total:0}};
  }},'opd',window.fixture.scope);
 });
 check('multiple evaluations require an explicit choice',await page.getByLabel('方法对应的固定评测记录').inputValue()===''&&await page.locator('[data-metric="external_em"] .ma-metric-value').innerText()==='待选择评测');
 await page.getByLabel('方法对应的固定评测记录').selectOption('synthetic-eval-a');await page.locator('[data-metric="external_em"][data-status="measured"]').waitFor();
 check('explicit evaluation selector forwards identity and preserves URL state',await page.evaluate(()=>window.calls.some(x=>x.params?.evaluation==='synthetic-eval-a')&&window.states.some(x=>x.opd_evaluation==='synthetic-eval-a'))&&await page.locator('[data-metric="external_em"] .ma-metric-value').innerText()==='25%');
 check('macro result displays sample count without inventing a micro numerator',(await page.locator('[data-metric="external_em"]').innerText()).includes('4 题')&&!(await page.locator('[data-metric="external_em"]').innerText()).includes('1 / 4'));
 await page.evaluate(async()=>{
  window.cleanup();const host=document.querySelector('#host');host.replaceChildren();
  const fixture={...window.fixture.fixture,family:'self_play',evidence:{total:2,items:[
   {id:'accepted-but-invalid',kind:'generated_question',scope:window.fixture.scope,assessment:{review_status:'accepted',verified:false,consumed:null}},
   {id:'accepted-but-unconfirmed',kind:'generated_question',scope:window.fixture.scope,assessment:{review_status:'accepted',verified:null,consumed:null}},
  ]}};
  window.cleanup=await window.component.mountMethodAssessment(host,{...window.ctx,api:async()=>fixture},'self_play',window.fixture.scope);
 });
 await page.locator('.ma-evidence>.ma-fold>summary').click();
 check('acceptance cannot override explicit failed verification',(await page.locator('[data-object="accepted-but-invalid"]>summary').innerText()).includes('验证未通过')&&!(await page.locator('[data-object="accepted-but-invalid"]>summary').innerText()).includes('验证接受'));
 check('accepted question with unknown verification remains unconfirmed',(await page.locator('[data-object="accepted-but-unconfirmed"]>summary').innerText()).includes('已接受 · 验证未确认'));
 check('missing consumption stays unknown instead of claiming not sampled',(await page.locator('[data-object="accepted-but-unconfirmed"]>summary').innerText()).includes('未记录')&&!(await page.locator('[data-object="accepted-but-unconfirmed"]>summary').innerText()).includes('记录为未采样'));
 await page.evaluate(async()=>{
  window.cleanup();const host=document.querySelector('#host');host.replaceChildren();let firstActive=true,release;
  const late=new Promise(resolve=>{release=resolve;});
  const first=window.component.mountMethodAssessment(host,{...window.ctx,alive:()=>firstActive,api:async()=>late},'opd',window.fixture.scope);
  firstActive=false;host.replaceChildren();
  const current={...window.fixture.fixture,family:'self_play',metrics:[{id:'supply',label:'Synthetic supply',value:0,unit:'count',status:'measured'},{id:'signal',label:'Synthetic signal',value:null,status:'not_recorded'},{id:'em',label:'Synthetic external EM',value:null,status:'not_recorded'}],evidence:{items:[],total:0},counts:{generated_questions:0}};
  window.cleanup=await window.component.mountMethodAssessment(host,{...window.ctx,api:async()=>current},'self_play',window.fixture.scope);
  release(window.fixture.fixture);const dispose=await first;dispose();
 });
 check('late old-family response cannot replace current pane',await page.locator('.ma-assessment[data-family="self_play"]').count()===1&&await page.locator('.ma-assessment[data-family="opd"]').count()===0&&(await page.locator('#host').innerText()).includes('Synthetic supply'));
 check('empty evidence does not display an invented zero task score',await page.locator('[data-metric="em"] .ma-metric-value').innerText()==='未记录');
 check('zero JavaScript errors',errors.length===0);
 await fs.writeFile(path.join(output,'component-checks.json'),JSON.stringify({synthetic:true,checks,errors},null,2)+'\n');
}catch(error){await fs.mkdir(output,{recursive:true});await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});await fs.writeFile(path.join(output,'failure.txt'),String(error)+'\n'+await page.locator('body').innerText()+'\n'+JSON.stringify(errors));throw error;}
finally{await browser.close();}
