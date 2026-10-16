/** One experiment page: shared run controls, lazy training and applicable method tabs. */
import {node,button,link,href,badge,select,field,section,empty,card,controls,jsonModal,profileNotice} from './common.js';


import {syncExecutionState} from './execution-state.js';
import {renderPlan} from './experiments.js';
import {familyLabel,editPresentation} from './workspace.js';
import {entropyPending} from './entropy-discovery.js';
import {selfplayPending} from './selfplay-discovery.js';
import {originLabel,mountSource} from './team.js';
const methodLabels={skill:'技能资产',self_play:'Self-play 轮次与生成题',opd:'OPD 监督与修复',entropy:'熵定义与观测',latent:'Latent 状态与干预'};

export async function mountExperimentPage(host,ctx){
 const {api,project,track,q,alive}=ctx;
 syncExecutionState(q,{}, {initial:true});
 const cat=ctx.experimentEntry?{experiments:[ctx.experimentEntry],projects:ctx.workspace.projects||[],project_id:project,track}:await api('studio/catalog',{project,track});if(!alive())return;
 const explicit=q.get('experiment'),rid=q.get('run');
 let chosen=rid?cat.experiments.find(e=>e.runs.some(r=>r.id===rid)):explicit?cat.experiments.find(e=>e.experiment.id===explicit):cat.experiments[0];
 if(!chosen){
  if(rid||explicit){host.append(empty('当前分区没有此实验 / 运行。未自动替换成其他实验。'),link('返回实验列表',href('experiments',{project,track})));return;}
  return (await import('./dynamics.js')).mountDynamics(host,{...ctx,catalog:cat});
 }
 if(explicit&&chosen.experiment.id!==explicit){host.append(empty('实验与运行归属不一致。没有展示另一实验的结果。'));return;}
 const unbound=q.get('scope')==='experiment';
 const run=unbound?null:chosen.runs.find(r=>r.id===rid)||chosen.runs.at(-1);
 q.set('experiment',chosen.experiment.id);if(run){q.set('run',run.id);q.delete('scope');}else{q.delete('run');q.set('scope','experiment');}
 q.set('project',project);q.set('track',track);q.delete('standalone');
 ctx.markDemo(!!run?.demo);
 const head=node('div','s-heading ex-heading'),title=node('div');
 title.append(node('div','s-eyebrow','EXPERIMENT / '+(track==='reproduction'?'LITERATURE REPRODUCTION':'RESEARCH')),node('h1','',chosen.experiment.name),node('p','s-subtitle',chosen.profile.question||chosen.experiment.objective||'配置、运行结果与本实验的方法记录在同一页面查看。'));
 head.append(title,controls(link('← '+familyLabel(chosen.family||ctx.family),href('experiments',{family:chosen.family||ctx.family}),'s-btn'),button('↻ 刷新',ctx.refresh,'s-btn')));host.append(head);
 let currentView=q.get('view')||q.get('family')||'results';
 const selectors=node('div','s-selector-row ex-selectors');host.append(selectors);
 const switchTo=extra=>{location.hash=href('experiment',{project,track,experiment:chosen.experiment.id,view:currentView,...extra});};
 
 const models=[...new Set(chosen.runs.map(r=>r.base_model||'未登记底座'))];
 if(models.length>1)selectors.append(field('模型',select(models.map(m=>[m,m]),run?.base_model||'未登记底座',m=>{const r=chosen.runs.find(r=>(r.base_model||'未登记底座')===m);switchTo({run:r.id});},'底座模型')));
 const options=chosen.runs.filter(r=>!run||!models.length||(r.base_model||'未登记底座')===(run.base_model||'未登记底座')).map(r=>[r.id,(r.name||r.id)+' · seed '+(r.seed??'未记录')+originLabel(r)]);
 options.push(['','实验级产物（未绑定 run）']);
 selectors.append(field('当前运行',select(options,run?.id||'',id=>switchTo(id?{run:id}:{scope:'experiment'}),'当前运行')));
 const summary=node('div','ex-context');summary.append(badge(track==='reproduction'?'文献复现':track==='main'?'我的实验':'用途未分类',track==='reproduction'?'purple':''),node('span','s-muted',run?`${run.base_model||'底座未记录'} · ${run.status||'运行状态未记录'}`:'只看明确挂在实验上的产物，不汇总各 run'),button('实验档案',async()=>{try{renderPlan(await api('studio/profile',{experiment:chosen.experiment.id}),chosen.experiment);}catch(e){ctx.toast(e.message);}},'s-link'));
 if(chosen.presentation?.is_baseline)summary.firstChild.replaceWith(badge('共同基线','purple'));else summary.prepend(badge(familyLabel(chosen.family||ctx.family)));if(!ctx.session.read_only&&chosen.presentation)summary.append(button('整理分类',()=>editPresentation(ctx,chosen),'s-link'));host.append(summary);
 const overviewPromise=api('methods/experiment-overview',{experiment_id:chosen.experiment.id,run_id:run?.id});
 const sourceHost=node('div'),configHost=node('div');host.append(sourceHost,configHost);
 // Source metadata and method tabs never block the first result request.
 if(run)mountSource(sourceHost,ctx,run.id);
 let overview={profiles:[],families:[]};
 const tabs=node('div','ex-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','当前实验的内容');
 const subtabs=node('div','ex-subtabs');subtabs.setAttribute('role','tablist');subtabs.setAttribute('aria-label','执行评测查看方式');
 const methodLinks=node('div','ex-method-links');configHost.append(methodLinks);
 const pane=node('div','ex-pane');pane.id='ex-pane';pane.setAttribute('role','tabpanel');host.append(tabs,subtabs,pane);
 const views=[{id:'results',title:'结果总览'},{id:'trace',title:'单条轨迹'},{id:'trace-compare',title:'同题对比'},{id:'diagnostics',title:'固定题诊断'},{id:'training',title:'训练评测'}];
 const wanted=q.get('view')||q.get('family');
 currentView=wanted|| (ctx.page==='training'?'training':'results');
 // Saved method links open the same records inside the RL view.
 if(currentView==='opd'){currentView='training';q.set('opd_legacy','1');}
 if(currentView==='entropy'){currentView='training';q.set('entropy_legacy','1');}
 if(currentView==='self_play'||currentView==='selfplay'){currentView='training';q.set('selfplay_legacy','1');}
 let dead=false,epoch=0,cleanup=null;
 const buttons=new Map();
 let lastExecution=['results','trace','trace-compare','diagnostics'].includes(currentView)?currentView:q.get('execution_view')||'results';
 const executionTab=button('Agent 执行评测',()=>show(lastExecution),'ex-tab');executionTab.dataset.section='execution';executionTab.id='ex-tab-execution';executionTab.setAttribute('role','tab');executionTab.setAttribute('aria-controls','ex-pane');tabs.append(executionTab);
 function addTab(v){
  if(buttons.has(v.id))return;
  const b=button(v.title,()=>show(v.id),'ex-tab');b.dataset.view=v.id;b.setAttribute('role','tab');b.id='ex-tab-'+v.id;b.setAttribute('aria-controls','ex-pane');
  if(v.declaredOnly)b.append(node('small','s-muted',' · 待接入'));
  const dest=v.id==='training'?tabs:['results','trace','trace-compare','diagnostics'].includes(v.id)?subtabs:methodLinks;
  if(dest===methodLinks){b.className='s-link ex-method-link';b.removeAttribute('role');b.removeAttribute('aria-controls');}
  dest.append(b);buttons.set(v.id,b);
 }
 for(const v of views)addTab(v);
 for(const list of [tabs,subtabs])list.addEventListener('keydown',e=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
  const items=[...list.querySelectorAll('[role="tab"]')],i=items.indexOf(document.activeElement);if(i<0)return;e.preventDefault();
  const next=e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowLeft'?-1:1)+items.length)%items.length;items[next].focus();items[next].click();
 });
 function updateState(patch){
  if(dead||!alive())return;
  syncExecutionState(q,patch);
  q.set('view',currentView);q.delete('family');q.delete('id');
  history.replaceState(null,'',href('experiment',Object.fromEntries(q)));
 }
 async function show(view){
  const token=++epoch;currentView=view;cleanup?.();cleanup=null;
  const execution=['results','trace','trace-compare','diagnostics'].includes(view);if(execution)lastExecution=view;updateState({view,execution_view:lastExecution});
  executionTab.classList.toggle('active',execution);executionTab.setAttribute('aria-selected',String(execution));executionTab.tabIndex=execution?0:-1;subtabs.hidden=!execution;
  for(const[id,b]of buttons){const selected=id===view||(id==='training'&&!execution);b.classList.toggle('active',selected);if(b.getAttribute('role')==='tab'){b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;}else{b.tabIndex=0;if(id===view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}}
  pane.setAttribute('aria-labelledby','ex-tab-'+view);pane.replaceChildren(empty('读取本实验 / 当前运行的记录…'));
  const body=node('div');pane.replaceChildren(body);
  const methodControllers=[];
  const child={...ctx,catalog:cat,embedded:true,methodsOverview:overview,
   alive:()=>alive()&&!dead&&token===epoch,
   markDemo:v=>ctx.markDemo(!!run?.demo||v),
   setViewState:patch=>{if(token===epoch){updateState(patch);for(const controller of methodControllers)controller?.update?.(patch);}}};
  try{
   let dispose;
   if(view==='results'||view==='diagnostics'){
    dispose=await (await import('./results.js')).mountResults(body,{...child,run,experiment:chosen,diagnosticsOnly:view==='diagnostics'});
   }else if(view==='trace'||view==='trace-compare'){
    dispose=await (await import('./trace-browser.js')).mountTraceBrowser(body,{...child,run,experiment:chosen},{compare:view==='trace-compare'});
   }else if(view==='training'){
    if(!run){
     body.append(empty('请选择上方已有运行，查看它的训练信号与 checkpoint 评测。'));
     const applicable=await overviewPromise;
     if(child.alive()&&applicable?.families.some(f=>f.id==='opd'))dispose=await(await import('./opd-tracking.js')).mountOPDLegacy(body,child,applicable.scope);
     if(child.alive()&&applicable?.families.some(f=>f.id==='entropy')){const previous=dispose,entropy=await import('./entropy-tracking.js');body.append(section('Entropy 的实验级对照'));const compareDispose=await entropy.mountEntropyComparisons(body,child,applicable.scope),legacyDispose=applicable.counts?.entropy_definition?await entropy.mountEntropyLegacy(body,child,applicable.scope):null;dispose=()=>{previous?.();compareDispose?.();legacyDispose?.();};}
     if(child.alive()&&applicable?.families.some(f=>f.id==='self_play')){const previous=dispose,selfplay=await import('./selfplay-tracking.js');body.append(section('Self-play 的实验级记录'));const compareDispose=await selfplay.mountSelfplayComparisons(body,child,applicable.scope),legacyDispose=await selfplay.mountSelfplayLegacy(body,child,applicable.scope);dispose=()=>{previous?.();compareDispose?.();legacyDispose?.();};}
    }else{
    // Groundtruth remains selectable even without a completed evaluation.
    const panel=q.get('panel')||((chosen.latest_evaluation?.run_id||chosen.latest_evaluation?.run?.id)===run.id?chosen.latest_evaluation.panel_id:'');
    if(ctx.session.panels.length)body.append(field('关联评测题单',select([['','不关联题单 · 仅训练记录'],...ctx.session.panels.map(p=>[p.id,p.name+' · '+p.size+' 题'])],panel,id=>{updateState({panel:id});show('training');},'固定评测题单')));
    const training=node('div');body.append(training);dispose=await (await import('./dynamics.js')).mountDynamics(training,child);
    }
   }else dispose=await (await import('./experiment-methods.js')).mountExperimentMethod(body,child,view,overview);
   if(['results','training'].includes(view)&&run){
    // The primary result appears first; method metadata cannot block it.
    let applicable;try{applicable=await overviewPromise;}catch{applicable=null;}
    if(child.alive()){
     const tasks=[];
     for(const family of ['skill','opd','entropy','self_play'])if(applicable?.families.some(f=>f.id===family)){
      const fold=node('details','ex-method-fold'),slot=node('div',({skill:'st',opd:'ot',entropy:'et',self_play:'sp'}[family])+'-experiment-slot');
      fold.append(node('summary','',methodLabels[family]+' · 本实验记录'),slot);body.append(fold);
      let mounted=false;const mountOnce=async()=>{if(mounted||!child.alive())return;mounted=true;try{
       const method=await (family==='skill'?import('./skill-tracking.js'):family==='opd'?import('./opd-tracking.js'):family==='entropy'?import('./entropy-tracking.js'):import('./selfplay-tracking.js'));
       const mount=family==='skill'?(view==='results'?method.mountSkillExecution:method.mountSkillTraining):family==='opd'?(view==='results'?method.mountOPDExecution:method.mountOPDTraining):family==='entropy'?(view==='results'?method.mountEntropyExecution:method.mountEntropyTraining):(view==='results'?method.mountSelfplayExecution:method.mountSelfplayTraining);
       const controller=await mount(slot,child,applicable.scope,{evaluationId:q.get('result_b')||'',requireEvaluation:true});
       if(!child.alive())controller?.();else methodControllers.push(controller);
      }catch(error){if(child.alive()){mounted=false;slot.replaceChildren(empty(error.message),button('重试',mountOnce,'s-btn'));}}};
      fold.addEventListener('toggle',()=>{if(fold.open)mountOnce();});
      const prefixes={skill:['skill_'],opd:['opd_'],entropy:['entropy_','definition'],self_play:['selfplay_']}[family];
      if([...q.keys()].some(k=>prefixes.some(prefix=>k.startsWith(prefix)))){fold.open=true;tasks.push(mountOnce());}
     }
     await Promise.allSettled(tasks);
    }
   }
   // A user-selected research direction is enough to offer an honest setup
   // state, but never to declare method evidence or attach another run's data.
   if(['results','training'].includes(view)&&(chosen.family||ctx.family)==='entropy'){
    let applicable;try{applicable=await overviewPromise;}catch{applicable=null;}
    if(child.alive()&&applicable&&!applicable.families.some(f=>f.id==='entropy'))body.append(entropyPending(child,{view,scope:applicable.scope}));
   }
   if(['results','training'].includes(view)&&(chosen.family||ctx.family)==='self_play'){
    let applicable;try{applicable=await overviewPromise;}catch{applicable=null;}
    if(child.alive()&&applicable&&!applicable.families.some(f=>f.id==='self_play'))body.append(selfplayPending(child,{view,scope:applicable.scope}));
   }
   if(!alive()||dead||token!==epoch){dispose?.();methodControllers.forEach(controller=>controller?.());}else cleanup=()=>{dispose?.();methodControllers.forEach(controller=>controller?.());};
  }catch(e){if(alive()&&!dead&&token===epoch)body.replaceChildren(empty(e.message),button('重试当前内容',()=>show(view),'s-btn'));}
 }
 const methodTask=(async()=>{try{
  overview=await overviewPromise;if(dead||!alive())return;
  if(overview.profiles.length){const details=node('details','ex-config');details.append(node('summary','','方法配置 · '+overview.profiles.map(o=>o.data.method_name).join(' / ')));for(const p of overview.profiles)details.append(button(p.data.method_name,()=>jsonModal('已记录的方法配置',p),'s-link'));configHost.append(details);}
  for(const f of overview.families){
   if(['opd','entropy','self_play'].includes(f.id))continue;
   if(f.id==='skill'&&!['skill_asset','skill_bank','skill_archive'].some(kind=>overview.counts?.[kind]||overview.parent_evidence_counts?.[kind]))continue;
   const v={id:f.id,title:methodLabels[f.id]||f.id,declaredOnly:f.id!=='skill'&&!f.recorded};views.push(v);addTab(v);
  }
  if(!buttons.has(currentView))await show('results');else if(!['results','training','trace','trace-compare','diagnostics'].includes(currentView))await show(currentView);
 }catch(e){if(!dead&&alive()){configHost.append(node('p','s-muted','方法配置暂不可用：'+e.message));if(!['results','training','trace','trace-compare','diagnostics'].includes(currentView))await show('results');}}})();
 const firstTask=['results','training','trace','trace-compare','diagnostics'].includes(currentView)?show(currentView):Promise.resolve();
 await Promise.allSettled([firstTask,methodTask]);
 return()=>{dead=true;++epoch;cleanup?.();};
}
