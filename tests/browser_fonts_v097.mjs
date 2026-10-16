/** Focused, real-browser font acceptance against the compiled application.
 * Original data/imports are hashed only; both viewers use temporary copies.
 * --font-file names the separately downloaded original official TTF. It is
 * installed only into a temporary workspace and never copied into source.
 */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),options={};
for(let i=2;i<process.argv.length;i+=2){
 if(!['--data-dir','--font-file','--out'].includes(process.argv[i])||!process.argv[i+1])throw Error('Use --data-dir PATH --font-file OFFICIAL_TTF [--out PATH].');
 options[process.argv[i].slice(2)]=process.argv[i+1];
}
if(!options['data-dir']||!options['font-file'])throw Error('--data-dir and --font-file are required.');
const source=path.resolve(options['data-dir']),fontFile=path.resolve(options['font-file']),out=path.resolve(options.out||path.join(root,'docs/v097'));
const fontPath='/fonts/TsangerYuKai-Regular.ttf',officialSha='3402a3a595edceb5f0cb69143be236807cff5d74382c3816635354f23e4e1a16';
const python=process.env.SEARCHAGENT_PYTHON||process.env.CODEX_PRIMARY_RUNTIME_PYTHON||'python3';
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const report={version:'0.9.7',passed:false,checks:[],runs:[],screenshots:[],transport:'Compiled assets and real LabServer HTTP through same-origin virtual transport; original response bytes are forwarded.',limitations:[
 'Two temporary copies of the supplied real snapshot are used; no synthetic scores or model execution.',
 'Font requests are deliberately delayed once to verify that workspace rendering does not wait for the font.',
 'The official font is an external test input and is not redistributed in source or test outputs.',
]};
const check=(name,passed,detail)=>{report.checks.push({name,passed:!!passed,...(detail===undefined?{}:{detail})});console.log((passed?'PASS ':'FAIL ')+name);if(!passed)throw Error(name);};
async function hashInputs(){
 const result={};
 async function walk(directory,prefix){
  let entries;try{entries=await fs.readdir(directory,{withFileTypes:true});}catch(error){if(error.code==='ENOENT')return;throw error;}
  for(const entry of entries.sort((a,b)=>a.name.localeCompare(b.name))){
   const absolute=path.join(directory,entry.name),key=path.posix.join(prefix,entry.name);
   if(entry.isDirectory())await walk(absolute,key);else if(entry.isFile())result[key]=hash(await fs.readFile(absolute));
  }
 }
 await walk(source,'data');await walk(path.join(path.dirname(source),'imports'),'imports');return result;
}
const before=await hashInputs(),temp=await fs.mkdtemp(path.join(os.tmpdir(),'searchagent-fonts-v097-'));
await fs.mkdir(path.join(out,'screenshots'),{recursive:true});
const manifestPath=path.join(root,'searchagent_lab/static/assets/build-manifest.json'),manifestBefore=await fs.readFile(manifestPath);
report.build_manifest_sha256=hash(manifestBefore);report.build=JSON.parse(manifestBefore);
report.original_file_counts={databases:Object.keys(before).filter(p=>p.startsWith('data/')&&p.endsWith('.sqlite3')).length,imports:Object.keys(before).filter(p=>p.startsWith('imports/')).length};
const children=new Set();let browser,page,releaseFont;
async function execute(args){
 const child=spawn(python,args,{cwd:root,stdio:['ignore','pipe','pipe']});children.add(child);let stdout='',stderr='';
 child.stdout.on('data',chunk=>stdout+=String(chunk));child.stderr.on('data',chunk=>stderr+=String(chunk));
 return new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',code=>{children.delete(child);if(code!==0)reject(Error('Python exited '+code+': '+stderr));else resolve({stdout,stderr});});});
}
const serverCode=`import json,sys\nfrom searchagent_lab.store import Store\nfrom searchagent_lab.server import LabServer\nwith Store(sys.argv[1]) as store,LabServer(('127.0.0.1',0),store) as server:\n print('QA_READY='+json.dumps({'url':'http://127.0.0.1:'+str(server.server_address[1])}),flush=True)\n server.serve_forever()\n`;
async function startServer(data){
 const child=spawn(python,['-u','-c',serverCode,data],{cwd:root,stdio:['ignore','pipe','pipe']});children.add(child);let stderr='';child.stderr.on('data',chunk=>stderr+=String(chunk));
 const server=await new Promise((resolve,reject)=>{
  let buffer='';const timer=setTimeout(()=>reject(Error('Server startup timeout: '+stderr)),30000);
  child.stdout.on('data',chunk=>{buffer+=String(chunk);const line=buffer.split('\n').find(value=>value.startsWith('QA_READY='));if(line){clearTimeout(timer);resolve(JSON.parse(line.slice(9)));}});
  child.once('error',error=>{clearTimeout(timer);reject(error);});child.once('exit',code=>{clearTimeout(timer);children.delete(child);reject(Error('Server exited '+code+': '+stderr));});
 });return {child,...server};
}
async function stopServer(child){
 if(!child||child.exitCode!==null)return;
 child.kill('SIGTERM');await new Promise(resolve=>{child.once('exit',resolve);setTimeout(resolve,3000).unref();});
 if(child.exitCode===null)child.kill('SIGKILL');children.delete(child);
}
async function openWorkspace(mode,data,{holdFont=false}={}){
 const server=await startServer(data),origin='http://searchagent-fonts-'+mode+'.test';
 const run={mode,origin,requests:[],font_requests:[],javascript_errors:[],transport_errors:[]};report.runs.push(run);
 const context=await browser.newContext({viewport:{width:1440,height:1060}});page=await context.newPage();page.setDefaultTimeout(16000);
 page.on('pageerror',error=>run.javascript_errors.push(String(error)));
 page.on('request',request=>{if(request.resourceType()==='font')run.font_requests.push({url:request.url(),same_origin:new URL(request.url()).origin===origin});});
 let fontStartedResolve;const fontStarted=new Promise(resolve=>fontStartedResolve=resolve);
 const gate=holdFont?new Promise(resolve=>releaseFont=resolve):Promise.resolve();
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==origin){run.transport_errors.push('Unexpected external request: '+request.url());await route.abort();return;}
  if(url.pathname===fontPath){fontStartedResolve();await gate;}
  const headers={...request.headers()};delete headers.host;delete headers['accept-encoding'];headers.origin=server.url;
  try{
   const response=await fetch(server.url+url.pathname+url.search,{method:request.method(),headers,body:['GET','HEAD'].includes(request.method())?undefined:request.postDataBuffer()});
   const body=Buffer.from(await response.arrayBuffer()),record={method:request.method(),path:url.pathname,status:response.status};
   if(url.pathname===fontPath){record.content_type=response.headers.get('content-type');record.sha256=hash(body);}
   run.requests.push(record);const responseHeaders=Object.fromEntries(response.headers);delete responseHeaders['content-length'];delete responseHeaders['content-encoding'];
   await route.fulfill({status:response.status,headers:responseHeaders,body});
  }catch(error){if(!page.isClosed()){run.transport_errors.push(String(error));await route.abort().catch(()=>{});}}
 });
 await page.goto(origin+'/#/experiments',{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.querySelector('#s-app')?.getAttribute('aria-busy')==='false');
 await page.locator('#s-app h1').waitFor();
 return {run,context,page,server,fontStarted};
}
async function platformFonts(page,selector){
 const client=await page.context().newCDPSession(page);
 try{
  await client.send('DOM.enable');await client.send('CSS.enable');
  const document=await client.send('DOM.getDocument'),node=await client.send('DOM.querySelector',{nodeId:document.root.nodeId,selector});
  if(!node.nodeId)throw Error('Missing rendered node: '+selector);
  const result=await client.send('CSS.getPlatformFontsForNode',{nodeId:node.nodeId});
  return {selector,text:await page.locator(selector).innerText(),fonts:result.fonts};
 }finally{await client.detach();}
}
const isYuKai=font=>/TsangerYuKai|仓耳玉楷|倉耳玉楷/.test(font.familyName+' '+font.postScriptName);
try{
 // Ignore user-installed font directories so the absent-font case is reproducible.
 // Existing system fonts remain available; no local Tsanger font is installed.
 const fontConfig=path.join(temp,'fontconfig.conf');
 await fs.writeFile(fontConfig,'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>/usr/share/fonts</dir><dir>/usr/local/share/fonts</dir><cachedir>'+path.join(temp,'font-cache')+'</cachedir><alias><family>monospace</family><prefer><family>DejaVu Sans Mono</family></prefer></alias><alias><family>ui-monospace</family><prefer><family>DejaVu Sans Mono</family></prefer></alias><alias><family>sans-serif</family><prefer><family>DejaVu Sans</family></prefer></alias></fontconfig>');
 const library=process.env.SEARCHAGENT_PLAYWRIGHT_PATH||(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES?path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'playwright/index.mjs'):null);
 const {chromium}=await import(library?pathToFileURL(library).href:'playwright');
 browser=await chromium.launch({headless:true,executablePath:process.env.SEARCHAGENT_BROWSER_PATH||undefined,env:{...process.env,FONTCONFIG_FILE:fontConfig},args:['--no-sandbox','--disable-dev-shm-usage']});

 const absentData=path.join(temp,'absent','data');await fs.cp(source,absentData,{recursive:true});
 await fs.rm(path.join(absentData,'ui-fonts'),{recursive:true,force:true});
 const absent=await openWorkspace('absent',absentData);
 const missing=await absent.page.evaluate(async()=>{const response=await fetch('/api/ui/font');return {status:response.status,body:await response.json()};});
 check('uninstalled font API returns 200 with explicit unavailable state',missing.status===200&&!missing.body.available&&missing.body.url===null,missing);
 await absent.page.locator('#s-help').click();await absent.page.locator('#s-modal[open]').waitFor();
 const absentLabel=await absent.page.locator('#s-modal-body').innerText();
 check('workspace remains usable and does not claim an unloaded font is ready',(await absent.page.locator('#s-app h1').innerText())==='基线与实验'&&absentLabel.includes('仓耳玉楷未载入')&&!await absent.page.evaluate(()=>document.documentElement.dataset.uiFont==='TsangerYuKai'));
 check('uninstalled workspace does not request a missing TTF',absent.run.font_requests.length===0&&!absent.run.requests.some(request=>request.path===fontPath));
 await absent.context.close();await stopServer(absent.server.child);

 const installedData=path.join(temp,'installed','data');await fs.cp(source,installedData,{recursive:true});await fs.rm(path.join(installedData,'ui-fonts'),{recursive:true,force:true});
 const install=JSON.parse((await execute(['-m','searchagent_lab','setup-font','--data-dir',installedData,'--font-file',fontFile])).stdout);
 const originalFontSha=hash(await fs.readFile(fontFile)),installedFontSha=hash(await fs.readFile(path.join(installedData,'ui-fonts','TsangerYuKai-Regular.ttf')));
 report.font_install={...install,original_file_sha256:originalFontSha,installed_file_sha256:installedFontSha};
 check('separate official original is installed unchanged in the temporary workspace',install.available&&install.source==='local_file'&&originalFontSha===officialSha&&installedFontSha===officialSha,report.font_install);
 const installed=await openWorkspace('installed',installedData,{holdFont:true});
 let fontTimer;
 try{await Promise.race([installed.fontStarted,new Promise((_,reject)=>fontTimer=setTimeout(()=>reject(Error('Workspace font request was not started; ensure Tsanger is not installed as a system font.')),16000))]);}finally{clearTimeout(fontTimer);}
 check('workspace renders while the asynchronous font response is still pending',await installed.page.locator('#s-app h1').isVisible()&&!await installed.page.evaluate(()=>document.documentElement.dataset.uiFont==='TsangerYuKai'));
 releaseFont();releaseFont=null;
 await installed.page.waitForFunction(()=>document.documentElement.dataset.uiFont==='TsangerYuKai');
 await installed.page.evaluate(()=>document.fonts.ready);
 const meta=await installed.page.evaluate(async()=>(await fetch('/api/ui/font')).json());
 check('workspace font finishes loading from its local fixed resource',meta.available&&meta.source==='local_file'&&meta.url===fontPath&&meta.sha256===officialSha,meta);
 const actualFonts=[];
 for(const selector of ['#s-app h1','#s-nav a[href="#/experiments"]','#s-app .s-subtitle'])actualFonts.push(await platformFonts(installed.page,selector));
 report.actual_chinese_fonts=actualFonts;
 check('CDP confirms actual YuKai glyphs in the Chinese title navigation and body',actualFonts.every(result=>/[\u3400-\u9fff]/.test(result.text)&&result.fonts.some(font=>font.glyphCount>0)&&result.fonts.every(font=>font.glyphCount===0||(isYuKai(font)&&font.isCustomFont))),actualFonts);
 await installed.page.locator('#s-help').click();await installed.page.locator('#s-modal[open]').waitFor();
 const helpLabel=await installed.page.locator('#s-modal-body').innerText();
 check('help truthfully reports 仓耳玉楷 as loaded',helpLabel.includes('仓耳玉楷 · 已载入')&&!helpLabel.includes('仓耳玉楷未载入'));
 await installed.page.locator('#s-modal-close').click();
 await installed.page.screenshot({path:path.join(out,'screenshots','05_yukai_loaded_saved_workspace.png'),fullPage:true,animations:'disabled'});
 report.screenshots.push({file:'screenshots/05_yukai_loaded_saved_workspace.png',data:'supplied real snapshot copy',font_sha256:officialSha});

 await installed.page.locator('#s-nav a[href="#/experiments?family=entropy"]').click();
 await installed.page.getByRole('button',{name:'查看 Entropy 接入说明',exact:true}).click();
 await installed.page.locator('.en-integration summary').filter({hasText:'SDK 最小接入'}).click();
 const codeSelector='#s-modal .en-integration details[open] pre code',codeFonts=await platformFonts(installed.page,codeSelector);
 const codeMetrics=await installed.page.locator(codeSelector).evaluate(node=>{const style=getComputedStyle(node),canvas=document.createElement('canvas'),context=canvas.getContext('2d');context.font=style.font;return {font:style.font,font_family:style.fontFamily,narrow_width:context.measureText('iiiiiiii').width,wide_width:context.measureText('WWWWWWWW').width};});
 report.actual_code_fonts={...codeFonts,metrics:codeMetrics};
 check('actual code text keeps a monospace font and equal glyph widths',codeFonts.fonts.some(font=>/Mono|Consolas|Courier/i.test(font.familyName+' '+font.postScriptName)&&font.glyphCount>0)&&!codeFonts.fonts.some(isYuKai)&&Math.abs(codeMetrics.narrow_width-codeMetrics.wide_width)<0.01,report.actual_code_fonts);
 const fontRequests=report.runs.flatMap(run=>run.font_requests),fontResponses=report.runs.flatMap(run=>run.requests.filter(request=>request.path===fontPath));
 check('all font requests stay same origin and serve the verified TTF bytes',fontRequests.length>0&&fontRequests.every(request=>request.same_origin)&&fontResponses.every(response=>response.status===200&&response.content_type==='font/ttf'&&response.sha256===officialSha),{font_requests:fontRequests,font_responses:fontResponses});
 const entries=[...new Set(report.runs.flatMap(run=>run.requests.filter(request=>/^\/assets\/main-.*\.js$/.test(request.path)).map(request=>request.path)))];
 report.tested_compiled_entries=entries;
 check('both scenarios use one compiled build with no JavaScript or transport errors',entries.length===1&&report.runs.every(run=>run.requests.some(request=>request.path===entries[0])&&!run.javascript_errors.length&&!run.transport_errors.length)&&hash(await fs.readFile(manifestPath))===report.build_manifest_sha256);
 await installed.context.close();await stopServer(installed.server.child);report.passed=true;
}catch(error){report.failure={message:String(error),stack:error.stack};console.error(error.stack);}
finally{
 releaseFont?.();if(browser)await browser.close();for(const child of children)await stopServer(child);
 const after=await hashInputs(),unchanged=JSON.stringify(before)===JSON.stringify(after);
 report.source_data_unchanged=unchanged;report.source_data_hashes=before;
 report.checks.push({name:'original databases and imported source files remain byte-identical',passed:unchanged});
 console.log((unchanged?'PASS ':'FAIL ')+'original databases and imported source files remain byte-identical');
 report.passed=report.passed&&unchanged&&report.checks.length===12&&report.checks.every(item=>item.passed);
 await fs.writeFile(path.join(out,'font-browser-acceptance.json'),JSON.stringify(report,null,2)+'\n');await fs.rm(temp,{recursive:true,force:true});
 console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,report:path.join(out,'font-browser-acceptance.json')}));if(!report.passed)process.exitCode=1;
}
