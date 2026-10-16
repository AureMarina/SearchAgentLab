import {node,button,tag,short,pretty,empty,kindLabels,statusLabels,groupLabels,link,methodURL,refURL,evidenceLink,pair,fold,note,objectLink,synthetic,section,jsonDialog,findingsPanel} from './common.js';
import {JsonTree} from '../inspector/json-tree.js';
import {TraceTree} from '../inspector/trace-tree.js';

/** Method workspace is a subview of research overview, not another chat app. */
export async function mountMethods(host,{q,session,api,toast,embedded=false,setViewState,alive:parentAlive=()=>true}){
 let scope={};for(const k of ['project_id','experiment_id','run_id'])if(q.has(k))scope[k]=q.get(k);
 let id=q.get('id')||'',view=q.get('view')||'',catalog=null,active=null,epoch=0;
 const shell=node('div','mx-workspace');host.replaceChildren(shell);
 const alive=()=>shell.isConnected&&parentAlive();
 const top=node('div','mx-heading');const title=node('div');title.append(node('div','eyebrow','METHOD PROCESSES / ARTIFACTS'),node('h1','','方法过程与产物'),node('p','subtitle','题是怎样生成的，技能怎样演化，哪些材料真正进入了请求或训练。'));
 const actions=node('div','mx-actions');const input=node('input');input.type='file';input.accept='.json,.jsonl,.ndjson';input.hidden=true;input.id='mx-file-input';
 const open=button('＋ 打开 SkillBank / 方法记录',()=>input.click(),'button primary');open.id='mx-open';
 actions.append(open,input,link('返回训练总览',scope.project_id?'#/overview?project='+encodeURIComponent(scope.project_id):'#/projects'));
 top.append(title,actions);shell.append(top);
 const scbar=node('div','card mx-scope');shell.append(scbar);
 const ps=node('select');ps.id='mx-project';ps.setAttribute('aria-label','方法记录所属项目');const standalone=node('option','','独立检查 · 未归类');standalone.value='';ps.append(standalone);
 for(const p of session.projects){const op=node('option','',p.name);op.value=p.id;ps.append(op);}ps.value=scope.project_id||'';
 const exsel=node('select');exsel.id='mx-experiment';exsel.setAttribute('aria-label','方法记录所属实验');
 const rs=node('select');rs.id='mx-run';rs.setAttribute('aria-label','方法记录所属运行');
 for(const [label,el] of [['研究项目',ps],['实验方案',exsel],['训练运行',rs]]){const l=node('label','',label);l.append(el);scbar.append(l);}
 scbar.append(node('span','mx-small','只读浏览不会创建训练记录。导入归属在此明确选择。'));
 const tabs=node('div','mx-tabs');shell.append(tabs);const tabsMap={};
 for(const [v,label] of [['selfplay','Self-play 轮次'],['skills','SkillBank'],['artifacts','产物与来源']]){const b=button(label,()=>{view=v;id='';refreshView();},'mx-tab');b.id='mx-tab-'+v;tabsMap[v]=b;tabs.append(b);}
 const banner=note('共享工具，不混合研究结果。查看器不执行出题、训练、技能更新或模型推理。');shell.append(banner);
 const grid=node('div','mx-grid');shell.append(grid);const left=node('aside','card mx-list');const main=node('section','card mx-main');grid.append(left,main);
 const lh=node('div','mx-list-header');const listTitle=node('h2','','记录');const search=node('input');search.id='mx-list-search';search.placeholder='搜索名称 / ID';lh.append(listTitle,search);left.append(lh);
 const list=node('div','mx-object-list');list.id='mx-object-list';left.append(list);
 const footer=node('div','mx-small');left.append(footer);
 const post=async(path,body)=>api(path,{}, {method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':session.csrf_token},body:JSON.stringify(body)});
 if(embedded){shell.classList.add('mx-embedded');top.remove();scbar.remove();tabs.remove();banner.remove();}
 function url(){if(embedded){setViewState?.({object:id,subview:view});return;}const p={...scope,...(view?{view}:{}),...(id?{id}:{})};history.replaceState(null,'','#/methods?'+new URLSearchParams(p));}
 function withReturn(url){if(!embedded)return url;const [page,query='']=url.split('?');const params=new URLSearchParams(query);params.set('return_experiment',scope.experiment_id||'');if(scope.run_id)params.set('return_run',scope.run_id);params.set('return_view',view==='skills'?'skill':view==='selfplay'?'self_play':'latent');params.set('return_object',id);return page+'?'+params;}
 function traceURL(ref){return withReturn(refURL(ref));}
 async function scopeOptions(){
  let experiments=[];let runs=[];
  if(ps.value){const c=await api('catalog',{project:ps.value});experiments=c.experiments;runs=c.runs;}
  exsel.replaceChildren();let op=node('option','','全部实验');op.value='';exsel.append(op);for(const e of experiments){op=node('option','',e.name);op.value=e.id;exsel.append(op);}exsel.value=scope.experiment_id||'';
  rs.replaceChildren();op=node('option','','全部运行');op.value='';rs.append(op);for(const r of runs.filter(x=>!scope.experiment_id||x.experiment_id===scope.experiment_id)){op=node('option','',r.name+' · '+r.id);op.value=r.id;rs.append(op);}rs.value=scope.run_id||'';
 }
 function selectedScope(){return {...(ps.value?{project_id:ps.value}:{}),...(exsel.value?{experiment_id:exsel.value}:{}),...(rs.value?{run_id:rs.value}:{})};}
 ps.onchange=async()=>{scope=ps.value?{project_id:ps.value}:{};id='';await scopeOptions();await loadCatalog();};
 exsel.onchange=async()=>{scope={project_id:ps.value,...(exsel.value?{experiment_id:exsel.value}:{})};id='';await scopeOptions();await loadCatalog();};
 rs.onchange=async()=>{scope=selectedScope();id='';await loadCatalog();};
 async function loadCatalog(){const t=++epoch;empty(main,'正在读取方法记录…');try{const c=await api('methods/catalog',{...scope,...(embedded?{kind:view==='skills'?'skill_bank':view==='selfplay'?'selfplay_cycle':'state_snapshot',limit:100}:{})});if(!alive()||t!==epoch)return;catalog=c;
  if(id&&!c.objects.some(x=>x.id===id)&&!embedded){const requested=await api('methods/object',{id});if(!alive()||t!==epoch)return;const os=requested.object.scope||{};
   // A deep link carries an explicit object ID. Show its owner rather than another project's unrelated data.
   scope=os;ps.value=os.project_id||'';await scopeOptions();catalog=await api('methods/catalog',scope);}
  if(!view){const k=catalog.objects.find(x=>x.id===id)?.kind;view=k==='skill_bank'?'skills':k==='selfplay_cycle'?'selfplay':k?'artifacts':catalog.counts.selfplay_cycle?'selfplay':catalog.counts.skill_bank?'skills':'selfplay';}
  await refreshView();
 }catch(e){empty(main,e.message);toast(e.message);}}
 function listItems(){const kinds=view==='selfplay'?['selfplay_cycle']:view==='skills'?['skill_bank']:null;const term=search.value.trim().toLocaleLowerCase();return (catalog?.objects||[]).filter(o=>(!kinds||kinds.includes(o.kind))&&(!term||JSON.stringify(o).toLocaleLowerCase().includes(term)));}
 function renderList(){for(const [v,b] of Object.entries(tabsMap))b.classList.toggle('selected',view===v);listTitle.textContent=view==='selfplay'?'已登记轮次':view==='skills'?'不可变库快照':'产物与过程记录';list.replaceChildren();const items=listItems();
  for(const o of items){const b=button('',()=>select(o.id),'mx-object-row'+(o.id===id?' selected':''));b.dataset.object=o.id;b.append(node('strong','',o.title||o.id),node('span','mx-small',(kindLabels[o.kind]||o.kind)+' · '+o.id));if(o.demo)b.append(tag('合成示例','reference'));list.append(b);}
  footer.textContent=embedded?`当前实验 / 运行 ${catalog?.total??items.length} 条${catalog?.has_more?'；仅显示前 100 条，请用具体记录链接定位':''}`:`当前范围 ${items.length} 条 / 全范围 ${catalog?.all_scope_count||0} 条；不跨项目汇总成绩。`;
  if(!items.length)list.append(note('尚无这类记录。可以直接打开技能文件，或导入方法证据 bundle。'));
 }
 async function refreshView(){url();renderList();const items=listItems();if(!items.find(o=>o.id===id)&&!embedded)id=items[0]?.id||'';else if(!id)id=items[0]?.id||'';if(id)await select(id);else{
  main.replaceChildren(node('h2','mx-empty-title',view==='skills'?'先打开一份实际 SkillBank':'从一轮真实过程开始'));
  main.append(note(view==='skills'?'支持 general_skills、query_type_skills、task_specific_skills、common_mistakes。未知字段原样保留；不虚构生成或使用记录。':'method-evidence-v1 显式关联出题者、生成题、审核和解题者。没有完整历史，也可以先接入已有产物。'));
  main.append(button('打开文件',()=>input.click(),'button primary'),link('查看当前轨迹检查器','#/trace'));
 }}
 search.oninput=renderList;
 async function select(next){id=next;url();renderList();const token=++epoch;empty(main,'正在加载内容与来源…');try{
  const data=await api('methods/object',{id});if(!alive()||token!==epoch)return;
  if(embedded&&['project_id','experiment_id','run_id'].some(k=>(data.object.scope?.[k]||'')!==(scope[k]||'')))throw Error('此记录不属于当前实验 / 运行；未加载其他运行的内容。');active=data;
  if(view==='skills'&&data.object.kind==='skill_bank')await renderBank(data,token);
  else if(view==='selfplay'&&data.object.kind==='selfplay_cycle')await renderCycle(data,token);
  else renderArtifact(data);
 }catch(e){empty(main,e.message);toast(e.message);}}
 function objectHeading(o){const h=node('div','mx-object-heading');const title=node('div');title.append(node('div','eyebrow',kindLabels[o.kind]||o.kind),node('h2','',o.title||o.id),node('div','mx-small',o.id+' · 内容 SHA256 '+o.content_hash.slice(0,16)));if(synthetic(o))title.append(synthetic(o));const a=node('div','mx-actions');a.append(link('原始 JSON / 诊断 ↗',refURL({object_id:o.id})),link('导出证据','/api/methods/export?id='+encodeURIComponent(o.id)));h.append(title,a);return h;}
 function sourceBlock(o){const b=node('div','mx-source');b.append(pair('用途',o.usage||'unknown'),pair('来源对象',o.derived_from||[]));for(const id of o.derived_from||[])b.append(link('来源：'+id,methodURL(id,'artifacts')));for(const r of o.evidence_refs||[])b.append(evidenceLink(r));if(!(o.derived_from?.length||o.evidence_refs?.length))b.append(note('来源未记录；不根据技能内容或题目文字补造历史。'));return b;}
 async function preview(ref,label,parent){if(!ref){parent.append(note(label+'未保存，不能重建。'));return;}
  const body=node('div','mx-trace-preview');body.append(evidenceLink(ref,'在完整检查器打开 '+label));const payload=node('div');body.append(payload);const box=fold(label,body);parent.append(box);let loaded=false;
  box.addEventListener('toggle',async()=>{if(!box.open||loaded)return;loaded=true;try{const params=ref.object_id?{method:ref.object_id}:ref.trajectory?{trajectory:ref.trajectory}:{file:ref.file,record:ref.record};const doc=await api('inspection/record',params);if(!payload.isConnected)return;new TraceTree(payload,doc,{states:new Map(),onSelect:p=>{location.hash=traceURL({...ref,pointer:p});}});}catch(e){empty(payload,e.message);}});
 }
 async function renderCycle(data,token){const d=await api('methods/cycle',{id});if(!alive()||token!==epoch)return;main.replaceChildren(objectHeading(d.cycle));
  const counts=node('div','mx-counts');for(const [k,label] of [['generated','出题'],['accepted','审核接受'],['rejected','审核驳回'],['attempts','解题尝试'],['sampled_questions','有采样记录的题']]){const c=node('div');c.append(node('strong','',d.counts[k]),node('span','',label));counts.append(c);}main.append(counts);
  const participants=node('div','mx-participants');for(const p of d.cycle.data.participants||[]){const a=node('div','mx-participant');a.append(node('strong','',p.label||p.id),node('div','mx-small',p.id),pair('实际策略',p.checkpoint_id||p.policy_ref),pair('模型',p.model));participants.append(a);}main.append(participants,note('出题答案不是 Groundtruth；审核通过、选入材料、实际采样分别保存。每个角色的可见输入在它自己的轨迹中查看。'));
  for(const item of d.questions){const qobj=item.question;const card=node('section','mx-question-card');card.dataset.question=qobj.id;
   const head=node('div','mx-question-head');head.append(tag(qobj.id),tag(statusLabels[item.review_status]||item.review_status,item.review_status==='accepted'?'correct':item.review_status==='rejected'?'incorrect':''),objectLink(qobj));card.append(head,node('h3','',qobj.data.question));
   const answers=node('div','mx-answer-grid');answers.append(pair('出题者提出的答案',qobj.data.proposed_answer));
   const verified=item.reviews.filter(x=>item.active_review_ids.includes(x.id)&&x.data.status==='accepted').map(x=>x.data.verified_answer).filter(x=>x!=null);
   answers.append(pair('审核者确认的答案',verified.length?verified:null));card.append(answers);
   for(const p of item.proposals){const from=d.cycle.data.participants.find(x=>x.id===p.data.actor_id);const to=d.cycle.data.participants.find(x=>x.id===p.data.solver_actor_id);
    card.append(node('div','mx-direction',(from?.label||p.data.actor_id)+' 出题 → '+(to?.label||p.data.solver_actor_id||'解题对象未记录')));await preview(p.data.trace_ref,'出题轨迹 · '+p.id,card);}
   const reviewBody=node('div');for(const rev of item.reviews){reviewBody.append(objectLink(rev),pair('审核状态',statusLabels[rev.data.status]),pair('审核器 / 版本',rev.data.reviewer),pair('审核说明',rev.data.reason),pair('确认答案',rev.data.verified_answer));for(const r of rev.evidence_refs||[])reviewBody.append(evidenceLink(r));}
   if(!item.reviews.length)reviewBody.append(note('尚无审核记录。'));card.append(fold('审核与修订记录 · '+item.reviews.length,reviewBody));
   const at=node('div','mx-attempt-list');for(const a of item.attempts){const body=node('div','mx-attempt');body.append(objectLink(a),pair('实际用于评分的参考答案',a.data.reference_used));const signals=node('div','mx-inline-stats');signals.append(tag(a.data.scores?.em==null?'EM 未记录':'EM = '+a.data.scores.em,a.data.scores?.em===1?'correct':a.data.scores?.em===0?'incorrect':''),tag('reward '+(a.data.reward??'未记录')),tag('advantage '+(a.data.advantage??'未记录')));body.append(signals);
    if(a.data.groundtruth_visible===true)body.append(note('记录声明答案对 solver 可见；不能记为无参考自主成功。','warning'));await preview(a.data.trace_ref,'解题轨迹 · '+a.data.actor_id+' · '+a.id,body);at.append(body);}
   card.append(fold('解题尝试 · '+item.attempts.length,at,true));
   const use=node('div');for(const u of item.training_uses){use.append(objectLink(u),pair('阶段',statusLabels[u.data.status]),pair('Batch / step',u.data.batch_id==null?null:{batch_id:u.data.batch_id,step:u.data.step}));for(const related of active.related||[]){void related;}}
   if(!item.training_uses.length)use.append(note('没有训练消费记录；不是自动认定未使用。'));card.append(fold('训练去向 · '+item.training_uses.length,use));
   if(item.lineage.restricted.length)card.append(note('已登记来源含 validation/test。请核查用途，不将其后续结果当作独立验证证明。','warning'));main.append(card);
  }
  const updates=section('本轮参数更新记录');for(const u of d.updates){updates.append(objectLink(u),pair('输出 checkpoint',u.data.output_checkpoint_id));if(u.scope?.run_id)updates.append(link('训练运行与固定评测 ↗','#/overview?'+new URLSearchParams({project:u.scope.project_id,run:u.scope.run_id})));}if(!d.updates.length)updates.append(note('未登记更新记录，不从轮次号推测 checkpoint。'));main.append(updates,fold('方法专属检查',findingsPanel(d.diagnostics)));
 }
 async function renderBank(data,token){const bank=await api('methods/bank',{id});if(!alive()||token!==epoch)return;main.replaceChildren(objectHeading(bank.snapshot));
  main.append(note('只读快照：历史技能不跟随最新文件变化。支持 Search 的 query_type_skills；不会把技能当成函数调用或生成虚构 start/end。'));
  const controls=node('div','mx-bank-toolbar');const filter=node('select');filter.id='mx-skill-group';filter.setAttribute('aria-label','技能分类');const all=node('option','','全部分类');all.value='';filter.append(all);
  for(const g of [...new Set(bank.members.map(m=>m.group))]){const op=node('option','',groupLabels[g]||g);op.value=g;filter.append(op);}
  const find=node('input');find.id='mx-skill-search';find.placeholder='搜索全部 skill 内容、条件和 ID';const baseline=node('select');baseline.id='mx-bank-baseline';baseline.setAttribute('aria-label','对照技能库');let opt=node('option','','选择另一个快照');opt.value='';baseline.append(opt);
  for(const b of catalog.objects.filter(x=>x.kind==='skill_bank'&&x.id!==id)){opt=node('option','',b.title||b.id);opt.value=b.id;baseline.append(opt);}const diff=button('比较版本差异',async()=>{if(!baseline.value)return toast('先选择对照快照。');try{renderDiff(await api('methods/diff',{a:baseline.value,b:id}),diffHost);}catch(e){toast(e.message);}},'button small');diff.id='mx-bank-diff';controls.append(filter,find,baseline,diff);main.append(controls);
  const bankGrid=node('div','mx-bank-grid');const skillList=node('div','mx-skill-list');skillList.id='mx-skill-list';const detail=node('div','mx-skill-detail');detail.id='mx-skill-detail';bankGrid.append(skillList,detail);main.append(bankGrid);const diffHost=node('div');diffHost.id='mx-diff';main.append(diffHost);
  let selected='',limit=100,skToken=0;
  function drawSkills(){const term=find.value.toLocaleLowerCase();const ms=bank.members.filter(m=>(!filter.value||m.group===filter.value)&&(!term||pretty(m.content).toLocaleLowerCase().includes(term)||m.key.toLocaleLowerCase().includes(term)));skillList.replaceChildren(node('div','mx-small',ms.length+' 个条目（技能 '+bank.skill_count+' / 经验教训 '+bank.mistake_count+'）'));
   for(const m of ms.slice(0,limit)){const b=button('',()=>selectSkill(m),'mx-skill-row'+(selected===m.pointer?' selected':''));b.dataset.skill=m.pointer;b.append(node('strong','',m.title),node('div','mx-small',(m.source_id||'ID 未记录')+' · '+(m.category||groupLabels[m.group])),node('div','mx-small',short(m.principle||m.content.description||'',94)));skillList.append(b);}if(ms.length>limit)skillList.append(button('显示更多',()=>{limit+=100;drawSkills();}));
   if(!selected&&ms.length)selectSkill(ms[0]);else if(!ms.length)empty(detail,'没有匹配的技能。');}
  async function selectSkill(m){selected=m.pointer;drawSkills();const local=++skToken;detail.replaceChildren(node('h3','',m.title),node('div','mx-small',m.source_id||'原始 ID 未记录'),tag(m.category||groupLabels[m.group]));
   detail.append(pair(m.group==='common_mistakes'?'经验描述':'原则',m.principle||m.content.description),pair(m.group==='common_mistakes'?'避免方法':'适用条件',m.when_to_apply||m.content.how_to_avoid));
   if(m.group==='common_mistakes')detail.append(note('这是库内保存的经验描述，不是当前轨迹已验证的根因。'));
   detail.append(pair('技能内容 SHA256',m.content_hash),evidenceLink({object_id:id,pointer:m.pointer,sha256:bank.snapshot.content_hash},'定位此技能原始 JSON'));
   const raw=node('div');new JsonTree(raw,m.content,{label:'Skill 原始字段',open:false});detail.append(fold('完整字段',raw),fold('技能来源 / 提炼依据',sourceBlock(bank.snapshot)));
   const usage=node('div');detail.append(usage);try{const u=await api('methods/usage',{bank:id,member:m.pointer});if(local!==skToken||!alive())return;const counts=node('div','mx-usage-counts');for(const [k,l] of [['candidate','候选记录'],['selected','入选记录'],['injection_recorded','注入记录'],['fragment_verified','输入片段核对']])counts.append(pair(l,u.counts[k]));usage.append(counts,note('以上是日志覆盖数，不是技能贡献或成功率；没有记录不等于没有使用。'));
    for(const x of u.uses){const card=node('div','mx-use-card');card.append(objectLink(x.record),pair('检索配置',x.record.data.mode),tag(x.selected?'已入选':'候选'));for(const j of x.injections){card.append(tag(j.exact_rendered_fragment_visible===true?'日志输入含渲染片段':j.exact_rendered_fragment_visible===false?'片段核对未命中':'缺少输入证据',j.exact_rendered_fragment_visible===false?'candidate':''),pair('实际渲染文本',j.rendered_text));if(j.evidence.available)card.append(evidenceLink(j.evidence.ref,'定位模型输入'));}if(x.trace.available)card.append(evidenceLink(x.trace.ref,'查看相关轨迹'));usage.append(card);}
    if(!u.uses.length)usage.append(note('尚无该技能的检索或注入记录。可以先阅读技能文件，后续导入 usage 日志。'));
   }catch(e){empty(usage,e.message);}}
  filter.onchange=()=>{selected='';limit=100;drawSkills();};find.oninput=()=>{selected='';limit=100;drawSkills();};drawSkills();
  if(bank.warnings.length)main.append(fold('格式提示 / 保留字段',note(bank.warnings.join('\n'))));
  const ev=section('演化与评测关联');for(const o of bank.related.filter(x=>x.kind!=='skill_retrieval')){ev.append(objectLink(o));if(o.kind==='evaluation_binding')ev.append(link('该次固定评测轨迹','#/trace?evaluation='+encodeURIComponent(o.data.evaluation_id)));}if(!ev.querySelector('a'))ev.append(note('尚未登记提炼、演化或评测绑定。没有用文件名猜测来源。'));main.append(ev,fold('方法专属检查',findingsPanel(data.diagnostics)));
  const matrixHost=section('Checkpoint × SkillBank · 已登记评测');matrixHost.id='mx-evaluation-matrix';main.append(matrixHost);
  try{const matrix=await api('methods/evaluation-matrix',{bank:id});if(!alive()||token!==epoch)return;
   matrixHost.append(note(matrix.statement));
   if(!matrix.groups.length)matrixHost.append(note('尚无评测绑定；这里只展示已保存结果，不启动模型评测。'));
   for(const group of matrix.groups){
    const box=node('div','mx-matrix-group');box.append(node('h4','',group.panel.name+' · '+group.id.slice(0,10)),note(group.conditions_complete?'登记条件完整；这不证明实际远端执行或因果贡献。':'条件记录不完整，仅供查阅，不作受控效果比较。'));
    const cols=[...new Set(group.cells.map(c=>c.bank_id||'no-skill'))];const rows=[...new Set(group.cells.map(c=>c.checkpoint.id))];
    const table=node('table','mx-matrix-table');const thead=node('thead');const hr=node('tr');hr.append(node('th','','Checkpoint'));for(const col of cols)hr.append(node('th','',col));thead.append(hr);table.append(thead);const tbody=node('tbody');
    for(const row of rows){const tr=node('tr');tr.append(node('th','',row));for(const col of cols){const td=node('td');const cells=group.cells.filter(c=>c.checkpoint.id===row&&(c.bank_id||'no-skill')===col);if(!cells.length)td.append(node('span','mx-small','未评测'));for(const c of cells){const item=node('div','mx-matrix-cell');item.append(link(`${c.stats.answer_correct}/${c.stats.expected} · 已评分 ${c.stats.scored}/${c.stats.expected}`,'#/trace?evaluation='+encodeURIComponent(c.evaluation_id)),node('div','mx-small',c.evaluation_id));if(c.demo)item.append(tag('合成','reference'));if(c.binding_origin==='retrospective')item.append(tag('历史回填'));td.append(item);}tr.append(td);}tbody.append(tr);}table.append(tbody);box.append(table);matrixHost.append(box);
   }
  }catch(e){matrixHost.append(note(e.message));}
  const originals=node('div','mx-actions');for(const f of bank.imports)originals.append(link('导出原文件 · '+f.name,'/api/methods/original?id='+encodeURIComponent(f.id)));main.append(originals);
 }
 function renderDiff(d,host){host.replaceChildren(node('h3','','SkillBank 版本差异'));if(!d.comparable){host.append(note(d.reason));return;}host.append(note('配对按原始 group/category/id。重命名显示新增+删除，不猜演化关系；不据此声称性能提升。'));
  const counts=node('div','mx-inline-stats');for(const [s,v] of Object.entries(d.counts))counts.append(tag(({added:'新增',removed:'删除',modified:'修改',unchanged:'不变'})[s]+' '+v));host.append(counts);
  for(const c of d.changes.filter(x=>x.change!=='unchanged')){const body=node('pre','mx-diff-code',c.diff);host.append(fold(c.change+' · '+c.key,body,true));}
 }
 function renderArtifact(data){const o=data.object;main.replaceChildren(objectHeading(o));main.append(pair('用途声明',o.usage||'unknown'),sourceBlock(o));
  const raw=node('div','mx-artifact-json');new JsonTree(raw,o.data,{label:'data · 完整原始字段',open:true,onSelect:p=>{location.hash=traceURL({object_id:o.id,pointer:p});}});main.append(raw);
  const linked=section('明确的上下游关联');for(const r of data.related)linked.append(objectLink(r));if(!data.related.length)linked.append(note('尚无消费或后续关联。'));main.append(linked,fold('来源链与限制',note(pretty(data.lineage))),findingsPanel(data.diagnostics));
  if(embedded)return;
  const review=section('人工复核（追加历史，不改原始内容）');const state=node('select');for(const [k,l] of [['pending','待复核'],['confirmed','人工确认'],['rejected','人工驳回']]){const op=node('option','',l);op.value=k;state.append(op);}const comment=node('textarea');comment.placeholder='记录证据、替代解释与下一步；这不是聊天输入框。';comment.setAttribute('aria-label','方法证据复核说明');const save=button('保存复核',async()=>{try{await post('methods/review',{id:o.id,source_hash:o.content_hash,status:state.value,note:comment.value,pointer:''});toast('复核已追加保存；未改写产物或评分。');await select(o.id);}catch(e){toast(e.message);}});review.append(state,comment,save);for(const r of data.reviews)review.append(note((statusLabels[r.status]||r.status)+' · '+r.created_at+'\n'+r.note));main.append(review);
  const caseButton=button('以此产物和来源建立诊断案例',async()=>{try{const refs=[{object_id:o.id,sha256:o.content_hash,pointer:''},...(o.evidence_refs||[])];const out=await post('methods/diagnostic-case',{title:'方法核查 · '+(o.title||o.id),scope:o.scope,evidence:refs});location.hash=traceURL({object_id:out.case_id});}catch(e){toast(e.message);}});main.append(caseButton,note('联合案例只供事后核查。它不是任一角色当时的可见前缀，也不会被发送给 Agent。'));
 }
 async function importFile(file){if(!file)return;if(file.size>50*1024*1024)return toast('文件超过 50 MiB。');open.disabled=true;
  try{const r=await fetch('/api/methods/upload?'+new URLSearchParams({name:file.name,scope:JSON.stringify(selectedScope())}),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Local-Token':session.csrf_token},body:file});const out=await r.json();if(!r.ok)throw new Error(out.error||'导入失败');id=out.snapshot_id||out.object_ids[0];view=out.kind==='skill_bank'?'skills':'';scope=out.scope||selectedScope();toast('已归档 '+out.count+' 个方法对象；未创建任何训练或评测结果。');await loadCatalog();}catch(e){toast(e.message);banner.textContent=e.message;}finally{open.disabled=false;input.value='';}}
 input.onchange=()=>importFile(input.files[0]);shell.addEventListener('dragover',e=>e.preventDefault());shell.addEventListener('drop',e=>{e.preventDefault();importFile(e.dataTransfer.files[0]);});
 if(embedded){
  // Follow evidence inside the experiment; do not navigate back to the global method workspace.
  shell.addEventListener('click',e=>{const a=e.target.closest('a');if(!a||e.ctrlKey||e.metaKey||e.shiftKey)return;const h=a.getAttribute('href')||'';if(h.startsWith('#/trace?')||h.startsWith('#/replay?')){e.preventDefault();location.hash=withReturn(h);return;}if(!h.startsWith('#/methods?'))return;const next=new URLSearchParams(h.split('?')[1]).get('id');if(next){e.preventDefault();select(next);}});
 }else await scopeOptions();await loadCatalog();return {dispose:()=>{++epoch;}};
}
