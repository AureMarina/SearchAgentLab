"""Current browser acceptance. Actual JS + real HTTP; only fixtures are synthetic."""
import sys,json,tempfile,threading
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
 checks=[];errors=[];shots=ROOT/'docs/v090/screenshots';shots.mkdir(parents=True,exist_ok=True)
 def check(name,yes):
  if not yes:raise AssertionError(name)
  checks.append(name);print('PASS',name,flush=True)
 with tempfile.TemporaryDirectory() as tmp:
  seed(tmp)
  with StudioRecorder(tmp) as r:
   r.store.register_project({'id':'skill-demo','name':'Skill · 文件查看','demo':True})
   r.store.register_experiment({'id':'skill-e','project_id':'skill-demo','name':'SkillRL · 已保存技能','demo':True})
   r.repo.register_profile({'experiment_id':'skill-e','track':'reproduction','question':'技能文件查看；不是训练实测。','reproduction':{'method_name':'SkillRL 文件查看'}})
   for rid in ['skill-run','entropy-run']:r.store.register_run({'id':rid,'experiment_id':'skill-e','name':rid,'base_model':'未登记（文件查看）','demo':True})
  with MethodRecorder(tmp) as r:
   r.import_file(ARCHIVE,{'run_id':'skill-run'});r.entropy_definition('h',definition(),{'run_id':'entropy-run'})
  with StudioRecorder(tmp) as rec:
   server=LabServer(('127.0.0.1',0),rec.store);threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_address[1]);mode='native';native_error=''
   try:
    with sync_playwright() as p:
     browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
     page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
     try:page.goto(base+'/#/experiments?project=studio-selfplay&track=main',timeout=5000);page.wait_for_selector('#s-app h1',timeout=5000)
     except Exception as e:native_error=str(e).splitlines()[0];page.close();mode='static_modules_real_HTTP_bridge';page=load(browser,base,'#/experiments?project=studio-selfplay&track=main',errors)
     page.set_viewport_size({'width':1440,'height':1000})
     def go(hash,selector):page.evaluate('(h)=>location.hash=h',hash);page.wait_for_selector(selector)
     def shot(name):
      print('SHOT',name,flush=True);page.screenshot(path=str(shots/name),full_page=name!='07_mobile.png',timeout=15000,animations='disabled')
     page.wait_for_selector('h1');check('three primary routes',page.locator('#s-nav a').count()==3)
     check('real Lucide icons present',page.locator('#s-nav svg').count()==3)
     check('main excludes reproduction','Dr.Zero' not in page.locator('#s-app').inner_text());shot('01_experiments.png')
     page.evaluate("() => {window.calls=[];const f=window.fetch;window.fetch=(u,o)=>{window.calls.push(String(u));return f(u,o)};}")
     go('#/experiment?project=studio-selfplay&track=main&experiment=studio-e03&run=studio-run','[data-result-sample]')
     check('default result-first tab',page.locator('[data-view="results"]').get_attribute('aria-selected')=='true')
     check('fixed results and macro EM recorded','61.1%' in page.locator('.f-kpis').inner_text() and page.locator('[data-result-sample]').count()==11)
     check('no training matrix on default view',not any('/studio/dynamics?' in x for x in page.evaluate('window.calls')))
     check('only relevant Self-play method tab',page.locator('.ex-tab').all_text_contents()==['结果与轨迹','训练动态','Self-play 轮次与生成题'])
     page.get_by_label('实验当前评测 B',exact=True).select_option('studio-run-eval100');page.wait_for_function("document.querySelector('.f-results-select select[aria-label=\"实验当前评测 B\"]').value==='studio-run-eval100'")
     page.get_by_label('实验对照评测 A',exact=True).select_option('studio-run-eval50');page.wait_for_selector('[data-result-filter="rescued"]')
     check('saved paired gains and regressions','↑ 2 ↓ 1' in ' '.join(page.locator('.f-kpis').inner_text().split()));shot('02_results.png')
     page.locator('[data-result-filter="rescued"]').click();check('rescue filter exact',page.locator('[data-result-sample]').count()==2)
     page.get_by_role('link',name='查看轨迹 →',exact=True).first.click();page.wait_for_selector('.s-step-item')
     check('correct trace for result','SYN-08' in page.locator('.s-replay-meta').inner_text());check('no permanent manual review form',page.locator('textarea:visible').count()==0)
     check('advanced details collapsed',page.locator('.f-log-fold[open]').count()==0)
     page.locator('[data-step="1"]').click();check('actual search and observation displayed','工具完整返回' in page.locator('.s-replay-body').inner_text());shot('03_trace.png')
     page.fill('#s-trace-search','hidden_signal_8472');page.press('#s-trace-search','Enter');page.wait_for_selector('.s-replay-body mark');check('collapsed evidence search','hidden_signal_8472' in page.locator('.s-replay-body mark').inner_text())
     page.get_by_role('button',name='同题对照',exact=True).click();page.wait_for_selector('.s-trace-compare');check('independent A B trace trees',page.locator('.s-trace-compare .ix-step').count()==10)
     page.get_by_role('link',name='返回实验',exact=True).click();page.wait_for_selector('[data-result-sample]');check('return preserves filter and pair',page.locator('[data-result-sample]').count()==2 and page.get_by_label('实验对照评测 A',exact=True).input_value()=='studio-run-eval50')
     page.locator('[data-view="training"]').click();page.wait_for_selector('.s-chart');check('training charts retained',page.locator('.s-chart').count()==3)
     check('vendored sparkline draws training series',page.locator('.sparkline--line').count()==2)
     check('evaluation has dots but no interpolation',page.locator('.s-chart').nth(2).locator('circle').count()==4 and page.locator('.s-chart').nth(2).locator('path').count()==0)
     check('signed loss preserved','-' in page.locator('.s-chart').first.inner_text());shot('04_training.png')
     page.locator('[data-view="self_play"]').click();page.wait_for_selector('.mx-question-card');check('selfplay remains inside experiment',page.locator('h1').inner_text().startswith('E03'))
     go('#/reports?project=studio-selfplay&track=main','input[aria-label="搜索评测记录"]');page.get_by_label('选择评测 studio-run-eval50',exact=True).check();page.get_by_label('选择评测 studio-run-eval100',exact=True).check();page.get_by_role('link',name='比较 A → B',exact=True).click();page.wait_for_selector('.s-report-cards');check('catalog pair selection works','修复 2 · 退化 1' in page.locator('.s-report-cards').inner_text())
     page.locator('[data-report-filter="harmed"]').click();check('report error transition filter',page.locator('[data-report-sample]').count()==1);shot('05_comparison.png')
     page.get_by_role('link',name='B ↗',exact=True).click();page.wait_for_selector('.s-step-item');page.get_by_role('link',name='返回训练 / 报告',exact=True).click();page.wait_for_selector('[data-report-sample]');check('report return preserves filter',page.locator('[data-report-sample]').count()==1)
     go('#/reports?b=studio-run-eval100&a=studio-repro-run-eval100','.s-report-cards');check('cross-purpose comparison cannot claim improvement','修复 2' not in page.locator('.s-report-cards').inner_text() and page.locator('[data-report-filter="rescued"]').count()==0)
     go('#/experiment?run=skill-run&view=skill','#md-detail h2');check('real skill archive intact',page.locator('.md-version').count()==23 and '89 条技能' in page.locator('.md-selected-bar').inner_text());check('skill embedded not global',page.locator('.ex-tab').all_text_contents()==['结果与轨迹','训练动态','技能内容与生命周期']);shot('06_skill.png')
     page.get_by_label('训练运行',exact=True).select_option('entropy-run');page.wait_for_selector('[data-view="entropy"]');check('switching run removes unrelated skill',page.locator('[data-view="skill"]').count()==0 and page.locator('.md-version').count()==0)
     page.locator('[data-view="entropy"]').click();page.wait_for_selector('select[aria-label="熵定义版本"]');check('entropy definition remains embedded',page.locator('#ex-pane').inner_text().find('尚无')>=0 or page.locator('select[aria-label="熵定义版本"]').count()==1)
     go('#/experiment?run=studio-run&view=results','[data-result-sample]');page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(100)
     check('narrow screen no document overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'));check('mobile menu opens',page.locator('#s-menu').is_visible());page.locator('#s-menu').click();check('mobile sidebar toggles',page.locator('body.s-nav-open').count()==1);page.locator('#s-closemenu').click();check('mobile drawer closes',page.locator('body.s-nav-open').count()==0);shot('07_mobile.png')
     page.set_viewport_size({'width':1440,'height':1000});page.locator('#s-help').click();page.wait_for_selector('#s-modal[open]');page.keyboard.press('Escape');check('keyboard accessible help',not page.locator('#s-modal').is_visible())
     # Rapid route changes must not let a stale method response overwrite results.
     page.evaluate("location.hash='#/experiment?run=skill-run&view=skill';setTimeout(()=>{location.hash='#/experiment?run=studio-run&view=results'},2)")
     page.wait_for_selector('[data-result-sample]');page.wait_for_timeout(250);check('rapid navigation does not leak method state',page.locator('h1').inner_text().startswith('E03') and page.locator('.md-version').count()==0)
     before=page.evaluate('location.hash');page.locator('.s-skip').focus();page.keyboard.press('Enter');check('skip link focuses content without changing route',page.evaluate('document.activeElement.id')=='s-app' and page.evaluate('location.hash')==before);check('zero JS runtime errors',not errors);browser.close()
   finally:server.shutdown();server.server_close()
 report={'mode':mode,'native_navigation_limit':native_error,'count':len(checks),'checks':checks,'javascript_errors':errors,'note':'Actual app modules and real HTTP API; synthetic test runs. Skill file reused only to test its display, not a performance result.'}
 (ROOT/'docs/v090/browser-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('PASSED',len(checks),mode)
if __name__=='__main__':main()
