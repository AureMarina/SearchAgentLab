"""SkillRL native/bridged browser acceptance; no model training is executed."""
import sys,json,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from bridge_studio import load
from test_method_data import definition,ARCHIVE
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.server import LabServer
from playwright.sync_api import sync_playwright

def main():
 checks=[];errors=[];shots=ROOT/'docs/v081/screenshots/standalone';shots.mkdir(parents=True,exist_ok=True)
 def check(name,condition):
  if not condition:raise AssertionError(name)
  checks.append({'name':name,'passed':True});print('PASS',name,flush=True)
 with tempfile.TemporaryDirectory() as tmp:
  with StudioRecorder(tmp) as r:
   r.store.register_project({'id':'md-test','name':'Synthetic Metadata Test'})
   r.store.register_experiment({'id':'md-e','project_id':'md-test','name':'Metadata transport test'})
   r.repo.register_profile({'experiment_id':'md-e','track':'reproduction','reproduction':{'method_name':'Synthetic test'}})
   r.store.register_run({'id':'md-run','experiment_id':'md-e','name':'Synthetic run','demo':True})
   scope={'run_id':'md-run'}
   r.methods.import_bundle({'method_schema':'method-evidence-v1','objects':[{'id':'md-profile','kind':'method_profile','demo':True,'data':{'method_name':'Synthetic entropy','families':['entropy'],'configuration':{'synthetic':True}}}]},scope=scope)
  with MethodRecorder(tmp) as r:
   r.entropy_definition('md-h',definition(),{'run_id':'md-run'})
   for i,c in enumerate(['training_on_policy','fixed_prefix_probe']):r.entropy_observation('md-o'+str(i),{'definition_id':'md-h','value':1+i/4,'context':c,'prefix_hash':'a'*64,'step':1,'token_count':12,'sample_count':1},{'run_id':'md-run'})
   r.entropy_definition('md-h2',{**definition(),'name':'Synthetic second definition','unit':'bits'},{'run_id':'md-run'})
   with LabServer(('127.0.0.1',0),r.store) as server:
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_address[1]);native_error=''
    try:
     with sync_playwright() as p:
      browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
      page=browser.new_page(viewport={'width':1680,'height':1120});page.on('pageerror',lambda e:errors.append(str(e)))
      mode='native'
      try:page.goto(base+'/#/method-data?standalone=1',timeout=5000);page.wait_for_selector('#s-open',timeout=2500)
      except Exception as e:
       native_error=str(e).splitlines()[0];page.close();mode='static_modules_real_HTTP_bridge';page=load(browser,base,'#/method-data?standalone=1',errors)
      page.wait_for_selector('#s-open');page.wait_for_timeout(100)
      start=time.perf_counter();page.set_input_files('#s-file',str(ARCHIVE));page.wait_for_selector('#md-detail h2',timeout=20000);import_to_detail=(time.perf_counter()-start)*1000
      check('ZIP opens the actual skill lifecycle page',page.locator('h1').inner_text()=='Skill · 内容与生命周期')
      check('standalone evidence does not inherit a synthetic project', '独立证据' in page.locator('#s-crumb').inner_text() and page.locator('#s-project').input_value()=='')
      check('all 23 saved snapshots are visible',page.locator('.md-version').count()==23)
      check('default is frozen step100, not training105','冻结评测 · step 100' in page.locator('.md-selected-bar').inner_text())
      check('89 skills and 11 lessons counted separately','89 条技能' in page.locator('.md-selected-bar').inner_text() and '11 条经验教训' in page.locator('.md-selected-bar').inner_text())
      check('initial-to-latest total is 30 to 92','30 → 92' in page.locator('.md-stat-grid').inner_text())
      check('skill lists are paged at 25 records',page.locator('.md-member').count()==25)
      check('full principle and applicability are readable','原则 / Principle' in page.locator('#md-detail').inner_text() and '适用条件' in page.locator('#md-detail').inner_text())
      check('no permanent manual review textarea',page.locator('textarea:visible').count()==0)
      page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(100);page.screenshot(path=str(shots/'01_skill_archive_overview.png'),full_page=True)
      page.locator('#md-members').get_by_role('button',name='下一页',exact=True).click();page.wait_for_function("document.querySelector('#md-members').textContent.includes('26–50')")
      check('next page retrieves the next 25 members',page.locator('.md-member').count()==25)
      page.locator('.md-version').filter(has_text='训练 · step 105').click();page.wait_for_function("document.querySelector('.md-selected-bar').textContent.includes('92 条技能')")
      page.fill('#md-search','dyn_060');page.press('#md-search','Enter');page.wait_for_function("document.querySelectorAll('.md-member').length===1")
      page.locator('.md-member').click();page.wait_for_function("document.querySelector('#md-detail').textContent.includes('High-Certainty Memory First')")
      check('search finds skills outside initially loaded page','dyn_060' in page.locator('.md-member').inner_text())
      check('lifecycle first observed at 105, frozen100 absent','首次观测：step 105' in page.locator('#md-detail').inner_text() and '冻结评测库不含此技能' in page.locator('#md-detail').inner_text())
      page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(100);page.screenshot(path=str(shots/'02_skill_lifecycle_dyn060.png'),full_page=True)
      page.get_by_role('button',name='版本内容差异',exact=True).click();page.wait_for_selector('#s-modal[open] .md-code');check('single-skill diff shows added text','High-Certainty Memory First' in page.locator('#s-modal .md-code').inner_text());page.locator('#s-modal-close').click()
      page.get_by_role('button',name='查询已登记使用记录',exact=True).click();page.wait_for_function("document.querySelector('#s-modal').textContent.includes('不是“使用次数为 0”')")
      check('missing usage is not reported as zero use or success','不能计算技能成功率' in page.locator('#s-modal').inner_text());page.locator('#s-modal-close').click()
      page.evaluate("location.hash='#/method-data?run=md-run&project=md-test&track=reproduction&family=entropy'")
      page.wait_for_selector('select[aria-label="熵定义版本"]');page.wait_for_function("document.querySelector('#s-app').textContent.includes('2 条已记录观测')")
      check('synthetic entropy records labeled as synthetic',page.locator('#s-demo').is_visible())
      check('training and fixed prefix context are explicit','training_on_policy' in page.locator('#s-app').inner_text() and 'fixed_prefix_probe' in page.locator('#s-app').inner_text())
      page.get_by_label('熵记录场景').select_option('fixed_prefix_probe');page.wait_for_function("document.querySelector('#s-app').textContent.includes('1 条已记录观测')")
      check('context filter does not mix on-policy and fixed-prefix probes','training_on_policy / ' not in page.locator('#s-app').inner_text())
      page.get_by_label('熵定义版本').select_option('md-h2');page.wait_for_function("document.querySelector('#s-app').textContent.includes('0 条已记录观测')")
      check('definitions are never merged into one observation set','尚无观测数据' in page.locator('#s-app').inner_text())
      check('no JavaScript runtime errors',not errors)
      browser.close()
    finally:server.shutdown();thread.join()
 report={'mode':mode,'native_error':native_error,'count':len(checks),'checks':checks,'errors':errors,'import_to_first_detail_ms':import_to_detail,'timing_scope':'local browser acceptance, includes ZIP import; not user-server latency'}
 (ROOT/'docs/v081/browser-method-data.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('PASSED',len(checks),mode)
if __name__=='__main__':main()
