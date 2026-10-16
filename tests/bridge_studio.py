"""Test-only static ES-module loader + real local HTTP bridge.
The sandbox blocks native browser navigation to localhost. This loader rewrites
relative import URLs only, not business logic, and never mocks API responses.
"""
from pathlib import Path
import re,json,urllib.request,urllib.error
from playwright.sync_api import sync_playwright
STATIC=Path(__file__).resolve().parents[1]/'searchagent_lab/static'
def load(browser,base,hash,errors,request_headers=None):
 page=browser.new_page(viewport={'width':1680,'height':1120})
 page.on('pageerror',lambda e:errors.append(str(e)))
 def bridge(url,options=None):
  opts=options or {};body=opts.get('body');body=body.encode() if isinstance(body,str) else bytes(body) if body is not None else None
  try:r=urllib.request.urlopen(urllib.request.Request(base+url,data=body,headers={**(request_headers or {}),**opts.get('headers',{})},method=opts.get('method','GET')))
  except urllib.error.HTTPError as e:r=e
  return {'body':r.read().decode(),'status':r.status,'headers':dict(r.headers)}
 page.expose_function('__local_http',bridge)
 html=re.sub(r'<link[^>]*>','',(STATIC/'index.html').read_text());html=re.sub(r'<script[^>]*>.*?</script>','',html,flags=re.S)
 page.set_content(html)
 for f in re.findall(r'<link[^>]+href="/([^"]+\.css)"', (STATIC/'index.html').read_text()):page.add_style_tag(content=(STATIC/f).read_text())
 page.evaluate('(h)=>location.hash=h',hash)
 page.add_script_tag(content='''window.fetch=async(url,opts={})=>{let body=opts.body;if(body instanceof Blob)body=Array.from(new Uint8Array(await body.arrayBuffer()));const r=await window.__local_http(url,{...opts,body});return new Response(r.body,{status:r.status,headers:r.headers});};''')
 urls={}
 def mod(path):
  path=path.resolve()
  if path in urls:return urls[path]
  src=re.sub(r"(\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)['\"](\.{1,2}/[^'\"]+\.js)['\"]",lambda m:m.group(1)+json.dumps(mod(path.parent/m.group(2))),path.read_text())
  urls[path]=page.evaluate('(src)=>URL.createObjectURL(new Blob([src],{type:"text/javascript"}))',src)
  return urls[path]
 page.add_script_tag(content='import '+json.dumps(mod(STATIC/'studio/app.js'))+';',type='module')
 return page
