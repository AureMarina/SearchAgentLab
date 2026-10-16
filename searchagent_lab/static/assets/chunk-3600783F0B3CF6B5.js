/** Research navigation is a presentation index; original experiment/run IDs stay stable. */
import {node,button,link,href,badge,select,field,empty,card,controls,table,addRow,pager,modal,closeModal} from './chunk-F51FD76F14331921.js';
import {icon} from './chunk-E8A274CB79B7A807.js';
import {entropyGuide} from './chunk-0DA19B2E303FFCF2.js';
import {selfplayGuide} from './chunk-390F0CAF823DF8D0.js';

export const FAMILY_LABELS={baseline:'原生 Qwen 基线',self_distillation:'Self-distillation',skill:'Skill',self_play:'Self-play',latent:'Latent',entropy:'Entropy',other:'其他 / 待分类'};
const ORIGIN_LABELS={main:'我的实验',reproduction:'文献复现',unassigned:'用途待登记'};
const DATASETS=[['nq','NQ'],['triviaqa','TriviaQA'],['popqa','PopQA'],['hotpotqa','HotpotQA'],['2wiki','2Wiki'],['musique','MuSiQue'],['bamboogle','Bamboogle']];
const percent=n=>Number.isFinite(n)?n.toFixed(2)+'%':'—';
const number=n=>Number.isFinite(n)?n.toLocaleString('en-US'):'—';
const aggregateOf=e=>e.aggregate||{};
export function experimentHref(e,extra={}){return href('experiment',{experiment:e.experiment.id,run:e.latest_run?.id,project:e.experiment.project_id,track:e.profile.track,...extra});}
export function familyLabel(f){return FAMILY_LABELS[f]||f||FAMILY_LABELS.other;}
function datasetId(id){return String(id).toLowerCase().replace(/[^a-z0-9]/g,'').replace('2wikimultihopqa','2wiki').replace('2wikimqa','2wiki').replace('naturalquestions','nq').replace('triv','triv');}
export function metricValue(aggregate){
 if(Number.isFinite(aggregate?.macro_em_pct))return {value:aggregate.macro_em_pct,label:(aggregate?.dataset_count===7?'七集':'数据集')+'宏平均 EM'};
 if(Number.isFinite(aggregate?.macro_logged_success_pct))return {value:aggregate.macro_logged_success_pct,label:'日志验证成功率'};
 if(Number.isFinite(aggregate?.macro_accuracy_pct))return {value:aggregate.macro_accuracy_pct,label:'原定义准确率'};
 return {value:null,label:'尚无完整集汇总'};
}
export function renderWorkspaceNav(host,ctx){
 host.replaceChildren();const current=ctx.family||'all',isList=ctx.page==='experiments';
 const navLink=(label,family,iconName,count)=>{const a=link(label,href('experiments',family==='all'?{}:{family}),'s-nav-item '+(isList&&current===family?'active':''));a.prepend(icon(iconName));if(count!=null)a.append(node('span','w-nav-count',String(count)));if(a.classList.contains('active'))a.setAttribute('aria-current','page');host.append(a);};
 navLink('全部实验','all','layout-grid',ctx.workspace.experiments.length);
 navLink('原生 Qwen 基线','baseline','compare');
 host.append(node('div','w-nav-label','研究方向'));
 // Explicit research directions remain reachable before their first experiment.
 // A zero count is navigation state, never a measured result.
 for(const f of ctx.workspace.families.filter(f=>f.id!=='baseline'&&(['entropy','self_play'].includes(f.id)||f.count>0||current===f.id)))navLink(familyLabel(f.id),f.id,'flask',f.count);
 host.append(node('div','w-nav-label','工具'));
 for(const[page,label,glyph]of [['versions','固定题单','list'],...(ctx.session.team?.enabled?[['team','数据接入','list']]:[])]){
  const a=link(label,href(page,{project:ctx.project,track:ctx.track}),'s-nav-item '+(ctx.page===page?'active':''));a.prepend(icon(glyph));if(ctx.page===page)a.setAttribute('aria-current','page');host.append(a);
 }
}

export function baselineCard(workspace,{compact=false}={}){
 const box=card('w-baseline'),head=node('div','w-baseline-head'),label=node('div');
 label.append(node('span','w-anchor','共同参照'),node('h2','','原生 Qwen · 七集问答基线'));
 const baselineRows=workspace.baseline_runs?.length?workspace.baseline_runs.map(b=>({experiment:b.experiment,latest_run:b.run,runs:[b.run],aggregate:b.aggregate,profile:workspace.baselines.find(e=>e.experiment.id===b.experiment.id)?.profile||{track:'main'}})):workspace.baselines;
 const total=baselineRows.reduce((s,e)=>s+e.runs.length,0);
 head.append(label,badge(total+' 个已接入运行','purple'));box.append(head);
 if(!workspace.baselines.length){box.append(node('div','w-empty-inline','尚未登记原生基线。导入原生模型结果后，在“归类”中设为共同基线。'));return box;}
 const t=table(['基线模型',...(!compact?DATASETS.map(x=>x[1]):[]),'宏平均 EM','评测范围','']);t.t.classList.add('w-benchmark-table');
 for(const e of baselineRows){const a=aggregateOf(e),run=e.latest_run,datasets=new Map((a.datasets||[]).map(x=>[datasetId(x.id),x]));const model=node('div','w-model');model.append(link(run?.base_model||e.experiment.name,experimentHref(e),'w-exp-name'),node('small','s-muted','原生模型 · '+(run?.status==='completed'?'已完成':run?.status||'待接入')));
  const score=node('strong','w-number',percent(a.macro_em_pct));
  const scope=node('div','w-scope');scope.append(node('span','',number(a.sample_count)+' 题'),node('small','s-muted',(a.dataset_count||0)+' 个数据集 · 汇总'));
  const tr=addRow(t.body,[model,...(!compact?DATASETS.map(([id])=>percent(datasets.get(id)?.em_pct)):[]),score,scope,link('查看 →',experimentHref(e),'s-link')]);tr.dataset.baseline=e.experiment.id;tr.dataset.baselineRun=run?.id||'';
 }
 box.append(t.wrap);const hint=node('div','w-baseline-note');hint.append(node('span','','所有实验共用此基线入口，按底座、题目与评测口径匹配。'));
 const models=workspace.baselines.flatMap(x=>x.runs.map(r=>r.base_model||''));if(!models.some(m=>/4b/i.test(m)))hint.append(badge('4B 基线待接入','muted'));box.append(hint);return box;
}

export async function mountWorkspace(host,ctx){
 const {q,alive}=ctx,workspace=ctx.workspace||await ctx.api('studio/workspace');if(!alive())return;
 let family=ctx.family||'all',origin=q.get('origin')||'',query=q.get('q')||'',page=Math.max(0,Number(q.get('page'))||0),timer;
 const emptyEntropy=family==='entropy'&&!workspace.experiments.some(e=>e.family==='entropy');
 const emptySelfplay=family==='self_play'&&!workspace.experiments.some(e=>e.family==='self_play');
 const head=node('div','s-heading'),title=node('div');title.append(node('div','s-eyebrow','SEARCH AGENT LAB'),node('h1','',family==='all'?'基线与实验':familyLabel(family)),node('p','s-subtitle',family==='baseline'?'原生模型的七集问答结果，是各研究方向的共同参照。':family==='entropy'?'把熵的测量、作用过程和实际效果分别看清楚。':family==='self_play'?'同时看清每轮题目供给、实际训练信号和固定评测上的解题能力。':'按研究方向查看自己的实验与文献复现，打开实验即可查看基线对照与轨迹。'));
 head.append(title);if(!ctx.session.read_only)head.append(controls(button('导入结果',()=>importResults(ctx),'s-btn'),!emptyEntropy&&!emptySelfplay&&button('＋ 新建实验',()=>createExperiment(ctx,family),'s-btn primary')));host.append(head);
 if(family==='entropy')host.append(entropyGuide(ctx,{empty:emptyEntropy,create:()=>createExperiment(ctx,'entropy')}));
 if(family==='self_play')host.append(selfplayGuide(ctx,{empty:emptySelfplay,create:()=>createExperiment(ctx,'self_play')}));
 host.append(baselineCard(workspace,{compact:family!=='all'&&family!=='baseline'}));
 if(emptyEntropy||emptySelfplay){ctx.markDemo(false);return;}
 if(family==='baseline'){
  const body=node('div','w-baseline-experiments');for(const e of workspace.baselines){const row=node('div','w-baseline-detail');row.append(link(e.experiment.name,experimentHref(e),'w-exp-name'),node('span','s-muted',e.experiment.objective||e.profile.question||''),link('查看七集结果 →',experimentHref(e),'s-link'));body.append(row);}host.append(body);return;
 }
 const toolbar=node('div','w-toolbar'),search=node('input');search.type='search';search.value=query;search.placeholder='搜索实验、方法或底座…';search.setAttribute('aria-label','搜索实验');
 const tabs=node('div','w-origin-tabs');tabs.setAttribute('aria-label','实验来源');for(const[value,label]of [['','全部'],['main','我的实验'],['reproduction','文献复现']]){const b=button(label,()=>{origin=value;page=0;draw();},'s-filter');b.dataset.origin=value;tabs.append(b);}toolbar.append(tabs,search);host.append(toolbar);
 const content=node('div','w-groups');host.append(content);
 const indexed=workspace.experiments.filter(e=>!e.presentation?.is_baseline&&e.family!=='baseline').map(e=>({e,search:[e.experiment.name,e.experiment.objective,e.profile.question,familyLabel(e.family),...e.runs.map(r=>r.base_model)].filter(Boolean).join(' ').toLowerCase()}));
 function remember(){history.replaceState(null,'',href('experiments',{family:family==='all'?'':family,origin,q:query,page:page||''}));}
 function draw(){if(!alive())return;remember();content.replaceChildren();for(const b of tabs.children){const on=b.dataset.origin===origin;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));}
  const rows=indexed.filter(x=>(family==='all'||x.e.family===family)&&(!origin||x.e.profile.track===origin)&&(!query||x.search.includes(query.toLowerCase()))).map(x=>x.e);page=Math.min(page,Math.max(0,Math.ceil(rows.length/50)-1));const visible=rows.slice(page*50,page*50+50);
  const groups=workspace.families.filter(f=>f.id!=='baseline'&&(family==='all'?visible.some(e=>e.family===f.id):f.id===family));
  for(const f of groups){const list=visible.filter(e=>e.family===f.id),section=node('section','w-group');const top=node('div','w-group-heading');top.append(node('h2','',familyLabel(f.id)),node('span','s-muted',String(rows.filter(e=>e.family===f.id).length)+' 个实验'));section.append(top);
   for(const track of ['main','reproduction','unassigned']){if(origin&&origin!==track)continue;const items=list.filter(e=>e.profile.track===track);if(!items.length){if(family!=='all'&&track==='main'&&!query)section.append(node('div','w-empty-own','我的实验 · 尚未接入'));continue;}
    const group=card('w-experiment-group'),caption=node('div','w-origin-heading');caption.append(node('span','w-origin-dot '+track),node('strong','',ORIGIN_LABELS[track]),node('small','s-muted',String(items.length)));group.append(caption);
    const tableView=table(['实验 / 方法','底座','完整集结果','固定题诊断','原生基线','']);
    for(const e of items){const run=e.latest_run,a=aggregateOf(e),metric=metricValue(a),diag=e.diagnostics?.latest||{},name=node('div','w-experiment-name');name.append(link(e.experiment.name,experimentHref(e),'w-exp-name'));const subtitle=e.profile.question||e.experiment.objective;if(subtitle)name.append(node('small','s-muted w-ellipsis',subtitle));
     const model=node('div','w-model');model.append(node('span','',run?.base_model||'尚无运行'),node('small','s-muted',run?((a.step!=null?'step '+a.step+' · ':'')+(run.status==='completed'?'已完成':run.status||'已接入')):'待接入'));
     const score=node('div','w-score');score.append(node('strong','w-number',percent(metric.value)),node('small','s-muted',metric.label),node('small','s-muted',a.sample_count?number(a.sample_count)+' 题':''));
     const diagnostic=node('div','w-score');diagnostic.append(node('strong','',diag.trajectory_count!=null?`${diag.answer_correct??'—'} / ${diag.panel_size??diag.trajectory_count}`:'—'),node('small','s-muted',diag.trajectory_count?diag.trajectory_count+' 条轨迹':'尚无逐题轨迹'));
     const reference=node('div','w-reference');reference.append(badge(e.reference?.status==='comparable'?'可比':e.reference?.status==='missing_baseline'?'待接入同底座':'查看口径',e.reference?.status==='comparable'?'good':'muted'),link('与基线查看 →',experimentHref(e,{view:'results'}),'s-link'));
     const actions=controls(link('打开 →',experimentHref(e),'s-link'));if(!ctx.session.read_only)actions.append(button('归类',()=>editPresentation(ctx,e),'w-organize s-link'));
     const row=addRow(tableView.body,[name,model,score,diagnostic,reference,actions]);row.dataset.experiment=e.experiment.id;row.dataset.family=e.family;row.dataset.origin=e.profile.track;
    }group.append(tableView.wrap);section.append(group);
   }content.append(section);
  }
  if(!rows.length)content.append(empty(query||origin?'没有匹配的实验。试试清除搜索或切换来源。':'此研究方向尚未接入实验。'));
  if(rows.length>50)content.append(pager(rows.length,page,50,n=>{page=n;draw();content.scrollIntoView({block:'start'});}));
 }
 search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{query=search.value;page=0;draw();},120);});draw();
 ctx.markDemo(workspace.experiments.length>0&&workspace.experiments.every(e=>e.runs.length&&e.runs.every(r=>r.demo)));
 return()=>clearTimeout(timer);
}

function textInput(value='',placeholder=''){const n=node('input');n.value=value;n.placeholder=placeholder;return n;}
export function editPresentation(ctx,e){
 const body=modal('整理实验分类'),family=select(Object.entries(FAMILY_LABELS).filter(([k])=>k!=='baseline'),e.family==='baseline'?'other':e.family,()=>{},'研究方向'),baseline=node('input');baseline.type='checkbox';baseline.checked=!!e.presentation?.is_baseline;
 body.append(node('p','s-muted',e.experiment.name),field('研究方向',family),field('设为共同原生基线',baseline));
 let origin,method;if(e.profile.track==='unassigned'){origin=select([['main','我的实验'],['reproduction','文献复现']],'main',v=>{method.parentElement.hidden=v!=='reproduction';},'实验来源');method=textInput('','例如 SDAR');const methodField=field('复现方法',method);methodField.hidden=true;body.append(field('登记用途',origin),methodField);}else body.append(node('p','s-muted','现有用途：'+ORIGIN_LABELS[e.profile.track]+'。实验、运行与结果仍保留原始 ID。'));
 const save=button('保存分类',async()=>{save.disabled=true;try{
  if(origin){if(origin.value==='reproduction'&&!method.value.trim())throw Error('请填写复现方法。');const profile={experiment_id:e.experiment.id,track:origin.value,binding_origin:'retrospective'};if(origin.value==='reproduction')profile.reproduction={method_name:method.value,protocol_mode:'unknown'};await ctx.post('studio/profile',profile);}
  await ctx.post('studio/presentation',{experiment_id:e.experiment.id,family:baseline.checked?'baseline':family.value,is_baseline:baseline.checked});closeModal();await ctx.refresh();
 }catch(err){ctx.toast(err.message);save.disabled=false;}},'s-btn primary');body.append(save);
}
function createExperiment(ctx,currentFamily){
 const body=modal('新建实验'),form=node('form','s-form'),name=textInput('','例如：观察条件蒸馏 · 第1轮'),direction=select(Object.entries(FAMILY_LABELS).filter(([k])=>k!=='baseline'),['all','baseline'].includes(currentFamily)?'self_distillation':currentFamily,()=>{},'研究方向'),origin=select([['main','我的实验'],['reproduction','文献复现']],'main',v=>{repro.hidden=v!=='reproduction';},'实验来源'),question=textInput('','这次想验证什么？'),method=textInput('','例如 SDAR'),paper=textInput('','论文 URL'),repo=textInput('','代码仓库 URL'),repro=node('div','s-form');repro.hidden=true;repro.append(field('复现方法',method),field('论文',paper),field('代码',repo));name.required=true;
 const advanced=node('details','s-fold'),project=select(ctx.session.projects.map(p=>[p.id,p.name]),ctx.project,()=>{},'保存到项目');advanced.append(node('summary','','更多设置'),field('保存到项目',project));form.append(field('实验名称',name),field('研究方向',direction),field('实验来源',origin),field('研究问题',question),repro,advanced);const submit=node('button','s-btn primary','创建实验');submit.type='submit';form.append(submit);body.append(form);
 form.onsubmit=async event=>{event.preventDefault();if(origin.value==='reproduction'&&!method.value.trim())return ctx.toast('请填写复现方法。');submit.disabled=true;try{
  let projectId=project.value;const existing=ctx.workspace.experiments.find(e=>e.family===direction.value&&e.family!=='baseline');if(existing&&project.value===ctx.project)projectId=existing.experiment.project_id;
  if(!projectId){projectId='research-'+direction.value;await ctx.post('studio/project',{id:projectId,name:familyLabel(direction.value)});}
  const id='exp-'+Date.now(),profile={experiment_id:id,track:origin.value,question:question.value,binding_origin:'prospective'};if(origin.value==='reproduction')profile.reproduction={method_name:method.value,source_url:paper.value,repository_url:repo.value,protocol_mode:'unknown'};
  await ctx.post('studio/experiment',{experiment:{id,name:name.value,project_id:projectId,objective:question.value},profile});await ctx.post('studio/presentation',{experiment_id:id,family:direction.value,is_baseline:false});closeModal();location.hash=href('experiments',{family:direction.value});await ctx.refresh();
 }catch(err){ctx.toast(err.message);submit.disabled=false;}};
}
function importResults(ctx){
 if(ctx.session.team?.enabled){location.hash=href('team',{project:ctx.project,track:ctx.track});return;}
 const body=modal('导入已有结果'),input=node('input');input.type='file';input.accept='.json';body.append(node('p','s-muted','选择 schema_version=1/2 的结果包 JSON。较大的结果包可继续使用原 CLI 导入。'),input);input.onchange=async()=>{try{const file=input.files[0];if(!file)return;if(file.size>1024*1024)throw Error('此结果包超过网页导入大小，请使用 CLI import。');await ctx.post('studio/import',JSON.parse(await file.text()));closeModal();await ctx.refresh();ctx.toast('结果已接入，可在实验行中整理研究方向。');}catch(err){ctx.toast(err.message);}};
}
