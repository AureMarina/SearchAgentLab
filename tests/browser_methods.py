"""Method UI acceptance: unmodified static logic + actual HTTP/storage (bridge fallback)."""
import sys,json,tempfile,threading,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'examples')]
from searchagent_lab.store import Store
from searchagent_lab.server import LabServer
from seed_methods_demo import seed
from browser_inspection import launch_page
from playwright.sync_api import sync_playwright

def main():
 checks=[];errors=[];shots=ROOT/'docs/screenshots';shots.mkdir(exist_ok=True)
 def check(name,value):
  if not value:raise AssertionError(name)
  checks.append({'name':name,'passed':True});print('PASS',name,flush=True)
 with tempfile.TemporaryDirectory() as tmp:
  seed(tmp)
  with Store(tmp) as store:
   server=LabServer(('127.0.0.1',0),store);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_address[1]}'
   try:
    with sync_playwright() as p:
     b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
     page=b.new_page(viewport={'width':1760,'height':1150});mode='native'
     try:
      page.goto(base+'/#/methods',timeout=5000);page.wait_for_selector('#mx-open',timeout=5000);page.on('pageerror',lambda e:errors.append(str(e)))
     except Exception as e:
      print('Native navigation unavailable:',str(e)[:160],flush=True);page.close();mode='static_module_real_HTTP_bridge';page=launch_page(b,base,'#/methods',errors)
     page.wait_for_selector('#mx-open')
     check('method files open without project/run registration',page.locator('#mx-project').input_value()=='' and '尚无这类记录' in page.locator('#mx-object-list').inner_text())
     check('two main navigation entries retained',page.locator('.sidebar nav a').count()==2)
     check('no chat composer added',page.locator('textarea').count()==0)
     page.set_input_files('#mx-file-input',str(ROOT/'examples/methods/skillbank_v1.synthetic.json'));page.wait_for_selector('#mx-skill-list .mx-skill-row');page.wait_for_timeout(200)
     check('native SkillRL-shaped JSON opens directly',page.locator('#mx-skill-list .mx-skill-row').count()==4)
     check('direct-open synthetic flag retained','合成示例' in page.locator('.mx-object-heading').inner_text())
     check('raw opened snapshot not attached to research run',page.locator('#mx-project').input_value()=='')
     page.get_by_text('技能来源 / 提炼依据',exact=True).click()
     check('missing provenance explicitly shown','来源未记录' in page.locator('#mx-skill-detail').inner_text())
     page.select_option('#mx-skill-group','query_type_skills');check('Search query_type_skills not discarded',page.locator('#mx-skill-list .mx-skill-row').count()==1)
     page.select_option('#mx-skill-group','common_mistakes');check('common mistakes displayed without root-cause claim','不是当前轨迹已验证的根因' in page.locator('#mx-skill-detail').inner_text())
     page.select_option('#mx-skill-group','');page.fill('#mx-skill-search','syn_chain');check('bank full-text search filters stable skill id',page.locator('#mx-skill-list .mx-skill-row').count()==1)
     page.evaluate("location.hash='#/methods?project_id=demo-selfplay&id=sp-round-003'");page.wait_for_selector('.mx-question-card');page.wait_for_timeout(200)
     check('project switch filters methods not just heading','skill-bank-v1' not in page.locator('#mx-object-list').inner_text())
     check('both proposal directions visible','Agent A 出题 → Agent B' in page.locator('.mx-main').inner_text() and 'Agent B 出题 → Agent A' in page.locator('.mx-main').inner_text())
     check('proposed answer distinct from reviewer verification','出题者提出的答案' in page.locator('.mx-main').inner_text() and '审核者确认的答案' in page.locator('.mx-main').inner_text())
     page.get_by_text('出题轨迹 · sp-proposal-a',exact=True).click();page.wait_for_selector('.mx-trace-preview .ix-step')
     proposal=page.locator('.mx-fold').filter(has=page.locator('summary').filter(has_text='出题轨迹 · sp-proposal-a')).last
     check('proposal unfolds independently to real saved steps',page.locator('.mx-trace-preview .ix-step').count()>0)
     page.get_by_text('解题轨迹 · agent-b · sp-attempt-017-1',exact=True).click();page.wait_for_timeout(200)
     check('solver and proposer can stay expanded together',page.locator('.mx-trace-preview .ix-step').count()>=4)
     page.get_by_text('解题轨迹 · agent-b · sp-attempt-017-1',exact=True).click();check('closing solver does not close proposal',page.get_by_text('出题轨迹 · sp-proposal-a',exact=True).evaluate('(e)=>e.parentElement.open'))
     # Field folding inside proposal is ordinary trace-tree folding, not just text clipping.
     first=page.locator('.mx-trace-preview .ix-step > summary').first;first.click();page.wait_for_timeout(100)
     check('method-linked trace steps independently expandable',page.locator('.mx-trace-preview .ix-step[open]').count()>0)
     # Distinct lifecycle stages and rejected questions retain original scores.
     page.get_by_text('训练去向 · 2',exact=True).click()
     check('selected and sampled displayed separately','记录为已采样' in page.locator('.mx-main').inner_text() and '已选入' in page.locator('.mx-main').inner_text())
     check('rejected question keeps attempts and original EM','审核驳回' in page.locator('.mx-main').inner_text() and '原评分保留' in page.locator('.mx-main').inner_text())
     check('batch update links back to training and fixed validation',page.get_by_role('link',name='训练运行与固定评测 ↗').count()==1)
     page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(shots/'11_selfplay_round.png'),full_page=True)
     page.evaluate("location.hash='#/methods?project_id=demo-skillrl&id=skill-bank-v1'");page.wait_for_selector('#mx-skill-detail .mx-use-card');page.wait_for_timeout(200)
     check('skill text conditions and content hash visible','适用条件' in page.locator('#mx-skill-detail').inner_text() and 'SHA256' in page.locator('#mx-skill-detail').inner_text())
     check('selected/injected/visible remain distinct counters','入选记录' in page.locator('#mx-skill-detail').inner_text() and '输入片段核对' in page.locator('#mx-skill-detail').inner_text())
     check('no causal skill-success metric inferred','不是技能贡献或成功率' in page.locator('#mx-skill-detail').inner_text())
     check('four checkpoint-bank cells retain separate evaluations',page.locator('#mx-evaluation-matrix .mx-matrix-cell').count()==4)
     page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(shots/'12_skillbank_and_usage.png'),full_page=True)
     page.select_option('#mx-bank-baseline','skill-bank-v2');page.locator('#mx-bank-diff').click();page.wait_for_selector('#mx-diff .mx-diff-code')
     check('snapshot diff shows added removed and modified','新增 1' in page.locator('#mx-diff').inner_text() and '修改 1' in page.locator('#mx-diff').inner_text())
     page.locator('#mx-diff').scroll_into_view_if_needed();page.screenshot(path=str(shots/'13_skillbank_diff.png'),full_page=False)
     # Exact input evidence routes into same old inspector and has method associations.
     page.get_by_role('link',name='定位模型输入',exact=True).click();page.wait_for_selector('.ix-path-bar');page.wait_for_timeout(300)
     check('skill evidence opens model input in existing inspector','/steps/0/input/0/content' in page.locator('.ix-path-bar').inner_text())
     check('four diagnostic tabs retained with contextual method tab',page.locator('.lp-workflow-tab').count()==5)
     page.locator('#lp-tab-methods').click();page.wait_for_timeout(200)
     check('trace links back to SkillBank and retrieval record','skill-bank-v1' in page.locator('#mx-inspector-panel').inner_text() and 'skill-retrieval-01' in page.locator('#mx-inspector-panel').inner_text())
     page.screenshot(path=str(shots/'14_trace_method_evidence.png'),full_page=True)
     page.evaluate("location.hash='#/methods?id=skill-retrieval-missing&view=artifacts'");page.wait_for_selector('[aria-label="方法证据复核说明"]')
     check('injection mismatch exposed as candidate','渲染片段未出现在所引用输入中' in page.locator('.mx-main').inner_text())
     page.get_by_label('方法证据复核说明').fill('Browser audit: compare rendered fragment and actual request before attributing failure.');page.get_by_role('button',name='保存复核',exact=True).click();page.wait_for_function("document.querySelector('.mx-main').textContent.includes('Browser audit:')")
     check('method review persists in real sidecar',len(server.methods.reviews('skill-retrieval-missing'))==1)
     page.locator('#refresh-button').click();page.wait_for_selector('[aria-label="方法证据复核说明"]');check('review survives refresh','Browser audit:' in page.locator('.mx-main').inner_text())
     page.get_by_role('button',name='以此产物和来源建立诊断案例',exact=True).click();page.wait_for_selector('.ix-record-heading');page.wait_for_timeout(300)
     check('multiple-artifact diagnostic case not passed off as role trace','retrospective' in page.locator('#ix-tree').inner_text() or len(server.methods.objects(kind='method_case'))==1)
     page.locator('#lp-tab-checks').click();page.wait_for_selector('#lp-body .lp-layer');check('method cases connect to existing layered checks',page.locator('#lp-body .lp-layer').count()==4)
     # Direct snapshot remains unchanged after reviews and comparisons.
     check('frozen bank did not follow newer skill revision',server.methods.object('skill-bank-v1')['data']['general_skills'][0]['principle']=='先确认中间实体，再查询它的目标属性。')
     check('no JavaScript runtime errors',not errors)
     b.close()
   finally:server.shutdown();server.server_close()
 (ROOT/'docs/browser_methods_output.json').write_text(json.dumps({'mode':mode,'checks':checks,'count':len(checks),'errors':errors},ensure_ascii=False,indent=2));print('PASSED',len(checks),'method browser checks')
if __name__=='__main__':main()
