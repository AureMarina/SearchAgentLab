import {node,button,short,tag,empty,saveJSON} from './dom.js';
import {JsonTree} from './json-tree.js';
import {TraceTree} from './trace-tree.js';
import {DiagnosisPanel} from './diagnosis-panel.js';
import {LoopPanel} from './loop-panel.js';
import {MethodEvidencePanel} from '../methods/evidence-panel.js';

const expandMemory=new Map();
const outcomes={correct:'正确',incorrect:'错误',unscored:'未评分',invalid:'JSON 异常'};

/** One working page for standalone JSONL or an already registered evaluation. */
export async function mountInspector(host,{q,session,api,toast,onEvaluation=()=>{}}){
  const alive=()=>shell.isConnected;
  const shell=node('div','ix-workbench');host.replaceChildren(shell);
  let methodId=q.get('method')||'',methodPanel=null;
  let fileId=q.get('file')||'',evalId=q.get('evaluation')||'',ordinal=Number(q.get('record')||0),trajectoryId=q.get('trajectory')||'';
  let rows=[],offset=ordinal>0?Math.floor((ordinal-1)/50)*50:0,total=0,current=null,mode='trace',tree=null,diagnosis=null,listToken=0,docToken=0,query='',search='',hits=[],hitIndex=0;
  let outcome=q.get('outcome')||'',rule='',dataset=q.get('dataset')||'';
  let files=[],evaluation=null,rules={},loop=null,sectionMode='trace';
  const header=node('div','ix-page-heading');
  const intro=node('div');intro.append(node('div','eyebrow','TRAJECTORY WORKBENCH'),node('h1','','轨迹检查'),node('p','subtitle','执行证据 → 分层检查 → 归因候选 → 最小验证与修复回归。'));
  header.append(intro);const actions=node('div','ix-heading-actions');
  const upload=node('input');upload.type='file';upload.accept='.jsonl,.ndjson,.json';upload.id='ix-file-input';upload.hidden=true;
  const openBtn=button('＋ 打开 JSONL / JSON',()=>upload.click(),'button primary');openBtn.id='ix-open-file';actions.append(openBtn,upload);const ml=node('a','button','方法产物 ↗');ml.href='#/methods';actions.append(ml);
  const gt=node('a','button','固定题目 ↗');gt.href='#/questions'+(session.panels[0]?'?panel='+encodeURIComponent(session.panels[0].id):'');actions.append(gt);header.append(actions);shell.append(header);
  const toolbar=node('div','card ix-source-toolbar');const fileLabel=node('label','ix-source-label','检查来源');const fileSelect=node('select');fileSelect.id='ix-file-select';fileSelect.setAttribute('aria-label','检查来源');fileLabel.append(fileSelect);toolbar.append(fileLabel);
  const sourceInfo=node('span','ix-source-info');toolbar.append(sourceInfo);const originLink=node('a','button small','导出原文件');toolbar.append(originLink);shell.append(toolbar);
  const sourceNotice=node('div','ix-source-notice');shell.append(sourceNotice);
  const grid=node('div','ix-grid');shell.append(grid);
  const left=node('aside','card ix-record-panel');grid.append(left);
  const listHead=node('div','ix-list-head');listHead.append(node('h2','','记录列表'));const listCount=node('span','ix-small');listHead.append(listCount);
  const listSearch=node('input');listSearch.placeholder='搜索整个文件（含折叠字段）';listSearch.id='ix-list-search';listSearch.setAttribute('aria-label','搜索整个文件');listHead.append(listSearch);
  const filters=node('div','ix-list-filters');const outcomeSelect=node('select');outcomeSelect.id='ix-outcome';outcomeSelect.setAttribute('aria-label','筛选评分');
  for(const [val,label] of [['','全部结果'],...Object.entries(outcomes)]){const o=node('option','',label);o.value=val;outcomeSelect.append(o);}outcomeSelect.value=outcome;filters.append(outcomeSelect);
  const ruleSelect=node('select');ruleSelect.id='ix-rule-filter';ruleSelect.setAttribute('aria-label','筛选诊断规则');filters.append(ruleSelect);listHead.append(filters);left.append(listHead);
  const recordList=node('div','ix-record-list');recordList.id='ix-record-list';left.append(recordList);
  const listFoot=node('div','ix-list-foot');const previous=button('‹ 上一页',()=>{offset=Math.max(0,offset-50);loadList();});const next=button('下一页 ›',()=>{offset+=50;loadList();});listFoot.append(previous,next);left.append(listFoot);
  const center=node('section','card ix-center');grid.append(center);
  const recordHead=node('div','ix-record-heading');center.append(recordHead);
  const workflowTabs=node('div','lp-workflow-tabs');center.append(workflowTabs);
  const workflowButtons={};for(const [key,label] of [['trace','执行轨迹'],['checks','分层检查'],['hypotheses','归因候选'],['verification','验证与修复'],['methods','方法证据']]){const b=button(label,()=>switchSection(key),'lp-workflow-tab'+(key==='trace'?' selected':''));b.id='lp-tab-'+key;workflowButtons[key]=b;workflowTabs.append(b);}
  const tabs=node('div','ix-tabs');const traceTab=button('分层轨迹',()=>switchMode('trace'),'ix-tab selected');traceTab.id='ix-tab-trace';const jsonTab=button('原始 JSON',()=>switchMode('json'),'ix-tab');jsonTab.id='ix-tab-json';tabs.append(traceTab,jsonTab);center.append(tabs);
  const controls=node('div','ix-tree-controls');const collapse=button('全部折叠',()=>tree?.collapseAll(),'button ghost small');collapse.id='ix-collapse';
  const expand=button('展开步骤',()=>{if(mode==='trace')tree?.expandSteps();else{const r=tree?.expandAll();if(r?.limited)toast('为避免浏览器卡死，单次展开限制 2500 个节点；可在数组底部继续展开。');}},'button ghost small');expand.id='ix-expand';controls.append(collapse,expand);
  const firstSignal=button('定位步骤信号',()=>{const f=current?.report.findings.find(x=>x.step_index!=null&&x.evidence.length);if(f)revealPointer(f.evidence[0].pointer);else toast('没有可定位到步骤的规则信号；可选字段人工标注。');},'button ghost small');firstSignal.title='定位规则命中的步骤，不代表已证明最早因果错误';controls.append(firstSignal);
  const copyPointer=button('复制证据路径',async()=>{if(!selectedPointer)return toast('请点击某字段“定位”，或点击诊断中的证据链接。');try{await navigator.clipboard.writeText(selectedPointer);toast('已复制 JSON pointer');}catch{toast('证据路径：'+selectedPointer);}},'button ghost small');controls.append(copyPointer);center.append(controls);
  const replay=node('div','ix-replay-bar');replay.append(node('span','ix-small','步骤回放'));
  const replaySelect=node('select');replaySelect.setAttribute('aria-label','选择已记录步骤');
  const prevStep=button('←',()=>moveStep(-1),'button ghost small');prevStep.setAttribute('aria-label','上一步');
  const nextStep=button('→',()=>moveStep(1),'button ghost small');nextStep.setAttribute('aria-label','下一步');
  replay.append(prevStep,replaySelect,nextStep);center.append(replay);
  replaySelect.onchange=()=>{mode='trace';renderTree();revealPointer(replaySelect.value);};
  function moveStep(delta){if(!replaySelect.options.length)return;replaySelect.selectedIndex=Math.max(0,Math.min(replaySelect.options.length-1,replaySelect.selectedIndex+delta));replaySelect.onchange();}
  const searchBar=node('div','ix-search-bar');const recordSearch=node('input');recordSearch.placeholder='搜索当前记录：输入关键词后按 Enter';recordSearch.id='ix-record-search';recordSearch.setAttribute('aria-label','搜索当前记录');const searchBtn=button('查找',()=>findMatches(),'button small');const prevHit=button('↑',()=>goHit(-1),'button small');const nextHit=button('↓',()=>goHit(1),'button small');nextHit.setAttribute('aria-label','下一个命中');prevHit.setAttribute('aria-label','上一个命中');const hitCount=node('span','ix-small','');searchBar.append(recordSearch,searchBtn,prevHit,nextHit,hitCount);center.append(searchBar);
  const matches=node('div','ix-match-results');center.append(matches);
  const pathBar=node('div','ix-path-bar','证据路径：未选择');center.append(pathBar);let selectedPointer='';
  const treeHost=node('div','ix-tree-scroll');treeHost.id='ix-tree';center.append(treeHost);
  const loopHost=node('div','lp-workflow-body');loopHost.id='lp-body';loopHost.hidden=true;center.append(loopHost);
  const methodHost=node('div');methodHost.id='mx-inspector-panel';methodHost.hidden=true;center.append(methodHost);
  const right=node('aside','card ix-diagnosis');right.id='ix-diagnosis';grid.append(right);

  function switchSection(key){sectionMode=key;for(const [k,b] of Object.entries(workflowButtons))b.classList.toggle('selected',k===key);
    for(const el of [tabs,controls,replay,searchBar,matches,pathBar,treeHost])el.hidden=key!=='trace';
    if(key==='trace')replay.hidden=!replaySelect.options.length;
    methodHost.hidden=key!=='methods';
    loopHost.hidden=key==='trace'||key==='methods';if(key!=='trace'&&key!=='methods'){if(loop)loop.select(key);else empty(loopHost,'请先打开一条真实轨迹。');}
  }
  function sourceParams(){return methodId?{method:methodId}:evalId?{trajectory:trajectoryId}:{file:fileId,record:ordinal};}
  function rememberURL(){const params=methodId?{method:methodId}:evalId?{evaluation:evalId,...(trajectoryId?{trajectory:trajectoryId}:{})}:{file:fileId,...(ordinal?{record:ordinal}:{})};history.replaceState(null,'','#/trace'+(Object.values(params).some(Boolean)?'?'+new URLSearchParams(params):''));}
  async function refreshFiles(){const res=await api('inspection/files');if(!alive())return;files=res.files;fileSelect.replaceChildren();
    if(methodId){const o=node('option','','方法轨迹 / 产物 · '+methodId);o.value='method';fileSelect.append(o);}
    if(evalId){const o=node('option','',`正式评测 · ${evaluation?.checkpoint?.name||evalId}`);o.value='evaluation';fileSelect.append(o);}
    const placeholder=node('option','','请选择已打开的 JSONL 文件');placeholder.value='';fileSelect.append(placeholder);
    for(const f of files){const o=node('option','',f.name+' · '+f.record_count+' 条 · '+f.sha256.slice(0,7));o.value=f.id;fileSelect.append(o);}
    fileSelect.value=methodId?'method':evalId?'evaluation':fileId;
  }
  async function openFile(file){
    if(!file)return;if(file.size>50*1024*1024)return toast('文件超过 50 MiB，请先拆分。');
    openBtn.disabled=true;sourceNotice.textContent='正在读取与建立索引；原始文件和每行行号会保留…';
    try{
      const url='/api/inspection/upload?'+new URLSearchParams({name:file.name,format:file.name.toLowerCase().endsWith('.json')?'json':'jsonl'});
      const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Local-Token':session.csrf_token},body:file});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'导入失败');if(!alive())return;
      methodId='';fileId=data.id;evalId='';evaluation=null;ordinal=0;trajectoryId='';query='';listSearch.value='';outcome='';outcomeSelect.value='';rule='';offset=0;
      await refreshFiles();await loadList(true);toast(data.already_present?'已打开原有归档；原始内容与复核历史保留。':`已打开 ${data.record_count} 条记录；${data.invalid_count} 条 JSON 异常均保留。`);
    }catch(e){toast(e.message);sourceNotice.textContent=e.message;}finally{openBtn.disabled=false;upload.value='';}
  }
  upload.onchange=()=>openFile(upload.files[0]);
  shell.addEventListener('dragover',e=>{e.preventDefault();shell.classList.add('ix-dragging');});shell.addEventListener('dragleave',()=>shell.classList.remove('ix-dragging'));
  shell.addEventListener('drop',e=>{e.preventDefault();shell.classList.remove('ix-dragging');openFile(e.dataTransfer.files[0]);});
  fileSelect.onchange=async()=>{if(fileSelect.value==='evaluation'||fileSelect.value==='method')return;methodId='';fileId=fileSelect.value;evalId='';evaluation=null;ordinal=0;trajectoryId='';offset=0;rule='';await refreshFiles();await loadList(true);};
  let debounce;listSearch.oninput=()=>{clearTimeout(debounce);debounce=setTimeout(()=>{query=listSearch.value;offset=0;loadList();},300);};
  outcomeSelect.onchange=()=>{outcome=outcomeSelect.value;offset=0;loadList();};ruleSelect.onchange=()=>{rule=ruleSelect.value;offset=0;loadList();};
  async function loadList(selectFirst=false){
    const token=++listToken;
    if(methodId){
      const doc=await api('inspection/record',{method:methodId});if(!alive()||token!==listToken)return;
      current=doc;recordList.replaceChildren(node('div','ix-empty','当前为显式关联的方法轨迹/产物。通过“方法证据”查看出题、审核、技能与训练来源。'));
      const back=node('a','button','返回方法工作区 ↗');back.href='#/methods?id='+encodeURIComponent(methodId);recordList.append(back);
      listCount.textContent='1 个明确来源';sourceInfo.textContent='方法对象 · '+methodId;sourceNotice.textContent='本记录属于方法证据，不自动计入固定 validation 成绩。原始输入和未记录字段不补造。';
      ruleSelect.hidden=true;previous.disabled=true;next.disabled=true;originLink.href='/api/methods/export?id='+encodeURIComponent(methodId);originLink.textContent='导出方法证据';originLink.hidden=false;
      rememberURL();renderRecord();return;
    }
    if(!fileId&&!evalId){empty(recordList,'打开一个文件，即可按行检查。不需要先创建实验或伪造 checkpoint。');empty(treeHost,'拖入 JSONL / JSON，或点击右上角“打开 JSONL / JSON”。');empty(right,'诊断在真实记录载入后显示。');recordHead.replaceChildren(node('h2','','从一条实际记录开始'));listCount.textContent='0 条';sourceInfo.textContent='独立检查区';sourceNotice.textContent='检查文件不会进入训练数据，也不会变成某个项目的正式评测成绩。';originLink.hidden=true;previous.disabled=true;next.disabled=true;rememberURL();return;}
    empty(recordList,'正在读取记录…');
    try{
      let data;
      if(evalId){
        outcomeSelect.querySelector('option[value="invalid"]').disabled=true;
        data=await api('trajectories',{evaluation:evalId,dataset,outcome,q:query,offset,limit:50});
        if(!alive()||token!==listToken)return;
        rows=data.items.map((r,i)=>({...r,ordinal:i+offset+1,question:r.question,sample_id:r.sample_id,score_value:r.scores?.em,score_label:r.scores?.em!=null?'EM':null,outcome:r.scores?.em===1?'correct':r.scores?.em===0?'incorrect':'unscored',step_count:r.step_count,source_line:null}));
        total=data.total;sourceInfo.textContent=`${evaluation?.panel?.name||''} · ${evalId}`;sourceNotice.textContent='已登记的独立评测；展开与诊断不会改写原始 EM、reward 或 Groundtruth。';listSearch.placeholder='搜索本次评测题目 / ID';
        originLink.href='/api/export/evaluation?id='+encodeURIComponent(evalId);originLink.textContent='导出评测原证据';originLink.hidden=false;
        ruleSelect.hidden=true;
      }else{
        outcomeSelect.querySelector('option[value="invalid"]').disabled=false;
        data=await api('inspection/records',{file:fileId,q:query,outcome,rule,offset,limit:50});if(!alive()||token!==listToken)return;
        rows=data.items;total=data.total;
        sourceInfo.textContent=`${data.file.record_count} 条 · ${data.file.invalid_count} 条异常 · SHA256 ${data.file.sha256.slice(0,12)}`;
        sourceNotice.textContent='独立文件检查，不是正式评测。源行号、未知字段、异常行和原始字节全部保留；不把参考检索当成模型轨迹。';
        originLink.href='/api/inspection/original?file='+encodeURIComponent(fileId);originLink.textContent='导出原文件';originLink.hidden=false;listSearch.placeholder='搜索整个文件（含折叠字段）';
        ruleSelect.hidden=false;ruleSelect.replaceChildren();const o=node('option','','全部诊断');o.value='';ruleSelect.append(o);
        for(const [id,n] of Object.entries(data.rules)){const o=node('option','',(rules[id]||id)+` (${n})`);o.value=id;ruleSelect.append(o);}ruleSelect.value=rule;
      }
      listCount.textContent=`${total} 条 · ${offset+1>total?0:offset+1}–${Math.min(offset+rows.length,total)}`;previous.disabled=offset===0;next.disabled=offset+rows.length>=total;recordList.replaceChildren();
      for(const row of rows){const b=button('',()=>selectRow(row),'ix-record-row');b.dataset.record=String(row.ordinal);if(row.id)b.dataset.trajectory=row.id;
        const top=node('div','ix-row-meta');top.append(node('span','',row.source_line!=null?'LINE '+row.source_line:row.source_pointer||'# '+row.ordinal),tag(outcomes[row.outcome]||row.outcome,row.outcome));b.append(top);
        b.append(node('strong','ix-row-question',short(row.question||row.sample_id||(row.error?'JSON 解析异常':'未记录问题'),160)));
        const foot=node('div','ix-row-foot');foot.append(node('span','',short(row.dataset||row.adapter||'',25)),node('span','',(row.step_count??'—')+' 步'));
        if(row.candidate_count)foot.append(tag(row.candidate_count+' 个信号','candidate'));b.append(foot);
        if(row.execution_kind==='reference_only')b.append(tag('仅参考材料','reference'));recordList.append(b);}
      if(!rows.length){empty(recordList,'没有匹配记录；过滤只影响列表，不删除原文件。');return;}
      const selected=evalId?rows.find(r=>r.id===trajectoryId):rows.find(r=>r.ordinal===ordinal);
      if(selectFirst||!current||!selected)await selectRow(selected||rows[0]);else updateSelected();
    }catch(e){empty(recordList,e.message);toast(e.message);}
  }
  function updateSelected(){recordList.querySelectorAll('.ix-record-row').forEach(b=>b.classList.toggle('selected',evalId?b.dataset.trajectory===trajectoryId:Number(b.dataset.record)===ordinal));}
  async function selectRow(row){
    const token=++docToken;loop?.dispose();loop=null;ordinal=row.ordinal;trajectoryId=evalId?row.id:'';updateSelected();rememberURL();
    empty(treeHost,'正在读取完整原始记录…');
    try{const doc=await api('inspection/record',sourceParams());if(!alive()||token!==docToken)return;current=doc;selectedPointer='';pathBar.textContent='证据路径：未选择';search='';recordSearch.value='';hits=[];matches.replaceChildren();hitCount.textContent='';
      renderRecord();}catch(e){empty(treeHost,e.message);toast(e.message);}
  }
  function renderRecord(){
    const doc=current,v=doc.view;recordHead.replaceChildren();
    const top=node('div','ix-record-context');top.append(tag(v.adapter),node('span','ix-small',doc.source.line?'原文件第 '+doc.source.line+' 行':'记录 '+(doc.source.trajectory_id||ordinal)),tag(v.execution_kind==='reference_only'?'参考材料':'原始日志'));
    if(doc.demo===true||doc.raw?.demo===true||doc.raw?.data?.demo===true)top.append(tag('合成样例 · 非真实成绩','reference'));
    recordHead.append(top,node('h2','',short(v.question||v.sample_id||'当前记录未保存问题文本',500)));
    const result=node('div','ix-record-result');
    const score=typeof v.scores.em==='number' && [0,1].includes(v.scores.em)?v.scores.em:null;
    result.append(tag(score==null?'EM 未记录':'EM = '+score,score===0?'incorrect':score===1?'correct':''));
    if(v.reward!=null)result.append(tag('reward = '+short(v.reward,25)));
    result.append(node('span','',v.parsed_answer!=null?'解析答案：'+short(v.parsed_answer,100):'解析答案：未记录'));
    recordHead.append(result);
    for(const w of v.warnings)recordHead.append(node('p','ix-adapter-notice',w));
    if(doc.parse_error)recordHead.append(node('p','ix-adapter-notice error','原始行保留：'+doc.parse_error));
    methodPanel?.dispose();methodPanel=new MethodEvidencePanel(methodHost,{api,session,toast,params:sourceParams()});methodPanel.load();
    loop?.dispose();loop=new LoopPanel(loopHost,doc,{api,session,params:sourceParams(),toast,
      reveal:p=>revealPointer(p),getPointer:()=>selectedPointer,onSection:switchSection});loop.load();
    diagnosis=new DiagnosisPanel(right,doc,{onHypothesis:f=>loop?.fromRule(f),reveal:p=>{recordSearch.value='';matches.replaceChildren();hitCount.textContent='';hits=[];revealPointer(p);},
      save:async review=>{try{const r=await api('inspection/reviews',{}, {method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':session.csrf_token},body:JSON.stringify({...sourceParams(),review})});toast('复核已追加保存；原始记录与得分未改变。');return r;}catch(e){toast(e.message);throw e;}},
      onExport:()=>{const a=node('a');a.href='/api/inspection/export?'+new URLSearchParams(sourceParams());a.click();}});
    replaySelect.replaceChildren();
    for(const t of v.trajectories)for(const step of t.steps){const o=node('option','',`${t.name} / ${t.kind==='messages'?'Message':'Step'} ${step.index} · ${step.title}`);o.value=step.pointer;replaySelect.append(o);}
    replay.hidden=!replaySelect.options.length;
    if(doc.parse_error||!v.trajectories.length)mode='json';renderTree();switchSection(sectionMode);if(q.has('pointer')){const p=q.get('pointer');q.delete('pointer');revealPointer(p);}
  }
  function recordStates(){if(!expandMemory.has(current.target)){if(expandMemory.size>25)expandMemory.delete(expandMemory.keys().next().value);expandMemory.set(current.target,new Map());}return expandMemory.get(current.target);}
  function choosePointer(p,manual=true){selectedPointer=p;pathBar.textContent='证据路径：'+(p||'$');if(manual)diagnosis?.select(p,'');}
  function renderTree(){if(!current)return;
    traceTab.classList.toggle('selected',mode==='trace');jsonTab.classList.toggle('selected',mode==='json');expand.textContent=mode==='trace'?'展开步骤':'展开 JSON';
    if(current.parse_error){tree=null;treeHost.replaceChildren(node('pre','ix-invalid-line',current.raw_text));return;}
    tree=mode==='trace'?new TraceTree(treeHost,current,{onSelect:choosePointer,states:recordStates()}):new JsonTree(treeHost,current.raw,{label:'$ · 原始记录',open:true,onSelect:choosePointer,state:recordStates()});
  }
  function switchMode(m){mode=m;renderTree();}
  function revealPointer(pointer,needle=''){
    if(!current)return;
    switchSection('trace');
    // Auto-expand the exact step/field. Unknown fields fall back to the raw tree.
    if(!tree?.reveal(pointer,needle)){mode='json';renderTree();tree?.reveal(pointer,needle);}
    selectedPointer=pointer;pathBar.textContent='证据路径：'+(pointer||'$');
  }
  async function findMatches(){if(!current)return;const token=docToken;search=recordSearch.value.trim();
    try{const result=await api('inspection/search',{...sourceParams(),q:search});if(!alive()||token!==docToken)return;
      hits=result.items;hitIndex=0;matches.replaceChildren();hitCount.textContent=result.total?`${result.total} 个字段${result.truncated?'（前 200 个）':''}`:'无命中';
      for(const [i,h] of hits.entries()){const b=button(h.pointer||'$',()=>{hitIndex=i;showHit();},'ix-match');b.title=h.preview;matches.append(b);}
      if(hits.length)showHit();
    }catch(e){toast(e.message);}
  }
  function showHit(){if(!hits.length)return;matches.querySelectorAll('button').forEach((b,i)=>b.classList.toggle('selected',i===hitIndex));hitCount.textContent=`${hitIndex+1} / ${hits.length}${hits.length===200?'（最多显示 200 个）':''}`;revealPointer(hits[hitIndex].pointer,search);}
  function goHit(dir){if(!hits.length)return;hitIndex=(hitIndex+dir+hits.length)%hits.length;showHit();}
  recordSearch.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();findMatches();}};
  try{
    const r=await api('inspection/rules');rules=Object.fromEntries(r.rules.map(x=>[x.id,x.title]));
    if(trajectoryId&&!evalId){const t=await api('trajectory',{id:trajectoryId});evalId=t.evaluation_id;}
    if(evalId){evaluation=await api('evaluation',{id:evalId});onEvaluation(evaluation);
      if(trajectoryId){const pos=await api('inspection/evaluation-position',{evaluation:evalId,trajectory:trajectoryId,dataset,outcome});dataset=pos.dataset;offset=pos.offset;}
    }
    await refreshFiles();if(!alive())return;
    if(!methodId&&!fileId&&!evalId&&files.length)fileId=files[0].id;
    fileSelect.value=methodId?'method':evalId?'evaluation':fileId;
    await loadList(true);
  }catch(e){if(alive()){empty(treeHost,e.message);toast(e.message);}}
  return {dispose:()=>{++docToken;++listToken;loop?.dispose();methodPanel?.dispose();clearTimeout(debounce);}};
}
