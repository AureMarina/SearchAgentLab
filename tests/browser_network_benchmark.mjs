/** Browser-cold HTTP benchmark; only reads a temporary copy of saved data.
 * node tests/browser_network_benchmark.mjs --data-dir DATA --out report.json
 * Optional --source-root ROOT --hash '#/experiments' --trials 3 --delays 0,100
 * QA_CHROMIUM_PATH selects an already-installed Chromium; Playwright is dev-only.
 */
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {spawn} from 'node:child_process';import {createRequire} from 'node:module';import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);let playwright;
try{playwright=require('playwright');}catch{if(!process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES)throw Error('Install playwright as a development tool.');playwright=require(path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,'playwright'));}
const args={};for(let i=2;i<process.argv.length;i+=2)args[process.argv[i].replace(/^--/,'')]=process.argv[i+1];
if(!args['data-dir']||!args.out)throw Error('--data-dir and --out are required.');
const source=path.resolve(args['source-root']||path.join(path.dirname(fileURLToPath(import.meta.url)),'..')),out=path.resolve(args.out);
const hash=args.hash||'#/experiments',trials=Number(args.trials||3),delays=(args.delays||'0,100').split(',').map(Number);
if(!Number.isInteger(trials)||trials<1||trials>20||delays.some(x=>!Number.isFinite(x)||x<0||x>1000))throw Error('Invalid trial count or artificial delay.');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'searchagent-network-'));const data=path.join(temp,'data');fs.cpSync(path.resolve(args['data-dir']),data,{recursive:true});
const report={hash,transport:'Native Chromium and real HTTP LabServer',data:'Scratch copy of supplied saved workspace; no synthetic model results',cache:'New browser context each trial; server already started',ready_definition:'MutationObserver records navigation-relative performance.now() when #s-app aria-busy becomes false',delay_definition:'Artificial wait before every browser HTTP request; not measured production RTT, no bandwidth limit',runs:[]};
let browser,server;
try{
 const code=`import sys\nfrom searchagent_lab.store import Store\nfrom searchagent_lab.server import LabServer\nwith Store(sys.argv[1]) as store,LabServer(('127.0.0.1',0),store) as server:\n print('QA_SERVER_URL=http://127.0.0.1:'+str(server.server_address[1]),flush=True)\n server.serve_forever()\n`;
 server=spawn(process.env.QA_PYTHON||'python',['-u','-c',code,data],{cwd:source,stdio:['ignore','pipe','pipe']});let logs='';server.stderr.on('data',b=>{logs+=String(b);});
 const base=await new Promise((resolve,reject)=>{let buffer='';server.stdout.on('data',b=>{buffer+=String(b);const m=buffer.match(/QA_SERVER_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(m)resolve(m[1]);});server.once('exit',c=>reject(Error('Server exited '+c+' '+logs)));setTimeout(()=>reject(Error('Server startup timeout '+logs)),10000).unref();});
 browser=await playwright.chromium.launch({headless:true,...(process.env.QA_CHROMIUM_PATH?{executablePath:process.env.QA_CHROMIUM_PATH}:{}),args:['--no-sandbox','--disable-dev-shm-usage','--no-zygote','--disable-gpu','--font-render-hinting=none']});report.browser=browser.version();
 for(const delay of delays)for(let trial=1;trial<=trials;trial++){
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();const requests=[],errors=[],failures=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('requestfailed',r=>failures.push({url:r.url().replace(base,''),error:r.failure()?.errorText}));
  page.on('requestfinished',async r=>{const response=await r.response();requests.push({url:r.url().replace(base,''),type:r.resourceType(),status:response.status(),body_bytes:Number(response.headers()['content-length']||0),content_encoding:response.headers()['content-encoding']||'',timing:r.timing()});});
  if(delay)await page.route('**/*',async route=>{if(route.request().url().startsWith(base))await new Promise(r=>setTimeout(r,delay));await route.continue();});
  await page.addInitScript(()=>{window.qaReady=null;window.qaLongTasks=[];new PerformanceObserver(list=>{window.qaLongTasks.push(...list.getEntries().map(x=>({start_ms:x.startTime,duration_ms:x.duration})));}).observe({type:'longtask',buffered:true});const observer=new MutationObserver(()=>{if(window.qaReady===null&&document.querySelector('#s-app[aria-busy="false"]')){window.qaReady=performance.now();observer.disconnect();}});observer.observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-busy']});});
  await page.goto(base+'/'+hash,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>window.qaReady!==null,null,{timeout:20000});await page.waitForLoadState('networkidle');
  const metrics=await page.evaluate(()=>({ready_ms:window.qaReady,long_tasks:window.qaLongTasks,dom_nodes:document.getElementsByTagName('*').length,resources:performance.getEntriesByType('resource').map(r=>({url:r.name,start_ms:r.startTime,duration_ms:r.duration,transfer_bytes:r.transferSize,encoded_bytes:r.encodedBodySize,decoded_bytes:r.decodedBodySize}))}));for(const r of metrics.resources)r.url=r.url.replace(base,'');
  const row={trial,artificial_delay_ms:delay,title:await page.locator('h1').innerText(),http_requests:requests.length,api_requests:requests.filter(r=>r.url.startsWith('/api/')).length,response_body_bytes:requests.reduce((s,r)=>s+r.body_bytes,0),requests,errors,failures,...metrics};report.runs.push(row);console.log(JSON.stringify({trial,delay,ready_ms:Math.round(row.ready_ms*10)/10,http_requests:row.http_requests,response_body_bytes:row.response_body_bytes,errors}));await context.close();
 }
 const median=a=>{a.sort((x,y)=>x-y);return a.length%2?a[(a.length-1)/2]:(a[a.length/2-1]+a[a.length/2])/2;};report.summary=delays.map(delay=>{const rows=report.runs.filter(r=>r.artificial_delay_ms===delay);return {artificial_delay_ms:delay,trial_count:rows.length,median_ready_ms:Math.round(median(rows.map(r=>r.ready_ms))*10)/10,http_request_count:rows[0].http_requests,api_request_count:rows[0].api_requests,response_body_bytes:rows[0].response_body_bytes};});
}catch(e){report.error=e.stack;process.exitCode=1;console.error(e.stack);}
finally{await browser?.close();server?.kill('SIGTERM');fs.mkdirSync(path.dirname(out),{recursive:true});fs.writeFileSync(out,JSON.stringify(report,null,2));fs.rmSync(temp,{recursive:true,force:true});}
