/** Method-specific panes live inside an explicit experiment/run, never a global family chooser. */
import {node,button,select,field,section,empty,card,controls,jsonModal,table,addRow,link,href} from './chunk-9F75D166F7553CB2.js';
import {mountSkillLibrary} from './chunk-1707009B2C72883F.js';
import {mountMethodAssessment} from './chunk-FE424550A720216B.js';
import {mountSkillAssets} from './chunk-472F685AAE895D77.js';
import {mountMethods} from './chunk-ED54685E602E7D25.js';
const known=v=>v==null?'未记录':typeof v==='object'?JSON.stringify(v):String(v);
const scopeQuery=scope=>({...scope,...(!scope.run_id?{unbound:'1'}:{})});

export async function mountExperimentMethod(host,ctx,family,overview){
 const scope=overview.scope,query=scopeQuery(scope);
 if(family==='entropy')return mountEntropyLegacyRecords(host,ctx,query);
 if(family==='skill'){
  host.append(section('技能资产'),node('p','s-muted','在这里查看准确版本、正文与保存的快照。实际使用在 Agent 执行评测中，训练决定与 SkillCredit 在 RL 训练评测中。'));
  const assets=node('div'),banks=node('div');host.append(assets,banks);
  const tasks=await Promise.allSettled([mountSkillAssets(assets,ctx,scope),mountSkill(banks,ctx,query)]),cleanups=[];
  tasks.forEach((result,index)=>{if(result.status==='fulfilled')cleanups.push(result.value);else if(ctx.alive())(index?banks:assets).replaceChildren(empty(result.reason?.message||'资产暂不可用。'));});
  return()=>cleanups.forEach(dispose=>dispose?.());
 }
 if(['self_play','opd'].includes(family)){
  const summary=node('div'),content=node('div');host.append(summary,content);
  // A missing summary must not hide the already saved archive or cycle viewer.
  const tasks=[mountMethodAssessment(summary,ctx,family,scope)];
  if(family==='self_play')tasks.push(mountSelfPlayHistory(content,ctx,scope));
  const settled=await Promise.allSettled(tasks),cleanups=[];
  settled.forEach((result,index)=>{if(result.status==='fulfilled')cleanups.push(result.value);else if(ctx.alive())(index===0?summary:content).replaceChildren(empty(result.reason?.message||'此部分记录暂不可用。'));});
  return()=>cleanups.forEach(dispose=>dispose?.());
 }
 return mountLegacyMethod(host,ctx,family,scope);
}

export async function mountSelfPlayHistory(host,ctx,scope,options={}){
 if(options.direct)return mountLegacyMethod(host,ctx,'self_play',scope);
 const fold=node('details','ma-fold ma-legacy'),body=node('div');
 fold.append(node('summary','','轮次与完整过程'),body);host.append(fold);
 let started=false,disposed=false,cleanup=null;
 async function load(){
  if(!fold.open||started||disposed)return;
  started=true;body.replaceChildren(empty('读取轮次与完整过程…'));
  try{
   const dispose=await mountLegacyMethod(body,{...ctx,alive:()=>ctx.alive()&&!disposed},'self_play',scope);
   if(disposed||!ctx.alive())dispose?.();else cleanup=dispose;
  }catch(e){if(ctx.alive()&&!disposed)body.replaceChildren(empty(e.message||'轮次记录暂不可用。'));}
 }
 fold.addEventListener('toggle',load);
 // Open a specifically linked cycle or artifact, while keeping the default overview compact.
 fold.open=Boolean(ctx.q.get('selfplay_object')||ctx.q.get('object'));
 if(fold.open)await load();
 return()=>{disposed=true;fold.removeEventListener('toggle',load);cleanup?.();};
}

async function mountLegacyMethod(host,ctx,family,scope){
 const stateKey=family==='self_play'?'selfplay_object':'latent_object';
 const q=new URLSearchParams({...scope,view:family==='self_play'?'selfplay':'artifacts',id:ctx.q.get(stateKey)||ctx.q.get('object')||''});
 const result=await mountMethods(host,{...ctx,q,embedded:true,
  setViewState:state=>ctx.setViewState({[stateKey]:state.object,object:'',subview:''}),
  api:(path,params={},opts={})=>ctx.api(path,{...params,...(path==='methods/catalog'&&!scope.run_id?{unbound:'1'}:{})},opts)});
 return()=>result?.dispose?.();
}

async function mountSkill(host,ctx,scope){
 const {api,alive,q}=ctx;
 const cat=await api('methods/catalog',{...scope,kind:'skill_archive',limit:100});
 if(!alive())return;
 if(!cat.objects.length){
  // Older individual SkillBanks remain readable in-place, without a fake archive.
  const banks=await api('methods/catalog',{...scope,kind:'skill_bank',limit:1});
  if(!alive())return;
  if(banks.total){
   const params=new URLSearchParams({...scope,view:'skills',id:q.get('skill_object')||q.get('object')||''});
   const result=await mountMethods(host,{...ctx,q:params,embedded:true,
    setViewState:state=>ctx.setViewState({skill_object:state.object,object:'',subview:''}),
    api:(path,params={},opts={})=>api(path,{...params,...(path==='methods/catalog'&&scope.unbound?{unbound:'1'}:{})},opts)});
   return()=>result?.dispose?.();
  }
  if(!ctx.methodsOverview?.counts?.skill_asset)host.append(empty('本运行尚未接入外部技能资产。已有配置不代表技能文件已到齐。'));
  if(scope.run_id){
   const parent=await api('methods/catalog',{experiment_id:scope.experiment_id,unbound:'1',kind:'skill_archive',limit:1});
   if(alive()&&parent.total)host.append(node('p','s-muted','本实验另存有未绑定运行的技能归档，可单独查看内容和版本。'),link('查看实验级技能归档 →',href('experiment',{project:ctx.project,track:ctx.track,experiment:scope.experiment_id,scope:'experiment',view:'skill'}),'s-btn'));
  }
  return;
 }
 let selected=q.get('archive')||cat.objects.at(-1).id,disposed=false,epoch=0,cleanup=null;
 if(!cat.objects.some(o=>o.id===selected)){
  host.append(empty('指定归档不在当前实验 / 运行的已加载列表中；没有借用其他运行的数据。请重新选择归档。'));
  selected=cat.objects.at(-1).id;
 }
 const body=node('div','ex-skill-pane');
 if(cat.objects.length>1){
  host.append(field('当前运行的技能归档',select(cat.objects.map(o=>[o.id,o.title||o.id]),selected,id=>{
   selected=id;ctx.setViewState({archive:id,bank:'',member:''});load();
  },'当前运行的技能归档')));
 }
 if(cat.has_more)host.append(node('p','s-muted','当前仅加载前 100 个归档；未加载的归档没有合并或丢弃。'));
 host.append(body);
 async function load(){
  const token=++epoch;cleanup?.();cleanup=null;body.replaceChildren(empty('读取此运行的技能快照…'));
  const pane=node('div');body.replaceChildren(pane);
  const params=new URLSearchParams(ctx.q);params.set('archive',selected);
  try{
   const dispose=await mountSkillLibrary(pane,{...ctx,q:params,embedded:true,alive:()=>alive()&&!disposed&&token===epoch});
   if(!alive()||disposed||token!==epoch)dispose?.();else cleanup=dispose;
  }catch(e){if(alive()&&!disposed&&token===epoch)pane.replaceChildren(empty(e.message));}
 }
 await load();
 return()=>{disposed=true;++epoch;cleanup?.();};
}

export async function mountEntropyLegacyRecords(host,ctx,scope){
 const {api,alive,q}=ctx;let dead=false,epoch=0;
 const cat=await api('methods/catalog',{...scope,kind:'entropy_definition',limit:100});
 if(!alive())return;
 if(!cat.objects.length){host.append(empty('本运行尚无熵定义与观测。不会从其他实验借用定义，也不从一个 entropy 标量猜测计算口径。'));return;}
 if(q.get('definition')&&!cat.objects.some(o=>o.id===q.get('definition'))){host.append(empty('指定熵定义不在当前实验 / 运行。没有自动替换成其他定义。'));return;}
 let definition=q.get('definition')||cat.objects[0].id;
 let context=['training_on_policy','fixed_prefix_probe','unknown'].includes(q.get('context'))?q.get('context'):'';
 let offset=0;
 const definitions=select(cat.objects.map(o=>[o.id,o.title||o.id]),definition,v=>{definition=v;offset=0;load();},'熵定义版本');
 const contexts=select([['','全部场景（逐条标明）'],['training_on_policy','训练现场'],['fixed_prefix_probe','固定前缀探针'],['unknown','场景未记录']],context,v=>{context=v;offset=0;load();},'熵记录场景');
 host.append(section('熵定义与观测'),controls(field('定义 / 实现版本',definitions),field('观测场景',contexts)));
 if(cat.has_more)host.append(node('p','s-muted','定义列表仅展示前 100 个版本；没有合并不同定义。'));
 const box=card('ex-entropy-pane');host.append(box);
 async function load(){
  const token=++epoch;ctx.setViewState({definition,context});box.replaceChildren(empty('读取当前定义的观测…'));
  try{
   const d=await api('methods/entropy',{...scope,definition,context,offset,limit:50});
   if(!alive()||dead||token!==epoch)return;
   box.replaceChildren(button('查看完整熵定义、mask 与使用口径',()=>jsonModal('Entropy definition',d.definition),'s-btn'));
   box.append(node('p','s-muted','不同定义不拼曲线；训练现场与固定前缀探针分开解释。这里只展示记录，不推断探索质量或自主提升。'));
   const t=table(['step','场景 / prefix','值与单位','有效 token / 样本','训练使用','原始记录']);
   for(const o of d.observations){const x=o.data;addRow(t.body,[known(x.step),x.context+' / '+(x.prefix_hash?.slice(0,12)||'prefix 未记录'),known(x.value)+' '+d.definition.data.unit+(x.value==null?' · '+x.missing_reason:''),known(x.token_count)+' / '+known(x.sample_count),known(x.use),button('展开',()=>jsonModal(o.id,o),'s-link')]);}
   box.append(t.wrap);
   const prev=button('上一页',()=>{offset=Math.max(0,offset-50);load();},'s-btn');prev.disabled=offset===0;
   const next=button('下一页',()=>{offset+=50;load();},'s-btn');next.disabled=!d.has_more;
   box.append(controls(prev,node('span','',`${d.total} 条已记录观测`),next));
   if(!d.total)box.append(empty('口径已登记，但尚无观测数据。'));
  }catch(e){if(alive()&&!dead&&token===epoch)box.replaceChildren(empty(e.message));}
 }
 await load();return()=>{dead=true;++epoch;};
}
