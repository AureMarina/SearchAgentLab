from playwright.sync_api import sync_playwright, expect
from pathlib import Path
import json,time
BASE='#/experiment?run=skillrl-qwen3-4b-150steps-20261006'
A='uniopsd-qwen3-4b-step150-fixed11-eval-20261009'
import argparse, shutil
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
parser=argparse.ArgumentParser(description='Read-only browser acceptance for the bundled fixed11 snapshot. Start its server first; requires Playwright and Chromium.')
parser.add_argument('--base-url',default='http://127.0.0.1:8765')
parser.add_argument('--artifact-dir',default='browser-artifacts-v099')
parser.add_argument('--browser-path',default=shutil.which('chromium') or shutil.which('chromium-browser'))
parser.add_argument('--local-transport',action='store_true',help='For restricted QA environments only: route production assets and API through a synthetic browser origin.')
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
  page.evaluate("""() => document.addEventListener('click',e=>{const a=e.target.closest('a'),url=a?.getAttribute('href');if(url?.startsWith('#/')){e.preventDefault();location.hash=url;}},true)""")
 page.wait_for_selector('#s-app[aria-busy="false"]',timeout=60000)
 return errors

def screenshot(page,name):
 page.evaluate('()=>{document.activeElement?.blur();window.scrollTo(0,0)}')
 page.wait_for_timeout(150)
 page.screenshot(path=str(OUT/name),full_page=True)

checks=[]; all_errors=[]
def done(name):
 checks.append(name);print('PASS',name,flush=True)
def ready(page):
 page.wait_for_selector('.tb-column-footer',timeout=30000)
 expect(page.locator('.tb-shell')).to_have_attribute('aria-busy','false',timeout=30000)
def open_tab(page,name):
 page.locator(f'.ex-tabs [data-view="{name}"]').click()
 if name.startswith('trace'):ready(page)
def active_heading(page, term):
 expect(page.locator('.tb-question-heading h2')).to_contain_text(term,timeout=15000)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=args.browser_path,args=['--no-sandbox','--disable-dev-shm-usage'])
 page=browser.new_page(viewport={'width':1660,'height':1080},device_scale_factor=1)
 errors=open_app(page,BASE+'&view=trace');all_errors.append(errors);ready(page)
 expect(page.locator('.ex-tabs [data-view="trace"]')).to_have_attribute('aria-selected','true')
 for name in ['Agent 执行评测','单条轨迹','同题对比','RL 训练评测','技能资产']:expect(page.get_by_role('tab',name=name,exact=True)).to_be_visible()
 expect(page.locator('.tb-list-heading')).to_have_text('题目 11 / 11')
 expect(page.locator('.tb-summary')).to_contain_text('8 / 11 正确')
 assert page.locator('.tb-step').count()==3
 done('Original experiment tabs and method records retained; actual 11 questions / 3 grouped steps loaded')
 tokens=page.evaluate('''() => {const s=getComputedStyle(document.documentElement);return [s.getPropertyValue('--s-blue').trim(),s.getPropertyValue('--s-sidebar').trim()]}''')
 assert tokens==['#2f7467','#f2ebdf'],tokens
 assert page.locator('.tb-shell [class*=insight]').count()==0
 done('Original theme variables unchanged; no automatic root-cause panel')
 page.locator('[data-trace-field="model"]').click();expect(page.locator('.tb-detail-body')).to_contain_text('<search>')
 page.locator('[data-trace-field="returned"]').click();expect(page.locator('.tb-detail-body')).to_contain_text('工具原始返回不代表')
 page.locator('[data-trace-field="more"]').click();expect(page.locator('.tb-detail-body')).to_contain_text('本步记录的输入');page.locator('.tb-detail-body summary').first.click();expect(page.locator('.tb-detail-body')).to_contain_text('Zazie')
 page.locator('[data-trace-field="visible"]').click()
 h=page.get_by_label('高亮可见内容',exact=True);h.fill('Louis Malle');assert page.locator('.tb-document-text mark').count()>0
 h.fill('<img src=x onerror=alert(1)>');assert page.locator('.tb-details img').count()==0
 h.fill('');done('Field tabs keep model-visible, raw return, and recorded input separate; safe literal highlight')
 page.get_by_role('button',name='下一步 →',exact=True).click();expect(page.locator('.tb-detail-heading h3')).to_have_text('第 2 步 · 搜索')
 page.locator('.tb-question-heading h2').click();page.keyboard.press('ArrowRight');expect(page.locator('.tb-detail-heading h3')).to_have_text('第 3 步 · 回答')
 page.keyboard.press('ArrowLeft');expect(page.locator('.tb-detail-heading h3')).to_have_text('第 2 步 · 搜索')
 done('Click and keyboard step selection update actual details')
 page.locator('[data-trace-filter="incorrect"]').click();expect(page.locator('.tb-list-heading')).to_have_text('题目 3 / 11')
 page.locator('[data-trace-filter="all"]').click();expect(page.locator('.tb-list-heading')).to_have_text('题目 11 / 11')
 search=page.get_by_label('轨迹题目搜索',exact=True);search.fill('xyz-no-match-199939');expect(page.locator('.tb-main')).to_contain_text('没有匹配题目')
 search.fill('cathedral');active_heading(page,'cathedral');expect(page.locator('.tb-list-heading')).to_have_text('题目 1 / 11');ready(page)
 search.fill('');expect(page.locator('.tb-question-heading')).to_contain_text('11 / 11')
 done('Question score filters / search / empty-state recovery; current question count updates')
 page.locator('.tb-step-button').first.click();page.locator('[data-trace-field="returned"]').click()
 saved=page.evaluate('location.hash');assert 'trace_sample=' in saved and 'trace_field=returned' in saved
 reload_page=browser.new_page(viewport={'width':1660,'height':1080});re=open_app(reload_page,saved);all_errors.append(re);ready(reload_page)
 active_heading(reload_page,'cathedral');expect(reload_page.locator('[data-trace-field="returned"]')).to_have_attribute('aria-selected','true');reload_page.close()
 done('Deep links restore current question and selected field')
 # Keep real screenshot focused on the fixed11 cathedral question, not a mockup.
 page.locator('[data-trace-field="visible"]').click();screenshot(page,'single_final.png')
 page.locator('.tb-column-footer').get_by_role('link',name='完整日志 / 方法记录 ↗').click()
 page.wait_for_selector('.s-replay-grid',timeout=20000)
 expect(page.locator('body')).to_contain_text('返回')
 back=page.locator('a[href*="view=trace"]').first
 expect(back).to_be_visible();back.click();ready(page);active_heading(page,'cathedral')
 done('Preserved advanced log page opens and returns to the same trace question')
 open_tab(page,'results');page.wait_for_selector('a[href*="view=trace"]',timeout=20000)
 page.locator('a[href*="view=trace"]').first.click();ready(page)
 done('Existing evaluation table links into the new single-trace view')
 open_tab(page,'training');page.wait_for_timeout(800);expect(page.locator('.ex-pane')).not_to_contain_text('读取本实验 / 当前运行的记录')
 assert page.locator('.ex-pane').inner_text().strip()
 open_tab(page,'skill');page.wait_for_timeout(800);assert page.locator('.ex-pane').inner_text().strip()
 done('Original RL training and skill views still render')
 open_tab(page,'trace-compare');expect(page.locator('.tb-condition')).to_contain_text('先选择对照 A');assert page.locator('.tb-column').count()==1
 page.get_by_label('轨迹对照评测 A',exact=True).select_option(A);ready(page)
 expect(page.locator('.tb-condition')).to_contain_text('仅浏览差异');expect(page.locator('.tb-summary')).to_contain_text('A · 7 / 11');assert page.locator('.tb-column').count()==2
 done('Comparison requires explicit A; cross-project traces browse without training-gain attribution')
 page.locator('[data-trace-filter="rescued"]').click();expect(page.locator('.tb-list-heading')).to_have_text('题目 2 / 11')
 page.locator('[data-trace-filter="harmed"]').click();expect(page.locator('.tb-list-heading')).to_have_text('题目 1 / 11')
 active_heading(page,'Alice Of Champagne');ready(page)
 page.locator('[data-trace-filter="all"]').click();ready(page)
 done('Paired outcomes reflect saved scores: two A-wrong/B-right; one A-right/B-wrong')
 page.get_by_label('轨迹题目搜索',exact=True).fill('cathedral');ready(page);active_heading(page,'cathedral')
 expect(page.locator('[data-trace-jump="difference"]')).to_be_visible();page.locator('[data-trace-jump="difference"]').click()
 page.locator('[data-trace-side="a"] .tb-step-button').first.click();expect(page.locator('.tb-detail-heading h3')).to_contain_text('A · 第 1 步')
 page.locator('[data-trace-side="b"] .tb-step-button').last.click();expect(page.locator('.tb-detail-heading h3')).to_contain_text('B ·')
 page.locator('[data-trace-jump="difference"]').click();page.get_by_label('轨迹题目搜索',exact=True).fill('')
 screenshot(page,'compare_final.png')
 done('Independent A/B steps selectable; first literal action/return difference jump works')
 sizes=[]
 for width in [1440,1100,800,390]:
  page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(120)
  values=page.evaluate('''() => ({w:innerWidth,doc:document.documentElement.scrollWidth,shell:document.querySelector('.tb-shell').getBoundingClientRect().width})''')
  sizes.append(values)
  assert values['doc']<=values['w']+1,values
 done('Responsive compare: no page-wide horizontal overflow at 1440/1100/800/390 px')
 page.set_viewport_size({'width':1660,'height':1080})
 # Rapid changes must leave evidence owned by the final selected question.
 search=page.get_by_label('轨迹题目搜索',exact=True)
 search.fill('');page.locator('.tb-question').nth(0).click();page.locator('.tb-question').nth(1).click();page.locator('.tb-question').nth(2).click();ready(page)
 active_heading(page,'Andriy');expect(page.locator('.tb-details')).not_to_contain_text('读取中')
 done('Rapid question changes retain final selection without stale evidence')
 page2=browser.new_page(viewport={'width':1440,'height':1000});errs=open_app(page2,'#/experiment?run=native-qwen3-8b-sevenqa-20261009-run&view=trace');all_errors.append(errs)
 expect(page2.locator('.tb-summary')).to_contain_text('未接入逐题评测');assert page2.locator('.tb-step').count()==0
 expect(page2.locator('.tb-main')).to_contain_text('没有保存本题轨迹')
 done('Aggregate-only native baseline shows missing per-question traces; never fabricates steps')
 flat=[e for group in all_errors for e in group];assert flat==[],flat
 done('No uncaught browser JavaScript errors across new and retained views')
 (OUT/'browser_acceptance.json').write_text(json.dumps({'passed':len(checks),'checks':checks,'responsive':sizes,'errors':flat,'local_transport':args.local_transport,'note':'Read-only bundled snapshot test; production built modules and live server API.'},ensure_ascii=False,indent=2))
 browser.close()
print('COMPLETE',len(checks),flush=True)
