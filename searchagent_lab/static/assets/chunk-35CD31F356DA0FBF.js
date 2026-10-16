import {node,button,tag,short} from './chunk-9415BE6CCA5FCED4.js';
import {JsonTree} from './chunk-0CE2C231BA0406FE.js';

/** Episode → trajectory/role → step/message → field → nested JSON. */
export class TraceTree {
  constructor(host,document,{onSelect=()=>{},states=new Map()}={}){
    this.host=host;this.doc=document;this.steps=[];this.fields=[];this.onSelect=onSelect;this.states=states;
    host.replaceChildren();this.outer=this.fold('Episode / 当前记录',document.view.question||document.view.sample_id||'已保存的轨迹',true,'episode');host.append(this.outer.el);
    const view=document.view;
    if(!view.trajectories.length){this.outer.body.append(node('div','ix-empty',view.execution_kind==='reference_only'?'这是参考材料，不是 agent 的执行轨迹。请在“原始 JSON”查看完整字段。':'没有识别到逐步记录。原始字段仍可展开，不补造步骤。'));}
    for(const [i,t] of view.trajectories.entries()){
      const f=this.fold(`Trajectory ${i+1} · ${t.name}`,`${t.step_count} ${t.kind==='messages'?'messages':'steps'}${t.reward!=null?' · reward '+t.reward:''}`,true,'trajectory:'+t.pointer);
      this.outer.body.append(f.el);
      for(const [j,s] of t.steps.entries()){
        const error=s.fields.some(f=>f.name==='error'&&f.value);
        const sf=this.fold(`${t.kind==='messages'?'Message':'Step'} ${s.index} · ${s.title}`,s.kind,false,'step:'+s.pointer);
        sf.el.classList.add('ix-step');sf.el.dataset.stepPointer=s.pointer;if(error)sf.el.classList.add('ix-step-error');
        f.body.append(sf.el);const record={...sf,source:s,trajectory:f,filled:false,trees:[]};this.steps.push(record);
        record.fill=()=>{if(record.filled)return;record.filled=true;
          for(const field of s.fields){
            const holder=node('div','ix-field');sf.body.append(holder);
            const tree=new JsonTree(holder,field.value,{base:field.pointer,label:field.label,onSelect, state:states});
            const entry={tree,pointer:field.pointer,step:record};record.trees.push(entry);this.fields.push(entry);
          }
        };
        sf.el.addEventListener('toggle',()=>{if(sf.el.open)record.fill();});
        if(sf.el.open)record.fill();
      }
    }
    const summary=this.fold('最终输出与记录的评分','参考答案与模型答案分开保留',true,'result');this.outer.body.append(summary.el);
    const labels={raw_final_output:'最终原始输出',parsed_answer:'解析答案',gold_answers:'标准答案 / 别名',scores:'原始评分（不重算）',reward:'reward（原值）',runtime_status:'运行状态',termination_reason:'终止原因'};
    for(const [key,label] of Object.entries(labels)){
      if(!view.pointers[key])continue;
      const holder=node('div','ix-field');summary.body.append(holder);
      const tree=new JsonTree(holder,view[key],{base:view.pointers[key],label,onSelect,state:states});
      this.fields.push({tree,pointer:view.pointers[key],section:summary});
    }
    if(!summary.body.children.length)summary.body.append(node('div','ix-empty','未记录终局评分 / 解析答案。'));
  }
  fold(title,secondary,open,key){
    const el=node('details','ix-fold');const sum=node('summary','ix-fold-summary');
    sum.append(node('strong','',title),node('span','ix-fold-sub',short(secondary,130)));
    const body=node('div','ix-fold-body');el.append(sum,body);
    el.open=this.states.has(key)?this.states.get(key):open;
    el.addEventListener('toggle',()=>this.states.set(key,el.open));return {el,body};
  }
  collapseAll(){this.host.querySelectorAll('details').forEach(d=>d.open=false);}
  expandSteps(){this.outer.el.open=true;for(const r of this.steps){r.trajectory.el.open=true;r.el.open=true;r.fill();}}
  reveal(pointer,query=''){
    this.outer.el.open=true;
    const step=this.steps.find(s=>pointer===s.source.pointer||pointer.startsWith(s.source.pointer+'/'));
    if(step){step.trajectory.el.open=true;step.el.open=true;step.fill();
      if(pointer===step.source.pointer){step.el.classList.add('ix-pointer-hit');step.el.scrollIntoView({block:'center',behavior:'smooth'});this.onSelect(pointer,false);return true;}}
    const f=this.fields.filter(f=>pointer===f.pointer||pointer.startsWith(f.pointer+'/')).sort((a,b)=>b.pointer.length-a.pointer.length)[0];
    if(!f)return false;if(f.section)f.section.el.open=true;return f.tree.reveal(pointer,query);
  }
}
