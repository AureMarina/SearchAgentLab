import {node,button,link,href,badge,select,field,section,empty,card,controls,jsonModal,table,addRow,modal} from './common.js';
import {JsonTree} from '../inspector/json-tree.js';
const groups={general_skills:'通用技能',query_type_skills:'题型技能',task_specific_skills:'任务技能',common_mistakes:'经验教训'};
const eventLabels={first_observed:'首次在已存快照中出现',reappeared:'重新出现',unchanged:'内容不变',modified:'内容修改',removed_between_saved_snapshots:'相邻快照间消失',not_observed:'未出现',present_in_frozen_snapshot:'冻结评测库含此技能',absent_in_frozen_snapshot:'冻结评测库不含此技能'};
const known=v=>v==null?'未记录':typeof v==='object'?JSON.stringify(v):String(v);
const versionLabel=s=>s.phase==='initial'?'初始库':(s.phase==='evaluation'?'冻结评测':'训练')+' · step '+s.step;
function info(label,value){const e=node('div','md-info');e.append(node('span','s-muted',label),node('div','',known(value)));return e;}
function fold(title,content,open=false){const d=node('details','md-fold');d.open=open;d.append(node('summary','',title),content);return d;}
function scopeOf(ctx){if(ctx.q.get('standalone')==='1')return{};return ctx.q.get('run')?{run_id:ctx.q.get('run')}:{...(ctx.project?{project_id:ctx.project}:{}),...(ctx.q.get('experiment')?{experiment_id:ctx.q.get('experiment')}: {})};}
function heading(host,title,sub,action){const h=node('div','s-heading'),left=node('div');left.append(node('div','s-eyebrow','METHOD EVIDENCE'),node('h1','',title),node('p','s-subtitle',sub));h.append(left);if(action)h.append(action);host.append(h);}

export async function mountMethodData(host,ctx){const{api,alive,q,project,track}=ctx;let scope=scopeOf(ctx),dead=false;
 heading(host,'方法与元数据','项目决定研究归属；方法类型决定要记录和查看的证据。',link('← 训练动态',href('training',{project,track,run:q.get('run')}),'s-btn'));
 if(scope.project_id&&!scope.experiment_id&&!scope.run_id){
  const cat=await api('studio/catalog',{project:scope.project_id,track});if(!alive())return;
  if(!cat.experiments.length){host.append(empty('当前项目 / 分区没有实验。独立导入的技能归档请从独立产物区打开。'),link('查看独立产物',href('method-data',{standalone:'1',family:q.get('family')||'skill'}),'s-btn'));return;}
  const exp=cat.experiments[0].experiment.id;scope.experiment_id=exp;
  const es=select(cat.experiments.map(x=>[x.experiment.id,x.experiment.name]),exp,id=>{location.hash=href('method-data',{project,track,experiment:id,family:q.get('family')||'skill'});},'方法所属实验');host.append(field('当前分区内的实验',es));
 }else if(scope.experiment_id){const exp=await api('experiment',{id:scope.experiment_id});host.append(node('p','s-muted','所属实验：'+exp.name));}
 const ov=await api('methods/overview',scope);if(!alive())return;
 if(!Object.keys(scope).length)host.append(node('div','s-notice','独立产物区 · 尚未绑定训练运行；没有创建虚构 checkpoint 或评测成绩。'));
 ctx.markDemo(ov.profiles.some(p=>p.demo===true));
 const profiles=card();profiles.append(controls(node('strong','','实际方法配置'),...ov.profiles.map(p=>button(p.data.method_name,()=>jsonModal('方法配置 · '+p.id,p),'s-link'))));
 if(!ov.profiles.length)profiles.append(node('p','s-muted','尚未登记 method_profile。产物仍可查阅；不从项目名猜测算法、模型或训练参数。'));
 host.append(profiles);
 const tabs=controls();let family=q.get('family')||ov.families.find(f=>f.recorded)?.id||'skill';const body=node('div');
 for(const f of ov.families){tabs.append(link(f.title.split(' · ')[0]+' · '+(f.recorded?'有记录':f.declared?'仅配置':'未接入'),href('method-data',{project,track,run:q.get('run'),experiment:scope.experiment_id,family:f.id,standalone:q.get('standalone')}),'s-btn '+(f.id===family?'primary':'')));}
 host.append(section('方法专属工作区',tabs),body);const spec=ov.families.find(f=>f.id===family)||ov.families[0];
 const contract=table(['字段组','应记录的内容','它能帮助判断什么']);for(const[k,label,note]of spec.fields)addRow(contract.body,[k,label,note]);body.append(fold(spec.title+' · 字段契约（未接入不填零）',contract.wrap));
 const url=(page,extra={})=>href(page,{project,track,run:scope.run_id,experiment:scope.experiment_id,standalone:q.get('standalone'),...extra});
 if(family==='skill'){
  const cat=await api('methods/catalog',{...scope,kind:'skill_archive',limit:100});if(!alive())return;
  const list=card();body.append(section('技能库归档'),list);
  for(const a of cat.objects){const row=node('div','md-archive-row');row.append(node('strong','',a.title||a.id),badge('快照与技能内容'),link('打开生命周期 →',url('skill-library',{archive:a.id}),'s-btn primary'));list.append(row);}
  if(!cat.objects.length)list.append(empty('尚无技能 ZIP 归档。可导入包含 manifest.json 的 SkillRL 快照包；已有单独 SkillBank 仍可在原方法页查看。'));
  if(cat.has_more)list.append(node('p','s-muted','仅列前 100 个归档；请按运行缩小范围。'));
  const banks=await api('methods/catalog',{...scope,kind:'skill_bank',limit:25});if(!alive())return;
  const links=node('div');for(const b of banks.objects)links.append(node('div','md-simple-row'),link(b.title||b.id,href('methods',{id:b.id,...scope,track,standalone:q.get('standalone')})));
  body.append(fold('单独 SkillBank / 原方法证据页',links));
 }else if(family==='self_play'){
  const cat=await api('methods/catalog',{...scope,kind:'selfplay_cycle',limit:100});if(!alive())return;
  const count=controls();for(const[k,v]of Object.entries(spec.counts))if(v)count.append(badge(k+' '+v));body.append(count);
  const list=card();for(const o of cat.objects)list.append(node('div','md-archive-row'),link(o.title||o.id,href('methods',{id:o.id,...scope,track,standalone:q.get('standalone')}),'s-btn'));
  if(!cat.objects.length)list.append(empty('未接入 Self-play 轮次。需要角色/生成题/审核/solver 尝试/训练消费/更新记录；不会用技能快照代替这些证据。'));
  body.append(section('轮次 → 生成题 → 审核 → 解题 → 训练消费'),list);
 }else if(family==='entropy'){
  const cat=await api('methods/catalog',{...scope,kind:'entropy_definition',limit:100});if(!alive())return;
  if(!cat.objects.length){body.append(empty('没有 Entropy 定义和逐决策记录。只有 entropy 标量曲线时，不能推断计算分布、token 区域或探索质量。'));return;}
  const obs=card();body.append(section('先选同一口径，再看观测'),obs);let definition=cat.objects[0].id,context='',offset=0,token=0;
  const defs=select(cat.objects.map(o=>[o.id,o.title||o.id]),definition,id=>{definition=id;offset=0;load();},'熵定义版本');
  const contexts=select([['','全部场景（逐条标明）'],['training_on_policy','训练现场'],['fixed_prefix_probe','固定前缀探针'],['unknown','场景未记录']],context,v=>{context=v;offset=0;load();},'熵记录场景');
  const tools=controls(field('定义 / 实现版本',defs),field('观测场景',contexts));body.insertBefore(tools,obs);
  async function load(){const t=++token;const d=await api('methods/entropy',{...scope,definition,context,offset,limit:50});if(!alive()||dead||t!==token)return;obs.replaceChildren();
   obs.append(button('查看完整熵定义、mask 与使用口径',()=>jsonModal('Entropy definition',d.definition),'s-btn'),node('p','s-muted','不同定义不拼曲线；训练现场与固定前缀探针分开解释。这里只展示记录，不推断探索质量或自主提升。'));
   const tableView=table(['step','场景 / prefix','值与单位','有效 token / 样本','训练使用','原始记录']);
   for(const o of d.observations){const x=o.data;addRow(tableView.body,[known(x.step),x.context+' / '+(x.prefix_hash?.slice(0,12)||'prefix 未记录'),known(x.value)+' '+d.definition.data.unit+(x.value==null?' · '+x.missing_reason:''),known(x.token_count)+' / '+known(x.sample_count),known(x.use),button('展开',()=>jsonModal(o.id,o),'s-link')]);}
   obs.append(tableView.wrap);const prev=button('上一页',()=>{offset=Math.max(0,offset-50);load();},'s-btn'),next=button('下一页',()=>{offset+=50;load();},'s-btn');prev.disabled=offset===0;next.disabled=!d.has_more;obs.append(controls(prev,node('span','',`${d.total} 条已记录观测`),next));if(!d.total)obs.append(empty('口径已登记，但尚无观测数据。'));
  }await load();
 }else{
  const cat=await api('methods/catalog',{...scope,kind:'state_snapshot',limit:50});if(!alive())return;
  const list=card();for(const o of cat.objects)list.append(link(o.title||o.id,href('methods',{id:o.id,...scope,track}),'s-btn'));if(!cat.objects.length)list.append(empty('未接入状态快照或干预记录。'));body.append(list);
 }
 return()=>{dead=true;};
}

export async function mountSkillLibrary(host,ctx){const{api,alive,q,project,track,toast}=ctx;let dead=false,searchToken=0,detailToken=0,timer=null,controller=null;
 const aid=q.get('archive');const a=await api('methods/skill-archive',{id:aid});if(!alive())return;
 ctx.markDemo(a.archive.demo===true);
 const scope=a.archive.scope||{},training=a.snapshots.filter(s=>s.phase!=='evaluation'),frozen=a.snapshots.filter(s=>s.phase==='evaluation');
 let current=a.snapshots.find(s=>s.bank_id===q.get('bank'))||frozen.at(-1)||training.at(-1)||a.snapshots[0];
 let selected=q.get('member')||'',offset=0,group='',query='',onlyChanges=true;const lifeCache=new Map();
 const back=link('← 返回实验列表',href('experiments',{project,track,run:scope.run_id,experiment:scope.experiment_id,standalone:Object.keys(scope).length?'':'1',family:'skill'}),'s-btn');
 if(!ctx.embedded)heading(host,'Skill · 内容与生命周期',a.archive.title||aid,back);
 if(!scope.run_id&&!ctx.embedded)host.append(node('div','s-notice','独立技能归档 · 未绑定 run。这里只展示真实文件，不伪造训练成绩、调用次数或技能收益。'));
 const snapshotStats=[['已存快照',a.snapshots.length,`初始 ${training.filter(s=>s.phase==='initial').length} / 训练 ${training.filter(s=>s.phase==='training').length} / 冻结 ${frozen.length}`],['技能总数（不含教训）',`${a.initial_counts?.skill_count??'—'} → ${a.last_training_counts?.skill_count??'—'}`,'初始库 → 最后训练快照'],['通用技能',`${a.initial_counts?.general_skills??'—'} → ${a.last_training_counts?.general_skills??'—'}`,'与题型技能分开统计']];
 if(ctx.embedded){const strip=node('div','md-snapshot-strip');for(const [label,value,sub] of snapshotStats){const text=node('span','',label+' '+value);text.title=sub;strip.append(text);}host.append(strip);}
 else{const metrics=node('div','md-stat-grid');for(const[label,value,sub]of [...snapshotStats,['逐题使用 / 信用','按记录查看','归档内容与逐次调用分别保存']]){const c=card('md-stat');c.append(node('small','s-muted',label),node('strong','',value),node('small','s-muted',sub));metrics.append(c);}host.append(metrics);}
 const origin=node('div');origin.append(info('原 ZIP SHA256',a.archive_sha256),info('证据覆盖',a.coverage));for(const f of a.imports)origin.append(link('原文件 · '+f.name,'/api/methods/original?id='+encodeURIComponent(f.id),'s-btn'));host.append(fold('来源与未记录的阶段',origin));
 const selectedBar=node('div','md-selected-bar');host.append(selectedBar);
 const grid=node('div','md-grid'),versions=card('md-versions'),list=card('md-list'),detail=card('md-detail');versions.id='md-versions';list.id='md-members';detail.id='md-detail';grid.append(versions,list,detail);host.append(grid);
 versions.append(node('h2','','快照时间线'),node('p','s-muted','step 取自原文件名；不是精确创建时间。'));
 const versionList=node('div');versions.append(versionList);
 const tools=controls();const search=node('input');search.placeholder='搜索全部内容 / ID / 适用条件';search.setAttribute('aria-label','搜索技能全文');search.id='md-search';const category=select([['','所有条目（含经验教训）'],...Object.entries(groups)],group,g=>{group=g;offset=0;loadMembers();},'技能分类');tools.append(search,category);list.append(node('h2','','技能内容'),tools);
 const listBody=node('div'),pager=controls();list.append(listBody,pager);
 function url(){if(ctx.setViewState){ctx.setViewState({archive:aid,bank:current.bank_id,member:selected});return;}history.replaceState(null,'',href('skill-library',{archive:aid,bank:current.bank_id,member:selected,project,track,run:scope.run_id,standalone:Object.keys(scope).length?'':'1'}));}
 function drawVersions(){versionList.replaceChildren();selectedBar.replaceChildren(node('strong','',`当前：${versionLabel(current)}`),badge(`${current.counts.skill_count} 条技能`),badge(`${current.counts.mistake_count} 条经验教训`));
  if(current.phase==='evaluation'){const same=a.snapshots[current.byte_identical_training_index];selectedBar.append(node('span','s-muted',same?`与 ${versionLabel(same)} 的原文件逐字节相同；不是最后训练库。`:'未找到逐字节相同的训练快照。'));}
  for(const s of a.snapshots){const b=button('',()=>chooseVersion(s),'md-version '+(s.bank_id===current.bank_id?'selected':''));b.dataset.bank=s.bank_id;b.append(node('strong','',versionLabel(s)),node('span','',`${s.counts.skill_count} 技能 / ${s.counts.mistake_count} 教训`));if(s.changes)b.append(node('small','s-muted',`新增 ${s.changes.added} · 修改 ${s.changes.modified} · 消失 ${s.changes.removed}`));if(s.phase==='evaluation')b.classList.add('frozen');versionList.append(b);} }
 async function chooseVersion(s){current=s;offset=0;++detailToken;drawVersions();url();await loadMembers();if(selected)await chooseSkill(selected);}
 async function loadMembers(){controller?.abort();controller=new AbortController();const token=++searchToken;const bank=current.bank_id;
  try{const d=await api('methods/skill-members',{bank,q:query,group,offset,limit:25},{signal:controller.signal});if(!alive()||dead||token!==searchToken||bank!==current.bank_id)return;listBody.replaceChildren(node('p','s-muted',`${d.total} 条匹配；服务端分页，不预载全库正文。`));
   for(const m of d.items){const b=button('',()=>chooseSkill(m.member_key,m.pointer),'md-member '+(selected===m.member_key?'selected':''));b.dataset.member=m.member_key;b.append(node('strong','',m.title),node('small','s-muted',`${m.source_id||'ID 未记录'} · ${m.category||groups[m.group_name]}`),node('p','',m.preview));listBody.append(b);}
   const prev=button('上一页',()=>{offset=Math.max(0,offset-25);loadMembers();},'s-btn'),next=button('下一页',()=>{offset+=25;loadMembers();},'s-btn');prev.disabled=offset===0;next.disabled=!d.has_more;pager.replaceChildren(prev,node('span','',`${offset+1}–${offset+d.items.length} / ${d.total}`),next);if(!d.items.length)listBody.append(empty('没有匹配条目。'));
   if(!selected&&d.items.length)await chooseSkill(d.items[0].member_key,d.items[0].pointer);
  }catch(e){if(e.name!=='AbortError'&&alive()&&!dead)toast(e.message);}
 }
 async function chooseSkill(key,pointer){selected=key;const token=++detailToken;const bank=current.bank_id;url();listBody.querySelectorAll('[data-member]').forEach(e=>e.classList.toggle('selected',e.dataset.member===key));detail.replaceChildren(empty('读取此技能内容与版本历史…'));
  try{let lifePromise=lifeCache.get(key);if(!lifePromise){lifePromise=api('methods/skill-lifecycle',{archive:aid,member:key});lifeCache.set(key,lifePromise);if(lifeCache.size>50)lifeCache.delete(lifeCache.keys().next().value);}
   const [item,life]=await Promise.all([api('methods/skill-detail',{bank,member:pointer||key}).catch(e=>({error:e.message})),lifePromise]);if(!alive()||dead||token!==detailToken||bank!==current.bank_id)return;
   detail.replaceChildren();if(item.error){detail.append(node('h2','',key),empty('此技能在当前选中快照不存在。下方仍保留它在其他快照中的历史。'));}else{
    const m=item.member,c=m.content;detail.append(node('h2','',m.title),node('p','s-muted',`${m.source_id||'ID 未记录'} · ${m.category||groups[m.group_name]}`));
    detail.append(info(m.group_name==='common_mistakes'?'经验描述':'原则 / Principle',c.principle??c.description),info(m.group_name==='common_mistakes'?'避免方法':'适用条件 / When to apply',c.when_to_apply??c.how_to_avoid));
    const raw=button('完整原始字段',()=>jsonModal('Skill 原文 · '+m.source_id,c),'s-btn');detail.append(controls(raw,button('版本内容差异',()=>showDiff(key,life),'s-btn'),button('查询已登记使用记录',()=>showUses(bank,m.pointer),'s-btn')));
    detail.append(fold('技能内容哈希与原始位置',node('pre','md-code',`${m.content_hash}\n${current.file}\n${m.pointer}`)));
   }
   if(!life.comparable){detail.append(empty(life.reason));return;}
   const first=life.first_observed;detail.append(section('此技能的已保存历史'),node('p','s-muted',first?`首次观测：${first.phase==='initial'?'初始库':'step '+first.step}。只界定已存快照中的出现位置，不补造创建、审核或使用事件。`:'仅在评测快照中观测到。'));
   const hist=node('div');const showAll=button(onlyChanges?'显示所有快照':'只看出现 / 变化 / 冻结',()=>{onlyChanges=!onlyChanges;renderHistory();},'s-link');detail.append(showAll,hist);
   function renderHistory(){showAll.textContent=onlyChanges?'显示所有快照':'只看出现 / 变化 / 冻结';hist.replaceChildren();const t=table(['快照','观测事件','内容']);for(const x of life.timeline.filter(x=>!onlyChanges||!['unchanged','not_observed'].includes(x.event))){addRow(t.body,[button(versionLabel(x),()=>chooseVersion(a.snapshots.find(s=>s.bank_id===x.bank_id)),'s-link'),eventLabels[x.event],x.content_hash?.slice(0,10)||'—']);}hist.append(t.wrap);}renderHistory();
   detail.append(node('div','s-notice','未记录的创建/审核原因不会显示为“已接受”。库里有技能、检索入选、输入注入、参与训练与提高正确率，是不同阶段。'));
  }catch(e){if(alive()&&!dead&&token===detailToken)detail.replaceChildren(empty(e.message));}
 }
 async function showUses(bank,pointer){const container=modal('技能候选、入选与注入 · 已登记证据'),body=node('div');container.append(body);body.append(empty('读取使用记录…'));
  const current=()=>alive()&&!dead&&body.isConnected&&container.closest('dialog')?.open;
  try{const u=await api('methods/usage',{bank,member:pointer});if(!current())return;body.replaceChildren();if(!u.uses.length){body.append(empty('没有相关使用记录；不是“使用次数为 0”，也不能计算技能成功率。'));return;}
   const stages=node('div','md-usage-stage');for(const [key,label] of [['candidate','候选记录'],['selected','入选记录'],['injection_recorded','注入记录'],['fragment_verified','输入片段已核验']]){const stage=node('div');stage.append(node('span','s-muted',label),node('strong','',known(u.counts[key])));stages.append(stage);}body.append(stages,node('p','s-muted','以上是日志覆盖数。检索命中、最终注入和技能贡献分别判断；未记录的行为采用或信用分配不补推。'));
   for(const x of u.uses){const c=card('md-usage-record');c.append(node('h3','',x.record.title||x.record.id),info('候选 / 入选',`${x.candidate?'是':'否'} / ${x.selected?'是':'否'}`));
    for(const injection of x.injections||[]){const detail=node('div');detail.append(info('最终渲染文本',injection.rendered_text),info('最终输入片段核验',injection.exact_rendered_fragment_visible===true?'片段匹配':injection.exact_rendered_fragment_visible===false?'片段不匹配':'未核验'),button('查看原始输入引用',()=>jsonModal('注入证据',injection),'s-link'));c.append(fold('注入文本与输入证据',detail));}
    if(!x.injections?.length)c.append(node('p','s-muted',x.record.data?.injections_complete===true?'完整注入日志中未登记此技能。':'此技能的注入情况未记录。'));
    c.append(button('查看输入引用与原始调用',()=>jsonModal(x.record.id,x),'s-link'));body.append(c);}
  }catch(e){if(current())body.replaceChildren(empty(e.message));}}
 async function showDiff(key,life){const body=modal('单技能版本对照'),index=a.snapshots.findIndex(s=>s.bank_id===current.bank_id);let baseline=current.phase==='evaluation'?a.snapshots[current.byte_identical_training_index]?.bank_id:a.snapshots[Math.max(0,index-1)]?.bank_id;
  const box=node('div');const s=select(a.snapshots.map(x=>[x.bank_id,versionLabel(x)]),baseline,x=>{baseline=x;load();},'技能对照快照');body.append(field('对照快照 → 当前快照',s),box);let seq=0;
  async function load(){const t=++seq;try{const d=await api('methods/skill-member-diff',{a:baseline,b:current.bank_id,member:key});if(t!==seq)return;box.replaceChildren(node('p','',d.change==='unchanged'?'原始内容完全相同。':d.change),node('pre','md-code',d.diff||'无内容差异。'));}catch(e){box.replaceChildren(empty(e.message));}}await load();}
 search.oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{query=search.value;offset=0;loadMembers();},250);};search.onkeydown=e=>{if(e.key==='Enter'){clearTimeout(timer);query=search.value;offset=0;loadMembers();}};
 const hadSelection=Boolean(selected);drawVersions();await loadMembers();if(hadSelection)await chooseSkill(selected);
 return()=>{dead=true;++searchToken;++detailToken;clearTimeout(timer);controller?.abort();};
}
