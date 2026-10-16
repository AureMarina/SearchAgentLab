from playwright.sync_api import sync_playwright, expect
from pathlib import Path
import json,time
BASE='#/experiment?run=skillrl-qwen3-4b-150steps-20261006'
A='uniopsd-qwen3-4b-step150-fixed11-eval-20261009'
import argparse, shutil
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
parser=argparse.ArgumentParser(description='Organization browser acceptance for the bundled fixed11 snapshot. Start its server first; requires Playwright and Chromium.')
parser.add_argument('--base-url',default='http://127.0.0.1:8765')
parser.add_argument('--artifact-dir',default='browser-artifacts-v0100')
parser.add_argument('--browser-path',default=shutil.which('chromium') or shutil.which('chromium-browser'))
parser.add_argument('--local-transport',action='store_true',help='For restricted QA environments only: route production assets and API through a synthetic browser origin.')
parser.add_argument('--fixture-data',required=True,help='Disposable copied workspace only; this test writes synthetic runs and a review.')
parser.add_argument('--allow-test-writes',action='store_true',required=True)
args=parser.parse_args()
OUT=Path(args.artifact_dir);OUT.mkdir(parents=True,exist_ok=True)

def open_app(page, hash_path):
 errors=[]
 page.on('pageerror',lambda error:errors.append(str(error)))
 if not args.local_transport:
  page.goto(args.base_url.rstrip('/')+'/'+hash_path,wait_until='domcontentloaded')
 else:
  # Test-only transport. The shipped application does not contain this shim.
  def forward(route):
   req=route.request; u=urlsplit(req.url)
   headers={k:v for k,v in req.headers.items() if k.lower() not in ['host','accept-encoding','origin']}
   headers['Origin']=args.base_url.rstrip('/')
   request=Request(args.base_url.rstrip('/')+u.path+('?' + u.query if u.query else ''),data=req.post_data_buffer,headers=headers,method=req.method)
   try: response=urlopen(request,timeout=30)
   except Exception as error:
    if hasattr(error,'read'): response=error
    else: raise
   with response: route.fulfill(status=response.code,headers={k:v for k,v in response.headers.items() if k.lower() not in ['content-length','content-encoding']},body=response.read())
  page.route('https://searchagent.test/**',forward)
  page.evaluate("""() => {
   for(const name of ['replaceState','pushState']) {
    const original=history[name].bind(history);
    history[name]=(state,title,url)=>original(state,title,typeof url==='string'&&url.startsWith('#')?'about:blank'+url:url);
   }
  }""")
  page.evaluate('(hash)=>location.hash=hash',hash_path)
  html=urlopen(args.base_url.rstrip('/')+'/').read().decode().replace('<head>','<head><base href="https://searchagent.test/">')
  page.set_content(html,wait_until='domcontentloaded')
  page.evaluate("""() => document.addEventListener('click',e=>{const a=e.target.closest('a'),url=a?.getAttribute('href');if(url?.startsWith('#/')){e.preventDefault();setTimeout(()=>{location.hash=a.getAttribute('href');},0);}},true)""")
 page.wait_for_selector('#s-app[aria-busy="false"]',timeout=60000)
 return errors

def screenshot(page,name):
 page.evaluate('()=>{document.activeElement?.blur();window.scrollTo(0,0)}')
 page.wait_for_timeout(150)
 page.screenshot(path=str(OUT/name),full_page=True)



import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from searchagent_lab.studio.sdk import StudioRecorder
fixture=Path(args.fixture_data).resolve()
assert fixture!=Path(__file__).resolve().parents[2]/'data','Never write fixtures into the shipped snapshot'
with StudioRecorder(fixture) as rec:
 rec.store.register_project({'id':'clarity-qa-only','name':'QA fixtures only','demo':True})
 for stage in ['sft','rl']:
  exp='clarity-qa-'+stage
  rec.store.register_experiment({'id':exp,'project_id':'clarity-qa-only','name':'QA synthetic '+stage,'demo':True})
  rec.register_experiment_profile(experiment_id=exp,track='main',kind='agent_iteration')
  rec.store.register_run({'id':exp,'experiment_id':exp,'name':'QA synthetic '+stage,'run_type':'training','training_stage':stage,'demo':True,'base_model':'Synthetic QA only'})
  rec.store.register_checkpoint({'id':exp+'-cp','run_id':exp,'step':1,'name':'QA only step1'})
 rec.store.log_metrics('clarity-qa-sft',1,{'train/loss':0.2,'train/token_accuracy':0.75})
 rec.log_sft_batch(run_id='clarity-qa-sft',batch_id='clarity-qa-sft-batch',step=1,dataset_version='synthetic-training-fixture-v1',samples=[
  {'id':'literal-demo','model_input':[{'role':'user','content':'QA fixture, not development data'}],
   'teacher_output':'literal teacher output','supervision_target':'literal target','input_ids':[2,3,4],
   'labels':[-100,3,4],'loss_mask':[0,1,1],'loss':0.2},
  {'id':'partial-demo','supervision_target':'only target exported'}])
 rec.store.log_metrics('clarity-qa-rl',1,{'train/loss':-0.3,'train/reward':0.0})
 rec.log_training_batch({'id':'clarity-qa-rl-batch','run_id':'clarity-qa-rl','step':1,'training_stage':'rl',
  'groups':[{'id':'zero-group','samples':[{'id':'r0','reward':0.0,'advantage':0.0},{'id':'r1','reward':0.0,'advantage':0.0}]}]})
 before=rec.store.export_evaluation('skillrl-qwen3-4b-step150-fixed11-eval-20261009');before.pop('exported_at',None)
checks=[];all_errors=[]
def done(name):checks.append(name);print('PASS',name,flush=True)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=args.browser_path,args=['--no-sandbox','--disable-dev-shm-usage'])
 page=browser.new_page(viewport={'width':1450,'height':1000})
 all_errors.append(open_app(page,'#/experiment?run=clarity-qa-sft&view=training'))
 page.wait_for_selector('.s-small-context')
 expect(page.locator('.s-small-context')).to_contain_text('SFT')
 expect(page.locator('.ex-pane')).not_to_contain_text('训练 Reward')
 assert not page.locator('.ex-pane .s-chart').filter(has_text='固定题诊断').count()
 page.get_by_role('button',name='查看监督样本 ↗',exact=True).click()
 page.get_by_text('demonstrations · 2 监督样本',exact=True).click()
 page.get_by_role('button',name='查看监督样本',exact=True).first.click()
 expect(page.locator('dialog')).to_contain_text('literal teacher output')
 expect(page.locator('dialog')).to_contain_text('literal target')
 expect(page.locator('dialog')).to_contain_text('-100')
 expect(page.locator('dialog')).to_contain_text('loss_mask 2/3')
 expect(page.locator('dialog')).to_contain_text('不是模型自主执行')
 screenshot(page,'04_sft_fixture_only.png')
 done('Explicit SFT stage uses actual recorded inputs/teacher/labels/masks, without Reward chart or fabricated evaluation')
 page.get_by_role('button',name='← 返回本步批次',exact=True).click()
 page.get_by_text('demonstrations · 2 监督样本',exact=True).click()
 page.get_by_role('button',name='查看监督样本',exact=True).nth(1).click()
 expect(page.locator('dialog')).to_contain_text('Token 未记录 · mask 未记录')
 expect(page.locator('dialog')).to_contain_text('未记录；不从教师文本或其他字段重建')
 done('Partially exported SFT examples retain missing input/labels/mask rather than reconstructing them')
 page2=browser.new_page(viewport={'width':1450,'height':1000});all_errors.append(open_app(page2,'#/experiment?run=clarity-qa-rl&view=training'))
 expect(page2.locator('.s-small-context')).to_contain_text('RL')
 page2.get_by_role('button',name='查看 batch 与 rollout group ↗',exact=True).click()
 page2.get_by_text('zero-group · 2 rollouts',exact=True).click()
 expect(page2.locator('dialog')).to_contain_text('同组奖励一致')
 expect(page2.locator('dialog')).to_contain_text('非零优势 0/2')
 expect(page2.locator('dialog')).to_contain_text('mask 未记录')
 done('RL grouped evidence shows actually recorded zero advantages and missing masks; no gradient inference')
 page3=browser.new_page(viewport={'width':1450,'height':1000});all_errors.append(open_app(page3,BASE+'&view=trace&trace_q=cathedral'))
 page3.wait_for_selector('.tb-column-footer')
 page3.locator('.cl-problem > summary').first.click()
 page3.get_by_text('人工复核（可选）',exact=True).click()
 note='QA synthetic manual review <img src=x onerror=alert(1)> — not a scientific annotation'
 page3.get_by_label('问题摘要复核说明',exact=True).fill(note)
 page3.get_by_role('button',name='保存复核',exact=True).click()
 expect(page3.locator('.cl-problem > summary')).to_contain_text('QA synthetic manual review',timeout=15000)
 expect(page3.locator('.cl-problem > summary')).to_contain_text('已复核')
 assert page3.locator('.cl-problem img').count()==0
 page3.locator('[data-view="results"]').click()
 expect(page3.locator('.cl-problem-cell').first).to_contain_text('QA synthetic manual review',timeout=15000)
 done('Optional review writes original hash-bound sidecar; the same escaped summary appears back in results')
 with StudioRecorder(fixture) as rec:
  after=rec.store.export_evaluation('skillrl-qwen3-4b-step150-fixed11-eval-20261009');after.pop('exported_at',None)
  assert after==before
 done('Review and SFT/RL diagnostic fixtures do not mutate original evaluation scores/trajectories')
 errors=[e for group in all_errors for e in group];assert not errors,errors
 done('No uncaught browser JavaScript errors in SFT/RL/manual-review paths')
 (OUT/'learning_acceptance.json').write_text(json.dumps({'passed':len(checks),'checks':checks,'errors':errors,'synthetic_fixtures':True,'fixture_data':str(fixture),'local_transport':args.local_transport,'not_training_results':True},ensure_ascii=False,indent=2))
 browser.close()
print('COMPLETE',len(checks),flush=True)
