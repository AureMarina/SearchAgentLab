import { mountInspector } from './inspector/workbench.js';
import { mountMethods } from './methods/workspace.js';
/* SearchAgent Lab — dependency-free UI. All imported text is escaped.
   No external requests, model execution, chat assistant, or generated diagnoses. */
'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = x => typeof x === 'string' ? x : JSON.stringify(x, null, 2);
const shown = x => x === null || x === undefined || x === '' ? '未记录' : String(x);
const pct = x => x == null ? '—' : (x * 100).toFixed(2) + '%';
const num = (x, n = 3) => x == null ? '—' : Number(x).toFixed(n);
const datasetNames = obj => { const order=['NQ','TriviaQA','PopQA','HotpotQA','2Wiki','MuSiQue','Bamboogle']; return Object.keys(obj).sort((a,b)=>(order.includes(a)?order.indexOf(a):99)-(order.includes(b)?order.indexOf(b):99)||a.localeCompare(b)); };
const count = x => x == null ? '—' : Number(x).toLocaleString('en-US');
const shortHash = x => x ? x.slice(0, 10) : 'unknown';
const statusText = {running:'进行中', completed:'已完成', failed:'已中断', cancelled:'已取消', stopped:'已停止', ok:'正常结束', error:'运行异常', timeout:'超时', correct:'正确', incorrect:'错误', unscored:'未评分', unknown:'未记录'};
const groupText = {rescued:'A 错 → B 对', harmed:'A 对 → B 错', both_correct:'都正确', both_wrong:'都错误', unscored:'未评分', unknown:'未记录'};
const categoryText = {undetermined:'尚未定位', wrong_query:'检索目标 / 中间实体错误', missing_evidence:'缺失必要证据', wrong_reasoning:'证据使用 / 推理错误', early_final:'过早输出最终答案', parsing:'答案解析问题', scoring:'评分规则问题', runtime:'运行 / 工具异常', other:'其他'};
const state = {project:'',experiment:'',projectKey:'',matrixDataset:'',matrixToken:0,epoch:0, listEpoch:0, traceEpoch:0, session:null, overview:null, run:'', panel:'', evalChoices:{}, trace:null, evaluation:null, traceTab:'steps', step:0, play:null, auto:null, lossKey:'', rewardKey:'', filter:'', dataset:'', query:'', offset:0, compareData:null, compareGroup:'', compareQuery:'', compareVisible:100, lastTrace:''};
let toastTimer;
async function api(path, params = {}, options) {
  const url = '/api/' + path + (Object.keys(params).length ? '?' + new URLSearchParams(params) : '');
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '本地请求失败');
  return data;
}
function href(page, params = {}) { return '#/' + page + (Object.keys(params).length ? '?' + new URLSearchParams(params) : ''); }
function navigate(page, params = {}) { location.hash = href(page, params); }
function routeInfo() { const [path, q] = (location.hash.slice(1) || '/projects').split('?'); return {page:path.replace(/^\//,''), q:new URLSearchParams(q || '')}; }
function toast(text) { $('#toast').textContent = text; $('#toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(()=>$('#toast').classList.remove('show'), 3300); }
function badge(text, type = 'neutral') { return `<span class="badge ${esc(type)}">${esc(text)}</span>`; }
function setDemo(value) { $('#demo-badge').classList.toggle('hidden', !value); }
function stopPlay() { clearInterval(state.play); state.play = null; const btn = $('#play-trace'); if (btn) btn.textContent = '▷ 顺序回放'; }
function modal(title, html) { $('#modal-title').textContent = title; $('#modal-body').innerHTML = html; if (!$('#modal').open) $('#modal').showModal(); }
function errorBox(message) { return `<div class="error-box"><strong>这条记录暂时无法显示</strong><p>${esc(message)}</p><button class="button" id="retry-page">重新读取</button></div>`; }
function option(value, label, selected) { return `<option value="${esc(value)}" ${value === selected ? 'selected' : ''}>${esc(label)}</option>`; }
function evalLabel(e) { return `${e.checkpoint?.name || e.checkpoint_id} · ${e.created_at?.slice(5,16).replace('T',' ')} · ${statusText[e.status] || e.status}`; }
function metricKeys(data) { return [...new Set(data.flatMap(row => Object.keys(row).filter(k => k !== 'step')))]; }
function metricAt(data, step, key) { return data.find(x => x.step === step)?.[key] ?? null; }
function currentEval(cp) { const chosen = state.evalChoices[`${state.panel}:${cp.id}`]; return cp.evaluations.find(e => e.id === chosen) || cp.evaluations[0] || null; }

function helpModal() {
  modal('接入你的训练日志与评测轨迹', `<div class="note-box">这是独立的日志查看器，不会修改 veRL、search_agent harness、模型请求、奖励或评分器。它不负责启动训练/评测。</div>
  <h3>已有结果：先转换，再导入</h3><p>将原始结果显式转换成包内 docs/DATA_FORMAT.md 规定的 JSON / JSONL；原始评分和工具内容保持不变。无法恢复的输入要留空，不能用最终 messages 反向拼造。</p><pre>python -m searchagent_lab import bundle.json --data-dir ./workspace
python -m searchagent_lab serve --data-dir ./workspace --port 8765</pre>
  <h3>训练曲线：记录原始字段</h3><pre>from searchagent_lab import Recorder

with Recorder("./workspace") as recorder:
    recorder.log_metrics("run-001", step, {
        "actor/pg_loss": actual_loss,
        "train/reward": actual_reward,
    })</pre><p>run ID 必须先登记。按原日志语义选择曲线字段，不把 policy loss 改称其他损失。仅 rank 0 写公共指标。SDK 与离线导入可在同一台机器共享 SQLite 工作区。</p>
  <h3>两个页面</h3><p>总览：loss/reward、checkpoint 结果与评测记录。轨迹：模型实际输入、输出、工具原始返回、模型可见内容、评分与人工标注。左右方向键切换已保存的步骤；回放不会重新执行。</p>
  <h3>服务器上运行</h3><pre>ssh -L 8765:127.0.0.1:8765 user@your-server</pre><p>在浏览器打开本地 127.0.0.1:8765。当前版本只绑定回环地址，不提供公网托管、多用户鉴权或任务调度。</p>`);
}
function showProtocol(ev) {
  modal('评测协议与可复现标识', `<div class="note-box">成绩属于这一条独立评测记录。不同记录不拼接结果；未记录的协议字段不能默认相同。</div><h3>评测记录</h3><pre>${esc(ev.id)}</pre><h3>题单 SHA256</h3><pre>${esc(ev.panel_hash)}</pre><h3>协议 SHA256</h3><pre>${esc(ev.protocol_hash)}</pre><h3>原始协议</h3><pre>${esc(pretty(ev.protocol))}</pre><p>协议完整：${ev.protocol_complete?'基础字段已登记':'否；包含 unknown'}。扩展复现字段：${ev.readiness?.complete?'齐全':'不完整'}${ev.readiness?.missing?.length?'（'+esc(ev.readiness.missing.join(', '))+'）':''}。工具预算是上限，不是必须用满的调用次数。</p>`);
}
function emptyWorkspace() {
  return `<section class="card empty-workspace"><div class="eyebrow">YOUR EVIDENCE STARTS HERE</div><h1>工作区还没有评测记录</h1><p>导入训练日志与固定题单后，这里会出现 loss / reward 曲线和 checkpoint 总表。页面不自动把缺失数据当成 0，也不会生成假成绩。</p><pre>python -m searchagent_lab import bundle.json --data-dir ./workspace</pre><p>先试用合成演示数据（请使用独立目录）：</p><pre>python -m searchagent_lab demo --data-dir ./demo_workspace --port 8766</pre><button class="button primary" id="empty-help">查看接入说明</button></section>`;
}
let referenceEvidenceCache;
async function referenceEvidence() {
  if (referenceEvidenceCache !== undefined) return referenceEvidenceCache;
  try {
    const response = await fetch('/reference_evidence.json');
    referenceEvidenceCache = response.ok ? await response.json() : {};
  } catch (_) { referenceEvidenceCache = {}; }
  return referenceEvidenceCache;
}
function sampleLink(panelId, sampleId, project = '') {
  return href('questions', {panel: panelId, sample: sampleId, ...(project ? {project} : {})});
}
async function renderQuestionWorkspace(epoch, q) {
  const panelId = q.get('panel') || state.panel || state.session.panels[0]?.id;
  if (!panelId) { $('#app').innerHTML = '<section class="card empty-workspace"><h2>尚未导入固定题单</h2><p>导入真实题目后，问题与答案会出现在这里。</p></section>'; return; }
  const panel = await api('panel', {id: panelId}); if (epoch !== state.epoch) return;
  const refs = await referenceEvidence(); if (epoch !== state.epoch) return;
  state.panel = panelId;
  const project = q.get('project') || state.project || '';
  const requested = q.get('sample');
  const selected = panel.samples.find(s => s.id === requested) || panel.samples[0];
  const evidence = refs[selected?.id] || [];
  const sourceMeta = selected?.metadata || {};
  const notice = panel.isolation?.status === 'not_checked' ? '训练数据隔离仍待补证' : auditLabel(panel.isolation);
  $('#app').innerHTML = `<div class="page-heading inspector-heading"><div><div class="eyebrow">FIXED QUESTIONS / ${esc(panel.version)}</div><h1>固定题目与参考答案</h1><p class="subtitle">先按题号查看 Groundtruth。模型输出和搜索过程只在真实评测导入后显示。</p></div><a class="button" href="${project ? href('overview',{project,panel:panelId}) : href('projects')}">← ${project ? '项目总览' : '研究项目'}</a></div>
  <div class="panel-toolbar"><span class="gt-mini">GT</span><strong>${esc(panel.name)}</strong><span class="panel-meta">${panel.size} 题 · SHA256 <code>${esc(shortHash(panel.fingerprint))}</code></span><span class="grow"></span><span class="isolation-state">${esc(notice)}</span></div>
  <div class="inspector-grid groundtruth-workspace"><aside class="card sample-panel"><div class="sample-top"><h2>固定题目 <span class="muted">(${panel.samples.length})</span></h2><input id="groundtruth-search" placeholder="搜索题目或原始题号" aria-label="搜索固定题目"></div><div class="sample-list" id="groundtruth-list"></div></aside>
  <div class="trace-main"><section class="card question-card"><div class="question-id">${esc(selected.dataset)} / <span class="mono">${esc(sourceMeta.source_id || selected.id)}</span></div><h2>${esc(selected.question)}</h2></section><section class="card reference-section"><h2>标准答案与可接受别名</h2><p class="groundtruth-primary">${esc(selected.answers[0])}</p>${selected.answers.length > 1 ? `<p class="side-note">其他可接受答案：${esc(selected.answers.slice(1).join(' / '))}</p>` : ''}<h3>参考证据</h3><p class="side-note">以下是原始数据的支持段落或固定检索语料返回的文本，不是模型实际搜索轨迹。</p>${evidence.length ? evidence.map((e,i)=>`<details class="reference-passage"><summary>${i+1}. ${esc(e.title || '参考段落')} <small>${esc(e.origin || '')}</small></summary><p>${esc(e.text || '')}</p></details>`).join('') : '<div class="empty-small">尚未保存可核对的参考段落。</div>'}<h3>来源信息</h3><dl class="kv"><dt>原始划分</dt><dd>${esc(sourceMeta.source_split || '未记录')}</dd><dt>数据版本</dt><dd>${esc(sourceMeta.source_revision || '未记录')}</dd></dl></section></div>
  <aside class="score-panel"><section class="card side-card"><h2>当前模型结果</h2><p class="side-note">该题尚无当前 checkpoint 的评测记录。</p><dl class="kv"><dt>模型答案</dt><dd>未评测</dd><dt>搜索调用</dt><dd>未记录</dd><dt>评分</dt><dd>未记录</dd></dl></section><section class="card side-card"><h2>数据接入状态</h2><p class="side-note">题单已接入。项目、训练日志与评测记录分别登记；缺失内容不补零，也不生成步骤。</p></section></aside></div>`;
  function list() {
    const term = ($('#groundtruth-search').value || '').trim().toLocaleLowerCase();
    const items = panel.samples.filter(s => !term || `${s.id} ${s.question} ${s.dataset}`.toLocaleLowerCase().includes(term));
    $('#groundtruth-list').innerHTML = items.map(s => `<a class="sample-item ${s.id===selected.id?'active':''}" href="${sampleLink(panelId,s.id,project)}"><span class="outcome-icon unknown">·</span><div><div class="sample-question">${esc(s.question)}</div><div class="sample-meta">${esc(s.dataset)} · 未评测</div><div class="secondary-line mono">${esc(s.metadata?.source_id || s.id)}</div></div></a>`).join('') || '<div class="empty-small">没有匹配的题目。</div>';
  }
  $('#groundtruth-search').oninput = list; list();
}
function chartSVG(points, key, checkpoints, type) {
  const valid = points.filter(x => typeof x[key] === 'number' && Number.isFinite(x[key]));
  if (!valid.length) return `<div class="empty-small">此字段尚未记录。缺失值不会补零。</div>`;
  const W=640,H=210,L=43,R=16,T=29,B=34;
  const maxX=Math.max(1,...points.map(x=>x.step)), minY=Math.min(0,...valid.map(x=>x[key])), rawMax=Math.max(...valid.map(x=>x[key]));
  const maxY=rawMax===minY?minY+1:rawMax+(rawMax-minY)*.1;
  const x=v=>L+v/maxX*(W-L-R), y=v=>T+(maxY-v)/(maxY-minY)*(H-T-B);
  let grid='';
  for(let i=0;i<5;i++){ const v=minY+(maxY-minY)*i/4; grid+=`<line class="chart-grid" x1="${L}" y1="${y(v)}" x2="${W-R}" y2="${y(v)}"/><text class="chart-label" text-anchor="end" x="${L-9}" y="${y(v)+3}">${v.toFixed(2)}</text>`; }
  for(let i=0;i<5;i++){ const v=maxX*i/4; grid+=`<text class="chart-label" text-anchor="middle" x="${x(v)}" y="${H-17}">${v>=1000?(v/1000).toFixed(1)+'k':v.toFixed(0)}</text>`; }
  const marks=checkpoints.filter(cp=>cp.step>0&&cp.step<=maxX).map(cp=>`<g class="cp-svg" data-mark-cp="${esc(cp.id)}"><title>${esc(cp.name)} · step ${cp.step}</title><rect x="${x(cp.step)-12}" y="${T-23}" width="24" height="${H-B-T+23}" fill="transparent"/><line class="cp-marker" x1="${x(cp.step)}" y1="${T-7}" x2="${x(cp.step)}" y2="${H-B}"/><text class="marker-label" x="${x(cp.step)}" y="${T-15}" text-anchor="${cp.step===maxX?'end':'middle'}">ckpt-${cp.step}</text></g>`).join('');
  // Missing measurements break the line instead of inventing zeros.
  let d='', drawing=false;
  for(const row of points){const val=row[key]; if(typeof val!=='number'||!Number.isFinite(val)){drawing=false;continue;} d+=(drawing?' L':' M')+x(row.step).toFixed(2)+','+y(val).toFixed(2);drawing=true;}
  const dots=valid.filter((_,i)=>i%Math.max(1,Math.ceil(valid.length/300))===0).map(row=>`<circle cx="${x(row.step)}" cy="${y(row[key])}" r="5" fill="transparent"><title>step ${row.step} · ${esc(key)} = ${row[key]}</title></circle>`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(key)} 随训练 step 变化">${grid}<path class="chart-line ${type}" d="${d}"/>${dots}${marks}<text class="chart-label" text-anchor="middle" x="${W/2}" y="${H-1}">Training step</text></svg>`;
}
function chartCard(title, key, data, checkpoints, type, keys) {
  const valid=data.filter(x=>typeof x[key]==='number'); const latest=valid.at(-1);
  return `<section class="card chart-card"><div class="chart-heading"><div><div class="chart-title"><span class="legend-dot ${type}"></span>${esc(title)}</div><div class="chart-value">${num(latest?.[key],4)}</div><div class="chart-sub">${latest?'最新已记录值 · step '+latest.step:'尚无记录'}</div></div><select class="metric-select" id="${type}-metric" aria-label="${esc(title)} 字段">${keys.map(k=>option(k,k,key)).join('')}</select></div><div class="chart-container">${chartSVG(data,key,checkpoints,type)}</div></section>`;
}
async function renderProjects(epoch) {
  setDemo(state.session.projects.some(p=>p.demo));
  const projects=state.session.projects, panels=state.session.panels;
  $('#app').innerHTML=`<div class="home-heading"><div class="eyebrow">RESEARCH WORKSPACE</div><h1>从一个研究问题开始。</h1><p>先选项目，再看实验。配置、训练曲线和评测结论分别归档。</p></div>
  <div class="project-grid">${projects.map((p,i)=>`<a class="project-card project-${esc(p.id)}" href="${href('overview',{project:p.id})}" data-project-card="${esc(p.id)}"><div class="project-card-top"><span class="project-symbol">${p.id==='self-play'?'↻':p.id==='latent'?'▧':p.id==='entropy'?'∑':'◇'}</span><span class="project-index">0${i+1} ↗</span></div><h2>${esc(p.name)}</h2><p>${esc(p.description||'独立研究项目')}</p><div class="project-card-bottom"><span><b>${p.experiment_count}</b> 实验方案</span><span><b>${p.run_count}</b> 运行</span></div></a>`).join('') || `<div class="card empty-workspace"><h2>尚未登记研究项目</h2><p>导入 projects / experiments / runs 后显示；不自动创建研究记录或假成绩。</p><button class="button primary" id="empty-help">查看接入格式</button></div>`}</div>
  <div class="section-heading"><div><h2>共享 Groundtruth</h2><p class="subtitle">一次选定；所有 checkpoint 读取同一份只读 validation。</p></div><span class="badge neutral">题单共享，结果分开</span></div>
  <div class="groundtruth-list">${panels.map(p=>`<section class="card groundtruth-card"><div class="groundtruth-mark">GT</div><div class="grow"><div class="gt-title"><h3>${esc(p.name)}</h3>${badge('只读','blue')}${badge(p.demo?'合成演示':'隔离状态见报告',p.demo?'demo':'warning')}</div><div class="gt-datasets">${datasetNames(p.datasets).map(ds=>`<span>${esc(ds)} <b>${p.datasets[ds]}</b></span>`).join('')}</div><div class="secondary-line mono">SHA256 ${esc(shortHash(p.fingerprint))}… · ${esc(p.version)}</div></div><div class="gt-size"><strong>${p.size}</strong><span>固定题目</span></div><a class="button" href="${href('questions',{panel:p.id})}">进入逐题工作区 ↗</a></section>`).join('') || `<section class="card empty-small">尚未导入真实 Groundtruth。请先核对训练与正式测试清单，再导入固定文件；不自动抽题。</section>`}</div>
  <div class="note-box home-note">${projects.some(p=>p.demo)?'当前为合成演示工作区：项目、问题、分数与轨迹仅用于界面测试，不是真实实验结果。':'Groundtruth 的只读性不等于隔离已验证。训练、蒸馏、self-play 生成材料与正式测试清单需分别审核。'}<br>选题之后不按模型表现换题；新增题单使用新的版本与哈希。</div>
  <div class="home-flow"><span>研究项目</span><i>→</i><span>实验方案</span><i>→</i><span>训练运行</span><i>→</i><span>Checkpoint</span><i>→</i><span>固定 validation</span><i>→</i><span>单题证据</span></div>`;
  $('#empty-help')?.addEventListener('click',helpModal);
}
function updateResearchNav() {
  $('#project-nav').innerHTML=state.session.projects.map(p=>`<a class="research-nav ${state.project===p.id?'selected':''}" href="${href('overview',{project:p.id})}"><span class="research-dot"></span>${esc(p.name)}<small>${p.run_count}</small></a>`).join('');
  $('#nav-overview').href=state.project?href('overview',{project:state.project,...(state.run?{run:state.run}:{})}):href('projects');
  $('#nav-trace').href=href('trace');
}
async function renderOverview(epoch,q) {
  const sess=state.session;
  let project=q.get('project') || (q.get('run')?sess.runs.find(r=>r.id===q.get('run'))?.project_id:null) || state.project;
  if(!project){await renderProjects(epoch);return;}
  if(!sess.projects.some(p=>p.id===project))throw new Error('找不到该研究项目；不会回退到其他项目的数据。');
  const changed=project!==state.project;
  if(changed){state.run='';state.experiment='';state.lossKey='';state.rewardKey='';state.projectKey='';state.lastTrace='';$('#nav-trace').href=href('trace');}
  state.project=project;updateResearchNav();
  const catalog=await api('catalog',{project});if(epoch!==state.epoch)return;
  const requestedRun=q.get('run')?catalog.runs.find(r=>r.id===q.get('run')):null;
  if(q.get('run')&&!requestedRun)throw new Error('所选运行不属于当前项目；已阻止加载其他项目记录。');
  const model=q.get('model')||requestedRun?.base_model||catalog.models[0]||'';
  const eligibleRuns=catalog.runs.filter(r=>!model||r.base_model===model);
  const exps=catalog.experiments.filter(e=>!model||eligibleRuns.some(r=>r.experiment_id===e.id));
  const expId=q.get('experiment')||requestedRun?.experiment_id||(!changed?state.experiment:'')||exps[0]?.id;
  let exp=exps.find(e=>e.id===expId)||exps[0];
  const runs=eligibleRuns.filter(r=>r.experiment_id===exp?.id);
  const run=requestedRun||runs.find(r=>r.id===state.run)||runs[0];
  if(requestedRun&&q.get('experiment')&&requestedRun.experiment_id!==q.get('experiment'))throw new Error('运行与实验方案不匹配。');
  if(requestedRun&&q.get('model')&&requestedRun.base_model!==q.get('model'))throw new Error('运行与所选底座模型不匹配。');
  state.experiment=exp?.id||'';state.run=run?.id||'';
  state.panel=q.get('panel')||(sess.panels.some(p=>p.id===state.panel)?state.panel:catalog.project.default_panel_id)||sess.panels[0]?.id||'';
  if(!sess.panels.some(p=>p.id===state.panel)&&sess.panels.length)state.panel=sess.panels[0].id;
  const filters=`<section class="scope-bar"><label>底座模型<select id="model-select">${catalog.models.map(m=>option(m,m,model)).join('')}</select></label><label>实验方案<select id="experiment-select">${exps.map(e=>option(e.id,e.name,state.experiment)).join('')}</select></label><label>训练运行<select id="run-select" aria-label="训练运行">${runs.map(r=>option(r.id,r.name+(r.seed!=null?' · seed '+r.seed:''),state.run)).join('')}</select></label><label>只读 Validation<select id="panel-select" aria-label="固定评测集">${sess.panels.map(p=>option(p.id,`${p.name} · ${p.size} 题`,state.panel)).join('')}</select></label></section>`;
  const heading=`<div class="page-heading"><div><div class="eyebrow">RESEARCH PROJECT / ${esc(catalog.project.name.toUpperCase())}</div><h1>${esc(catalog.project.name)} <span class="h1-light">实验看板</span></h1><p class="subtitle">只显示当前项目的实验、运行与 checkpoint。相同步数不等于相同训练预算。</p></div><a class="button" href="${href('projects')}">← 研究项目</a></div>`;
  if(!run||!state.panel){
    setDemo(catalog.project.demo);
    const panel=state.panel?await api('panel',{id:state.panel}):null;if(epoch!==state.epoch)return;
    const rows=panel?.samples||[];
    $('#app').innerHTML=heading+`<div class="scope-evidence"><span>${esc(catalog.project.name)}</span><span class="muted">实验、运行尚未登记</span><span class="grow"></span>${badge('题单已接入','blue')}</div>
    <div class="setup-status"><span>✓ 固定题单 ${rows.length} 题</span><span>○ 训练日志未接入</span><span>○ Checkpoint 未登记</span><span>○ 评测轨迹未接入</span></div>
    <div class="charts"><section class="card chart-card"><div class="chart-title"><span class="legend-dot loss"></span>训练 Loss</div><div class="chart-empty">尚未接入训练日志</div></section><section class="card chart-card"><div class="chart-title"><span class="legend-dot reward"></span>训练 Reward</div><div class="chart-empty">尚未接入训练日志</div></section></div>
    <div class="table-heading"><div><h2>Checkpoint · 固定题答对数</h2><p class="subtitle">有真实运行后，已保存的 checkpoint 会出现在这里。</p></div><button class="button" id="empty-import-help">登记与导入说明</button></div>
    <div class="table-wrap"><table class="results-table"><thead><tr><th>Checkpoint</th><th>2WikiMQA</th><th>MuSiQue</th><th>TriviaQA</th><th>总答对数</th><th>评测记录</th></tr></thead><tbody><tr><td colspan="6" class="empty-small">尚未登记 checkpoint。没有生成虚构训练步数或成绩。</td></tr></tbody></table></div>
    <section class="matrix-section"><div class="section-heading"><div><h2>固定题目与 Groundtruth</h2><p class="subtitle">先看真实题目，模型结果在评测导入后填入对应位置。</p></div><a class="button" href="${href('questions',{panel:state.panel,project})}">进入逐题工作区 ↗</a></div>
    <div class="table-wrap"><table class="question-matrix"><thead><tr><th>固定题目</th><th>Groundtruth</th><th>当前模型结果</th></tr></thead><tbody>${rows.map(s=>`<tr><td class="matrix-question"><a href="${sampleLink(state.panel,s.id,project)}"><strong>${esc(s.metadata?.source_id||s.id)}</strong><div>${esc(s.question)}</div></a></td><td class="matrix-gold">${esc(s.answers.join(' / '))}</td><td class="matrix-missing">未评测</td></tr>`).join('')||'<tr><td colspan="3" class="empty-small">尚未导入固定题目。</td></tr>'}</tbody></table></div><p class="matrix-footnote">标准答案仅供评分与事后检查，不属于模型实际轨迹。</p></section>`;
    $('#empty-import-help').onclick=helpModal;
    return;
  }
  const data=await api('overview',{project,experiment:state.experiment,run:state.run,panel:state.panel});if(epoch!==state.epoch)return;
  state.overview=data;state.catalog=catalog;setDemo(data.run.demo||data.panel.demo);updateResearchNav();
  const keys=metricKeys(data.metrics);
  if(!keys.includes(state.lossKey))state.lossKey=keys.find(k=>k.includes('loss'))||keys[0]||'';
  if(!keys.includes(state.rewardKey))state.rewardKey=keys.find(k=>k.includes('reward'))||keys[1]||'';
  if(!keys.includes(state.projectKey))state.projectKey=keys.find(k=>k!==state.lossKey&&k!==state.rewardKey)||'';
  const allEvals=data.checkpoints.flatMap(cp=>cp.evaluations);
  $('#app').innerHTML=heading+filters+`
  <div class="scope-evidence"><span>${esc(data.experiment.name)}</span><span class="mono">${esc(run.id)}</span><button class="link-button" id="experiment-notes">实验目的、配置与结论 ↗</button><span class="grow"></span>${badge(statusText[run.status],run.status==='completed'?'success':'running')}</div>
  <div class="charts" id="charts"></div>
  <div class="project-metric-definition" id="metric-definition"></div>
  <div class="table-heading"><div><h2>Checkpoint · 固定题答对数</h2><p class="subtitle">默认最近一次记录，不选最好成绩。训练 reward 与 validation EM 分开。</p></div><div class="table-actions"><button class="button" id="compare-toggle">⇄ 对比 checkpoint</button><button class="button" id="cross-compare">选择其他实验</button></div></div>
  <div class="panel-toolbar"><span class="gt-mini">GT</span><strong>${esc(data.panel.name)}</strong>${badge('只读','blue')}<span class="panel-meta">${data.panel.size} 题 · SHA256 <code>${esc(shortHash(data.panel.fingerprint))}</code></span><button class="link-button" id="view-panel">题目与标准答案 ↗</button><span class="grow"></span><span class="isolation-state">${data.panel.demo?'合成题 · 非真实 validation':auditLabel(data.panel.isolation)}</span></div>
  <div id="compare-bar" class="compare-bar hidden"><label>A</label><select id="compare-a" aria-label="对比 A">${allEvals.map(e=>option(e.id,evalLabel(e),allEvals[0]?.id)).join('')}</select><label>B</label><select id="compare-b" aria-label="对比 B">${allEvals.map(e=>option(e.id,evalLabel(e),allEvals.at(-1)?.id)).join('')}</select><button id="compare-go" class="button primary" ${allEvals.length<2?'disabled':''}>查看同题对比</button></div>
  <div id="overview-protocol-notice"></div><div id="results-table"></div>
  <div class="table-footer"><span>答对数按 EM=1 计，运行异常另列；缺失与未评分不补零。</span><span>5–10 题用于诊断，不替代正式测试。</span></div>
  <section class="matrix-section"><div class="section-heading"><div><h2>固定题目 × Checkpoint</h2><p class="subtitle">同一行，永远是同一道题。点击单元格回放该 checkpoint 的完整轨迹。</p></div><span class="badge neutral" id="matrix-size"></span></div><div class="matrix-toolbar"><div class="dataset-tabs" id="matrix-datasets"></div><select id="matrix-filter" aria-label="题目变化筛选"><option value="">全部题目</option><option value="rescued">发生错 → 对</option><option value="harmed">发生对 → 错</option></select></div><div id="question-matrix"><div class="loading">正在读取固定题目…</div></div><p class="matrix-footnote">Groundtruth 仅供评分和事后检查；不是给 agent 的提示词，也不强制模型采用参考搜索路径。</p></section>`;
  bindScopes(catalog,model);renderCharts();renderResults();
  $('#view-panel').onclick=()=>showPanel(state.panel);
  $('#experiment-notes').onclick=()=>showExperiment(state.experiment);
  $('#compare-toggle').onclick=()=>$('#compare-bar').classList.toggle('hidden');
  $('#compare-go').onclick=()=>navigate('compare',{a:$('#compare-a').value,b:$('#compare-b').value});
  $('#cross-compare').onclick=()=>{const ev=currentEval(data.checkpoints.at(-1));ev?chooseComparison(ev):toast('请先导入本运行的评测记录。');};
  const datasets=datasetNames(data.panel.datasets);
  if(!datasets.includes(state.matrixDataset))state.matrixDataset=datasets.includes('HotpotQA')?'HotpotQA':datasets[0];
  $('#matrix-datasets').innerHTML=datasets.map(ds=>`<button class="dataset-tab ${state.matrixDataset===ds?'active':''}" data-matrix-dataset="${esc(ds)}">${esc(ds)} <small>${data.panel.datasets[ds]}</small></button>`).join('');
  $$('[data-matrix-dataset]').forEach(b=>b.onclick=()=>{state.matrixDataset=b.dataset.matrixDataset;$$('[data-matrix-dataset]').forEach(x=>x.classList.toggle('active',x===b));renderMatrix();});
  $('#matrix-filter').onchange=()=>renderMatrix();
  await renderMatrix();
}
function bindScopes(catalog,model){
  $('#model-select').onchange=e=>navigate('overview',{project:state.project,model:e.target.value,panel:state.panel});
  $('#experiment-select').onchange=e=>{state.run='';state.experiment=e.target.value;navigate('overview',{project:state.project,model,experiment:e.target.value,panel:state.panel});};
  $('#run-select').onchange=e=>navigate('overview',{project:state.project,run:e.target.value,panel:state.panel});
  $('#panel-select').onchange=e=>navigate('overview',{project:state.project,run:state.run,panel:e.target.value});
}
function renderCharts(){
  const d=state.overview;const keys=metricKeys(d.metrics);
  $('#charts').innerHTML=chartCard('训练 Loss',state.lossKey,d.metrics,d.checkpoints,'loss',keys)+chartCard('训练 Reward',state.rewardKey,d.metrics,d.checkpoints,'reward',keys)+(state.projectKey?chartCard(d.metric_definitions[state.projectKey]?.label||'项目指标',state.projectKey,d.metrics,d.checkpoints,'project',keys):'');
  $('#loss-metric').onchange=e=>{state.lossKey=e.target.value;renderCharts();};
  $('#reward-metric').onchange=e=>{state.rewardKey=e.target.value;renderCharts();};
  $('#project-metric')?.addEventListener('change',e=>{state.projectKey=e.target.value;renderCharts();});
  const def=d.metric_definitions[state.projectKey];
  $('#metric-definition').innerHTML=state.projectKey?`<span class="definition-label">指标口径</span><code>${esc(state.projectKey)}</code><span>${esc(def?.definition||'未登记具体定义、计算位置及归一化口径；不能仅凭指标名称解释。')}</span>${def?.unit?`<span class="muted">单位：${esc(def.unit)}</span>`:''}`:'';
  $$('[data-mark-cp]').forEach(el=>el.onclick=()=>{$$('.results-table tr').forEach(r=>r.classList.remove('focused'));const row=$(`[data-row-cp="${CSS.escape(el.dataset.markCp)}"]`);row?.classList.add('focused');row?.scrollIntoView({block:'nearest',behavior:'smooth'});});
}
function renderResults(){
  const d=state.overview;const datasets=datasetNames(d.panel.datasets);
  const measured=d.checkpoints.map(cp=>currentEval(cp)).filter(Boolean);
  const states=measured.map(e=>e.method_state).filter(Boolean);
  const methodSame=states.every(x=>x.status==='not_recorded')||(states.length===measured.length&&states.every(x=>x.status==='recorded')&&new Set(states.map(x=>x.fingerprint)).size<=1);
  const same=methodSame&&measured.every(e=>e.protocol_complete&&(e.readiness.complete||e.protocol.protocol_version!==2))&&new Set(measured.map(e=>e.protocol_hash)).size<=1;
  $('#overview-protocol-notice').innerHTML=same?'':`<div class="note-box warn">当前选定记录的协议或方法外部状态不一致/不完整；只展示各自成绩，不显示改善/退化标记。请检查协议原始记录。</div>`;
  $('#results-table').innerHTML=`<div class="table-wrap"><table class="results-table"><thead><tr><th>Checkpoint<small>当前运行保存点</small></th>${datasets.map(ds=>`<th>${esc(ds)}<small>答对 / ${d.panel.datasets[ds]} · EM</small></th>`).join('')}<th>总答对数<small>不代替宏平均</small></th><th>宏平均 EM<small>完整覆盖才显示</small></th><th>评测记录<small>独立保留每次执行</small></th><th>操作</th></tr></thead><tbody>${d.checkpoints.map(cp=>{
    const e=currentEval(cp);
    return `<tr data-row-cp="${esc(cp.id)}"><td><div class="checkpoint-name">${esc(cp.name)}</div><div class="secondary-line mono" title="${esc(cp.id)}">step ${cp.step}</div></td>${datasets.map(ds=>{const s=e?.stats.datasets[ds];return `<td>${s?.scored?`<button class="result-score" data-open-eval="${esc(e.id)}" data-dataset="${esc(ds)}">${s.answer_correct}<span class="denominator">/${s.expected}</span></button><div class="secondary-line">${pct(s.em)}${s.fully_scored?'':` · 仅评 ${s.scored}/${s.expected}`}</div>`:`<span class="muted">—</span><div class="secondary-line">${e?'未评分':'未评测'}</div>`}</td>`;}).join('')}<td class="total-count">${e?`<strong>${e.stats.answer_correct}<span>/${e.stats.expected}</span></strong><small>${e.stats.scored}/${e.stats.expected} 已评分</small>`:'—'}</td><td><strong>${pct(e?.stats.macro_em)}</strong><div class="secondary-line">${e?.stats.fully_scored?'完整覆盖':'未全覆盖'}</div></td><td>${e?`${badge(statusText[e.status],e.status==='completed'?'success':'warning')}<div class="secondary-line">${cp.evaluations.length} 次 · ${e.stats.errors} 异常</div>${cp.evaluations.length>1?`<select class="eval-select" data-cp-select="${esc(cp.id)}" aria-label="${esc(cp.name)} 评测记录">${cp.evaluations.map(ev=>option(ev.id,`${ev.id.split('-').at(-1)} · ${ev.created_at.slice(11,16)}`,e.id)).join('')}</select>`:`<span class="secondary-line mono">${esc(e.id.split('-').at(-1))}</span>`}<button class="link-button record-protocol" data-protocol-eval="${esc(e.id)}">协议 ${esc(shortHash(e.protocol_hash))}</button>`:'<span class="muted">尚未导入</span>'}</td><td>${e?`<button class="button small" data-open-eval="${esc(e.id)}" data-dataset="">轨迹 ↗</button>${cp.evaluations.length>1?`<button class="link-button repro-link" data-repro-cp="${esc(cp.id)}">重复评测检查</button>`:''}`:'—'}</td></tr>`;
  }).join('')}</tbody></table></div>`;
  $$('[data-open-eval]').forEach(el=>el.onclick=()=>navigate('trace',{evaluation:el.dataset.openEval,...(el.dataset.dataset?{dataset:el.dataset.dataset}:{})}));
  $$('[data-protocol-eval]').forEach(el=>el.onclick=()=>showProtocol(d.checkpoints.flatMap(cp=>cp.evaluations).find(e=>e.id===el.dataset.protocolEval)));
  $$('[data-cp-select]').forEach(el=>el.onchange=()=>{state.evalChoices[`${state.panel}:${el.dataset.cpSelect}`]=el.value;renderResults();renderMatrix();});
  $$('[data-repro-cp]').forEach(el=>el.onclick=()=>showRepro(d.checkpoints.find(cp=>cp.id===el.dataset.reproCp)));
}
async function renderMatrix(){
  const epoch=state.epoch,token=++state.matrixToken;const d=state.overview;
  const selections=Object.fromEntries(d.checkpoints.map(cp=>[cp.id,currentEval(cp)?.id]).filter(([,id])=>id));
  try{
    const data=await api('matrix',{project:state.project,run:state.run,panel:state.panel,dataset:state.matrixDataset,selections:JSON.stringify(selections)});
    if(epoch!==state.epoch||token!==state.matrixToken||!$('#question-matrix'))return;
    const filter=$('#matrix-filter').value;
    $('#matrix-filter').disabled=!data.comparable;
    const rows=data.rows.filter(row=>!filter||row.cells.some(c=>c?.change===filter));
    $('#matrix-size').textContent=`${state.matrixDataset} · 固定 ${data.rows.length} 题`;
    $('#question-matrix').innerHTML=`${!data.comparable?'<div class="note-box warn">协议或方法外部状态不一致：保留逐题证据，禁用权重变化的错→对/对→错归因。</div>':''}<div class="table-wrap matrix-wrap"><table class="question-matrix"><thead><tr><th>固定题目<small>原始 ID 与问题不变</small></th><th>Groundtruth<small>只读标准答案及别名</small></th>${data.columns.map(c=>`<th>${esc(c.checkpoint.name)}<small>${esc(c.evaluation?.id.split('-').at(-1)||'未评测')}${c.repeat_count>1?` · ${c.repeat_count} 次记录`:''}</small></th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr><td class="matrix-question"><strong>${esc(row.sample.id)}</strong><div>${esc(row.sample.question)}</div></td><td class="matrix-gold"><span class="gt-answer">${esc(row.sample.answers.join(' / '))}</span><button class="link-button" data-matrix-sample="${esc(row.sample.id)}">来源 / 参考证据</button></td>${row.cells.map((c,i)=>`<td>${c?`<button class="matrix-cell ${c.scores.em===1?'is-correct':c.scores.em===0?'is-wrong':'is-unscored'}" data-matrix-trace="${esc(c.id)}" data-matrix-eval="${esc(data.columns[i].evaluation.id)}" title="${esc(shown(c.parsed_answer))}"><span class="cell-status">${c.scores.em===1?'✓ 正确':c.scores.em===0?'× 错误':'· 未评分'}${c.change?`<em class="${c.change}">${c.change==='rescued'?'错→对':'对→错'}</em>`:''}</span><span class="cell-answer">${esc(shown(c.parsed_answer))}</span><span class="cell-bottom">${c.usage?.tool_calls==null?'搜索未记录':c.usage.tool_calls+' 次搜索'} · ${c.runtime_status==='ok'?'查看轨迹 ↗':esc(statusText[c.runtime_status]||c.runtime_status)}</span></button>`:'<span class="matrix-missing">— 未评测</span>'}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${data.columns.length+2}" class="empty-small">没有符合筛选的题目。</td></tr>`}</tbody><tfoot><tr><th>全题合计<small>${esc(state.matrixDataset)} · ${data.rows.length} 道固定题</small></th><td>答对数 / 固定题数</td>${data.columns.map(c=>{const s=c.evaluation?.stats.datasets[state.matrixDataset];return `<td><strong>${s?`${s.answer_correct} / ${s.expected}`:'—'}</strong><small>${s?`${s.scored}/${s.expected} 已评分`:''}</small></td>`;}).join('')}</tr></tfoot></table></div>`;
    $$('[data-matrix-trace]').forEach(b=>b.onclick=()=>navigate('trace',{evaluation:b.dataset.matrixEval,trajectory:b.dataset.matrixTrace,dataset:state.matrixDataset}));
    $$('[data-matrix-sample]').forEach(b=>b.onclick=()=>{const s=data.rows.find(r=>r.sample.id===b.dataset.matrixSample).sample;modal('Groundtruth · '+s.id,`<div class="note-box">只用于评分与事后诊断。参考链不是必须采用的唯一路径。</div><h3>${esc(s.question)}</h3><p>标准答案：${esc(s.answers.join(' / '))}</p><pre>${esc(pretty(s.metadata||{}))}</pre>`);});
  }catch(e){if(epoch===state.epoch&&$('#question-matrix'))$('#question-matrix').innerHTML=errorBox(e.message);}
}

function outcomeIcon(outcome){return `<span class="outcome-icon ${esc(outcome)}">${outcome==='correct'?'✓':outcome==='error'?'!':outcome==='incorrect'?'×':'·'}</span>`;}
function payload(label,value,error=false){
  if(value===undefined||value===null)return `<div class="payload-block"><div class="payload-label">${esc(label)}</div><div class="step-missing">未记录；不重建、不补造。</div></div>`;
  return `<div class="payload-block"><div class="payload-label">${esc(label)}<button class="payload-expand" type="button">展开 / 收起全文</button></div><pre class="payload ${error?'error-payload':''}">${esc(pretty(value))}</pre></div>`;
}
function stepCard(s, prefix='trace', open=false){
  const title=s.title||({model:'模型调用',tool:'工具调用',observation:'环境观察',final:'最终输出',event:'运行事件'}[s.kind]||s.kind);
  let content='';
  if(s.kind==='model') content+=payload('模型实际输入 · 原样保存',s.input)+payload('模型原始输出',s.output);
  else if(s.kind==='tool') content+=payload('工具名称与调用参数',s.tool_call)+payload('工具完整原始返回',s.tool_response)+payload('经过处理后 · 模型实际可见的内容',s.model_visible);
  else content+=payload('输入',s.input)+payload('输出 / 观察',s.output??s.observation);
  if(s.error)content+=payload('记录到的异常',s.error,true);
  if(s.metadata&&Object.keys(s.metadata).length)content+=`<details class="step-missing"><summary>其他原始字段 / 元数据</summary>${payload('Metadata',s.metadata)}</details>`;
  return `<details class="step-card ${s.status==='error'?'has-error':''}" id="${prefix}-step-${s.index}" data-step-index="${s.index}" ${open?'open':''}><summary><span class="step-number">${s.index}</span><span class="step-title">${esc(title)}</span><span class="step-kind">${esc(s.kind)}</span>${s.latency_ms!=null?`<span class="step-latency">${Math.round(s.latency_ms)} ms</span>`:''}</summary><div class="step-body">${content}</div></details>`;
}
function attachPayloadExpand(root=document){$$('.payload-expand',root).forEach(b=>b.onclick=()=>b.closest('.payload-block').querySelector('.payload').classList.toggle('expanded'));}
async function renderTrace(epoch,q){
  let id=q.get('evaluation');
  if(!id && state.lastTrace){location.hash=state.lastTrace;return;}
  if(!id){await renderQuestionWorkspace(epoch,q);return;}
  const ev=await api('evaluation',{id});if(epoch!==state.epoch)return;
  state.evaluation=ev;state.project=ev.project_id;state.experiment=ev.experiment_id;state.run=ev.checkpoint.run_id;updateResearchNav();state.panel=ev.panel_id;state.dataset=q.get('dataset')||'';state.filter=q.get('outcome')||'';state.query=q.get('q')||'';state.offset=0;state.trace=null;state.traceTab='steps';setDemo(ev.demo);
  state.lastTrace=href('trace',{evaluation:id,...(state.dataset?{dataset:state.dataset}:{})});$('#nav-trace').href='#/trace';
  $('#app').innerHTML=`<div class="page-heading inspector-heading"><div><div class="eyebrow">${esc(ev.project_id)} / ${esc(ev.experiment_id)} / ${esc(ev.run?.name||ev.checkpoint.run_id)}</div><h1>轨迹详情</h1><p class="subtitle">逐步检查实际输入、模型输出、工具返回与评分证据。</p></div><a class="button" href="${href('overview',{run:state.run,panel:state.panel})}">← 返回总览</a></div>
  <div class="inspector-toolbar"><span class="control-label">评测记录</span><span class="badge blue">${esc(ev.checkpoint.name)}</span><code class="muted">${esc(ev.id)}</code><span class="grow"></span><select id="trace-dataset" aria-label="筛选数据集">${option('','全部数据集',state.dataset)}${datasetNames(ev.panel.datasets).map(ds=>option(ds,`${ds} · ${ev.panel.datasets[ds]} 题`,state.dataset)).join('')}</select><button class="button small" id="trace-compare">⇄ 对比其他 checkpoint</button><a class="button small" href="/api/export/csv?id=${encodeURIComponent(id)}">导出 CSV</a></div>
  <div class="eval-summary"><span>题单 <strong>${esc(ev.panel.name)} ${esc(ev.panel.version)}</strong></span><span>轨迹 <strong>${ev.stats.recorded} / ${ev.stats.expected}</strong></span><span>宏平均 EM <strong>${pct(ev.stats.macro_em)}</strong></span><span>状态 ${badge(statusText[ev.status],ev.status==='completed'?'success':'warning')}</span><button class="link-button" id="trace-protocol">查看评分与运行协议 ↗</button></div>
  <div class="inspector-grid"><aside class="card sample-panel"><div class="sample-top"><h2>题目列表 <span id="sample-total" class="muted"></span></h2><input id="sample-search" placeholder="搜索问题 / Sample ID" value="${esc(state.query)}" aria-label="搜索题目"><div class="sample-filters" id="sample-filters"></div></div><div class="sample-list" id="sample-list"><div class="empty-small">正在读取题目…</div></div><div class="list-bottom" id="sample-pagination"></div></aside><div class="trace-main" id="trace-main"><div class="inspector-empty">选择左侧题目，展开其执行证据。</div></div><aside class="score-panel" id="score-panel"></aside></div>`;
  $('#trace-dataset').onchange=e=>navigate('trace',{evaluation:id,...(e.target.value?{dataset:e.target.value}:{})});
  $('#trace-protocol').onclick=()=>showProtocol(ev);
  $('#trace-compare').onclick=()=>chooseComparison(ev);
  let searchTimer;$('#sample-search').oninput=e=>{const value=e.target.value;clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.query=value;state.offset=0;loadTraceList();},300);};
  await loadTraceList(q.get('trajectory'));
}
async function loadTraceList(preferred){
  const token=++state.listEpoch;const evaluationId=state.evaluation?.id;if(!evaluationId)return;
  try{const d=await api('trajectories',{evaluation:evaluationId,dataset:state.dataset,outcome:state.filter,q:state.query,offset:state.offset,limit:30});
    if(token!==state.listEpoch||routeInfo().page!=='trace'||state.evaluation?.id!==evaluationId)return;
    $('#sample-total').textContent=`(${d.counts.all})`;
    $('#sample-filters').innerHTML=[['','全部',d.counts.all],['correct','正确',d.counts.correct],['incorrect','错误',d.counts.incorrect],['error','异常',d.counts.error],['unscored','未评分',d.counts.unscored]].map(([value,label,n])=>`<button class="filter-button ${state.filter===value?'active':''}" data-value="${value}">${label} ${n}</button>`).join('');
    $$('.filter-button',$('#sample-filters')).forEach(b=>b.onclick=()=>{state.filter=b.dataset.value;state.offset=0;loadTraceList();});
    $('#sample-list').innerHTML=d.items.length?d.items.map(t=>`<button class="sample-item" data-trajectory="${esc(t.id)}">${outcomeIcon(t.outcome)}<div><div class="sample-question">${esc(t.question)}</div><div class="sample-meta"><span>${esc(t.dataset)}</span><span>${t.step_count} steps</span></div><div class="secondary-line mono">${esc(t.sample_id)}</div></div></button>`).join(''):'<div class="empty-small">没有符合条件的轨迹。<br>可切换筛选或等待导入。</div>';
    $('#sample-pagination').innerHTML=`<span>${d.total?d.offset+1:0}–${Math.min(d.offset+d.limit,d.total)} / ${d.total}</span><div><button class="button small" id="prev-page" ${d.offset===0?'disabled':''}>‹</button> <button class="button small" id="next-page" ${d.offset+d.limit>=d.total?'disabled':''}>›</button></div>`;
    $('#prev-page').onclick=()=>{state.offset=Math.max(0,state.offset-30);loadTraceList();};$('#next-page').onclick=()=>{state.offset+=30;loadTraceList();};
    $$('[data-trajectory]').forEach(b=>b.onclick=()=>loadTrajectory(b.dataset.trajectory));
    const target=preferred || (d.items.some(t=>t.id===state.trace?.id)?state.trace.id:d.items[0]?.id);
    if(target)await loadTrajectory(target);else{$('#trace-main').innerHTML='<div class="inspector-empty">没有匹配的题目。原始记录不会被筛选操作修改。</div>';$('#score-panel').innerHTML='';state.trace=null;stopPlay();}
  }catch(e){toast(e.message);}
}
async function loadTrajectory(id){
  const token=++state.traceEpoch;stopPlay();
  try{const t=await api('trajectory',{id});if(token!==state.traceEpoch||routeInfo().page!=='trace')return;
    if(t.evaluation_id!==state.evaluation?.id)throw new Error('轨迹不属于当前评测记录');
    state.trace=t;state.step=t.steps[0]?.index??0;state.traceTab='steps';
    const params={evaluation:t.evaluation_id,trajectory:t.id,...(state.dataset?{dataset:state.dataset}:{}),...(state.filter?{outcome:state.filter}:{}),...(state.query?{q:state.query}:{})};
    state.lastTrace=href('trace',params);history.replaceState(null,'',state.lastTrace);$('#nav-trace').href='#/trace';
    $$('[data-trajectory]').forEach(b=>b.classList.toggle('active',b.dataset.trajectory===id));renderTraceMain();renderScorePanel();
  }catch(e){toast(e.message);}
}
function renderTraceMain(){
  const t=state.trace;if(!t)return;
  $('#trace-main').innerHTML=`<section class="card question-card"><div class="question-id"><span>${esc(t.dataset)}</span><span> / </span><span class="mono">${esc(t.sample_id)}</span>${state.evaluation.demo?badge('合成示例','neutral'):''}</div><h2>${esc(t.question)}</h2></section><section class="card"><div class="trace-tabs">${[['steps','执行轨迹'],['reference','题目与参考'],['raw','原始 JSON']].map(([key,label])=>`<button class="tab-button ${state.traceTab===key?'active':''}" data-trace-tab="${key}">${label}</button>`).join('')}</div><div id="trace-content"></div></section><p class="trace-bottom-note">日志回放 ≠ 重新执行。这里只展示已记录的输入输出，不猜测未保存的中间过程。</p>`;
  $$('[data-trace-tab]').forEach(b=>b.onclick=()=>{state.traceTab=b.dataset.traceTab;stopPlay();renderTraceMain();});
  const content=$('#trace-content');
  if(state.traceTab==='raw'){content.innerHTML=`<div class="raw-content"><a class="button small" href="/api/export/trajectory?id=${encodeURIComponent(t.id)}">导出完整 JSON ↓</a><pre class="payload" style="margin-top:12px">${esc(pretty(t))}</pre></div>`;return;}
  if(state.traceTab==='reference'){
    content.innerHTML=`<div class="trace-reference"><div class="note-box">以下内容仅供事后检查，不是 agent 当时的输入。判断模型看到了什么，请看每步“模型实际输入”。</div><h3>标准答案及合法别名</h3><pre class="payload">${esc(pretty(t.gold_answers))}</pre><h3>冻结题单中的参考标注 · 只用于事后检查</h3><pre class="payload">${esc(pretty(t.panel_sample?.metadata||{}))}</pre><h3>轨迹中记录的元数据</h3><pre class="payload">${esc(pretty(t.metadata||{}))}</pre><button id="trace-panel-reference" class="button small" style="margin-top:14px">查看冻结题单与参考标注</button></div>`;
    $('#trace-panel-reference').onclick=()=>showPanel(state.evaluation.panel_id);return;
  }
  content.innerHTML=`<div class="trace-tools"><span class="muted">${t.steps.length} 条步骤记录 · 独立展开查看</span><div><button id="expand-steps" class="button ghost small">展开全部</button><button id="play-trace" class="button small" ${!t.steps.length?'disabled':''}>▷ 顺序回放</button></div></div><div class="step-strip">${t.steps.map(s=>`<button class="step-pill ${s.index===state.step?'active':''} ${s.status==='error'?'has-error':''}" data-step="${s.index}" aria-label="查看 Step ${s.index}">${s.index}</button>`).join('')}</div><div class="trace-scroll" id="trace-scroll">${t.steps.length?t.steps.map(s=>stepCard(s,'trace',s.index===state.step)).join(''):'<div class="empty-small">只有最终结果，没有逐步证据。<br>不能据此重建当时的工具调用和真实输入。</div>'}</div>`;
  $$('[data-step]').forEach(b=>b.onclick=()=>{stopPlay();selectStep(Number(b.dataset.step));});
  $('#expand-steps').onclick=()=>{const all=$$('.step-card',content);const open=all.some(el=>!el.open);all.forEach(el=>el.open=open);$('#expand-steps').textContent=open?'收起全部':'展开全部';};
  $('#play-trace').onclick=()=>{
    if(state.play){stopPlay();return;}
    if(state.step===t.steps.at(-1)?.index)selectStep(t.steps[0].index);
    $('#play-trace').textContent='Ⅱ 暂停回放';
    state.play=setInterval(()=>{const i=t.steps.findIndex(s=>s.index===state.step);if(i>=t.steps.length-1){stopPlay();return;}selectStep(t.steps[i+1].index);},1500);
  };
  attachPayloadExpand(content);
}
function selectStep(index){
  if(!state.trace?.steps.some(s=>s.index===index))return;state.step=index;
  $$('[data-step]').forEach(b=>b.classList.toggle('active',Number(b.dataset.step)===index));
  $$('.step-card',$('#trace-content')).forEach(d=>d.open=Number(d.dataset.stepIndex)===index);
  const target=$(`#trace-step-${index}`),scroll=$('#trace-scroll');
  if(target&&scroll){const offset=target.getBoundingClientRect().top-scroll.getBoundingClientRect().top+scroll.scrollTop;scroll.scrollTo({top:Math.max(0,offset-12),behavior:'smooth'});}
}
function renderScorePanel(){
  const t=state.trace,e=state.evaluation;if(!t||!e)return;
  const scores=t.scores||{},em=scores.em;const answerStatus=em===1?'正确':em===0?'错误':'未评分';const usage=t.usage||{};
  const annotations=t.annotations||[];
  $('#score-panel').innerHTML=`<section class="card side-card"><h2>答案与评分 ${badge(`${answerStatus}${em!=null?' · EM = '+em:''}`,em===1?'success':em===0?'error':'neutral')}</h2><dl class="kv"><dt>原始最终输出</dt><dd><div class="answer-text">${esc(shown(t.raw_final_output))}</div></dd><dt>解析后答案</dt><dd class="answer-value">${esc(shown(t.parsed_answer))}</dd><dt>标准答案</dt><dd>${esc(t.gold_answers?.join(' / ')||'未提供')}</dd><dt>评分器</dt><dd>${esc(typeof e.protocol.scorer==='object'?(e.protocol.scorer.name||pretty(e.protocol.scorer)):e.protocol.scorer)}</dd><dt>评分版本</dt><dd>${esc(e.protocol.scorer?.version||'协议内查看')}</dd></dl><div class="score-numbers">${Object.entries(scores).slice(0,4).map(([k,v])=>`<div class="score-number"><small>${esc(k.toUpperCase())}</small><strong>${v==null?'—':num(v,2)}</strong></div>`).join('')}${t.reward!=null?`<div class="score-number"><small>记录的 REWARD</small><strong>${num(t.reward,2)}</strong></div>`:''}</div><p class="side-note">显示原评分器结果，不在查看器内重新判分。答题结果与运行状态分开记录。</p></section>
  <section class="card side-card"><h2>运行信息 ${badge(statusText[t.runtime_status],t.runtime_status==='ok'?'success':t.runtime_status==='unknown'?'neutral':'warning')}</h2><div class="runtime-grid">${[['模型调用',usage.model_calls],['工具调用',usage.tool_calls],['输入 tokens',usage.input_tokens],['输出 tokens',usage.output_tokens]].map(([label,val])=>`<div><div class="stat-label">${label}</div><div class="stat-value">${count(val)}</div></div>`).join('')}</div><dl class="kv" style="margin-top:16px;margin-bottom:0"><dt>总耗时</dt><dd>${usage.latency_ms==null?'未记录':(usage.latency_ms/1000).toFixed(2)+' s'}</dd><dt>终止原因</dt><dd>${esc(shown(t.termination_reason))}</dd></dl></section>
  <section class="card side-card"><h2>问题标注 ${badge('人工 · 可追溯','neutral')}</h2>${annotations.length?annotations.slice(0,3).map(a=>`<div class="annotation-entry"><strong>${esc(categoryText[a.category]||a.category)}</strong> · ${a.step_index==null?'未定位':`Step ${a.step_index}`}<small>${a.confidence==='confirmed'?'已核验':'待核验'} · ${esc(a.created_at.slice(0,16).replace('T',' '))}</small>${esc(a.note)}</div>`).join(''):'<p class="side-note" style="margin-top:0;margin-bottom:13px">尚未定位。看到错误答案，并不代表已确定最早出错的步骤。</p>'}
  <form class="annotation-form" id="annotation-form"><details ${annotations.length?'':'open'}><summary>＋ 添加证据标注</summary><label for="annotation-category">问题类别</label><select id="annotation-category">${Object.entries(categoryText).map(([k,v])=>option(k,v,'undetermined')).join('')}</select><div class="form-row"><div><label for="annotation-step">证据步骤</label><select id="annotation-step">${option('','未定位','')}${t.steps.map(s=>option(String(s.index),'Step '+s.index,'')).join('')}</select></div><div><label for="annotation-confidence">核验状态</label><select id="annotation-confidence"><option value="pending">待核验</option><option value="confirmed">已核验</option></select></div></div><textarea id="annotation-note" placeholder="记录你观察到的证据，不覆盖原始日志。" required maxlength="20000" aria-label="人工证据标注"></textarea><button class="button" type="submit" id="annotation-save">保存人工标注</button></details></form><p class="side-note">不提供聊天框或自动生成的失败结论。历史标注保留在完整 JSON 中。</p></section>`;
  $('#annotation-form').onsubmit=async ev=>{
    ev.preventDefault();const button=$('#annotation-save');button.disabled=true;
    try{const idx=$('#annotation-step').value;await api('annotations',{}, {method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':state.session.csrf_token},body:JSON.stringify({trajectory_id:t.id,annotation:{category:$('#annotation-category').value,step_index:idx===''?null:Number(idx),confidence:$('#annotation-confidence').value,note:$('#annotation-note').value}})});
      const updated=await api('trajectory',{id:t.id});if(state.trace?.id===t.id){state.trace=updated;renderScorePanel();}toast('标注已保存；原始轨迹与评分未修改。');
    }catch(err){toast(err.message);button.disabled=false;}
  };
}

async function renderCompare(epoch,q){
  if(!q.get('a')||!q.get('b')){navigate('overview',{run:state.run,panel:state.panel});return;}
  const d=await api('compare',{a:q.get('a'),b:q.get('b'),dataset:q.get('dataset')||'',cross_project:q.get('cross_project')||''});if(epoch!==state.epoch)return;
  state.compareData=d;state.project=d.a.project_id;state.experiment=d.a.experiment_id;state.run=d.a.checkpoint.run_id;updateResearchNav();state.panel=d.a.panel_id;state.compareGroup=q.get('group')||'';state.compareQuery='';state.compareVisible=100;setDemo(d.a.demo||d.b.demo);
  $('#app').innerHTML=`<div class="page-heading inspector-heading"><div><div class="eyebrow">PAIRED CHECKPOINT INSPECTION</div><h1>同题轨迹对比</h1><p class="subtitle">先检查哪些题变好或退化，再独立展开两侧步骤。步骤编号不强制对齐。</p></div><a class="button" href="${href('overview',{run:state.run,panel:state.panel})}">← 返回总览</a></div>
  <div class="inspector-toolbar"><span class="badge blue">A · ${esc(d.a.project_id)} / ${esc(d.a.run.name)} / ${esc(d.a.checkpoint.name)}</span><span class="muted">→</span><span class="badge success">B · ${esc(d.b.project_id)} / ${esc(d.b.run.name)} / ${esc(d.b.checkpoint.name)}</span><span class="grow"></span><select id="compare-dataset" aria-label="对比数据集">${option('','全部数据集',q.get('dataset')||'')}${datasetNames(d.a.panel.datasets).map(ds=>option(ds,ds,q.get('dataset')||'')).join('')}</select><button class="button small" id="compare-protocol">查看协议</button></div>
  ${d.warnings?.length?`<p class="side-note">${d.warnings.map(esc).join(' ')}</p>`:''}${d.compatible?`<div class="compare-heading">${badge('✓ 记录的题单与协议一致','success')}<span class="muted" style="font-size:11px">整体配对 ${d.paired} / ${d.expected} 题</span>${d.macro_delta==null?badge('部分结果 · 不显示完整平均差值','warning'):`<span class="delta-stat">整体宏平均 B − A：${d.macro_delta>=0?'+':''}${(d.macro_delta*100).toFixed(2)} pp</span>`}<span class="muted" style="font-size:10px">同题单只显示均值变化，不据此声称统计显著。</span></div><div class="comparison-grid"><aside class="card sample-panel"><div class="sample-top"><h2>配对题目 <span id="compare-total" class="muted"></span></h2><input id="compare-search" placeholder="搜索问题 / Sample ID" aria-label="搜索对比题目"><div class="comparison-filters" id="comparison-filters"></div></div><div class="sample-list" id="compare-list"></div><div class="list-bottom"><span id="compare-list-note"></span><button class="button small" id="compare-more">更多</button></div></aside><div class="comparison-pair" id="comparison-pair"></div></div>`:`<div class="error-box" style="margin-top:20px"><strong>这两条记录不能作为同协议的效果对比</strong><p>${d.reasons.map(esc).join('<br>')}</p><p>不计算胜负分组或分数差。请使用相同固定题单及真实一致的评测协议重新评测，不要仅修改元数据来绕过检查。</p></div>`}`;
  $('#compare-dataset').onchange=e=>navigate('compare',{a:d.a.id,b:d.b.id,...(d.cross_project?{cross_project:1}:{}),...(e.target.value?{dataset:e.target.value}:{})});
  $('#compare-protocol').onclick=()=>modal('A / B 评测协议',`<h3>A · ${esc(d.a.checkpoint.name)}</h3><pre>${esc(pretty(d.a.protocol))}</pre><h3>B · ${esc(d.b.checkpoint.name)}</h3><pre>${esc(pretty(d.b.protocol))}</pre><p>题单 A：${esc(d.a.panel_hash)}<br>题单 B：${esc(d.b.panel_hash)}</p>`);
  if(d.compatible){renderCompareList();$('#compare-search').oninput=e=>{state.compareQuery=e.target.value;state.compareVisible=100;renderCompareList();};$('#compare-more').onclick=()=>{state.compareVisible+=100;renderCompareList();};}
}
function renderCompareList(){
  const d=state.compareData;
  const q=state.compareQuery.toLowerCase();const matches=d.items.filter(t=>(t.question+' '+t.sample_id).toLowerCase().includes(q));
  const counts=Object.fromEntries(Object.keys(groupText).map(g=>[g,matches.filter(x=>x.group===g).length]));
  $('#comparison-filters').innerHTML=[['','全部',matches.length],...Object.entries(groupText).map(([key,label])=>[key,label,counts[key]])].map(([g,label,n])=>`<button class="group-pill ${state.compareGroup===g?'active':''}" data-group="${g}">${label} ${n}</button>`).join('');
  $$('[data-group]').forEach(b=>b.onclick=()=>{state.compareGroup=b.dataset.group;state.compareVisible=100;renderCompareList();});
  const filtered=matches.filter(t=>!state.compareGroup||t.group===state.compareGroup);const rows=filtered.slice(0,state.compareVisible);
  $('#compare-total').textContent=`(${filtered.length})`;$('#compare-list-note').textContent=`显示 ${rows.length} / ${filtered.length}`;$('#compare-more').disabled=rows.length>=filtered.length;
  $('#compare-list').innerHTML=rows.length?rows.map((t,i)=>`<button class="sample-item" data-pair-index="${i}">${outcomeIcon(t.group==='rescued'?'correct':t.group==='harmed'?'incorrect':'unscored')}<div><div class="sample-question">${esc(t.question)}</div><div class="sample-meta"><span>${esc(t.dataset)}</span><span>${esc(groupText[t.group])}</span></div><div class="secondary-line mono">${esc(t.sample_id)}</div></div></button>`).join(''):'<div class="empty-small">没有符合此条件的配对题目。</div>';
  $$('[data-pair-index]').forEach(b=>b.onclick=()=>loadPair(rows[Number(b.dataset.pairIndex)],b));
  if(rows.length)loadPair(rows[0],$('[data-pair-index]'));else $('#comparison-pair').innerHTML='<div class="inspector-empty">切换左侧筛选以检查其他题目。</div>';
}
let pairToken=0;
async function loadPair(pair,button){
  const token=++pairToken, epoch=state.epoch;try{const [a,b]=await Promise.all([api('trajectory',{id:pair.a.id}),api('trajectory',{id:pair.b.id})]);if(token!==pairToken||epoch!==state.epoch||routeInfo().page!=='compare')return;
    $$('[data-pair-index]').forEach(el=>el.classList.toggle('active',el===button));
    $('#comparison-pair').innerHTML=pairColumn(a,state.compareData.a,'A')+pairColumn(b,state.compareData.b,'B');attachPayloadExpand($('#comparison-pair'));
    $$('[data-pair-step]').forEach(el=>el.onclick=()=>{const prefix=el.dataset.pairPrefix,index=el.dataset.pairStep;const target=$(`#${prefix}-step-${index}`);if(target){target.open=true;target.scrollIntoView({block:'nearest',behavior:'smooth'});}});
  }catch(e){toast(e.message);}
}
function pairColumn(t,ev,letter){
  const prefix='pair-'+letter,em=t.scores.em;
  return `<section class="card pair-column"><div class="pair-header"><span class="pair-letter ${letter==='B'?'b':''}">${letter}</span><div><h2>${esc(ev.checkpoint.name)}</h2><div class="secondary-line">step ${ev.checkpoint.step}</div></div><a class="button small" href="${href('trace',{evaluation:ev.id,trajectory:t.id,dataset:t.dataset})}">单独检查 ↗</a></div><div class="pair-question">${esc(t.question)}<div class="pair-score">${badge(em===1?'正确 · EM = 1':em===0?'错误 · EM = 0':'未评分',em===1?'success':em===0?'error':'neutral')}<span>${count(t.usage?.tool_calls)} 次工具调用 · ${t.steps.length} steps</span></div><div class="secondary-line" style="margin-top:9px">解析答案：<strong>${esc(shown(t.parsed_answer))}</strong>　|　标准答案：${esc(t.gold_answers.join(' / '))}</div></div><div class="step-strip" style="padding-top:12px">${t.steps.map(s=>`<button class="step-pill ${s.status==='error'?'has-error':''}" data-pair-prefix="${prefix}" data-pair-step="${s.index}">${s.index}</button>`).join('')}</div><div class="trace-scroll">${t.steps.length?t.steps.map((s,i)=>stepCard(s,prefix,i===0)).join(''):'<div class="empty-small">这条轨迹未保存逐步记录。</div>'}</div></section>`;
}

function auditLabel(a){return ({not_checked:'隔离未核验',blocked_overlap:'发现重叠，禁止声称隔离',manual_review:'近重复候选待人工审核',incomplete_scope:'清单范围不完整',no_detected_overlap_in_supplied_scope:'所提供清单内未发现重叠'})[a?.status]||'隔离未核验';}
async function showPanel(id){
  try{const p=await api('panel',{id});
    modal(`Groundtruth · ${p.name}`,`<div class="note-box ${p.demo?'warn':''}">${p.demo?'合成演示问题，不是真实基准样本或已验证隔离的 validation。':'题单只读；所有 checkpoint 读取相同具体题目。修改须新建版本，不能覆盖 v1。'}</div><div class="gt-modal-meta"><span>${p.size} 道题 · ${esc(p.version)}</span><a class="button small" href="/api/export/panel?id=${encodeURIComponent(id)}">导出完整 Groundtruth</a></div><pre>${esc(p.fingerprint)}</pre><div class="note-box warn"><strong>${esc(auditLabel(p.isolation))}</strong><p>${esc(p.isolation?.notice||'')}</p>${p.isolation?.id?`<button class="link-button" id="view-isolation-report">查看本次审核报告</button>`:''}</div><p class="subtitle">${p.source_metadata_complete?'每道题已登记来源 ID 与版本（内容为导入记录）。':'部分题目没有登记原始来源 ID / 版本；不要声称来源已经核实。'}</p><table class="modal-table"><thead><tr><th>Dataset / ID</th><th>Question</th><th>答案与别名</th><th>来源版本</th></tr></thead><tbody>${p.samples.map(s=>`<tr><td>${esc(s.dataset)}<div class="secondary-line mono">${esc(s.id)}</div></td><td>${esc(s.question)}</td><td>${esc(s.answers.join(' / '))}</td><td>${esc(s.metadata?.source_revision||'未记录')}<div class="secondary-line">${esc(s.metadata?.source_id||'未记录')}</div></td></tr>`).join('')}</tbody></table>`);
    $('#view-isolation-report')?.addEventListener('click',async()=>{try{const r=await api('audit',{id:p.isolation.id});modal('隔离审核 · 所提供清单的检查结果',`<pre>${esc(pretty(r))}</pre>`);}catch(e){toast(e.message);}});
  }catch(e){toast(e.message);}
}
async function showExperiment(id){
  try{const e=await api('experiment',{id});
    modal('实验方案 · '+e.name,`<div class="experiment-context"><dl><dt>要验证什么</dt><dd>${esc(e.objective||'未记录')}</dd><dt>相对对照改了什么</dt><dd>${esc(e.change||'未记录')}</dd><dt>保持不变的条件</dt><dd>${esc(e.constants||'未记录')}</dd></dl></div><details><summary>原始实验配置（只读）</summary><pre>${esc(pretty(e.config||{}))}</pre></details><h3>引用已有 baseline</h3>${e.references.map(r=>`<div class="note-box"><strong>${esc(r.evaluation_id)}</strong><p>${esc(r.reason)}</p><p class="secondary-line">来源项目：${esc(r.source_project)} · run：${esc(r.source_run)}</p><a class="button small" href="${href('trace',{evaluation:r.evaluation_id})}" data-close-on-nav>查看原始记录 ↗</a></div>`).join('')||'<p class="muted">未登记引用。引用不会复制数据或自动加入当前项目的曲线。</p>'}<h3>观察、提升与退化</h3><div id="experiment-note-list">${e.notes.map(n=>`<article class="experiment-note"><span class="badge ${n.kind==='negative'?'warning':'neutral'}">${esc(({observation:'观察',positive:'正面证据',negative:'负面证据',decision:'决定'})[n.kind])}</span><time>${esc(n.created_at)}</time><p>${esc(n.note)}</p>${n.evidence?.length?`<pre>${esc(pretty(n.evidence))}</pre>`:''}</article>`).join('')||'<p class="muted">尚无记录；系统不会根据分数自动编写结论。</p>'}</div><form id="experiment-note-form"><div class="note-entry-label">追加人工记录（不是聊天）</div><select id="exp-note-kind"><option value="observation">观察</option><option value="positive">正面证据</option><option value="negative">负面证据</option><option value="decision">决定</option></select><textarea id="exp-note-text" required maxlength="20000" placeholder="记录结论及对应评测 / 轨迹 ID，历史内容不覆盖。"></textarea><button class="button primary" id="exp-note-submit">保存人工记录</button></form>`);
    $$('[data-close-on-nav]').forEach(b=>b.onclick=()=>$('#modal').close());
    $('#experiment-note-form').onsubmit=async event=>{event.preventDefault();$('#exp-note-submit').disabled=true;try{await api('experiment-notes',{}, {method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':state.session.csrf_token},body:JSON.stringify({experiment_id:id,note:{kind:$('#exp-note-kind').value,note:$('#exp-note-text').value,evidence:[]}})});await showExperiment(id);toast('人工记录已追加；配置和原始成绩保持不变。');}catch(err){toast(err.message);$('#exp-note-submit').disabled=false;}};
  }catch(e){toast(e.message);}
}
async function showRepro(cp){
  const evals=[...cp.evaluations].reverse();
  modal('同一 checkpoint · 重复评测检查',`<div class="note-box">检查 <strong>${esc(cp.name)}</strong> 的两条独立记录；不会调用模型、搜索服务或重新打分。耗时差异不当作路径变化。</div><div class="repro-selectors"><label>记录 A<select id="repro-a">${evals.map(e=>option(e.id,`${e.id.split('-').at(-1)} · ${e.created_at}`,evals[0]?.id)).join('')}</select></label><label>记录 B<select id="repro-b">${evals.map(e=>option(e.id,`${e.id.split('-').at(-1)} · ${e.created_at}`,evals.at(-1)?.id)).join('')}</select></label><button id="repro-run" class="button primary">检查已有记录</button></div><div id="repro-result"></div>`);
  $('#repro-run').onclick=async()=>{const a=$('#repro-a').value,b=$('#repro-b').value;$('#repro-run').disabled=true;try{
    const d=await api('reproducibility',{a,b});if(!$('#repro-result'))return;
    if(!d.eligible){$('#repro-result').innerHTML=`<div class="error-box">${d.reasons.map(esc).join('<br>')}</div>`;return;}
    const flag=x=>x===true?'<span class="positive">一致</span>':x===false?'<span class="negative">不同</span>':'<span class="muted">证据不足</span>';
    $('#repro-result').innerHTML=`<div class="repro-stats"><div><strong>${d.counts.matched}</strong><span>记录一致</span></div><div><strong>${d.counts.different}</strong><span>存在差异</span></div><div><strong>${d.counts.insufficient+d.counts.missing}</strong><span>不足 / 缺失</span></div><a class="button small" href="/api/export/reproducibility?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}">导出核对报告</a></div><div class="note-box ${d.all_recorded_evidence_matches?'':'warn'}">${d.all_recorded_evidence_matches?'这两次记录中的答案、评分和所保存的执行路径匹配。':'这些记录不能标记为完整一致；请逐题查看差异或缺失证据。'} 两次一致不保证所有未来运行一致。</div><table class="modal-table repro-table"><thead><tr><th>题目</th><th>解析答案</th><th>原始输出</th><th>评分</th><th>轨迹</th><th>证据</th></tr></thead><tbody>${d.items.map(i=>`<tr class="${i.state==='different'?'repro-different':''}"><td>${esc(i.dataset)}<div class="secondary-line">${esc(i.sample_id)}</div></td><td>${flag(i.answer_equal)}</td><td>${flag(i.raw_output_equal)}</td><td>${flag(i.score_equal)}</td><td>${flag(i.trace_equal)}${i.first_different_step_index!=null?`<small>首先不同：Step ${i.first_different_step_index}</small>`:''}</td><td>${i.a?`<a href="${href('trace',{evaluation:a,trajectory:i.a,dataset:i.dataset})}" data-close-on-nav>A ↗</a>`:''} ${i.b?`<a href="${href('trace',{evaluation:b,trajectory:i.b,dataset:i.dataset})}" data-close-on-nav>B ↗</a>`:''}</td></tr>`).join('')}</tbody></table><p class="side-note">${d.limitations.map(esc).join(' ')}</p>`;
    $$('[data-close-on-nav]').forEach(el=>el.onclick=()=>$('#modal').close());
  }catch(e){toast(e.message);}finally{if($('#repro-run'))$('#repro-run').disabled=false;}};
  $('#repro-run').click();
}
async function chooseComparison(ev){
  try{
    modal('明确选择比较对象',`<div class="note-box">A：${esc(ev.project_id)} / ${esc(ev.experiment_id)} / ${esc(ev.run?.name||ev.checkpoint.run_id)} / ${esc(ev.checkpoint.name)}<br>不自动把别的项目结果加到当前曲线。跨项目比较需确认，并核对底座、题单、检索、评分和评测预算。</div><div class="compare-picker"><label>B 所属研究项目<select id="other-project">${state.session.projects.map(p=>option(p.id,p.name,ev.project_id)).join('')}</select></label><label>B 的实验 / 运行<select id="other-run"></select></label><label>B 的评测记录<select id="other-evaluation"></select></label><label id="cross-consent" class="hidden"><input id="cross-consent-checkbox" type="checkbox">明确进行跨项目比较；训练预算仍需另外核查</label><button class="button primary" id="modal-compare-go">查看比较</button></div>`);
    let req=0;
    async function loadRuns(){const token=++req;const project=$('#other-project').value;const c=await api('catalog',{project});if(token!==req||!$('#other-run'))return;$('#cross-consent').classList.toggle('hidden',project===ev.project_id);$('#cross-consent-checkbox').checked=false;
      $('#other-run').innerHTML=c.runs.map(r=>option(r.id,`${c.experiments.find(e=>e.id===r.experiment_id)?.name||r.experiment_id} / ${r.name} / ${r.base_model||'未记录模型'}`,ev.checkpoint.run_id)).join('');await loadEvals();}
    async function loadEvals(){const token=++req;const run=$('#other-run').value;if(!run){$('#other-evaluation').innerHTML='';return;}const d=await api('overview',{run,panel:ev.panel_id,project:$('#other-project').value});if(token!==req||!$('#other-evaluation'))return;const others=d.checkpoints.flatMap(c=>c.evaluations).filter(e=>e.id!==ev.id);$('#other-evaluation').innerHTML=others.map(e=>option(e.id,evalLabel(e),others.at(-1)?.id)).join('');}
    $('#other-project').onchange=()=>loadRuns().catch(e=>toast(e.message));$('#other-run').onchange=()=>loadEvals().catch(e=>toast(e.message));
    $('#modal-compare-go').onclick=()=>{const b=$('#other-evaluation').value,cross=$('#other-project').value!==ev.project_id;if(!b)return toast('没有可选择的评测记录。');if(cross&&!$('#cross-consent-checkbox').checked)return toast('跨项目比较需要明确确认。');$('#modal').close();navigate('compare',{a:ev.id,b,...(cross?{cross_project:1}:{})});};
    await loadRuns();
  }catch(e){toast(e.message);}
}


function attachMethodEntry(){
  const box=document.createElement('div');box.className='mx-project-links';
  const params=routeInfo().page!=='projects'&&state.project?{project_id:state.project,...(state.run?{run_id:state.run}:{})}:{};
  box.innerHTML='<strong>方法过程与产物</strong>';
  for(const [view,label] of [['selfplay','Self-play 出题与解题'],['skills','SkillBank 快照与使用'],['artifacts','打开方法记录']]){
    const a=document.createElement('a');a.className='button small';a.textContent=label;a.href='#/methods?'+new URLSearchParams({...params,view});box.append(a);
  }
  const target=$('#app .page-heading')||$('#app').firstElementChild;target?.after(box);
}

async function route(){
  stopPlay();const epoch=++state.epoch;++state.listEpoch;++state.traceEpoch;
  const {page,q}=routeInfo();const overview=page==='overview'||page==='projects'||page==='methods';
  $('#nav-overview').classList.toggle('active',overview);$('#nav-trace').classList.toggle('active',!overview);
  $('#page-crumb').textContent=page==='methods'?'方法过程与产物':page==='projects'?'研究项目':overview?'训练与评测':page==='compare'?'同题对比':page==='questions'?'固定题目':'轨迹详情';
  $('#app').innerHTML='<div class="loading">正在读取已保存的记录…</div>';
  try{
    state.session=await api('session');if(epoch!==state.epoch)return;
    $('#app-version').textContent='v'+state.session.version;$('#sync-label').textContent='已连接 · '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
    updateResearchNav();
    if(page==='methods')await mountMethods($('#app'),{q,session:state.session,api,toast});else if(page==='projects')await renderProjects(epoch);else if(page==='overview')await renderOverview(epoch,q);else if(page==='questions')await renderQuestionWorkspace(epoch,q);else if(page==='trace')await mountInspector($('#app'),{q,session:state.session,api,toast,onEvaluation:ev=>{state.evaluation=ev;state.project=ev.project_id;state.experiment=ev.experiment_id;state.run=ev.checkpoint.run_id;state.panel=ev.panel_id;updateResearchNav();setDemo(ev.demo);}});else if(page==='compare')await renderCompare(epoch,q);else navigate('projects');
    if((page==='projects'||page==='overview')&&epoch===state.epoch)attachMethodEntry();
  }catch(e){if(epoch===state.epoch){$('#app').innerHTML=errorBox(e.message);$('#retry-page').onclick=route;}}
}
$('#help-button').onclick=helpModal;$('#close-modal').onclick=()=>$('#modal').close();$('#refresh-button').onclick=route;
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal')){const r=$('#modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('#modal').close();}});
$('#auto-refresh').onchange=e=>{clearInterval(state.auto);state.auto=null;if(e.target.checked)state.auto=setInterval(()=>{if(!document.hidden&&!$('#modal').open&&routeInfo().page==='overview')route();},10000);toast(e.target.checked?'每 10 秒刷新总览；轨迹页不自动刷新，避免打断标注。':'已关闭自动刷新。');};
window.addEventListener('hashchange',route);
window.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)||$('#modal').open||routeInfo().page!=='trace'||state.traceTab!=='steps')return;if(!['ArrowLeft','ArrowRight'].includes(e.key))return;const steps=state.trace?.steps||[];const i=steps.findIndex(s=>s.index===state.step);const next=steps[i+(e.key==='ArrowRight'?1:-1)];if(next){e.preventDefault();stopPlay();selectStep(next.index);}});
route();
