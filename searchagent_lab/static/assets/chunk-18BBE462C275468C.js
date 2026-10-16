/** Method-specific summaries over saved evidence. Values are computed by the scoped API. */
import {node,button,badge,controls,empty,card,fmt,link,href,jsonModal,select,field,table,addRow} from './chunk-F51FD76F14331921.js';
import {JsonTree} from './chunk-38017A726E9F9CF5.js';

const titles={skill:'Skill 效果与实际使用',self_play:'Self-play 供给与学习',opd:'OPD 监督与修复'};
const listTitles={skill:'实际检索与注入记录',self_play:'题目供给与训练去向',opd:'失败状态与监督记录'};
const missingTexts={skill:'本范围尚未接入逐次检索与注入记录。技能库正文仍可在下方查看。',self_play:'本范围尚未接入生成题和训练消费记录。',opd:'本范围尚未接入失败状态、teacher 记录与监督去向。已登记方法配置，接入记录后会在这里展开。'};
const kindNames={opd_failure:'Student 失败状态',opd_teacher:'Teacher 记录',opd_supervision:'训练监督',training_use:'训练消费',training_update:'训练更新',skill_retrieval:'技能检索与注入',skill_evolution:'技能版本变更',generated_question:'生成题',question_review:'题目验证',solver_attempt:'Solver 尝试',proposal:'出题过程',skill_bank:'技能库快照',evaluation_binding:'执行时方法状态',trace:'执行记录'};
const statusNames={measured:'已记录',partial:'部分记录',not_recorded:'未记录',not_applicable:'未测',selection_required:'待选择对照'};
const verificationNames={passed:'验证通过',pass:'验证通过',success:'验证通过',failed:'验证未通过',fail:'验证未通过',error:'验证异常',not_run:'未验证',unknown:'未记录',accepted:'验证接受',rejected:'验证拒绝',needs_review:'待验证',unreviewed:'未验证',conflicting:'验证存在分歧'};
const processNames={completed:'已完成',failed:'失败',error:'异常',not_run:'未运行',accepted:'已接受',rejected:'已拒绝',sampled:'已采样',selected:'已选中',excluded:'已排除',skipped:'已跳过',unknown:'未记录'};
const alignmentNames={restored:'已恢复同一状态',replayed:'已重放至同一状态',text_only:'仅对齐文本',not_recorded:'未记录'};
const known=value=>value==null||value===''?'未记录':typeof value==='string'?value:JSON.stringify(value);
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const yesNo=(value,yes='是',no='否',missing='未记录')=>value===true?yes:value===false?no:missing;
const count=value=>finite(value)?String(value):'未记录';
const sameScope=(object,scope)=>['project_id','experiment_id','run_id'].every(key=>(object.scope?.[key]||'')===(scope[key]||''));
function fold(title,body,open=false){const box=node('details','ma-fold');box.open=open;box.append(node('summary','',title),body);return box;}
function pair(label,value){const row=node('div','ma-pair');row.append(node('span','s-muted',label),node('div','',known(value)));return row;}
function raw(title,value){const box=node('div');new JsonTree(box,value,{label:title,open:false});return fold(title,box);}
function metricValue(metric){
 if(!finite(metric.value))return metric.status==='selection_required'?(metric.id==='teacher_repair'?'按 K 分组':'待选择评测'):statusNames[metric.status]||'未记录';
 if(metric.unit==='ratio')return fmt(metric.value*100,1)+'%';
 if(metric.unit==='tokens/task')return fmt(metric.value,1);
 return fmt(metric.value,3)+(metric.unit&& !['count','tokens','pp'].includes(metric.unit)?' '+metric.unit:metric.unit==='pp'?' pp':'');
}
function metricCard(metric){
 const box=card('ma-metric');box.dataset.metric=metric.id||'';box.dataset.status=metric.status||'not_recorded';
 box.append(node('span','ma-metric-label',metric.label),node('strong','ma-metric-value',metricValue(metric)));
 const coverage=[];
 if(finite(metric.numerator)&&finite(metric.denominator))coverage.push(metric.numerator+' / '+metric.denominator);
 else if(finite(metric.denominator))coverage.push(metric.denominator+(metric.id==='external_em'||metric.unit==='tokens/task'?' 题':' 条可判定记录'));
 if(finite(metric.unknown)&&metric.unknown>0)coverage.push(metric.unknown+' 条未知');
 if(finite(metric.attempt_budget))coverage.push('K = '+metric.attempt_budget);
 if(finite(metric.attempted)&&finite(metric.eligible))coverage.push('已尝试 '+metric.attempted+' / '+metric.eligible+' 个状态');
 if(metric.unit==='tokens/task'&&finite(metric.value))coverage.unshift('tokens / task');
 if(coverage.length)box.append(node('small','ma-metric-coverage',coverage.join(' · ')));
 const note=metric.missing_reason||metric.note;
 if(note)box.append(node('small','s-muted',note));
 return box;
}

function rowFacts(family,item){
 const a=item.assessment||{};
 if(family==='skill'&&item.kind!=='skill_retrieval')return [['类型',kindNames[item.kind]||item.kind],['已存版本',a.version??a.bank_id??item.id],['内容 SHA256',item.content_hash?.slice(0,12)]];
 if(family==='skill')return [
  ['SkillBank',a.bank_id],['入选 / 注入记录',count(a.selected_count)+' / '+count(a.recorded_injections)],
  ['最终输入',a.input_status==='verified'?'片段已核验':a.input_status==='absent'?'记录为未注入':'未核验'],
 ];
 if(family==='self_play')return [
  ['验证',a.verified===true?'已验证':a.verified===false?'验证未通过':a.review_status==='accepted'?'已接受 · 验证未确认':verificationNames[a.review_status]||known(a.review_status)],
  ['实际消费',yesNo(a.consumed,'已采样','记录为未采样')],
  ['学习信号',yesNo(a.signal_active,'有有效项','记录为无有效项')],
 ];
 return [
  ['Teacher',count(a.continuation_attempts)+' 次接续 · '+count(a.rescore_count)+' 次重打分'],
  ['接续结果',yesNo(a.repair_success,'经验证成功','接续未成功','未测 / 未确认')],
  ['有效监督 / 更新',count(a.active_supervision_count)+' 条 · '+yesNo(a.completed_update,'已完成','未完成')],
 ];
}

/** Renders only this experiment and this run (or explicit unbound experiment artifacts). */
export async function mountMethodAssessment(host,ctx,family,scope){
 let dead=false,epoch=0,controller=null;
 const stateKey=family+'_evidence',offsetKey=family+'_evidence_offset';
 let selected=ctx.q.get(stateKey)||(family==='opd'?ctx.q.get('object'):'')||'';
 let evaluation=ctx.q.get(family+'_evaluation')||'';
 const requestedOffset=Number(ctx.q.get(offsetKey));let offset=Number.isSafeInteger(requestedOffset)&&requestedOffset>=0?requestedOffset:0;
 const alive=()=>!dead&&ctx.alive();
 const shell=node('section','ma-assessment');shell.dataset.family=family;host.append(shell);
 const selector=node('div','ma-evaluation-select'),metrics=node('div','ma-metrics'),supply=node('div'),evidence=node('div','ma-evidence'),protocol=node('div');
 const heading=node('div','ma-heading');heading.append(node('h2','',titles[family]),node('span','s-muted',scope.run_id?'当前运行的记录':'实验级产物 · 未绑定 run'));
 shell.append(heading,selector,metrics,supply,evidence,protocol);
 async function load(){
  controller?.abort();controller=new AbortController();const token=++epoch;
  metrics.replaceChildren(empty('读取评估摘要…'));supply.replaceChildren();evidence.replaceChildren();protocol.replaceChildren();
  try{
   const data=await ctx.api('methods/assessment',{experiment_id:scope.experiment_id,run_id:scope.run_id||'',family,offset,limit:25,...(evaluation?{evaluation}:{})},{signal:controller.signal});
   if(!alive()||token!==epoch)return;
   if(data.family!==family||!sameScope({scope:data.scope},scope))throw Error('评估摘要的实验 / 运行归属不一致。');
   selector.replaceChildren();
   const options=data.evaluation_options||data.evaluation?.candidates||[];
   if(options.length>1){
    const choices=[['','选择已明确关联的独立评测'],...options.map(option=>[option.evaluation_id,option.label||option.evaluation_id])];
    selector.append(field('固定评测记录',select(choices,evaluation,id=>{evaluation=id;ctx.setViewState({[family+'_evaluation']:id});load();},'方法对应的固定评测记录')));
   }else if(data.evaluation?.evaluation_id)selector.append(node('span','s-muted','固定评测：'+data.evaluation.evaluation_id));
   metrics.replaceChildren(...(data.metrics||[]).map(metricCard));
   if(!metrics.childElementCount)metrics.append(empty('评估口径尚未接入。'));
   if(family==='self_play'&&finite(data.counts?.generated_questions)&&data.counts.generated_questions>0){
    const strip=node('div','ma-supply-strip');
    for(const [key,label] of [['generated_questions','已记录生成题'],['unique_questions','明确去重独立题'],['verified_questions','已验证题'],['sampled_questions','已采样题'],['consumed_questions','已关联完成更新']]){const stage=node('div');stage.append(node('small','s-muted',label),node('strong','',count(data.counts[key])));strip.append(stage);}
    supply.append(strip);
    const unknown=[];for(const [key,label] of [['reused_questions','复用题'],['unknown_identity_questions','独立身份未知'],['review_errors','验证异常']])if(finite(data.counts[key])&&data.counts[key]>0)unknown.push(label+' '+data.counts[key]);
    if(unknown.length)supply.append(node('p','ma-supply-note s-muted',unknown.join(' · ')));
   }
   renderEvidence(data.evidence||{items:[],total:0,has_more:false});
   const notes=node('div','ma-protocol');
   for(const note of data.protocol_notes||[])notes.append(node('p','s-muted',typeof note==='string'?note:known(note)));
   if(data.counts&&Object.keys(data.counts).length)notes.append(raw('记录覆盖与分母',data.counts));
   if(data.cycles?.length)notes.append(raw('各轮次的供给与消费计数',data.cycles));
   if(data.budget_groups?.length){const groups=table(['尝试上限 K','纳入状态','已尝试','已验证成功','已验证失败','未知']);for(const group of data.budget_groups)addRow(groups.body,[known(group.attempt_budget),count(group.eligible),count(group.attempted),count(group.successes),count(group.failures),count(group.unknown)]);notes.append(fold('按尝试预算查看 Teacher 结果',groups.wrap));}
   notes.append(button('完整评估记录',()=>jsonModal(titles[family]+' · 当前范围',data),'s-link'));
   protocol.append(fold('计算口径与记录覆盖',notes));
  }catch(error){if(error.name!=='AbortError'&&alive()&&token===epoch){metrics.replaceChildren(empty('评估摘要暂不可用：'+error.message),button('重试摘要',load,'s-btn'));if(evaluation)metrics.append(button('重新选择评测',()=>{evaluation='';ctx.setViewState({[family+'_evaluation']:''});load();},'s-btn'));}}
 }
 function renderEvidence(page){
  const list=node('div','ma-evidence-list'),items=page.items||[];
  const name=family==='skill'&&items.length&&items.every(item=>item.kind!=='skill_retrieval')?'技能版本与演化记录':listTitles[family];
  const title=name+' · '+count(page.total)+' 条';
  if(family==='opd')evidence.append(node('h3','ma-list-title',title),list);
  else evidence.append(fold(title,list,!!selected));
  if(!items.length){list.append(empty(missingTexts[family]));return;}
  if(selected&&!items.some(item=>item.id===selected))list.append(node('p','s-muted','所选记录不在当前页中；未自动打开其他记录。可翻页或重新选择。'));
  const rows=[];
  for(const item of items){
   const details=node('details','ma-evidence-row');details.dataset.object=item.id;
   const summary=node('summary','ma-row-summary'),identity=node('div','ma-row-identity');
   identity.append(node('strong','',item.title||item.id),node('small','s-muted',item.id));
   if(item.demo===true)identity.append(badge('合成示例','purple'));
   summary.append(identity);
   for(const [label,value] of rowFacts(family,item)){const fact=node('div','ma-row-fact');fact.append(node('small','s-muted',label),node('span','',known(value)));summary.append(fact);}
   const content=node('div','ma-detail');details.append(summary,content);list.append(details);rows.push(details);
   let loaded=false,pending=false;
   const expand=async()=>{
    if(!details.open||pending||loaded||!alive())return;
    pending=true;selected=item.id;ctx.setViewState({[stateKey]:selected,[offsetKey]:offset,object:''});
    for(const other of rows)if(other!==details)other.open=false;
    const local=epoch;content.replaceChildren(empty('读取原始内容与关联证据…'));
    try{
     const result=await ctx.api('methods/object',{id:item.id});
     if(!alive()||!content.isConnected||local!==epoch)return;
     if(!sameScope(result.object,scope))throw Error('记录不属于当前实验 / 运行。');
     content.replaceChildren();renderObject(content,result.object);
     renderCaseEvidence(content,item);
     const related=(result.related||[]).filter(object=>sameScope(object,scope));
     if(related.length){
      const group=node('div','ma-related');
      for(const object of related){
       const body=node('div','ma-related-body'),box=fold((kindNames[object.kind]||object.kind)+' · '+(object.title||object.id),body);
       let drawn=false;box.addEventListener('toggle',()=>{if(box.open&&!drawn&&alive()){drawn=true;renderObject(body,object);}});group.append(box);
      }
      content.append(node('h4','',family==='opd'?'Teacher、监督与训练去向':'关联过程与训练去向'),group);
     }
     const provenance=node('div');provenance.append(pair('记录 ID',result.object.id),pair('内容 SHA256',result.object.content_hash),pair('来源对象',result.object.derived_from?.length?result.object.derived_from:'未记录'));
     for(const ref of result.object.evidence_refs||[])appendReference(provenance,ref,'来源证据');
     provenance.append(raw('原始对象与已登记来源关系',result));content.append(fold('来源与完整原始字段',provenance));loaded=true;
    }catch(error){if(alive()&&content.isConnected&&local===epoch)content.replaceChildren(empty(error.message),button('重试记录',expand,'s-btn'));}
    finally{pending=false;}
   };
   details.addEventListener('toggle',()=>{if(details.open){for(const other of rows)if(other!==details)other.open=false;expand();}});
   if(item.id===selected)details.open=true;
  }
  if(finite(page.total)&&page.total>25){
   const prev=button('上一页',()=>{offset=Math.max(0,offset-25);selected='';ctx.setViewState({[stateKey]:'',[offsetKey]:offset});load();},'s-btn');prev.disabled=offset===0;
   const next=button('下一页',()=>{offset+=25;selected='';ctx.setViewState({[stateKey]:'',[offsetKey]:offset});load();},'s-btn');next.disabled=!page.has_more;
   list.append(controls(prev,node('span','s-muted',(offset+1)+'–'+(offset+items.length)+' / '+page.total),next));
  }
 }
 function renderCaseEvidence(parent,item){
  const a=item.assessment||{},rows=family==='skill'?[
   ['实际输入核验',a.input_status==='verified'?'渲染片段已在最终输入中核验':a.input_status==='absent'?'记录显示未注入':'未核验'],
   ['注入日志完整性',yesNo(a.log_complete,'声明完整','声明不完整')],['已核验片段数',a.verified_injections],
  ]:family==='self_play'?[
   ['稳定题目 ID',a.question_uid],['首次生成轮次',a.first_generated_cycle_id],['去重记录',a.deduplication],
   ['实际采样',yesNo(a.consumed,'已记录','记录为未采样')],['更新完成',yesNo(a.update_completed,'已完成','未完成')],
   ['有效学习项',yesNo(a.signal_active,'有','记录为无')],
  ]:[
   ['纳入接续评估',yesNo(a.eligible,'纳入','排除')],['每个状态的尝试上限 K',a.attempt_budget],
   ['验证通过 / 未通过 / 异常',count(a.verification_passes)+' / '+count(a.verification_failures)+' / '+count(a.verification_errors)],
   ['监督总数 / 有效项',count(a.supervision_count)+' / '+count(a.active_supervision_count)],['训练更新',yesNo(a.completed_update,'已完成','未完成')],
  ];
  const body=node('div','ma-facts');for(const [label,value] of rows)body.append(pair(label,value));parent.append(fold('此条记录的评估依据',body));
 }
 function appendReference(parent,ref,label){
  if(!ref||typeof ref!=='object')return;
  const params={...(ref.object_id?{method:ref.object_id}:ref.trajectory?{trajectory:ref.trajectory}:ref.file?{file:ref.file,record:ref.record}:{}),pointer:ref.pointer,return_experiment:scope.experiment_id,return_run:scope.run_id,return_view:family,return_object:selected};
  if(!params.method&&!params.trajectory&&!params.file)return;
  parent.append(link(label+' ↗',href('replay',params),'s-link'));
 }
 function renderObject(parent,object){
  const data=object.data||{},facts=node('div','ma-facts');
  if(object.demo===true)parent.append(badge('合成示例 · 非真实实验','purple'));
  if(object.kind==='opd_failure'){
   const state=data.state||{},stateNames={captured:'已保存环境状态',replayed:'已重放环境状态',text_only:'仅保存文本',not_recorded:'未记录环境状态'};
   for(const [label,value] of [['题目 / 样本',data.sample_id],['Student checkpoint',data.student_checkpoint_id],['失败位置',data.failure_step??data.step],['状态保存',stateNames[state.status]||state.status],['状态标识',state.hash??state.state_hash??state.prefix_hash??data.state_hash??data.prefix_hash],['剩余预算',data.remaining_budget??state.remaining_budget]])facts.append(pair(label,value));
   parent.append(facts);appendContent(parent,'失败时可见输入',data.student_input??data.observation??data.prefix??state.prefix);
   appendReference(parent,data.trace_ref,'查看 Student 原始执行');appendReference(parent,data.state_ref??state.evidence_ref,'查看失败状态');
   if(Object.keys(state).length)parent.append(raw('已保存的环境状态',state));
  }else if(object.kind==='opd_teacher'){
   const mode=data.mode==='continuation'?'实际接续':data.mode==='rescore'?'只做重打分':'模式未记录';
   parent.append(badge(mode));
   for(const [label,value] of [['Teacher / 版本',data.teacher_id??data.teacher??data.teacher_checkpoint_id??data.teacher_model],['运行状态',processNames[data.status]||data.status],['接续尝试编号',data.attempt_index],['状态对齐',alignmentNames[data.state_alignment]??data.state_alignment??data.prefix_hash],['验证结果',verificationNames[data.verification?.status]||data.verification?.status],['验证器 / 版本',data.verification?.verifier],['验证方式',data.verification?.method],['尝试预算',data.budget]])facts.append(pair(label,value));
   parent.append(facts);appendContent(parent,'Teacher 实际输入',data.teacher_input??data.input);appendContent(parent,'Teacher 输出 / 接续',data.teacher_output??data.output??data.response??data.raw_output);
   appendContent(parent,'额外可见信息 / Context',data.context??data.extra_information??data.privileged_context);
   if(data.mode==='rescore')parent.append(node('p','s-muted','此记录只重打分；不计为已经运行的接续或任务修复。'));
   appendReference(parent,data.teacher_trace_ref??data.trace_ref,'查看 Teacher 完整记录');appendReference(parent,data.input_ref,'查看 Teacher 输入证据');appendReference(parent,data.verification?.evidence_ref,'查看结果验证证据');
  }else if(object.kind==='opd_supervision'){
   for(const [label,value] of [['监督形式',data.objective??data.loss_type??data.mode],['监督状态',processNames[data.status]||data.status],['有效 mask / token',data.loss_mask_tokens??data.active_tokens??data.loss_mask??data.mask],['权重 / 系数',data.loss_weight??data.coefficient],['日志声明的学习项',yesNo(data.loss_active,'已记录有效项','记录为无有效项')],['原始 advantage',data.original_advantage],['调整后 advantage',data.effective_advantage??data.fused_advantage],['训练批次',data.batch_id]])facts.append(pair(label,value));
   parent.append(facts);appendContent(parent,'监督内容',data.target??data.supervision);appendReference(parent,data.loss_ref,'查看实际学习项');
   if(data.objective==='advantage_reweight'&&data.original_advantage===0&&data.effective_advantage===0)parent.append(node('p','s-muted','此处原始与调整后 advantage 都为 0。该任务优势项未被激活；其他损失项需分别查看。'));
  }else if(object.kind==='skill_retrieval'){
   for(const [label,value] of [['SkillBank 版本',data.bank_id],['轨迹 ID',data.trajectory_id],['检索候选',data.candidates],['最终入选',data.selected],['注入日志完整',yesNo(data.injections_complete)],['轨迹调用记录完整',yesNo(data.trajectory_complete)]])facts.append(pair(label,value));parent.append(facts);
   for(const injection of data.injections||[]){const box=node('div','ma-content');box.append(pair('注入技能',injection.member));appendContent(box,'最终渲染文本',injection.rendered_text);appendReference(box,injection.evidence_ref,'查看实际模型输入');parent.append(box);}
   if(data.final_input!=null){const input=node('div');appendContent(input,'最终模型输入',data.final_input);parent.append(fold('核对最终模型输入',input));}
   appendReference(parent,data.trace_ref,'查看关联执行');
  }else if(object.kind==='generated_question'){
   appendContent(parent,'生成题',data.question);parent.append(pair('提出的答案',data.proposed_answer));appendContent(parent,'支持材料',data.supporting_evidence??data.evidence??data.context);
  }else if(object.kind==='training_use'||object.kind==='training_update'){
   for(const [label,value] of [['阶段 / 状态',processNames[data.status]||data.status],['批次 / step',data.batch_id??data.step],['使用的产物',data.artifact_ids??data.consumption_ids],['输出 checkpoint',data.output_checkpoint_id],['实际更新证据',data.parameter_update??data.update_evidence]])facts.append(pair(label,value));parent.append(facts);
  }
  if(['opd_supervision','training_use'].includes(object.kind))appendTrainingLinks(parent,object);
  parent.append(raw('完整原始记录 · '+object.id,data));
 }
 function appendTrainingLinks(parent,object){
  const title=object.kind==='training_use'?'关联更新记录':'实际训练消费记录',body=node('div'),box=fold(title,body);parent.append(box);let loaded=false,pending=false;
  const loadLinks=async()=>{
   if(!box.open||loaded||pending||!alive())return;pending=true;body.replaceChildren(empty('读取已关联的训练去向…'));
   try{
    const result=await ctx.api('methods/object',{id:object.id});if(!alive()||!body.isConnected)return;
    if(!sameScope(result.object,scope))throw Error('记录不属于当前实验 / 运行。');
    const target=object.kind==='training_use'?'training_update':'training_use',items=(result.related||[]).filter(item=>item.kind===target&&sameScope(item,scope));
    body.replaceChildren();if(!items.length)body.append(empty('尚无明确关联的'+title+'；不从 loss 或轮次号推断。'));
    for(const item of items){const record=node('div','ma-related-body');record.append(node('h4','',item.title||item.id));renderObject(record,item);body.append(record);}loaded=true;
   }catch(error){if(alive()&&body.isConnected)body.replaceChildren(empty(error.message),button('重试训练去向',loadLinks,'s-btn'));}finally{pending=false;}
  };box.addEventListener('toggle',loadLinks);
 }
 function appendContent(parent,label,value){if(value==null)return;const box=node('div','ma-content');box.append(node('h4','',label),node('pre','ma-text',typeof value==='string'?value:JSON.stringify(value,null,2)));parent.append(box);}
 await load();return()=>{dead=true;++epoch;controller?.abort();};
}
