/** Small, shared views over recorded evaluation evidence. No scoring or model calls. */
import {node,button,badge,controls,table,addRow,jsonModal,empty,fmt,modal} from './common.js';

import {trainingNumber} from './training-summary.js';

export const axisOptions=[['checkpoint','Checkpoint · 固定 Harness'],['harness','Harness · 固定模型'],['method_state','方法状态 / SkillBank · 固定模型']];
export const evaluationAxis=value=>['harness','method_state'].includes(value)?value:'checkpoint';
const short=value=>typeof value==='string'&&value.length>20?value.slice(0,16)+'…':value;
const show=value=>value==null||value===''?'未记录':typeof value==='string'?value:JSON.stringify(value);

export function renderEvaluationContext(host,data){
 host.replaceChildren();if(!data?.context?.model)return;
 const model=data.context?.model||{},harness=data.context?.harness||{};
 const row=node('div','ev-context');
 row.append(badge(data.purpose?.label||'评测用途未登记'),node('span','ev-context-model',model.base_model||'模型未记录'));
 if(model.checkpoint_id)row.append(node('span','s-muted',model.kind==='model_snapshot'?'模型快照':'Checkpoint '+(model.step??'未记录')));
 const h=node('span','s-muted','Harness '+short(harness.version||harness.fingerprint||'版本未记录'));h.title=show(harness.version||harness.fingerprint);row.append(h);
 row.append(button('执行条件',()=>jsonModal('本次执行的模型、Harness 与评测条件',data.context||{}),'s-link'));
 host.append(row);
}

export function renderExecutionDetails(data){
 const box=node('details','ev-details'),summary=node('summary','','执行记录 · 终止原因、消耗与工程检查');box.append(summary);
 const body=node('div','ev-detail-body');box.append(body);
 if(!data){body.append(empty('执行摘要暂未接入。原始结果与轨迹仍可查看。'));return box;}
 const outcomes=data.outcomes||{},usage=data.usage||{};
 const chips=node('div','ev-observations');
 for(const [key,label] of [['search_calls','搜索次数'],['tool_calls','工具调用'],['latency_ms','耗时'],['input_tokens','输入 tokens'],['output_tokens','输出 tokens']]){
  const v=usage[key];if(!v?.count)continue;
  const val=key==='latency_ms'?fmt(v.mean/1000,2)+' s':fmt(v.mean,1);
  const d=node('div');d.append(node('small','s-muted','平均'+label),node('strong','',val),node('small','s-muted',v.count+' 条有记录'+(v.missing?' · '+v.missing+' 条未记录':'')));chips.append(d);
 }
 if(chips.children.length)body.append(chips);
 const lists=node('div','ev-breakdowns');
 for(const [key,label] of [['termination_reasons','终止原因'],['symptoms','已观察到的现象']]){
  const entries=outcomes[key]||[];if(!entries.length)continue;
  const d=node('div');d.append(node('h3','',label));
  for(const item of entries){const row=node('div','ev-breakdown');row.append(node('span','',item.label||item.name),node('strong','',String(item.count)));d.append(row);}lists.append(d);
 }
 if(lists.children.length)body.append(lists);
 body.append(node('p','s-muted','现象用于定位执行位置；根因需要固定模型或固定 Harness 的对照证据。'));
 const checks=Array.isArray(data.checks)?data.checks:[];
 body.append(controls(button('Harness 工程检查'+(checks.length?' · '+checks.length+' 份':' · 待接入'),()=>showHarnessChecks(checks),'s-link'),button('完整执行摘要',()=>jsonModal('记录覆盖、原始指标与缺失字段',data),'s-link')));
 return box;
}

export function showHarnessChecks(checks){
 const body=modal('Harness 工程检查');
 body.append(node('p','s-subtitle','固定模型响应与工具返回，检查解析、停止、工具结果关联等执行逻辑。检查通过数与问答 EM 分别保存。'));
 if(!checks.length){body.append(empty('此运行尚未接入工程检查结果。可以先单独运行已有 Harness 的固定输入检查，再将实际结果挂到这里。'));return;}
 for(const c of checks){
  body.append(node('h3','',c.name||c.id||'已记录的检查'),node('p','s-muted',(c.passed??'—')+' / '+(c.total??'—')+' 通过 · '+show(c.harness_version)));
  const t=table(['检查项','结果','已记录的说明']);
  for(const item of c.checks||[]){const passed=item.passed;addRow(t.body,[item.name||item.id,passed===true?badge('通过','good'):passed===false?badge('未通过','bad'):badge('未判定'),item.reason||item.message||'—']);}
  body.append(t.wrap,button('检查输入与实际输出',()=>jsonModal('原始工程检查记录',c),'s-link'));
 }
}

export function renderControlComparison(pair){
 const box=node('details','ev-comparison');
 const legacy=pair?.legacy===true||!pair?.axis;
 const label=legacy?'历史报告 · 原比较协议':pair?.axis==='harness'?'Harness 对照 · 固定模型':pair?.axis==='method_state'?'方法状态 / SkillBank 对照 · 固定模型':'Checkpoint 对照 · 固定 Harness';
 box.append(node('summary','',label+' · '+(legacy?'保留当时记录':pair?.compatible?'已登记条件可比':'待核对条件')));
 const body=node('div','ev-detail-body');box.append(body);
 if(pair?.controls?.length){const t=table(['控制项','状态','A','B']);for(const c of pair.controls)addRow(t.body,[c.name,({same:'一致',changed:'已变化',missing:'未记录'})[c.status]||c.status,show(c.a),show(c.b)]);body.append(t.wrap);}
 if(pair?.reasons?.length){const list=node('ul');for(const reason of pair.reasons)list.append(node('li','',reason));body.append(list);}
 body.append(node('p','s-muted',pair?.claim||'展示已保存的结果；条件未核对时，不将差异归因为训练或 Harness 的改进。'));
 if(pair?.method_state_differences?.length)body.append(button('SkillBank 与检索配置差异',()=>jsonModal('本次方法状态对照',pair.method_state_differences),'s-link'));
 const usage=Object.entries(pair?.paired_usage||{}).filter(([,v])=>v.paired>0);
 if(usage.length){const names={latency_ms:'耗时（ms）',search_calls:'搜索次数',tool_calls:'工具调用',model_calls:'模型调用',input_tokens:'输入 tokens',output_tokens:'输出 tokens',total_tokens:'总 tokens'};const t=table(['配对消耗','A 均值','B 均值','B − A','共同记录数']);for(const[k,v]of usage)addRow(t.body,[names[k]||k,fmt(v.mean_a,1),fmt(v.mean_b,1),(v.mean_delta>0?'+':'')+fmt(v.mean_delta,1),v.paired+' / '+pair.paired]);body.append(t.wrap,node('p','s-muted','只比较双方都有记录的同题消耗；总 tokens 需要输入和输出均已记录。'));}
 if(pair?.configuration_differences?.length)body.append(button('全部配置差异',()=>jsonModal('两次执行实际改变的条件',pair.configuration_differences),'s-link'));
 return box;
}

export function renderAnswerEvidence(doc,ev,{training=false}={}){
 const box=node('div','ev-answer-evidence'),v=doc.view||{},raw=doc.raw?.type==='trajectory'?doc.raw.data:doc.raw;
 const fields=[['模型最终输出',v.raw_final_output],['实际解析答案',v.parsed_answer],['标准答案 / 别名',(v.gold_answers||[]).join(' / ')]];
 for(const [label,value] of fields){const d=node('div','ev-answer-field');d.append(node('small','s-muted',label));const text=show(value);const content=node('div','ev-answer-text',text);content.title=text;d.append(content);if(text.length>130)d.append(button('展开原文',()=>jsonModal(label,value),'s-link'));box.append(d);}
 const score=node('div','ev-answer-field');score.append(node('small','s-muted',training?'采样任务结果':'已记录的评分'));
 const em=v.scores?.em;
 if(em!=null||!v.scorer_results?.length)score.append(badge(em===1?'EM 1 · 正确':em===0?'EM 0 · 错误':'未记录 EM',em===1?'good':em===0?'bad':''));
 for(const entry of v.scorer_results||[])score.append(button(entry.name+' · '+show(entry.value),()=>jsonModal('原日志评分：'+entry.name,entry),'s-link'));
 const scorer=ev?.protocol?.scorer;const reason=raw?.scoring_provenance?.reason||raw?.score_reason||v.scoring_reason;
 if(reason)score.append(node('small','s-muted',show(reason)));
 if(scorer)score.append(button(typeof scorer==='object'?(scorer.name||'评分依据'):'评分依据',()=>jsonModal('评分器与实际判分依据',{scorer,provenance:raw?.scoring_provenance,scores:v.scores}),'s-link'));
 if(v.termination_reason)score.append(node('small','s-muted','终止：'+show(v.termination_reason)));box.append(score);
 return box;
}

export function renderTrainingRecord(host,record,doc){
 host.replaceChildren();const declared=['train_rollout','sft_demonstration'].includes(doc?.view?.record_purpose?.value);if(!record&&!declared)return;
 const raw=doc?.raw?.type==='trajectory'?doc.raw.data:doc?.raw;
 const sample=record?.sample||raw?.training||raw||{};
 const isSft=(record?.trace_role||doc?.view?.record_purpose?.value)==='sft_demonstration';
 const evidenceOnly=record&&!['train_rollout','sft_demonstration'].includes(record.trace_role);
 const box=node('section',evidenceOnly?'ev-training-context ev-batch-evidence':'ev-training-context');
 box.append(controls(badge(evidenceOnly?'批次关联证据':isSft?'SFT 监督示范':'训练采样',evidenceOnly?'':'purple'),node('span','s-muted',evidenceOnly?'以下字段属于所选批次样本；该执行未确认为训练采样。':'按训练 batch 查看 · 不计入固定题评测')));
 if(evidenceOnly)box.append(node('p','s-muted',record.trace_provenance?.notice||'引用关系不改变原始执行用途。'));
 const values=node('div','ev-training-values');
 const tokens=sample.input_ids||sample.token_ids||sample.response_ids||sample.response_token_ids;
 const mask=sample.loss_mask??sample.response_mask??sample.mask;
 const entries=[['Reward',trainingNumber(evidenceOnly?sample.reward:sample.reward??doc?.view?.reward)],['Advantage',Array.isArray(sample.advantage)?'已记录 '+sample.advantage.length+' 个值':trainingNumber(sample.advantage)],['训练 tokens',Array.isArray(tokens)?String(tokens.length):'未记录'],[sample.loss_mask!=null?'Loss mask':sample.response_mask!=null?'Response mask':'Mask',Array.isArray(mask)?mask.every(x=>x===0||x===1)?mask.filter(x=>x===1).length+' / '+mask.length+(sample.loss_mask!=null?' 计入 Loss':' 标记为 1'):'已记录 '+mask.length+' 个值':'未记录']];
 const shown=isSft?[['Loss',trainingNumber(sample.loss)],['labels',Array.isArray(sample.labels)?'已记录 '+sample.labels.length+' 个值':'未记录'],...entries.slice(2)]:entries;
 for(const [label,value] of shown){const d=node('div');d.append(node('small','s-muted',label),node('strong','',value==='—'?'未记录':value));values.append(d);}box.append(values);
 const identity=record?[(record.run_id||''),(record.batch_id||record.batch?.id||''),(record.group_id||record.group?.id||'')].filter(Boolean).join(' · '):'来自原记录的训练用途标记';
 box.append(controls(node('small','s-muted',identity),button('实际训练字段',()=>jsonModal('训练采样的原始字段',record||sample),'s-link')));
 host.append(box);
}

export function renderRecordedProgress(doc){
 const raw=doc.raw?.type==='trajectory'?doc.raw.data:doc.raw;
 const goals=Array.isArray(raw?.subgoals)?raw.subgoals:Array.isArray(raw?.progress?.subgoals)?raw.progress.subgoals:null;
 if(!goals?.length)return null;
 const box=node('details','ev-details');box.append(node('summary','','已记录的子目标 · '+goals.length+' 项'));
 const body=node('div','ev-detail-body'),t=table(['子目标','结果','证据']);
 for(const g of goals){if(!g||typeof g!=='object')continue;const s=g.passed===true||g.completed===true?'已完成':g.passed===false||g.completed===false?'未完成':g.status||'未判定';addRow(t.body,[g.name||g.id||g.description||'未命名',s,typeof g.evidence==='string'?g.evidence:typeof g.evidence_ref==='string'?g.evidence_ref:'未记录']);}
 body.append(t.wrap,node('p','s-muted','按原日志的可验证子目标展示；搜索次数和轨迹长度不折算为任务进度。'));box.append(body);return box;
}
