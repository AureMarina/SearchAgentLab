"""Browser regression via an in-memory static module / real HTTP API bridge.
Chromium navigation to localhost is blocked by this execution sandbox. Only
ES-module import URLs are rewritten to blob URLs; application logic is intact.
API data, binary uploads, storage, and review writes use the real HTTP server.
This does not validate a browser's native direct localhost navigation.
"""
import sys,tempfile,threading,urllib.request,urllib.error,re,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from searchagent_lab.store import Store
from searchagent_lab.server import LabServer
from playwright.sync_api import sync_playwright
STATIC=ROOT/'searchagent_lab/static'

def launch_page(browser,base,hash='#/trace',errors=None):
 page=browser.new_page(viewport={'width':1760,'height':1100})
 page.on('pageerror',lambda e: (errors.append(str(e)) if errors is not None else print('PAGE_ERROR',e)))
 page.set_default_timeout(10000)
 def bridge(path,options=None):
  opts=options or {};headers=opts.get('headers',{})
  body=opts.get('body');body=body.encode() if isinstance(body,str) else bytes(body) if body is not None else None
  try:r=urllib.request.urlopen(urllib.request.Request(base+path,data=body,headers=headers,method=opts.get('method','GET')))
  except urllib.error.HTTPError as e:r=e
  return {'body':r.read().decode(),'status':r.status,'headers':dict(r.headers)}
 page.expose_function('__local_http',bridge)
 html=re.sub(r'<(?:link)[^>]*>','',(STATIC/'legacy.html').read_text());html=re.sub(r'<script[^>]*>.*?</script>','',html,flags=re.S)
 page.set_content(html)
 page.add_style_tag(content=(STATIC/'style.css').read_text())
 page.add_style_tag(content=(STATIC/'inspector/inspector.css').read_text())
 page.evaluate('(h)=>{location.hash=h}',hash)
 page.add_script_tag(content='''window.fetch=async (url,opts={})=>{let body=opts.body;if(body instanceof Blob)body=Array.from(new Uint8Array(await body.arrayBuffer()));const r=await window.__local_http(url,{...opts,body});return new Response(r.body,{status:r.status,headers:r.headers});};''')
 page.add_style_tag(content=(STATIC/'methods/methods.css').read_text())
 urls={};loading=set()
 def module_url(path):
  path=Path(path).resolve()
  if path in urls:return urls[path]
  if path in loading:raise RuntimeError('Cyclic JS import in test loader: '+str(path))
  loading.add(path)
  source=path.read_text()
  # Rewrite only literal relative import/export source specifiers.
  pattern=r"(\bfrom\s*|\bimport\s*)['\"](\.{1,2}/[^'\"]+\.js)['\"]"
  def sub(match):
   return match.group(1)+json.dumps(module_url(path.parent/match.group(2)))
  source=re.sub(pattern,sub,source)
  urls[path]=page.evaluate('(src)=>URL.createObjectURL(new Blob([src],{type:"text/javascript"}))',source)
  loading.remove(path)
  return urls[path]
 app_url=module_url(STATIC/'app.js')
 page.add_script_tag(content='import '+json.dumps(app_url)+';',type='module')
 return page

from searchagent_lab.io import import_file
from searchagent_lab.demo import seed_demo
import time

def main():
 checks=[];errors=[];shots=ROOT/'docs/screenshots';shots.mkdir(parents=True,exist_ok=True)
 def check(name,ok):
  if not ok:raise AssertionError(name)
  checks.append({'name':name,'passed':True});print('PASS',name,flush=True)
 with tempfile.TemporaryDirectory() as tmp,Store(Path(tmp)/'real') as s, Store(Path(tmp)/'synthetic') as demo:
  server=LabServer(('127.0.0.1',0),s);demo_server=LabServer(('127.0.0.1',0),demo)
  for x in (server,demo_server):threading.Thread(target=x.serve_forever,daemon=True).start()
  base=f'http://127.0.0.1:{server.server_address[1]}'
  try:
   with sync_playwright() as p:
    b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=launch_page(b,base,errors=errors)
    page.wait_for_selector('#ix-file-input',state='attached')
    check('empty workspace has direct file open without run/eval registration',page.locator('#ix-open-file').is_visible() and len(s.runs())==0)
    check('two primary pages, no chat input',page.locator('.sidebar nav a').count()==2 and page.locator('textarea').count()==0)
    page.set_input_files('#ix-file-input',str(ROOT/'examples/inspection_demo.jsonl'))
    page.wait_for_selector('.ix-fold');page.wait_for_timeout(200)
    check('binary upload produces six records, invalid retained',page.locator('.ix-record-row').count()==6 and server.inspections.files()[0]['invalid_count']==1)
    check('physical source line numbers include gap',page.locator('.ix-row-meta').nth(2).inner_text().startswith('LINE 4'))
    check('synthetic results explicitly marked','合成样例' in page.locator('.ix-record-heading').inner_text())
    page.locator('[data-step-pointer="/steps/1"] > summary').click()
    page.wait_for_selector('[data-json-pointer="/steps/1/tool_response"]')
    page.locator('[data-json-pointer="/steps/1/tool_response"] > summary').click()
    page.wait_for_selector('[data-json-pointer="/steps/1/tool_response/documents"]')
    check('nested object can be independently expanded',page.locator('[data-json-pointer="/steps/1/tool_response"]').get_attribute('open') is not None)
    page.locator('#ix-collapse').click();page.wait_for_timeout(100)
    check('all trajectory and JSON folds close',page.locator('#ix-tree details[open]').count()==0)
    page.locator('#ix-expand').click();page.wait_for_timeout(100)
    check('expand steps restores all six step headers',page.locator('#ix-tree .ix-step[open]').count()==6)
    page.locator('[aria-label="下一步"]').click();page.wait_for_timeout(100)
    check('saved step replay navigates without model execution','/steps/1' in page.locator('.ix-path-bar').inner_text())
    page.locator('#ix-collapse').click()
    page.fill('#ix-record-search','hidden_signal_8472');page.press('#ix-record-search','Enter');page.wait_for_selector('#ix-tree mark')
    check('search traverses collapsed fields and highlights original value',page.locator('#ix-tree mark').inner_text()=='hidden_signal_8472')
    check('search opens exact tool result pointer','/steps/1/tool_response/documents/0/text' in page.locator('.ix-path-bar').inner_text())
    page.wait_for_timeout(3400);page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(shots/'01_jsonl_search_and_steps.png'),full_page=True)
    # Lazy array children and special JSON-pointer escaping.
    page.fill('#ix-record-search','array_tail_317');page.press('#ix-record-search','Enter');page.wait_for_selector('#ix-tree mark:has-text("array_tail_317")')
    check('search reveals array item beyond first 80 children','/317/value' in page.locator('.ix-path-bar').inner_text())
    check('unknown custom fields accessible in raw tree','selected' in page.locator('#ix-tab-json').get_attribute('class'))
    page.fill('#ix-record-search','special_pointer_953');page.press('#ix-record-search','Enter');page.wait_for_selector('#ix-tree mark:has-text("special_pointer_953")')
    check('slash and tilde keys resolve correctly','/custom_metadata/a~1b/tilde~0key' in page.locator('.ix-path-bar').inner_text())
    page.fill('#ix-record-search','<img');page.press('#ix-record-search','Enter');page.wait_for_selector('#ix-tree mark')
    check('model/log markup never executes or becomes DOM',page.locator('#ix-tree img, #ix-tree script').count()==0)
    # Candidate evidence, native folding, review write, and refresh persistence.
    page.locator('#ix-tab-trace').click();page.locator('#ix-diagnosis .ix-filter-button').filter(has_text='候选').click()
    candidate=page.locator('.ix-finding').filter(has_text='答案词面在完整返回中')
    if candidate.get_attribute('open') is None:candidate.locator('summary').click()
    candidate.locator('.ix-evidence-button').first.click()
    check('diagnostic evidence jumps to actual tool step',page.locator('[data-step-pointer="/steps/1"]').get_attribute('open') is not None)
    check('evidence jump keeps finding bound in review form','绑定规则' in page.locator('.ix-review-form .ix-small').inner_text())
    page.locator('.ix-diagnosis').evaluate('(e)=>e.scrollTop=0');page.wait_for_timeout(500);page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(shots/'02_diagnostic_evidence.png'),full_page=True)
    page.locator('[aria-label="核验状态"]').select_option('confirmed')
    page.fill('[aria-label="人工复核说明"]','Browser verification: saved candidate, checked raw/visible discrepancy. Not a causal proof.')
    page.locator('.ix-review-form button[type="submit"]').click();page.wait_for_selector('.ix-review-history .ix-review')
    check('review persisted with exact source pointer',len(server.inspections.record(server.inspections.files()[0]['id'],1)['reviews'])==1)
    check('review did not overwrite imported EM',server.inspections.record(server.inspections.files()[0]['id'],1)['raw']['scores']['em']==0)
    page.locator('.ix-review-form').scroll_into_view_if_needed();page.wait_for_timeout(200);page.screenshot(path=str(shots/'03_manual_review.png'),full_page=True)
    page.locator('#refresh-button').click();page.wait_for_selector('.ix-review-history .ix-review')
    check('review survives full application refresh','Browser verification' in page.locator('.ix-review-history').inner_text())
    # Archive filters search all raw content, not just titles or rendered DOM.
    page.fill('#ix-list-search','array_tail_317');page.wait_for_function("document.querySelectorAll('.ix-record-row').length===1")
    check('whole-file search finds hidden field',page.locator('.ix-record-row').count()==1)
    page.fill('#ix-list-search','');page.wait_for_function("document.querySelectorAll('.ix-record-row').length===6")
    page.select_option('#ix-rule-filter','parser_mismatch');page.wait_for_function("document.querySelectorAll('.ix-record-row').length===1")
    check('diagnostic category filters record list',page.locator('.ix-record-row').count()==1)
    page.select_option('#ix-rule-filter','');page.wait_for_function("document.querySelectorAll('.ix-record-row').length===6")
    page.locator('[data-record="2"]').click();page.wait_for_function("document.querySelector('.ix-record-heading').textContent.includes('Solver and verifier')")
    page.locator('#ix-tab-trace').click()
    check('RLLM episode exposes independent solver and verifier',page.locator('#ix-tree').inner_text().count('Trajectory ')==2)
    page.locator('#ix-expand').click();page.wait_for_selector('[data-json-pointer="/trajectories/0/steps/0/thought"]')
    check('explicit thought and role steps retained',page.locator('[data-json-pointer="/trajectories/0/steps/0/thought"]').count()==1)
    page.locator('[data-record="3"]').click();page.wait_for_function("document.querySelector('.ix-record-heading').textContent.includes('message log')")
    page.locator('#ix-tab-trace').click();page.locator('#ix-expand').click()
    check('messages display without invented per-round model input',page.locator('#ix-tree .ix-step').count()==4 and '不推断每轮完整模型输入' in page.locator('.ix-record-heading').inner_text())
    page.locator('[data-record="5"]').click();page.wait_for_function("document.querySelector('.ix-record-heading').textContent.includes('Reference only')")
    check('reference-only record is not falsely diagnosed as a model failure','参考材料，不是模型轨迹' in page.locator('#ix-diagnosis').inner_text())
    page.locator('[data-record="6"]').click();page.wait_for_selector('.ix-invalid-line')
    check('invalid JSON line remains readable','{"broken":' in page.locator('.ix-invalid-line').inner_text())
    page.select_option('#ix-outcome','invalid');page.wait_for_function("document.querySelectorAll('.ix-record-row').length===1")
    check('invalid line can be filtered and revisited',page.locator('.ix-record-row').count()==1)
    page.select_option('#ix-outcome','');page.wait_for_function("document.querySelectorAll('.ix-record-row').length===6")
    page.set_input_files('#ix-file-input',str(ROOT/'examples/inspection_demo.jsonl'));page.wait_for_timeout(400)
    check('reopening identical file does not duplicate archive',len(server.inspections.files())==1)
    file_id=server.inspections.files()[0]['id'];report=server.inspections.report(server.inspections.record(file_id,1))
    check('export includes raw evidence, versioned diagnostics and reviews',report['raw']['scores']['em']==0 and len(report['reviews'])==1 and report['diagnostics']['ruleset']=='trace-evidence-v1')
    check('original JSONL export byte identical',server.inspections.original(file_id)==(ROOT/'examples/inspection_demo.jsonl').read_bytes())
    # The uploaded real eleven-question manifest remains separate and unchanged.
    import_file(s,ROOT.parent/'eval-data/reference-assets/validation_panel_v1/searchagentlab_projects.json')
    original=json.loads((ROOT.parent/'eval-data/reference-assets/validation_panel_v1/searchagentlab_panel_v1.json').read_text());s.register_panel(original)
    page.evaluate("location.hash='#/projects'");page.wait_for_selector('[data-project-card]')
    check('three isolated project entries still available',page.locator('[data-project-card]').count()==3)
    check('all eleven supplied GT samples kept verbatim',s.panel(original['id'])['samples']==original['samples'])
    page.evaluate("location.hash='#/questions?panel=search-validation-fixed-v1'");page.wait_for_selector('#groundtruth-list')
    check('groundtruth-first still usable without checkpoint',page.locator('#groundtruth-list > *').count()==11)
    page.screenshot(path=str(shots/'04_existing_groundtruth.png'),full_page=True)
    page.locator('#nav-trace').click();page.wait_for_selector('#ix-file-input',state='attached')
    check('real sidebar opens JSONL inspector rather than trapping user in GT page',page.locator('#ix-open-file').is_visible())
    check('inspection did not create any training run or formal score',s.runs()==[])
    # Existing registered evaluations now open exactly the same hierarchical inspector.
    seed_demo(demo)
    page2=launch_page(b,f'http://127.0.0.1:{demo_server.server_address[1]}','#/trace?evaluation=lt-persistent-s42-step100-eval01',errors)
    page2.wait_for_selector('.ix-fold');page2.wait_for_timeout(200)
    check('existing checkpoint evaluation opens new inspector',page2.locator('.ix-record-row').count()==35 and page2.locator('#ix-tree .ix-step').count()>0)
    page2.locator('#ix-expand').click();page2.wait_for_selector('.ix-json-node')
    check('formal evaluation raw fields can also expand',page2.locator('.ix-json-node').count()>3)
    page2.screenshot(path=str(shots/'05_existing_evaluation.png'),full_page=True)
    page2.evaluate("location.hash='#/overview?project=latent'");page2.wait_for_selector('.results-table');page2.wait_for_selector('.chart-container svg')
    check('training loss/reward and checkpoint overview preserved',page2.locator('.chart-container svg').count()>=2 and page2.locator('.results-table tbody tr').count()==4)
    check('latent experiment list excludes self-play','Self-play' not in page2.locator('#experiment-select').inner_text())
    page2.screenshot(path=str(shots/'06_existing_overview.png'),full_page=True)
    check('no JavaScript page errors',not errors)
    b.close()
  finally:
   for x in (server,demo_server):x.shutdown();x.server_close()
 (ROOT/'docs/browser_test_output.json').write_text(json.dumps({'mode':'in-memory static modules + real local HTTP API bridge','checks':checks,'count':len(checks),'errors':errors},ensure_ascii=False,indent=2))
 print('PASSED',len(checks),'browser checks',flush=True)

if __name__=='__main__':main()
