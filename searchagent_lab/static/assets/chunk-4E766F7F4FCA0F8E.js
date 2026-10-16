/** One shared baseline comparison for every research direction. */
import {node,button,link,href,badge,select,field,empty,card,controls,table,addRow} from './chunk-F51FD76F14331921.js';

const percent=n=>Number.isFinite(n)?n.toFixed(2)+'%':'—';
const metricNames={em_pct:'EM',f1_pct:'F1',accuracy_pct:'原定义 Accuracy',logged_success_pct:'日志成功率',logged_score_pct:'日志分数'};
const macroKey=key=>'macro_'+key;
function autoMetric(aggregate){return ['em_pct','logged_success_pct','logged_score_pct','accuracy_pct','f1_pct'].find(k=>Number.isFinite(aggregate?.[macroKey(k)])||(aggregate?.datasets||[]).some(d=>Number.isFinite(d[k])))||'em_pct';}
const statusLabels={baseline:'共同基线',missing_baseline:'同底座基线待接入',missing_results:'汇总结果待接入',reference_only:'仅作参考',comparable:'同口径对照'};

export async function mountReference(host,ctx){
 let dead=false,epoch=0,selection=ctx.q.get('baseline_run')||'',metric='auto';
 const box=card('w-reference-card');box.setAttribute('aria-busy','true');box.append(node('div','w-loading','正在读取七集汇总与原生基线…'));host.append(box);
 async function load(){const token=++epoch;try{const data=await ctx.api('studio/reference',{experiment:ctx.experiment.experiment.id,run:ctx.run.id,baseline_run:selection});if(dead||!ctx.alive()||token!==epoch)return;render(data);}catch(error){if(!dead&&ctx.alive()&&token===epoch)box.replaceChildren(empty('基线汇总读取失败：'+error.message),button('重试',load,'s-btn'));}finally{if(token===epoch)box.setAttribute('aria-busy','false');}}
 function render(data){box.replaceChildren();const comparison=data.comparison||{},current=data.current||{},reference=data.baseline||data.available_reference,original=comparison.status==='baseline';
  const key=metric==='auto'?autoMetric(current):metric,referenceKey=metric==='auto'?autoMetric(reference?.aggregate):key;
  const heading=node('div','w-reference-head'),left=node('div');left.append(node('span','w-anchor',original?'BASELINE':'BASELINE COMPARISON'),node('h2','',original?'七集问答基线':'与原生 Qwen 基线对照'));
  const tools=controls();const options=[['auto','默认指标'],...Object.entries(metricNames).filter(([k])=>(current.datasets||[]).some(d=>Number.isFinite(d[k]))).map(([k,label])=>[k,label])];
  if(options.length>2)tools.append(select(options,metric,v=>{metric=v;render(data);},'汇总指标'));
  tools.append(badge(statusLabels[comparison.status]||'对照口径待核对',comparison.compatible?'good':original?'purple':'muted'));heading.append(left,tools);box.append(heading);
  const context=node('div','w-reference-context'),cur=node('div');cur.append(node('span','s-muted',original?'原生模型':'B · 当前实验'),node('strong','',data.run?.base_model||'底座未登记'),node('small','s-muted',current.step!=null?'step '+current.step:''));context.append(cur);
  const summary=node('div');summary.append(node('span','s-muted',(current.dataset_count===7?'七集宏平均 ': '宏平均 ')+metricNames[key]),node('strong','w-number',percent(current[macroKey(key)])),node('small','s-muted',(current.sample_count?.toLocaleString('en-US')||'—')+' 题 · '+(current.dataset_count||0)+' 个数据集'));context.append(summary);
  if(!original){const ref=node('div');ref.append(node('span','s-muted',data.baseline?'A · 匹配基线':'A · 已接入参考'),node('strong','',reference?.run?.base_model||'尚未接入'),node('small','s-muted',comparison.status==='missing_baseline'?'缺少当前底座的原生结果':metricNames[referenceKey]));context.append(ref);}box.append(context);
  if((data.baseline_candidates||[]).length>1&&!original)box.append(field('选择原生基线',select([['','自动匹配同底座'],...data.baseline_candidates.map(x=>[x.run_id,x.base_model+' · '+x.name])],selection,v=>{selection=v;ctx.setViewState?.({baseline_run:v});load();},'原生基线运行')));
  if(!original&&(comparison.reasons||[]).length){const notice=node('details','w-comparison-note'),line=node('summary','');line.append(node('span','',comparison.reasons[0]),node('small','s-muted',' 查看对照条件'));notice.append(line);const list=node('ul');for(const reason of comparison.reasons)list.append(node('li','',reason));notice.append(list);box.append(notice);}
  if(!current.dataset_count){box.append(node('div','w-empty-inline','此运行尚未保存完整数据集的汇总指标。下面可查看已接入的逐题评测。'));return;}
  const headers=['数据集',...(original?[]:[`A · ${reference?.run?.base_model||'原生基线'} · ${metricNames[referenceKey]}`]),`${original?'原生结果':'B · 当前'} · ${metricNames[key]}`, '题数',...(!original?['Δ '+metricNames[key]+'（pp）']:[])];
  const t=table(headers);t.t.classList.add('w-reference-table');
  const rows=data.rows?.length?data.rows:(current.datasets||[]).map(r=>({id:r.id,label:r.label,current:r}));
  for(const row of rows){const base=row.reference||row.baseline,values=[row.label||row.id,...(!original?[percent(base?.[referenceKey])]:[]),percent(row.current?.[key]),row.current?.sample_count?.toLocaleString('en-US')||'—'];
   if(!original){const deltaKey={em_pct:'delta_em_pp',f1_pct:'delta_f1_pp',accuracy_pct:'delta_accuracy_pp'}[key];const n=comparison.compatible&&key===referenceKey&&deltaKey?row[deltaKey]:null;const delta=node('span',Number.isFinite(n)?n>0?'w-positive':n<0?'w-negative':'':'s-muted',Number.isFinite(n)?(n>0?'+':'')+n.toFixed(2):'—');values.push(delta);}
   const tr=addRow(t.body,values);tr.dataset.referenceDataset=row.id;
  }
  const macroCurrent=current[macroKey(key)],macroReference=reference?.aggregate?.[macroKey(referenceKey)];const macroDelta=comparison.compatible&&key===referenceKey&&Number.isFinite(macroCurrent)&&Number.isFinite(macroReference)?macroCurrent-macroReference:null;
  const values=['宏平均',...(!original?[percent(reference?.aggregate?.[macroKey(referenceKey)])]:[]),percent(current[macroKey(key)]),'7 集等权',...(!original?[Number.isFinite(macroDelta)?(macroDelta>0?'+':'')+macroDelta.toFixed(2):'—']:[])];if(current.dataset_count!==7)values[values.length-(original?1:2)]=(current.dataset_count||0)+' 集';addRow(t.body,values,'w-macro-row');box.append(t.wrap);
  const foot=node('div','w-reference-foot');if(current.note)foot.append(node('span','',current.note));if(!original&&reference)foot.append(link('打开原生基线 →',href('experiment',{experiment:reference.experiment.id,run:reference.run.id,project:reference.experiment.project_id,track:'main'}),'s-link'));box.append(foot);
 }
 await load();return()=>{dead=true;epoch++;};
}
