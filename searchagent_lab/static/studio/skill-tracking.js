/** Frozen skill assets, observed runtime use, and recorded training decisions. */
import {node,button,link,href,badge,select,field,section,empty,controls,table,addRow} from './common.js';

const known=value=>value==null||value===''?'未记录':typeof value==='object'?JSON.stringify(value):String(value);
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const array=value=>Array.isArray(value)?value:[];
const labels={model:'模型主动选择',policy:'模型主动选择',retriever:'检索器注入',retrieval:'检索器注入',rule:'规则触发',user:'用户指定',generated:'模型当场生成',inline:'模型当场生成',unknown:'未记录',accepted:'接受',accept:'接受',rejected:'拒绝',reject:'拒绝',retained:'保留',retain:'保留',retired:'退役',retire:'退役',observe:'继续观察',observing:'继续观察',pending:'待定',created:'创建',create:'创建',modified:'修改',modify:'修改',unchanged:'保持',added:'新增',removed:'移除',proposed:'提出候选',proposal:'提出候选',review:'评估候选',audit:'检查与处置',fixed_skills_rl:'固定技能库 + RL',fixed_policy_skill_iteration:'固定策略 + 技能迭代',joint_evolution:'策略与技能共同演化',inline_skill_credit:'技能实例信用'};
const display=value=>labels[value]||known(value);
const scopeMatches=(record,scope)=>!!record?.scope&&['experiment_id','run_id'].every(key=>String(record.scope[key]||'')===String(scope[key]||''));
function assertScope(record,scope){if(!scopeMatches(record,scope))throw Error('技能记录与当前实验 / 运行归属不一致。');}
function patch(ctx,value){for(const[key,item]of Object.entries(value)){if(item==null||item==='')ctx.q.delete(key);else ctx.q.set(key,String(item));}ctx.setViewState?.(value);}
function textBlock(title,value){const box=node('div','st-content');box.append(node('h4','',title),node('pre','st-text',typeof value==='string'?value:JSON.stringify(value??'未记录',null,2)));return box;}
function pair(label,value){const box=node('div','st-pair');box.append(node('small','s-muted',label),node('div','',known(value)));return box;}
function fold(title,body,open=false){const detail=node('details','st-fold');detail.append(node('summary','',title),body);detail.open=open;return detail;}
function raw(title,value){return fold(title,textBlock('已保存的原始字段',value));}
function number(value){return finite(value)?String(value):'未记录';}
function evidenceState(value){if(value?.status==='observed'&&value.value===true)return['已观测','good'];if(value?.status==='not_observed'&&value.value===false)return['记录为无','muted'];return['未确认','muted'];}
function observedStrip(item){const strip=node('div','st-observations');for(const [key,label]of [['visible','可见'],['loaded','已加载 / 注入'],['execution','执行证据'],['gain','增益对照']]){const value=item.observations?.[key],state=evidenceState(value),cell=node('div');cell.dataset.observation=key;cell.append(node('small','s-muted',label),badge(state[0],state[1]));if(value?.reason)cell.title=value.reason;strip.append(cell);}return strip;}
function appendTrace(parent,ref,ctx,scope,label='查看关联轨迹',instance=''){
 if(!ref||typeof ref!=='object')return;
 const source=ref.trajectory?{trajectory:ref.trajectory}:ref.trajectory_id?{trajectory:ref.trajectory_id}:ref.object_id?{method:ref.object_id}:ref.file?{file:ref.file,record:ref.record}:null;
 if(!source)return;
 parent.append(link(label+' →',href('replay',{...source,project:ctx.project,track:ctx.track,evaluation:ref.evaluation_id||ref.evaluation,step:ref.step,pointer:ref.pointer,skill_instance:instance,return_experiment:scope.experiment_id,return_run:scope.run_id,return_view:ctx.q.get('view')||'results',return_skill_instance:instance,return_skill_credit:ctx.q.get('skill_credit'),return_skill_credit_instance:ctx.q.get('skill_credit_instance'),return_skill_event:ctx.q.get('skill_event'),return_skill_comparison:ctx.q.get('skill_comparison'),return_skill_matrix:ctx.q.get('skill_matrix'),return_result_b:ctx.q.get('result_b'),return_cp:ctx.q.get('cp'),return_step:ctx.q.get('step')}),'s-link'));
}
function assetLabel(ref){if(ref?.type==='inline')return '当场生成 · 无外部技能库';return [ref?.skill_id,ref?.version&&'@'+ref.version,ref?.asset_id||ref?.bank_id,ref?.member].filter(Boolean).join(' · ')||'冻结版本未记录';}

async function renderInstance(parent,item,ctx,scope,valid){
 parent.replaceChildren(empty('读取当时保存的技能正文与证据…'));
 const response=await ctx.api('methods/skill-instance',{id:item.id});if(!valid())return;
 assertScope(response.object,scope);
 const object=response.object,data=object.data||{},ref=response.asset_ref||item.asset_ref||{},frozen=response.frozen_content||{};
 parent.replaceChildren();const facts=node('div','st-facts');
 for(const [label,value]of [['实例 / 记录 ID',object.id],['选择来源',display(item.selection_source??data.selection?.source??data.selection_source)],['使用版本',assetLabel(ref)],['内容 SHA256',frozen.content_hash||ref.content_hash],['加载位置',item.recorded_steps?.length?item.recorded_steps:item.steps?.length?item.steps:item.step??data.selection?.step],['评测 / 轨迹',item.evaluation_id||item.trajectory_id]])facts.append(pair(label,value));
 parent.append(facts,observedStrip({...item,observations:response.observations||item.observations}));
 if(ref.type==='inline'||frozen.kind==='inline')parent.append(node('p','st-note','这是模型当场生成的技能实例；外部技能库为空。生成文本、动作关联与训练信用分别保留。'));
 if(frozen.status==='recorded')parent.append(textBlock(item.kind==='skill_retrieval'?'当次检索引用的冻结内容':'该实例引用的冻结内容',frozen.content));
 else parent.append(empty(frozen.reason||'没有可核验的冻结正文；没有用最新版本替代。'));
 if(data.loading?.rendered_text!=null)parent.append(textBlock('已记录的加载 / 注入片段',data.loading.rendered_text));
 const actions=array(data.related_actions).length?data.related_actions:array(item.related_actions);
 if(actions.length){const content=node('div'),rows=table(['Step / 调用','关联动作','依据']);for(const action of actions){const basis=node('div','',action.relation??action.reason??'已记录关联');appendTrace(basis,action.evidence_ref,ctx,scope,'打开动作记录',item.id);addRow(rows.body,[action.step??action.step_id??action.tool_call_id??action.call_id,action.label??action.kind??action.action??action.pointer,basis]);}content.append(rows.wrap,node('p','st-note','动作关联表示日志中声明了联系；执行证据和局部检查单独判断。没有保存边界时，不补推技能结束位置。'));parent.append(fold('关联的动作与工具调用 · '+actions.length,content));}
 if(data.loading||data.visibility)parent.append(raw('最终模型输入与加载证据',{loading:data.loading,visibility:data.visibility}));
 const comparisons=array(item.comparison_ids||data.comparison_ids);for(const id of comparisons)parent.append(link('查看技能效果对照 →',href('experiment',{project:ctx.project,track:ctx.track,experiment:scope.experiment_id,run:scope.run_id,view:'training',skill_comparison:id,skill_matrix:'1'}),'s-link'));
 const checks=data.local_checks??item.local_checks;if(checks!=null)parent.append(raw('局部检查与可验证结果',checks));
 const usage=data.usage??item.usage;if(usage!=null)parent.append(raw('已记录的上下文、token、工具与耗时',usage));
 const result=data.task_result??item.task_result;if(result!=null)parent.append(raw('该次运行的任务结果',result));
 appendTrace(parent,item.trajectory_id?{trajectory:item.trajectory_id,evaluation_id:item.evaluation_id}:item.trace_ref||data.trace_ref,ctx,scope,'打开原始执行轨迹',item.id);
 const provenance=node('div');for(const obs of Object.values(response.observations||item.observations||{}))for(const ref of array(obs.evidence_refs))appendTrace(provenance,ref,ctx,scope,'打开观测证据',item.id);
 provenance.append(raw('完整实例与来源',response));parent.append(fold('观测依据与完整日志',provenance));
}

function renderRuntimeRows(parent,response,ctx,scope,valid,{selected,onSelect}={}){
 const items=array(response.items||response.instances).filter(item=>scopeMatches(item,scope));
 if(selected&&!items.some(item=>item.id===selected))parent.append(node('p','st-note','指定技能实例不在此范围 / 当前页中；未自动打开其他实例。'));
 if(!items.length){parent.append(empty('尚未记录这个评测 / Step 的技能使用实例。'));return;}
 for(const item of items){
  const detail=node('details','st-instance-row'),summary=node('summary','st-instance-summary'),name=node('div','st-identity');detail.dataset.skillInstance=item.id;
  name.append(node('strong','',item.title||item.asset_ref?.skill_id||item.id),node('small','s-muted',assetLabel(item.asset_ref)));
  if(item.demo===true)name.append(badge('合成示例','purple'));
  summary.append(name,pair('选择来源',display(item.selection_source)),pair('记录位置',item.recorded_steps?.length?item.recorded_steps:item.steps?.length?item.steps:item.step));detail.append(summary,observedStrip(item));
  const body=node('div','st-instance-body');detail.append(body);parent.append(detail);let loaded=false,pending=false;
  async function load(){if(!detail.open||loaded||pending||!valid())return;pending=true;onSelect?.(item.id);try{await renderInstance(body,item,ctx,scope,()=>valid()&&body.isConnected);if(valid())loaded=true;}catch(error){if(valid()&&body.isConnected)body.replaceChildren(empty(error.message),button('重试记录',load,'s-btn'));}finally{pending=false;}}
  detail.addEventListener('toggle',load);if(item.id===selected){detail.open=true;load();}
 }
}

/** Embedded in Agent Evaluation. Replay passes an exact trajectory and optional step. */
export async function mountSkillExecution(host,ctx,scope,options={}){
 let dead=false,epoch=0,offset=0,evaluation=options.evaluationId??ctx.q.get('result_b')??'',trajectory=options.trajectoryId||'',step=options.step,selected=ctx.q.get('skill_instance')||'';
 const alive=()=>ctx.alive()&&!dead,box=node('section','st-runtime st-section');box.dataset.skillView=step!==undefined?'step':options.replay?'episode':'execution';host.append(box);
 function draw(response){
  box.replaceChildren(section(step!==undefined?'此 Step 的 Skill 记录':options.replay?'本条轨迹的 Skill 使用':'Skill 运行记录'));
  box.append(node('p','st-note',evaluation?'绑定评测 '+evaluation+(trajectory?' · 当前轨迹':''):'仅展示明确关联到当前轨迹的实例。'));
  const counts=response.counts||{},strip=node('div','st-counts');
  const metrics=[['已存使用记录',counts.total??response.total,'记录数，不作为调用率分母'],['已确认加载 / 注入',counts.loaded?.known_denominator?counts.loaded.observed:null,`${number(counts.loaded?.known_denominator)} 条可判断 · ${number(counts.loaded?.unknown)} 条未知`],['有执行证据',counts.execution?.known_denominator?counts.execution.observed:null,`${number(counts.execution?.known_denominator)} 条可判断 · ${number(counts.execution?.unknown)} 条未知`]];
  if(step===undefined)for(const[label,value,note]of metrics){const block=node('div');block.append(node('small','s-muted',label),node('strong','',number(value)),node('small','s-muted',note));strip.append(block);}if(strip.childElementCount)box.append(strip);
  const rows=node('div','st-runtime-rows');renderRuntimeRows(rows,response,ctx,scope,alive,{selected,onSelect:id=>{selected=id;patch(ctx,{skill_instance:id});}});
  if(options.replay||step!==undefined)box.append(rows);else box.append(fold('使用实例与冻结正文 · '+number(response.total)+' 条',rows,!!selected));
  if(response.has_more||offset){const prev=button('上一页',()=>{offset=Math.max(0,offset-25);load();},'s-btn small'),next=button('下一页',()=>{offset+=25;load();},'s-btn small');prev.disabled=!offset;next.disabled=!response.has_more;box.append(controls(prev,node('span','st-note',`${offset+1}–${offset+array(response.items||response.instances).length} / ${number(response.total)}`),next));}
  if(response.availability?.reason)box.append(node('p','st-note',response.availability.reason));
 }
 async function load(){const token=++epoch;
  if(options.requireEvaluation&&!evaluation){box.hidden=false;box.replaceChildren(section('Skill 运行记录'),node('p','st-note','选择上方已有 Agent 评测后，查看该次运行实际加载的技能。训练使用记录保存在 RL 训练评测中。'));return;}
  try{const data=await ctx.api('methods/skill-runtime',{experiment_id:scope.experiment_id,run_id:scope.run_id,evaluation_id:evaluation,trajectory_id:trajectory,step,offset,limit:25});if(!alive()||token!==epoch)return;assertScope(data,scope);
   if(data.availability?.status==='not_applicable'||options.hideEmpty&&!(data.total>0)){box.hidden=true;box.replaceChildren();return;}
   box.hidden=false;draw(data);
  }catch(error){if(alive()&&token===epoch)box.replaceChildren(section('Skill 运行记录'),empty(error.message),button('重试技能记录',load,'s-btn'));}
 }
 await load();const dispose=()=>{dead=true;++epoch;};
 dispose.update=value=>{let changed=false;const next=value.result_b??value.evaluationId;if(next!==undefined&&next!==evaluation){evaluation=next;offset=0;selected='';changed=true;}if(value.trajectoryId!==undefined&&value.trajectoryId!==trajectory){trajectory=value.trajectoryId;offset=0;changed=true;}if(value.step!==undefined&&value.step!==step){step=value.step;offset=0;changed=true;}if(changed)load();};
 return dispose;
}

async function renderLifecycleDetail(body,item,ctx,scope,valid,onComparison){
 const response=await ctx.api('methods/object',{id:item.id});if(!valid())return;assertScope(response.object,scope);body.replaceChildren();
 const data=response.object.data||{},facts=node('div','st-facts');for(const [label,value]of [['Checkpoint',item.checkpoint_id??data.checkpoint_id],['生效范围',item.effective_scope??data.effective_scope??scope],['技能变化',item.target??data.target],['决定及依据',[display(item.decision),item.reason].filter(Boolean).join(' · ')]])facts.append(pair(label,value));body.append(facts);
 for(const ref of array(item.source_refs||data.source_refs))appendTrace(body,ref,ctx,scope,'查看来源轨迹');
 if(item.comparison_ids?.length){const links=node('div','s-controls');for(const id of item.comparison_ids)links.append(button('查看对照 · '+id,()=>onComparison?.(id),'s-link'));body.append(links,node('p','st-note','对照是否匹配执行条件、是否参与开发反馈，在效果对照中分别核查。'));}
 if(item.reported_utility!=null)body.append(pair('日志报告的 utility',item.reported_utility),node('p','st-note','这是方法原日志值；独立评测中的条件贡献需查看匹配的有 / 无技能对照。'));
 const changes=data.changes??data.diff??data.change;if(changes!=null)body.append(raw('保存的修改内容',changes));
 body.append(raw('完整生命周期事件与来源',response));
}
function decisionValues(decision){return [decision?.reward,decision?.advantage,decision?.mask??decision?.loss_mask,decision?.source??decision?.definition??decision?.estimator].map(known);}
async function renderCreditDetail(body,item,ctx,scope,valid){
 const response=await ctx.api('methods/object',{id:item.id});if(!valid())return;assertScope(response.object,scope);body.replaceChildren();
 const rows=table(['信用对象','Reward','Advantage','Mask','计算 / 监督来源']);addRow(rows.body,['技能决策',...decisionValues(item.skill_decision)]);addRow(rows.body,['动作决策',...decisionValues(item.action_decision)]);body.append(rows.wrap);
 const credit=item.model_selection_credit||{};body.append(node('p','st-note',credit.reason||'模型是否作出了可计算信用的技能选择，以选择来源与决策 token 的记录为准。'));
 const detail=node('div','st-facts');for(const[label,value]of [['训练批次',item.batch_id],['关联更新',item.update_ids],['更新完成',item.update_completed===true?'已完成':item.update_completed===false?'记录为未完成':'未记录']])detail.append(pair(label,value));body.append(detail);
 if(item.instance_id){const instanceBody=node('div'),detail=fold('对应技能实例与动作片段',instanceBody);body.append(detail);let loaded=false;detail.addEventListener('toggle',async()=>{if(!detail.open||loaded||!valid())return;loaded=true;try{await renderInstance(instanceBody,{id:item.instance_id,selection_source:item.selection_source},ctx,scope,valid);}catch(error){if(valid())instanceBody.replaceChildren(empty(error.message));}});}
 body.append(raw('完整训练使用与信用字段',response));
}
function renderTrainingRows(parent,page,kind,ctx,scope,valid,selection,onComparison){
 const items=array(page?.items).filter(item=>!item.scope||scopeMatches(item,scope));
 if(!items.length){parent.append(empty(kind==='events'?'没有已记录的创建、修改或接受 / 退役事件。已有快照只作为资产观测保留。':'尚未保存技能实例的训练信用记录。外部技能库为空，也可以记录 SkillCredit。'));return;}
 for(const item of items){const detail=node('details','st-training-row');detail.dataset.skillRecord=item.id;if(item.instance_id)detail.dataset.creditInstance=item.instance_id;const summary=node('summary','st-training-summary');
  if(kind==='events')summary.append(pair('训练 step / checkpoint',`${known(item.step)} · ${known(item.checkpoint_id)}`),pair('变化',display(item.event)),pair('决定',display(item.decision)),pair('依据',item.reason));
  else summary.append(pair('技能实例',item.instance_id||item.id),pair('日志中的技能 Advantage',item.skill_decision?.advantage),pair('动作 Advantage',item.action_decision?.advantage),pair('选择 / 信用口径',display(item.selection_source)+(item.model_selection_credit?.value===false?' · 不属于模型选择信用':item.model_selection_credit?.value==null?' · 模型选择信用未确认':'')));
  const body=node('div','st-instance-body');detail.append(summary,body);parent.append(detail);let loaded=false,pending=false;
  async function load(){if(!detail.open||loaded||pending||!valid())return;pending=true;patch(ctx,{[kind==='events'?'skill_event':'skill_credit']:item.id,...(kind==='credits'?{skill_credit_instance:item.instance_id}: {})});body.replaceChildren(empty('读取已保存的依据…'));try{await(kind==='events'?renderLifecycleDetail:renderCreditDetail)(body,item,ctx,scope,()=>valid()&&body.isConnected,onComparison);if(valid())loaded=true;}catch(error){if(valid())body.replaceChildren(empty(error.message),button('重试记录',load,'s-btn'));}finally{pending=false;}}
  detail.addEventListener('toggle',load);const selectedInstance=ctx.q.get('skill_credit_instance');if(selection===item.id&&(kind==='events'||selectedInstance===item.instance_id||!selectedInstance&&items.filter(value=>value.id===selection).length===1)){detail.open=true;load();}
 }
}

/** Lifecycle and SkillCredit share the RL run while remaining distinct records. */
export async function mountSkillTraining(host,ctx,scope){
 let dead=false,epoch=0,offset=0,comparisonCleanup=null;const alive=()=>ctx.alive()&&!dead,box=node('section','st-training st-section');box.dataset.skillView='training';host.append(box);
 async function load(){const token=++epoch;comparisonCleanup?.();comparisonCleanup=null;try{
  const response=await ctx.api('methods/skill-lifecycle',{experiment_id:scope.experiment_id,run_id:scope.run_id,offset,limit:25});if(!alive()||token!==epoch)return;assertScope(response,scope);box.replaceChildren();
  const heading=section('Skill 训练与开发过程');if(response.mode?.value&&response.mode.value!=='unknown')heading.append(badge(display(response.mode.value)));box.append(heading,node('p','st-note','按当时的 checkpoint 查看技能变化与决策依据。技能实例信用单独记录；训练反馈与独立任务成绩分别查看。'));
  const events=node('div'),credits=node('div'),compare=node('div');
  const chooseComparison=id=>{patch(ctx,{skill_comparison:id,skill_matrix:'1'});comparisonCleanup?.select?.(id);compare.scrollIntoView({block:'nearest'});};
  renderTrainingRows(events,response.events,'events',ctx,scope,alive,ctx.q.get('skill_event'),chooseComparison);renderTrainingRows(credits,response.credits,'credits',ctx,scope,alive,ctx.q.get('skill_credit'));
  box.append(fold('技能生命周期 · '+number(response.events?.total)+' 条',events,!!ctx.q.get('skill_event')),fold('SkillCredit · '+number(response.credits?.total)+' 条训练使用',credits,!!ctx.q.get('skill_credit')));
  if(response.observed_snapshots?.total>0||array(response.observed_snapshots?.items||response.observed_snapshots).length)box.append(node('p','st-note','本运行另有已保存的技能库快照。快照变化和明确记录的生命周期决定分别保留。'));
  if(response.events?.has_more||response.credits?.has_more||offset){const prev=button('上一页',()=>{offset=Math.max(0,offset-25);load();},'s-btn small'),next=button('下一页',()=>{offset+=25;load();},'s-btn small');prev.disabled=!offset;next.disabled=!response.events?.has_more&&!response.credits?.has_more;box.append(controls(prev,node('span','st-note','记录偏移 '+offset),next));}
  box.append(compare);const dispose=await mountSkillComparison(compare,{...ctx,alive:()=>alive()&&token===epoch},scope);if(!alive()||token!==epoch)dispose?.();else comparisonCleanup=dispose;
 }catch(error){if(alive()&&token===epoch)box.replaceChildren(section('Skill 训练与开发过程'),empty(error.message),button('重试技能过程',load,'s-btn'));}}
 await load();const dispose=()=>{dead=true;++epoch;comparisonCleanup?.();};dispose.update=()=>{};return dispose;
}

/** New standalone skill assets supplement the existing imported bank browser. */
export async function mountSkillAssets(host,ctx,scope){
 let dead=false,epoch=0;const alive=()=>ctx.alive()&&!dead;
 const response=await ctx.api('methods/catalog',{...scope,...(!scope.run_id?{unbound:'1'}:{}),kind:'skill_asset',limit:100});if(!alive()||!response.total)return()=>{dead=true;};
 const box=node('section','st-assets st-section');host.append(box);box.append(section('独立技能包与版本'),node('p','st-note','每个资产保存准确版本及完整内容哈希；历史实例继续引用当时版本。'));
 for(const item of array(response.objects)){if(!scopeMatches(item,scope))continue;const detail=node('details','st-asset-row'),body=node('div','st-instance-body');detail.dataset.skillAsset=item.id;detail.append(node('summary','',item.title||item.id),body);box.append(detail);let loaded=false;
  detail.addEventListener('toggle',async()=>{if(!detail.open||loaded||!alive())return;loaded=true;const token=epoch;body.replaceChildren(empty('读取冻结技能包…'));try{const result=await ctx.api('methods/object',{id:item.id});if(!alive()||token!==epoch)return;assertScope(result.object,scope);body.replaceChildren(textBlock('此版本的完整技能包',result.object.data),raw('身份、内容哈希与来源',result));}catch(error){if(alive())body.replaceChildren(empty(error.message));}});
 }
 if(response.has_more)box.append(node('p','st-note','当前列出前 100 个资产版本；未合并其他版本。'));return()=>{dead=true;++epoch;};
}

/** The declaration specifies all evaluation IDs; never choose the best or latest. */
export async function mountSkillComparison(host,ctx,scope){
 let dead=false,epoch=0,loaded=false,selected=ctx.q.get('skill_comparison')||'',catalog=null;const alive=()=>ctx.alive()&&!dead,body=node('div'),container=fold('关键轮次的技能效果对照',body,ctx.q.get('skill_matrix')==='1'||!!selected);container.classList.add('st-comparison');host.append(container);
 async function load(){if(!container.open||loaded||!alive())return;loaded=true;const token=++epoch;patch(ctx,{skill_matrix:'1'});body.replaceChildren(empty('读取已保存的交叉对照…'));
  try{
   if(!catalog)catalog=await ctx.api('methods/catalog',{...scope,...(!scope.run_id?{unbound:'1'}:{}),kind:'skill_comparison',limit:100});if(!alive()||token!==epoch)return;
   const declarations=array(catalog.objects).filter(object=>scopeMatches(object,scope));
   if(selected&&!declarations.some(object=>object.id===selected)){body.replaceChildren(empty('指定对照不在当前实验 / 运行中。没有替换成其他对照。'));return;}
   if(!selected&&declarations.length===1)selected=declarations[0].id;
   body.replaceChildren();
   if(!declarations.length){body.append(empty('尚未保存交叉评测或单技能移除对照。空白格不代表零分。'));return;}
   if(declarations.length>1)body.append(field('明确保存的技能对照',select([['','选择一份对照'],...declarations.map(object=>[object.id,object.title||object.id])],selected,id=>{selected=id;patch(ctx,{skill_comparison:id});loaded=false;load();},'技能效果对照记录')));
   if(!selected){body.append(empty('有多份对照，请明确选择。没有自动取最高成绩或最新记录。'));return;}
   patch(ctx,{skill_comparison:selected});const result=await ctx.api('methods/skill-comparison',{id:selected});if(!alive()||token!==epoch)return;
   if(result.scope)assertScope(result,scope);if(result.object)assertScope(result.object,scope);
   const content=node('div');body.append(content);renderComparison(content,result,ctx,scope);
  }catch(error){if(alive()&&token===epoch)body.replaceChildren(empty(error.message),button('重试对照',()=>{loaded=false;load();},'s-btn'));}
 }
 container.addEventListener('toggle',load);if(container.open)await load();const dispose=()=>{dead=true;++epoch;container.removeEventListener('toggle',load);};dispose.select=id=>{selected=id;loaded=false;container.open=true;load();};return dispose;
}
function renderComparison(host,result,ctx,scope){
 host.append(node('p','st-note','固定题单、检索环境、预算与评分协议，比较 checkpoint 和冻结技能状态。分数为数据集宏平均 EM，每个格子对应明确的评测记录。'));
 if(result.feedback_state?.used_for_skill_iteration)host.append(badge('参与技能迭代 · 开发反馈','warn'),node('p','st-note',result.feedback_state.classification==='test_used_as_development_feedback'?'这份对照已用于修改技能，相关测试题成为开发反馈，不能作为本轮最终泛化证据。':'这份对照已关联技能迭代决定。最终泛化需要另外的固定评测与训练隔离证据。'));
 if(result.reason)host.append(node('p','st-note',result.reason));
 const matrix=result.matrix||{},columns=array(matrix.columns),rows=array(matrix.rows),cells=array(matrix.cells);
 if(rows.length&&columns.length){
  const t=table(['策略 / Checkpoint',...columns.map(col=>col.label||col.id)]);
  for(const row of rows){const name=node('div');name.append(node('strong','',row.label||row.id),node('small','s-muted',row.checkpoint_id||'Checkpoint 未记录'));const values=[name];for(const column of columns){const cell=cells.find(item=>item.row_id===row.id&&item.column_id===column.id)||{},body=node('div','st-matrix-cell');body.dataset.skillCell=row.id+':'+column.id;body.dataset.status=cell.status||'unmeasured';
    const text=cell.status==='incomparable'?'条件不匹配':cell.status==='selection_required'?'待选择评测':cell.status==='measured'&&finite(cell.value)?(cell.value*100).toFixed(1)+'%':cell.status==='unscored'?'未评分':'未测';body.append(node('strong','',text));
    if(cell.evaluation_id){body.append(node('small','s-muted',cell.evaluation_id));body.append(link('打开该次评测',href('experiment',{project:ctx.project,track:ctx.track,experiment:scope.experiment_id,run:scope.run_id,view:'results',result_b:cell.evaluation_id}),'s-link'));}if(cell.reason)body.append(node('small','s-muted',cell.reason));values.push(body);
   }addRow(t.body,values);}host.append(t.wrap);
 }else if(result.arms){const t=table(['技能条件','宏平均 EM','评测记录','范围']);for(const [key,label]of [['with','保留此技能'],['without','移除此技能']]){const arm=result.arms[key]||{};addRow(t.body,[label,arm.status==='measured'&&finite(arm.value)?(arm.value*100).toFixed(1)+'%':arm.status==='incomparable'?'条件不匹配':'未测',arm.evaluation_id||'未记录',arm.reason||'未记录']);}host.append(t.wrap);}
 else host.append(empty(result.reason||'这份记录没有完整的交叉评测配置。'));
 if(array(result.effects).length){const t=table(['对照问题','已核验差值','解释范围']);for(const effect of result.effects)addRow(t.body,[effect.label||effect.id,effect.status==='measured'&&finite(effect.value)?(effect.value>=0?'+':'')+(effect.value*100).toFixed(1)+' pp':effect.status==='incomparable'?'条件不匹配':'未测',effect.reason||'以此对照的匹配条件为准']);host.append(t.wrap);}
 if(result.utility!=null)host.append(pair('移除单技能后的条件贡献',result.utility));
 host.append(node('p','st-note','有 / 无某个技能的差值是当前策略、其他技能和任务分布下的条件贡献。它不等于训练信用；撤去技能后不掉分也不足以证明内化。'),raw('对照定义、控制条件与评测依据',result));
}
