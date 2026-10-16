/** Result-first experiment view. Real saved scores; read-only, no model calls. */
import {node,button,link,href,select,field,section,empty,card,count,pct,controls,scoreCell,gt,badge,labels,table,addRow,kpi,pager,jsonModal} from './common.js';
import {experimentParams} from './execution-state.js';
import {problemCell} from './problem-summary.js';
import {recordedGroup} from './trace-model.js';
import {mountReference} from './reference.js';
import {axisOptions,evaluationAxis,renderEvaluationContext,renderExecutionDetails,renderControlComparison} from './evaluation-ui.js';
export async function mountResults(host,ctx){
 const {api,q,run,alive}=ctx;
 if(!run){host.append(empty('当前尚未登记执行运行。接入 Agent 评测可以使用独立模型快照；已有方法产物仍可查看。'));return;}
 const aggregateHost=node('div'),referenceFold=node('details','ex-method-fold cl-reference-fold');referenceFold.append(node('summary','','完整集汇总 / 原生基线'),aggregateHost);if(!ctx.diagnosticsOnly)host.append(referenceFold);const referenceTask=ctx.diagnosticsOnly?Promise.resolve(null):mountReference(aggregateHost,ctx);
 if(!ctx.diagnosticsOnly){
  const params={project:ctx.project,track:ctx.track,experiment:ctx.experiment.experiment.id,run:run.id,view:'trace',trace_scope:'full'};
  host.append(controls(link('查看本次评测全部题目',href('experiment',params),'s-btn'),link('查看本次评测错题',href('experiment',{...params,trace_filter:'incorrect'}),'s-btn'),link('固定题诊断',href('experiment',{...params,view:'diagnostics',trace_scope:''}),'s-link')));
  host.append(node('p','s-muted','单条轨迹按所选评测读取全部逐题记录。固定 11 / 20 题是独立诊断评测；缺少步骤日志时仍可查看已保存的答案与评分。'));
  return await referenceTask;
 }
 let data,evaluationSummary=null,axis=evaluationAxis(q.get('result_axis')),filter=q.get('result_filter')||'all',term=q.get('result_q')||'',dataset=q.get('result_dataset')||'',page=Math.max(0,Number(q.get('result_page'))||0),epoch=0,summaryEpoch=0,dead=false;
 let panel=q.get('panel')||'',b=q.get('result_b')||'',a=q.get('result_a')||'';
 const problemCache=new Map(),problemPending=new Map();
 const summariesFor=ids=>{const key=JSON.stringify([b,ids]);if(problemCache.has(key))return Promise.resolve(problemCache.get(key));if(problemPending.has(key))return problemPending.get(key);const task=api('inspection/summaries',{evaluation:b,ids:JSON.stringify(ids)}).then(r=>{problemCache.set(key,r);return r;}).finally(()=>problemPending.delete(key));problemPending.set(key,task);return task;};
 if(b.startsWith('aggregate:')){b='';a='';panel='';}
 const selectors=node('div','f-results-select'),summary=node('div'),context=node('div'),content=card('f-result-table');
 const diagnosticHeading=node('div','w-diagnostic-title');diagnosticHeading.append(node('h2','','逐题评测'),node('span','s-muted','逐题结果与轨迹'));host.append(diagnosticHeading,selectors,context,summary,content);
 const updateURL=()=>ctx.setViewState?.({panel,result_b:b,result_a:a,result_filter:filter==='all'?'':filter,result_q:term,result_dataset:dataset,result_page:page||'',result_axis:a?axis:''});
 async function load(){const token=++epoch;content.setAttribute('aria-busy','true');try{
  const d=await api('studio/results',{run:run.id,panel,b,a,scope:'diagnostic'});if(dead||!alive()||token!==epoch)return;data=d;panel=d.panel?.id||'';b=d.report?.b.id||'';evaluationSummary=null;
  if(a&&d.report){d.report.pair={compatible:false,reasons:['正在核对对比条件'],axis,pending:true};d.report.items.forEach(r=>r.group='unpaired');}render();
  const requests=await Promise.allSettled([api('studio/evaluation-summary',{run:run.id,evaluation:b}),a&&b?api('studio/evaluation-pair',{a,b,axis}):Promise.resolve(null)]);
  if(dead||!alive()||token!==epoch)return;
  if(requests[0].status==='fulfilled')evaluationSummary=requests[0].value;
  if(a&&d.report){if(requests[1].status==='fulfilled'){const pair=requests[1].value;d.report.pair=pair;if(Array.isArray(pair?.items))d.report.items=pair.items;}
   else d.report.pair={compatible:false,reasons:['对比条件暂未读到，请重试'],axis};}
  render();
 }catch(e){if(!dead&&alive()&&token===epoch)content.replaceChildren(empty(e.message),button('重试',load,'s-btn'));}finally{if(token===epoch)content.setAttribute('aria-busy','false');}}
 function render(){updateURL();selectors.replaceChildren();renderEvaluationContext(context,evaluationSummary);const panels=(ctx.session.panels||[]).filter(p=>p.split!=='test');
 if(!data.evaluations.length&&!b){selectors.hidden=false;content.hidden=true;selectors.append(field('固定诊断题单',select(panels.map(p=>[p.id,`${p.name} · ${p.size} 题`]),panel,id=>{panel=id;b='';a='';page=0;load();},'结果固定题单')));summary.replaceChildren(node('div','w-diagnostic-empty','本运行在这份固定诊断题单上尚未接入评测结果。完整评测请从单条轨迹页选择，两者独立。'));if(evaluationSummary?.checks?.length)summary.append(renderExecutionDetails(evaluationSummary));return;}
 selectors.hidden=false;content.hidden=false;
 selectors.append(field('固定题单',select(panels.map(p=>[p.id,`${p.name} · ${p.size} 题`]),panel,id=>{panel=id;b='';a='';page=0;dataset='';filter='all';load();},'结果固定题单')));
 const options=data.evaluations.map(e=>[e.id,`${e.checkpoint.name} · ${e.id}`]);
 selectors.append(field('当前评测 B',select(options,b,id=>{b=id;if(a===b)a='';page=0;load();},'实验当前评测 B')),
  field('对照评测 A',select([['','不对比'],...(data.candidates||data.evaluations.filter(e=>e.id!==b).map(e=>({evaluation:e,same_run:true}))).map(c=>[c.evaluation.id,(c.same_run?'本运行':c.same_project?'同项目':'跨项目，仅浏览')+' · '+(c.evaluation.checkpoint?.name||c.evaluation.id)+' · '+c.evaluation.id])],a,id=>{a=id;page=0;filter='all';load();},'实验对照评测 A')));
 if(a)selectors.append(field('对比目标',select(axisOptions,axis,value=>{axis=value;page=0;filter='all';load();},'实验对比目标')));
 summary.replaceChildren();const report=data.report;
 if(report){const ev=report.b,pair=report.pair,k=node('div','f-kpis');k.append(kpi('当前评测 · 正确数',count(ev.stats),ev.stats.fully_scored?`数据集宏平均 EM ${pct(ev.stats.macro_em)}`:`${ev.stats.scored}/${ev.stats.expected} 已评分；未评分不补零`),kpi('执行覆盖',`${ev.stats.recorded} / ${ev.stats.expected}`,`${Math.max(0,ev.stats.expected-ev.stats.recorded)} 题待执行 · ${Math.max(0,ev.stats.recorded-ev.stats.scored)} 题待评分`),kpi('同题变化',pair?.compatible?`↑ ${pair.groups.rescued}   ↓ ${pair.groups.harmed}`:'—',pair?.compatible?(pair.full?'错→对 / 对→错':'只计已评分配对，不代表完整提升'):a?'条件不一致；只浏览已记录答案差异':'选择对照 A 后查看修复与退化'));summary.append(k);
  const tools=controls(button('评测协议',()=>jsonModal('已记录的评测条件',{protocol:ev.protocol,method_state:ev.method_state}),'s-link'),button('查看 GT',()=>gt(api,panel),'s-link'),link('完整对比报告 ↗',href('reports',{project:ctx.project,track:ctx.track,b,a,axis:a?axis:''}),'s-link'));summary.append(section('逐题结果',tools));
  if(ev.note)summary.append(node('p','s-muted',ev.note));
  if(pair)summary.append(renderControlComparison(pair));
  if(evaluationSummary)summary.append(renderExecutionDetails(evaluationSummary));
 }else summary.append(empty(data.panel?'本运行尚未在这份题单上保存评测。参考题目仍可查看。':'工作区尚无固定题单。'),link('管理固定题单',href('versions',{project:ctx.project,track:ctx.track}),'s-link'));
 buildTable();
 }
 function buildTable(){content.replaceChildren();const report=data.report,rows=report?.items||data.panel?.samples.map(sample=>({sample,a:null,b:null,group:'unpaired'}))||[],paired=!!report?.a;
 for(const r of rows)r.displayGroup=recordedGroup(r.a,r.b);
 const searchbar=node('div','f-search'),search=node('input');search.type='search';search.value=term;search.placeholder='搜索题目、ID 或模型答案…';search.setAttribute('aria-label','搜索逐题结果');
 const ds=select([['','全部数据集'],...[...new Set(rows.map(r=>r.sample.dataset))].sort().map(x=>[x,x])],dataset,v=>{dataset=v;page=0;updateURL();renderRows();},'结果数据集');
 const filters=controls();const groups=[['all','全部'],['incorrect','错误'],['error','运行异常'],['unscored','未评分 / 缺失'],...(paired?[['rescued','错→对'],['harmed','对→错'],['both_wrong','两次均错'],['both_correct','两次均对']]:[])];if(!groups.some(([id])=>id===filter)){filter='all';updateURL();}
 const isError=r=>!!r.b&&r.b.runtime_status&&!['ok','unknown'].includes(r.b.runtime_status);
 const match=(r,key)=>key==='all'||key==='incorrect'&&r.b?.scores?.em===0||key==='error'&&isError(r)||key==='unscored'&&(r.b?.scores?.em==null||(a&&r.a?.scores?.em==null))||['rescued','harmed','both_wrong','both_correct'].includes(key)&&r.displayGroup===key;
 for(const [key,label]of groups){const bt=button(`${label} ${rows.filter(r=>match(r,key)).length}`,()=>{filter=key;page=0;updateURL();for(const button of filters.children)button.classList.toggle('active',button.dataset.resultFilter===key);renderRows();},'s-filter '+(filter===key?'active':''));bt.dataset.resultFilter=key;filters.append(bt);}
 searchbar.append(search,ds);content.append(searchbar,node('div','f-search'));content.lastChild.append(filters);
 const rowsHost=node('div');content.append(rowsHost);
 search.oninput=()=>{term=search.value;page=0;updateURL();renderRows();};
 function renderRows(){const t=table(['题目 / 数据集',...(a?['A · 对照']:[]),a?'B · 当前':'模型结果',...(a?['答案变化']:[]),'问题摘要','参考','轨迹']);const query=term.toLocaleLowerCase();const selected=rows.filter(r=>(!dataset||r.sample.dataset===dataset)&&match(r,filter)&&(!query||[r.sample.question,r.sample.id,r.sample.dataset,r.b?.parsed_answer,r.a?.parsed_answer].join(' ').toLocaleLowerCase().includes(query)));
 page=Math.min(page,Math.max(0,Math.ceil(selected.length/50)-1));
 for(const row of selected.slice(page*50,page*50+50)){const question=node('div','f-question');question.append(node('span','s-muted',row.sample.dataset+' · '+row.sample.id),node('strong','',row.sample.question));const answer=x=>{const d=node('div');d.append(scoreCell(x));if(x?.parsed_answer){const text=node('div','f-answer',x.parsed_answer);text.title=x.parsed_answer;d.append(text);}if(x&&isError({b:x}))d.append(badge('运行异常','warn'));return d;};
 const values=[question,...(a?[answer(row.a)]:[]),answer(row.b),...(a?[labels[row.displayGroup]||'未配对']:[]),(()=>{const c=node('div','cl-problem-cell','读取问题摘要…');c.dataset.problemTrajectory=row.b?.id||'';return c;})(),button('GT',()=>gt(api,panel,row.sample.id,row.sample.dataset),'s-link')];
 const traceParams=experimentParams(ctx,{project:ctx.project,track:ctx.track,experiment:ctx.experiment.experiment.id,run:run.id,view:'trace',trace_scope:'diagnostic',result_b:b,result_a:a,panel,trace_sample:row.sample.id,trace_dataset:row.sample.dataset,trace_step:'',trace_field:'',result_axis:axis});
 values.push(row.b?controls(link('查看轨迹 →',href('experiment',traceParams),'s-link'),...(a?[link('同题对比',href('experiment',{...traceParams,view:'trace-compare'}),'s-link')]:[])):node('span','s-muted','尚无轨迹'));const tr=addRow(t.body,values);tr.dataset.resultSample=row.sample.id;tr.classList.toggle('is-current',q.get('trace_sample')===row.sample.id&&q.get('trace_dataset')===row.sample.dataset);
 }
 if(!selected.length)addRow(t.body,['没有匹配题目。请清除搜索或切换筛选。']);
 rowsHost.replaceChildren(t.wrap,pager(selected.length,page,50,n=>{page=n;updateURL();renderRows();}));
 const revision=++summaryEpoch,cells=[...rowsHost.querySelectorAll('[data-problem-trajectory]')],ids=cells.map(c=>c.dataset.problemTrajectory).filter(Boolean);
 for(const c of cells)if(!c.dataset.problemTrajectory)c.textContent='尚无执行记录';
 if(ids.length)summariesFor(ids).then(response=>{
  if(dead||!alive()||revision!==summaryEpoch)return;
  const map=new Map(response.items.map(item=>[item.trajectory_id,item]));for(const c of cells)if(c.dataset.problemTrajectory&&c.isConnected)c.replaceChildren(problemCell(map.get(c.dataset.problemTrajectory)));
 }).catch(()=>{if(!dead&&alive()&&revision===summaryEpoch)for(const c of cells)if(c.dataset.problemTrajectory)c.textContent='摘要暂不可用；仍可查看轨迹';});
 }
 renderRows();
 }
 const [,disposeReference]=await Promise.all([load(),referenceTask]);return()=>{dead=true;epoch++;summaryEpoch++;disposeReference?.();};
}
