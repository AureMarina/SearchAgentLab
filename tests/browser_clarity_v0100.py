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


checks=[]; all_errors=[]
def done(name):
 checks.append(name);print('PASS',name,flush=True)
def ready(page):
 page.wait_for_selector('.tb-column-footer',timeout=30000)
 expect(page.locator('.tb-shell')).to_have_attribute('aria-busy','false',timeout=30000)
def tab(page,name):
 if name in ['results','trace','trace-compare'] and not page.locator('.ex-subtabs').is_visible():page.locator('[data-section="execution"]').click()
 page.locator(f'[data-view="{name}"]').first.click()
 if name.startswith('trace'):ready(page)
 else:page.wait_for_timeout(500)
def heading(page,term):expect(page.locator('.tb-question-heading h2')).to_contain_text(term,timeout=15000)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=args.browser_path,args=['--no-sandbox','--disable-dev-shm-usage'])
 page=browser.new_page(viewport={'width':1500,'height':1000},device_scale_factor=1)
 errors=open_app(page,BASE+'&view=results');all_errors.append(errors)
 page.wait_for_selector('[data-result-sample]')
 expect(page.locator('.ex-tabs [role="tab"]')).to_have_count(2)
 expect(page.locator('.ex-subtabs [role="tab"]')).to_have_count(3)
 expect(page.locator('.ex-tabs')).to_contain_text('Agent 执行评测')
 expect(page.locator('.ex-tabs')).to_contain_text('训练评测')
 expect(page.locator('.f-kpis').first).to_contain_text('8/11')
 assert page.locator('.cl-reference-fold').get_attribute('open') is None
 page.wait_for_function("!document.querySelector('.f-result-table').textContent.includes('读取问题摘要')")
 done('Two primary views, three execution subviews; current score 8/11; distinct full-set reference collapsed; real summaries loaded')
 tokens=page.evaluate("() => {let s=getComputedStyle(document.documentElement);return [s.getPropertyValue('--s-blue').trim(),s.getPropertyValue('--s-sidebar').trim()]}")
 assert tokens==['#2f7467','#f2ebdf'],tokens
 done('Original green/sand theme tokens retained')
 screenshot(page,'01_results.png')
 page.get_by_label('实验对照评测 A',exact=True).select_option(A)
 page.wait_for_selector('[data-result-filter="harmed"]')
 page.locator('[data-result-filter="harmed"]').click()
 expect(page.locator('[data-result-sample]')).to_have_count(1)
 expect(page.locator('.f-result-table')).to_contain_text('Alice Of Champagne')
 expect(page.locator('.f-kpis').first).to_contain_text('条件不一致')
 page.get_by_role('link',name='查看轨迹 →',exact=True).click();ready(page);heading(page,'Alice Of Champagne')
 expect(page.locator('[data-trace-filter="harmed"]')).to_have_class('s-filter active')
 assert 'result_a='+A in page.evaluate('location.hash')
 done('Result filter/explicit A/selected question survive opening single trace; cross-project gain remains guarded')
 page.locator('.tb-step-button').last.click();page.locator('[data-trace-field="returned"]').click()
 saved=page.evaluate('location.hash');tab(page,'trace-compare');heading(page,'Alice Of Champagne')
 expect(page.locator('.tb-column')).to_have_count(2)
 expect(page.locator('.tb-list-heading')).to_have_text('题目 1 / 11')
 expect(page.get_by_label('轨迹对照评测 A',exact=True)).to_have_value(A)
 expect(page.locator('[data-trace-field="returned"]')).to_have_attribute('aria-selected','true')
 done('Same-question compare retains paired filter and selected field rather than resetting state')
 page.locator('[data-trace-side="b"] .tb-step-button').last.click()
 last_step=page.locator('.tb-detail-heading h3').inner_text()
 footer=page.locator('[data-trace-side="b"] .tb-column-footer')
 footer.get_by_role('link',name='完整日志 / 方法记录 ↗').click()
 
 try:page.wait_for_selector('.s-replay-grid',timeout=12000)
 except Exception:
  print('DEBUG',page.evaluate('location.hash'),errors,page.locator('body').inner_text()[:4000],flush=True)
  raise
 page.locator('a[href*="return_view="]').count() # no-op: return links use actual view
 back=page.locator('a[href*="view=trace-compare"]').first
 expect(back).to_be_visible();back.click();ready(page);heading(page,'Alice Of Champagne')
 expect(page.locator('.tb-detail-heading h3')).to_have_text(last_step)
 expect(page.locator('[data-trace-field="returned"]')).to_have_attribute('aria-selected','true')
 expect(page.locator('.tb-list-heading')).to_have_text('题目 1 / 11')
 done('Advanced logs round trip preserves A/B, question, paired filter, selected side, step and field')
 tab(page,'results');expect(page.locator('[data-result-sample]')).to_have_count(1)
 expect(page.locator('[data-result-sample].is-current')).to_have_count(1)
 expect(page.get_by_label('实验对照评测 A',exact=True)).to_have_value(A)
 done('Returning to results keeps filter and current-row highlight')
 tab(page,'training');page.wait_for_selector('.s-small-context')
 expect(page.locator('.s-small-context')).to_contain_text('阶段未登记')
 expect(page.locator('.ex-pane')).to_contain_text('已接入训练指标；逐样本学习证据未接入')
 expect(page.locator('.ex-subtabs')).not_to_be_visible()
 assert page.locator('.s-matrix').count()==0
 screenshot(page,'03_training_unknown.png')
 done('Existing training logs explicitly show unknown stage and missing batch evidence; no duplicate question matrix')
 page.locator('[data-section="execution"]').click();page.wait_for_selector('[data-result-sample]')
 expect(page.locator('[data-result-sample]')).to_have_count(1)
 done('Primary execution tab restores previous subview and selection')
 # Original skill detail is retained through a contextual link, not a third primary tab.
 page.locator('.ex-method-links [data-view="skill"]').click();page.wait_for_timeout(700)
 assert page.locator('.ex-pane').inner_text().strip()
 expect(page.locator('.ex-tabs [data-view="training"]')).to_have_attribute('aria-selected','true')
 done('Original Skill asset view remains reachable by contextual link')
 tab(page,'results');page.locator('[data-result-filter="all"]').click();tab(page,'trace-compare');ready(page)
 search=page.get_by_label('轨迹题目搜索',exact=True);search.fill('cathedral');heading(page,'cathedral');ready(page)
 page.locator('[data-trace-field="visible"]').click()
 literal=page.get_by_label('高亮可见内容',exact=True);literal.fill('<img src=x onerror=alert(1)>')
 assert page.locator('.tb-details img').count()==0
 literal.fill('')
 screenshot(page,'02_compare.png')
 sizes=[]
 for width in [1440,1100,800,390]:
  page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(150)
  row=page.evaluate('() => ({width:innerWidth,document:document.documentElement.scrollWidth})');sizes.append(row)
  assert row['document']<=row['width']+1,row
 done('Compare remains literal-safe and has no page-wide overflow at 1440/1100/800/390 px')
 page.set_viewport_size({'width':1500,'height':1000});search.fill('');ready(page)
 page.locator('.tb-question').nth(0).click();page.locator('.tb-question').nth(1).click();page.locator('.tb-question').nth(2).click();ready(page);heading(page,'Andriy')
 done('Rapid question changes retain the final selected record')
 old=browser.new_page(viewport={'width':1440,'height':1000});all_errors.append(open_app(old,BASE+'&view=trace&trace_filter=incorrect&trace_q=Alice'));ready(old);heading(old,'Alice Of Champagne')
 expect(old.locator('[data-trace-filter="incorrect"]')).to_have_class('s-filter active')
 done('Legacy deep links migrate their query/filter instead of losing state')
 p2=browser.new_page(viewport={'width':1440,'height':1000});all_errors.append(open_app(p2,'#/experiment?run=native-qwen3-8b-sevenqa-20261009-run&view=trace'))
 expect(p2.locator('.tb-main')).to_contain_text('没有保存本题轨迹')
 assert p2.locator('.tb-step').count()==0
 done('Aggregate-only evaluations never fabricate per-question evidence')
 flat=[err for group in all_errors for err in group];assert flat==[],flat
 done('No uncaught browser JavaScript errors across retained/new views')
 (OUT/'browser_acceptance.json').write_text(json.dumps({'passed':len(checks),'checks':checks,'responsive':sizes,'errors':flat,'local_transport':args.local_transport,'note':'Production built modules and actual server API. No API fixtures. Read-only checks on copied fixed11 snapshot; transport interception required by QA browser policy.'},ensure_ascii=False,indent=2))
 browser.close()
print('COMPLETE',len(checks),flush=True)
