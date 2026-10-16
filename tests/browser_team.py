"""Browser interaction with real center APIs via the existing sandbox HTTP bridge.
Every shown score/trajectory is SYNTHETIC. No benchmark/model training is run.
"""
import sys,json,tempfile,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'examples')]
from seed_studio_demo import seed
from bridge_studio import load
from searchagent_lab.team.package import init_source,export_run
from searchagent_lab.team.hub import Hub,gid
from searchagent_lab.team.server import TeamServer
from searchagent_lab.team.auth import Auth
from playwright.sync_api import sync_playwright

def main():
 checks=[];errors=[];shots=ROOT/'docs/v081/screenshots/team';shots.mkdir(parents=True,exist_ok=True)
 def check(name,value):
  if not value:raise AssertionError(name)
  checks.append({'name':name,'passed':True});print('PASS',name,flush=True)
 with tempfile.TemporaryDirectory() as tmp:
  local=Path(tmp)/'source';center=Path(tmp)/'center';seed(local);src=init_source(local,'同学 A（合成）','平台 A（合成）')
  with Hub(center) as h:
   pkgs={}
   for r in ['studio-base','studio-run','studio-repro-run']:
    pkg=export_run(local,r);pkgs[r]=pkg;h.ingest(pkg['path'],allow_definitions=True,submitter_owner='synthetic-student')
   admin,_=Auth(center).issue('synthetic-admin','admin');viewer,_=Auth(center).issue('synthetic-reader','viewer')
   with TeamServer(('127.0.0.1',0),h) as server:
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=server.public_origin
    try:
     with sync_playwright() as p:
      b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
      page=load(b,base,'#/team?project=studio-selfplay&track=main',errors,{'Authorization':'Bearer '+admin})
      page.wait_for_selector('.s-team-import');page.wait_for_function("document.querySelector('#s-app').textContent.includes('studio-run')")
      check('central intake visible, original four navigation items retained',page.locator('#s-nav a').count()==5)
      check('source labels visible','平台 A（合成）' in page.locator('#s-app').inner_text())
      check('training state not conflated with heartbeat','网络中断不等于运行失败' in page.locator('#s-app').inner_text())
      page.get_by_label('筛选接入运行').fill('studio-run');check('intake source search filters real received rows',page.locator('.s-table').first.locator('tbody tr').count()==1)
      page.get_by_label('筛选接入运行').fill('');page.screenshot(path=str(shots/'01_team_intake.png'),full_page=True)
      page.get_by_label('实际提交人').fill('synthetic-student');page.get_by_label('选择结果包').set_input_files(pkgs['studio-run']['path']);page.get_by_role('button',name='导入结果包',exact=True).click();page.wait_for_function("document.querySelector('.s-team-import').textContent.includes('duplicate')")
      check('browser ZIP resubmission returns duplicate, not a second run',len(h.runs())==3)
      rid=gid(src['workspace_id'],'run','studio-run');cp=gid(src['workspace_id'],'checkpoint','studio-run-cp100')
      page.evaluate('(hash)=>location.hash=hash',f'#/training?project=studio-selfplay&track=main&run={rid}&panel=studio-synthetic-11&cp={cp}')
      page.wait_for_selector('[data-cp="'+cp+'"]');check('training metrics and all fixed questions restored',page.locator('.s-chart').count()==3 and page.locator('tr[data-question]').count()==11)
      check('run selector includes operator/platform','平台 A（合成）' in page.get_by_label('训练运行',exact=True).inner_text())
      page.get_by_role('button',name='来源与完整性',exact=True).click();page.wait_for_selector('#s-modal[open]');check('provenance modal contains source and center IDs','studio-run' in page.locator('#s-modal').inner_text() and rid in page.locator('#s-modal').inner_text());page.locator('#s-modal-close').click()
      page.screenshot(path=str(shots/'02_training_source.png'),full_page=True)
      page.get_by_role('button',name='查看 batch → rollout group → reward / advantage / mask ↗').click();page.wait_for_selector('#s-modal[open]');page.locator('#s-modal summary').first.click();check('training batch/method links resolve after cross-platform relocation','0.5' in page.locator('#s-modal').inner_text());page.locator('#s-modal-close').click()
      page.locator('tr[data-question] .s-result-link').first.click();page.wait_for_selector('.s-replay-right');check('replay provenance is collapsed by default',page.get_by_text('来源与完整性',exact=True).count()==1 and not page.locator('.s-replay-right details').first.get_attribute('open'))
      check('no permanent manual review textarea',page.locator('textarea:visible').count()==0)
      page.screenshot(path=str(shots/'03_replay_provenance.png'),full_page=True)
      page.close()
      reader=load(b,base,'#/team?project=studio-selfplay&track=main',errors,{'Authorization':'Bearer '+viewer});reader.wait_for_selector('#s-nav a[data-page="team"]');reader.wait_for_function("document.querySelector('#s-app').textContent.includes('已接收运行')")
      check('viewer has no upload form',reader.locator('.s-team-import').count()==0);reader.close()
      check('no browser JavaScript errors',not errors);b.close()
    finally:server.shutdown();thread.join()
 (ROOT/'docs/v081/browser-team.json').write_text(json.dumps({'synthetic_only':True,'transport':'Existing in-page ES-module loader + real authenticated HTTP APIs; native loopback browser navigation blocked by sandbox policy','checks':checks,'errors':errors},ensure_ascii=False,indent=2))
 print('TEAM_BROWSER_OK',len(checks))
if __name__=='__main__':main()
