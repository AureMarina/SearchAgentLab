"""UI acceptance for 0.4. Try real navigation; explicit bridge fallback if blocked."""
import sys,tempfile,threading,json,time,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from searchagent_lab.store import Store
from searchagent_lab.server import LabServer
from playwright.sync_api import sync_playwright
from browser_inspection import launch_page
sys.path.insert(0,str(ROOT/'examples'))
from seed_verification_demo import seed


def main():
 checks=[];errors=[];mode='native';shots=ROOT/'docs/screenshots';shots.mkdir(exist_ok=True,parents=True)
 def check(name,ok):
  if not ok:raise AssertionError(name)
  checks.append({'name':name,'passed':True});print('PASS',name,flush=True)
 with tempfile.TemporaryDirectory() as tmp:
  f=seed(tmp)
  with Store(tmp) as store:
   server=LabServer(('127.0.0.1',0),store);threading.Thread(target=server.serve_forever,daemon=True).start()
   base='http://127.0.0.1:'+str(server.server_address[1]);doc=server.inspections.record(f['id'],1)
   try:
    with sync_playwright() as p:
     browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
     page=browser.new_page(viewport={'width':1800,'height':1150});page.set_default_timeout(12000)
     try:
      page.goto(base+'/#/trace?file='+f['id']+'&record=1',timeout=6000)
      page.wait_for_selector('.ix-fold',timeout=6000)
      page.on('pageerror',lambda e:errors.append(str(e)))
     except Exception as e:
      print('Native navigation unavailable:',str(e)[:180],flush=True);mode='static_module_real_HTTP_bridge';page.close()
      page=launch_page(browser,base,'#/trace?file='+f['id']+'&record=1',errors)
     page.wait_for_selector('.ix-fold');page.wait_for_timeout(400)
     check('four diagnostic tabs plus contextual method evidence tab are visible',page.locator('.lp-workflow-tab').count()==5)
     page.locator('#lp-tab-checks').click();page.wait_for_selector('.lp-layer-cards')
     check('four layers rendered independently',page.locator('.lp-layer').count()==4)
     check('reliability is insufficient for single trace','证据不足' in page.locator('.lp-layer').nth(3).inner_text())
     check('retrospective contract is labelled','历史回填' in page.locator('#lp-body').inner_text())
     check('no production gate applied','不发布、不拦截生产' in page.locator('#lp-body').inner_text())
     page.wait_for_timeout(300);page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(3400);page.screenshot(path=str(shots/'07_layered_scorecard.png'),full_page=True,timeout=15000,animations='disabled')
     link=page.locator('#lp-body .ix-evidence-button').first;target=link.inner_text().replace(' ↗','');link.click();page.wait_for_timeout(200)
     check('layer check evidence returns to trace tree','selected' in page.locator('#lp-tab-trace').get_attribute('class'))
     check('evidence jump selects exact original pointer',target in page.locator('.ix-path-bar').inner_text())
     # Contract UI: an unchanged version can be rebound, conflicting version rejected.
     page.locator('#lp-tab-checks').click();page.get_by_role('button',name='查看 / 新版本契约',exact=True).click();page.wait_for_selector('.lp-dialog')
     c=json.loads(page.get_by_label('契约 JSON',exact=True).input_value());c['notes']='browser test new version';page.get_by_label('契约 JSON',exact=True).fill(json.dumps(c))
     page.locator('.lp-dialog button[type=submit]').click();page.wait_for_function("document.querySelector('.lp-error').textContent.includes('Immutable')")
     check('UI rejects silent contract-version overwrite','Immutable' in page.locator('.lp-error').inner_text())
     c['version']='v2';page.get_by_label('契约 JSON',exact=True).fill(json.dumps(c));page.locator('.lp-dialog button[type=submit]').click();page.wait_for_selector('.lp-dialog',state='detached')
     check('new contract version preserved',len(server.loop.workspace(doc)['contract_history'])==2)
     # A rule candidate must remain unverified until a separate experiment/review.
     page.locator('#lp-tab-hypotheses').click();page.wait_for_selector('[data-hypothesis]')
     check('alternative explanation and minimal experiment shown','替代解释' in page.locator('#lp-body').inner_text() and '最小验证' in page.locator('#lp-body').inner_text())
     page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(3400);page.screenshot(path=str(shots/'08_hypothesis_and_evidence.png'),full_page=True,timeout=15000,animations='disabled')
     page.locator('#lp-tab-trace').click();page.locator('#ix-diagnosis .ix-filter-button').filter(has_text='候选').click()
     candidate=page.locator('.ix-finding').filter(has_text='答案标签与记录的解析答案不同')
     if candidate.get_attribute('open') is None:candidate.locator('summary').click()
     candidate.get_by_role('button',name='加入归因候选',exact=True).click();page.wait_for_function("document.querySelectorAll('[data-hypothesis]').length===2")
     check('one-click rule produces unverified hypothesis',server.loop.objects(doc,'hypothesis')[-1]['state']=='unverified')
     hc=page.locator('[data-hypothesis]').last;hc.get_by_role('button',name='建立验证任务',exact=True).click();page.wait_for_selector('.lp-dialog')
     page.get_by_label('任务名称',exact=True).fill('UI 实测：固定答案原文重放解析器')
     page.get_by_label('支持或反驳假设时，应看到什么',exact=True).fill('只比较解析输出；不宣称提升 EM。')
     page.locator('.lp-dialog button[type=submit]').click();page.wait_for_selector('.lp-dialog',state='detached');page.wait_for_function("document.querySelectorAll('[data-task]').length===3")
     t=server.loop.objects(doc,'task')[-1];check('UI creates a planned task without executing',t['state']=='planned' and not any(r['task_id']==t['id'] for r in server.loop.objects(doc,'result')))
     taskcard=page.locator('[data-task="'+t['id']+'"]');taskcard.get_by_role('button',name='运行本地组件重放',exact=True).click();page.wait_for_function("document.querySelectorAll('.lp-result').length===3")
     r=server.loop.objects(doc,'result')[-1];check('component executes from fixed source',r['candidate']['parsed']=='Copper Harbor' and r['input_hash'])
     check('component result not model uplift',r['autonomous_uplift'] is None)
     page.locator('#lp-body').evaluate('(e)=>e.scrollTop=0');page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(3400);page.screenshot(path=str(shots/'09_component_replay.png'),full_page=True,timeout=15000,animations='disabled')
     # Regression is not a success-only report: both repair and regression visible.
     reg=page.locator('[data-task]').nth(1);reg.scroll_into_view_if_needed();page.wait_for_timeout(200)
     check('regression preserves wrong-to-right and right-to-wrong','错 → 对' in reg.inner_text() and '对 → 错' in reg.inner_text())
     check('synthetic import is visibly not real execution','合成机制示例' in reg.inner_text())
     page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(3400);page.screenshot(path=str(shots/'10_repair_and_regression.png'),full_page=True,timeout=15000,animations='disabled')
     # Register a fix, no executable patch is accepted or deployed.
     taskcard.get_by_role('button',name='登记修改 / 接受 / 拒绝',exact=True).click();page.wait_for_selector('.lp-dialog')
     page.get_by_label('修复决定',exact=True).select_option('accept_for_further_evaluation')
     page.get_by_label('修改文件 / commit / patch 引用',exact=True).fill('synthetic://parser-fixture')
     page.get_by_label('对应结果',exact=True).select_option(r['id'])
     page.get_by_label('修复、退化与决定依据',exact=True).fill('仅在本地机制测试中接受；仍需真实从头回归。')
     page.locator('.lp-dialog button[type=submit]').click();page.wait_for_selector('.lp-dialog',state='detached')
     event=server.loop.events(t['id'])[-1]
     check('fix stores decision without applying patch',not event['data']['applied'] and not event['data']['published'])
     # Judge export is an evidence exchange, not a silent external API call.
     page.locator('#lp-tab-hypotheses').click();page.get_by_role('button',name='导出前缀 / 事后诊断证据包',exact=True).click();page.wait_for_selector('.lp-dialog')
     page.get_by_label('截止步骤 JSON pointer',exact=True).fill('/steps/0');page.locator('.lp-dialog button[type=submit]').click();page.wait_for_selector('.lp-dialog',state='detached')
     packet=server.loop.objects(doc,'judge_packet')[-1]
     check('prefix packet excludes gold, score and future',not any(x['pointer'].startswith(('/gold','/scores','/steps/1')) for x in packet['evidence']))
     page.get_by_role('button',name='导入 Judge 结果',exact=True).last.click();page.wait_for_selector('.lp-dialog')
     judge={'packet_hash':packet['packet_hash'],'judge':{'model':'synthetic-judge','revision':'fixture','prompt_version':packet['prompt_version']},'judgements':[{'dimension':'evidence_support','verdict':'insufficient','explanation':'仅第一步前缀，没有执行后证据。','evidence':[],'alternatives':['需要检索返回后再判断。'],'next_check':'检查后续可见证据。'}]}
     page.get_by_label('JSON 内容',exact=True).fill(json.dumps(judge,ensure_ascii=False));page.locator('.lp-dialog button[type=submit]').click();page.wait_for_selector('.lp-dialog',state='detached')
     check('external Judge abstention imported without rescore',len(server.loop.objects(doc,'judge_result'))==1 and server.inspections.record(f['id'],1)['raw']['scores']['em']==0)
     # Source separation and reload are intentional.
     old_count=len(server.loop.objects(doc,'hypothesis'))
     page.locator('[data-record="2"]').click();page.wait_for_function("document.querySelector('.ix-record-heading').textContent.includes('schema')")
     page.locator('#lp-tab-hypotheses').click();page.wait_for_timeout(300)
     check('different case does not inherit another case hypotheses',page.locator('[data-hypothesis]').count()==0)
     page.locator('[data-record="1"]').click();page.wait_for_function("document.querySelectorAll('[data-hypothesis]').length===2")
     check('returning to case preserves its workspace',len(server.loop.objects(doc,'hypothesis'))==old_count)
     page.locator('#refresh-button').click();page.wait_for_selector('.ix-fold');page.locator('#lp-tab-hypotheses').click();page.wait_for_selector('[data-hypothesis]')
     check('case state survives application refresh',page.locator('[data-hypothesis]').count()==2)
     check('no additional top-level dashboard or chat',page.locator('.sidebar nav a').count()==2)
     check('all original input bytes unchanged',server.inspections.original(f['id'])==(ROOT/'examples/verification_demo.jsonl').read_bytes())
     check('formal training/evaluation tables unchanged',store.runs()==[] and store.health()['evaluations']==0)
     check('no JavaScript errors',errors==[])
     browser.close()
   finally:server.shutdown();server.server_close()
 (ROOT/'docs/browser_workflow_output.json').write_text(json.dumps({'mode':mode,'checks':checks,'page_errors':errors},ensure_ascii=False,indent=2))
 print('PASSED',len(checks),'new browser checks; mode='+mode)
if __name__=='__main__':main()
