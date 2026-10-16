import {node,button,link,href,toast,modal,closeModal,badge,empty,select} from './chunk-9F75D166F7553CB2.js';
import {createAPI} from './chunk-BCE084CA98D5743A.js';
import {icon} from './chunk-3A7934D007C62728.js';
import {renderWorkspaceNav,familyLabel} from './chunk-746879D3AB907C24.js';
import {loadUIFont,uiFontStatus} from './chunk-91ABE232E37B6B2E.js';
void loadUIFont();
const localGet=k=>{try{return localStorage.getItem(k);}catch{return null;}};const localSet=(k,v)=>{try{localStorage.setItem(k,v);}catch{}};
let session,epoch=0,cleanup=null;const root=document.getElementById('s-app');
const client=createAPI();export const api=client.api;
const post=(path,body)=>api(path,{}, {method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':session.csrf_token},body:JSON.stringify(body)});
const nav=[['experiments','实验','layout-grid'],['reports','评测对比','compare'],['versions','固定题单','list']];
async function route(){const token=++epoch;cleanup?.();cleanup=null;closeModal();root.setAttribute('aria-busy','true');root.replaceChildren(empty('正在读取…'));document.body.classList.remove('s-nav-open');
 try{
  const raw=location.hash.slice(2)||'experiments';let[page,query='']=raw.split('?');const aliases={overview:'training',projects:'experiments',trace:'replay',questions:'versions',compare:'reports'};page=aliases[page]||page;const q=new URLSearchParams(query);
  const [loadedSession,workspace]=await Promise.all([api('session',{brief:'1'}),api('studio/workspace')]);if(token!==epoch)return;session=loadedSession;
  document.getElementById('s-version').textContent=session.version;document.getElementById('s-sync').textContent=session.team?.enabled?'中心已连接 · '+session.team.owner+' · '+session.team.role:'本地已连接';document.getElementById('s-open').textContent=session.team?.enabled?'数据接入':'打开数据文件';
  const baselineProject=workspace.baselines[0]?.experiment.project_id;
  let project=q.get('project')||q.get('project_id')||baselineProject||session.projects[0]?.id||'';
  let track=q.get('track')||'main';if(!['main','reproduction','unassigned'].includes(track))track='main';
  const oldProject=q.get('project')||q.get('project_id');
  let family=q.get('family')||(page==='experiments'&&oldProject?workspace.experiments.find(e=>e.experiment.project_id===oldProject)?.family:'')||'all';
  const entryFor=id=>workspace.experiments.find(e=>e.experiment.id===id);
  // A concrete deep link chooses the actual owner's context, never the previously selected project.
  let owner=null;
  if(q.get('run')||q.get('run_id'))owner=session.runs.find(r=>r.id===(q.get('run')||q.get('run_id')));
  if(q.get('snapshot')){const saved=await api('studio/saved-report',{id:q.get('snapshot')});owner=saved.snapshot.b.run;}
  if(q.get('evaluation')||q.get('b')){const ev=await api('evaluation',{id:q.get('evaluation')||q.get('b')});owner=ev.run;}
  if(q.get('trajectory')&&!owner){const tr=await api('trajectory',{id:q.get('trajectory')});const ev=await api('evaluation',{id:tr.evaluation_id});owner=ev.run;q.set('evaluation',ev.id);}
  if(q.get('archive')&&!(['experiment','training'].includes(page)&&(q.get('experiment')||q.get('run')))){const md=await api('methods/summary',{id:q.get('archive')});const sc=md.scope||{};if(sc.run_id)owner=session.runs.find(r=>r.id===sc.run_id);else if(sc.experiment_id){q.set('experiment',sc.experiment_id);const ep=await api('studio/profile',{experiment:sc.experiment_id});project=ep.project_id;track=ep.track;}else if(sc.project_id)project=sc.project_id;else q.set('standalone','1');}
  if(!owner&&(q.get('method')||(page==='methods'&&q.get('id')))){const md=await api('methods/summary',{id:q.get('method')||q.get('id')});const scope=md.scope||{};if(scope.run_id)owner=session.runs.find(r=>r.id===scope.run_id);else if(scope.experiment_id){q.set('experiment',scope.experiment_id);const ep=await api('studio/profile',{experiment:scope.experiment_id});project=ep.project_id;track=ep.track;}else if(scope.project_id)project=scope.project_id;else q.set('standalone','1');}
  if(!owner&&q.get('training_run'))owner=session.runs.find(r=>r.id===q.get('training_run'));
  if(owner){project=owner.project_id;const p=entryFor(owner.experiment_id)?.profile||await api('studio/profile',{experiment:owner.experiment_id});track=p.track;}
  if(!owner&&(q.get('experiment')||q.get('experiment_id'))){const entry=entryFor(q.get('experiment')||q.get('experiment_id'));if(entry){project=entry.experiment.project_id;track=entry.profile.track;}else{const ep=await api('studio/profile',{experiment:q.get('experiment')||q.get('experiment_id')});project=ep.project_id;track=ep.track;}}
  // Old bookmarks resolve into their owning experiment rather than a global family workspace.
  if(!q.has('standalone')&&['method-data','skill-library','methods'].includes(page)&&(owner||q.get('experiment')||q.get('experiment_id'))){
   const legacy=page;if(owner){q.set('run',owner.id);q.set('experiment',owner.experiment_id);}else if(q.get('experiment_id'))q.set('experiment',q.get('experiment_id'));
   if(legacy==='skill-library')q.set('view','skill');else if(legacy==='method-data'&&q.get('family'))q.set('view',q.get('family'));
   else if(legacy==='methods'){const oid=q.get('id');if(oid){const md=await api('methods/summary',{id:oid});q.set('object',oid);const families={skill_bank:'skill',skill_archive:'skill',skill_retrieval:'skill',skill_evolution:'skill',state_snapshot:'latent',entropy_definition:'entropy',entropy_observation:'entropy',entropy_probe:'entropy',entropy_intervention:'entropy',entropy_comparison:'entropy',opd_failure:'opd',opd_teacher:'opd',opd_supervision:'opd'};q.set('view',families[md.kind]||'self_play');if(md.kind==='entropy_definition')q.set('definition',oid);else if(md.kind==='entropy_comparison')q.set('entropy_comparison',oid);else if(['entropy_observation','entropy_probe','entropy_intervention'].includes(md.kind))q.set('entropy_record',oid);}else q.set('view',q.get('view')==='skills'?'skill':q.get('view')==='artifacts'?'latent':'self_play');}
   page='experiment';q.delete('id');q.delete('family');q.delete('project_id');q.delete('experiment_id');q.delete('run_id');
  }else if(page==='method-data'&&!q.has('standalone')){page='experiments';}

  if(!session.projects.some(p=>p.id===project))project=session.projects[0]?.id||'';
  if(token!==epoch)return;localSet('studio_project',project);localSet('studio_track',track);
  const experimentEntry=entryFor(owner?.experiment_id||q.get('experiment')||q.get('experiment_id'));
  if(experimentEntry&&page!=='experiments')family=experimentEntry.family;
  const ps=document.getElementById('s-project');ps.replaceChildren();for(const p of session.projects){const o=node('option','',p.name);o.value=p.id;ps.append(o);}ps.value=project;
  document.querySelector('.s-project-box').hidden=!['reports','versions','team','methods'].includes(page);
  ps.onchange=()=>{const e=workspace.experiments.find(e=>e.experiment.project_id===ps.value);location.hash=href(page,{project:ps.value,track:e?.profile.track||track});};
  const name=page==='experiments'?(family==='all'?'全部实验':familyLabel(family)):experimentEntry?familyLabel(experimentEntry.family):session.projects.find(p=>p.id===project)?.name||'工作区';
  document.getElementById('s-crumb').textContent=`${name}　/　${page==='team'?'数据接入':page==='experiment'?'实验详情':page==='experiments'?'总览':nav.find(x=>x[0]===page)?.[1]||'轨迹与方法证据'}`;
  document.getElementById('s-demo').hidden=true;
  const ctx={page,q,session,workspace,experimentEntry,family,project,track,api,post,toast,alive:()=>token===epoch,refresh:()=>{client.clear();return route();},markDemo:(v)=>{document.getElementById('s-demo').hidden=!v;}};
  renderWorkspaceNav(document.getElementById('s-nav'),ctx);
  root.replaceChildren();document.body.dataset.page=page;let dispose=null;
  if(page==='team'&&session.team?.enabled)dispose=await (await import('./chunk-2DAD1F7FD8475F6D.js')).mountTeam(root,ctx);
  else if(page==='experiments')dispose=await (await import('./chunk-746879D3AB907C24.js')).mountWorkspace(root,ctx);
  else if(page==='training'||page==='experiment')dispose=await (await import('./chunk-7C82BCDD5CEDA37E.js')).mountExperimentPage(root,ctx);
  else if(page==='reports')dispose=await (await import('./chunk-992DFF27AD104B44.js')).mountReports(root,ctx);
  else if(page==='versions')dispose=await (await import('./chunk-6BD55706D6B62309.js')).mountVersions(root,ctx);
  else if(page==='method-data')dispose=await (await import('./chunk-1707009B2C72883F.js')).mountMethodData(root,ctx);
  else if(page==='skill-library')dispose=await (await import('./chunk-1707009B2C72883F.js')).mountSkillLibrary(root,ctx);
  else if(page==='replay')dispose=await (await import('./chunk-F1258780DD675401.js')).mountReplay(root,ctx);
  else if(page==='methods'){
   // Method screens retain their existing scope UI; enforce current track through this API adapter.
   if(!q.has('project_id')&&project&&!q.has('standalone'))q.set('project_id',project);
   const scopedAPI=async(path,params={},opts={})=>{const d=await api(path,params,opts);if(path==='catalog'){
    const cat=await api('studio/catalog',{project:params.project,track});const ids=new Set(cat.experiments.map(x=>x.experiment.id));d.experiments=d.experiments.filter(e=>ids.has(e.id));d.runs=d.runs.filter(r=>ids.has(r.experiment_id));}
    if(path==='methods/catalog'&&!params.experiment_id&&!params.run_id&&params.project_id){const cat=await api('studio/catalog',{project:params.project_id,track});const ids=new Set(cat.experiments.map(x=>x.experiment.id));d.objects=d.objects.filter(o=>ids.has(o.scope?.experiment_id));d.counts={};for(const o of d.objects)d.counts[o.kind]=(d.counts[o.kind]||0)+1;d.all_scope_count=d.objects.length;}return d;};
   root.append(link('← 返回训练动态',href('training',{project,track,run:q.get('run_id')})));const h=node('div');root.append(h);await (await import('./chunk-ED54685E602E7D25.js')).mountMethods(h,{q,session,api:scopedAPI,toast});
  }else location.hash=href('experiments',{project,track});
  if(token!==epoch)dispose?.();else{cleanup=dispose;root.setAttribute('aria-busy','false');}
 }catch(e){if(token===epoch){root.setAttribute('aria-busy','false');root.replaceChildren(empty(e.message),button('重新加载',()=>{client.clear();route();},'s-btn'));document.getElementById('s-sync').textContent='读取失败';console.error(e);}}
}
async function openFile(file){if(session?.team?.enabled){location.hash=href('team');toast('中心请在数据接入页提交完整 ZIP；不直接改写原始结果。');return;}if(!file)return;if(file.size>50*1024*1024)return toast('文件超过 50 MiB，请拆分后打开。');try{
 const zip=file.name.toLowerCase().endsWith('.zip');
 const text=zip?'':await file.text();let parsed=null;try{parsed=JSON.parse(text);}catch{}
 const bank=zip||parsed&&('general_skills'in parsed||'query_type_skills'in parsed||'task_specific_skills'in parsed||parsed.method_schema);
 const endpoint=bank?'methods':'inspection';const r=await fetch('/api/'+endpoint+'/upload?'+new URLSearchParams({name:file.name}),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Local-Token':session.csrf_token},body:file});const d=await r.json();if(!r.ok)throw Error(d.error);client.clear();
 if(d.archive_id)location.hash=href('skill-library',{archive:d.archive_id,standalone:'1'});
 else if(bank)location.hash=href('methods',{id:d.snapshot_id||d.object_ids?.[0],standalone:'1'});else location.hash=href('replay',{file:d.id});
 }catch(e){toast(e.message);}}
document.getElementById('s-open').onclick=()=>{if(session?.team?.enabled)location.hash=href('team');else document.getElementById('s-file').click();};document.getElementById('s-file').onchange=e=>{openFile(e.target.files[0]);e.target.value='';};document.getElementById('s-modal-close').onclick=closeModal;
window.addEventListener('hashchange',route);window.addEventListener('dragover',e=>{e.preventDefault();});window.addEventListener('drop',e=>{e.preventDefault();if(e.dataTransfer.files[0])openFile(e.dataTransfer.files[0]);});route();

// Workspace utilities are available on every page without extra navigation entries.
document.getElementById('s-refresh').onclick=()=>{client.clear();route();};
const closeNav=()=>{document.body.classList.remove('s-nav-open');document.getElementById('s-menu').setAttribute('aria-expanded','false');};
document.getElementById('s-menu').onclick=()=>{if(innerWidth<800){document.body.classList.toggle('s-nav-open');document.getElementById('s-menu').setAttribute('aria-expanded',String(document.body.classList.contains('s-nav-open')));}else document.body.classList.toggle('s-nav-collapsed');};
document.getElementById('s-closemenu').onclick=closeNav;document.getElementById('s-nav-backdrop').onclick=closeNav;
document.getElementById('s-help').onclick=()=>{const b=modal('使用帮助');
 const fontRow=node('div','s-detail-pair');fontRow.append(node('strong','','界面字体'),node('span','',uiFontStatus().label));b.append(fontRow);
 const rows=[['从基线开始','原生 Qwen 固定置顶；按研究方向查看自己的实验与文献复现。打开实验后先看基线对照，再看逐题轨迹。'],['两种评测视角','Agent 执行评测查看任务结果、执行条件与轨迹；RL 训练评测查看实际训练信号、checkpoint 变化与训练采样。'],['对比两次评测','固定模型比较 Harness，或固定 Harness 比较 Checkpoint；未记录的条件保留为待核对。'],['/','聚焦当前页面的搜索框。'],['← / →','在轨迹页切换步骤。'],['Esc','关闭弹窗。'],['刷新','重新读取全部缓存，不修改任何训练或评测结果。'],['数据接入','沿用原来的 CLI、SDK 与 team-sync；打开普通 JSONL 仅供检查，不会自动登记成正式评测。']];
 for(const [label,text]of rows){const row=node('div','s-detail-pair');row.append(node('strong','',label),node('span','',text));b.append(row);}
 b.append(node('p','s-muted','开源复用来源和许可证见交付包 THIRD_PARTY_NOTICES.md。'));
};
document.addEventListener('keydown',e=>{if(e.target.closest('input,textarea,select,[contenteditable],dialog'))return;if(e.key==='/'){const input=root.querySelector('input[type="search"],input[aria-label*="搜索"]');if(input){e.preventDefault();input.focus();}}if(e.key==='?'){e.preventDefault();document.getElementById('s-help').click();}});

document.querySelector('.s-skip').onclick=e=>{e.preventDefault();root.focus();};
