import {node,button,short,pretty,ptr,highlight} from './chunk-E1EB354F726277C3.js';

/** Lazily-rendered JSON tree; an object/array/string is a real independent fold.
 * Full raw values are never replaced by previews. Source pointers are stable.
 * Large collections are paged in batches of 80 children to bound the DOM.
 */
export class JsonTree {
  constructor(host, value, {base='',label='$',open=false,onSelect=()=>{},state=new Map()}={}) {
    this.host=host;this.value=value;this.base=base;this.nodes=new Map();this.onSelect=onSelect;this.state=state;
    host.classList.add('ix-json-tree'); host.replaceChildren(this.make(value,base,label,open));
  }
  make(value,path,label,initial=false) {
    const obj=value!==null && typeof value==='object';
    const entries=obj ? (Array.isArray(value)?value.map((v,i)=>[i,v]):Object.entries(value)) : null;
    const item=node('details','ix-json-node');item.dataset.jsonPointer=path;
    const summary=node('summary','ix-json-summary');
    summary.append(node('span','ix-json-key',label));
    const type=obj?(Array.isArray(value)?`[ ${entries.length} items ]`:`{ ${entries.length} keys }`):value===null?'null':typeof value;
    summary.append(node('span','ix-json-type',type));
    if(!obj)summary.append(node('span','ix-json-preview',short(value,115).replace(/\n/g,' ↵ ')));
    const action=button('定位',e=>{e.preventDefault();e.stopPropagation();this.onSelect(path);},'ix-field-action');
    action.title='选择此字段作为诊断证据：'+(path||'/'); summary.append(action);
    const body=node('div',obj?'ix-json-children':'ix-json-value');item.append(summary,body);
    const rec={item,body,value,entries,loaded:0,more:null,filled:false,path};this.nodes.set(path,rec);
    const load=()=>{
      if(rec.filled && !obj)return;
      if(obj && rec.loaded===0)this.page(rec);
      if(!obj&&!rec.filled){const pre=node('pre','ix-value-text',pretty(value));body.append(pre);rec.filled=true;}
    };
    rec.load=load;
    item.addEventListener('toggle',()=>{if(item.open)load();this.state.set(path,item.open);});
    const shouldOpen=this.state.has(path)?this.state.get(path):initial;
    if(shouldOpen){item.open=true;load();}
    return item;
  }
  page(rec) {
    rec.more?.remove(); const end=Math.min(rec.loaded+80,rec.entries.length);
    for(let i=rec.loaded;i<end;i++){const [k,v]=rec.entries[i];rec.body.append(this.make(v,ptr(rec.path,k),String(k)));}
    rec.loaded=end;
    if(end<rec.entries.length){rec.more=button(`继续展开 ${end} / ${rec.entries.length} 个字段`,()=>this.page(rec),'button small ix-load-more');rec.body.append(rec.more);}
    else if(!rec.entries.length&&!rec.filled)rec.body.append(node('span','ix-json-empty',Array.isArray(rec.value)?'[]':'{}'));
    rec.filled=true;
  }
  collapseAll(){for(const r of this.nodes.values()){r.item.open=false;this.state.set(r.path,false);}}
  expandAll(max=2500){
    let opened=0;
    const queue=[this.nodes.get(this.base)];
    while(queue.length&&opened<max){
      const r=queue.shift();r.item.open=true;r.load();opened++;
      if(r.entries){while(r.loaded<r.entries.length&&this.nodes.size<max)this.page(r);
        for(const [k] of r.entries){const x=this.nodes.get(ptr(r.path,k));if(x)queue.push(x);}}
    }
    return {limited:queue.length>0||this.nodes.size>=max,count:opened};
  }
  reveal(path, query='') {
    if(path!==this.base&&!path.startsWith(this.base+'/'))return false;
    let r=this.nodes.get(this.base);if(!r)return false;
    const suffix=path.slice(this.base.length);
    const tokens=suffix?suffix.slice(1).split('/'):[];let here=this.base;
    for(const token of tokens){
      r.item.open=true;r.load();
      const decoded=token.replaceAll('~1','/').replaceAll('~0','~');
      const next=ptr(here,decoded);
      if(r.entries){
        const position=r.entries.findIndex(([k])=>String(k)===decoded);
        if(position<0)return false;
        while(position>=r.loaded)this.page(r);
      }
      r=this.nodes.get(next);if(!r)return false;here=next;
    }
    r.item.open=true;r.load();
    this.host.querySelectorAll('.ix-pointer-hit').forEach(n=>n.classList.remove('ix-pointer-hit'));
    r.item.classList.add('ix-pointer-hit');
    const pre=r.body.querySelector(':scope > pre');if(pre)highlight(pre,pretty(r.value),query);
    r.item.scrollIntoView({block:'center',behavior:'smooth'});this.onSelect(path,false);return true;
  }
}
