"""Exercise experiment-owned panes against real HTTP APIs (no GPU/model runs)."""
import sys,json,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'examples')]
from bridge_studio import load
from seed_studio_demo import seed
from test_method_data import definition,ARCHIVE
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.server import LabServer
from playwright.sync_api import sync_playwright


def main():
 checks=[];errors=[];shots=ROOT/'docs/v081/screenshots';shots.mkdir(parents=True,exist_ok=True)
 def check(name,condition):
  checks.append({'name':name,'passed':bool(condition)})
  print(('PASS ' if condition else 'FAIL ')+name,flush=True)
  if not condition:raise AssertionError(name)
 with tempfile.TemporaryDirectory() as tmp:
  seed(tmp)
  with StudioRecorder(tmp) as r:
   r.store.register_project({'id':'embedded-skill','name':'Skill · 页面验收','demo':True})
   r.store.register_experiment({'id':'embedded-e','project_id':'embedded-skill','name':'SkillRL · 技能归档查看','objective':'界面验收：使用上传的真实技能文件。实验与 run 为接入测试，不是训练结果。','demo':True})
   r.repo.register_profile({'experiment_id':'embedded-e','track':'reproduction','reproduction':{'method_name':'SkillRL 文件查看（不是复现实测）'},'question':'在同一个实验页面里阅读技能内容与已保存的版本历史。'})
   # Insert skill last so the list's latest run is the archive viewer.
   for rid,name in [('embedded-b','Entropy 接入测试'),('embedded-a','技能归档接入测试')]:
    r.store.register_run({'id':rid,'experiment_id':'embedded-e','name':name,'base_model':'未登记（文件查看）','demo':True})
  with MethodRecorder(tmp) as r:
   archive=r.import_file(ARCHIVE,{'run_id':'embedded-a'})['archive_id']
   r.entropy_definition('embed-h',definition(),{'run_id':'embedded-b'})
   for i,c in enumerate(['training_on_policy','fixed_prefix_probe']):r.entropy_observation('embed-o'+str(i),{'definition_id':'embed-h','value':1+i/4,'context':c,'prefix_hash':'a'*64,'step':1,'token_count':12,'sample_count':1},{'run_id':'embedded-b'})
   with LabServer(('127.0.0.1',0),r.store) as server:
    th=threading.Thread(target=server.serve_forever,daemon=True);th.start();base='http://127.0.0.1:'+str(server.server_address[1]);native_error='';mode='native';page=None
    try:
     with sync_playwright() as p:
      browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
      page=browser.new_page(viewport={'width':1680,'height':1120});page.on('pageerror',lambda e:errors.append(str(e)))
      try:page.goto(base+'/#/experiments?project=embedded-skill&track=reproduction',timeout=5000);page.wait_for_selector('#s-open',timeout=2500)
      except Exception as e:
       native_error=str(e).splitlines()[0];page.close();mode='static_modules_real_HTTP_bridge';page=load(browser,base,'#/experiments?project=embedded-skill&track=reproduction',errors)
      page.wait_for_selector('a.ex-name');page.evaluate("() => {window.calls=[];const oldFetch=window.fetch;window.fetch=(url,opts)=>{window.calls.push(String(url));return oldFetch(url,opts);};}")
      page.get_by_role('link',name='实验详情',exact=True).click();page.wait_for_selector('#md-detail h2',timeout=20000)
      check('experiment title, not method workspace heading',page.locator('h1').inner_text()=='SkillRL · 技能归档查看')
      check('specific content embedded in experiment URL',page.evaluate("location.hash.startsWith('#/experiment?') && location.hash.includes('run=embedded-a')"))
      check('sidebar global metadata entry removed',page.locator('#s-methods').count()==0)
      check('only training and Skill tabs visible',page.locator('.ex-tab').all_text_contents()==['训练与评测','技能内容与生命周期'])
      check('Skill tab active and keyboard accessible',page.locator('[data-view="skill"]').get_attribute('aria-selected')=='true')
      check('archive default still frozen step100', '冻结评测 · step 100' in page.locator('.md-selected-bar').inner_text())
      check('all 23 snapshots remain inline',page.locator('.md-version').count()==23)
      check('Skill and lesson counts retained', '89 条技能' in page.locator('.md-selected-bar').inner_text() and '11 条经验教训' in page.locator('.md-selected-bar').inner_text())
      check('full principle and lifecycle on same page','原则 / Principle' in page.locator('#md-detail').inner_text() and '此技能的已保存历史' in page.locator('#md-detail').inner_text())
      check('no unrelated endpoint or training matrix on initial Skill view',not any('/studio/dynamics?' in u or '/methods/entropy?' in u or '/methods/cycle?' in u for u in page.evaluate('window.calls')))
      check('paged skill body, not all snapshot bodies',page.locator('.md-member').count()==25)
      check('test run marked synthetic; archive is not a performance claim',page.locator('#s-demo').is_visible())
      page.screenshot(path=str(shots/'01_skill_inside_experiment.png'),full_page=True)
      page.locator('.md-version').filter(has_text='训练 · step 105').click();page.wait_for_function("document.querySelector('.md-selected-bar').textContent.includes('92 条技能')")
      page.fill('#md-search','dyn_060');page.press('#md-search','Enter');page.wait_for_function("document.querySelectorAll('.md-member').length===1")
      page.locator('.md-member').click();page.wait_for_function("document.querySelector('#md-detail').textContent.includes('首次观测：step 105')")
      check('history retains late skill absent in frozen100','冻结评测库不含此技能' in page.locator('#md-detail').inner_text())
      check('version and member selection do not leave experiment',page.evaluate("location.hash.startsWith('#/experiment?')&&location.hash.includes('member=')"))
      page.get_by_role('button',name='版本内容差异',exact=True).click();page.wait_for_selector('#s-modal[open] .md-code')
      check('inline skill content difference remains functional','High-Certainty Memory First' in page.locator('#s-modal .md-code').inner_text());page.locator('#s-modal-close').click()
      page.screenshot(path=str(shots/'02_skill_lifecycle_inside_experiment.png'),full_page=True)
      page.locator('[data-view="training"]').click();page.wait_for_selector('.s-chart')
      check('switch to training without losing experiment header',page.locator('h1').inner_text()=='SkillRL · 技能归档查看' and page.locator('.s-chart').count()==3)
      check('no duplicate run selectors in embedded training',page.get_by_label('训练运行',exact=True).count()==1)
      page.locator('[data-view="skill"]').click();page.wait_for_function("document.querySelector('#md-detail')?.textContent.includes('首次观测：step 105')")
      check('same-run tab switch retains selected skill and version','训练 · step 105' in page.locator('.md-selected-bar').inner_text())
      page.get_by_label('训练运行',exact=True).select_option('embedded-b');page.wait_for_selector('select[aria-label="熵定义版本"]');page.wait_for_function("document.querySelector('#ex-pane').textContent.includes('2 条已记录观测')")
      check('switching run replaces applicable tabs instead of union',page.locator('.ex-tab').all_text_contents()==['训练与评测','熵定义与观测'])
      check('sibling run never displays prior Skill content',page.locator('.md-version').count()==0 and page.locator('#md-detail').count()==0)
      check('entropy definitions and observations inline','training_on_policy' in page.locator('#ex-pane').inner_text())
      page.get_by_label('熵记录场景').select_option('fixed_prefix_probe');page.wait_for_function("document.querySelector('#ex-pane').textContent.includes('1 条已记录观测')")
      check('entropy context filter kept separate', 'training_on_policy / ' not in page.locator('#ex-pane').inner_text())
      page.screenshot(path=str(shots/'03_entropy_inside_experiment.png'),full_page=True)
      page.get_by_label('训练运行',exact=True).select_option('');page.wait_for_function("document.querySelector('.ex-tabs')?.children.length===1")
      check('unbound experimental view never aggregates sibling runs',page.locator('.ex-tab').all_text_contents()==['训练与评测'] and '实验级产物' in page.locator('#ex-pane').inner_text())
      page.evaluate("location.hash='#/method-data?run=embedded-a&family=latent&project=embedded-skill&track=reproduction'");page.wait_for_selector('#md-detail h2')
      check('old metadata bookmark opens owning experiment, no empty Latent tab',page.locator('h1').inner_text()=='SkillRL · 技能归档查看' and page.locator('.ex-tab').all_text_contents()==['训练与评测','技能内容与生命周期'])
      page.evaluate("location.hash='#/skill-library?archive='+"+json.dumps(archive));page.wait_for_selector('#md-detail h2');page.wait_for_function("location.hash.startsWith('#/experiment?')")
      check('bound old archive bookmark resolves owner',page.get_by_label('训练运行').input_value()=='embedded-a')
      page.evaluate("location.hash='#/experiment?run=studio-run&view=self_play'");page.wait_for_selector('.mx-question-card')
      check('Self-play cycle and generated questions embedded in own experiment',page.locator('h1').inner_text()=='E03 · 交替出题与解题' and 'A 出题' in page.locator('.mx-main').inner_text())
      check('no nested generic method scope or family navigation',page.locator('.mx-heading').count()==0 and page.locator('.mx-scope').count()==0 and page.locator('.mx-tabs').count()==0)
      check('Self-play URL retains correct run and project',page.evaluate("location.hash.startsWith('#/experiment?')&&location.hash.includes('run=studio-run')&&location.hash.includes('project=studio-selfplay')"))
      check('Self-play independent from Skill and Entropy tabs',page.locator('.ex-tab').all_text_contents()==['训练与评测','Self-play 轮次与生成题'])
      page.screenshot(path=str(shots/'04_selfplay_inside_experiment.png'),full_page=True)
      # A generated question source link should open in the same experiment pane.
      links=page.locator('.mx-main a[href*="#/methods?"]')
      if links.count():
       links.first.click();page.wait_for_selector('.mx-artifact-json')
       check('method evidence link opens inline rather than generic workspace',page.evaluate("location.hash.startsWith('#/experiment?')") and page.locator('.mx-heading').count()==0)
       check('artifact details do not reintroduce permanent review form',page.locator('textarea:visible').count()==0)
      page.locator('.mx-main').get_by_role('link',name='原始 JSON / 诊断 ↗',exact=True).click();page.wait_for_selector('.s-replay-meta')
      check('method raw trace carries experiment return context',page.get_by_role('link',name='返回实验',exact=True).count()==1)
      page.get_by_role('link',name='返回实验',exact=True).click();page.wait_for_selector('.mx-artifact-json')
      check('method raw trace returns to same run and method pane',page.locator('[data-view="self_play"]').get_attribute('aria-selected')=='true' and page.get_by_label('训练运行',exact=True).input_value()=='studio-run')
      page.locator('[data-view="training"]').click();page.wait_for_selector('[data-cp="studio-run-cp100"]')
      check('existing training checkpoints and question matrix intact',page.locator('[data-cp]').count()==4 and page.locator('[data-question]').count()==11)
      page.locator('[data-cp="studio-run-cp100"]').get_by_role('button',name='选中',exact=True).click()
      page.locator('.s-selected-col .s-result-link').nth(7).click();page.wait_for_selector('.s-step-item')
      check('training matrix still opens original trajectory','SYN-08' in page.locator('.s-replay-meta').inner_text())
      page.get_by_role('link',name='返回训练 / 报告',exact=True).click();page.wait_for_selector('[data-cp="studio-run-cp100"]')
      check('trajectory return restores experiment and checkpoint',page.locator('[data-cp="studio-run-cp100"]').get_attribute('class')=='selected' and page.evaluate("location.hash.startsWith('#/experiment?')"))
      check('no JavaScript runtime errors',not errors)
      browser.close()
    except Exception:
     if page:
      try:page.screenshot(path=str(shots/'failure.png'),full_page=True);print(page.locator('#s-app').inner_text()[:3000]);print('ERRORS',errors)
      except:pass
     raise
    finally:server.shutdown();th.join()
 report={'mode':mode,'native_error':native_error,'count':len(checks),'checks':checks,'errors':errors,'scope':'Actual application JS + real HTTP backend; fixtures use real uploaded SkillRL file but synthetic experiment/run metadata. No GPU training.'}
 (ROOT/'docs/v081/browser-experiment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('PASSED',len(checks),mode)

if __name__=='__main__':main()
