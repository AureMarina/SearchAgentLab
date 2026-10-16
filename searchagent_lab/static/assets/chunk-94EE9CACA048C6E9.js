/** Compact view of the existing rules/reviews. Writes use the existing sidecar. */
import {node,button,badge,select,field,controls} from './chunk-9F75D166F7553CB2.js';
export function problemCell(value){
 const box=node('div','cl-problem-cell');
 if(!value){box.textContent='问题摘要未读取';return box;}
 box.append(badge(value.label,value.status==='confirmed'?'purple':''),node('span','',value.title));box.title=value.title;return box;
}
export function renderProblem(host,doc,ctx,{label='问题摘要',reveal,updated}={}){
 host.replaceChildren();if(!doc||doc.loadError)return;
 const item=doc.problem_summary;if(!item)return;
 const fold=node('details','cl-problem'),heading=node('summary');
 heading.append(node('span','s-muted',label),badge(item.label,item.status==='confirmed'?'purple':''),node('strong','',item.title));
 fold.append(heading);host.append(fold);let built=false;
 fold.addEventListener('toggle',()=>{
  if(!fold.open||built)return;built=true;const body=node('div','cl-problem-body');fold.append(body);
  body.append(node('p','',item.explanation),node('p','s-muted','原因：尚未确定。'+item.limitation));
  if(item.pointer&&reveal)body.append(button('查看对应步骤',()=>reveal(item.pointer),'s-link'));
  body.append(node('p','cl-problem-note','下一步核查：'+item.next_check));
  if(ctx.session.read_only||!doc.source?.trajectory_id||!ctx.post)return;
  const review=node('details','s-fold');review.append(node('summary','','人工复核（可选）'));body.append(review);
  const form=node('form'),text=node('textarea');text.rows=3;text.required=true;text.maxLength=20000;text.placeholder='只记录已经核对的现象和依据，不要求填写训练根因。';text.setAttribute('aria-label','问题摘要复核说明');
  const status=select([['confirmed','确认观察'],['pending','待进一步核查'],['rejected','驳回当前线索']],'confirmed',()=>{},'问题摘要复核状态');
  const save=node('button','s-btn','保存复核');save.type='submit';const feedback=node('span','s-muted');feedback.setAttribute('role','status');
  form.append(field('状态',status),text,controls(save,feedback));review.append(form);
  form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{
   await ctx.post('inspection/reviews',{trajectory:doc.source.trajectory_id,review:{finding_id:item.finding_id||null,pointer:item.pointer||'',status:status.value,note:text.value,source_hash:doc.source_hash}});
   const fresh=await ctx.api('inspection/record',{trajectory:doc.source.trajectory_id});if(!ctx.alive())return;
   updated?.(fresh);renderProblem(host,fresh,ctx,{label,reveal,updated});
  }catch(error){feedback.textContent='保存失败：'+error.message;}finally{save.disabled=false;}};
 });
}
