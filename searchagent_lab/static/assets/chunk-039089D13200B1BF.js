/** Experiment-local, read-only single trace / same-question comparison.
 * The timeline is a projection of saved events. No model calls, judges or writes.
 */
import {node,button,link,href,badge,select,field,empty,card,controls,scoreCell,jsonModal,saveJSON} from './chunk-F51FD76F14331921.js';
import {experimentParams,executionReturnParams} from './chunk-D875F66C32166310.js';
import {renderProblem} from './chunk-29D445140A1FB405.js';
import {highlight} from './chunk-E1EB354F726277C3.js';
import {renderControlComparison,axisOptions,evaluationAxis} from './chunk-FA5868B8E5D4159E.js';
import {buildTrace,documents,valueText,hasValue,firstDifference,sameQuestion,recordedGroup} from './chunk-896DA65B5AA6ABA2.js';

const clip=(text,n=190)=>{const s=valueText(text);return s.length>n?s.slice(0,n)+'…':s;};
const outcomeLabels={rescued:'A 错 B 对',harmed:'A 对 B 错',both_correct:'两边都对',both_wrong:'两边都错',missing:'未配对 / 结果缺失'};
const kindSymbols={search:'⌕',read:'▤',tool:'↗',model:'·',answer:'✓',system:'!',error:'!'};
const rowKey=r=>r.key||JSON.stringify([r.sample.dataset,r.sample.id]);
const shortRun=ev=>[ev?.run?.base_model,ev?.checkpoint?.name].filter(Boolean).join(' · ');
const scored=v=>hasValue(v?.scores?.em);

export async function mountTraceBrowser(host,ctx,{compare=false}={}) {
 const {api,q,run,alive}=ctx;
 if(!run){host.append(empty('请选择上方的运行，查看已保存的单条轨迹和同题对比。'));return;}
 let dead=false,loadEpoch=0,docEpoch=0,data=null,rows=[],selectedRow=null;
 let b=q.get('trace_b')||q.get('result_b')||'',a=q.get('trace_a')||q.get('result_a')||'';
 let panel=q.get('trace_panel')||q.get('panel')||'',axis=evaluationAxis(q.get('trace_axis'));
 let scope=q.get('trace_scope')||'',selectedKey=q.get('trace_key')||'',searchTimer=null;
 let sample=q.get('trace_sample')||'',dataset=q.get('trace_dataset')||'',term=q.get('trace_q')||'',filter=q.get('result_filter')||q.get('trace_filter')||'all',datasetFilter=q.get('result_dataset')||'';
 if(filter==='unscored')filter='missing';
 let side=q.get('trace_side')==='a'?'a':'b',stepIndex=Math.max(0,Number(q.get('trace_step'))||0);
 let detailTab=q.get('trace_field')||'visible',onlyDiff=q.get('trace_diff')==='1',listPage=Math.max(0,Number(q.get('trace_page'))||0),highlightTerm='';
 let docs={a:null,b:null},traces={a:null,b:null},streamIndices={a:0,b:0},difference=null;
 const cache=new Map(),pending=new Map();
 const shell=node('div','tb-shell');shell.dataset.traceBrowser=compare?'compare':'single';host.append(shell);
 const toolbar=node('div','tb-toolbar'),summary=node('div','tb-summary'),condition=node('div','tb-condition');
 const layout=node('div','tb-layout'),questions=card('tb-questions'),main=node('div','tb-main');
 const questionHeading=node('div','tb-question-heading'),answers=node('div','tb-answers'),timelineTools=node('div','tb-timeline-tools');
 const timelines=node('div','tb-timelines'),details=card('tb-details'),problemHost=node('div','cl-problems');
 shell.append(toolbar,summary,condition,layout);layout.append(questions,main);main.append(questionHeading,answers,problemHost,timelineTools,timelines,details);
 const live=node('div','tb-sr-only');live.setAttribute('role','status');live.setAttribute('aria-live','polite');shell.append(live);
 let questionList=null,questionCount=null,filterControls=null;
 const valid=()=>!dead&&alive();
 const state=()=>ctx.setViewState?.({trace_b:b,trace_a:a,trace_panel:panel,result_dataset:datasetFilter,trace_scope:scope,trace_key:selectedKey,trace_page:listPage||'',trace_axis:axis,trace_sample:sample,trace_dataset:dataset,trace_q:term,trace_filter:filter==='all'?'':filter,trace_side:side,trace_step:stepIndex||'',trace_field:detailTab==='visible'?'':detailTab,trace_diff:onlyDiff?'1':''});
 function failure(error,retry){const box=node('div','tb-load-error');box.append(empty(error.message||String(error)),button('重试',retry,'s-btn'));return box;}
 async function documentFor(id){
  if(cache.has(id))return cache.get(id);
  if(pending.has(id))return pending.get(id);
  const task=api('inspection/record',{trajectory:id}).then(d=>{if(valid()){cache.set(id,d);if(cache.size>6)cache.delete(cache.keys().next().value);}return d;}).finally(()=>pending.delete(id));
  pending.set(id,task);return task;
 }
 async function load(){
  if((!compare||!a)&&['rescued','harmed','both_correct','both_wrong'].includes(filter))filter='all';
  const token=++loadEpoch;++docEpoch;shell.setAttribute('aria-busy','true');
  main.replaceChildren(empty('读取本次评测的逐题记录…'));
  try{
   const result=await api('studio/trace-browser',{run:run.id,panel:scope==='diagnostic'?panel:'',scope,b,a:compare?a:'',axis,offset:listPage*30,limit:30,q:term,filter,dataset_filter:datasetFilter,sample,dataset,key:selectedKey});
   if(!valid()||token!==loadEpoch)return;
   data=result;b=data.selected_evaluation?.id||'';panel=data.panel?.id||'';listPage=Math.floor((data.page?.offset||0)/30);
   if(compare)a=data.selected_a||'';
   if(a&&!data.candidates.some(c=>c.evaluation.id===a))a='';
   rows=data.report?.items||[];
   for(const r of rows)r.displayGroup=r.group||recordedGroup(r.a,r.b);
   renderToolbar();renderSummary();buildQuestionList();
   const available=filteredRows();
   const requested=available.find(r=>selectedKey?rowKey(r)===selectedKey:r.sample.id===sample&&r.sample.dataset===dataset);
   const row=requested||available[0];
   if(row){sample=row.sample.id;dataset=row.sample.dataset;selectedKey=rowKey(row);renderQuestionRows();await loadQuestion(row,{keepStep:!!requested});}
   else{sample='';dataset='';selectedKey='';selectedRow=null;state();main.replaceChildren(empty(data.page?.total?'没有匹配题目。请清除搜索或切换筛选。':data.message));}
  }catch(error){if(valid()&&token===loadEpoch){toolbar.replaceChildren();summary.replaceChildren();condition.replaceChildren();questions.replaceChildren();main.replaceChildren(failure(error,load));}}
  finally{if(valid()&&token===loadEpoch)shell.setAttribute('aria-busy','false');}
 }
 function renderToolbar(){
  toolbar.replaceChildren();
  const label=e=>[e.run?.base_model,e.checkpoint?.name,e.scope==='diagnostic'?'固定诊断':'完整评测',(e.dataset_names||[]).join(' / '),'split '+(e.split||'未记录'),(e.question_count??'未知')+' 题',e.record_type==='aggregate'?'逐题结果待接入':e.name||e.id].filter(Boolean).join(' · ');
  const evaluations=data.evaluations.map(e=>[e.id,label(e)]);
  toolbar.append(field(compare?'B · 当前评测':'当前评测',select([...(evaluations.length?[]:[['','尚未接入评测记录']]),...evaluations],b,id=>{b=id;a='';datasetFilter='';scope='';panel='';sample='';dataset='';selectedKey='';listPage=0;stepIndex=0;filter='all';load();},'轨迹当前评测 B')));
  toolbar.append(link('固定诊断题单',href('experiment',{project:ctx.project,track:ctx.track,experiment:ctx.experiment.experiment.id,run:run.id,view:'diagnostics'}),'s-link'));
  if(compare){
   const options=data.candidates.map(c=>[c.evaluation.id,label(c.evaluation)]);
   toolbar.append(field('A · 对照评测',select([['',options.length?'选择要比较的评测':'暂无可接入逐题结果的对照评测'],...options],a,id=>{a=id;stepIndex=0;filter='all';listPage=0;sample='';selectedKey='';load();},'轨迹对照评测 A')));
  }
 }
 function renderSummary(){
  summary.replaceChildren();condition.replaceChildren();const report=data.report;
  if(!report){summary.append(badge(data.selected_evaluation?'已有评测汇总':'结果未接入'),node('span','s-muted',data.message));return;}
  if(report.b?.note)condition.append(node('p','tb-notice',report.b.note));
  const count=ev=>`${ev.stats.answer_correct} / ${ev.stats.expected} 正确`;
  if(compare&&report.a)summary.append(badge('A · '+count(report.a)));
  summary.append(badge((compare?'B · ':'')+count(report.b),'purple'),node('span','s-muted',`${report.b.stats.recorded} 题已接入 · ${report.b.stats.scored} 题已评分 · ${data.message}`));
  if(report.pair?.compatible){
   const counts=report.pair.groups;
   if(counts)summary.append(node('strong','tb-pair-count',`错→对 ${counts.rescued||0} · 对→错 ${counts.harmed||0}`));
  }
  const protocol=button('评测条件',()=>jsonModal('已记录的评测条件',{evaluation:report.b.id,run:run.id,protocol:report.b.protocol,method_state:report.b.method_state}),'s-link');summary.append(protocol);
  if(compare&&!a)condition.append(node('p','tb-notice','先选择对照 A。按数据集、题目 ID、题目正文和数据划分核对配对；未配对的题单独保留。'));
  if(compare&&report.pair){
   const box=node('div','tb-comparison-status');
   box.append(badge(report.pair.compatible?'已登记条件可比':'仅浏览差异',''+(report.pair.compatible?'good':'warn')),node('span','s-muted',report.pair.compatible?'同题变化用于开发诊断，不代表统计显著或因果证明。':'模型、Harness、预算或记录完整性未满足对照要求，不归因于训练提升。'));
   condition.append(box);
   const fold=renderControlComparison(report.pair);
   fold.append(field('核对目标',select(axisOptions,axis,value=>{axis=value;load();},'轨迹对比目标')));condition.append(fold);
  }
 }
 function matches(r){
  if(filter==='incorrect')return r.b?.scores?.em===0;
  if(filter==='error')return !!r.b?.runtime_status&&!['ok','unknown'].includes(r.b.runtime_status);
  if(filter==='unscored')return !scored(r.b)||(a&&!scored(r.a));
  if(['rescued','harmed','both_correct','both_wrong'].includes(filter))return r.displayGroup===filter;
  return true;
 }
 function filteredRows(){return rows;}
 function buildQuestionList(){
  if(!data.report){questions.replaceChildren(empty('逐题结果待接入。接入后可按答案错误、运行异常和缺失状态筛选。'));questionList=null;return;}
  questions.replaceChildren();const title=node('div','tb-list-heading');questionCount=node('strong');title.append(questionCount);questions.append(title);
  const search=node('input','tb-question-search');search.type='search';search.value=term;search.placeholder='搜索题目或答案';search.setAttribute('aria-label','轨迹题目搜索');
  search.oninput=()=>{term=search.value;listPage=0;clearTimeout(searchTimer);searchTimer=setTimeout(onFilter,300);};questions.append(search);
  questions.append(select([['','全部数据集'],...(data.selected_evaluation?.dataset_names||[]).map(d=>[d,d])],datasetFilter,v=>{datasetFilter=v;listPage=0;onFilter();},'轨迹数据集'));
  filterControls=node('div','tb-list-filters');
  const filters=compare&&a?[['all','全部'],['harmed','A 对 B 错'],['rescued','A 错 B 对'],['both_wrong','两边都错'],['both_correct','两边都对'],['missing','未配对 / 结果缺失']]:[['all','全部'],['incorrect','答案错误'],['error','运行异常'],['missing','缺失 / 未评分']];
  if(!filters.some(([key])=>key===filter))filter='all';
  for(const [key,label]of filters){const b=button(label+' '+(data.page?.counts?.[key]||0),()=>{filter=key;listPage=0;onFilter();},'s-filter '+(filter===key?'active':''));b.dataset.traceFilter=key;filterControls.append(b);}
  questions.append(filterControls);questionList=node('div','tb-question-list');questions.append(questionList);renderQuestionRows();
 }
 function onFilter(){
  sample='';dataset='';selectedKey='';state();load();
 }
 function renderQuestionRows(){
  if(!questionList)return;const visible=filteredRows();questionCount.textContent=`题目 ${data.page?.filtered||0} / ${data.page?.total||0}`;questionList.replaceChildren();
  for(const r of visible){
   const active=rowKey(r)===selectedKey;
   const b=button('',()=>chooseQuestion(r),'tb-question '+(active?'active':''));b.dataset.traceSample=r.sample.id;b.dataset.traceDataset=r.sample.dataset;
   b.setAttribute('aria-current',active?'true':'false');b.append(node('small','s-muted',`${listPage*30+rows.indexOf(r)+1} · ${r.sample.dataset}`),node('strong','',r.sample.question));
   b.title=r.sample.question;
   if(compare&&a)b.append(badge(outcomeLabels[r.displayGroup],r.displayGroup==='harmed'?'bad':r.displayGroup==='rescued'?'good':''));else b.append(scoreCell(r.b));
   questionList.append(b);
  }
  if(data.page?.filtered>30){const prev=button('上一页',()=>{listPage--;onFilter();},'s-link'),next=button('下一页',()=>{listPage++;onFilter();},'s-link');prev.disabled=listPage===0;next.disabled=(listPage+1)*30>=data.page.filtered;questionList.append(controls(prev,node('small','s-muted',`${listPage+1}/${Math.ceil(data.page.filtered/30)}`),next));}
  if(!visible.length)questionList.append(empty('无匹配题目'));
 }
 async function chooseQuestion(row){sample=row.sample.id;dataset=row.sample.dataset;selectedKey=rowKey(row);stepIndex=0;detailTab='visible';highlightTerm='';streamIndices={a:0,b:0};renderQuestionRows();state();await loadQuestion(row);}
 async function loadQuestion(row,{keepStep=false}={}){
  const token=++docEpoch;selectedRow=row;docs={a:null,b:null};traces={a:null,b:null};difference=null;
  main.replaceChildren(questionHeading,answers,problemHost,timelineTools,timelines,details);renderQuestionHeading();renderAnswers();problemHost.replaceChildren();timelineTools.replaceChildren();timelines.replaceChildren(empty('读取本题实际执行记录…'));details.replaceChildren();details.hidden=true;
  const sides=compare&&a?['a','b']:['b'];
  const requests=await Promise.allSettled(sides.map(s=>row[s]?documentFor(row[s].id):Promise.resolve(null)));
  if(!valid()||token!==docEpoch)return;
  for(let i=0;i<sides.length;i++){
   const result=requests[i],s=sides[i];
   if(result.status==='fulfilled'){
    const doc=result.value;
    // A stale/misrouted API reply must not attach another question's evidence.
    if(doc&&(doc.view.sample_id!==row.sample.id||doc.view.dataset!==row.sample.dataset||doc.source?.evaluation_id!==(s==='a'?a:b))){timelines.replaceChildren(empty('轨迹归属与当前题目不一致，已阻止显示。'));return;}
    docs[s]=doc;
   }else docs[s]={loadError:result.reason};
  }
  if(docs.a&&!docs.a.loadError&&docs.b&&!docs.b.loadError&&!sameQuestion(docs.a,docs.b)){
   timelines.replaceChildren(empty('两条记录的题目正文或 dataset / sample ID 不一致，已阻止误配。'));return;
  }
  for(const s of sides)if(docs[s]&&!docs[s].loadError&&!docs[s].parse_error){traces[s]=buildTrace(docs[s],streamIndices[s]);}
  difference=firstDifference(traces.a,traces.b);
  if(!compare||!a||!traces[side])side=traces.b?'b':traces.a?'a':'b';
  if(!keepStep)stepIndex=0;stepIndex=Math.min(stepIndex,Math.max(0,(traces[side]?.groups.length||1)-1));
  ctx.markDemo(!!run.demo||!!docs.a?.demo||!!docs.b?.demo);renderQuestionHeading();renderAnswers();renderProblems();renderTimelineTools();renderTimelines();renderDetails();state();
  live.textContent='已加载题目：'+row.sample.question;
 }
 function renderProblems(){
  problemHost.replaceChildren();for(const s of compare&&a?['a','b']:['b']){
   const slot=node('div');problemHost.append(slot);const doc=docs[s];
   renderProblem(slot,doc,ctx,{label:(compare?s.toUpperCase()+' · ':'')+'问题摘要',
    reveal:pointer=>{const group=traces[s]?.groups.find(g=>g.events.some(e=>pointer===e.pointer||pointer.startsWith(e.pointer+'/')));if(group){chooseStep(s,group.position,'more');details.scrollIntoView({block:'nearest'});}else ctx.toast('此证据属于轨迹整体；请打开完整日志查看原始字段。');},
    updated:fresh=>{docs[s]=fresh;cache.set(fresh.source.trajectory_id,fresh);}});
  }
 }
 function renderQuestionHeading(){
  questionHeading.replaceChildren();if(!selectedRow)return;
  const text=node('div');text.append(node('small','s-muted',selectedRow.sample.dataset+' · '+selectedRow.sample.id),node('h2','',selectedRow.sample.question));
  const visible=filteredRows(),i=visible.findIndex(r=>rowKey(r)===rowKey(selectedRow));
  if(selectedRow.pairing_note)text.append(node('p','s-muted',selectedRow.pairing_note));
  const prev=button('上一题',()=>moveQuestion(-1),'s-btn small'),next=button('下一题',()=>moveQuestion(1),'s-btn small');prev.disabled=listPage*30+i<=0;next.disabled=i<0||listPage*30+i>=data.page.filtered-1;
  questionHeading.append(text,controls(prev,node('span','s-muted',`${listPage*30+i+1} / ${data.page.filtered}`),next));
 }
 function renderAnswers(){
  answers.replaceChildren();if(!selectedRow)return;
  for(const s of compare&&a?['a','b']:['b']){
   const r=selectedRow[s],ev=s==='a'?data.report?.a:data.report?.b;
   const cell=node('div','tb-answer-cell');cell.append(node('small','s-muted',(compare?s.toUpperCase()+' · ':'')+(ev?.checkpoint?.name||'当前结果')),scoreCell(r),node('strong','',r?valueText(r.parsed_answer).trim()||'解析答案未记录':'本次评测没有此题结果'));
   if(r?.runtime_status&&!['ok','unknown'].includes(r.runtime_status))cell.append(badge('运行状态 · '+r.runtime_status,'warn'));
   cell.title=shortRun(ev);answers.append(cell);
  }
  const gold=node('div','tb-answer-cell tb-reference');gold.append(node('small','s-muted','标准答案 · 仅供评分参考'),node('strong','',valueText((selectedRow.sample.gold_answers||selectedRow.sample.answers||docs.b?.view?.gold_answers||docs.a?.view?.gold_answers||[]).join(' / '))||'未记录'));
  answers.append(gold);
 }
 function renderTimelineTools(){
  timelineTools.replaceChildren();const title=node('strong','',compare?'同题独立轨迹':'执行步骤');timelineTools.append(title);
  if(compare&&a&&difference){
   if(difference.identical)timelineTools.append(node('span','s-muted','动作和返回未见字面差异；完整模型回复可展开查看。'));
   else{
    const jump=button(difference.prefix?`跳到首个动作 / 返回差异 · 相同前缀 ${difference.prefix} 步`:'跳到首个动作 / 返回差异',jumpDifference,'s-btn small');jump.dataset.traceJump='difference';timelineTools.append(jump);
    if(difference.prefix){const label=node('label','tb-diff-toggle'),input=node('input');input.type='checkbox';input.checked=onlyDiff;input.onchange=()=>{onlyDiff=input.checked;state();renderTimelines();};label.append(input,document.createTextNode('收起相同前缀'));timelineTools.append(label);}
   }
  }
  const explain=node('details','tb-explain');explain.append(node('summary','','查看口径'),node('p','s-muted','一张卡合并可明确关联的模型回复与工具记录，保留原始序号。两侧按各自顺序展示，不把相同步号当作同一语义阶段。重复和差异是日志现象，不是根因判定。'));timelineTools.append(explain);
 }
 function renderTimelines(){
  timelines.replaceChildren();timelines.classList.toggle('is-compare',compare&&!!a);
  for(const s of compare&&a?['a','b']:['b']){
   const trace=traces[s],doc=docs[s],ev=s==='a'?data.report?.a:data.report?.b;
   const column=card('tb-column');column.dataset.traceSide=s;const h=node('div','tb-column-heading');
   h.append(node('strong','',compare?(s==='a'?'A · 对照':'B · 当前'):'单条轨迹'),node('span','s-muted',ev?.checkpoint?.name||'未选择评测'));
   if(trace)h.append(node('small','s-muted',`${trace.groups.length} 个展示步骤 · ${trace.rawStepCount} 条原始记录`));
   column.append(h);
   if(trace?.streams.length>1)column.append(field('记录中的轨迹',select(trace.streams.map((t,i)=>[String(i),t.name||`轨迹 ${i+1}`]),String(streamIndices[s]),value=>{streamIndices[s]=Number(value);traces[s]=buildTrace(doc,streamIndices[s]);stepIndex=0;side=s;difference=firstDifference(traces.a,traces.b);renderTimelineTools();renderTimelines();renderDetails();},`轨迹分支 ${s}`)));
   if(!doc)column.append(empty(selectedRow?.[s]?'读取中…':'这次评测没有保存本题轨迹，未按其他题补齐。'));
   else if(doc.loadError)column.append(failure(doc.loadError,()=>loadQuestion(selectedRow,{keepStep:true})));
   else if(doc.parse_error)column.append(empty('原始记录无法解析：'+doc.parse_error),button('查看损坏的原文',()=>jsonModal('原始内容',doc.raw_text),'s-link'));
   else if(!trace?.groups.length)column.append(empty('未保存步骤。最终输出和评分仍可查看。'));
   else{
    const scroll=node('div','tb-step-scroll');scroll.tabIndex=0;scroll.setAttribute('aria-label',`${s.toUpperCase()} 执行步骤`);
    if(onlyDiff&&difference?.prefix)scroll.append(node('div','tb-prefix-note',`已收起相同前缀 ${difference.prefix} 步`));
    for(const g of trace.groups){if(onlyDiff&&difference?.prefix&&g.position<difference.prefix)continue;scroll.append(stepCard(g,s));}column.append(scroll);
   }
   if(doc&&!doc.loadError){
    const foot=node('div','tb-column-footer');
    const params={...executionReturnParams(q),project:ctx.project,track:ctx.track,evaluation:ev?.id,trajectory:doc.source?.trajectory_id,return_experiment:ctx.experiment.experiment.id,return_run:run.id,return_view:compare?'trace-compare':'trace',return_trace_b:b,return_trace_a:a,return_trace_sample:sample,return_trace_dataset:dataset,return_trace_panel:panel,return_trace_axis:axis};
    foot.append(button('原始最终输出',()=>jsonModal('已记录的最终输出',{raw_final_output:doc.view.raw_final_output,parsed_answer:doc.view.parsed_answer,termination_reason:doc.view.termination_reason,runtime_status:doc.view.runtime_status,runtime_error:doc.view.runtime_error,scores:doc.view.scores}),'s-link'),(()=>{const log=link('完整日志 / 方法记录 ↗',href('replay',params),'s-link');log.addEventListener('click',()=>{state();log.href=href('replay',{...params,...executionReturnParams(q)});});return log;})());column.append(foot);
   }
   timelines.append(column);
  }
 }
 function stepCard(g,s){
  const item=node('article','tb-step');item.dataset.traceStep=g.position;item.dataset.traceOwner=s;
  const active=side===s&&stepIndex===g.position;item.classList.toggle('active',active);
  const differing=!!difference&&!difference.identical&&difference[s]===g.position;item.classList.toggle('first-difference',differing);
  if(difference?.prefix&&g.position<difference.prefix)item.classList.add('common-prefix');
  const head=button('',()=>chooseStep(s,g.position),'tb-step-button');head.setAttribute('aria-expanded',active?'true':'false');head.setAttribute('aria-label',`${s.toUpperCase()} 第 ${g.position+1} 步 ${g.label}`);
  head.append(node('span','tb-step-number',g.position+1),node('strong','tb-step-kind',g.label),node('span','tb-step-action',clip(g.headline,220)));
  if(differing)head.append(badge('首个字面差异','purple'));head.append(node('span','tb-step-chevron','›'));item.append(head);
  if(g.docs.length){
   const preview=button('',()=>chooseStep(s,g.position,'visible'),'tb-evidence-preview');const d=g.docs[0];
   preview.append(node('small','s-muted',`模型可见 · ${g.docs.length} 个片段`),node('strong','',d.title),node('span','',clip(d.text,compare?145:235)));item.append(preview);
  }else if(g.actions.length||g.returnRecorded){item.append(node('p','tb-step-note',g.visibleRecorded?'模型可见内容明确为空':'模型可见内容未记录 · 不用工具返回补齐'));}
  else if(g.kind==='model'){const text=clip(g.outputs[0]?.value,145);if(text)item.append(node('p','tb-step-note',text));}
  const meta=node('div','tb-step-meta');
  for(const f of g.flags){const bt=button(f.label,()=>{chooseStep(s,g.position,f.label.includes('错误')?'more':'visible');},'tb-event-flag');bt.title=f.detail;meta.append(bt);}
  if(g.actions.some(c=>c.requested))meta.append(badge('含仅记录请求的调用'));
  if(hasValue(g.latency))meta.append(node('small','s-muted',`${g.latency} ms`));
  if(meta.children.length)item.append(meta);
  item.append(node('small','tb-raw-index','原始序号 '+g.rawIndices.join(' / ')));return item;
 }
 function chooseStep(s,index,tab){side=s;stepIndex=index;if(tab)detailTab=tab;state();updateSelection();renderDetails();}
 function updateSelection(){for(const el of timelines.querySelectorAll('[data-trace-step]')){const selected=el.dataset.traceOwner===side&&Number(el.dataset.traceStep)===stepIndex;el.classList.toggle('active',selected);el.querySelector('.tb-step-button')?.setAttribute('aria-expanded',String(selected));}}
 function jumpDifference(){
  if(!difference||difference.identical)return;
  for(const s of ['a','b'])if(difference[s]!==null){const scroll=timelines.querySelector(`[data-trace-side="${s}"] .tb-step-scroll`),el=scroll?.querySelector(`[data-trace-step="${difference[s]}"]`);if(scroll&&el)scroll.scrollTop=el.offsetTop-scroll.offsetTop-8;}
  chooseStep(difference.b!==null?'b':'a',difference.b??difference.a,'visible');
 }
 async function moveQuestion(delta){const list=filteredRows(),i=list.findIndex(r=>rowKey(r)===selectedKey);if(i>=0&&list[i+delta])chooseQuestion(list[i+delta]);else if(i>=0){listPage+=delta;sample='';dataset='';selectedKey='';await load();if(delta<0&&rows.length)chooseQuestion(rows.at(-1));}}
 function renderDetails(){
  details.replaceChildren();details.hidden=false;
  const trace=traces[side],doc=docs[side],g=trace?.groups[stepIndex];
  if(!g){details.append(empty('选择一条已保存的步骤，查看该步原文。'));return;}
  const heading=node('div','tb-detail-heading');
  heading.append(node('h3','',`${compare?side.toUpperCase()+' · ':''}第 ${stepIndex+1} 步 · ${g.label}`));
  const prev=button('← 上一步',()=>chooseStep(side,stepIndex-1),'s-btn small'),next=button('下一步 →',()=>chooseStep(side,stepIndex+1),'s-btn small');prev.disabled=stepIndex===0;next.disabled=stepIndex>=trace.groups.length-1;
  heading.append(controls(prev,next));details.append(heading);
  const tabOptions=[['visible','模型可见内容'],['model','模型回复'],['returned','工具原始返回'],['more','输入 / 提醒 / 错误']];
  if(!tabOptions.some(([k])=>k===detailTab))detailTab='visible';
  const tabs=node('div','tb-detail-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','选中步骤字段');
  for(const[k,label]of tabOptions){const bt=button(label,()=>{detailTab=k;state();renderDetails();},'s-tab '+(k===detailTab?'active':''));bt.setAttribute('role','tab');bt.setAttribute('aria-selected',String(k===detailTab));bt.dataset.traceField=k;tabs.append(bt);}details.append(tabs);
  const content=node('div','tb-detail-body');content.setAttribute('role','tabpanel');details.append(content);
  if(detailTab==='visible'){
   content.append(node('p','s-muted','来自日志中单独保存的 model_visible 字段，不等同于完整模型输入。没有记录时不从工具返回推测。'));
   if(!g.visibleRecorded)content.append(empty('模型可见内容：未记录'),button('查看已保存的工具返回',()=>{detailTab='returned';state();renderDetails();},'s-link'));
   else{
    const highlightBox=node('input','tb-highlight');highlightBox.type='search';highlightBox.value=highlightTerm;highlightBox.placeholder='高亮本步中的文字（仅字面匹配）';highlightBox.setAttribute('aria-label','高亮可见内容');content.append(highlightBox);
    const docHost=node('div','tb-documents');content.append(docHost);const render=()=>{docHost.replaceChildren();if(!g.docs.length)docHost.append(empty('模型可见内容明确为空'));for(const d of g.docs)docHost.append(documentCard(d,highlightTerm));};highlightBox.oninput=()=>{highlightTerm=highlightBox.value;render();};render();
    if(g.flags.length){const events=node('div','tb-event-notes');for(const f of g.flags){const note=node('p');note.append(badge(f.label,'warn'),node('span','s-muted',' '+f.detail));if(hasValue(f.related))note.append(button(`查看第 ${f.related+1} 步`,()=>chooseStep(side,f.related,'visible'),'s-link'));events.append(note);}content.append(events);}
    for(const f of g.visible)content.append(button('查看此字段原文',()=>jsonModal(f.pointer,f.value),'s-link'));
   }
  }else if(detailTab==='model'){
   if(!g.outputs.length)content.append(empty('此步没有单独记录模型回复。'));
   for(const f of g.outputs)content.append(textBlock(f.label||'原始模型回复',f.value,f.pointer));
   for(const c of g.actions){const box=node('div','tb-call');box.append(node('strong','',c.name),node('pre','tb-code',valueText(c.arguments)));content.append(box);}
  }else if(detailTab==='returned'){
   content.append(node('p','s-muted','工具原始返回不代表模型实际看到了全部内容。此处只展示已保存的返回。'));
   if(!g.returnRecorded)content.append(empty('工具原始返回：未记录'));
   for(const f of g.returns){const docs=documents(f.value);if(!docs.length)content.append(empty('工具返回明确为空'));for(const d of docs)content.append(documentCard(d,''));content.append(button('查看返回的原始字段',()=>jsonModal(f.pointer,f.value),'s-link'));}
  }else{
   if(g.inputs.length){for(const f of g.inputs)content.append(textBlock(f.label||'本步记录的输入',f.value,f.pointer,true));}else content.append(node('p','s-muted','本次模型输入：未记录，不从后续消息倒推。'));
   if(g.notices.length){for(const f of g.notices)content.append(textBlock('系统提醒 · '+f.name,f.value,f.pointer));}else content.append(node('p','s-muted','此步未单独记录系统提醒。'));
   if(g.errors.length){for(const f of g.errors)content.append(textBlock('已记录的错误 · '+f.name,f.value,f.pointer));}else content.append(node('p','s-muted','此步未记录 error / 异常 status；不代表已验证无异常。'));
   const rest=g.all.filter(f=>['stop_reason','finish_reason','done','status','observation_truncated','timestamp','latency_ms'].includes(f.name));
   if(rest.length){const values=node('div','tb-recorded-values');for(const f of rest)values.append(node('div','',`${f.name} · ${valueText(f.value)}`));content.append(values);}
   content.append(button('查看本步全部原始字段',()=>jsonModal('本步原始记录（未改写）',g.events.map(e=>({pointer:e.pointer,fields:e.fields}))),'s-link'));
  }
  const footer=node('div','tb-detail-footer');footer.append(node('small','s-muted','原始记录 '+g.events.map(e=>e.pointer).join(' · ')),button('导出整条原始轨迹',()=>saveJSON(doc.raw,'trajectory.json'),'s-link'));details.append(footer);
 }
 function documentCard(d,term){
  const box=node('article','tb-document');const title=node('div','tb-document-title');title.append(node('strong','',d.title));
  if(d.id)title.append(node('small','s-muted','ID '+d.id));
  // Link only a saved, safe http(s) URL. Never invent a website for a corpus hit.
  if(/^https?:\/\//i.test(d.url)){const a=link('原始链接 ↗',d.url,'s-link');a.target='_blank';a.rel='noopener noreferrer';title.append(a);}box.append(title);
  const text=node('div','tb-document-text');highlight(text,d.text,term);box.append(text);
  if(hasValue(d.repeatedFrom))box.append(badge(`与第 ${d.repeatedFrom+1} 步可见文本重复`,'warn'));
  if(d.pointer)box.append(node('small','tb-field-path',d.pointer));return box;
 }
 function textBlock(label,value,pointer,folded=false){
  const box=node(folded?'details':'section','tb-text-block');box.append(node(folded?'summary':'h4','',label));
  const body=node('div');body.append(node('pre','tb-plain',valueText(value)),node('small','tb-field-path',pointer));
  if(folded){box.addEventListener('toggle',()=>{if(box.open&&!body.isConnected)box.append(body);});}else box.append(body);return box;
 }
 const keys=e=>{if(!valid()||!shell.isConnected||e.target.closest('input,textarea,select,dialog,button,[role="tab"]'))return;if(e.key==='ArrowRight'&&stepIndex<(traces[side]?.groups.length||0)-1){e.preventDefault();chooseStep(side,stepIndex+1);}if(e.key==='ArrowLeft'&&stepIndex>0){e.preventDefault();chooseStep(side,stepIndex-1);}};
 document.addEventListener('keydown',keys);
 await load();
 return()=>{dead=true;loadEpoch++;docEpoch++;clearTimeout(searchTimer);cache.clear();pending.clear();document.removeEventListener('keydown',keys);};
}
