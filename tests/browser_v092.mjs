/** Real compiled app + real LabServer HTTP, with virtual-origin browser routing. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [base,out,mode]=process.argv.slice(2);
const library=process.env.SEARCHAGENT_PLAYWRIGHT_PATH;
const {chromium}=await import(library?pathToFileURL(library).href:'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.SEARCHAGENT_BROWSER_PATH||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
const page=await browser.newPage({viewport:{width:1440,height:1040}}),errors=[],checks=[],requests=[];
page.on('pageerror',error=>errors.push(String(error)));
page.setDefaultTimeout(12000);
await page.route('http://searchagent.local/**',async route=>{
 const req=route.request(),url=new URL(req.url()),headers={...req.headers()};
 delete headers.host;delete headers['accept-encoding'];headers.origin=base;
 const response=await fetch(base+url.pathname+url.search,{method:req.method(),headers,body:['GET','HEAD'].includes(req.method())?undefined:req.postDataBuffer()});
 requests.push({path:url.pathname,status:response.status});
 const responseHeaders=Object.fromEntries(response.headers);delete responseHeaders['content-encoding'];delete responseHeaders['content-length'];
 await route.fulfill({status:response.status,headers:responseHeaders,body:Buffer.from(await response.arrayBuffer())});
});
function check(name,condition){if(!condition)throw Error('FAIL '+name);checks.push(name);console.log('PASS '+name);}
async function go(hash,selector){await page.evaluate(h=>location.hash=h,hash);await page.waitForSelector(selector);}
async function screenshot(name){await page.evaluate(()=>scrollTo(0,0));await fs.mkdir(path.join(out,'screenshots'),{recursive:true});await page.screenshot({path:path.join(out,'screenshots',name),fullPage:true,animations:'disabled'});}
try{
 await page.goto('http://searchagent.local/#/experiments');await page.waitForSelector('#s-app h1');
 if(mode==='snapshot'){
  check('original homepage and native baseline retained',(await page.locator('#s-app').innerText()).includes('36.80%'));
  await screenshot('01_home.png');
  const hash='#/experiment?run=skillrl-qwen3-4b-150steps-20261006&view=results';
  await go(hash,'[data-result-sample]');await page.waitForSelector('.ev-context');
  check('two evaluation lenses inside original experiment',(await page.locator('.ex-tab').allTextContents()).slice(0,2).join('|')==='Agent 执行评测|RL 训练评测');
  check('saved fixed questions and scores unchanged',await page.locator('[data-result-sample]').count()===11&&(await page.locator('.f-kpis').innerText()).includes('8/11'));
  check('Agent view does not load training matrices',!requests.some(r=>r.path==='/api/studio/dynamics'));
  check('legacy purpose not fabricated',(await page.locator('.ev-context').innerText()).includes('用途未声明'));
  await page.locator('.ev-details').first().locator('summary').click();
  check('execution reasons and costs available',(await page.locator('.ev-details').first().innerText()).includes('工具调用'));
  await screenshot('02_agent_evaluation.png');
  await page.locator('[data-view="training"]').click();await page.waitForSelector('.s-chart');
  check('RL view puts fixed diagnostic evidence first',(await page.locator('.s-chart').first().innerText()).includes('固定题'));
  check('train-split panel is not claimed as held-out',(await page.locator('#ex-pane').innerText()).includes('未核对训练隔离'));
  check('training reward and loss have separate charts',await page.locator('.s-chart-grid').first().locator('.s-chart').count()===3);
  await screenshot('03_rl_evaluation.png');
  await page.locator('[data-view="results"]').click();await page.waitForSelector('[data-result-sample]');
  await page.getByRole('link',{name:'查看轨迹 →',exact:true}).first().click();await page.waitForSelector('.ev-answer-evidence');
  const evidence=await page.locator('.ev-answer-evidence').innerText();
  check('raw output extraction gold and score separately visible',['模型最终输出','实际解析答案','标准答案','已记录的评分'].every(x=>evidence.includes(x)));
  check('Agent trace is not a training rollout',await page.locator('.ev-training-context').count()===0);
  check('manual review stays collapsed',await page.locator('textarea:visible').count()===0&&await page.locator('.f-log-fold[open]').count()===0);
  await screenshot('04_agent_trajectory.png');
  await page.getByRole('link',{name:'返回实验',exact:true}).click();await page.waitForSelector('[data-result-sample]');
  check('trace returns to original result lens',await page.locator('[data-view="results"]').getAttribute('aria-selected')==='true');
  await go('#/reports?b=skillrl-qwen3-4b-step150-fixed11-eval-20261009&a=uniopsd-qwen3-4b-step150-fixed11-eval-20261009&axis=harness','.ev-comparison');
  check('explicit controlled comparison axis',await page.getByLabel('报告对比目标',{exact:true}).inputValue()==='harness');
  await page.locator('.ev-comparison summary').click();
  check('different protocols do not claim a controlled gain',(await page.locator('.ev-comparison').innerText()).includes('待核对')&&(await page.locator('.s-report-cards').innerText()).includes('未启用可比对照'));
  await screenshot('05_comparison_conditions.png');
  await go(hash,'[data-result-sample]');await page.setViewportSize({width:390,height:844});
  check('mobile document has no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await screenshot('06_mobile.png');
 }else if(mode==='no_panel'){
  await go('#/experiment?run=browser-run&view=training','.s-chart');
  check('no-panel run still shows its training reward',(await page.locator('#ex-pane').innerText()).includes('训练 Reward'));
  check('no-panel run still shows its recorded loss',(await page.locator('.s-chart').filter({hasText:'训练 Loss'}).innerText()).includes('train/loss'));
  const firstChart=await page.locator('.s-chart').first().innerText();
  check('validation log does not replace fixed evaluation',!firstChart.includes('0.75')&&!firstChart.includes('val/success_rate'));
  await page.getByRole('button',{name:/查看 batch/}).click();await page.waitForSelector('#s-modal[open]');
  const group=page.locator('#s-modal details').first();await group.locator('summary').click();
  check('actual small signed advantages are preserved',(await group.innerText()).includes('1.000e-5'));
  check('missing training fields are explicit',(await group.innerText()).includes('未记录'));
  await group.getByRole('link',{name:/查看.*轨迹/}).first().click();await page.waitForSelector('.ev-training-context');
  check('inline rollout uses shared trace viewer',(await page.locator('h1').innerText())==='训练采样轨迹'&&await page.locator('.s-step-item').count()===1);
  check('training rollout excluded from fixed evaluation',(await page.locator('.ev-training-context').innerText()).includes('不计入固定题评测'));
  check('recorded response mask displayed',(await page.locator('.ev-training-context').innerText()).includes('2 / 3'));
  await page.fill('#s-trace-search','Recorded synthetic output');await page.press('#s-trace-search','Enter');await page.waitForSelector('.s-replay-body mark');
  check('inline rollout search preserves original pointers',await page.locator('.s-replay-body mark').count()>0);
  await page.getByRole('link',{name:'返回实验',exact:true}).click();await page.waitForSelector('[data-view="training"][aria-selected="true"]');
  check('training return restores selected optimization step',(await page.evaluate(()=>location.hash)).includes('step=2'));
 }else if(mode==='provenance'){
  async function openGroup(id){
   await go('#/experiment?run=browser-run&view=training&step=3','.s-chart');
   await page.getByRole('button',{name:/查看 batch/}).click();await page.waitForSelector('#s-modal[open]');
   const group=page.locator('#s-modal details').filter({has:page.locator('summary').filter({hasText:new RegExp('^'+id+' ·')})});
   await group.locator('summary').click();return group;
  }
  const evidenceGroup=await openGroup('evidence-only');
  await evidenceGroup.getByRole('link',{name:'查看训练轨迹 ↗',exact:true}).click();
  await page.waitForSelector('.ev-batch-evidence');
  check('evidence-only reference keeps Agent execution title',await page.locator('#s-app h1').innerText()==='Agent 执行轨迹');
  check('evidence-only reference has no confirmed training banner',await page.locator('.ev-training-context:not(.ev-batch-evidence)').count()===0);
  const association=await page.locator('.ev-batch-evidence').innerText();
  check('neutral association explains batch fields belong to the selected sample',association.includes('批次关联证据')&&association.includes('以下字段属于所选批次样本；该执行未确认为训练采样。'));
  const batchReward=page.locator('.ev-batch-evidence .ev-training-values > div').filter({has:page.locator('small').filter({hasText:/^Reward$/})});
  check('association uses batch reward and never falls back to execution reward',await batchReward.locator('strong').innerText()==='0.125'&&!association.includes('0.875'));
  const answerEvidence=await page.locator('.ev-answer-evidence').innerText();
  check('execution EM remains separate from referring batch reward',answerEvidence.includes('EM 1 · 正确')&&!answerEvidence.includes('0.125')&&!answerEvidence.includes('采样任务结果'));
  check('evidence reference shows the original execution content',(await page.locator('.s-replay-body').innerText()).includes('SYNTHETIC EVIDENCE ORIGINAL'));

  const conflictGroup=await openGroup('legacy-conflict');
  await conflictGroup.getByRole('link',{name:'查看训练轨迹 ↗',exact:true}).click();
  await page.waitForSelector('.ev-answer-evidence');
  check('legacy conflicting reference keeps Agent execution title',await page.locator('#s-app h1').innerText()==='Agent 执行轨迹');
  check('legacy conflict never restores a training association from raw sample ref',await page.locator('.ev-training-context').count()===0);
  const conflictText=await page.locator('#s-app').innerText();
  check('legacy conflicting association is explicitly rejected in the viewer',conflictText.includes('未读取到训练批次关联')&&conflictText.includes('以下按原始执行记录展示'));
  check('rejected association preserves original trace and task result',(await page.locator('.s-replay-body').innerText()).includes('SYNTHETIC CONFLICT ORIGINAL')&&(await page.locator('.ev-answer-evidence').innerText()).includes('EM 0 · 错误')&&!conflictText.includes('0.625'));

  const numericGroup=await openGroup('numeric-collision');
  const unnamed=numericGroup.locator('tbody tr').nth(1).getByRole('link',{name:'查看训练轨迹 ↗',exact:true});
  const target=await unnamed.getAttribute('href'),params=new URLSearchParams(target.slice(target.indexOf('?')+1));
  check('anonymous sample link carries explicit index alongside colliding numeric id',params.get('training_sample_index')==='1'&&params.get('training_sample')==='1');
  await unnamed.click();await page.waitForSelector('.ev-training-context:not(.ev-batch-evidence)');
  const execution=await page.locator('.s-replay-body').innerText();
  check('explicit sample index opens anonymous B instead of named A',execution.includes('SYNTHETIC INDEX B anonymous sample')&&!execution.includes('SYNTHETIC INDEX A'));
  const selectedReward=page.locator('.ev-training-values > div').filter({has:page.locator('small').filter({hasText:/^Reward$/})});
  check('disambiguated rollout displays its own reward and training purpose',await selectedReward.locator('strong').innerText()==='0.25'&&await page.locator('#s-app h1').innerText()==='训练采样轨迹');
 }else{
  throw Error('Unknown acceptance mode: '+mode);
 }
 check('zero JavaScript runtime errors',errors.length===0);
 check('all app HTTP reads succeeded',requests.every(r=>r.status>=200&&r.status<400));
 await fs.writeFile(path.join(out,'browser-'+mode+'.json'),JSON.stringify({mode,checks,errors,requests:requests.length},null,2)+'\n');
}catch(error){await fs.mkdir(out,{recursive:true});await page.screenshot({path:path.join(out,'browser-failure-'+mode+'.png'),fullPage:true});await fs.writeFile(path.join(out,'browser-failure-'+mode+'.txt'),String(error)+'\n'+(await page.locator('body').innerText())+'\n'+JSON.stringify(errors));throw error;}
finally{await browser.close();}
