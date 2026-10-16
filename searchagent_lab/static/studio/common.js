export {node,button,short,pretty,tag,saveJSON} from '../inspector/dom.js';
import {node,button} from '../inspector/dom.js';
import {JsonTree} from '../inspector/json-tree.js';
export const labels={main:'主实验',reproduction:'文献复现',unassigned:'未分类',correct:'正确',incorrect:'错误',error:'运行异常',unscored:'未评分',rescued:'错→对',harmed:'对→错',both_wrong:'两次均错',both_correct:'两次均对',unpaired:'未配对'};
export function link(label,path,cls='s-link'){const a=node('a',cls,label);a.href=path;return a;}
export function href(page,params={}){const q=new URLSearchParams();for(const[k,v]of Object.entries(params))if(v!==undefined&&v!==null&&v!=='')q.set(k,String(v));return '#/'+page+(q.size?'?'+q:'');}
export function badge(label,type=''){return node('span','s-badge '+type,label);}
export function select(items,value,fn,label){const el=node('select');el.setAttribute('aria-label',label);for(const item of items){const[v,t]=Array.isArray(item)?item:[item.id,item.name||item.id];const o=node('option','',t);o.value=v;el.append(o);}el.value=value||'';el.onchange=()=>fn(el.value);return el;}
export function field(label,control){const d=node('label','s-field');d.append(node('span','',label),control);return d;}
export function section(title,tools){const d=node('div','s-section');d.append(node('h2','',title));if(tools)d.append(tools);return d;}
export function empty(text){return node('div','s-empty',text);}
export function card(cls=''){return node('section','s-card '+cls);}
export function fmt(v,d=3){return typeof v==='number'&&Number.isFinite(v)?Number(v.toFixed(d)).toString():'—';}
export function pct(v){return typeof v==='number'? (v*100).toFixed(1)+'%':'—';}
export function count(stats){if(!stats)return '未评测';return stats.fully_scored?`${stats.answer_correct}/${stats.expected}`:`${stats.answer_correct} 对 · ${stats.scored}/${stats.expected} 已评分`;}
export function scoreCell(t){if(!t)return badge('— 未评测','muted');const s=t.scores?.em;return badge(s===1?'✓ 正确':s===0?'× 错误':'— 未评分',s===1?'good':s===0?'bad':'muted');}
export function groupOf(x,y,eligible=true){if(!eligible)return'unpaired';if(!x||!y)return'unpaired';const a=x.scores?.em,b=y.scores?.em;if(a==null||b==null)return'unscored';return a===0&&b===1?'rescued':a===1&&b===0?'harmed':b===1?'both_correct':'both_wrong';}
export function modal(title){const dlg=document.getElementById('s-modal');document.getElementById('s-modal-title').textContent=title;const body=document.getElementById('s-modal-body');body.replaceChildren();if(!dlg.open)dlg.showModal();return body;}
export function closeModal(){document.getElementById('s-modal').close();}
export function jsonModal(title,value){const body=modal(title);new JsonTree(body,value,{open:true});return body;}
export function toast(msg){const e=document.getElementById('s-toast');e.textContent=msg;e.classList.add('shown');clearTimeout(window.studioToastTimer);window.studioToastTimer=setTimeout(()=>e.classList.remove('shown'),5000);}
export function table(headers){const wrap=node('div','s-table-wrap');const t=node('table','s-table');const head=node('thead'),row=node('tr');headers.forEach(x=>row.append(node('th','',x)));head.append(row);const body=node('tbody');t.append(head,body);wrap.append(t);return {wrap,t,body};}
export function addRow(body,values,cls=''){const tr=node('tr',cls);for(const v of values){const td=node('td');td.append(v instanceof Node?v:document.createTextNode(v==null?'—':String(v)));tr.append(td);}body.append(tr);return tr;}
export function controls(...els){const d=node('div','s-controls');d.append(...els.filter(Boolean));return d;}
export function profileNotice(p){if(p.track==='reproduction')return node('div','s-notice purple','文献复现 · 可查看完整训练、评测与方法过程；不参与主实验迭代。论文结果与本地结果分别保存。');if(p.track==='unassigned')return node('div','s-notice warn','该实验尚未分类。原始数据保留；不会自动把它计入主实验。请在实验页登记用途。');return null;}
export async function gt(api,panel,sample,dataset){const p=await api('panel',{id:panel});const body=modal('Groundtruth · 只读参考，不是模型执行');if(sample){const matches=p.samples.filter(s=>s.id===sample&&(!dataset||s.dataset===dataset));new JsonTree(body,matches.length===1?matches[0]:{status:matches.length?'题号有多个数据集匹配，未自动选取':'题目不存在',matches},{open:true});}else new JsonTree(body,p,{open:true});}
export function trackTabs(ctx,counts={}){const d=node('div','s-track-tabs');for(const t of ['main','reproduction','unassigned']){const b=link(labels[t]+(counts[t]!=null?' '+counts[t]:''),href(ctx.page,{project:ctx.project,track:t}),'s-track '+(ctx.track===t?'active':''));b.dataset.track=t;d.append(b);}return d;}

export function kpi(label,value,note=''){const c=node('section','f-kpi');c.append(node('span','s-muted',label),node('strong','',value),node('small','',note));return c;}
export function pager(total,page,pageSize,change){const d=node('div','f-pager');const pages=Math.max(1,Math.ceil(total/pageSize));d.append(node('span','',total?`${page*pageSize+1}–${Math.min(total,(page+1)*pageSize)} / ${total} 条`:'没有匹配记录'));const prev=button('上一页',()=>change(page-1),'s-btn small'),next=button('下一页',()=>change(page+1),'s-btn small');prev.disabled=page<=0;next.disabled=page>=pages-1;d.append(controls(prev,node('span','',`${page+1} / ${pages}`),next));return d;}
