import {node,button,tag,short,empty,saveJSON,pretty} from './dom.js';
import {JsonTree} from './json-tree.js';
const states={pass:'通过',fail:'失败',insufficient:'证据不足',not_applicable:'不适用'};
const components={prompt:'Prompt / 指令',tool_schema:'工具 Schema',retrieval:'检索',context_builder:'上下文构造',parser:'答案解析器',scorer:'评分器',runtime:'运行环境',policy:'策略 / 决策',skill:'Skill',training:'训练信号',unknown:'待定位'};
const hstates={unverified:'尚未验证',supported_by_experiment:'实验支持 · 人工判断',refuted:'实验反驳 · 人工判断',inconclusive:'结果不确定'};
const modes={component:'组件重放 · 本地执行',prefix:'前缀干预 · 外部执行',autonomous:'从头回归 · 外部执行'};
const required=['/checkpoint_id','/panel_hash','/retriever','/prompt','/parser','/scorer','/budget','/decoding','/runtime'];
function lines(s){return s.split('\n').map(x=>x.trim()).filter(Boolean);}
function section(title,sub){const c=node('section','lp-section');c.append(node('h3','',title));if(sub)c.append(node('p','lp-muted',sub));return c;}
function field(form,label,value='',type='input'){const l=node('label','lp-label',label);const e=node(type);e.setAttribute('aria-label',label);if(type==='textarea')e.rows=4;e.value=typeof value==='string'?value:JSON.stringify(value,null,2);l.append(e);form.append(l);return e;}
function select(form,label,options,value){const e=field(form,label,'','select');for(const [k,v] of Object.entries(options)){const o=node('option','',v);o.value=k;e.append(o);}if(value)e.value=value;return e;}
function jsonView(parent,value,label='原始对象'){const host=node('div','lp-json');parent.append(host);new JsonTree(host,value,{label,open:false});return host;}
function table(parent,headers,rows){const wrap=node('div','lp-table-wrap'),t=node('table','lp-table'),head=node('thead'),tr=node('tr');for(const s of headers)tr.append(node('th','',s));head.append(tr);t.append(head);const body=node('tbody');for(const row of rows){const r=node('tr');for(const val of row){const c=node('td');c.append(val instanceof Node?val:node('span','',val??'未记录'));r.append(c);}body.append(r);}t.append(body);wrap.append(t);parent.append(wrap);}

/** Case workflow, independently testable from the trace/tree renderer. */
export class LoopPanel {
  constructor(host,doc,{api,session,params,reveal,toast,getPointer,onSection}){
    Object.assign(this,{host,doc,api,session,params:{...params},reveal,toast,getPointer,onSection});
    this.data=null;this.section='checks';this.disposed=false;this.token=0;
  }
  dispose(){this.disposed=true;this.token++;}
  async load(){const token=++this.token;try{const d=await this.api('loop/workspace',this.params);if(this.disposed||token!==this.token)return;this.data=d;if(!this.host.hidden)this.render();}catch(e){if(!this.disposed)empty(this.host,e.message);}}
  async post(op,data){const r=await this.api('loop/'+op,{}, {method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':this.session.csrf_token},body:JSON.stringify({...this.params,source_hash:this.doc.source_hash,...data})});if(!this.disposed)await this.load();return r;}
  select(name){this.section=name;this.render();}
  render(){if(this.disposed)return;if(!this.data){empty(this.host,'正在读取分层检查与案例记录…');return;}
    this.host.replaceChildren();const top=node('div','lp-heading');top.append(node('span','lp-muted','CASE → EVALUATION → FIX → REGRESSION'),button('导出案例证据包',async()=>{const d=await this.api('loop/export',this.params);saveJSON(d,'case-diagnosis-evidence.json');},'button small'));this.host.append(top);
    if(this.section==='checks')this.checks();else if(this.section==='hypotheses')this.hypotheses();else this.verifications();
  }
  evidence(parent,paths){for(const p of paths||[])parent.append(button(p+' ↗',()=>this.reveal(p),'ix-evidence-button'));}
  async safe(op,data,msg='已保存到诊断侧车；原始分数未改变。'){try{const r=await this.post(op,data);this.toast(msg);return r;}catch(e){this.toast(e.message);throw e;}}
  dialog(title,build,onSubmit){
    const d=node('dialog','lp-dialog'),form=node('form'),header=node('div','lp-modal-head');header.append(node('h2','',title),button('×',()=>d.close(),'icon-button'));form.append(header);
    const body=node('div','lp-modal-body');form.append(body);const values=build(body)||{};const error=node('p','lp-error');form.append(error);
    if(onSubmit){const footer=node('div','lp-modal-actions'),cancel=button('取消',()=>d.close(),'button'),submit=node('button','button primary','保存');submit.type='submit';footer.append(cancel,submit);form.append(footer);
      form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;error.textContent='';try{await onSubmit(values);d.close();}catch(err){error.textContent=err.message;}finally{submit.disabled=false;}};}
    else form.onsubmit=e=>e.preventDefault();
    d.append(form);document.body.append(d);d.onclose=()=>d.remove();d.showModal();return d;
  }
  checks(){const sc=this.data.scorecard;
    const cards=node('div','lp-layer-cards');for(const l of sc.layers){const b=node('div','lp-layer '+l.status);b.append(node('span','',l.name),node('strong','',states[l.status]),node('small','',`${l.counts.pass} 通过 / ${l.counts.fail} 失败 / ${l.counts.insufficient} 待补证据`));cards.append(b);}this.host.append(cards);
    const con=section('执行契约 / Case Contract',this.data.contract?'当前契约已冻结；修改需要新的版本。':'尚未绑定契约：不默认要求搜索次数、固定推理路线或工具。');
    const bar=node('div','lp-actions');bar.append(tag(this.data.contract?this.data.contract.contract.name+' · '+this.data.contract.contract.version:'未绑定'),tag(sc.binding==='recorded_at_execution'?'日志记录了契约哈希':'历史回填 / 非运行前证明','reference'),button(this.data.contract?'查看 / 新版本契约':'绑定执行契约',()=>this.contractDialog(),'button small'));con.append(bar);
    con.append(node('p','lp-muted',`门禁：${sc.gate.mode} · ${sc.gate.status}。${sc.gate.note}`));this.host.append(con);
    const c=section('分层校验结果','保留“不适用”和“证据不足”；未记录不补零。语义判断不覆盖确定性检查。');
    for(const layer of sc.layers){const group=node('details','lp-check-group');group.open=layer.status==='fail';group.append(node('summary','',layer.name+' · '+states[layer.status]));
      for(const x of sc.checks.filter(x=>x.layer===layer.name)){const row=node('div','lp-check');const title=node('div','lp-check-title');title.append(tag(states[x.status],x.status==='fail'?'incorrect':x.status==='pass'?'correct':'insufficient'),node('strong','',x.title));row.append(title,node('p','lp-muted',x.detail));this.evidence(row,x.evidence);group.append(row);}c.append(group);}this.host.append(c);
    const chain=section('请求 → 返回 → 模型输入','只显示显式关联。不根据步骤相邻或时间接近猜测依赖。');
    for(const link of sc.lineage.links){const row=node('div','lp-link-row');row.append(button(link.from,()=>this.reveal(link.from),'lp-pointer'),node('span','','→'),button(link.to,()=>this.reveal(link.to),'lp-pointer'),tag(link.basis));chain.append(row);chain.append(node('p','lp-muted',link.label));}
    if(!sc.lineage.links.length)chain.append(node('p','lp-muted','尚无可关联的 call_id / JSON pointer / raw-visible 配对。'));
    for(const g of sc.lineage.gaps){const row=node('div','lp-gap');row.append(node('span','',g.message),button('定位',()=>this.reveal(g.pointer),'button small'));chain.append(row);}this.host.append(chain);
    const timing=section('分段耗时','仅展示已记录毫秒值；不同计时段可能重叠，不自动求和。');table(timing,['字段 / 步骤','时长'],sc.lineage.timings.map(x=>[button(x.pointer,()=>this.reveal(x.pointer),'lp-pointer'),x.value_ms+' ms']));if(!sc.lineage.timings.length)timing.append(node('p','lp-muted','未记录分段耗时，无法判断模型排队、检索或重试哪段最慢。'));this.host.append(timing);
    const versions=section('执行版本与差异',sc.context.limitation);versions.append(node('p','lp-muted','缺少：'+(sc.context.missing.join(', ')||'核心元数据均有记录')));jsonView(versions,sc.context.values,'当前执行上下文');versions.append(button('核对预期改动与额外差异',()=>this.contextDialog(),'button small'));this.host.append(versions);
  }
  contractDialog(){this.dialog('冻结 Case Contract',body=>{body.append(node('p','lp-muted','格式 any 表示不加格式要求。使用 v2/v3 保存更改；默认历史回填不追认运行前规范。支持的 Schema 关键字见说明；未支持规则显示证据不足。'));
      const c=this.data.contract?structuredClone(this.data.contract.contract):this.data.contract_template;
      return {json:field(body,'契约 JSON',c,'textarea'),binding:select(body,'绑定依据',{retrospective:'历史回填（默认）',recorded_at_execution:'日志内已保存相同 contract_hash'},'retrospective')};
    },async v=>{await this.safe('contract',{contract:JSON.parse(v.json.value),binding:v.binding.value});});}
  contextDialog(){const baseline=this.data.scorecard.context.values;this.dialog('控制变量：允许变化的字段才可以不同',body=>({
      a:field(body,'基线执行上下文',baseline,'textarea'),b:field(body,'候选执行上下文',baseline,'textarea'),
      allowed:field(body,'允许变化的路径（每行一项）','/context_builder','textarea'),req:field(body,'必须记录的路径（每行一项）',required.join('\n'),'textarea')
    }),async v=>{const r=await this.post('context-diff',{baseline:JSON.parse(v.a.value),candidate:JSON.parse(v.b.value),allowed_changes:lines(v.allowed.value),required_paths:lines(v.req.value)});
      this.dialog('配置差异核查结果',body=>{body.append(tag(r.eligible?'元数据可比':'不可直接归因',r.eligible?'correct':'incorrect'));jsonView(body,r,'完整版本差异');});});}
  hypotheses(){const head=section('归因候选，不是根因判决','保留已知事实、反证、替代解释和最小验证。单条规则命中不自动确认因果。');head.append(button('＋ 新建归因候选',()=>this.hypothesisDialog(),'button primary small'));this.host.append(head);
    if(!this.data.hypotheses.length){const c=section('从已有信号建立假设','右侧规则支持“一键加入候选”；也可先定位原字段，再手工记录。');
      for(const f of this.doc.report.findings.filter(x=>x.evidence.length && x.basis!=='insufficient'))c.append(button('＋ '+f.title,()=>this.fromRule(f.id),'button lp-candidate-button'));this.host.append(c);}
    for(const h of this.data.hypotheses){const state=[...h.history].reverse().find(e=>e.kind==='hypothesis_review')?.data.status||h.state;const c=section(h.title);c.dataset.hypothesis=h.id;
      c.append(tag(h.layer),tag(components[h.component]),tag(hstates[state],'candidate'));c.append(node('p','lp-text',h.observation));this.evidence(c,h.evidence);
      if(h.counter_evidence.length){c.append(node('h4','','反证'));this.evidence(c,h.counter_evidence);}
      c.append(node('h4','','替代解释'));for(const a of h.alternatives)c.append(node('p','lp-text',a));
      c.append(node('h4','','最小验证'),node('p','lp-text',h.minimal_test));
      const bar=node('div','lp-actions');bar.append(button('建立验证任务',()=>this.taskDialog(h),'button primary small'),button('记录验证判断',()=>this.reviewHypothesis(h),'button small'));c.append(bar);
      if(h.history.length)jsonView(c,h.history,'追加判断历史');this.host.append(c);
    }
    this.judgeSection();
  }
  async fromRule(id){try{await this.safe('rule-hypothesis',{finding_id:id},'已建立未验证候选；请检查替代解释，再创建验证任务。');this.onSection('hypotheses');}catch{}}
  hypothesisDialog(){this.dialog('新建可验证假设',body=>({
    title:field(body,'候选原因',''),layer:select(body,'失败层级',{Outcome:'Outcome',Decision:'Decision',Action:'Action',Reliability:'Reliability'},'Action'),component:select(body,'可能的修复对象',components,'unknown'),
    observation:field(body,'已观察事实','','textarea'),evidence:field(body,'支持证据 JSON pointer（每行一项）',this.getPointer()||'','textarea'),
    counter:field(body,'反证 JSON pointer（可空）','','textarea'),alternatives:field(body,'替代解释（至少一项）','','textarea'),test:field(body,'最小验证：改什么，保持什么不变','','textarea')
  }),async v=>{await this.safe('hypothesis',{hypothesis:{title:v.title.value,layer:v.layer.value,component:v.component.value,observation:v.observation.value,evidence:lines(v.evidence.value),counter_evidence:lines(v.counter.value),alternatives:lines(v.alternatives.value),minimal_test:v.test.value}});});}
  reviewHypothesis(h){const tasks=this.data.tasks.filter(t=>t.hypothesis_id===h.id);const results=this.data.results.filter(r=>tasks.some(t=>r.task_id===t.id));this.dialog('追加验证判断（不覆盖历史）',body=>({status:select(body,'验证判断',hstates,'inconclusive'),result:select(body,'绑定验证结果',Object.fromEntries([['','尚无结果'],...results.map(r=>[r.id,r.id])]),''),note:field(body,'判断依据与适用边界','','textarea')}),async v=>{await this.safe('hypothesis-review',{hypothesis_id:h.id,status:v.status.value,result_id:v.result.value||null,note:v.note.value});});}
  taskDialog(h){this.dialog('建立最小验证任务',body=>{
    body.append(node('p','lp-muted','本地只执行内置纯函数组件检查。前缀与从头回归需交给现有评测器；所有任务先冻结，再接收结果。'));
    const title=field(body,'任务名称','验证：'+h.title),mode=select(body,'验证方式',modes,'component');
    const change=field(body,'修改候选（配置 / patch 描述）',h.minimal_test,'textarea'),expected=field(body,'支持或反驳假设时，应看到什么','','textarea');
    const local=node('div'),external=node('div');body.append(local,external);
    const op=select(local,'内置组件',{answer_parser:'答案解析（不重新评分）',tool_schema:'参数 Schema 校验',context_render:'上下文渲染（字符预算，不是 token）'},'answer_parser');
    const p=field(local,'固定组件输入 JSON pointer',this.doc.view.pointers.raw_final_output||this.getPointer()||''),a=field(local,'基线组件配置',{mode:'strip'},'textarea'),b=field(local,'候选组件配置',{mode:'answer_tag',tag:'answer'},'textarea');
    op.onchange=()=>{if(op.value==='context_render'){p.value=this.getPointer()||'/steps/1/tool_response/documents';a.value=JSON.stringify({max_chars:1024,max_documents:5,selection:'first'},null,2);b.value=JSON.stringify({max_chars:1024,max_documents:5,selection:'last'},null,2);}else if(op.value==='tool_schema'){p.value=this.getPointer()||'/steps/1/tool_call/arguments';a.value=JSON.stringify({schema:{type:'object',required:['query'],properties:{query:{type:'string'}}}},null,2);b.value=a.value;}else{p.value=this.doc.view.pointers.raw_final_output||'';a.value='{"mode":"strip"}';b.value='{"mode":"answer_tag","tag":"answer"}';}};
    const allowed=field(external,'允许变化的路径（每行一项）','/context_builder','textarea'),req=field(external,'必须记录的路径（每行一项）',required.join('\n'),'textarea');
    const keys=field(external,'固定回归题目（dataset / sample_id）',[{dataset:this.doc.view.dataset||'',sample_id:this.doc.view.sample_id||''}],'textarea');
    if(this.doc.source.evaluation_id)external.append(button('载入本次正式评测的整份固定题单',async()=>{try{const ev=await this.api('evaluation',{id:this.doc.source.evaluation_id});const panel=await this.api('panel',{id:ev.panel_id});keys.value=JSON.stringify(panel.samples.map(s=>({dataset:s.dataset,sample_id:s.id})),null,2);}catch(e){this.toast(e.message);}},'button small'));
    const pp=field(external,'前缀停止步骤 JSON pointer（仅前缀实验）',this.getPointer()||'');
    external.hidden=true;mode.onchange=()=>{local.hidden=mode.value!=='component';external.hidden=mode.value==='component';};
    return {title,mode,change,expected,op,p,a,b,allowed,req,keys,pp};
  },async v=>{const task={hypothesis_id:h.id,title:v.title.value,mode:v.mode.value,change_candidate:v.change.value,expected_observation:v.expected.value,
    allowed_changes:v.mode.value==='component'?[]:lines(v.allowed.value),required_paths:v.mode.value==='component'?[]:lines(v.req.value)};
    if(v.mode.value==='component')task.component={operation:v.op.value,input_pointer:v.p.value,baseline:JSON.parse(v.a.value),candidate:JSON.parse(v.b.value)};
    else {task.expected_cases=JSON.parse(v.keys.value);if(v.mode.value==='prefix'){task.prefix_pointer=v.pp.value;task.restore_mode='approximate_text_reconstruction';}}
    await this.safe('task',{task},'验证任务已冻结，尚未执行。');this.onSection('verification');});}
  judgeSection(){const s=section('语义 Judge：受约束的证据交换','本地不调用模型。导出证据包给自己的 Judge，再导入其结构化判断；校验证据路径和原文引用，不把 Judge 当根因证明。');
    s.append(button('导出前缀 / 事后诊断证据包',()=>this.dialog('生成 Judge 证据包',body=>({mode:select(body,'Judge 观察范围',{decision_prefix:'当时前缀：排除未来输出、GT 和评分',retrospective:'事后诊断：可含 GT，仅供评测器'},'decision_prefix'),step:field(body,'截止步骤 JSON pointer',this.getPointer()||'/steps/0')}),async v=>{const r=await this.safe('judge-packet',{mode:v.mode.value,step_pointer:v.step.value});saveJSON(r,'judge-evidence-packet.json');}),'button small'));
    for(const p of this.data.judge_packets){const row=node('div','lp-actions');row.append(tag(p.mode),node('span','lp-mono',p.packet_hash.slice(0,12)),button('导出',()=>saveJSON(p,'judge-evidence-packet.json'),'button small'),button('导入 Judge 结果',()=>this.importJSON('导入结构化 Judge 结果',v=>this.safe('judge-result',{packet_id:p.id,response:v})),'button small'));s.append(row);}
    for(const r of this.data.judge_results){const c=node('div','lp-check');c.append(tag('外部 Judge · 待人工核验','candidate'),node('span','lp-muted',r.judge.model+' / '+r.judge.revision));for(const j of r.judgements){c.append(node('h4','',j.dimension+' · '+j.verdict),node('p','lp-text',j.explanation));this.evidence(c,j.evidence.map(e=>e.pointer));}c.append(button('复核 Judge 判断',()=>this.dialog('人工复核 Judge',body=>({status:select(body,'复核结论',{pending:'待复核',confirmed:'人工支持其判断',rejected:'人工驳回'},'pending'),note:field(body,'复核依据','','textarea')}),async v=>this.safe('judge-review',{judge_result_id:r.id,status:v.status.value,note:v.note.value})),'button small'));if(r.history.length)jsonView(c,r.history,'Judge 复核历史');s.append(c);}this.host.append(s);
  }
  importJSON(title,submit){this.dialog(title,body=>{const text=field(body,'JSON 内容','','textarea');const file=node('input');file.type='file';file.accept='.json';file.setAttribute('aria-label','选择 JSON 结果文件');file.onchange=async()=>{if(file.files[0]){if(file.files[0].size>900000)return this.toast('结果超过 900KB，请使用逐题摘要与原文件哈希。');text.value=await file.files[0].text();}};body.prepend(file);return {text};},async v=>submit(JSON.parse(v.text.value)));}
  verifications(){const h=section('验证任务与修复回归','组件检查可以立即运行；前缀干预和从头评测只保存任务与导入结果。尚未执行不显示“已修好”。');h.append(button('从归因候选建立任务',()=>this.onSection('hypotheses'),'button small'));this.host.append(h);
    for(const t of this.data.tasks){const rs=this.data.results.filter(r=>r.task_id===t.id);const c=section(t.title);c.dataset.task=t.id;c.append(tag(modes[t.mode]),tag(rs.length?`${rs.length} 次结果`:'尚未执行',rs.length?'observed':'insufficient'),node('p','lp-text','改动：'+t.change_candidate),node('p','lp-text','预期：'+t.expected_observation));
      const bar=node('div','lp-actions');bar.append(button('导出冻结任务',()=>saveJSON(t,'verification-task.json'),'button small'));
      if(t.mode==='component')bar.append(button('运行本地组件重放',async()=>{try{await this.safe('execute',{task_id:t.id},'组件重放已实际执行。输出变化不等于模型自主能力提升。');}catch{}},'button primary small'));
      else {bar.append(button('导出结果模板',()=>saveJSON(this.resultTemplate(t),'external-result-template.json'),'button small'),button('导入外部验证结果',()=>this.importJSON('绑定本任务的外部结果',v=>this.safe('result',{task_id:t.id,result:v})),'button primary small'),button('关联已登记评测 A / B',()=>this.evaluationDialog(t),'button small'));}
      c.append(bar);jsonView(c,t,'任务、固定输入与控制变量');
      for(const r of rs){const box=node('div','lp-result');box.append(node('h4','',r.report?'外部执行结果（本地未执行 Agent）':'组件结果（已在本机执行）'));
        if(!r.report){table(box,['基线组件输出','候选组件输出'],[[pretty(r.baseline.rendered??r.baseline.parsed??r.baseline.errors??r.baseline),pretty(r.candidate.rendered??r.candidate.parsed??r.candidate.errors??r.candidate)]]);jsonView(box,{baseline:r.baseline,candidate:r.candidate},'组件完整输出与预算记录');box.append(node('p','lp-muted',r.limitation));if(r.matched_budgets===false)box.append(tag('预算发生变化，不是等预算对照','incorrect'));}
        else {const report=r.report;box.append(tag(report.comparison.eligible?'提交的配置可比':'存在额外差异 / 缺少版本',report.comparison.eligible?'correct':'incorrect'),node('p','lp-muted',report.note));
          const reg=report.regression;const stat=node('div','lp-regression-counts');for(const [k,label] of [['repaired','错 → 对'],['regressed','对 → 错'],['both_correct','保持正确'],['missing','缺题'],['unscored','未评分']]){const b=node('div');b.append(node('strong','',reg.counts[k]),node('small','',label));stat.append(b);}box.append(stat);
          box.append(node('p','lp-text',report.autonomous_comparison_eligible?`从空状态、无参考辅助的宏平均差值（提交结果）：${(100*report.autonomous_macro_delta).toFixed(2)} pp`:'不作为自主提升结论：请核查协议完整性、是否从空状态开始、是否使用参考辅助，以及覆盖情况。'));
          table(box,['数据集','基线','候选'],reg.datasets.map(d=>[d.dataset,`${d.baseline.correct}/${d.baseline.total}（已评分 ${d.baseline.scored}）`,`${d.candidate.correct}/${d.candidate.total}（已评分 ${d.candidate.scored}）`]));jsonView(box,report.comparison,'预期改动与额外版本差异');jsonView(box,reg.items,'逐题修复 / 退化记录');}
        box.append(button('导出本次结果',()=>saveJSON(r,'verification-result.json'),'button small'));c.append(box);}
      c.append(button('登记修改 / 接受 / 拒绝',()=>this.fixDialog(t,rs),'button small'));if(t.history.length)jsonView(c,t.history,'修复决定历史（未自动应用）');this.host.append(c);
    }
    if(!this.data.tasks.length)this.host.append(node('p','lp-muted','暂无验证任务。先选择原始证据、提出可区分的原因，再冻结一项最小验证。'));
    const rel=section('同一道题的重复运行 / Reliability','需同一条件下多次独立运行。不同题目不混算；未声明重置或版本不全则保留证据不足。');rel.append(button('导入重复运行记录',()=>this.importJSON('导入同题复测记录（runs 数组）',v=>this.safe('reliability',v)),'button small'));
    for(const r of this.data.reliability){rel.append(node('p','lp-text',`独立运行 ${r.report.n} 次；${r.report.correct??'—'}/${r.report.scored??'—'} 已评分正确；状态 ${r.report.status}`));rel.append(node('p','lp-muted',r.report.note));jsonView(rel,r.report,'完整可靠性报告');}this.host.append(rel);
  }
  resultTemplate(t){const arm={evaluation_id:'填写实际评测 ID',trace_sha256:'填写原轨迹文件 SHA256',context:t.baseline_context,groundtruth_assisted:false,from_empty_state:t.mode==='autonomous',cases:t.expected_cases.map(k=>({...k,em:null,parsed_answer:null,runtime_status:'unknown'}))};return {task_id:t.id,task_hash:t.task_hash,execution:'executed_externally',baseline:structuredClone(arm),candidate:structuredClone(arm)};}
  evaluationDialog(t){this.dialog('使用现有正式评测结果（不重新执行）',body=>({a:field(body,'基线 evaluation ID',this.doc.source.evaluation_id||''),b:field(body,'候选 evaluation ID',''),cross:select(body,'跨项目比较',{no:'不允许（默认）',yes:'明确允许跨项目比较'},'no')}),async v=>{await this.safe('evaluation-result',{task_id:t.id,baseline_evaluation:v.a.value,candidate_evaluation:v.b.value,cross_project:v.cross.value==='yes'});});}
  fixDialog(t,rs){this.dialog('记录修改与决定，不自动上线',body=>({decision:select(body,'修复决定',{proposed:'记录修改候选',accept_for_further_evaluation:'接受，进入后续评测',reject:'拒绝该修改',needs_more_evidence:'继续补充证据'},'proposed'),patch:field(body,'修改文件 / commit / patch 引用',''),result:select(body,'对应结果',Object.fromEntries([['','无结果'],...rs.map(r=>[r.id,r.id])]),''),note:field(body,'修复、退化与决定依据','','textarea')}),async v=>{await this.safe('fix',{task_id:t.id,decision:v.decision.value,patch_ref:v.patch.value,result_id:v.result.value||null,note:v.note.value});});}
}
