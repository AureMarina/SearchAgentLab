import {node,button,tag,short,empty,kindLabels,objectLink,note,evidenceLink,pair,findingsPanel,link,methodURL} from './chunk-6F786F763E2DFEE4.js';
/** Method facts are linked, never inferred from JSONL adjacency. */
export class MethodEvidencePanel{
 constructor(host,{api,session,toast,params}){this.host=host;this.api=api;this.session=session;this.toast=toast;this.params=params;this.dead=false;}
 dispose(){this.dead=true;}
 async load(){empty(this.host,'正在查找方法产物和来源…');try{
  const data=await this.api('methods/related',this.params);if(this.dead)return;this.host.replaceChildren();
  this.host.append(note('只显示明确登记的关联。入选 ≠ 注入 ≠ 行为一致 ≠ 实验证明有效；没有关联不代表未使用。'));
  const h=node('div','mx-actions');h.append(link('打开方法过程与产物', '#/methods'));this.host.append(h);
  if(!data.objects.length){this.host.append(note('当前轨迹尚无方法关联。导入 Self-play / SkillBank 记录，使用 trace_ref 指向此记录即可；不根据文件顺序猜测。'));return;}
  for(const o of data.objects){const card=node('section','mx-related-card');card.dataset.methodObject=o.id;card.append(objectLink(o));
   card.append(node('div','mx-small',o.id+' · '+(o.scope?.project_id||'独立检查区')+' · '+o.content_hash.slice(0,12)));
   const d=o.data;
   if(d.cycle_id)card.append(link('对应 Self-play 轮次',methodURL(d.cycle_id,'selfplay',o.scope)));
   if(d.question_id)card.append(link('对应生成题',methodURL(d.question_id,'artifacts',o.scope)));
   if(d.bank_id)card.append(link('当时的 SkillBank 快照 · '+d.bank_id,methodURL(d.bank_id,'skills',o.scope)));
   if(d.mode)card.append(pair('记录的模式',d.mode));
   if(d.actor_id)card.append(pair('角色',d.actor_id));
   if(d.selected)card.append(pair('入选技能',d.selected));
   for(const x of d.injections||[]){card.append(pair('注入片段 · '+x.member,x.rendered_text));if(x.evidence_ref)card.append(evidenceLink(x.evidence_ref,'定位实际输入字段'));}
   if(d.trace_ref)card.append(evidenceLink(d.trace_ref,'打开关联轨迹'));
   this.host.append(card);
  }
 }catch(e){if(!this.dead)empty(this.host,e.message);}}
}
