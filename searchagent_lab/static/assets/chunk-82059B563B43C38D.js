import {node,button,tag,short} from './chunk-9415BE6CCA5FCED4.js';
const basis={observed:'已记录事实',review:'待复核候选',insufficient:'证据不足'};
const status={pending:'待复核',confirmed:'人工确认',rejected:'人工驳回'};

/** Checks and reviews are visibly distinct; there is intentionally no chat. */
export class DiagnosisPanel {
  constructor(host,doc,{reveal,save,onExport,onHypothesis}={}){
    this.host=host;this.doc=doc;this.reveal=reveal;this.save=save;this.pointer='';this.finding='';
    host.replaceChildren();const heading=node('div','ix-diagnostic-heading');heading.append(node('h2','','诊断与复核'),tag(doc.report.ruleset));host.append(heading);
    host.append(node('p','ix-small','规则只检查已保存字段。候选不等于根因，原始评分不会被修改。'));
    const counts=node('div','ix-diagnostic-counts');for(const [k,label] of Object.entries(basis)){
      const box=node('div');box.append(node('strong','',doc.report.counts[k]),node('span','',label));counts.append(box);}
    host.append(counts);
    const filters=node('div','ix-filter-tabs');this.filter='';
    const list=node('div','ix-findings');
    for(const [key,label] of [['','全部'],['observed','事实'],['review','候选'],['insufficient','缺证据']]){
      const b=button(label,()=>{this.filter=key;filters.querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));render();},'ix-filter-button'+(!key?' selected':''));filters.append(b);}
    host.append(filters,list);
    const render=()=>{
      list.replaceChildren();const findings=doc.report.findings.filter(f=>!this.filter||f.basis===this.filter);
      for(const [i,f] of findings.entries()){
        const card=node('details','ix-finding level-'+f.level);card.dataset.findingId=f.id;card.open=i===0;
        const summary=node('summary');summary.append(tag(basis[f.basis],f.basis),node('strong','',f.title));card.append(summary);
        const body=node('div','ix-finding-body');body.append(node('p','',f.explanation));
        for(const e of f.evidence){
          const b=button(`${f.step_index!=null?'Step '+f.step_index+' · ':''}${e.pointer||'$'} ↗`,()=>{this.select(e.pointer,f.id);reveal(e.pointer);},'ix-evidence-button');b.title=e.preview;body.append(b);
        }
        body.append(node('div','ix-check-label','下一步核查'),node('p','',f.next_check),node('p','ix-limitation',f.limitation));
        const latest=[...doc.reviews].reverse().find(r=>r.finding_id===f.id);
        if(latest)body.append(tag(status[latest.status],'reviewed'),node('p','ix-small',latest.note));
        body.append(button('复核此项',()=>{this.select(f.evidence[0]?.pointer||'',f.id);this.form.scrollIntoView({block:'nearest'});this.textarea.focus();},'button small'));
        if(onHypothesis && f.evidence.length && f.basis!=='insufficient')body.append(button('加入归因候选',()=>onHypothesis(f.id),'button small lp-add-hypothesis'));
        card.append(body);list.append(card);
      }
      if(!findings.length)list.append(node('div','ix-empty','未触发此类检查。这不证明轨迹没有问题。'));
    };
    render();
    const form=node('form','ix-review-form');this.form=form;
    const formTitle=node('div','ix-form-heading');formTitle.append(node('h3','','人工复核'),button('新增标注',()=>this.select('',''),'ix-field-action'));form.append(formTitle);
    this.scope=node('div','ix-small','自由标注 · 当前记录');form.append(this.scope);
    const l=node('label','','证据字段');this.path=node('input');this.path.readOnly=true;this.path.value='$';this.path.setAttribute('aria-label','人工标注证据路径');l.append(this.path);form.append(l);
    const statusLabel=node('label','','核验状态');this.status=node('select');this.status.setAttribute('aria-label','核验状态');
    for(const [v,label] of Object.entries(status)){const o=node('option','',label);o.value=v;this.status.append(o);}statusLabel.append(this.status);form.append(statusLabel);
    const noteLabel=node('label','','观察与判断依据');this.textarea=node('textarea');this.textarea.required=true;this.textarea.maxLength=20000;this.textarea.rows=4;this.textarea.placeholder='例如：核对 Step 3，query 使用了前一步的错误实体；对应字段已定位。';this.textarea.setAttribute('aria-label','人工复核说明');noteLabel.append(this.textarea);form.append(noteLabel);
    const submit=node('button','button primary','保存复核');submit.type='submit';form.append(submit);
    form.onsubmit=async e=>{e.preventDefault();submit.disabled=true;
      try{const review=await save({finding_id:this.finding||null,pointer:this.pointer,status:this.status.value,note:this.textarea.value,source_hash:doc.source_hash});
        doc.reviews.push(review);this.textarea.value='';render();this.renderHistory();
      }catch(e){ this.scope.textContent='保存失败：'+e.message; }finally{submit.disabled=false;}};
    host.append(form);this.history=node('div','ix-review-history');host.append(this.history);this.renderHistory();
    host.append(button('导出诊断 + 原始证据',onExport,'button ix-export-report'));
  }
  select(pointer,finding){this.pointer=pointer;this.finding=finding||'';this.path.value=pointer||'$';this.scope.textContent=this.finding?'绑定规则：'+(this.doc.report.findings.find(f=>f.id===this.finding)?.title||''):'自由标注 · 当前字段';}
  renderHistory(){this.history.replaceChildren(node('h3','','复核历史'));
    for(const r of [...this.doc.reviews].reverse()){
      const e=node('div','ix-review');e.append(tag(status[r.status]),node('time','',r.created_at.slice(0,19).replace('T',' ')),node('p','',r.note));
      if(r.pointer)e.append(button(r.pointer+' ↗',()=>this.reveal(r.pointer),'ix-evidence-button'));this.history.append(e);}
    for(const r of this.doc.legacy_annotations||[]){const e=node('div','ix-review');e.append(tag('旧版人工标注'),node('pre','ix-small',JSON.stringify(r,null,2)));this.history.append(e);}
    if(!this.doc.reviews.length&&!this.doc.legacy_annotations?.length)this.history.append(node('p','ix-small','尚无人工记录。保存后可跨刷新 / 重启继续核查。'));
  }
}
