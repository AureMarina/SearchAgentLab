import {node,button,tag,short,pretty,empty} from '../inspector/dom.js';
export {node,button,tag,short,pretty,empty};
export const kindLabels={selfplay_cycle:'Self-play 轮次',generated_question:'生成题',proposal:'出题记录',question_review:'质量审核',solver_attempt:'解题尝试',trace:'真实记录 / 轨迹',training_use:'训练消费',training_update:'参数更新记录',skill_bank:'SkillBank 快照',skill_retrieval:'技能检索 / 注入',skill_evolution:'技能演化',evaluation_binding:'评测外部状态',dataset_manifest:'数据清单',method_case:'联合诊断案例',state_snapshot:'状态快照'};
export const statusLabels={accepted:'审核接受',rejected:'审核驳回',needs_review:'待审核',conflicting:'审核存在分歧',unreviewed:'尚未审核',selected:'已选入',sampled:'记录为已采样',excluded:'已排除',pending:'待复核',confirmed:'人工确认'};
export const groupLabels={general_skills:'通用技能',query_type_skills:'Search 问题类别',task_specific_skills:'任务技能',common_mistakes:'经验教训'};
export function link(label,url,cls='button small'){const a=node('a',cls,label);a.href=url;return a;}
export function methodURL(id,view='',scope={}){return '#/methods?'+new URLSearchParams({id,...(view?{view}:{}),...scope});}
export function refURL(ref){
  const p=ref.pointer||'';
  if(ref.object_id)return '#/trace?'+new URLSearchParams({method:ref.object_id,pointer:p});
  if(ref.trajectory)return '#/trace?'+new URLSearchParams({trajectory:ref.trajectory,pointer:p});
  return '#/trace?'+new URLSearchParams({file:ref.file,record:ref.record,pointer:p});
}
export function evidenceLink(ref,label){return link(label||((ref.object_id||ref.trajectory||ref.file)+' '+(ref.pointer||'$'))+' ↗',refURL(ref),'mx-evidence-link');}
export function pair(label,value){const wrap=node('div','mx-pair');wrap.append(node('span','mx-label',label),node('div','mx-value',value==null?'未记录':pretty(value)));return wrap;}
export function fold(title,body,open=false){const d=node('details','mx-fold');d.open=open;d.append(node('summary','',title),body);return d;}
export function note(text,kind=''){return node('div','mx-note '+kind,text);}
export function objectLink(o){return link((kindLabels[o.kind]||o.kind)+' · '+(o.title||o.id),methodURL(o.id,'',o.scope||{}),'mx-object-link');}
export function synthetic(o){return o?.demo===true?tag('合成示例 · 非真实实验','reference'):null;}
export function section(title){const s=node('section','mx-section');s.append(node('h3','',title));return s;}
export function jsonDialog(title,value){const d=node('dialog','mx-dialog');d.append(node('h2','',title));const pre=node('pre','mx-json',pretty(value));d.append(pre,button('关闭',()=>d.close()));document.body.append(d);d.addEventListener('close',()=>d.remove());d.showModal();}
export function findingsPanel(report){const box=node('div','mx-findings');
  if(!report?.findings?.length){box.append(note('没有命中已实现的规则，不代表没有问题。'));return box;}
  for(const f of report.findings){const body=node('div','mx-finding-body');body.append(node('p','',f.explanation));for(const r of f.evidence||[])body.append(evidenceLink(r));if(f.next_check)body.append(pair('下一步核查',f.next_check));body.append(node('p','mx-small',f.limitation));box.append(fold((f.basis==='fact'?'已记录事实':f.basis==='candidate'?'待验证候选':'证据不足')+' · '+f.title,body));}
  return box;
}
