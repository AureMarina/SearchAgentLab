/** Self-play evidence is scoped to a run, a round and two explicit fixed references. */
import {node,button,link,href,badge,select,field,section,empty,controls,table,addRow} from './common.js';
import {JsonTree} from '../inspector/json-tree.js';
import {openSelfplayIntegration} from './selfplay-discovery.js';

const array=value=>Array.isArray(value)?value:[];
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const known=value=>value==null||value===''?'未记录':typeof value==='object'?JSON.stringify(value):String(value);
const yes=value=>value===true?'是':value===false?'否':'未记录';
const names={passed:'通过',failed:'未通过',error:'验证异常',unknown:'未记录',conflicting:'记录冲突',accepted:'接受',rejected:'拒绝',needs_review:'待验证',not_run:'未运行',unique:'独立题',duplicate:'重复题',new:'本轮新题',old_first_use:'旧题首次消费',reused:'旧题复用',selected:'已选中',sampled:'已采样',excluded:'已排除',completed:'已完成',pending:'待完成',skipped:'已跳过',measured:'已记录',partial:'部分记录',not_recorded:'未记录',unmeasured:'未测',incomparable:'条件不匹配',selection_required:'待选择评测',all_failed:'多次尝试全失败',partial_success:'部分成功',all_succeeded:'多次尝试全成功',incomplete:'尝试不完整 / 未评分',pool_supply:'新题供给对照',admitted:'入池',removed:'移出题池',training:'训练',reference:'固定参考',train_rollout:'训练采样',reference_probe:'固定参考探针'};
const display=value=>value==null||value===''?'未记录':names[value]||known(value);
function sameScope(value,scope){return !!value?.scope&&['experiment_id','run_id'].every(key=>String(value.scope[key]||'')===String(scope[key]||''))&&(!scope.project_id||!value.scope.project_id||scope.project_id===value.scope.project_id);}
function assertScope(value,scope){if(!sameScope(value,scope))throw Error('Self-play 记录与当前实验 / 运行归属不一致。');}
function assertComparison(value,scope){const s=value?.scope;if(!s||s.experiment_id!==scope.experiment_id||(s.run_id&&s.run_id!==scope.run_id)||(s.project_id&&scope.project_id&&s.project_id!==scope.project_id))throw Error('Self-play 对照不属于当前实验 / 运行。');}
function patch(ctx,state){for(const[key,value]of Object.entries(state)){if(value==null||value==='')ctx.q.delete(key);else ctx.q.set(key,String(value));}ctx.setViewState?.(state);}
function pair(label,value){const box=node('div','sp-pair');box.append(node('small','s-muted',label),node('div','',known(value)));return box;}
function fold(title,body,open=false,cls=''){const box=node('details','sp-fold '+cls);box.append(node('summary','',title),body);box.open=open;return box;}
function notes(host,values){for(const value of array(values))if(value)host.append(node('p','sp-note',known(value)));}
function raw(title,value){const body=node('div','sp-raw'),box=fold(title,body);let drawn=false;box.addEventListener('toggle',()=>{if(box.open&&!drawn){drawn=true;new JsonTree(body,value,{label:'已保存字段',open:false});}});return box;}
function metricValue(metric){
 if(['incomparable','unmeasured','not_measured','not_recorded','selection_required','unsupported'].includes(metric?.status))return display(metric.status);
 if(!finite(metric?.value))return display(metric?.status||'not_recorded');
 if(['ratio','fraction'].includes(metric.unit))return (metric.value*100).toFixed(1)+'%';
 if(metric.unit==='pp')return (metric.value>=0?'+':'')+Number(metric.value.toFixed(2))+' pp';
 return String(Number(metric.value.toFixed(3)))+(metric.unit&&!['count','tokens','tokens/task'].includes(metric.unit)?' '+metric.unit:'');
}
function metricCard(metric){
 const box=node('div');box.dataset.selfplayMetric=metric.id||'';box.dataset.status=metric.status||'not_recorded';
 box.append(node('small','s-muted',metric.label||metric.id),node('strong','',metricValue(metric)));
 const coverage=[];if(finite(metric.numerator)&&finite(metric.denominator))coverage.push(metric.numerator+' / '+metric.denominator);else if(finite(metric.denominator))coverage.push(metric.denominator+' 题');
 if(finite(metric.unknown)&&metric.unknown>0)coverage.push(metric.unknown+' 项未知');if(coverage.length)box.append(node('small','',coverage.join(' · ')));
 if(metric.missing_reason||metric.note)box.append(node('small','s-muted',metric.missing_reason||metric.note));return box;
}
function compact(value){if(Array.isArray(value))return value.length<=8?JSON.stringify(value):JSON.stringify(value.slice(0,8))+' … · '+value.length+' 项';return known(value);}
function appendText(host,label,value){if(value==null)return;const box=node('div','sp-content');box.append(node('h4','',label),node('pre','sp-text',typeof value==='string'?value:JSON.stringify(value,null,2)));host.append(box);}
function appendReference(host,ref,ctx,scope,label='查看关联轨迹'){
 if(!ref||typeof ref!=='object')return;const source=ref.trajectory?{trajectory:ref.trajectory}:ref.trajectory_id?{trajectory:ref.trajectory_id}:ref.object_id?{method:ref.object_id}:ref.file?{file:ref.file,record:ref.record}:null;if(!source)return;
 const state={};for(const key of ['selfplay_cycle','selfplay_reference','selfplay_evaluation','selfplay_question','selfplay_questions_open','selfplay_offset','selfplay_comparison','selfplay_comparison_open','selfplay_legacy','result_b','cp','panel'])state['return_'+key]=ctx.q.get(key);
 host.append(link(label+' →',href('replay',{...source,project:ctx.project,track:ctx.track,evaluation:ref.evaluation_id||ref.evaluation,step:ref.step,pointer:ref.pointer,return_experiment:scope.experiment_id,return_run:scope.run_id,return_view:ctx.q.get('view')||'training',...state}),'s-link'));
}
function objectFacts(host,object,fields,ctx,scope){
 const data=object.data||{},facts=node('div','sp-facts');for(const[label,key]of fields){const value=typeof key==='function'?key(data,object):data[key];facts.append(pair(label,value));}host.append(facts);
 for(const ref of [data.trace_ref,...array(object.evidence_refs)])appendReference(host,ref,ctx,scope);
 host.append(raw('已保存字段 · '+object.id,object));
}
function recordList(host,items,render,emptyText){
 if(!items.length){host.append(empty(emptyText));return;}
 for(const object of items){const body=node('div','sp-related-body'),box=fold(object.title||object.id,body);box.dataset.selfplayRelated=object.id;let drawn=false;box.addEventListener('toggle',()=>{if(box.open&&!drawn){drawn=true;render(body,object);}});host.append(box);}
}

function renderSignals(host,task,opd){
 const t=table(['实际学习项','有有效信号','明确无有效项','未知','总分母','覆盖范围']);t.t.classList.add('sp-signals-table');
 for(const[label,value]of[['任务 / RLVR',task||{}],['OPD 蒸馏',opd||{}]]){
  const bound=finite(value.lower_bound)&&finite(value.upper_bound)?(value.lower_bound*100).toFixed(1)+'% – '+(value.upper_bound*100).toFixed(1)+'%':'未记录';
  addRow(t.body,[label,known(value.active),known(value.inactive),known(value.unknown),known(value.total),bound]);
 }
 host.append(t.wrap,node('p','sp-note','未知样本保留在总分母中。这里反映实际 advantage、有效 mask 与学习项的记录覆盖；能力变化查看固定外部评测。'));
}
function externalFacts(host,external={},metrics=[],ctx,scope){
 const facts=node('div','sp-facts');
 for(const[label,value]of[['固定评测',external.evaluation_id],['Solver checkpoint',external.checkpoint_id],['题集 / 内容版本',external.panel_id?(external.panel_id+' · '+known(external.panel_hash)):null],['固定协议',external.protocol_hash],['已评分 / 固定题数',finite(external.scored)&&finite(external.expected)?external.scored+' / '+external.expected:null]])facts.append(pair(label,value));host.append(facts);
 if(external.demo)host.prepend(badge('合成示例 · 非真实实验','purple'));
 notes(host,[external.independence_basis,external.isolation_note]);notes(host,external.qualification_reasons);
 if(external.evaluation_id){const go=href('experiment',{project:ctx.project,track:ctx.track,experiment:scope.experiment_id,run:scope.run_id,view:'results',result_b:external.evaluation_id});host.append(link('查看固定评测与逐题轨迹 →',go,'s-link'));}
 const cost=metrics.find(item=>item.id==='tokens_per_task');if(cost)host.append(pair('每题 token 成本',metricValue(cost)),node('p','sp-note',cost.note||cost.missing_reason||''));
 if(array(external.excluded).length)host.append(raw('未作为独立外部评测的记录',external.excluded));
}

/** Follows the existing Agent result selector; it never substitutes a training reward. */
export async function mountSelfplayExecution(host,ctx,scope,options={}){
 let dead=false,epoch=0,evaluation=options.evaluationId??ctx.q.get('result_b')??'';const alive=()=>ctx.alive()&&!dead,box=node('section','sp-section sp-runtime');box.dataset.selfplayView='execution';host.append(box);
 async function load(){const token=++epoch;box.replaceChildren(section('Self-play 与固定外部评测'));
  if(options.requireEvaluation&&!evaluation){box.append(node('p','sp-note','选择上方 Agent 评测，查看本次 Solver 的固定题集、协议与独立性记录。'));return;}
  try{const response=await ctx.api('methods/selfplay-runtime',{experiment_id:scope.experiment_id,run_id:scope.run_id,evaluation_id:evaluation});if(!alive()||token!==epoch)return;assertScope(response,scope);
   if(evaluation&&response.external?.evaluation_id&&response.external.evaluation_id!==evaluation)throw Error('返回的外部评测与当前选择不一致。');
   box.replaceChildren(section('Self-play 与固定外部评测'));const metrics=array(response.metrics),metric=metrics.find(item=>item.id==='external_em');
   if(metric)box.append(node('p','sp-note',(metric.label||'固定外部评测 EM')+'：'+metricValue(metric)+(metric.missing_reason?' · '+metric.missing_reason:'')));
   externalFacts(box,response.external,metrics,ctx,scope);notes(box,response.notes);
  }catch(error){if(alive()&&token===epoch)box.replaceChildren(section('Self-play 与固定外部评测'),empty(error.message),button('重试固定评测记录',load,'s-btn'));}
 }
 await load();const dispose=()=>{dead=true;++epoch;};dispose.update=state=>{if(Object.hasOwn(state,'result_b')&&state.result_b!==evaluation){evaluation=state.result_b||'';load();}};return dispose;
}

async function renderQuestion(host,item,ctx,scope,valid,cycle){
 const response=await ctx.api('methods/selfplay-question',{id:item.id,cycle_id:cycle});if(!valid())return;assertScope(response,scope);assertScope(response.question,scope);
 if(response.question.id!==item.id)throw Error('生成题身份与当前选择不一致。');const question=response.question,data=question.data||{},a=response.assessment||item.assessment||{};host.replaceChildren();if(question.demo)host.append(badge('合成示例 · 非真实实验','purple'));
 appendText(host,'生成题',data.question);const identity=node('div','sp-facts');for(const[label,value]of[['稳定题目 ID',a.question_uid||data.question_uid],['首次生成轮次',a.first_generated_cycle_id||data.first_generated_cycle_id],['提出的答案',data.proposed_answer],['题目结构 / 跳数',data.hop_structure??data.hops??data.hop_count??(data.requested_hops!=null?{requested:data.requested_hops,verified:data.verified_hops??null}:null)]])identity.append(pair(label,value));host.append(identity);
 appendText(host,'支持证据 / 来源材料',data.supporting_evidence??data.evidence??data.context);
 for(const ref of array(question.evidence_refs))appendReference(host,ref,ctx,scope,'查看已保存支持材料');
 const origins=node('div');recordList(origins,array(response.cycles),(body,object)=>objectFacts(body,object,[['轮次',d=>d.round_index??d.round],['角色与版本','participants'],['出题条件','generation_input'],['种子 / 来源材料',d=>d.seed??d.generation_input?.seed??d.source_material],['验证器版本',d=>d.verifier_revision??d.verifier]],ctx,scope),'没有保存轮次与出题条件。');
 recordList(origins,array(response.proposals),(body,object)=>{objectFacts(body,object,[['出题者','actor_id'],['轮次','cycle_id']],ctx,scope);appendText(body,'Proposer 实际输入',object.data?.raw_input??object.data?.input);appendText(body,'Proposer 实际输出',object.data?.raw_output??object.data?.output);},'没有保存逐次出题输入与输出。');host.append(fold('轮次、角色与出题条件',origins));
 const verification=node('div');verification.append(pair('当前验证状态',display(a.validation_status)));
 recordList(verification,array(response.reviews),(body,object)=>{const d=object.data||{};objectFacts(body,object,[['验证结果',()=>display(d.verification?.status||d.validation_status||d.status)],['验证器 / 版本',()=>d.verifier_revision||d.reviewer||d.verification?.verifier],['格式检查',()=>d.format_check??d.checks?.format],['可回答性',()=>d.answerability??d.checks?.answerability],['拒绝 / 异常原因',()=>d.reason??d.error??d.verification?.reason],['确认的答案','verified_answer']],ctx,scope);},'没有保存验证记录。');host.append(fold('验证、拒绝与异常 · '+array(response.reviews).length,verification));
 const pool=node('div','sp-pool');pool.append(pair('去重结论',display(a.deduplication)),pair('使用分类',display(a.reuse_status)),pair('此前已完成消费次数',a.previous_completed_uses),pair('本轮已完成消费次数',a.completed_use_count),pair('重复题关联',data.duplicate_of));
 recordList(pool,array(response.pool_events),(body,object)=>objectFacts(body,object,[['题池事件',d=>display(d.event??d.status)],['稳定题目 ID','question_uid'],['发生时间',d=>d.occurred_at??d.timestamp],['本轮 / 实际批次',d=>d.cycle_id??d.batch_id],['复用次数','reuse_count'],['去重依据',d=>d.deduplication??d.reason]],ctx,scope),'尚未保存题池事件。');host.append(fold('去重、入池与复用 · '+array(response.pool_events).length,pool));
 const attempts=node('div'),rows=table(['Solver / 用途','得分 EM / reward','原始 advantage','训练实际项 / mask','尝试记录']);rows.t.classList.add('sp-attempt-table');
 for(const object of array(response.attempts)){const d=object.data||{},detail=node('div');detail.append(button('展开',()=>{const existing=attempts.querySelector('[data-selfplay-attempt="'+CSS.escape(object.id)+'"]');if(existing){existing.open=!existing.open;return;}const body=node('div'),box=fold('尝试 · '+(object.title||object.id),body,true);box.dataset.selfplayAttempt=object.id;objectFacts(body,object,[['Solver / checkpoint',v=>v.solver_revision??v.solver_checkpoint_id??v.checkpoint_id??v.actor_id],['采样协议 / reference',v=>v.reference_id??v.sampling_protocol],['尝试序号',v=>v.attempt_index],['尝试记录 advantage',v=>v.effective_advantage??v.advantage],['尝试记录 mask',v=>v.loss_mask??v.effective_mask??v.mask],['OPD 蒸馏信号',v=>v.opd_signal??v.signals?.opd],['实际批次',v=>v.batch_id]],ctx,scope);appendText(body,'实际输入',d.input??d.solver_input);appendText(body,'Solver 输出',d.output??d.response??d.answer);attempts.append(box);},'s-link'));
  const usage=array(response.training_uses).filter(use=>use.data?.attempt_id===object.id||array(use.data?.artifact_ids).includes(object.id)),actual=node('div','sp-actual-signal');
  for(const use of usage){const u=use.data||{},signal=u.task_signal||u.signals?.task||u.signal||{};actual.append(node('div','',known(u.batch_id)+' · advantage '+compact(signal.effective_advantages??signal.effective_advantage)+' / mask '+compact(signal.loss_mask??signal.effective_mask??signal.loss_mask_tokens)));if(u.opd_signal)actual.append(node('small','s-muted','OPD mask '+compact(u.opd_signal.loss_mask)+' · weight '+known(u.opd_signal.loss_weight)));}
  if(!usage.length)actual.append(node('span','s-muted','尚无关联训练使用记录'));
  addRow(rows.body,[(d.solver_revision||d.solver_checkpoint_id||d.checkpoint_id||d.actor_id||'未记录')+' · '+display(d.context||d.purpose||d.role),known(d.scores?.em)+' / '+known(d.reward),compact(d.advantage),actual,detail]);}
 if(array(response.attempts).length)attempts.prepend(rows.wrap);else attempts.append(empty('没有保存 Solver 尝试。'));attempts.append(node('p','sp-note','得分、reward 和实际学习项分别保留。题目难度只读取所选固定参考 Solver 的完整尝试。'));host.append(fold('多次解答与训练信号 · '+array(response.attempts).length,attempts));
 const training=node('div');training.append(pair('实际采样',yes(a.sampled)),pair('完成更新',yes(a.completed)),pair('本轮新增、有效且完成消费',yes(a.new_verified_consumed)));
 recordList(training,array(response.training_uses),(body,object)=>objectFacts(body,object,[['消费状态',d=>display(d.status)],['真实批次','batch_id'],['训练 step','step'],['实际采样的产物','artifact_ids'],['任务 advantage 与有效 mask',d=>d.task_signal??d.signals?.task??d.signal],['OPD 监督与有效 mask',d=>d.opd_signal??d.signals?.opd],['复用次数','reuse_count']],ctx,scope),'尚无关联的训练采样记录。');
 recordList(training,array(response.updates),(body,object)=>objectFacts(body,object,[['更新状态',d=>display(d.status)],['实际消费关联','consumption_ids'],['输出 checkpoint','output_checkpoint_id'],['更新证据',d=>d.parameter_update??d.update_evidence]],ctx,scope),'尚无关联的完成更新记录。');host.append(fold('实际采样批次与参数更新',training));
 notes(host,response.notes);if(response.truncation&&Object.values(response.truncation).some(v=>v===true||typeof v==='object'&&(v?.truncated||v?.has_more)))host.append(node('p','sp-note','关联记录较多，当前每类最多展示 100 条。完整原始记录仍保留。'));
 host.append(node('p','sp-note','题目摘要按所选轮次计算；关联记录保留各轮历史。'),raw('当前题目链与记录覆盖',response));
 const download=node('a','s-link','导出题目原始记录');download.href='/api/methods/export?id='+encodeURIComponent(item.id);download.setAttribute('download','');host.append(download);
}

function questionRows(host,response,ctx,scope,valid,selected,cycle){
 const items=array(response.items);if(selected&&!items.some(item=>item.id===selected))host.append(node('p','sp-note','所选题目不在当前轮次 / 当前页中；没有自动打开其他题。'));
 if(!items.length){host.append(empty('当前轮次尚无已保存的生成题。'));return;}
 for(const item of items){if(!sameScope(item,scope)){host.append(empty('一条题目记录的实验 / 运行归属不一致，已停止显示此条。'));continue;}const a=item.assessment||{},detail=node('details','sp-question'),head=node('summary','sp-question-summary'),identity=node('div','sp-identity');detail.dataset.selfplayQuestion=item.id;
  identity.append(node('strong','',a.question_preview||item.title||item.id),node('small','s-muted',(a.question_uid||item.id)+' · '+display(a.reuse_status)));if(item.demo)identity.append(badge('合成示例','purple'));
  const ref=finite(a.reference_successes)&&finite(a.reference_attempts)?a.reference_successes+' / '+a.reference_attempts+(finite(a.reference_expected)?'（目标 '+a.reference_expected+'）':''):'未记录';head.append(identity,pair('验证 / 去重',display(a.validation_status)+' · '+display(a.deduplication)),pair('固定参考 Solver',ref),pair('训练去向',a.completed===true?'完成更新':a.sampled===true?'已采样 · 更新未确认':'未记录实际消费'));
  const body=node('div','sp-question-body');detail.append(head,body);host.append(detail);let loaded=false,pending=false;
  async function load(){if(!detail.open||loaded||pending||!valid())return;pending=true;patch(ctx,{selfplay_question:item.id});body.replaceChildren(empty('读取原题、验证、解答与训练去向…'));try{await renderQuestion(body,item,ctx,scope,()=>valid()&&body.isConnected,cycle);if(valid())loaded=true;}catch(error){if(valid()&&body.isConnected)body.replaceChildren(empty(error.message),button('重试题目记录',load,'s-btn'));}finally{pending=false;}}
  detail.addEventListener('toggle',load);if(selected===item.id){detail.open=true;load();}
 }
}
function difficultyBody(host,data){
 const difficulty=data.difficulty||{},groups=difficulty.groups||{},row=node('div','sp-difficulty-counts');
 for(const[key,label]of[['all_failed','多次尝试全失败'],['partial_success','部分成功'],['all_succeeded','多次尝试全成功'],['incomplete','尝试不完整 / 未评分']])row.append(pair(label,groups[key]));host.append(row);
 const rounds=array(difficulty.rounds);if(rounds.length){const t=table(['生成轮次','全失败','部分成功','全成功','不完整','题数']);t.t.classList.add('sp-difficulty-table');for(const round of rounds){const r=addRow(t.body,[round.title||round.cycle_id,known(round.groups?.all_failed),known(round.groups?.partial_success),known(round.groups?.all_succeeded),known(round.groups?.incomplete),known(round.total)]);r.dataset.selfplayDifficultyRound=round.cycle_id;}host.append(t.wrap);}
 notes(host,[difficulty.note||'固定同一参考 Solver 与尝试协议后比较题目；跳数是结构记录，难度来自此 Solver 的成功次数。']);
 host.append(node('p','sp-note','不同方法可以使用不同出题目标，界面不预设 50% 成功率为最优。'));
}

export async function mountSelfplayComparisons(host,ctx,scope,options={}){
 const body=node('div'),box=fold('同预算：固定旧题池与持续新增有效题',body,false,'sp-comparison');box.dataset.selfplayComparisonPanel='';host.append(box);
 let dead=false,epoch=0,loaded=false,comparison=ctx.q.get('selfplay_comparison')||'',choices=array(options.items);const alive=()=>ctx.alive()&&!dead;
 async function selectComparison(){const token=++epoch,content=node('div');body.querySelector('.sp-comparison-body')?.remove();content.classList.add('sp-comparison-body');body.append(content);patch(ctx,{selfplay_comparison:comparison,selfplay_comparison_open:'1'});if(!comparison){content.append(node('p','sp-note','选择已保存的对照协议，核对初始 Solver、总预算与固定评测。'));return;}content.append(empty('读取同预算对照…'));
  try{const response=await ctx.api('methods/selfplay-comparison',{id:comparison});if(!alive()||token!==epoch)return;assertComparison(response.comparison,scope);if(response.comparison.id!==comparison)throw Error('Self-play 对照身份不匹配。');content.replaceChildren();content.dataset.selfplayQualified=String(response.comparison_qualified===true);
   if(response.comparison.demo)content.append(badge('合成示例 · 非真实实验','purple'));content.append(badge(display(response.status),response.comparison_qualified?'good':'muted'));
   const t=table(['题池策略','Solver / 运行','实际总预算','固定外部 EM','供给与消费']);t.t.classList.add('sp-budget-table');
   for(const arm of array(response.arms)){const result=arm.result||{},budget=arm.budget||{},identity=node('div');identity.append(node('span','',known(arm.checkpoint_id)));if(arm.run_id)identity.append(link('打开运行 →',href('experiment',{project:ctx.project,track:ctx.track,experiment:scope.experiment_id,run:arm.run_id,view:'results',result_b:arm.evaluation_id}),'s-link'));const details=node('div');details.append(raw('题目供给与训练消费',arm.counts||{}),raw('生成、验证、解题与训练成本',budget));const row=addRow(t.body,[arm.label,identity,finite(budget.verified_total)?known(budget.verified_total)+' '+known(budget.unit):(budget.complete===false?'成本不完整':'未记录'),metricValue({value:result.value,unit:'ratio',status:result.status}),details]);row.dataset.selfplayArm=arm.label||arm.run_id;}
   content.append(t.wrap);const effect=node('p','sp-effect');effect.dataset.selfplayEffect='';const delta=response.delta||{};
   effect.textContent=response.comparison_qualified===true&&response.status==='measured'&&finite(delta.value)?'固定评测差值：'+metricValue({...delta,unit:'pp'}):'条件尚未匹配，暂不显示受控收益。';content.append(effect);notes(content,response.qualification_reasons);notes(content,response.notes);if(delta.reason)notes(content,[delta.reason]);content.append(raw('完整对照条件与原始数据',response));
  }catch(error){if(alive()&&token===epoch)content.replaceChildren(empty(error.message),button('重试对照',selectComparison,'s-btn'));}
 }
 async function load(){if(!box.open||loaded||!alive())return;loaded=true;patch(ctx,{selfplay_comparison_open:'1'});body.replaceChildren(empty('读取已保存的供给对照…'));
  try{if(!choices.length){const results=await Promise.all([ctx.api('methods/catalog',{experiment_id:scope.experiment_id,run_id:scope.run_id,kind:'selfplay_comparison',limit:100}),...(scope.run_id?[ctx.api('methods/catalog',{experiment_id:scope.experiment_id,unbound:'1',kind:'selfplay_comparison',limit:100})]:[])]);if(!alive())return;const seen=new Set();choices=results.flatMap(result=>array(result.objects)).filter(item=>{try{assertComparison(item,scope);}catch{return false;}if(seen.has(item.id))return false;seen.add(item.id);return true;});}
   body.replaceChildren();if(!choices.length){body.append(empty('尚未登记同预算供给对照。'),node('p','sp-note','比较相同初始 Solver，在包含出题、验证、解题和训练的总预算下，固定旧题池与持续新增有效题的外部评测表现。'));return;}
   if(comparison&&!choices.some(item=>item.id===comparison)){body.append(node('p','sp-note','指定对照不在当前实验中。请重新选择。'));comparison='';}
   body.append(field('已保存的供给对照',select([['','选择一个已保存的对照'],...choices.map(item=>[item.id,item.title||item.id])],comparison,id=>{comparison=id;selectComparison();},'Self-play 同预算供给对照')));await selectComparison();
  }catch(error){if(alive())body.replaceChildren(empty(error.message));}
 }
 box.addEventListener('toggle',load);if(comparison||ctx.q.get('selfplay_comparison_open')==='1'){box.open=true;await load();}return()=>{dead=true;++epoch;};
}

export async function mountSelfplayLegacy(host,ctx,scope){
 const body=node('div'),box=fold('旧版轮次与完整过程',body,!!(ctx.q.get('selfplay_legacy')||ctx.q.get('selfplay_object')||ctx.q.get('object')),'sp-legacy');host.append(box);let started=false,dead=false,cleanup;
 async function load(){if(!box.open||started||dead)return;started=true;body.replaceChildren(empty('读取已保存的旧版轮次…'));try{const {mountSelfPlayHistory}=await import('./experiment-methods.js');const child={...ctx,alive:()=>ctx.alive()&&!dead};cleanup=await mountSelfPlayHistory(body,child,scope,{direct:true});if(dead)cleanup?.();}catch(error){if(ctx.alive()&&!dead)body.replaceChildren(empty(error.message));}}
 box.addEventListener('toggle',load);if(box.open)await load();return()=>{dead=true;cleanup?.();};
}

/** Default view has three summaries; large records are fetched only when opened. */
export async function mountSelfplayTraining(host,ctx,scope){
 let dead=false,epoch=0,cycle=ctx.q.get('selfplay_cycle')||'',reference=ctx.q.get('selfplay_reference')||'',evaluation=ctx.q.get('selfplay_evaluation')||'',offset=Math.max(0,Number(ctx.q.get('selfplay_offset'))||0),questionsOpen=ctx.q.get('selfplay_questions_open')==='1',cleanupComparison=null,cleanupLegacy=null;
 const alive=()=>ctx.alive()&&!dead,box=node('section','sp-section sp-training');box.dataset.selfplayView='training';host.append(box);
 async function load(){const token=++epoch;cleanupComparison?.();cleanupLegacy?.();cleanupComparison=null;cleanupLegacy=null;box.replaceChildren(section('Self-play：题目供给与实际学习'),empty('读取当前运行的轮次与固定参照…'));
  try{const response=await ctx.api('methods/selfplay-training',{experiment_id:scope.experiment_id,run_id:scope.run_id,cycle_id:cycle,reference_id:reference,evaluation_id:evaluation,offset,limit:25});if(!alive()||token!==epoch)return;assertScope(response,scope);cycle=response.selected_cycle_id||'';reference=response.selected_reference_id||'';
   patch(ctx,{selfplay_cycle:cycle,selfplay_reference:reference,selfplay_evaluation:evaluation,selfplay_offset:offset||''});box.replaceChildren(section('Self-play：题目供给与实际学习'));
   const rounds=array(response.rounds),selectors=controls();selectors.classList.add('sp-selectors');
   if(rounds.length)selectors.append(field('当前轮次',select(rounds.map(item=>[item.id,item.title||('轮次 '+known(item.round_index))]),cycle,id=>{cycle=id;offset=0;patch(ctx,{selfplay_cycle:id,selfplay_question:'',selfplay_offset:''});load();},'Self-play 当前轮次')));
   if(selectors.childElementCount)box.append(selectors);
   if(!rounds.length)box.append(node('p','sp-note','当前运行尚未接入完整轮次。已有独立评测仍可在 Agent 执行评测中查看。'));
   const metricHost=node('div','sp-counts');for(const id of ['new_verified_consumed','signal_coverage','external_em']){const metric=array(response.metrics).find(item=>item.id===id)||{id,label:{new_verified_consumed:'本轮新增有效消费题',signal_coverage:'训练信号覆盖率',external_em:'固定外部 Solver EM'}[id],value:null,status:'not_recorded'};metricHost.append(metricCard(metric));}box.append(metricHost);
   const counts=response.counts||{},supply=node('div','sp-supply');supply.dataset.selfplaySupply='';
   for(const[key,label]of[['generated_questions','生成'],['validation_passed','验证通过'],['unique_questions','去重独立'],['pool_admitted_questions','入池'],['sampled_questions','实际采样'],['completed_questions','完成更新']])supply.append(pair(label,counts[key]));box.append(supply,node('p','sp-note sp-supply-note','去重独立按全部生成候选计算，包含验证未通过与异常的题目。'));
   const extra=node('div','sp-supply-extra');for(const[key,label]of[['validation_failed','验证未通过'],['validation_error','验证异常'],['validation_unknown','验证未知'],['duplicate_questions','重复题'],['old_first_consumed_questions','旧题首次消费'],['reused_questions','旧题复用']])extra.append(node('span','',label+' '+known(counts[key])));box.append(extra);
   const referenceHost=node('div','sp-reference'),referenceChoices=array(response.references),evaluationChoices=array(response.external?.options);
   const referenceSelect=select([['','选择固定参考 Solver'],...referenceChoices.map(item=>[item.id,item.title||item.solver_revision||item.id])],reference,id=>{reference=id;offset=0;patch(ctx,{selfplay_reference:id,selfplay_question:'',selfplay_offset:''});load();},'Self-play 固定参考 Solver');referenceSelect.disabled=!referenceChoices.length;
   const evaluationSelect=select([['','选择固定外部评测'],...evaluationChoices.map(item=>[item.evaluation_id||item.id,item.label||item.id])],evaluation||response.external?.evaluation_id||'',id=>{evaluation=id;patch(ctx,{selfplay_evaluation:id});load();},'Self-play 固定外部评测');evaluationSelect.disabled=!evaluationChoices.length;
   referenceHost.append(field('看题目：固定参考 Solver',referenceSelect),field('看 Solver：固定外部评测',evaluationSelect));box.append(referenceHost);
   if(reference){const current=referenceChoices.find(item=>item.id===reference);if(current)box.append(node('p','sp-note','参考版本：'+known(current.solver_revision)+' · 每题尝试 '+known(current.attempts_per_question)+' 次。'));}
   const questionBody=node('div'),page=response.questions||{items:[],total:0},questionFold=fold('本轮生成题与完整去向 · '+known(page.total),questionBody,false,'sp-questions');questionFold.dataset.selfplayQuestions='';box.append(questionFold);let questionsDrawn=false;
   const drawQuestions=()=>{if(!questionFold.open||questionsDrawn||!alive()||token!==epoch)return;questionsDrawn=true;questionRows(questionBody,page,ctx,scope,()=>alive()&&token===epoch,ctx.q.get('selfplay_question'),cycle);
    if(page.has_more||offset){const prev=button('上一页题目',()=>{offset=Math.max(0,offset-25);patch(ctx,{selfplay_question:'',selfplay_offset:offset||''});load();},'s-btn small'),next=button('下一页题目',()=>{offset+=25;patch(ctx,{selfplay_question:'',selfplay_offset:offset});load();},'s-btn small');prev.disabled=offset===0;next.disabled=!page.has_more;questionBody.append(controls(prev,node('span','sp-note',`${offset+1}–${offset+array(page.items).length} / ${known(page.total)}`),next));}
   };questionFold.addEventListener('toggle',()=>{if(alive()&&token===epoch){questionsOpen=questionFold.open;patch(ctx,{selfplay_questions_open:questionsOpen?'1':''});drawQuestions();}});if(questionsOpen||ctx.q.get('selfplay_question')||offset){questionFold.open=true;drawQuestions();}
   const signalBody=node('div');renderSignals(signalBody,response.task_signal,response.opd_signal);box.append(fold('实际 advantage、mask 与训练信号',signalBody));
   const difficulty=node('div');if(reference)difficultyBody(difficulty,response);else difficulty.append(empty(referenceChoices.length?'先选择固定参考 Solver，查看多次尝试的成功次数分布。':'尚未登记固定参考 Solver 与尝试协议。'));box.append(fold('固定参考 Solver：各轮题目难度',difficulty));
   const external=node('div');externalFacts(external,response.external,[],ctx,scope);box.append(fold('固定外部评测：Solver 能力',external));
   const disposeComparison=await mountSelfplayComparisons(box,ctx,scope,{items:array(response.comparisons?.items)});if(!alive()||token!==epoch){disposeComparison?.();return;}cleanupComparison=disposeComparison;
   const disposeLegacy=await mountSelfplayLegacy(box,ctx,scope);if(!alive()||token!==epoch){disposeLegacy?.();return;}cleanupLegacy=disposeLegacy;
   const protocol=node('div');notes(protocol,response.notes);protocol.append(raw('计数与完整口径',response));protocol.append(button('查看 Self-play 接入说明',()=>openSelfplayIntegration(scope),'s-link'));box.append(fold('记录覆盖与计算口径',protocol));
  }catch(error){if(alive()&&token===epoch)box.replaceChildren(section('Self-play：题目供给与实际学习'),empty(error.message),button('重试 Self-play 记录',load,'s-btn'));}
 }
 await load();return()=>{dead=true;++epoch;cleanupComparison?.();cleanupLegacy?.();};
}
